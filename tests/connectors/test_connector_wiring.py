"""Connector wiring: the agent gets the Google Workspace toolset only for users with an active account
and only when the server is configured. Otherwise the agent is unchanged."""

from __future__ import annotations

import pytest
from google_workspace.principal import verify_principal
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cliniar_server.agent_chat import build_agent
from cliniar_server.connectors_api import PROVIDER, connector_binding_for
from cliniar_server.db import connector_account, make_engine, migrate, seed_tenant
from cliniar_server.schema_sdl import SCHEMA_SDL

KEY = "wiring-test-signing-key"
URL = "http://localhost:8000/mcp"


@pytest.fixture
def seeded(tmp_path):
    engine = make_engine(tmp_path / "wiring.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Wire Org",
        org_url_key="wire-org",
        user_email="owner@wire.test",
        token="wire-token",
        demo_issues=False,
    )
    return engine, admin


def _add_account(engine, org: str, owner: str | None, status: str = "active") -> None:
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id=f"acct-{owner or 'org'}-{status}",
                organization_id=org,
                owner_user_id=owner,
                provider=PROVIDER,
                external_account="x@wire.test",
                scopes="",
                encrypted_refresh_token="x",
                status=status,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("CLINIAR_CONNECTOR_MCP_URL", URL)
    monkeypatch.setenv("CLINIAR_CONNECTOR_SIGNING_KEY", KEY)


def test_no_binding_when_server_not_configured(seeded, monkeypatch) -> None:
    engine, admin = seeded
    monkeypatch.delenv("CLINIAR_CONNECTOR_MCP_URL", raising=False)
    monkeypatch.delenv("CLINIAR_CONNECTOR_SIGNING_KEY", raising=False)
    _add_account(engine, admin["org_id"], admin["user_id"])
    assert connector_binding_for(engine, admin["org_id"], admin["user_id"]) is None


def test_no_binding_without_active_account(seeded, configured) -> None:
    engine, admin = seeded
    assert connector_binding_for(engine, admin["org_id"], admin["user_id"]) is None


def test_revoked_account_gives_no_binding(seeded, configured) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], admin["user_id"], status="revoked")
    assert connector_binding_for(engine, admin["org_id"], admin["user_id"]) is None


def test_other_users_account_gives_no_binding(seeded, configured) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], "someone-else")
    assert connector_binding_for(engine, admin["org_id"], admin["user_id"]) is None


def test_active_account_gives_principal_for_that_user_only(seeded, configured) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], admin["user_id"])
    binding = connector_binding_for(engine, admin["org_id"], admin["user_id"])
    assert binding is not None
    assert binding.url == URL
    principal = verify_principal(binding.principal, key=KEY)
    assert (principal.user_id, principal.org_id) == (admin["user_id"], admin["org_id"])


def test_org_account_is_visible_to_members(seeded, configured) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], None)
    assert connector_binding_for(engine, admin["org_id"], admin["user_id"]) is not None


def _scripted(messages, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("ok")])


def test_binding_attaches_google_toolset_to_agent(seeded, configured) -> None:
    engine, admin = seeded
    _add_account(engine, admin["org_id"], admin["user_id"])
    binding = connector_binding_for(engine, admin["org_id"], admin["user_id"])

    agent = build_agent(FunctionModel(_scripted), SCHEMA_SDL, binding)
    toolset_ids = [getattr(t, "id", None) for t in agent.toolsets]
    assert "google-workspace" in toolset_ids


def test_agent_without_binding_has_no_google_toolset(seeded) -> None:
    agent = build_agent(FunctionModel(_scripted), SCHEMA_SDL, None)
    toolset_ids = [getattr(t, "id", None) for t in agent.toolsets]
    assert "google-workspace" not in toolset_ids
