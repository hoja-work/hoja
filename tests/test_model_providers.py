"""Admin-managed model providers: admin gate, encryption at rest, key never returned."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

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
