"""Connecting an MCP client to a hosted workspace, end to end over HTTP.

Covers the approval step (browser session only, scope choice, workspace choice,
expiry, sign-in round trip), the token it produces (resource, scopes), refresh
rotation's grace window, and dynamic client registration limits.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient

pytest.importorskip("mcp")

from hoja_server import db
from hoja_server.app import create_app
from hoja_server.db import organization, organization_membership, seed_tenant
from hoja_server.mcp_oauth import (
    ALL_SCOPES,
    MAX_REGISTRATIONS_PER_IP_HOUR,
    PENDING_TTL_MINUTES,
    READ_SCOPE,
    REFRESH_GRACE_SECONDS,
    REQUEST_CLIENT_IP,
    WRITE_SCOPE,
    HojaOAuthProvider,
)
from hoja_server.store import Store
from tests import rls_mode

CANONICAL = "app.hoja.work"
PINNED = "bravo.hoja.work"
REDIRECT = "http://127.0.0.1:53682/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


def _identity(engine, org_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(organization_membership.c.identity_id).where(
            organization_membership.c.organization_id == org_id)).scalar_one()


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """Alpha and Charlie on the canonical host (one person in both), Bravo on its own host."""
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "onboarding")
    a = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", user_email="o@alpha.test",
                    token="alpha-tok", demo_issues=False)
    c = seed_tenant(engine, org_name="Charlie", org_url_key="charlie", user_email="o2@alpha.test",
                    token="charlie-tok", demo_issues=False)
    b = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                    token="bravo-tok", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == b["org_id"]).values(vanity_host=PINNED))
        # The Alpha person is also a member of Charlie (and of Bravo, below in some tests).
        conn.execute(update(organization_membership).where(
            organization_membership.c.organization_id == c["org_id"]).values(identity_id=_identity(engine, a["org_id"])))
    for seeded in (a, b, c):
        seeded["identity_id"] = _identity(engine, seeded["org_id"])
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=CANONICAL)
    client = TestClient(app, base_url=f"https://{CANONICAL}", client=("127.0.0.1", 40000))
    with client:
        yield {"engine": engine, "client": client, "a": a, "b": b, "c": c}


def _register(client, host=CANONICAL, name="Test Client", scope=None) -> str:
    meta = {"client_name": name, "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}
    if scope:
        meta["scope"] = scope
    res = client.post("/mcp/register", json=meta, headers={"Host": host})
    assert res.status_code == 201, res.text
    return res.json()["client_id"]


def _authorize(client, client_id, host=CANONICAL, **extra) -> str:
    params = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
              "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "st", **extra}
    res = client.get("/mcp/authorize", params=params, headers={"Host": host}, follow_redirects=False)
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    assert location.startswith(f"https://{host}/mcp/oauth/consent?pending="), location
    return location


def _session(engine, seeded) -> str:
    raw, _ = Store(engine).create_session_for_membership(seeded["identity_id"], seeded["user_id"], seeded["org_id"])
    assert raw
    return raw


def _consent(client, url, cookie, host=CANONICAL, **form):
    path = urlsplit(url).path + "?" + urlsplit(url).query
    page = client.get(path, headers={"Host": host}, cookies={"hoja_session": cookie}, follow_redirects=False)
    if not form:
        return page, None
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    res = client.post(path, data={"csrf_token": csrf, **form}, headers={"Host": host},
                      cookies={"hoja_session": cookie}, follow_redirects=False)
    return page, res


def _token(client, client_id, code, host=CANONICAL):
    res = client.post("/mcp/token", headers={"Host": host}, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": VERIFIER})
    assert res.status_code == 200, res.text
    return res.json()


def _refresh(client, client_id, refresh, host=CANONICAL):
    return client.post("/mcp/token", headers={"Host": host}, data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})


def _mcp(client, token, host=CANONICAL, method="initialize", params=None):
    from mcp.types import LATEST_PROTOCOL_VERSION

    params = params or {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}
    return client.post("/mcp/", headers={"Host": host, "Authorization": f"Bearer {token}",
                                          "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def _connect(hosted, seeded, host=CANONICAL, **form):
    client = hosted["client"]
    cid = _register(client, host)
    url = _authorize(client, cid, host)
    _page, res = _consent(client, url, _session(hosted["engine"], seeded), host,
                          decision="allow", **form)
    assert res.status_code == 303, res.text
    query = parse_qs(urlsplit(res.headers["location"]).query)
    return cid, _token(client, cid, query["code"][0], host)


# --------------------------------------------------------------------------- consent


def test_a_bearer_cannot_approve_a_grant(hosted):
    """A read-only API key (or any bearer) used to be accepted as the consenting identity."""
    client, engine, a = hosted["client"], hosted["engine"], hosted["a"]
    with engine.begin() as conn:
        conn.execute(db.api_key.insert().values(
            id="ro", token_hash=db.token_hash("read-only-key"), label="ro", access="read",
            user_id=a["user_id"], organization_id=a["org_id"], created_at=db.now_iso()))
    url = _authorize(client, _register(client))
    path = urlsplit(url).path + "?" + urlsplit(url).query
    auth = {"Host": CANONICAL, "Authorization": "Bearer read-only-key"}
    page = client.get(path, headers=auth, follow_redirects=False)
    assert page.status_code == 303 and "consent" in page.headers["location"]
    posted = client.post(path, headers=auth, data={"decision": "allow", "csrf_token": "x", "access": "write"},
                         follow_redirects=False)
    assert posted.status_code == 401
    with engine.connect() as conn:
        assert conn.execute(select(db.oauth_authorization)).first() is None


def test_consent_offers_a_choice_and_grants_only_what_was_chosen(hosted):
    _cid, tokens = _connect(hosted, hosted["a"], access="read")
    assert tokens["scope"] == READ_SCOPE
    _cid, tokens = _connect(hosted, hosted["a"], access="write")
    assert tokens["scope"].split() == list(ALL_SCOPES)


def test_consent_page_names_app_workspace_and_account(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client, name="Claude <Desktop>"))
    page, _ = _consent(client, url, _session(hosted["engine"], hosted["a"]))
    assert page.status_code == 200
    text = page.text
    assert "Claude &lt;Desktop&gt;" in text and "<Desktop>" not in text
    assert "127.0.0.1:53682" in text
    assert "o@alpha.test" in text and 'href="/signout"' in text
    assert 'value="write" checked' in text and 'value="read"' in text
    # One person, two canonical workspaces: both offered, the signed-in one preselected.
    assert f'value="{hosted["a"]["org_id"]}" checked' in text and "Charlie" in text
    assert "&#x27;" not in text and '["' not in text  # no JSON-string scopes leaking into the page


def test_a_read_only_request_shows_no_write_option(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client), scope=READ_SCOPE)
    page, _ = _consent(client, url, _session(hosted["engine"], hosted["a"]))
    assert 'value="write"' not in page.text and "asked for read access" in page.text


def test_the_grant_binds_to_the_workspace_picked_on_the_canonical_host(hosted):
    _cid, tokens = _connect(hosted, hosted["a"], access="read", workspace=hosted["c"]["org_id"])
    with hosted["engine"].connect() as conn:
        row = conn.execute(select(db.oauth_access_token).where(
            db.oauth_access_token.c.token_hash == db.token_hash(tokens["access_token"]))).mappings().one()
    assert (row["organization_id"], row["user_id"]) == (hosted["c"]["org_id"], hosted["c"]["user_id"])


def test_a_workspace_the_person_does_not_belong_to_is_refused(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client))
    _page, res = _consent(client, url, _session(hosted["engine"], hosted["a"]),
                          decision="allow", workspace=hosted["b"]["org_id"])
    assert res.status_code == 403


def test_a_vanity_workspace_is_never_granted_on_the_canonical_host(hosted):
    """The person's only workspace has its own host: the page sends them there."""
    client, engine, b = hosted["client"], hosted["engine"], hosted["b"]
    # A canonical-host session for a vanity workspace (the sign-in flow decides
    # whether one can exist; the consent step must not mint from it regardless).
    url = _authorize(client, _register(client))
    raw, _ = Store(engine).create_session_for_membership(b["identity_id"], b["user_id"], b["org_id"])
    page, _ = _consent(client, url, raw)
    assert page.status_code == 409
    assert f"https://{PINNED}/mcp" in page.text


