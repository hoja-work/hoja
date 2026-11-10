"""Cross-organization isolation for connectors on the shared tier (ADR-003).

Every assertion here is an attack, not a description. One shared database now holds
every organization's connector accounts, so each layer is probed on its own:

* the **route layer** — a session in workspace A listing, reading or revoking B's
  account or token;
* the **crypto layer** — B's ciphertext moved into an A row must not open, because the
  key is derived per organization with HKDF and the organization is bound as AAD;
* the **state layer** — a sealed OAuth state minted for A must not complete in B, and a
  nonce is single-use inside its own workspace;
* the **database layer** — under ``HOJA_TEST_RLS`` the same run happens with the app
  connecting as a restricted, non-owning role under the production policies, so a
  forgotten predicate fails the test instead of leaking in production.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select, update

from hoja_server.app import create_app
from hoja_server.db import (
    connector_account,
    connector_oauth_state,
    seed_tenant,
)
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret
from tests import rls_mode

CANONICAL = "app.hoja.work"
HOST_A = "alpha.hoja.work"
HOST_B = "bravo.hoja.work"
PROVIDER = "google_workspace"
KEY = "shared-tenancy-test-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_HOST", CANONICAL)
    monkeypatch.setenv("HOJA_CONNECTOR_REDIRECT_HOSTS", f"{HOST_A} {HOST_B}")
    monkeypatch.setenv("HOJA_CONNECTOR_BROKER_URL", "https://api.hoja.work")
    monkeypatch.setenv("HOJA_CONNECTOR_BROKER_HOST", "api.hoja.work")
    monkeypatch.setenv("HOJA_CONNECTOR_MCP_URL", "http://connector-mcp:8099/mcp")
    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", KEY)
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("HOJA_GOOGLE_CLIENT_SECRET", "GOCSPX-shared-tenancy-test")
    monkeypatch.setenv("HOJA_GOOGLE_REDIRECT_URI", "https://unused.example.test/callback")
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_ID", "v_calendly_client_id_000000")
    monkeypatch.setenv("HOJA_CALENDLY_CLIENT_SECRET", "calendly-secret-test-value")
    monkeypatch.setenv("HOJA_CALENDLY_REDIRECT_URI", "https://unused.example.test/callback")
    monkeypatch.delenv("HOJA_TENANT_BASE_DOMAIN", raising=False)


def _setup(tmp_path):
    engine, kwargs = rls_mode.workspace(tmp_path, "shared-tenancy")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", user_email="o@alpha.test",
                        token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                        token="bravo-tok", demo_issues=False)
    client = _app(kwargs)
    return engine, client, alpha, bravo


def _app(kwargs):
    from starlette.testclient import TestClient

    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host=CANONICAL)
    return TestClient(app, base_url=f"https://{CANONICAL}", client=("127.0.0.1", 40000))


def _add_account(engine, org: str, owner: str | None, *, account_id: str,
                 token: str = "1//BRAVO-REFRESH") -> None:
    with engine.begin() as conn:
        conn.execute(connector_account.insert().values(
            id=account_id,
            organization_id=org,
            owner_user_id=owner,
            provider=PROVIDER,
            external_account="shared@bravo.test",
            scopes="https://www.googleapis.com/auth/gmail.readonly",
            encrypted_refresh_token=encrypt_secret(token, org),
            status="active",
            created_at="2026-11-09T00:00:00+00:00",
            updated_at="2026-11-09T00:00:00+00:00",
        ))


# --- route layer ------------------------------------------------------------------------


def test_another_workspace_cannot_list_or_revoke_a_connector_account(tmp_path) -> None:
    engine, client, alpha, bravo = _setup(tmp_path)
    _add_account(engine, bravo["org_id"], bravo["user_id"], account_id="acct-bravo")

    with client:
        listing = client.get("/api/connectors/accounts",
                             headers={"Authorization": f"Bearer {alpha['token']}"})
        assert listing.status_code == 200
        assert listing.json()["accounts"] == []

        revoke = client.post("/api/connectors/accounts/acct-bravo/revoke",
                             headers={"Authorization": f"Bearer {alpha['token']}"})
    assert revoke.status_code == 404, "another workspace's account must not be addressable"

    with engine.connect() as conn:
        row = conn.execute(
            select(connector_account).where(connector_account.c.id == "acct-bravo")
        ).mappings().one()
    assert row["status"] == "active", "the refusal must not have revoked anything"
    assert row["organization_id"] == bravo["org_id"]


def test_another_workspace_cannot_use_a_connector_token_through_the_agent(tmp_path, monkeypatch) -> None:
    """The MCP service resolves grants for the signed principal's organization only."""
    engine, _client, alpha, bravo = _setup(tmp_path)
    _add_account(engine, bravo["org_id"], bravo["user_id"], account_id="acct-bravo")

    from google_workspace.principal import sign_principal, verify_principal

    from hoja_server.connector_mcp_service import _active_rows

    principal = verify_principal(sign_principal(alpha["user_id"], alpha["org_id"], "read", key=KEY), key=KEY)
    assert _active_rows(engine, principal, "", PROVIDER) == []
    other = verify_principal(sign_principal(bravo["user_id"], bravo["org_id"], "read", key=KEY), key=KEY)
    assert [row["id"] for row in _active_rows(engine, other, "", PROVIDER)] == ["acct-bravo"]


