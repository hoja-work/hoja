"""Each rotating-token store must read its own row as the owning tenant.

The MCP service connects as the app role, exactly like the web app, so row-level
security admits a connector account only inside a transaction bound to its
organization. `_active_rows` binds the tenant for its one query; the token store
then opens its own connection to re-read the row and to write the rotated pair.
If that connection is unbound the row is invisible and a healthy grant answers
"the account is no longer connected" to every tool call.

GitHub's store was fixed this way in #97. Zoom, Calendly and Sentry share the
same shape, so each is asserted here against the *restricted* engine — the one
the app actually runs on — not the owner engine that bypasses the policies.
"""

from __future__ import annotations

import asyncio
import importlib
import time

import pytest

from hoja_server.db import connector_account, make_engine, seed_tenant
from hoja_server.secrets_box import encrypt_secret
from tests import rls_mode

# provider, store module, store class, error module, error class
STORES = [
    ("zoom", "hoja_server.zoom_tokens", "ZoomTokenStore", "zoom.errors", "ConnectorAuthError"),
    ("calendly", "hoja_server.calendly_tokens", "CalendlyTokenStore",
     "calendly.errors", "ConnectorAuthError"),
    ("sentry", "hoja_server.sentry_tokens", "SentryTokenStore",
     "sentry.errors", "ConnectorAuthError"),
]

ACCOUNT_ID = "acct-tenant-store"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _resolve(module: str, name: str):
    return getattr(importlib.import_module(module), name)


def _add_rotating_account(engine, org_id: str, user_id: str, provider: str) -> None:
    """A grant the store can use without touching the network: a live access token."""
    now = int(time.time())
    with engine.begin() as conn:
        conn.execute(connector_account.insert().values(
            id=ACCOUNT_ID,
            organization_id=org_id,
            owner_user_id=user_id,
            provider=provider,
            external_account="owner@example.test",
            external_id="ext-1",
            scopes="",
            encrypted_refresh_token=encrypt_secret("refresh-mine", org_id),
            encrypted_access_token=encrypt_secret("access-mine", org_id),
            access_token_expires_at=str(now + 3600),
            status="active",
            created_at="2026-11-09T00:00:00+00:00",
            updated_at="2026-11-09T00:00:00+00:00",
        ))


def _workspace(tmp_path, label):
    engine, kwargs = rls_mode.workspace(tmp_path, label)
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="o@alpha.test", token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo",
                        user_email="o@bravo.test", token="bravo-tok", demo_issues=False)
    # The engine the app runs on, subject to the production policies.
    return engine, make_engine(kwargs["database_url"]), alpha, bravo


@pytest.mark.skipif(not rls_mode.ENABLED, reason="row-level security is not enabled for this run")
@pytest.mark.parametrize("provider,module,name,err_module,err_name", STORES)
def test_the_store_reads_its_row_only_under_the_owning_organization(
    tmp_path, provider, module, name, err_module, err_name
) -> None:
    engine, app_engine, alpha, bravo = _workspace(tmp_path, f"token-store-{provider}")
    _add_rotating_account(engine, alpha["org_id"], alpha["user_id"], provider)
    store_cls = _resolve(module, name)

    assert store_cls(engine=app_engine, http=None, config=None)._row(
        ACCOUNT_ID, alpha["org_id"]) is not None
    assert store_cls(engine=app_engine, http=None, config=None)._row(
        ACCOUNT_ID, bravo["org_id"]) is None


@pytest.mark.skipif(not rls_mode.ENABLED, reason="row-level security is not enabled for this run")
@pytest.mark.parametrize("provider,module,name,err_module,err_name", STORES)
def test_the_store_serves_a_live_grant_and_never_another_tenant(
    tmp_path, provider, module, name, err_module, err_name
) -> None:
    """The behaviour, not just the query: the owning tenant gets the token; another gets the
    reconnect error rather than someone else's credential."""
    engine, app_engine, alpha, bravo = _workspace(tmp_path, f"token-store-live-{provider}")
    _add_rotating_account(engine, alpha["org_id"], alpha["user_id"], provider)
    store_cls = _resolve(module, name)
    auth_error = _resolve(err_module, err_name)

    own = store_cls(engine=app_engine, http=None, config=None)
    assert asyncio.run(own.access_token(ACCOUNT_ID, alpha["org_id"])) == "access-mine"

    other = store_cls(engine=app_engine, http=None, config=None)
    with pytest.raises(auth_error):
        asyncio.run(other.access_token(ACCOUNT_ID, bravo["org_id"]))
