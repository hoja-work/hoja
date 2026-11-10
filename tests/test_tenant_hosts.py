"""Workspace hosts in the shared tier: app.hoja.work for everyone, a pinned host per
enterprise workspace, and the broker host that must never be mistaken for a tenant.

Merged from two branches that both added this module: feat/shared-tenancy's vanity-host
suite and feat/connector-zoom's broker/slug-policy suite. Keeping both is the point --
the broker callback (api.hoja.work) is what the Calendly and Sentry connectors depend on,
and it must stay a reserved, non-tenant host.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import update
from starlette.testclient import TestClient

from hoja_server import google_login
from hoja_server.app import create_app
from hoja_server.db import organization, seed_tenant, user
from hoja_server.identity_transfer import tenant_target
from hoja_server.signup_policy import RESERVED_SLUGS
from tests import rls_mode

CANONICAL = "app.hoja.work"
PINNED = "bravo.hoja.work"
VIEWER = "{ viewer { id } workspace { id } }"

_HERE = Path(__file__).resolve()
# OSS keeps hoja_server/ at the repo root; Cloud keeps it under app/.
ROOT = _HERE.parents[1]


def _first_existing(*relative: str) -> Path | None:
    for base in (ROOT, ROOT.parent):
        for entry in relative:
            candidate = base / entry
            if candidate.is_file():
                return candidate
    return None
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


# --- Broker host vs tenant host (from feat/connector-zoom) ---

def _first_existing(*relative: str) -> Path | None:
    for base in (ROOT, ROOT.parent):
        for rel in relative:
            candidate = base / rel
            if candidate.is_file():
                return candidate
    return None


@pytest.mark.parametrize("host", ["api", "app", "www", "admin"])
def test_a_reserved_name_is_never_a_tenant_host(host: str) -> None:
    assert tenant_target(f"https://{host}.hoja.work", apex="hoja.work") is None


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.com",
        "https://hoja.work",  # the apex is not a tenant
        "http://clover.hoja.work",  # not https
        "https://clover.hoja.work.evil.com",
        "",
        None,
    ],
)
def test_only_real_tenant_origins_are_return_targets(value: str | None) -> None:
    assert tenant_target(value, apex="hoja.work") is None


def test_a_normal_tenant_origin_is_allowed() -> None:
    assert tenant_target("https://clover.hoja.work", apex="hoja.work") == "https://clover.hoja.work/"


def test_api_stays_reserved_in_the_server_policy() -> None:
    assert "api" in RESERVED_SLUGS


def test_api_stays_reserved_in_the_generated_frontend_policy() -> None:
    generated = _first_existing(
        "frontend/src/signup/slugPolicy.generated.ts",
        "app/frontend/src/signup/slugPolicy.generated.ts",
    )
    if generated is None:
        pytest.skip("no generated slug policy in this checkout")
    text = generated.read_text(encoding="utf-8")
    names = set(re.findall(r'"([a-z0-9-]+)"', text)) | set(re.findall(r"'([a-z0-9-]+)'", text))
    # The generator emits a bare-word list too; fall back to a word-boundary search.
    assert "api" in names or re.search(r'\bapi\b', text), "api missing from slugPolicy.generated.ts"


def test_reserved_slugs_match_the_provisioner_script() -> None:
    """Parity with the script the signup gate and the provisioner both rely on."""
    script = _first_existing("infra/scripts/hoja-tenant", "scripts/hoja-tenant")
    if script is None:
        pytest.skip("no hoja-tenant provisioner in this checkout")
    source = script.read_text(encoding="utf-8")
    block = re.search(r"RESERVED_SLUGS = \{([^}]*)\}", source)
    assert block is not None
    provisioner = ast.literal_eval("{" + block.group(1) + "}")
    assert set(provisioner) == set(RESERVED_SLUGS)
    assert "api" in provisioner
