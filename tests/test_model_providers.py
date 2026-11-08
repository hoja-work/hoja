"""Admin-managed model providers: admin gate, encryption at rest, key never returned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect, select
from starlette.testclient import TestClient

from hoja_server.agent_chat import _enabled_provider
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, model_provider, seed_tenant
from hoja_server.store import Store

CREATE = (
    "mutation($n: String!, $p: String!, $m: String!, $k: String!) "
    "{ modelProviderCreate(name: $n, provider: $p, modelId: $m, apiKey: $k) "
    "{ id name provider modelId hasApiKey enabled } }"
)
LIST = "{ modelProviders { id name hasApiKey } }"
UPDATE = "mutation($id: String!, $k: String) { modelProviderUpdate(id: $id, apiKey: $k) { id hasApiKey } }"
DELETE = "mutation($id: String!) { modelProviderDelete(id: $id) }"
ME = "{ viewer { id } }"
SECRET = "sk-test-SECRET-value-9f2"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _setup(tmp_path):
    db_path = tmp_path / "providers.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Provider Org", org_url_key="provider-org",
                        user_email="owner@provider.test", token="owner-token", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return engine, app, admin


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def _member_token(store, admin, client):
    from tests.test_member_management import _add_member
    member_id = _add_member(client, admin["token"], "member@provider.test")
    _, key = store.create_api_key(admin["org_id"], member_id, "member key", "read_write", None)
    return key


def test_admin_creates_provider_and_key_is_encrypted_and_hidden(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = _gql(client, admin["token"], CREATE, {"n": "Main", "p": "openai", "m": "gpt-4o", "k": SECRET})
        assert "errors" not in res, res
        created = res["data"]["modelProviderCreate"]
        assert created["hasApiKey"] is True and created["enabled"] is True
        assert SECRET not in str(res)

        listed = _gql(client, admin["token"], LIST)
        assert listed["data"]["modelProviders"][0]["hasApiKey"] is True
        assert SECRET not in str(listed)

    with engine.connect() as conn:
        stored = conn.execute(select(model_provider.c.encrypted_api_key)).scalar_one()
    assert SECRET not in stored


def test_member_cannot_manage_providers(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    store = Store(engine)
    with TestClient(app) as client:
        member_key = _member_token(store, admin, client)
        res = _gql(client, member_key, CREATE, {"n": "X", "p": "openai", "m": "m", "k": SECRET})
        assert res.get("errors"), res
        assert "admins" in res["errors"][0]["message"]
        assert _gql(client, member_key, LIST).get("errors")
        with engine.connect() as conn:
            assert conn.execute(select(model_provider)).first() is None


def test_update_rotates_key_and_delete_removes_row(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        pid = _gql(client, admin["token"], CREATE, {"n": "A", "p": "openai", "m": "m", "k": SECRET})[
            "data"]["modelProviderCreate"]["id"]
        with engine.connect() as conn:
            before = conn.execute(select(model_provider.c.encrypted_api_key)).scalar_one()
        upd = _gql(client, admin["token"], UPDATE, {"id": pid, "k": "sk-rotated"})
        assert upd["data"]["modelProviderUpdate"]["hasApiKey"] is True
        with engine.connect() as conn:
            after = conn.execute(select(model_provider.c.encrypted_api_key)).scalar_one()
        assert after != before
        assert _gql(client, admin["token"], DELETE, {"id": pid})["data"]["modelProviderDelete"] is True
        assert _gql(client, admin["token"], LIST)["data"]["modelProviders"] == []


def test_create_fails_clearly_without_master_key(tmp_path, monkeypatch) -> None:
    engine, app, admin = _setup(tmp_path)
    monkeypatch.delenv("HOJA_SECRET_KEY")
    with TestClient(app) as client:
        res = _gql(client, admin["token"], CREATE, {"n": "A", "p": "openai", "m": "m", "k": SECRET})
        assert res.get("errors"), res
        assert "HOJA_SECRET_KEY" in res["errors"][0]["message"]
        assert SECRET not in str(res)


# --- managed providers: internal URL must never be exposed -------------------

MANAGED_ROW = {
    "id": "default-trefoil",
    "name": "Trefoil (included)",
    "provider": "openai",
    "model_id": "wafer-glm-5.2",
    "base_url": "http://100.77.155.122:8080/v1",
    "encrypted_api_key": "ciphertext",
    "enabled": True,
    "created_at": "2026-11-08T00:00:00Z",
    "updated_at": "2026-11-08T00:00:00Z",
}


def test_managed_provider_base_url_is_never_serialized():
    from hoja_server.model_providers import _serialize

    out = _serialize(MANAGED_ROW)
    assert out["managed"] is True
    assert out["baseUrl"] is None, "internal endpoint leaked to the API"
    assert "100.77" not in repr(out)


def test_user_provider_base_url_is_unchanged():
    from hoja_server.model_providers import _serialize

    out = _serialize({**MANAGED_ROW, "id": "custom-openai"})
    assert out["managed"] is False
    assert out["baseUrl"] == MANAGED_ROW["base_url"]


def test_internal_host_rejected_without_allowlist(monkeypatch):
    from hoja_server.model_providers import ALLOWLIST_ENV, validate_model_base_url

    monkeypatch.delenv("HOJA_ALLOW_INSECURE_MODEL_URL", raising=False)
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    with pytest.raises(ValueError, match="must use https"):
        validate_model_base_url("http://100.77.155.122:8080/v1")
    with pytest.raises(ValueError, match="must not point to a local or private"):
        validate_model_base_url("https://100.77.155.122:8080/v1")


def test_allowlist_permits_only_named_networks(monkeypatch):
    from hoja_server.model_providers import ALLOWLIST_ENV, validate_model_base_url

    monkeypatch.delenv("HOJA_ALLOW_INSECURE_MODEL_URL", raising=False)
    monkeypatch.setenv(ALLOWLIST_ENV, "100.64.0.0/10")
    # inside the tailnet range: allowed over plain http because the network is a
    # WireGuard tunnel, and the URL is never handed to a client anyway
    assert validate_model_base_url("http://100.77.155.122:8080/v1") == "http://100.77.155.122:8080/v1"
    # a different private network is still refused
    with pytest.raises(ValueError):
        validate_model_base_url("http://10.0.0.5/v1")


def test_public_urls_unaffected_by_allowlist(monkeypatch):
    from hoja_server.model_providers import ALLOWLIST_ENV, validate_model_base_url

    monkeypatch.delenv("HOJA_ALLOW_INSECURE_MODEL_URL", raising=False)
    monkeypatch.setenv(ALLOWLIST_ENV, "100.64.0.0/10")
    assert validate_model_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert validate_model_base_url(None) is None


# --- the included cloud offering is the workspace default --------------------
#
# The row a new workspace gets does not come from a runtime fallback: it is seeded
# into the tenant's own `model_provider` table by
# infra/scripts/hoja-trefoil-default from infra/tenants/default-provider.json (a
# Cloud-only file; the OSS repo has neither). These tests pin that contract - the
# offering resolves as the workspace's enabled provider, and a workspace without
# the seed is unchanged.

OFFERING_PATH = Path(__file__).resolve().parents[2] / "infra" / "tenants" / "default-provider.json"


def _offering() -> dict:
    return json.loads(OFFERING_PATH.read_text())


def _seed_offering_row(engine, org_id: str) -> dict:
    """Insert the offering's row the way `hoja-trefoil-default seed --apply` does."""
    provider = _offering()["provider"]
    with engine.begin() as conn:
        conn.execute(model_provider.insert().values(
            id=provider["row_id"],
            organization_id=org_id,
            name=provider["name"],
            provider=provider["provider"],
            model_id=provider["model_id"],
            base_url=provider["base_url"],
            context_window=provider.get("context_window"),
            encrypted_api_key="ciphertext",
            enabled=provider.get("enabled", True),
            created_at="2026-11-08T00:00:00Z",
            updated_at="2026-11-08T00:00:00Z",
        ))
    return provider


