"""Workspace hosts in the shared tier: app.hoja.work for everyone, a pinned host per enterprise workspace."""
from __future__ import annotations

import pytest
from sqlalchemy import update
from starlette.testclient import TestClient

from hoja_server import google_login
from hoja_server.app import create_app
from hoja_server.db import organization, seed_tenant, user
from tests import rls_mode

CANONICAL = "app.hoja.work"
PINNED = "bravo.hoja.work"
VIEWER = "{ viewer { id } workspace { id } }"


@pytest.fixture
def shared(tmp_path):
    engine, kwargs = rls_mode.workspace(tmp_path, "hosts")
    a = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", user_email="o@alpha.test",
                    token="alpha-tok", demo_issues=False)
    b = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                    token="bravo-tok", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == b["org_id"]).values(vanity_host=PINNED))
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=CANONICAL)
    client = TestClient(app, base_url=f"https://{CANONICAL}", client=("127.0.0.1", 40000))
    return engine, client, a, b


def _gql(client, host, token):
    return client.post("/graphql", json={"query": VIEWER},
                       headers={"Host": host, "Authorization": f"Bearer {token}"})


def test_a_pinned_host_accepts_only_its_own_workspace(shared):
    _engine, client, a, b = shared
    assert _gql(client, PINNED, "alpha-tok").status_code == 401
    own = _gql(client, PINNED, "bravo-tok")
    assert own.status_code == 200
    assert own.json()["data"]["workspace"]["id"] == b["org_id"]


def test_the_canonical_host_serves_every_workspace(shared):
    _engine, client, a, b = shared
    for token, org in (("alpha-tok", a["org_id"]), ("bravo-tok", b["org_id"])):
        res = _gql(client, CANONICAL, token)
        assert res.json()["data"]["workspace"]["id"] == org


def test_other_workspace_hosts_redirect_to_the_canonical_host(shared):
    _engine, client, _a, _b = shared
    res = client.get("/team/ENG?x=1", headers={"Host": "alpha.hoja.work"}, follow_redirects=False)
    assert res.status_code == 308
    assert res.headers["location"] == f"https://{CANONICAL}/team/ENG?x=1"
    posted = client.post("/graphql", json={"query": "{ viewer { id } }"}, follow_redirects=False,
                         headers={"Host": "alpha.hoja.work", "Authorization": "Bearer alpha-tok"})
    assert posted.status_code == 421 and "location" not in posted.headers
    for path, auth in (("/mcp/", {}), ("/team/ENG", {"Authorization": "Bearer alpha-tok"})):
        got = client.get(path, headers={"Host": "alpha.hoja.work", **auth}, follow_redirects=False)
        assert got.status_code == 421
    assert client.get("/ready", headers={"Host": "alpha.hoja.work"}, follow_redirects=False).status_code == 200
    assert client.get("/health", headers={"Host": "localhost"}).status_code == 200


def test_oauth_metadata_names_the_pinned_host(shared):
    _engine, client, _a, _b = shared
    meta = client.get("/.well-known/oauth-authorization-server/mcp", headers={"Host": PINNED}).json()
    assert meta["issuer"] == f"https://{PINNED}/mcp"
    meta = client.get("/.well-known/oauth-authorization-server/mcp", headers={"Host": CANONICAL}).json()
    assert meta["issuer"] == f"https://{CANONICAL}/mcp"


def _mcp_init(client, host, token):
    from mcp.types import LATEST_PROTOCOL_VERSION

    return client.post("/mcp/", headers={"Host": host, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}}})


def test_mcp_on_a_pinned_host_refuses_another_workspace_key(shared, monkeypatch):
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    _engine, client, _a, _b = shared
    with client:
        assert _mcp_init(client, PINNED, "alpha-tok").status_code == 401
        assert _mcp_init(client, PINNED, "bravo-tok").status_code == 200
        assert _mcp_init(client, CANONICAL, "alpha-tok").status_code == 200


def test_mcp_on_a_pinned_host_waits_for_startup(shared):
    _engine, client, _a, _b = shared
    res = _mcp_init(client, PINNED, "bravo-tok")
    assert (res.status_code, res.headers["retry-after"]) == (503, "5")


def test_sign_in_on_a_pinned_host_resolves_only_members_of_that_workspace(shared):
    engine, _client, a, b = shared
    with engine.begin() as conn:
        own = google_login.resolve_member(conn, email="O@bravo.test", subject="sub-b", organization_id=b["org_id"])
        other = google_login.resolve_member(conn, email="o@alpha.test", subject="sub-a", organization_id=b["org_id"])
        conn.execute(update(user).where(user.c.email == "o@bravo.test").values(active=False))
        gone = google_login.resolve_member(conn, email="o@bravo.test", subject="sub-b", organization_id=b["org_id"])
    assert (own["action"], own["organization_id"]) == (google_login.ACTION_LOGIN, b["org_id"])
    assert (other["action"], other["reason"]) == (google_login.ACTION_DENIED, "not_invited")
    assert gone["action"] == google_login.ACTION_DENIED


MASTER = "control-plane-master-that-is-long-enough-0001"


def test_a_handoff_on_a_pinned_host_signs_in_only_its_members(tmp_path, monkeypatch):
    from hoja_server import identity_transfer

    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", f"https://{CANONICAL}")
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_MASTER", MASTER)
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "handoff")
    a = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", user_email="o@alpha.test",
                    token="alpha-tok", demo_issues=False)
    b = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                    token="bravo-tok", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == b["org_id"]).values(vanity_host=PINNED))
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=CANONICAL)

    def finish(client, email, key_host=PINNED):
        token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, key_host), audience=PINNED,
                                        email=email, subject="sub", email_verified=True)
        return client.get(f"/auth/google/finish?t={token}", headers={"Host": PINNED}, follow_redirects=False)

    with TestClient(app, base_url=f"https://{PINNED}", client=("127.0.0.1", 40001)) as client:
        member = finish(client, "o@bravo.test")
        assert member.status_code == 302, member.text
        assert "hoja_session" in member.headers.get("set-cookie", "")
        session = member.cookies.get("hoja_session") or client.cookies.get("hoja_session")
        viewer = client.post("/graphql", json={"query": VIEWER}, headers={"Host": PINNED},
                             cookies={"hoja_session": session})
        assert viewer.json()["data"]["workspace"]["id"] == b["org_id"]
        client.cookies.clear()
        assert finish(client, "o@alpha.test").status_code == 403
        assert finish(client, "o@bravo.test", key_host="alpha.hoja.work").status_code == 400
    assert a["org_id"] != b["org_id"]
