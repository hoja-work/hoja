"""The MCP bearer must resolve and bind on every path, not just system paths.

``_mcp_oauth_auth`` fills ``scope["hoja_auth"]`` for every ``/mcp`` request. Its
token lookup only works while something else has already granted system context:
the consent route inherits that from the middleware's path allowlist, the resource
endpoint does not. Read outside system context the lookup matches nothing, so the
principal is silently absent on the endpoint that serves the 72 tools.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.db import seed_tenant
from hoja_server.mcp_oauth import ALL_SCOPES, HojaOAuthProvider
from hoja_server.store import Store
from tests import rls_mode

pytestmark = pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS + Postgres")
HOST = "mcp.bearer.test"


class ScopeRecorder:
    """Observes the ASGI scope after ``mcp_endpoint`` has filled in the principal."""

    def __init__(self, app):
        self.app, self.seen = app, []

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)
        if scope.get("type") == "http" and scope.get("path", "").startswith("/mcp"):
            self.seen.append((scope.get("path"), scope.get("hoja_auth")))


@pytest.fixture
def mcp_app(tmp_path):
    from hoja_server.app import create_app

    owner, kwargs = rls_mode.workspace(tmp_path, "mcp-bearer")
    alpha = seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                        user_email="a@alpha.test", token="bearer-token-a", demo_issues=False)
    bravo = seed_tenant(owner, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                        user_email="b@bravo.test", token="bearer-token-b", demo_issues=False)
    with owner.begin() as conn:
        conn.execute(db.oauth_client.insert().values(
            client_id="bearer-client", created_at=db.now_iso(),
            client_json='{"client_id":"bearer-client","redirect_uris":["https://client.test/cb"],'
                        '"token_endpoint_auth_method":"none"}'))
    provider = HojaOAuthProvider(owner, Store(owner), True, public_host=HOST)

    def mint(seeded, scopes=ALL_SCOPES):
        with owner.begin() as conn:
            return provider._mint_pair(conn, "bearer-client", seeded["user_id"], seeded["org_id"],
                                       list(scopes), f"https://{HOST}/mcp").access_token

    app = create_app(**kwargs, open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host=HOST)
    recorder = ScopeRecorder(app)
    with TestClient(recorder, base_url=f"https://{HOST}", client=("127.0.0.1", 40123)) as client:
        yield {"client": client, "recorder": recorder, "alpha": alpha, "bravo": bravo,
               "token": mint(alpha), "owner": owner}


def _initialize(client, token):
    from mcp.types import LATEST_PROTOCOL_VERSION

    return client.post("/mcp/", headers={"Host": HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "binding-test", "version": "1"}}})


def test_resource_request_resolves_the_bearer_principal(mcp_app):
    assert _initialize(mcp_app["client"], mcp_app["token"]).status_code == 200
    paths = [path for path, _ in mcp_app["recorder"].seen]
    assert paths, "the MCP endpoint was never reached"
    principal = [auth for _path, auth in mcp_app["recorder"].seen if auth is not None]
    assert principal, "the bearer resolved to no principal on the resource endpoint"
    assert principal[-1]["org_id"] == mcp_app["alpha"]["org_id"]
    assert principal[-1]["user_id"] == mcp_app["alpha"]["user_id"]
    assert principal[-1]["org_name"] == "Alpha"


def test_a_revoked_or_expired_bearer_resolves_to_nothing(mcp_app):
    """Enumerating the lookup by hash must not drop the revocation and expiry predicates."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    minted = mcp_app["token"]
    with mcp_app["owner"].begin() as conn:
        conn.execute(update(db.oauth_access_token).where(
            db.oauth_access_token.c.token_hash == db.token_hash(minted)
        ).values(revoked_at=db.now_iso()))
    assert _initialize(mcp_app["client"], minted).status_code in (200, 401)
    assert [auth for _path, auth in mcp_app["recorder"].seen if auth is not None] == []

    from hoja_server.mcp_oauth import HojaOAuthProvider as Provider
    provider = Provider(mcp_app["owner"], Store(mcp_app["owner"]), True, public_host=HOST)
    with mcp_app["owner"].begin() as conn:
        expired = provider._mint_pair(conn, "bearer-client", mcp_app["alpha"]["user_id"],
                                      mcp_app["alpha"]["org_id"], list(ALL_SCOPES),
                                      f"https://{HOST}/mcp").access_token
        conn.execute(update(db.oauth_access_token).where(
            db.oauth_access_token.c.token_hash == db.token_hash(expired)
        ).values(expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()))
    assert _initialize(mcp_app["client"], expired).status_code in (200, 401)
    assert [auth for _path, auth in mcp_app["recorder"].seen if auth is not None] == []


def test_an_api_key_still_resolves_on_the_resource_endpoint(mcp_app):
    """Both credential types go through the same enumerated lookup."""
    assert _initialize(mcp_app["client"], mcp_app["alpha"]["token"]).status_code == 200
    principal = [auth for _path, auth in mcp_app["recorder"].seen if auth is not None]
    assert principal and principal[-1]["client_id"] == "api-key"
    assert principal[-1]["org_id"] == mcp_app["alpha"]["org_id"]


def test_a_read_only_api_key_keeps_its_read_scope(mcp_app):
    import json

    from sqlalchemy import insert

    with mcp_app["owner"].begin() as conn:
        conn.execute(insert(db.api_key).values(
            id="read-only-key", token_hash=db.token_hash("read-only-token"), label="ro",
            access="read", user_id=mcp_app["alpha"]["user_id"],
            organization_id=mcp_app["alpha"]["org_id"], created_at=db.now_iso()))
    assert _initialize(mcp_app["client"], "read-only-token").status_code == 200
    principal = [auth for _path, auth in mcp_app["recorder"].seen if auth is not None][-1]
    assert json.loads(principal["scopes"]) == ["hoja:read"]


def test_a_bearer_for_another_workspace_resolves_to_that_workspace(mcp_app):
    """The lookup is enumerated, not scoped to whatever the host pins."""
    with mcp_app["owner"].begin() as conn:
        provider = HojaOAuthProvider(mcp_app["owner"], Store(mcp_app["owner"]), True, public_host=HOST)
        token = provider._mint_pair(conn, "bearer-client", mcp_app["bravo"]["user_id"],
                                    mcp_app["bravo"]["org_id"], list(ALL_SCOPES),
                                    f"https://{HOST}/mcp").access_token
    assert _initialize(mcp_app["client"], token).status_code == 200
    principal = [auth for _path, auth in mcp_app["recorder"].seen if auth is not None]
    assert principal and principal[-1]["org_id"] == mcp_app["bravo"]["org_id"]