def test_included_offering_is_the_workspace_default(tmp_path) -> None:
    engine, app, admin = _setup(tmp_path)
    provider = _seed_offering_row(engine, admin["org_id"])

    # The agent resolves the first enabled provider for the org; the seeded offering
    # must be it, with the wafer model id and the context window it registers.
    row = _enabled_provider(Store(engine), admin["org_id"])
    assert row is not None
    assert row["id"] == provider["row_id"]
    assert row["model_id"] == "wafer-deepseek-v4.1-flash-1017"
    assert row["context_window"] == 350000
    assert row["provider"] == "openai"

    with TestClient(app) as client:
        listed = _gql(client, admin["token"],
                      "{ modelProviders { id name modelId contextWindow managed baseUrl enabled } }")
    assert "errors" not in listed, listed
    [shown] = listed["data"]["modelProviders"]
    assert shown["modelId"] == "wafer-deepseek-v4.1-flash-1017"
    assert shown["contextWindow"] == 350000
    assert shown["managed"] is True
    assert shown["baseUrl"] is None, "internal trefoil endpoint leaked to the API"
    assert "100.77" not in json.dumps(listed)


def test_workspace_without_the_seed_has_no_implicit_default(tmp_path) -> None:
    """The default is a seeded row, not a code path: no seed means no provider.

    This is the OSS-facing behaviour - OSS has no offering file and never seeds it,
    so upgrading must not hand an OSS workspace a provider it did not configure.
    """
    engine, app, admin = _setup(tmp_path)
    assert _enabled_provider(Store(engine), admin["org_id"]) is None
    with TestClient(app) as client:
        assert _gql(client, admin["token"], LIST)["data"]["modelProviders"] == []


def test_context_window_migration_is_guarded_and_idempotent(tmp_path) -> None:
    from hoja_server.db import _apply_model_provider_context_window

    db_path = tmp_path / "window.db"
    engine = make_engine(db_path)
    migrate(engine)
    with engine.begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("model_provider")}
        assert "context_window" in columns
    # A second pass (an older database replayed forward, or a retried migration)
    # must not fail on the column already existing.
    with engine.begin() as conn:
        _apply_model_provider_context_window(conn)
        _apply_model_provider_context_window(conn)