def test_on_a_workspace_host_the_workspace_is_fixed(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client, PINNED), PINNED)
    page, _ = _consent(client, url, _session(hosted["engine"], hosted["b"]), PINNED)
    assert page.status_code == 200
    assert f"Bravo · {PINNED}" in page.text and "started on bravo.hoja.work" in page.text
    assert 'type="radio" name="workspace"' not in page.text


def test_deny_returns_access_denied_to_the_client(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client))
    _page, res = _consent(client, url, _session(hosted["engine"], hosted["a"]), decision="deny")
    assert res.status_code == 303
    query = parse_qs(urlsplit(res.headers["location"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["st"]
    assert query["iss"] == [f"https://{CANONICAL}/mcp"]


def test_an_expired_request_renders_a_page_naming_the_app(hosted):
    client, engine = hosted["client"], hosted["engine"]
    url = _authorize(client, _register(client, name="Cursor"))
    with engine.begin() as conn:
        expires = conn.execute(select(db.oauth_pending.c.expires_at)).scalar_one()
        assert datetime.fromisoformat(expires) > datetime.now(timezone.utc) + timedelta(minutes=PENDING_TTL_MINUTES - 1)
        conn.execute(update(db.oauth_pending).values(
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    page, _ = _consent(client, url, _session(engine, hosted["a"]))
    assert page.status_code == 400 and page.headers["content-type"].startswith("text/html")
    assert "This connection request expired" in page.text and "from Cursor" in page.text


# --------------------------------------------------------------------------- sign-in round trip


def test_signed_out_consent_goes_to_the_web_login_with_the_path(hosted):
    client = hosted["client"]
    url = _authorize(client, _register(client))
    path = urlsplit(url).path + "?" + urlsplit(url).query
    res = client.get(path, headers={"Host": CANONICAL}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/auth/login?next=" + quote(path, safe="")


MASTER = "control-plane-master-that-is-long-enough-0001"


def test_signed_out_consent_on_a_workspace_host_round_trips_through_hosted_sign_in(tmp_path, monkeypatch):
    """Server-side return path: control plane /signin?next=<consent URL> -> finish?to=<consent path>."""
    from hoja_server import identity_transfer

    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", f"https://{CANONICAL}")
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_MASTER", MASTER)
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "roundtrip")
    b = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                    token="bravo-tok", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == b["org_id"]).values(vanity_host=PINNED))
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=CANONICAL)
    with TestClient(app, base_url=f"https://{PINNED}", client=("127.0.0.1", 40001)) as client:
        url = _authorize(client, _register(client, PINNED), PINNED)
        consent_path = urlsplit(url).path + "?" + urlsplit(url).query
        res = client.get(consent_path, headers={"Host": PINNED}, follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == (
            f"https://{CANONICAL}/signin?next=" + quote(f"https://{PINNED}{consent_path}", safe=""))
        # The control plane hands the browser back with the consent path as `to`.
        target = identity_transfer.tenant_target(f"https://{PINNED}{consent_path}")
        assert target == f"https://{PINNED}{consent_path}"
        assert identity_transfer.return_path(target) == consent_path
        token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, PINNED), audience=PINNED,
                                        email="o@bravo.test", subject="sub", email_verified=True)
        finish = client.get(f"/auth/google/finish?t={token}&to={quote(consent_path, safe='')}",
                            headers={"Host": PINNED}, follow_redirects=False)
        assert finish.status_code == 302 and finish.headers["location"] == consent_path
        page = client.get(consent_path, headers={"Host": PINNED}, follow_redirects=False)
        assert page.status_code == 200 and "Allow Test Client" in page.text


def test_a_return_target_keeps_only_a_well_formed_pending_id():
    from hoja_server import identity_transfer

    assert identity_transfer.tenant_target(
        "https://acme.hoja.work/mcp/oauth/consent?pending=abc") == "https://acme.hoja.work/mcp/oauth/consent"
    assert identity_transfer.tenant_target(
        "https://acme.hoja.work/team?pending=" + "a" * 20) == "https://acme.hoja.work/team"
    assert identity_transfer.return_path("https://acme.hoja.work/") is None


# --------------------------------------------------------------------------- tokens


def test_authorize_without_resource_mints_a_usable_token(hosted):
    """A token stored with resource=None was refused by the SDK and looped the client through OAuth."""
    _cid, tokens = _connect(hosted, hosted["a"], access="read")
    with hosted["engine"].connect() as conn:
        resource = conn.execute(select(db.oauth_access_token.c.resource)).scalar_one()
    assert resource == f"https://{CANONICAL}/mcp"
    res = _mcp(hosted["client"], tokens["access_token"])
    assert res.status_code == 200, res.text


def test_metadata_advertises_both_scopes_and_registration_defaults_to_both(hosted):
    client = hosted["client"]
    prm = client.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": CANONICAL}).json()
    assert prm["scopes_supported"] == list(ALL_SCOPES)
    res = client.post("/mcp/register", headers={"Host": CANONICAL}, json={
        "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"})
    assert res.json()["scope"].split() == list(ALL_SCOPES)


def test_a_read_only_client_can_step_up_to_write(hosted):
    """Registered with hoja:read only, the client can still ask for write after a 403 challenge."""
    client = hosted["client"]
    cid = _register(client, scope=READ_SCOPE)
    url = _authorize(client, cid, scope=f"{READ_SCOPE} {WRITE_SCOPE}")
    _page, res = _consent(client, url, _session(hosted["engine"], hosted["a"]), decision="allow", access="write")
    code = parse_qs(urlsplit(res.headers["location"]).query)["code"][0]
    assert _token(client, cid, code)["scope"].split() == list(ALL_SCOPES)


def test_refresh_grace_window_then_reuse_detection(hosted):
    client, engine = hosted["client"], hosted["engine"]
    cid, first = _connect(hosted, hosted["a"], access="read")
    second = _refresh(client, cid, first["refresh_token"])
    assert second.status_code == 200
    # The client lost that response and retries with the previous token: a fresh pair.
    retry = _refresh(client, cid, first["refresh_token"])
    assert retry.status_code == 200, retry.text
    assert _mcp(client, retry.json()["access_token"]).status_code == 200
    assert _mcp(client, second.json()["access_token"]).status_code == 200
    # A second reuse of the same old token is a replay: the whole grant is revoked.
    replay = _refresh(client, cid, first["refresh_token"])
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert _mcp(client, retry.json()["access_token"]).status_code == 401
    with engine.connect() as conn:
        live = conn.execute(select(db.oauth_refresh_token).where(
            db.oauth_refresh_token.c.revoked_at.is_(None))).all()
    assert live == []


def test_reuse_after_the_grace_window_revokes_the_grant(hosted):
    client, engine = hosted["client"], hosted["engine"]
    cid, first = _connect(hosted, hosted["a"], access="read")
    second = _refresh(client, cid, first["refresh_token"]).json()
    old = (datetime.now(timezone.utc) - timedelta(seconds=REFRESH_GRACE_SECONDS + 5)).isoformat()
    with engine.begin() as conn:
        conn.execute(update(db.oauth_refresh_token).where(
            db.oauth_refresh_token.c.token_hash == db.token_hash(first["refresh_token"])).values(revoked_at=old))
    assert _refresh(client, cid, first["refresh_token"]).status_code == 400
    assert _refresh(client, cid, second["refresh_token"]).status_code == 400


def test_an_explicitly_revoked_token_gets_no_grace(hosted):
    client = hosted["client"]
    cid, first = _connect(hosted, hosted["a"], access="read")
    # The SDK's revocation form declares client_secret without a default, so a
    # public client sends it empty.
    revoked = client.post("/mcp/revoke", headers={"Host": CANONICAL},
                          data={"token": first["refresh_token"], "client_id": cid, "client_secret": ""})
    assert revoked.status_code == 200, revoked.text
    assert _refresh(client, cid, first["refresh_token"]).status_code == 400


# --------------------------------------------------------------------------- registration


def test_registration_is_limited_per_address_not_globally(hosted):
    engine = hosted["engine"]
    provider = HojaOAuthProvider(engine, Store(engine), True, public_host=CANONICAL)
    import asyncio

    from mcp.server.auth.provider import RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull

    def register(ip):
        info = OAuthClientInformationFull(client_id=f"c-{ip}-{datetime.now().timestamp()}",
                                          redirect_uris=[REDIRECT], token_endpoint_auth_method="none")
        token = REQUEST_CLIENT_IP.set(ip)
        try:
            asyncio.run(provider.register_client(info))
        finally:
            REQUEST_CLIENT_IP.reset(token)

    for _ in range(MAX_REGISTRATIONS_PER_IP_HOUR):
        register("198.51.100.7")
    with pytest.raises(RegistrationError):
        register("198.51.100.7")
    register("203.0.113.9")  # another network is unaffected


def test_unused_registrations_are_pruned(hosted):
    client, engine = hosted["client"], hosted["engine"]
    stale_unused = _register(client)
    _cid, _tokens = _connect(hosted, hosted["a"], access="read")
    used = _cid
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with engine.begin() as conn:
        conn.execute(update(db.oauth_client).values(created_at=old))
    _register(client)  # pruning runs on registration
    with engine.connect() as conn:
        ids = {r[0] for r in conn.execute(select(db.oauth_client.c.client_id))}
    assert stale_unused not in ids and used in ids


def test_max_scopes_follow_the_profile(hosted):
    engine, a = hosted["engine"], hosted["a"]
    provider = HojaOAuthProvider(engine, Store(engine), True, public_host=CANONICAL)
    assert provider.max_scopes(a["org_id"], a["user_id"]) == list(ALL_SCOPES)
    with engine.begin() as conn:
        conn.execute(update(db.user).where(db.user.c.id == a["user_id"]).values(kind="agent"))
    assert provider.max_scopes(a["org_id"], a["user_id"]) == [READ_SCOPE]
    with engine.begin() as conn:
        conn.execute(update(db.user).where(db.user.c.id == a["user_id"]).values(active=False))
    assert provider.max_scopes(a["org_id"], a["user_id"]) == []


def test_wrong_workspace_host_for_an_oauth_token(hosted):
    """An Alpha OAuth token on Bravo's host: 403 with Alpha's own MCP URL, not a 401 loop."""
    _cid, tokens = _connect(hosted, hosted["a"], access="read")
    res = _mcp(hosted["client"], tokens["access_token"], PINNED)
    assert res.status_code == 403
    assert res.json()["code"] == "wrong_workspace_host"
    assert res.json()["url"] == f"https://{CANONICAL}/mcp"
    assert json.dumps(res.json()).count("bravo") == 0