# --- crypto layer -----------------------------------------------------------------------


def test_a_sealed_token_moved_into_another_organization_does_not_open(tmp_path) -> None:
    engine, _client, alpha, bravo = _setup(tmp_path)
    _add_account(engine, bravo["org_id"], bravo["user_id"], account_id="acct-bravo")

    with engine.connect() as conn:
        ciphertext = conn.execute(
            select(connector_account.c.encrypted_refresh_token)
            .where(connector_account.c.id == "acct-bravo")
        ).scalar_one()

    # The value is intact for its own organization...
    assert decrypt_secret(ciphertext, bravo["org_id"]) == "1//BRAVO-REFRESH"
    # ...and useless under any other one, which is the whole point: one shared database,
    # one row, and a copy that follows the row is still unreadable.
    with pytest.raises(SecretBoxError):
        decrypt_secret(ciphertext, alpha["org_id"])

    # Rewrite the row as Alpha's and the row's own token stops working: no silent use.
    with engine.begin() as conn:
        conn.execute(update(connector_account).where(connector_account.c.id == "acct-bravo")
                     .values(organization_id=alpha["org_id"]))
    with engine.connect() as conn:
        moved = conn.execute(
            select(connector_account.c.encrypted_refresh_token)
            .where(connector_account.c.id == "acct-bravo")
        ).scalar_one()
    with pytest.raises(SecretBoxError):
        decrypt_secret(moved, alpha["org_id"])


def test_an_unreadable_grant_is_skipped_rather_than_used(tmp_path) -> None:
    """A row whose ciphertext does not authenticate for its organization yields no grants."""
    engine, _client, alpha, _bravo = _setup(tmp_path)
    _add_account(engine, alpha["org_id"], alpha["user_id"], account_id="acct-alpha")
    with engine.begin() as conn:
        conn.execute(update(connector_account).where(connector_account.c.id == "acct-alpha")
                     .values(encrypted_refresh_token=encrypt_secret("1//OTHER", "someone-else")))

    import httpx
    from google_workspace.principal import sign_principal, verify_principal

    from hoja_server.connector_mcp_service import make_grant_resolver

    async def resolve():
        principal = verify_principal(
            sign_principal(alpha["user_id"], alpha["org_id"], "read", key=KEY), key=KEY)
        async with httpx.AsyncClient() as http:
            resolver = make_grant_resolver(engine, http=http, config=_google_config())
            return await resolver(principal, "")

    import asyncio

    assert asyncio.run(resolve()) == []


def _google_config():
    from google_workspace import GoogleOAuthConfig

    return GoogleOAuthConfig(client_id="c", client_secret="s",
                             redirect_uri=f"https://{HOST_A}/api/connectors/google/callback")


# --- state layer ------------------------------------------------------------------------


def test_a_state_minted_for_one_workspace_does_not_complete_in_another(tmp_path) -> None:
    _engine, _client, alpha, bravo = _setup(tmp_path)
    payload = {
        "uid": alpha["user_id"],
        "org": alpha["org_id"],
        "scope": "user",
        "verifier": "v",
        "exp": int(time.time()) + 600,
        "nonce": "n1",
    }
    state = encrypt_secret(json.dumps(payload), alpha["org_id"])
    # Bravo's key cannot open Alpha's state, so the callback cannot be replayed there.
    with pytest.raises(SecretBoxError):
        decrypt_secret(state, bravo["org_id"])


def test_the_state_nonce_ledger_is_organization_scoped(tmp_path) -> None:
    """Single-use is per workspace: A consuming its nonce does not spend B's."""
    from hoja_server.connectors_api import _consume_state

    engine, _client, alpha, bravo = _setup(tmp_path)
    expires = int(time.time()) + 600
    assert _consume_state(engine, alpha["org_id"], "shared-nonce", expires) is True
    # Replay inside the same workspace is refused...
    assert _consume_state(engine, alpha["org_id"], "shared-nonce", expires) is False
    # ...and Bravo's identical value is a different row, so it is still spendable.
    assert _consume_state(engine, bravo["org_id"], "shared-nonce", expires) is True
    with engine.connect() as conn:
        rows = conn.execute(
            select(connector_oauth_state.c.organization_id, connector_oauth_state.c.nonce_hash)
        ).all()
    assert len(rows) == 2


# --- redirect derivation ----------------------------------------------------------------


