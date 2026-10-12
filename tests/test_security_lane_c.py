"""Regression tests for lane C hardening: OAuth refresh reuse, DCR limits, model URLs, thread bounds, Docker."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
pytest.importorskip("mcp")

from starlette.testclient import TestClient

from cliniar_server import agent_threads, db, mcp_oauth, model_providers
from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant
from cliniar_server.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _tenant(tmp_path, name="sec"):
    path = tmp_path / f"{name}.db"
    engine = make_engine(path)
    migrate(engine)
    tenant = seed_tenant(engine, org_name="Sec Org", org_url_key=f"sec-{name}",
                         user_email=f"{name}@sec.test", demo_issues=False)
    return path, engine, tenant


def _client_row(engine, client_id):
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(client_id=client_id, redirect_uris=["https://client.test/cb"],
                                        token_endpoint_auth_method="none")
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id=client_id,
                     client_json=client.model_dump_json(), created_at=db.now_iso()))
    return client


# 1. Refresh-token reuse over the real HTTP /mcp/token endpoint revokes the grant family.
def test_refresh_reuse_over_http_revokes_grant_family(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_HOST", "mcp.test")
    path, engine, tenant = _tenant(tmp_path, "replay")
    provider = mcp_oauth.HojaOAuthProvider(engine, Store(engine), True)
    _client_row(engine, "replay-client")
    with engine.begin() as conn:
        pair = provider._mint_pair(conn, "replay-client", tenant["user_id"], tenant["org_id"],
                                   [mcp_oauth.READ_SCOPE], "https://mcp.test/mcp")
    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host="mcp.test", open_mode=False)
    form = {"grant_type": "refresh_token", "client_id": "replay-client", "refresh_token": pair.refresh_token}
    with TestClient(app, base_url="https://mcp.test", client=("127.0.0.1", 44321)) as http:
        first = http.post("/mcp/token", data=form)
        assert first.status_code == 200, first.text
        rotated = first.json()
        replay = http.post("/mcp/token", data=form)
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    async def tokens_active():
        at = await provider.load_access_token(rotated["access_token"])
        from mcp.shared.auth import OAuthClientInformationFull
        rt = await provider.load_refresh_token(
            OAuthClientInformationFull(client_id="replay-client", redirect_uris=["https://client.test/cb"]),
            rotated["refresh_token"])
        return at, rt

    at, rt = asyncio.run(tokens_active())
    assert at is None, "rotated access token must be revoked after reuse"
    assert rt is None, "rotated refresh token must be revoked after reuse"


# 2. DCR limits.
def test_registration_rejects_too_many_redirect_uris_and_long_names(tmp_path) -> None:
    from mcp.server.auth.provider import RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull

    _, engine, _ = _tenant(tmp_path, "dcr")
    provider = mcp_oauth.HojaOAuthProvider(engine, Store(engine), True)
    too_many = OAuthClientInformationFull(
        client_id="a", redirect_uris=[f"https://c{i}.test/cb" for i in range(mcp_oauth.MAX_REDIRECT_URIS + 1)])
    with pytest.raises(RegistrationError):
        asyncio.run(provider.register_client(too_many))
    long_name = OAuthClientInformationFull(
        client_id="b", client_name="x" * (mcp_oauth.MAX_CLIENT_NAME_LENGTH + 1),
        redirect_uris=["https://client.test/cb"])
    with pytest.raises(RegistrationError):
        asyncio.run(provider.register_client(long_name))
    ok = OAuthClientInformationFull(client_id="c", client_name="fine", redirect_uris=["https://client.test/cb"])
    asyncio.run(provider.register_client(ok))


def test_registration_global_rate_limit(tmp_path, monkeypatch) -> None:
    from mcp.server.auth.provider import RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull

    _, engine, _ = _tenant(tmp_path, "rate")
    provider = mcp_oauth.HojaOAuthProvider(engine, Store(engine), True)
    monkeypatch.setattr(mcp_oauth, "MAX_REGISTRATIONS_PER_HOUR", 2)
    for i in range(2):
        asyncio.run(provider.register_client(
            OAuthClientInformationFull(client_id=f"r{i}", redirect_uris=["https://client.test/cb"])))
    with pytest.raises(RegistrationError):
        asyncio.run(provider.register_client(
            OAuthClientInformationFull(client_id="r2", redirect_uris=["https://client.test/cb"])))


def test_registration_total_client_cap(tmp_path, monkeypatch) -> None:
    from mcp.server.auth.provider import RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull

    _, engine, _ = _tenant(tmp_path, "cap")
    provider = mcp_oauth.HojaOAuthProvider(engine, Store(engine), True)
    monkeypatch.setattr(mcp_oauth, "MAX_OAUTH_CLIENTS", 1)
    asyncio.run(provider.register_client(
        OAuthClientInformationFull(client_id="cap1", redirect_uris=["https://client.test/cb"])))
    with pytest.raises(RegistrationError):
        asyncio.run(provider.register_client(
            OAuthClientInformationFull(client_id="cap2", redirect_uris=["https://client.test/cb"])))


def test_oauth_module_has_no_production_assert() -> None:
    import ast

    tree = ast.parse((ROOT / "cliniar_server" / "mcp_oauth.py").read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]


# 3. Model provider base_url validation.
@pytest.fixture
def no_insecure_flag(monkeypatch):
    monkeypatch.delenv(model_providers.ALLOW_INSECURE_ENV, raising=False)


def _resolve_to(monkeypatch, ip):
    monkeypatch.setattr(model_providers.socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, 1, 6, "", (ip, 0))])


@pytest.mark.parametrize("url", [
    "http://api.example.test/v1",
    "https://127.0.0.1/v1",
    "https://localhost/v1",
    "https://169.254.169.254/latest",
    "https://10.0.0.5/v1",
    "https://192.168.1.10/v1",
    "https://[fe80::1]/v1",
    "https://[fc00::1]/v1",
    "https://[::ffff:127.0.0.1]/v1",
    "https://user:pw@api.example.test/v1",
    "ftp://api.example.test/v1",
])
def test_model_base_url_rejects_unsafe(url, no_insecure_flag) -> None:
    with pytest.raises(ValueError):
        model_providers.validate_model_base_url(url)


def test_model_base_url_rejects_hostname_resolving_to_private(no_insecure_flag, monkeypatch) -> None:
    _resolve_to(monkeypatch, "10.1.2.3")
    with pytest.raises(ValueError):
        model_providers.validate_model_base_url("https://api.example.test/v1")


def test_model_base_url_allows_public_https(no_insecure_flag, monkeypatch) -> None:
    _resolve_to(monkeypatch, "8.8.8.8")
    assert model_providers.validate_model_base_url("https://api.example.test/v1") == "https://api.example.test/v1"


def test_model_base_url_insecure_flag_allows_localhost_http(monkeypatch) -> None:
    monkeypatch.setenv(model_providers.ALLOW_INSECURE_ENV, "1")
    assert model_providers.validate_model_base_url("http://localhost:11434/v1")
    assert model_providers.validate_model_base_url("https://10.0.0.5/v1")


def test_model_provider_mutation_rejects_unsafe_base_url(tmp_path, no_insecure_flag, monkeypatch) -> None:
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    _resolve_to(monkeypatch, "127.0.0.1")
    path, _, admin = _tenant(tmp_path, "mp")
    app = create_app(str(path), open_mode=False)
    query = ("mutation($n: String!, $p: String!, $m: String!, $k: String!, $b: String) "
             "{ modelProviderCreate(name: $n, provider: $p, modelId: $m, apiKey: $k, baseUrl: $b) { id } }")
    with TestClient(app) as client:
        res = client.post("/graphql", headers={"Authorization": f"Bearer {admin['token']}"}, json={
            "query": query, "variables": {"n": "x", "p": "openai", "m": "m", "k": "k",
                                          "b": "https://169.254.169.254/v1"}}).json()
    assert res.get("errors"), res


# 4. Thread / conversation bounds.
def test_thread_message_bounds() -> None:
    too_many = [{"role": "user", "content": "hi"}] * (agent_threads.MAX_MESSAGES_PER_THREAD + 1)
    with pytest.raises(ValueError):
        agent_threads.validate_thread_messages(too_many)
    big = [{"role": "user", "content": "x" * (agent_threads.MAX_MESSAGE_BYTES + 1)}]
    with pytest.raises(ValueError):
        agent_threads.validate_thread_messages(big)
    agent_threads.validate_thread_messages([{"role": "user", "content": "x" * agent_threads.MAX_MESSAGE_BYTES}])


def test_thread_title_and_save_message_bounds(tmp_path) -> None:
    from cliniar_server.agent_conversations import (
        MAX_MESSAGE_BYTES,
        save_message,
        start_conversation,
    )

    _, engine, tenant = _tenant(tmp_path, "thr")
    store = Store(engine)
    with pytest.raises(ValueError):
        agent_threads.update_thread(store, tenant["org_id"], tenant["user_id"], "missing",
                                    "t" * (agent_threads.MAX_TITLE_INPUT + 1))
    with engine.begin() as conn:
        cid = start_conversation(conn, tenant["org_id"], tenant["user_id"])
        with pytest.raises(ValueError):
            save_message(conn, cid, "user", "x" * (MAX_MESSAGE_BYTES + 1))


def test_thread_create_rejects_oversized_message(tmp_path) -> None:
    _, engine, tenant = _tenant(tmp_path, "thr2")
    with pytest.raises(ValueError):
        agent_threads.create_thread(Store(engine), tenant["org_id"], tenant["user_id"],
                                    [{"role": "user", "content": "x" * (agent_threads.MAX_MESSAGE_BYTES + 1)}])


# 5. Docker hardening.
def test_dockerfile_runs_as_non_root_without_embedded_db_credentials() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "USER cliniar" in text
    assert "cliniar:cliniar@" not in text
    assert "CLINIAR_DATABASE_URL" not in text


def test_compose_requires_postgres_password() -> None:
    text = (ROOT / "docker-compose.yml").read_text()
    assert "change-me" not in text
    assert "${POSTGRES_PASSWORD:?" in text


def test_dockerignore_excludes_secrets_and_state() -> None:
    lines = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()}
    for entry in (".git", ".env", ".venv", ".swarm", ".cliniar", "tests", "docs", "frontend/node_modules"):
        assert entry in lines
