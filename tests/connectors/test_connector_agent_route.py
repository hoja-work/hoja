"""The live /api/agent/chat route hands a connector binding to the agent only for an active account."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from cliniar_server import agent_chat
from cliniar_server.app import create_app
from cliniar_server.connectors_api import PROVIDER
from cliniar_server.db import connector_account, make_engine, migrate, seed_tenant

MESSAGES = {"messages": [{"role": "user", "content": "list my unread mail"}]}


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("CLINIAR_CONNECTOR_MCP_URL", "http://localhost:8000/mcp")
    monkeypatch.setenv("CLINIAR_CONNECTOR_SIGNING_KEY", "route-test-signing-key")


def _setup(tmp_path, *, with_account: bool):
    db_path = tmp_path / "route.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Route Org",
        org_url_key="route-org",
        user_email="owner@route.test",
        token="route-token",
        demo_issues=False,
    )
    if with_account:
        with engine.begin() as conn:
            conn.execute(
                connector_account.insert().values(
                    id="acct-route",
                    organization_id=admin["org_id"],
                    owner_user_id=admin["user_id"],
                    provider=PROVIDER,
                    external_account="owner@route.test",
                    scopes="",
                    encrypted_refresh_token="x",
                    status="active",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
    return create_app(str(db_path), open_mode=False), admin


def _capture_stream_reply(monkeypatch) -> list:
    seen: list = []

    async def fake(store, org_id, uid, messages, schema, schema_sdl, base_context, thread_id=None, connector=None):
        seen.append(connector)
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_chat, "stream_reply", fake)
    return seen


def test_active_account_passes_binding_to_agent(tmp_path, monkeypatch) -> None:
    app, _admin = _setup(tmp_path, with_account=True)
    seen = _capture_stream_reply(monkeypatch)
    with TestClient(app) as client:
        res = client.post(
            "/api/agent/chat",
            json=MESSAGES,
            headers={"Authorization": "Bearer route-token"},
        )
    assert res.status_code == 200
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].url == "http://localhost:8000/mcp"


def test_no_account_passes_no_binding(tmp_path, monkeypatch) -> None:
    app, _admin = _setup(tmp_path, with_account=False)
    seen = _capture_stream_reply(monkeypatch)
    with TestClient(app) as client:
        res = client.post(
            "/api/agent/chat",
            json=MESSAGES,
            headers={"Authorization": "Bearer route-token"},
        )
    assert res.status_code == 200
    assert seen == [None]