def test_the_redirect_is_derived_from_the_workspace_host(tmp_path) -> None:
    engine, client, alpha, bravo = _setup(tmp_path)
    with engine.begin() as conn:
        from hoja_server.db import organization

        conn.execute(update(organization).where(organization.c.id == alpha["org_id"])
                     .values(vanity_host=HOST_A))
        conn.execute(update(organization).where(organization.c.id == bravo["org_id"])
                     .values(vanity_host=HOST_B))

    with client:
        on_alpha = client.get("/api/connectors/google/start?scope=user",
                              headers={"Host": HOST_A,
                                       "Authorization": f"Bearer {alpha['token']}"},
                              follow_redirects=False)
        on_bravo = client.get("/api/connectors/google/start?scope=user",
                              headers={"Host": HOST_B,
                                       "Authorization": f"Bearer {bravo['token']}"},
                              follow_redirects=False)
    assert on_alpha.status_code == 302 and on_bravo.status_code == 302
    for response, host in ((on_alpha, HOST_A), (on_bravo, HOST_B)):
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["redirect_uri"] == [f"https://{host}/api/connectors/google/callback"]


def test_an_unregistered_workspace_host_reports_unavailable_instead_of_failing_at_the_provider(tmp_path) -> None:
    engine, client, _alpha, bravo = _setup(tmp_path)
    with engine.begin() as conn:
        from hoja_server.db import organization

        # Charlie's workspace exists but its callback was never registered with Google.
        conn.execute(update(organization).where(organization.c.id == bravo["org_id"])
                     .values(vanity_host="charlie.hoja.work"))

    with client:
        start = client.get("/api/connectors/google/start?scope=user",
                           headers={"Host": "charlie.hoja.work",
                                    "Authorization": f"Bearer {bravo['token']}"},
                           follow_redirects=False)
        config = client.get("/api/connectors/config", headers={"Host": "charlie.hoja.work"}).json()
        registered = client.get("/api/connectors/config",
                                headers={"Host": CANONICAL}).json()
    assert start.status_code == 409
    assert start.json()["error"] == "not available on this workspace host"
    assert start.json()["hint"]
    assert config["availableOnThisHost"]["google_workspace"] is False
    assert "not available on this workspace host" in config["unavailableReason"]["google_workspace"]
    # Calendly goes through the broker, so it stays reachable from any workspace host.
    assert config["availableOnThisHost"]["calendly"] is True
    assert config["unavailableReason"]["calendly"] is None
    # The canonical host has no per-tenant callback registered either.
    assert registered["availableOnThisHost"]["google_workspace"] is False


def test_a_raw_host_header_cannot_move_the_redirect(tmp_path) -> None:
    """A host that is not in the registry never becomes a redirect, whatever the header says."""
    _engine, client, alpha, _bravo = _setup(tmp_path)
    with client:
        res = client.get("/api/connectors/google/start?scope=user",
                         headers={"Host": "evil.example.com",
                                  "Authorization": f"Bearer {alpha['token']}"},
                         follow_redirects=False)
    assert res.status_code == 409
    assert "location" not in res.headers


def test_the_connector_mcp_resolver_reads_only_its_own_organization_under_rls(tmp_path) -> None:
    """The MCP service connects as the app role, so its read must be tenant-bound.

    Without a bound transaction row-level security returns no rows and every agent tool call
    answers "no connected account" — a silent outage, not a leak. This asserts the positive case
    (its own organization resolves) and the negative one (another organization's does not) against
    the *restricted* engine, not the owner engine the other tests use.
    """
    from google_workspace.principal import sign_principal, verify_principal

    from hoja_server.connector_mcp_service import _active_rows

    engine, kwargs, alpha, bravo = _setup_with_kwargs(tmp_path)
    _add_account(engine, bravo["org_id"], bravo["user_id"], account_id="acct-bravo")
    _add_account(engine, alpha["org_id"], alpha["user_id"], account_id="acct-alpha")

    if kwargs.get("database_url"):
        from hoja_server.db import make_engine

        app_engine = make_engine(kwargs["database_url"])
    else:
        app_engine = engine

    mine = verify_principal(
        sign_principal(alpha["user_id"], alpha["org_id"], "read", key=KEY), key=KEY)
    theirs = verify_principal(
        sign_principal(bravo["user_id"], bravo["org_id"], "read", key=KEY), key=KEY)
    assert [row["id"] for row in _active_rows(app_engine, mine, "", PROVIDER)] == ["acct-alpha"]
    assert [row["id"] for row in _active_rows(app_engine, theirs, "", PROVIDER)] == ["acct-bravo"]


def _setup_with_kwargs(tmp_path):
    engine, kwargs = rls_mode.workspace(tmp_path, "shared-tenancy-mcp")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha", user_email="o@alpha.test",
                        token="alpha-tok", demo_issues=False)
    bravo = seed_tenant(engine, org_name="Bravo", org_url_key="bravo", user_email="o@bravo.test",
                        token="bravo-tok", demo_issues=False)
    return engine, kwargs, alpha, bravo
