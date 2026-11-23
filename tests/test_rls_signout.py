"""Sign-out must revoke the workspace session under row-level security.

``/signout`` and ``/signin/signout`` are not under a system path prefix, and
``browser_session`` is a tenant table. Without an explicit system scope the
revoke matches no rows as the restricted role, so the page says "signed out"
while the session stays live. SQLite has no policies, so this only bites in a
Postgres deployment; under HOJA_TEST_RLS the app below runs as that role.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.db import seed_tenant
from hoja_server.store import Store
from tests import rls_mode

HOST = "signout.rls.test"


@pytest.fixture
def signout_app(tmp_path):
    from hoja_server.app import create_app

    owner, kwargs = rls_mode.workspace(tmp_path, "rls-signout")
    seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                user_email="a@alpha.test", token="signout-token-a", demo_issues=False)
    app = create_app(**kwargs, open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host=HOST)
    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 40124)) as client:
        yield client, owner


def _revoked_at(owner, raw: str):
    with owner.connect() as conn:
        return conn.execute(
            select(db.browser_session.c.revoked_at)
            .where(db.browser_session.c.token_hash == db.token_hash(raw))
        ).scalar_one()


@pytest.mark.parametrize("path", ["/signout", "/signin/signout"])
def test_signout_revokes_the_workspace_session(signout_app, path):
    client, owner = signout_app
    raw, _ = Store(owner).create_browser_session_for_token("signout-token-a")
    client.cookies.set("hoja_session", raw)
    assert client.get("/auth/me").status_code == 200

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert _revoked_at(owner, raw) is not None, "sign-out left the session live"
    client.cookies.set("hoja_session", raw)
    assert client.get("/auth/me").status_code == 401
