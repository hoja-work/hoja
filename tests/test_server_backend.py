"""Focused, token-free tests for the self-hosted backend."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect, select
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cliniar_server.app import create_app
from cliniar_server.db import (
    CURRENT_SCHEMA_REVISION,
    api_key,
    applied_schema_revision,
    attachment,
    cycle,
    display_database_target,
    global_identity,
    identity_credential,
    issue,
    make_engine,
    metadata,
    migrate,
    new_id,
    now_iso,
    organization_membership,
    project,
    project_team,
    resolve_database_target,
    resolve_token_identity,
    schema_revision,
    seed_tenant,
    team,
    team_member,
    token_hash,
    workflow_state,
)
from cliniar_server.store import Store
from cliniar_server.writer import InvalidReferenceError, Writer


def test_oauth_pending_survives_provider_instance_and_binds_session(tmp_path) -> None:
    pytest.importorskip("mcp")
    from urllib.parse import parse_qs, urlparse

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    from cliniar_server import db
    from cliniar_server.mcp_oauth import READ_SCOPE, HojaOAuthProvider, _digest

    path = tmp_path / "durable-oauth.db"
    engine = make_engine(path)
    migrate(engine)
    _seed(engine, name="OAuth", key="oauth", email="oauth@example.test")
    store = Store(engine)
    first = HojaOAuthProvider(engine, store, True)
    second = HojaOAuthProvider(make_engine(path), store, True)
    client = OAuthClientInformationFull(client_id="client", redirect_uris=["https://client.test/cb"])
    import asyncio
    import os
    os.environ["HOJA_TRUSTED_PUBLIC_HOST"] = "mcp.test"
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id="client", client_json=client.model_dump_json(), created_at=now_iso()))
    params = AuthorizationParams(state="state", scopes=[READ_SCOPE], code_challenge="challenge", redirect_uri="https://client.test/cb", redirect_uri_provided_explicitly=True, resource="https://mcp.test/mcp")
    path_url = asyncio.run(first.authorize(client, params))
    pending_id = parse_qs(urlparse(path_url).query)["pending"][0]
    with engine.connect() as conn:
        pending = conn.execute(select(db.oauth_pending).where(db.oauth_pending.c.pending_hash == _digest(pending_id))).mappings().one()
    assert pending["pending_hash"] != pending_id
    assert pending["resource"] == params.resource
    assert pending["client_id"] == "client" and pending["user_id"] is None
    assert second.engine is not None
    os.environ.pop("HOJA_TRUSTED_PUBLIC_HOST", None)


def test_oauth_refresh_replay_revokes_family_and_persists_resource(tmp_path) -> None:
    pytest.importorskip("mcp")
    import asyncio

    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull

    from cliniar_server import db
    from cliniar_server.mcp_oauth import READ_SCOPE, HojaOAuthProvider

    engine = make_engine(tmp_path / "oauth-refresh.db")
    migrate(engine)
    tenant = _seed(engine, name="OAuth refresh", key="oauth-refresh", email="oauth-refresh@example.test")
    provider = HojaOAuthProvider(engine, Store(engine), True)
    client = OAuthClientInformationFull(client_id="refresh-client", redirect_uris=["https://client.test/cb"])
    resource = "https://mcp.test/mcp"
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id=client.client_id, client_json=client.model_dump_json(), created_at=now_iso()))
        initial = provider._mint_pair(conn, client.client_id, tenant["user_id"], tenant["org_id"], [READ_SCOPE], resource)
    with engine.connect() as conn:
        from cliniar_server.mcp_oauth import _digest
        minted_refresh = conn.execute(select(db.oauth_refresh_token).where(db.oauth_refresh_token.c.token_hash == _digest(initial.refresh_token))).mappings().one()
        assert minted_refresh["resource"] == resource
    original = asyncio.run(provider.load_refresh_token(client, initial.refresh_token))
    rotated = asyncio.run(provider.exchange_refresh_token(client, original, []))
    latest_access = asyncio.run(provider.load_access_token(rotated.access_token))
    rotated_refresh = asyncio.run(provider.load_refresh_token(client, rotated.refresh_token))
    assert latest_access.resource == resource
    assert rotated_refresh.resource == resource

    with pytest.raises(TokenError):
        asyncio.run(provider.exchange_refresh_token(client, original, []))
    assert asyncio.run(provider.load_access_token(rotated.access_token)) is None
    assert asyncio.run(provider.load_refresh_token(client, rotated.refresh_token)) is None
    with engine.connect() as conn:
        rows = conn.execute(select(db.oauth_access_token).where(db.oauth_access_token.c.grant_id == minted_refresh["grant_id"])).mappings().all()
        refresh_rows = conn.execute(select(db.oauth_refresh_token).where(db.oauth_refresh_token.c.grant_id == minted_refresh["grant_id"])).mappings().all()
    assert all(row["revoked_at"] for row in rows)
    assert all(row["revoked_at"] for row in refresh_rows)


def test_oauth_refresh_scope_escalation_keeps_original_usable(tmp_path) -> None:
    pytest.importorskip("mcp")
    import asyncio

    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull

    from cliniar_server import db
    from cliniar_server.mcp_oauth import READ_SCOPE, WRITE_SCOPE, HojaOAuthProvider

    engine = make_engine(tmp_path / "oauth-scope.db")
    migrate(engine)
    tenant = _seed(engine, name="OAuth scope", key="oauth-scope", email="oauth-scope@example.test")
    provider = HojaOAuthProvider(engine, Store(engine), True)
    client = OAuthClientInformationFull(client_id="scope-client", redirect_uris=["https://client.test/cb"])
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id=client.client_id, client_json=client.model_dump_json(), created_at=now_iso()))
        initial = provider._mint_pair(conn, client.client_id, tenant["user_id"], tenant["org_id"], [READ_SCOPE], "https://mcp.test/mcp")
    original = asyncio.run(provider.load_refresh_token(client, initial.refresh_token))
    with pytest.raises(TokenError) as error:
        asyncio.run(provider.exchange_refresh_token(client, original, [WRITE_SCOPE]))
    assert error.value.error == "invalid_scope"
    assert asyncio.run(provider.load_refresh_token(client, initial.refresh_token)) is not None


def test_oauth_authorization_code_concurrent_redemption_is_single_use(tmp_path) -> None:
    pytest.importorskip("mcp")
    import asyncio
    from datetime import datetime, timedelta, timezone

    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull

    from cliniar_server import db
    from cliniar_server.mcp_oauth import READ_SCOPE, HojaOAuthProvider

    engine = make_engine(tmp_path / "oauth-code-race.db")
    migrate(engine)
    tenant = _seed(engine, name="OAuth race", key="oauth-race", email="oauth-race@example.test")
    provider = HojaOAuthProvider(engine, Store(engine), True)
    client = OAuthClientInformationFull(client_id="race-client", redirect_uris=["https://client.test/cb"])
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id=client.client_id,
            client_json=client.model_dump_json(), created_at=now_iso()))
        provider._mint_pair(conn, client.client_id, tenant["user_id"], tenant["org_id"],
                            [READ_SCOPE], "https://mcp.test/mcp")
        code = "race-authorization-code"
        from cliniar_server.mcp_oauth import _digest
        conn.execute(db.oauth_authorization.insert().values(code_hash=_digest(code), client_id=client.client_id,
            user_id=tenant["user_id"], organization_id=tenant["org_id"], scopes='["hoja:read"]',
            code_challenge="challenge", redirect_uri="https://client.test/cb", resource="https://mcp.test/mcp",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), consumed_at=None))
    authorization = asyncio.run(provider.load_authorization_code(client, code))
    assert authorization is not None
    def redeem():
        try:
            return asyncio.run(provider.exchange_authorization_code(client, authorization))
        except TokenError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem(), range(2)))
    assert sum(result is not None for result in results) == 1


def _seed(
    engine,
    *,
    name: str,
    key: str,
    email: str,
    team_key: str = "ENG",
) -> dict:
    return seed_tenant(
        engine,
        org_name=name,
        org_url_key=key,
        team_key=team_key,
        user_name=f"{name} User",
        user_email=email,
        demo_issues=False,
    )


def test_database_target_precedence(monkeypatch, tmp_path) -> None:
    sqlite_path = tmp_path / "fallback.db"
    monkeypatch.setenv("CLINIAR_DATABASE_URL", "sqlite:///environment.db")

    assert resolve_database_target(
        database_url="sqlite:///explicit.db",
        db_path=sqlite_path,
        tenant="ignored",
    ) == "sqlite:///explicit.db"
    assert resolve_database_target(db_path=sqlite_path) == "sqlite:///environment.db"

    monkeypatch.delenv("CLINIAR_DATABASE_URL")
    assert resolve_database_target(db_path=sqlite_path) == str(sqlite_path)


def test_mcp_oauth_http_discovery_and_dynamic_registration(tmp_path) -> None:
    pytest.importorskip("mcp")
    from cliniar_server.db import make_engine, migrate

    migrate(make_engine(tmp_path / "oauth-http.db"))
    app = create_app(
        db_path=str(tmp_path / "oauth-http.db"),
        trusted_proxy_cidrs="127.0.0.1/32",
        trusted_public_host="mcp.example.test",
    )
    with TestClient(app, base_url="https://mcp.example.test", client=("127.0.0.1", 44321)) as client:
        authorization = client.get("/.well-known/oauth-authorization-server/mcp")
        assert authorization.status_code == 200
        auth_metadata = authorization.json()
        assert auth_metadata["issuer"] == "https://mcp.example.test/mcp"
        assert auth_metadata["authorization_endpoint"] == "https://mcp.example.test/mcp/authorize"
        assert auth_metadata["registration_endpoint"] == "https://mcp.example.test/mcp/register"

        protected = client.get("/.well-known/oauth-protected-resource/mcp")
        assert protected.status_code == 200
        resource_metadata = protected.json()
        assert resource_metadata["resource"] == "https://mcp.example.test/mcp"
        assert resource_metadata["authorization_servers"] == ["https://mcp.example.test/mcp"]

        registration = client.post(
            "/mcp/register", headers={"Host": "mcp.example.test"},
            json={"redirect_uris": ["https://client.example/callback"], "token_endpoint_auth_method": "none"},
        )
        assert registration.status_code == 201
        assert registration.json()["client_id"]

        unsafe = client.post(
            "/mcp/register", headers={"Host": "mcp.example.test"},
            json={"redirect_uris": ["https://client.example/callback#fragment"], "token_endpoint_auth_method": "none"},
        )
        assert unsafe.status_code == 400


def test_mcp_oauth_http_authorization_code_pkce_flow(tmp_path, monkeypatch) -> None:
    pytest.importorskip("mcp")
    import base64
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlencode, urlparse

    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_HOST", "mcp.example.test")
    monkeypatch.setenv("HOJA_TRUSTED_PUBLIC_SCHEME", "https")

    path = tmp_path / "oauth-pkce-http.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="OAuth Browser", org_url_key="oauth-browser",
                         user_name="OAuth User", user_email="oauth-browser@example.test", demo_issues=False)
    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host="mcp.example.test", open_mode=False)
    with TestClient(app, base_url="https://mcp.example.test", client=("127.0.0.1", 44321), follow_redirects=False) as client:
        assert client.post("/auth/setup", json={"token": seeded["token"],
            "password": "correct horse battery"}).status_code == 201
        login = client.post("/auth/login", json={"email": "oauth-browser@example.test",
            "password": "correct horse battery"})
        assert login.status_code == 200 and "hoja_session" in login.cookies

        registered = client.post("/mcp/register", json={
            "redirect_uris": ["https://client.example/callback"], "token_endpoint_auth_method": "none",
        })
        assert registered.status_code == 201
        client_id = registered.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = "state-value"
        authorize = client.get("/mcp/authorize?" + urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": "https://client.example/callback",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "scope": "hoja:read", "resource": "https://mcp.example.test/mcp",
        }))
        assert authorize.status_code in (302, 303)
        consent_url = authorize.headers["location"]
        assert "/oauth/consent?" in consent_url
        consent_path = "/mcp" + urlparse(consent_url).path.rstrip("/") + "?" + urlparse(consent_url).query
        consent = client.get(consent_path, headers={"host": "mcp.example.test"})
        assert consent.status_code == 200
        assert "Authorize MCP client" in consent.text
        import re
        csrf = re.search(r'name=csrf_token value="([^"]+)"', consent.text).group(1)
        denied = client.post(consent_path, data={"decision": "allow", "csrf_token": "wrong"},
                             headers={"host": "mcp.example.test"})
        assert denied.status_code == 400
        approved = client.post(consent_path, data={"decision": "allow", "csrf_token": csrf},
                               headers={"host": "mcp.example.test"})
        assert approved.status_code in (302, 303, 307)
        redirect = urlparse(approved.headers["location"])
        result = parse_qs(redirect.query)
        assert redirect.netloc == "client.example" and result["state"] == [state]
        code = result["code"][0]

        wrong = client.post("/mcp/token", data={"grant_type": "authorization_code", "client_id": client_id,
            "code": code, "redirect_uri": "https://client.example/callback", "code_verifier": "incorrect"})
        assert wrong.status_code == 400
        token = client.post("/mcp/token", data={"grant_type": "authorization_code", "client_id": client_id,
            "code": code, "redirect_uri": "https://client.example/callback", "code_verifier": verifier})
        assert token.status_code == 200
        assert token.json()["token_type"].lower() == "bearer"
        assert token.json()["access_token"] and token.json()["refresh_token"]
        replay = client.post("/mcp/token", data={"grant_type": "authorization_code", "client_id": client_id,
            "code": code, "redirect_uri": "https://client.example/callback", "code_verifier": verifier})
        assert replay.status_code == 400


def test_mcp_http_rejects_missing_and_invalid_bearer_tokens(tmp_path) -> None:
    pytest.importorskip("mcp")
    from cliniar_server.db import make_engine, migrate

    path = tmp_path / "oauth-calls.db"
    migrate(make_engine(path))
    _seed(make_engine(path), name="MCP calls", key="mcp-calls", email="mcp@example.test")
    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host="mcp.example.test")
    with TestClient(app) as client:
        assert client.get("/mcp/", headers={"Host": "mcp.example.test"}).status_code in (401, 403)
        assert client.get("/mcp/", headers={"Host": "mcp.example.test", "Authorization": "Bearer invalid"}).status_code in (401, 403)


def test_mcp_streamable_http_lifecycle_and_host_validation(tmp_path) -> None:
    pytest.importorskip("mcp")
    from mcp.types import LATEST_PROTOCOL_VERSION

    from cliniar_server import db
    from cliniar_server.mcp_oauth import READ_SCOPE, HojaOAuthProvider

    path = tmp_path / "mcp-sdk-lifecycle.db"
    engine = make_engine(path)
    migrate(engine)
    tenant = _seed(engine, name="MCP lifecycle", key="mcp-life", email="mcp-life@example.test")
    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host="mcp.example.test")
    provider = HojaOAuthProvider(engine, Store(engine), True)
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(client_id="synthetic", client_json='{"client_id":"synthetic","redirect_uris":["https://client.test/cb"],"token_endpoint_auth_method":"none"}', created_at=now_iso()))
        token = provider._mint_pair(conn, "synthetic", tenant["user_id"], tenant["org_id"], [READ_SCOPE], "https://mcp.example.test/mcp")

    with TestClient(app, base_url="https://mcp.example.test", client=("127.0.0.1", 44321)) as client:
        headers = {"Host": "mcp.example.test", "Authorization": f"Bearer {token.access_token}"}
        base = {"jsonrpc": "2.0", "id": 1}
        init = client.post("/mcp/", headers=headers, json={**base, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        assert init.status_code == 200, init.text
        envelope = {"_meta": {"io.modelcontextprotocol/protocolVersion": LATEST_PROTOCOL_VERSION, "io.modelcontextprotocol/clientCapabilities": {}}}
        session_headers = {**headers, "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION, "Mcp-Method": "tools/list"}
        listed = client.post("/mcp/", headers=session_headers, json={**base, "id": 2, "method": "tools/list", "params": envelope})
        assert listed.status_code == 200, listed.text
        assert any(item["name"] == "hoja_viewer" for item in listed.json()["result"]["tools"])
        call_headers = {**session_headers, "Mcp-Method": "tools/call", "Mcp-Name": "hoja_viewer"}
        called = client.post("/mcp/", headers=call_headers, json={**base, "id": 3, "method": "tools/call", "params": {**envelope, "name": "hoja_viewer", "arguments": {}}})
        assert called.status_code == 200, called.text
        assert not called.json()["result"].get("isError"), called.json()
        denied = client.post("/mcp/", headers={**session_headers, "Mcp-Method": "tools/call", "Mcp-Name": "hoja_teamCreate"}, json={**base, "id": 4, "method": "tools/call", "params": {**envelope, "name": "hoja_teamCreate", "arguments": {"name": "No"}}})
        assert denied.status_code == 200 and denied.json()["result"].get("isError")
        assert "hoja:write" in denied.json()["result"]["content"][0]["text"], denied.json()
        wrong_host = client.post("/mcp/", headers={**session_headers, "Host": "attacker.invalid"}, json={**base, "id": 5, "method": "tools/list", "params": envelope})
        assert wrong_host.status_code == 421

        with engine.begin() as conn:
            conn.execute(db.oauth_access_token.update().where(db.oauth_access_token.c.token_hash == __import__("cliniar_server.mcp_oauth", fromlist=["_digest"])._digest(token.access_token)).values(revoked_at=now_iso()))
        revoked = client.post("/mcp/", headers={**session_headers, "Mcp-Method": "tools/list"}, json={**base, "id": 6, "method": "tools/list", "params": envelope})
        assert revoked.status_code in (401, 403)



def test_app_starts_without_oauth_feature_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HOJA_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("HOJA_TRUSTED_PUBLIC_HOST", raising=False)
    app = create_app(db_path=str(tmp_path / "oauth-disabled.db"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_oauth_trusted_proxy_cidr_and_public_host_fail_closed(tmp_path) -> None:
    path = tmp_path / "oauth-proxy-boundary.db"
    migrate(make_engine(path))
    trusted = create_app(
        db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32",
        trusted_public_host="mcp.example.test", open_mode=False,
    )
    with TestClient(trusted, client=("198.51.100.25", 44321)) as client:
        # Forwarded headers do not upgrade an untrusted peer into a trusted proxy.
        result = client.get(
            "/.well-known/oauth-authorization-server/mcp",
            headers={"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "mcp.example.test"},
        )
        assert result.status_code == 404

    trusted_peer = create_app(
        db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32",
        trusted_public_host="mcp.example.test", open_mode=False,
    )
    with TestClient(trusted_peer, client=("127.0.0.1", 44321)) as client:
        assert client.get("/.well-known/oauth-authorization-server/mcp").status_code == 200
        bad_host = client.get(
            "/mcp/", headers={"Host": "attacker.example.test", "X-Forwarded-Host": "mcp.example.test"}
        )
        assert bad_host.status_code == 421

    with pytest.raises(ValueError, match="HOJA_TRUSTED_PROXY_CIDRS"):
        create_app(db_path=str(path), trusted_proxy_cidrs="not-a-network",
                   trusted_public_host="mcp.example.test", open_mode=False)

    invalid_host = create_app(
        db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32",
        trusted_public_host="https://evil.example.test/path", open_mode=False,
    )
    with TestClient(invalid_host, client=("127.0.0.1", 44321)) as client:
        assert client.get("/.well-known/oauth-authorization-server/mcp").status_code == 404


@pytest.mark.parametrize("target", ["database.db", "sqlite:///:memory:"])
def test_make_engine_accepts_path_or_url(target) -> None:
    engine = make_engine(target)
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_make_engine_uses_psycopg3_and_redacts_passwords() -> None:
    pytest.importorskip("psycopg")
    engine = make_engine("postgresql://user:secret@db.example.test/cliniar")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
    assert display_database_target(
        "postgresql://user:secret@db.example.test/cliniar"
    ) == "postgresql://user:***@db.example.test/cliniar"


def test_migrate_upgrades_a_recorded_prior_schema(tmp_path) -> None:
    engine = make_engine(tmp_path / "prior-schema.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        attachment.drop(conn)
        project_team.drop(conn)
        conn.execute(schema_revision.insert().values(revision=1, applied_at=now_iso()))

    migrate(engine)

    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    assert inspect(engine).has_table(attachment.name)
    assert inspect(engine).has_table(project_team.name)
    indexes = inspect(engine).get_indexes(api_key.name)
    assert any(
        index["name"] == "uq_api_key_token_hash" and index["unique"]
        for index in indexes
    )


def test_identity_membership_migration_preserves_duplicate_email_profiles(tmp_path) -> None:
    engine = make_engine(tmp_path / "identity-migration.db")
    migrate(engine)
    first = seed_tenant(engine, org_name="First", org_url_key="identity-first",
                        user_name="Same Person One", user_email="same@example.test", demo_issues=False)
    second = seed_tenant(engine, org_name="Second", org_url_key="identity-second",
                         user_name="Same Person Two", user_email="same@example.test", demo_issues=False)
    with engine.begin() as conn:
        organization_membership.drop(conn)
        global_identity.drop(conn)
        conn.execute(schema_revision.delete().where(schema_revision.c.revision >= 6))
    with engine.begin() as conn:
        # Simulate a revision-5 database retaining the old password hashes.
        from cliniar_server.db import password_credential
        for seeded in (first, second):
            conn.execute(password_credential.insert().values(
                user_id=seeded["user_id"], password_hash=f"legacy-hash-{seeded['user_id']}",
                created_at=now_iso(), updated_at=now_iso()))
    migrate(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(organization_membership.c.identity_id,
                                   organization_membership.c.organization_id,
                                   organization_membership.c.profile_user_id)).all()
        credentials = conn.execute(select(identity_credential.c.password_hash)).scalars().all()
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    mapped = {row.profile_user_id: (row.identity_id, row.organization_id) for row in rows}
    assert mapped[first["user_id"]][1] == first["org_id"]
    assert mapped[second["user_id"]][1] == second["org_id"]
    assert mapped[first["user_id"]][0] != mapped[second["user_id"]][0]
    assert sorted(credentials) == sorted([
        f"legacy-hash-{first['user_id']}", f"legacy-hash-{second['user_id']}"])


def test_migrate_rejects_unsafe_duplicate_legacy_tokens(tmp_path) -> None:
    engine = make_engine(tmp_path / "duplicate-legacy-token.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        attachment.drop(conn)
        project_team.drop(conn)
        conn.execute(schema_revision.insert().values(revision=1, applied_at=now_iso()))
    seeded = _seed(engine, name="Alpha", key="alpha", email="alpha@example.test")
    with engine.begin() as conn:
        existing_hash = conn.execute(select(api_key.c.token_hash)).scalar_one()
        conn.execute(
            api_key.insert().values(
                id=new_id(),
                token_hash=existing_hash,
                user_id=seeded["user_id"],
                organization_id=seeded["org_id"],
                created_at=now_iso(),
            )
        )

    with pytest.raises(RuntimeError, match="duplicate API token hashes"):
        migrate(engine)
    assert applied_schema_revision(engine) == 1


def test_migrate_rejects_a_gapped_revision_ledger(tmp_path) -> None:
    engine = make_engine(tmp_path / "gapped-revisions.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            schema_revision.insert(),
            [
                {"revision": 1, "applied_at": now_iso()},
                {"revision": 3, "applied_at": now_iso()},
            ],
        )

    with pytest.raises(RuntimeError, match="not a contiguous prefix"):
        applied_schema_revision(engine)
    with pytest.raises(RuntimeError, match="not a contiguous prefix"):
        migrate(engine)


def test_shared_schema_isolates_organizations_and_rejects_duplicate_key(tmp_path) -> None:
    engine = make_engine(tmp_path / "shared.db")
    migrate(engine)
    alpha = _seed(engine, name="Alpha", key="alpha", email="alpha@example.test")
    beta = _seed(engine, name="Beta", key="beta", email="beta@example.test")
    writer = Writer(engine)
    store = Store(engine)

    alpha_issue = writer.issue_create(
        alpha["org_id"], alpha["user_id"], {"teamId": "ENG", "title": "Alpha only"}
    )
    beta_issue = writer.issue_create(
        beta["org_id"], beta["user_id"], {"teamId": "ENG", "title": "Beta only"}
    )

    assert alpha_issue is not None
    assert beta_issue is not None
    assert store.issue_by_id_or_identifier(alpha["org_id"], "ENG-1")["title"] == "Alpha only"
    assert store.issue_by_id_or_identifier(beta["org_id"], "ENG-1")["title"] == "Beta only"
    assert store.issue_by_id_or_identifier(alpha["org_id"], beta_issue) is None
    with pytest.raises(ValueError, match="already exists"):
        _seed(engine, name="Duplicate", key="alpha", email="other@example.test")


def test_api_tokens_are_globally_unique_and_identity_consistent(tmp_path) -> None:
    engine = make_engine(tmp_path / "token-constraints.db")
    migrate(engine)
    shared_token = "cliniar_test_shared_token"
    alpha = seed_tenant(
        engine,
        org_name="Alpha",
        org_url_key="alpha",
        user_email="alpha@example.test",
        token=shared_token,
        demo_issues=False,
    )
    with pytest.raises(ValueError, match="API token already exists"):
        seed_tenant(
            engine,
            org_name="Beta",
            org_url_key="beta",
            user_email="beta@example.test",
            token=shared_token,
            demo_issues=False,
        )

    beta = _seed(engine, name="Beta", key="beta", email="beta@example.test")
    inconsistent_token = "cliniar_test_inconsistent_identity"
    with engine.begin() as conn:
        conn.execute(
            api_key.insert().values(
                id=new_id(),
                token_hash=token_hash(inconsistent_token),
                user_id=beta["user_id"],
                organization_id=alpha["org_id"],
                created_at=now_iso(),
            )
        )
    assert Store(engine).resolve_token(inconsistent_token) is None


def test_issue_references_cannot_cross_organization_boundaries(tmp_path) -> None:
    engine = make_engine(tmp_path / "reference-isolation.db")
    migrate(engine)
    alpha = _seed(engine, name="Alpha", key="alpha", email="alpha@example.test")
    beta = _seed(engine, name="Beta", key="beta", email="beta@example.test")
    writer = Writer(engine)

    beta_project = writer.project_create(
        beta["org_id"], beta["user_id"], {"name": "Beta project"}
    )
    beta_parent = writer.issue_create(
        beta["org_id"], beta["user_id"], {"teamId": "ENG", "title": "Beta parent"}
    )
    beta_label = writer.label_create(beta["org_id"], {"name": "Beta label"})
    with engine.begin() as conn:
        beta_state = conn.execute(
            select(workflow_state.c.id).where(
                workflow_state.c.organization_id == beta["org_id"]
            )
        ).scalars().first()
        beta_cycle = new_id()
        conn.execute(
            cycle.insert().values(
                id=beta_cycle,
                organization_id=beta["org_id"],
                team_id=beta["team_id"],
                number=1,
                name="Beta cycle",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )

    foreign_inputs = {
        "stateId": beta_state,
        "assigneeId": beta["user_id"],
        "projectId": beta_project,
        "cycleId": beta_cycle,
        "parentId": beta_parent,
        "labelIds": [beta_label],
    }
    for field, value in foreign_inputs.items():
        with pytest.raises(InvalidReferenceError) as exc_info:
            writer.issue_create(
                alpha["org_id"],
                alpha["user_id"],
                {"teamId": "ENG", "title": f"Reject {field}", field: value},
            )
        assert exc_info.value.extensions == {
            "code": "BAD_USER_INPUT",
            "field": field,
        }

    alpha_issue = writer.issue_create(
        alpha["org_id"], alpha["user_id"], {"teamId": "ENG", "title": "Alpha issue"}
    )
    for field, value in foreign_inputs.items():
        with pytest.raises(InvalidReferenceError):
            writer.issue_update(alpha["org_id"], alpha_issue, {field: value})

    with engine.connect() as conn:
        counter = conn.execute(
            select(team.c.issue_counter).where(team.c.id == alpha["team_id"])
        ).scalar_one()
        title = conn.execute(
            select(issue.c.title).where(issue.c.id == alpha_issue)
        ).scalar_one()
    assert counter == 1
    assert title == "Alpha issue"
    assert writer.attachment_create(
        alpha["org_id"],
        alpha["user_id"],
        {
            "issueId": beta_parent,
            "url": "https://example.test/foreign",
            "title": "Foreign issue",
        },
    ) is None
    with pytest.raises(InvalidReferenceError) as unsafe_url:
        writer.attachment_create(
            alpha["org_id"],
            alpha["user_id"],
            {
                "issueId": alpha_issue,
                "url": "javascript:alert(1)",
                "title": "Unsafe URL",
            },
        )
    assert unsafe_url.value.extensions["field"] == "url"


def test_cycle_and_boolean_issue_filters_do_not_broaden_results(tmp_path) -> None:
    engine = make_engine(tmp_path / "cycle-filters.db")
    migrate(engine)
    seeded = _seed(engine, name="Filters", key="filters", email="filters@example.test")
    writer = Writer(engine)
    with engine.begin() as conn:
        current_cycle = new_id()
        next_cycle = new_id()
        for cycle_id, number, name in (
            (current_cycle, 1, "Current Cycle"),
            (next_cycle, 2, "Next Cycle"),
        ):
            conn.execute(
                cycle.insert().values(
                    id=cycle_id,
                    organization_id=seeded["org_id"],
                    team_id=seeded["team_id"],
                    number=number,
                    name=name,
                    created_at=now_iso(),
                    updated_at=now_iso(),
                )
            )
        conn.execute(
            team.update()
            .where(team.c.id == seeded["team_id"])
            .values(active_cycle_id=current_cycle)
        )
    writer.issue_create(
        seeded["org_id"],
        seeded["user_id"],
        {
            "teamId": seeded["team_id"],
            "title": "Current urgent",
            "cycleId": current_cycle,
            "priority": 1,
        },
    )
    writer.issue_create(
        seeded["org_id"],
        seeded["user_id"],
        {
            "teamId": seeded["team_id"],
            "title": "Next low",
            "cycleId": next_cycle,
            "priority": 4,
        },
    )
    store = Store(engine)

    current = store.issues(
        seeded["org_id"],
        seeded["user_id"],
        {"cycle": {"isActive": {"eq": True}}},
    )
    upcoming = store.issues(
        seeded["org_id"],
        seeded["user_id"],
        {"cycle": {"isNext": {"eq": True}}},
    )
    combined = store.issues(
        seeded["org_id"],
        seeded["user_id"],
        {
            "and": [
                {"title": {"contains": "Current"}},
                {"priority": {"eq": 1}},
            ]
        },
    )
    alternatives = store.issues(
        seeded["org_id"],
        seeded["user_id"],
        {
            "or": [
                {"title": {"eq": "Current urgent"}},
                {"title": {"eq": "Next low"}},
            ]
        },
    )

    assert [row["title"] for row in current] == ["Current urgent"]
    assert [row["title"] for row in upcoming] == ["Next low"]
    assert [row["title"] for row in combined] == ["Current urgent"]
    assert {row["title"] for row in alternatives} == {"Current urgent", "Next low"}


def test_project_and_label_references_are_organization_scoped(tmp_path) -> None:
    engine = make_engine(tmp_path / "project-reference-isolation.db")
    migrate(engine)
    alpha = _seed(engine, name="Alpha", key="alpha", email="alpha@example.test")
    beta = _seed(engine, name="Beta", key="beta", email="beta@example.test")
    writer = Writer(engine)
    second_team_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            team.insert().values(
                id=second_team_id,
                organization_id=alpha["org_id"],
                key="TWO",
                name="Second team",
                issue_counter=0,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
        conn.execute(
            team_member.insert().values(
                team_id=second_team_id,
                user_id=alpha["user_id"],
            )
        )

    with pytest.raises(InvalidReferenceError) as lead_error:
        writer.project_create(
            alpha["org_id"],
            alpha["user_id"],
            {"name": "Invalid lead", "leadId": beta["user_id"]},
        )
    assert lead_error.value.extensions["field"] == "leadId"

    with pytest.raises(InvalidReferenceError) as teams_error:
        writer.project_create(
            alpha["org_id"],
            alpha["user_id"],
            {"name": "Invalid team", "teamIds": [beta["team_id"]]},
        )
    assert teams_error.value.extensions["field"] == "teamIds"

    restricted_project = writer.project_create(
        alpha["org_id"],
        alpha["user_id"],
        {"name": "Second team project", "teamIds": [second_team_id]},
    )
    with pytest.raises(InvalidReferenceError) as project_error:
        writer.issue_create(
            alpha["org_id"],
            alpha["user_id"],
            {
                "teamId": alpha["team_id"],
                "title": "Wrong project team",
                "projectId": restricted_project,
            },
        )
    assert project_error.value.extensions["field"] == "projectId"

    org_wide_project = writer.project_create(
        alpha["org_id"], alpha["user_id"], {"name": "Organization-wide project"}
    )
    org_wide_issue = writer.issue_create(
        alpha["org_id"],
        alpha["user_id"],
        {
            "teamId": alpha["team_id"],
            "title": "Organization-wide project issue",
            "projectId": org_wide_project,
        },
    )
    assert org_wide_issue
    with pytest.raises(InvalidReferenceError):
        writer.issue_update(
            alpha["org_id"],
            org_wide_issue,
            {"projectId": restricted_project},
        )

    with pytest.raises(InvalidReferenceError) as label_error:
        writer.label_create(
            alpha["org_id"], {"name": "Invalid label", "teamId": beta["team_id"]}
        )
    assert label_error.value.extensions["field"] == "teamId"


def test_token_identity_requires_unambiguous_selection_in_sqlite(tmp_path) -> None:
    engine = make_engine(tmp_path / "tokens.db")
    migrate(engine)
    alpha = _seed(engine, name="Alpha", key="alpha", email="alpha@example.test")

    assert resolve_token_identity(engine) == (alpha["user_id"], alpha["org_id"])

    beta = _seed(engine, name="Beta", key="beta", email="beta@example.test")
    with pytest.raises(ValueError, match="Multiple organizations"):
        resolve_token_identity(engine)
    assert resolve_token_identity(
        engine, organization_ref="beta", email="beta@example.test"
    ) == (beta["user_id"], beta["org_id"])
    with pytest.raises(ValueError, match="No matching user"):
        resolve_token_identity(
            engine, organization_ref="alpha", email="beta@example.test"
        )


def test_generated_urls_use_configured_application_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLINIAR_APP_URL", "https://issues.example.test/root/")
    engine = make_engine(tmp_path / "urls.db")
    migrate(engine)
    seeded = _seed(engine, name="Example", key="example", email="user@example.test")
    writer = Writer(engine)

    issue_id = writer.issue_create(
        seeded["org_id"], seeded["user_id"], {"teamId": "ENG", "title": "Hosted issue"}
    )
    project_id = writer.project_create(
        seeded["org_id"], seeded["user_id"], {"name": "Hosted project"}
    )

    with engine.connect() as conn:
        issue_url = conn.execute(select(issue.c.url).where(issue.c.id == issue_id)).scalar_one()
        project_url = conn.execute(
            select(project.c.url).where(project.c.id == project_id)
        ).scalar_one()
    assert issue_url == "https://issues.example.test/root/example/issue/ENG-1"
    assert project_url == (
        "https://issues.example.test/root/example/project/hosted-project"
    )
    assert "linear.app" not in issue_url
    assert "linear.app" not in project_url


def test_concurrent_issue_identifiers_are_atomic_and_monotonic(tmp_path) -> None:
    engine = make_engine(tmp_path / "concurrent.db")
    migrate(engine)
    seeded = _seed(engine, name="Concurrent", key="concurrent", email="user@example.test")
    writer = Writer(engine)

    def create(number: int) -> str | None:
        return writer.issue_create(
            seeded["org_id"],
            seeded["user_id"],
            {"teamId": "ENG", "title": f"Issue {number}"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(20)))

    assert all(ids)
    with engine.connect() as conn:
        rows = conn.execute(
            select(issue.c.number, issue.c.identifier)
            .where(issue.c.organization_id == seeded["org_id"])
            .order_by(issue.c.number)
        ).all()
    assert rows == [(number, f"ENG-{number}") for number in range(1, 21)]


def test_health_and_readiness(tmp_path) -> None:
    db_path = tmp_path / "health.db"
    engine = make_engine(db_path)
    migrate(engine)
    engine.dispose()
    app = create_app(str(db_path), open_mode=False)

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_team_create_graphql_admin_success_and_duplicate_key(tmp_path) -> None:
    db_path = tmp_path / "team-create.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Create Team", org_url_key="create-team",
                         user_email="create@example.test", token="create-team-token",
                         demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    query = "mutation($input: TeamCreateInput!) { teamCreate(input: $input) { success team { id name key description states { nodes { name type } } } } }"
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {seeded['token']}"}
        result = client.post("/graphql", headers=headers, json={
            "query": query,
            "variables": {"input": {"name": "Product Team", "key": "prd", "description": "Product"}},
        }).json()
        payload = result["data"]["teamCreate"]
        assert payload["success"] is True
        assert payload["team"]["name"] == "Product Team"
        assert payload["team"]["key"] == "PRD"
        assert payload["team"]["states"]["nodes"]

        duplicate = client.post("/graphql", headers=headers, json={
            "query": query,
            "variables": {"input": {"name": "Another team", "key": "prd"}},
        }).json()
        assert duplicate["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"


def test_personal_api_key_lifecycle_is_scoped_and_secret_is_one_time(tmp_path) -> None:
    engine = make_engine(tmp_path / "personal-api-keys.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Key Workspace", org_url_key="key-workspace",
                         user_email="keys@example.test", token="keys-seed-token", demo_issues=False)
    store = Store(engine)
    created = store.create_api_key(seeded["org_id"], seeded["user_id"], "Laptop", "read_write")
    assert created is not None
    metadata_row, secret = created
    assert secret.startswith("lin_api_")
    assert "token_hash" not in metadata_row
    assert store.list_api_keys(seeded["org_id"], seeded["user_id"])[-1]["label"] == "Laptop"
    assert store.resolve_token(secret)["id"] == metadata_row["id"]
    assert store.revoke_api_key(seeded["org_id"], seeded["user_id"], metadata_row["id"])
    assert store.resolve_token(secret) is None
    assert not store.revoke_api_key(seeded["org_id"], seeded["user_id"], metadata_row["id"])
    assert not store.revoke_api_key("other-workspace", seeded["user_id"], metadata_row["id"])
    assert store.list_api_keys(seeded["org_id"], seeded["user_id"])[-1]["revokedAt"]


def test_user_preferences_persist_and_validate_values(tmp_path) -> None:
    engine = make_engine(tmp_path / "user-preferences.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Preference Workspace", org_url_key="preference-workspace",
                         user_email="prefs@example.test", token="prefs-seed-token", demo_issues=False)
    store = Store(engine)
    assert store.user_preferences(seeded["user_id"]) == {}
    assert store.set_user_preference(seeded["user_id"], "theme", "dark")
    assert store.set_user_preference(seeded["user_id"], "fontSize", "110")
    assert store.user_preferences(seeded["user_id"]) == {"theme": "dark", "fontSize": "110"}
    assert not store.set_user_preference(seeded["user_id"], "theme", "neon")
    assert not store.set_user_preference(seeded["user_id"], "billing", "enterprise")
    assert store.user_preferences(seeded["user_id"]) == {"theme": "dark", "fontSize": "110"}



def test_user_preferences_graphql_roundtrip_and_invalid_value(tmp_path) -> None:
    db_path = tmp_path / "preferences-graphql.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Preference GraphQL", org_url_key="preferences-graphql",
                         user_email="prefs-gql@example.test", token="prefs-gql-token", demo_issues=False)
    headers = {"Authorization": f"Bearer {seeded['token']}"}
    mutation = "mutation($key:String!,$value:String!){userPreferenceSet(key:$key,value:$value){success theme fontSize}}"
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        response = client.post("/graphql", headers=headers, json={
            "query": mutation, "variables": {"key": "theme", "value": "dark"}}).json()
        assert response["data"]["userPreferenceSet"] == {"success": True, "theme": "dark", "fontSize": "100"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        read = client.post("/graphql", headers=headers, json={
            "query": "{myPreferences{success theme fontSize}}"}).json()
        assert read["data"]["myPreferences"] == {"success": True, "theme": "dark", "fontSize": "100"}
        invalid = client.post("/graphql", headers=headers, json={
            "query": mutation, "variables": {"key": "theme", "value": "neon"}}).json()
        assert invalid["data"]["userPreferenceSet"]["success"] is False
        assert invalid["data"]["userPreferenceSet"]["theme"] == "dark"


def test_profile_name_update_and_notification_preferences_filter_inbox(tmp_path) -> None:
    engine = make_engine(tmp_path / "profile-notifications.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Profile Settings", org_url_key="profile-settings",
                         user_email="settings@example.test", token="settings-token", demo_issues=False)
    store = Store(engine)
    assert store.update_profile_name(seeded["org_id"], seeded["user_id"], "  New Name  ")["name"] == "New Name"
    assert store.update_profile_name(seeded["org_id"], seeded["user_id"], " ") is None
    assert store.set_notification_preference(seeded["user_id"], "issueCreated", False)
    assert not store.set_notification_preference(seeded["user_id"], "unknown", False)
    from cliniar_server.db import inbox_notification
    created_issue = Writer(engine).issue_create(seeded["org_id"], seeded["user_id"], {
        "teamId": seeded["team_id"], "title": "Notification preference test"})
    with engine.begin() as conn:
        for kind in ("issueCreated", "issueUpdated"):
            conn.execute(inbox_notification.insert().values(
                id=new_id(), organization_id=seeded["org_id"], recipient_id=seeded["user_id"],
                issue_id=created_issue, actor_id=None, kind=kind, created_at=now_iso()))
    visible = store.inbox_notifications(seeded["org_id"], seeded["user_id"])
    assert [item["kind"] for item in visible] == ["issueUpdated"]
    assert store.inbox_unread_count(seeded["org_id"], seeded["user_id"]) == 1


def test_profile_notification_and_label_settings_graphql_authorization(tmp_path) -> None:
    db_path = tmp_path / "settings-graphql.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Settings GraphQL", org_url_key="settings-graphql",
                         user_email="settings-admin@example.test", token="settings-admin-token", demo_issues=False)
    member, member_token = Store(engine).invite_member(seeded["org_id"], "Member", "settings-member@example.test")
    assert member and member_token
    admin_headers = {"Authorization": f"Bearer {seeded['token']}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        renamed = client.post("/graphql", headers=admin_headers, json={
            "query": "mutation($name:String!){profileNameUpdate(name:$name){success user{id name email}}}",
            "variables": {"name": "Renamed admin"}}).json()
        assert renamed["data"]["profileNameUpdate"]["user"]["name"] == "Renamed admin"
        preference = client.post("/graphql", headers=admin_headers, json={
            "query": "mutation($kind:String!,$enabled:Boolean!){notificationPreferenceSet(kind:$kind,enabled:$enabled){success issueCreated issueUpdated}}",
            "variables": {"kind": "issueCreated", "enabled": False}}).json()
        assert preference["data"]["notificationPreferenceSet"] == {
            "success": True, "issueCreated": False, "issueUpdated": True}
        created = client.post("/graphql", headers=admin_headers, json={
            "query": "mutation($input:IssueLabelCreateInput!){issueLabelCreate(input:$input){success issueLabel{id name color}}}",
            "variables": {"input": {"name": "Settings label", "color": "#345678"}}}).json()
        label_id = created["data"]["issueLabelCreate"]["issueLabel"]["id"]
        read = client.post("/graphql", headers=member_headers, json={
            "query": "{issueLabelSettings{id name}}"}).json()
        assert any(item["id"] == label_id for item in read["data"]["issueLabelSettings"])
        denied_create = client.post("/graphql", headers=member_headers, json={
            "query": "mutation($input:IssueLabelCreateInput!){issueLabelCreate(input:$input){success}}",
            "variables": {"input": {"name": "Member cannot create"}}}).json()
        assert denied_create["data"]["issueLabelCreate"]["success"] is False
        denied_delete = client.post("/graphql", headers=member_headers, json={
            "query": "mutation($id:String!){issueLabelDelete(id:$id){success}}",
            "variables": {"id": label_id}}).json()
        assert denied_delete["data"]["issueLabelDelete"]["success"] is False
        deleted = client.post("/graphql", headers=admin_headers, json={
            "query": "mutation($id:String!){issueLabelDelete(id:$id){success}}",
            "variables": {"id": label_id}}).json()
        assert deleted["data"]["issueLabelDelete"]["success"] is True

def test_health_stays_available_when_schema_is_unmigrated(tmp_path) -> None:
    app = create_app(str(tmp_path / "unmigrated.db"), open_mode=False)

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "reason": "schema_outdated"}


def test_cloverops_graphql_operation_contract(tmp_path) -> None:
    target = os.environ.get("CLINIAR_TEST_POSTGRES_URL") or (
        tmp_path / "cloverops-contract.db"
    )
    suffix = uuid.uuid4().hex[:12]
    contract_token = f"cliniar_test_cloverops_contract_{suffix}"
    engine = make_engine(target)
    migrate(engine)
    seeded = seed_tenant(
        engine,
        org_name="Contract Organization",
        org_url_key=f"contract-{suffix}",
        team_key="ENG",
        user_name="Contract User",
        user_email="contract@example.test",
        token=contract_token,
        demo_issues=False,
    )
    writer = Writer(engine)
    project_id = writer.project_create(
        seeded["org_id"],
        seeded["user_id"],
        {"name": "Contract Project", "teamIds": [seeded["team_id"]]},
    )
    issue_id = writer.issue_create(
        seeded["org_id"],
        seeded["user_id"],
        {
            "teamId": seeded["team_id"],
            "title": "Contract issue",
            "projectId": project_id,
        },
    )
    with engine.begin() as conn:
        cycle_id = new_id()
        conn.execute(
            cycle.insert().values(
                id=cycle_id,
                organization_id=seeded["org_id"],
                team_id=seeded["team_id"],
                number=1,
                name="Contract Cycle",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
    engine.dispose()

    app = create_app(database_url=str(target), open_mode=False)
    headers = {"Authorization": contract_token}

    def execute(client, query: str, variables: dict | None = None) -> dict:
        response = client.post(
            "/graphql",
            headers=headers,
            json={"query": query, "variables": variables or {}},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "errors" not in payload, payload.get("errors")
        return payload["data"]

    with TestClient(app) as client:
        bootstrap = execute(
            client,
            """query Bootstrap($first: Int!) {
              t: teams { nodes { id name key description icon color } }
              p: projects(first: $first) { nodes {
                id name slugId description icon color state progress startDate targetDate
                lead { id name } teams { nodes { id name key } } health url
              } }
              s: workflowStates { nodes {
                id name type color description position team { id key }
              } }
            }""",
            {"first": 100},
        )
        assert bootstrap["t"]["nodes"][0]["key"] == "ENG"
        assert bootstrap["p"]["nodes"][0]["teams"]["nodes"][0]["key"] == "ENG"
        assert bootstrap["s"]["nodes"][0]["team"]["key"] == "ENG"

        cycles_data = execute(
            client,
            """query ListCycles($filter: CycleFilter) {
              cycles(filter: $filter) {
                nodes { id name number startsAt endsAt progress team { id key } }
              }
            }""",
            {"filter": {"team": {"id": {"eq": seeded["team_id"]}}}},
        )
        assert cycles_data["cycles"]["nodes"][0]["id"] == cycle_id
        assert cycles_data["cycles"]["nodes"][0]["team"]["key"] == "ENG"

        issues_data = execute(
            client,
            """query FindIssue($filter: IssueFilter!) {
              issues(filter: $filter) { nodes { id } }
            }""",
            {"filter": {"identifier": {"eq": "ENG-1"}}},
        )
        assert issues_data["issues"]["nodes"][0]["id"] == issue_id

        create_data = execute(
            client,
            """mutation CreateIssue($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success issue {
                  id identifier title state { name type color }
                  priority team { id key } url
                }
              }
            }""",
            {
                "input": {
                    "teamId": seeded["team_id"],
                    "title": "Created by CloverOps",
                    "projectId": project_id,
                    "priority": 2,
                }
            },
        )
        created_id = create_data["issueCreate"]["issue"]["id"]
        assert create_data["issueCreate"]["success"] is True

        states_data = execute(
            client,
            """query GetStartedStates {
              workflowStates(filter: { type: { eq: "started" } }) {
                nodes { id name }
              }
            }""",
        )
        started_id = states_data["workflowStates"]["nodes"][0]["id"]
        execute(
            client,
            """mutation MoveIssue($id: String!, $sid: String!) {
              issueUpdate(id: $id, input: { stateId: $sid }) { success }
            }""",
            {"id": created_id, "sid": started_id},
        )
        execute(
            client,
            """mutation Comment($iid: String!, $b: String!) {
              commentCreate(input: {issueId: $iid, body: $b}) { success }
            }""",
            {"iid": created_id, "b": "Contract comment"},
        )
        attachment_data = execute(
            client,
            """mutation Attach(
              $iid: String!, $url: String!, $t: String!, $st: String
            ) {
              attachmentCreate(input: {
                issueId: $iid, url: $url, title: $t, subtitle: $st
              }) { success }
            }""",
            {
                "iid": created_id,
                "url": "https://example.test/change/1",
                "t": "Contract attachment",
                "st": "Created by the contract test",
            },
        )
        assert attachment_data["attachmentCreate"]["success"] is True

    verification_engine = make_engine(target)
    with verification_engine.connect() as conn:
        assert conn.execute(
            select(attachment.c.id).where(attachment.c.issue_id == created_id)
        ).scalar_one()
    verification_engine.dispose()


def test_browser_password_session_setup_login_graphql_and_logout(tmp_path) -> None:
    from starlette.testclient import TestClient

    db_path = tmp_path / "browser-auth.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Auth Workspace", org_url_key="auth-test",
                         user_name="Browser User", user_email="browser@example.test",
                         demo_issues=False)
    client = TestClient(create_app(str(db_path), open_mode=False))
    weak = client.post("/auth/setup", json={"token": seeded["token"], "password": "short"})
    assert weak.status_code == 400
    setup = client.post("/auth/setup", json={"token": seeded["token"], "password": "correct horse battery"})
    assert setup.status_code == 201
    bad = client.post("/auth/login", json={"email": "browser@example.test", "password": "wrong password"})
    assert bad.status_code == 401
    login = client.post("/auth/login", json={"email": "browser@example.test", "password": "correct horse battery"})
    assert login.status_code == 200
    assert "hoja_session" in login.cookies
    assert "httponly" in login.headers.get("set-cookie", "").lower()
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["organizationId"] == seeded["org_id"]
    graph = client.post("/graphql", json={"query": "{ viewer { email } }"})
    assert graph.status_code == 200
    assert graph.json()["data"]["viewer"]["email"] == "browser@example.test"
    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    assert client.get("/auth/me").status_code == 401
    assert client.post("/graphql", json={"query": "{ viewer { id } }"}).status_code == 401


def test_browser_password_login_accepts_api_key_and_rejects_invalid_password(tmp_path) -> None:
    from starlette.testclient import TestClient

    db_path = tmp_path / "browser-api-key-login.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="API Key Workspace", org_url_key="api-key-login",
                         user_name="API Key User", user_email="api-key-login@example.test",
                         demo_issues=False)
    client = TestClient(create_app(str(db_path), open_mode=False))
    assert client.post("/auth/setup", json={"token": seeded["token"],
        "password": "correct horse battery"}).status_code == 201

    login = client.post("/auth/login", json={"apiKey": seeded["token"]})
    assert login.status_code == 200
    assert "hoja_session" in login.cookies
    assert client.get("/auth/me").json()["organizationId"] == seeded["org_id"]

    client.post("/auth/logout")
    invalid = client.post("/auth/login", json={"apiKey": "invalid-token"})
    assert invalid.status_code == 401
    assert "hoja_session" not in invalid.cookies


def test_api_key_login_skips_workspace_lookup_on_outdated_schema(tmp_path) -> None:
    db_path = tmp_path / "browser-api-key-outdated.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Outdated Workspace", org_url_key="outdated-login",
                         user_name="API Key User", user_email="outdated@example.test",
                         demo_issues=False)
    organization_membership.drop(engine)
    global_identity.drop(engine)
    client = TestClient(create_app(str(db_path), open_mode=False))

    login = client.post("/auth/login", json={"apiKey": seeded["token"]})
    assert login.status_code == 200
    assert "hoja_session" in login.cookies

    invalid = client.post("/auth/login", json={"apiKey": "invalid-token"})
    assert invalid.status_code == 401
    assert "hoja_session" not in invalid.cookies

    email_login = client.post("/auth/login", json={
        "email": "outdated@example.test", "password": "irrelevant",
    })
    assert email_login.status_code == 503
    assert "migrated" in email_login.json()["error"]


def test_global_identity_can_switch_only_to_its_explicit_memberships(tmp_path) -> None:
    db_path = tmp_path / "identity-switch.db"
    engine = make_engine(db_path)
    migrate(engine)
    one = seed_tenant(engine, org_name="Switch One", org_url_key="switch-one",
                      user_name="Shared Person One", user_email="shared-switch@example.test",
                      demo_issues=False)
    two = seed_tenant(engine, org_name="Switch Two", org_url_key="switch-two",
                      user_name="Shared Person Two", user_email="shared-switch@example.test",
                      demo_issues=False)
    outsider = seed_tenant(engine, org_name="Outsider", org_url_key="switch-outsider",
                           user_name="Other Person", user_email="other-switch@example.test",
                           demo_issues=False)
    client = TestClient(create_app(str(db_path), open_mode=False))
    for seeded in (one, two, outsider):
        assert client.post("/auth/setup", json={"token": seeded["token"],
            "password": "correct horse battery"}).status_code == 201

    need_choice = client.post("/auth/login", json={"email": "shared-switch@example.test",
                              "password": "correct horse battery"})
    assert need_choice.status_code == 409
    assert client.post("/auth/workspaces", json={"email": "shared-switch@example.test"}).status_code == 401
    available = client.post("/auth/workspaces", json={"email": "shared-switch@example.test",
                            "password": "correct horse battery"}).json()
    assert {w["organizationId"] for w in available["workspaces"]} == {one["org_id"], two["org_id"]}
    login = client.post("/auth/login", json={"email": "shared-switch@example.test",
        "password": "correct horse battery", "organizationId": one["org_id"]})
    assert login.status_code == 200
    assert client.get("/auth/me").json()["organizationId"] == one["org_id"]

    switched = client.post("/graphql", json={"query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success organization{name}}}",
                                                   "variables": {"id": two["org_id"]}})
    assert switched.json()["data"]["workspaceSwitch"]["success"] is False
    assert client.get("/auth/me").json()["organizationId"] == one["org_id"]
    denied = client.post("/graphql", json={"query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success}}",
                                                 "variables": {"id": outsider["org_id"]}})
    assert denied.json()["data"]["workspaceSwitch"]["success"] is False
    assert client.get("/auth/me").json()["organizationId"] == one["org_id"]

    # Explicitly add the second org profile to the same identity; an active membership enables switching.
    from cliniar_server.db import organization_membership
    with engine.begin() as conn:
        identity_id = conn.execute(select(organization_membership.c.identity_id).where(
            organization_membership.c.profile_user_id == one["user_id"])).scalar_one()
        conn.execute(organization_membership.update().where(
            organization_membership.c.profile_user_id == two["user_id"]).values(identity_id=identity_id))
    allowed = client.post("/graphql", json={"query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success organization{name}}}",
                                               "variables": {"id": two["org_id"]}})
    assert allowed.json()["data"]["workspaceSwitch"]["success"] is True
    assert client.get("/auth/me").json()["organizationId"] == two["org_id"]
    # API keys remain pinned to their organization even when the browser session switches.
    api_client = TestClient(create_app(str(db_path), open_mode=False))
    api_key = api_client.post("/graphql", headers={"Authorization": two["token"]}, json={
        "query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success}}",
        "variables": {"id": one["org_id"]}})
    assert api_key.json()["data"]["workspaceSwitch"]["success"] is False


def test_cross_identity_workspace_link_requires_verified_invitation(tmp_path) -> None:
    db_path = tmp_path / "identity-link.db"
    engine = make_engine(db_path)
    migrate(engine)
    one = seed_tenant(engine, org_name="Link One", org_url_key="link-one",
                      user_name="Person One", user_email="link@example.test", demo_issues=False)
    two = seed_tenant(engine, org_name="Link Two", org_url_key="link-two",
                      user_name="Person Two", user_email="link@example.test", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    client_a, client_b = TestClient(app), TestClient(app)
    assert client_a.post("/auth/setup", json={"token": one["token"], "password": "correct horse battery"}).status_code == 201
    assert client_b.post("/auth/setup", json={"token": two["token"], "password": "another secure password"}).status_code == 201
    assert client_a.post("/auth/login", json={"email": "link@example.test", "password": "correct horse battery",
                                               "organizationId": one["org_id"]}).status_code == 200
    assert client_b.post("/auth/login", json={"email": "link@example.test", "password": "another secure password",
                                               "organizationId": two["org_id"]}).status_code == 200
    denied = client_a.post("/graphql", json={"query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success}}",
                                             "variables": {"id": two["org_id"]}})
    assert denied.json()["data"]["workspaceSwitch"]["success"] is False
    create = client_b.post("/graphql", json={"query": "mutation($id:String!){membershipInvitationCreate(userId:$id){success inviteCode}}",
                                              "variables": {"id": two["user_id"]}}).json()["data"]["membershipInvitationCreate"]
    assert create["success"] is True
    mismatch = client_a.post("/graphql", json={"query": "mutation($code:String!,$pw:String!){membershipInvitationAccept(code:$code,inviteePassword:$pw){success}}",
                                                "variables": {"code": create["inviteCode"], "pw": "correct horse battery"}})
    assert mismatch.json()["data"]["membershipInvitationAccept"]["success"] is False
    accepted = client_a.post("/graphql", json={"query": "mutation($code:String!,$pw:String!){membershipInvitationAccept(code:$code,inviteePassword:$pw){success}}",
                                                "variables": {"code": create["inviteCode"], "pw": "another secure password"}})
    assert accepted.json()["data"]["membershipInvitationAccept"]["success"] is True
    switched = client_b.post("/graphql", json={"query": "mutation($id:String!){workspaceSwitch(organizationId:$id){success}}",
                                                "variables": {"id": one["org_id"]}})
    assert switched.json()["data"]["workspaceSwitch"]["success"] is True


def test_workspace_members_and_admin_update_are_org_scoped(tmp_path) -> None:
    suffix = uuid.uuid4().hex[:8]
    engine = make_engine(tmp_path / "workspace-admin.db")
    migrate(engine)
    one = seed_tenant(engine, org_name="One", org_url_key=f"one-{suffix}",
                      user_name="Admin One", user_email=f"admin1-{suffix}@example.test",
                      demo_issues=False)
    two = seed_tenant(engine, org_name="Two", org_url_key=f"two-{suffix}",
                      user_name="Admin Two", user_email=f"admin2-{suffix}@example.test",
                      demo_issues=False)
    app = create_app(str(tmp_path / "workspace-admin.db"), open_mode=False)
    client = TestClient(app)

    def gql(token, query, variables=None):
        return client.post("/graphql", headers={"Authorization": token},
                           json={"query": query, "variables": variables or {}}).json()

    members = gql(one["token"], "{ organizationMembers { nodes { email admin } } workspace { id name urlKey } }")
    assert "errors" not in members
    assert [m["email"] for m in members["data"]["organizationMembers"]["nodes"]] == [f"admin1-{suffix}@example.test"]
    assert members["data"]["workspace"]["name"] == "One"
    updated = gql(one["token"], "mutation($input: WorkspaceUpdateInput!){workspaceUpdate(input:$input){success organization{name urlKey}}}",
                  {"input": {"name": "Renamed One", "urlKey": f"renamed-{suffix}"}})
    assert "errors" not in updated
    assert updated["data"]["workspaceUpdate"]["organization"]["name"] == "Renamed One"
    still_two = gql(two["token"], "{ workspace { name urlKey } organizationMembers { nodes { email } } }")
    assert still_two["data"]["workspace"]["name"] == "Two"
    assert still_two["data"]["organizationMembers"]["nodes"][0]["email"] == f"admin2-{suffix}@example.test"
    outsider_role = gql(one["token"], "mutation($id:String!){memberRoleUpdate(userId:$id,admin:true){success user{id}}}",
                        {"id": two["user_id"]})
    assert outsider_role["data"]["memberRoleUpdate"]["success"] is False
    second_admin = new_id()
    from cliniar_server.db import user as user_table
    with engine.begin() as conn:
        conn.execute(user_table.insert().values(id=second_admin, organization_id=one["org_id"],
                     name="Second Admin", email=f"second-{suffix}@example.test", active=True,
                     admin=True, created_at=now_iso(), updated_at=now_iso()))
    granted = gql(one["token"], "mutation($id:String!){memberRoleUpdate(userId:$id,admin:false){success user{id admin}}}",
                  {"id": second_admin})
    assert granted["data"]["memberRoleUpdate"]["success"] is True
    assert granted["data"]["memberRoleUpdate"]["user"]["admin"] is False
    invited = gql(one["token"], "mutation($input:MemberInviteInput!){memberInvite(input:$input){success user{id email admin} apiKey}}",
                  {"input": {"name": "New teammate", "email": f"new-{suffix}@example.test"}})
    invited_user = invited["data"]["memberInvite"]
    assert invited_user["success"] is True
    assert invited_user["user"]["email"] == f"new-{suffix}@example.test"
    assert invited_user["apiKey"].startswith("lin_api_")
    duplicate = gql(one["token"], "mutation($input:MemberInviteInput!){memberInvite(input:$input){success}}",
                    {"input": {"name": "Duplicate", "email": f"new-{suffix}@example.test"}})
    assert duplicate["data"]["memberInvite"]["success"] is False
    denied_invite = gql(two["token"], "mutation($input:MemberInviteInput!){memberInvite(input:$input){success}}",
                        {"input": {"name": "No admin", "email": f"not-admin-{suffix}@example.test"}})
    assert denied_invite["data"]["memberInvite"]["success"] is True

    with engine.begin() as conn:
        conn.execute(user_table.update().where(user_table.c.id == one["user_id"]).values(admin=False))
    denied = gql(one["token"], "mutation($input: WorkspaceUpdateInput!){workspaceUpdate(input:$input){success organization{name}}}",
                 {"input": {"name": "Unauthorized"}})
    assert "errors" not in denied
    assert denied["data"]["workspaceUpdate"]["success"] is False
    denied_invite = gql(one["token"], "mutation($input:MemberInviteInput!){memberInvite(input:$input){success apiKey}}",
                        {"input": {"name": "No admin", "email": f"not-admin-{suffix}@example.test"}})
    assert denied_invite["data"]["memberInvite"]["success"] is False
    assert denied_invite["data"]["memberInvite"]["apiKey"] is None
    assert gql(one["token"], "{ workspace { name } }")["data"]["workspace"]["name"] == "Renamed One"


def test_first_run_onboarding_bootstraps_admin_session_and_disables_setup(tmp_path) -> None:
    db_path = tmp_path / "onboarding.db"
    migrate(make_engine(db_path))
    client = TestClient(create_app(str(db_path), open_mode=False))
    assert client.get("/onboarding/status").json() == {"initialized": False}

    response = client.post("/onboarding/bootstrap", json={
        "name": "First Admin", "email": "first@example.test",
        "password": "correct horse battery", "workspaceName": "First Workspace",
        "workspaceKey": "first-workspace", "teamName": "Engineering", "teamKey": "ENG",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["apiKey"].startswith("lin_api_")
    assert response.cookies.get("hoja_session")
    assert client.get("/onboarding/status").json() == {"initialized": True}
    assert client.get("/auth/me").status_code == 200
    assert client.post("/onboarding/bootstrap", json={
        "name": "Other Admin", "email": "other@example.test",
        "password": "another correct horse", "workspaceName": "Other",
        "workspaceKey": "other", "teamName": "Other", "teamKey": "OTH",
    }).status_code == 409


def test_first_run_onboarding_rejects_invalid_password_without_initializing(tmp_path) -> None:
    db_path = tmp_path / "onboarding-invalid.db"
    migrate(make_engine(db_path))
    client = TestClient(create_app(str(db_path), open_mode=False))
    response = client.post("/onboarding/bootstrap", json={
        "name": "First Admin", "email": "first@example.test", "password": "short",
        "workspaceName": "First Workspace", "workspaceKey": "first-workspace",
        "teamName": "Engineering", "teamKey": "ENG",
    })
    assert response.status_code == 400
    assert client.get("/onboarding/status").json() == {"initialized": False}


def test_new_member_invite_code_acceptance_sets_password_and_is_one_time(tmp_path) -> None:
    db_path = tmp_path / "member-invite-code.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Inviter", org_url_key="invite-code-test",
                         user_name="Workspace Admin", user_email="admin-invite@example.test", demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    admin = TestClient(app)
    assert admin.post("/auth/setup", json={"token": seeded["token"],
        "password": "admin passphrase long"}).status_code == 201
    assert admin.post("/auth/login", json={"email": "admin-invite@example.test",
        "password": "admin passphrase long"}).status_code == 200
    invite_response = admin.post("/graphql", json={"query":
        "mutation($input:MemberInviteInput!){memberInvite(input:$input){success user{id email}}}",
        "variables": {"input": {"name": "New Member", "email": "new-member@example.test"}}})
    invited = invite_response.json()["data"]["memberInvite"]
    code_response = admin.post("/graphql", json={"query":
        "mutation($id:String!){membershipInvitationCreate(userId:$id){success inviteCode}}",
        "variables": {"id": invited["user"]["id"]}})
    code = code_response.json()["data"]["membershipInvitationCreate"]["inviteCode"]
    recipient = TestClient(app)
    mismatch = recipient.post("/auth/invite/accept", json={"code": code,
        "email": "wrong@example.test", "password": "new member password"})
    assert mismatch.status_code == 401
    accepted = recipient.post("/auth/invite/accept", json={"code": code,
        "email": "new-member@example.test", "password": "new member password"})
    assert accepted.status_code == 200
    assert accepted.cookies.get("hoja_session")
    assert recipient.get("/auth/me").status_code == 200
    reused = TestClient(app).post("/auth/invite/accept", json={"code": code,
        "email": "new-member@example.test", "password": "another password"})
    assert reused.status_code == 401


def test_concurrent_first_run_bootstrap_only_creates_one_workspace(tmp_path) -> None:
    db_path = tmp_path / "onboarding-race.db"
    migrate(make_engine(db_path))
    app = create_app(str(db_path), open_mode=False)
    payload = {
        "name": "First Admin", "email": "first@example.test",
        "password": "correct horse battery", "workspaceName": "First Workspace",
        "workspaceKey": "first-workspace", "teamName": "Engineering", "teamKey": "ENG",
    }
    def submit():
        with TestClient(app) as client:
            return client.post("/onboarding/bootstrap", json=payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(statuses) == [200, 409]
    engine = make_engine(db_path)
    try:
        from cliniar_server.db import organization, user
        with engine.connect() as conn:
            assert conn.execute(select(organization.c.id)).all().__len__() == 1
            assert conn.execute(select(user.c.id).where(user.c.admin.is_(True))).all().__len__() == 1
    finally:
        engine.dispose()


def test_read_only_api_key_is_selectable_and_blocks_mutations_and_sessions(tmp_path) -> None:
    db_path = tmp_path / "read-only-api-key.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Read Only Keys", org_url_key="read-only-keys",
                         user_email="readonly-keys@example.test", token="readonly-seed-token",
                         demo_issues=False)
    store = Store(engine)
    _, read_secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "Dashboard", "read")
    _, write_secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "CLI", "read_write")
    assert store.create_api_key(seeded["org_id"], seeded["user_id"], "Bad", "admin") is None
    read_headers = {"Authorization": f"Bearer {read_secret}"}
    mutation = "mutation($label:String!){apiKeyCreate(label:$label){success}}"
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        read = client.post("/graphql", headers=read_headers, json={"query": "{viewer{id}}"}).json()
        assert "errors" not in read
        blocked = client.post("/graphql", headers=read_headers, json={
            "query": mutation, "variables": {"label": "Nope"}})
        assert blocked.status_code == 403
        assert client.post("/auth/login", json={"apiKey": read_secret}).status_code == 403
        write = client.post("/graphql", headers={"Authorization": f"Bearer {write_secret}"}, json={
            "query": mutation, "variables": {"label": "Allowed"}}).json()
        assert write["data"]["apiKeyCreate"]["success"] is True
    listed = {k["label"]: k["access"] for k in store.list_api_keys(seeded["org_id"], seeded["user_id"])}
    assert listed["Dashboard"] == "read"
    assert listed["CLI"] == "read_write"


def test_api_key_expiry_hint_and_last_used_are_enforced(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone
    engine = make_engine(tmp_path / "api-key-lifecycle.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Key Lifecycle", org_url_key="key-lifecycle",
                         user_email="lifecycle-keys@example.test", token="lifecycle-seed-token",
                         demo_issues=False)
    store = Store(engine)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert store.create_api_key(seeded["org_id"], seeded["user_id"], "Old", "read_write", past) is None
    assert store.create_api_key(seeded["org_id"], seeded["user_id"], "Junk", "read_write", "not-a-date") is None
    meta_ok, live_secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "Current", "read_write", future)
    assert store.resolve_token(live_secret) is not None
    meta_forever, forever_secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "Forever", "read_write")
    assert store.resolve_token(forever_secret) is not None
    store.touch_api_key(meta_ok["id"])
    rows = {k["label"]: k for k in store.list_api_keys(seeded["org_id"], seeded["user_id"])}
    assert rows["Current"]["lastUsedAt"] is not None
    assert rows["Forever"]["lastUsedAt"] is None
    assert rows["Current"]["expiresAt"] == future
    assert rows["Forever"]["expiresAt"] is None
    assert rows["Forever"]["hint"] == forever_secret[-4:]


def test_read_write_api_key_can_create_issue_with_team_and_project(tmp_path) -> None:
    db_path = tmp_path / "issue-create-api-key.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Issue Key", org_url_key="issue-key",
                         user_email="issue-key@example.test", token="issue-key-seed", demo_issues=False)
    store = Store(engine)
    _, secret = store.create_api_key(seeded["org_id"], seeded["user_id"], "Agent", "read_write")
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        teams = client.post("/graphql", headers=headers, json={"query": "{teams{nodes{id key}}}"}).json()
        team_id = teams["data"]["teams"]["nodes"][0]["id"]
        created = client.post("/graphql", headers=headers, json={
            "query": "mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{identifier}}}",
            "variables": {"i": {"teamId": team_id, "title": "Agent issue"}}})
        assert created.status_code == 200, created.text
        assert created.json()["data"]["issueCreate"]["success"] is True


def test_team_membership_is_granted_on_invite_and_managed_by_admins(tmp_path) -> None:
    db_path = tmp_path / "team-membership.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Membership", org_url_key="membership",
                         user_email="membership-admin@example.test", token="membership-seed", demo_issues=False)
    token = seeded["token"]
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        def run(query, variables=None, bearer=token):
            return client.post("/graphql", headers={"Authorization": f"Bearer {bearer}"},
                               json={"query": query, "variables": variables or {}}).json()

        teams = run("{teams{nodes{id key}}}")["data"]["teams"]["nodes"]
        team_id = teams[0]["id"]
        invited = run("mutation($i:MemberInviteInput!){memberInvite(input:$i){success user{id} apiKey}}",
                      {"i": {"name": "Robert", "email": "robert-membership@example.test", "teamIds": [team_id]}})
        robert = invited["data"]["memberInvite"]
        assert robert["success"] is True
        robert_id, robert_key = robert["user"]["id"], robert["apiKey"]

        assigned = run("mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id}}}",
                       {"i": {"teamId": team_id, "title": "Assigned to Robert", "assigneeId": robert_id}})
        assert assigned["data"]["issueCreate"]["success"] is True, assigned

        removed = run("mutation($t:String!,$u:String!){teamMemberRemove(teamId:$t,userId:$u){success}}",
                      {"t": team_id, "u": robert_id})
        assert removed["data"]["teamMemberRemove"]["success"] is True
        rejected = run("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
                       {"i": {"teamId": team_id, "title": "No longer member", "assigneeId": robert_id}})
        assert rejected.get("errors"), rejected

        added = run("mutation($t:String!,$u:String!){teamMemberAdd(teamId:$t,userId:$u){success}}",
                    {"t": team_id, "u": robert_id})
        assert added["data"]["teamMemberAdd"]["success"] is True

        denied = run("mutation($t:String!,$u:String!){teamMemberAdd(teamId:$t,userId:$u){success}}",
                     {"t": team_id, "u": robert_id}, bearer=robert_key)
        assert denied["data"]["teamMemberAdd"]["success"] is False

        bad_team = run("mutation($i:MemberInviteInput!){memberInvite(input:$i){success}}",
                       {"i": {"name": "X", "email": "x-membership@example.test", "teamIds": ["missing"]}})
        assert bad_team.get("errors"), bad_team


def test_team_create_adds_selected_members_and_rejects_unknown_ids(tmp_path) -> None:
    db_path = tmp_path / "team-create-members.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Team Create", org_url_key="team-create",
                         user_email="team-admin@example.test", token="team-create-seed", demo_issues=False)
    token = seeded["token"]
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        def run(query, variables=None):
            return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                               json={"query": query, "variables": variables or {}}).json()
        invited = run("mutation($i:MemberInviteInput!){memberInvite(input:$i){success user{id}}}",
                      {"i": {"name": "Member", "email": "member-team-create@example.test"}})
        member_id = invited["data"]["memberInvite"]["user"]["id"]
        created = run("mutation($i:TeamCreateInput!){teamCreate(input:$i){success team{id key members(first:10){nodes{id}}}}}",
                      {"i": {"name": "Design", "key": "DSG", "memberIds": [member_id]}})
        assert created["data"]["teamCreate"]["success"] is True, created
        member_ids = {m["id"] for m in created["data"]["teamCreate"]["team"]["members"]["nodes"]}
        assert member_id in member_ids
        bad = run("mutation($i:TeamCreateInput!){teamCreate(input:$i){success}}",
                  {"i": {"name": "Bad", "key": "BAD", "memberIds": ["missing-user"]}})
        assert bad.get("errors"), bad


def test_bootstrap_returns_team_id_for_onboarding_invite(tmp_path) -> None:
    from starlette.testclient import TestClient as _TC
    db_path = tmp_path / "bootstrap-team.db"
    engine = make_engine(db_path)
    migrate(engine)
    with _TC(create_app(str(db_path), open_mode=False)) as client:
        response = client.post("/onboarding/bootstrap", json={
            "name": "Owner", "email": "owner-bootstrap@example.test", "password": "a-long-password-123",
            "workspaceName": "Boot", "workspaceKey": "boot-team", "teamName": "Engineering", "teamKey": "ENG"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["teamId"]
        assert body["apiKey"].startswith("lin_api_")


def test_realtime_socket_replays_changes_after_cursor_and_rejects_bad_auth(tmp_path) -> None:
    db_path = tmp_path / "realtime.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Realtime", org_url_key="realtime",
                         user_email="realtime@example.test", token="realtime-seed", demo_issues=False)
    token = seeded["token"]
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        team_id = client.post("/graphql", headers=headers, json={"query": "{teams{nodes{id}}}"}).json()["data"]["teams"]["nodes"][0]["id"]
        created = client.post("/graphql", headers=headers, json={
            "query": "mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id}}}",
            "variables": {"i": {"teamId": team_id, "title": "Realtime issue"}}}).json()
        issue_id = created["data"]["issueCreate"]["issue"]["id"]
        client.post("/graphql", headers=headers, json={
            "query": "mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}",
            "variables": {"id": issue_id, "i": {"title": "Renamed"}}})

        with client.websocket_connect(f"/ws?after=0", headers=headers) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["entity"] == "issue" and event["action"] == "update":
                    break
        issue_events = [e for e in events if e["entity"] == "issue"]
        assert [e["action"] for e in issue_events] == ["create", "update"]
        assert all(e["entityId"] == issue_id for e in issue_events)
        first, second = issue_events
        assert first["seq"] < second["seq"]

        with client.websocket_connect(f"/ws?after={first['seq']}", headers=headers) as ws:
            resumed = []
            while not resumed or resumed[-1]["seq"] < second["seq"]:
                resumed.append(ws.receive_json())
        assert all(e["seq"] > first["seq"] for e in resumed)
        assert resumed[-1]["seq"] == second["seq"]

        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/ws?after=0", headers={"Authorization": "Bearer nope"}) as ws:
                ws.receive_json()
        assert rejected.value.code == 4401


def test_realtime_capture_covers_comments_projects_labels_and_project_updates(tmp_path) -> None:
    db_path = tmp_path / "realtime-coverage.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Coverage", org_url_key="coverage",
                         user_email="coverage@example.test", token="coverage-seed", demo_issues=False)
    headers = {"Authorization": f"Bearer {seeded['token']}"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        def gql(query, variables=None):
            body = client.post("/graphql", headers=headers, json={"query": query, "variables": variables or {}}).json()
            assert "errors" not in body, body
            return body["data"]

        team_id = gql("{teams{nodes{id}}}")["teams"]["nodes"][0]["id"]
        issue_id = gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}",
                       {"i": {"teamId": team_id, "title": "Covered"}})["issueCreate"]["issue"]["id"]
        gql("mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}",
            {"i": {"issueId": issue_id, "body": "hi"}})
        gql("mutation($i:IssueLabelCreateInput!){issueLabelCreate(input:$i){success}}",
            {"i": {"name": "bug", "color": "#ff0000"}})
        project_id = gql("mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
                         {"i": {"name": "Launch", "teamIds": [team_id]}})["projectCreate"]["project"]["id"]
        gql("mutation($i:ProjectUpdateCreateInput!){projectUpdateCreate(input:$i){success}}",
            {"i": {"projectId": project_id, "body": "On track"}})

        with client.websocket_connect("/ws?after=0", headers=headers) as ws:
            seen = set()
            while not {"comment", "label", "project", "projectUpdate"} <= seen:
                seen.add(ws.receive_json()["entity"])
        assert {"comment", "label", "project", "projectUpdate"} <= seen


def test_graphql_rejects_malformed_bodies_with_400_and_keeps_valid_requests(tmp_path) -> None:
    db_path = tmp_path / "graphql-bodies.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Bodies", org_url_key="bodies",
                         user_email="bodies@example.test", token="bodies-seed", demo_issues=False)
    headers = {"Authorization": f"Bearer {seeded['token']}", "Content-Type": "application/json"}
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        malformed = client.post("/graphql", headers=headers, content=b"{not json")
        assert malformed.status_code == 400
        assert malformed.json()["errors"][0]["extensions"]["code"] == "BAD_REQUEST"

        not_object = client.post("/graphql", headers=headers, content=b"[1,2,3]")
        assert not_object.status_code == 400

        valid = client.post("/graphql", headers=headers, json={"query": "{viewer{id}}"})
        assert valid.status_code == 200
        assert valid.json()["data"]["viewer"]["id"]


def test_realtime_socket_rejects_out_of_range_cursors(tmp_path) -> None:
    db_path = tmp_path / "realtime-cursor.db"
    engine = make_engine(db_path)
    migrate(engine)
    seed_tenant(engine, org_name="Cursor", org_url_key="cursor",
                user_email="cursor@example.test", token="cursor-seed", demo_issues=False)
    with TestClient(create_app(str(db_path), open_mode=False)) as client:
        for cursor in ["99999999999999999999999", "-5", "abc", "1.5"]:
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect(f"/ws?after={cursor}", headers={"Authorization": "Bearer cursor-seed"}) as ws:
                    ws.receive_json()
            assert closed.value.code == 4400, cursor


def test_state_changed_at_moves_only_when_status_changes(tmp_path) -> None:
    engine = make_engine(tmp_path / "state-changed.db")
    migrate(engine)
    seeded = _seed(engine, name="StateChanged", key="statechanged", email="user@example.test")
    writer = Writer(engine)
    issue_id = writer.issue_create(
        seeded["org_id"], seeded["user_id"], {"teamId": "ENG", "title": "Track status"}
    )
    store = Store(engine)
    first = store.issue_by_id_or_identifier(seeded["org_id"], issue_id)
    assert first["stateChangedAt"]

    with engine.connect() as conn:
        states = conn.execute(
            select(workflow_state.c.id, workflow_state.c.type).where(
                workflow_state.c.team_id == seeded["team_id"]
            )
        ).all()
    current = first["_state_id"]
    other = next(s.id for s in states if s.id != current)

    writer.issue_update(seeded["org_id"], issue_id, {"title": "Renamed"}, actor_id=seeded["user_id"])
    after_rename = store.issue_by_id_or_identifier(seeded["org_id"], issue_id)
    assert after_rename["stateChangedAt"] == first["stateChangedAt"]

    writer.issue_update(seeded["org_id"], issue_id, {"stateId": other}, actor_id=seeded["user_id"])
    after_move = store.issue_by_id_or_identifier(seeded["org_id"], issue_id)
    assert after_move["stateChangedAt"] > first["stateChangedAt"]

    writer.issue_update(seeded["org_id"], issue_id, {"stateId": other}, actor_id=seeded["user_id"])
    assert store.issue_by_id_or_identifier(seeded["org_id"], issue_id)["stateChangedAt"] == after_move["stateChangedAt"]
