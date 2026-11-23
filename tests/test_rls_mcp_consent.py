"""The MCP consent screen under row-level security.

Consent is approved by a person at a browser: only the Hoja session cookie
counts. A bearer (OAuth access token or API key) used to be accepted here, which
let a read-only key approve hoja:write; it is now refused. The session lookup and
the pending-request claim still run before and across tenant binding, so they
are exercised here under the restricted role.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.db import seed_tenant
from hoja_server.mcp_oauth import ALL_SCOPES, HojaOAuthProvider
from hoja_server.store import Store
from tests import rls_mode

pytestmark = pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS + Postgres")
HOST = "mcp.consent.test"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture
def consent_app(tmp_path):
    from hoja_server.app import create_app

    owner, kwargs = rls_mode.workspace(tmp_path, "mcp-consent")
    alpha = seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                        user_email="a@alpha.test", token="consent-token-a", demo_issues=False)
    with owner.begin() as conn:
        conn.execute(db.oauth_client.insert().values(
            client_id="consent-client", created_at=db.now_iso(),
            client_json='{"client_id":"consent-client","redirect_uris":["https://client.test/cb"],'
                        '"token_endpoint_auth_method":"none"}'))
    provider = HojaOAuthProvider(owner, Store(owner), True, public_host=HOST)
    with owner.begin() as conn:
        access = provider._mint_pair(conn, "consent-client", alpha["user_id"], alpha["org_id"],
                                     list(ALL_SCOPES), f"https://{HOST}/mcp").access_token

    app = create_app(**kwargs, open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host=HOST)
    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 40123)) as client:
        yield {"client": client, "owner": owner, "access": access, "alpha": alpha}


def _pending(owner, org_id=None) -> tuple[str, str]:
    """An unclaimed authorization request, exactly as /mcp/authorize leaves it."""
    pending_id, csrf = uuid.uuid4().hex, uuid.uuid4().hex
    with owner.begin() as conn:
        conn.execute(db.oauth_pending.insert().values(
            pending_hash=_digest(pending_id), client_id="consent-client",
            scopes='["hoja:read"]', code_challenge="challenge",
            redirect_uri="https://client.test/cb", redirect_explicit=True,
            csrf_token=csrf, resource=f"https://{HOST}/mcp", consumed_at=None,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        ))
    return pending_id, csrf


def _browser_session(consent_app) -> str:
    alpha = consent_app["alpha"]
    from sqlalchemy import select

    with consent_app["owner"].connect() as conn:
        identity = conn.execute(select(db.organization_membership.c.identity_id).where(
            db.organization_membership.c.organization_id == alpha["org_id"])).scalar_one()
    raw, _ = Store(consent_app["owner"]).create_session_for_membership(identity, alpha["user_id"], alpha["org_id"])
    return raw


def test_an_oauth_bearer_cannot_open_or_approve_consent(consent_app):
    pending_id, csrf = _pending(consent_app["owner"])
    auth = {"Authorization": f"Bearer {consent_app['access']}"}
    resp = consent_app["client"].get(f"/mcp/oauth/consent?pending={pending_id}", headers=auth,
                                     follow_redirects=False)
    assert resp.status_code == 303 and "/auth/login" in resp.headers["location"]
    posted = consent_app["client"].post(f"/mcp/oauth/consent?pending={pending_id}", headers=auth,
                                        data={"decision": "allow", "csrf_token": csrf},
                                        follow_redirects=False)
    assert posted.status_code == 401


def test_a_browser_session_resolves_the_consent_screen_under_rls(consent_app):
    pending_id, _csrf = _pending(consent_app["owner"])
    resp = consent_app["client"].get(
        f"/mcp/oauth/consent?pending={pending_id}",
        cookies={"hoja_session": _browser_session(consent_app)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, (resp.status_code, resp.headers.get("location"), resp.text)
    assert "Allow consent-client" in resp.text
    assert "Alpha" in resp.text


def test_consent_claims_the_pending_request_for_the_session_workspace(consent_app):
    pending_id, _csrf = _pending(consent_app["owner"])
    resp = consent_app["client"].get(
        f"/mcp/oauth/consent?pending={pending_id}",
        cookies={"hoja_session": _browser_session(consent_app)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, (resp.status_code, resp.text)
    from sqlalchemy import select
    with consent_app["owner"].connect() as conn:
        row = conn.execute(select(db.oauth_pending.c.user_id, db.oauth_pending.c.organization_id)
                           .where(db.oauth_pending.c.pending_hash == _digest(pending_id))).one()
    assert row == (consent_app["alpha"]["user_id"], consent_app["alpha"]["org_id"])


def test_an_unknown_bearer_still_degrades_to_login(consent_app):
    """The fix must not turn the consent screen into an unauthenticated page."""
    pending_id, _csrf = _pending(consent_app["owner"])
    resp = consent_app["client"].get(
        f"/mcp/oauth/consent?pending={pending_id}",
        headers={"Authorization": "Bearer not-a-real-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/auth/login?next=")
