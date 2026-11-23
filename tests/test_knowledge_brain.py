"""Company Knowledge Brain & multi-connector Agent Chat tests.

Covers:
- knowledgeDocCreate, knowledgeDocUpdate, knowledgeDocDelete, knowledgeDocs, and
  knowledgeContext hierarchical merging (workspace -> team -> project)
- Disabled docs excluded from knowledgeContext
- Cross-tenant isolation (Bravo cannot read, update, or delete Alpha's knowledge docs)
- Auto-injection into agent_prompt / compose_prompt and live Agent Chat system instructions
- MCP tool exposure (knowledgeDocs, knowledgeContext, knowledgeDocCreate, knowledgeDocUpdate)
- connector_bindings_for multi-connector helper in connectors_api.py and
  build_agent accepting multiple ConnectorBindings in agent_chat.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.agent_chat import AgentDeps, build_agent
from hoja_server.agent_prompt import compose_prompt
from hoja_server.app import create_app
from hoja_server.connectors_api import (
    GITHUB_PROVIDER,
    NOTION_PROVIDER,
    PROVIDER,
    SLACK_PROVIDER,
    connector_binding_for,
    connector_bindings_for,
)
from hoja_server.db import (
    connector_account,
    make_engine,
    migrate,
    now_iso,
    seed_tenant,
)
from hoja_server.mcp_oauth import READ_SCOPE, WRITE_SCOPE, HojaOAuthProvider
from hoja_server.resolvers import build_schema
from hoja_server.schema_sdl import SCHEMA_SDL
from hoja_server.store import Store
from tests import rls_mode

CREATE_DOC = """
mutation($input: KnowledgeDocInput!) {
  knowledgeDocCreate(input: $input) {
    success
    knowledgeDoc {
      id
      scope
      scopeId
      title
      body
      enabled
      createdBy { id email displayName }
      updatedBy { id email displayName }
      createdAt
      updatedAt
    }
  }
}
"""

UPDATE_DOC = """
mutation($id: String!, $input: KnowledgeDocUpdateInput!) {
  knowledgeDocUpdate(id: $id, input: $input) {
    success
    knowledgeDoc {
      id
      scope
      scopeId
      title
      body
      enabled
    }
  }
}
"""

DELETE_DOC = """
mutation($id: String!) {
  knowledgeDocDelete(id: $id)
}
"""

LIST_DOCS = """
query($scope: String, $scopeId: String) {
  knowledgeDocs(scope: $scope, scopeId: $scopeId) {
    id
    scope
    scopeId
    title
    body
    enabled
  }
}
"""

GET_CONTEXT = """
query($teamId: String, $projectId: String) {
  knowledgeContext(teamId: $teamId, projectId: $projectId)
}
"""


def _setup_two_tenants(tmp_path):
    if rls_mode.ENABLED:
        owner_url, schema = rls_mode.new_schema_url()
        engine = make_engine(owner_url)
        migrate(engine)
        alpha = seed_tenant(
            engine,
            org_name="Alpha Org",
            org_url_key="alpha-org",
            team_key="ALP",
            user_email="alpha@example.test",
            token="alpha-token",
            demo_issues=False,
        )
        bravo = seed_tenant(
            engine,
            org_name="Bravo Org",
            org_url_key="bravo-org",
            team_key="BRV",
            user_email="bravo@example.test",
            token="bravo-token",
            demo_issues=False,
        )
        app = create_app(None, database_url=rls_mode.restrict(owner_url, schema), open_mode=False)
        return engine, app, alpha, bravo

    db_path = tmp_path / "knowledge-brain.db"
    engine = make_engine(db_path)
    migrate(engine)
    alpha = seed_tenant(
        engine,
        org_name="Alpha Org",
        org_url_key="alpha-org",
        team_key="ALP",
        user_email="alpha@example.test",
        token="alpha-token",
        demo_issues=False,
    )
    bravo = seed_tenant(
        engine,
        org_name="Bravo Org",
        org_url_key="bravo-org",
        team_key="BRV",
        user_email="bravo@example.test",
        token="bravo-token",
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return engine, app, alpha, bravo


def _gql(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()


def test_knowledge_crud_and_hierarchical_context_merging(tmp_path) -> None:
    _engine, app, alpha, _bravo = _setup_two_tenants(tmp_path)
    with TestClient(app) as client:
        teams = _gql(client, alpha["token"], "{ teams { nodes { id key name } } }")["data"]["teams"]["nodes"]
        team_id = teams[0]["id"]

        proj_res = _gql(
            client,
            alpha["token"],
            'mutation($t: String!) { projectCreate(input: { name: "Brain Project", teamIds: [$t] }) { success project { id } } }',
            {"t": team_id},
        )
        assert "errors" not in proj_res, proj_res
        project_id = proj_res["data"]["projectCreate"]["project"]["id"]

        # 1. Create workspace, team, and project knowledge docs
        ws_res = _gql(
            client,
            alpha["token"],
            CREATE_DOC,
            {
                "input": {
                    "scope": "workspace",
                    "title": "Company Architecture",
                    "body": "Use strict tenant isolation and PostgreSQL RLS.",
                }
            },
        )
        assert "errors" not in ws_res, ws_res
        ws_doc = ws_res["data"]["knowledgeDocCreate"]["knowledgeDoc"]
        assert ws_doc["scope"] == "workspace"
        assert ws_doc["scopeId"] is None
        assert ws_doc["enabled"] is True
        assert ws_doc["createdBy"]["email"] == "alpha@example.test"

        team_res = _gql(
            client,
            alpha["token"],
            CREATE_DOC,
            {
                "input": {
                    "scope": "team",
                    "scopeId": team_id,
                    "title": "Team ALP Conventions",
                    "body": "Always write pytest integration tests.",
                }
            },
        )
        assert "errors" not in team_res, team_res
        team_doc = team_res["data"]["knowledgeDocCreate"]["knowledgeDoc"]
        assert team_doc["scope"] == "team"
        assert team_doc["scopeId"] == team_id

        proj_doc_res = _gql(
            client,
            alpha["token"],
            CREATE_DOC,
            {
                "input": {
                    "scope": "project",
                    "scopeId": project_id,
                    "title": "Brain Project Spec",
                    "body": "Inject hierarchical Markdown context into agents.",
                }
            },
        )
        assert "errors" not in proj_doc_res, proj_doc_res
        proj_doc = proj_doc_res["data"]["knowledgeDocCreate"]["knowledgeDoc"]
        assert proj_doc["scope"] == "project"
        assert proj_doc["scopeId"] == project_id

        # 2. List all docs and filtered docs
        all_docs = _gql(client, alpha["token"], LIST_DOCS)["data"]["knowledgeDocs"]
        assert [d["title"] for d in all_docs] == [
            "Company Architecture",
            "Team ALP Conventions",
            "Brain Project Spec",
        ]
        team_docs = _gql(client, alpha["token"], LIST_DOCS, {"scope": "team", "scopeId": team_id})["data"][
            "knowledgeDocs"
        ]
        assert [d["id"] for d in team_docs] == [team_doc["id"]]

        # 3. Verify hierarchical merging order: workspace -> team -> project
        ws_only_ctx = _gql(client, alpha["token"], GET_CONTEXT)["data"]["knowledgeContext"]
        assert "### [Workspace] Company Architecture" in ws_only_ctx
        assert "Team ALP Conventions" not in ws_only_ctx
        assert "Brain Project Spec" not in ws_only_ctx

        full_ctx = _gql(
            client,
            alpha["token"],
            GET_CONTEXT,
            {"teamId": team_id, "projectId": project_id},
        )["data"]["knowledgeContext"]
        ws_pos = full_ctx.index("### [Workspace] Company Architecture")
        team_pos = full_ctx.index("### [Team] Team ALP Conventions")
        proj_pos = full_ctx.index("### [Project] Brain Project Spec")
        assert ws_pos < team_pos < proj_pos

        # 4. Update & disable a doc -> excluded from knowledgeContext
        upd_res = _gql(
            client,
            alpha["token"],
            UPDATE_DOC,
            {
                "id": team_doc["id"],
                "input": {
                    "title": "Team ALP Updated",
                    "enabled": False,
                },
            },
        )
        assert "errors" not in upd_res, upd_res
        assert upd_res["data"]["knowledgeDocUpdate"]["knowledgeDoc"]["enabled"] is False
        assert upd_res["data"]["knowledgeDocUpdate"]["knowledgeDoc"]["title"] == "Team ALP Updated"

        ctx_after_disable = _gql(
            client,
            alpha["token"],
            GET_CONTEXT,
            {"teamId": team_id, "projectId": project_id},
        )["data"]["knowledgeContext"]
        assert "### [Workspace] Company Architecture" in ctx_after_disable
        assert "Team ALP" not in ctx_after_disable
        assert "### [Project] Brain Project Spec" in ctx_after_disable

        # 5. Delete doc
        del_res = _gql(client, alpha["token"], DELETE_DOC, {"id": proj_doc["id"]})
        assert del_res["data"]["knowledgeDocDelete"] is True
        remaining = _gql(client, alpha["token"], LIST_DOCS)["data"]["knowledgeDocs"]
        assert [d["id"] for d in remaining] == [ws_doc["id"], team_doc["id"]]


def test_knowledge_cross_tenant_isolation(tmp_path) -> None:
    _engine, app, alpha, bravo = _setup_two_tenants(tmp_path)
    with TestClient(app) as client:
        created = _gql(
            client,
            alpha["token"],
            CREATE_DOC,
            {
                "input": {
                    "scope": "workspace",
                    "title": "Alpha Secret Runbook",
                    "body": "Top-secret Alpha standard.",
                }
            },
        )
        alpha_doc_id = created["data"]["knowledgeDocCreate"]["knowledgeDoc"]["id"]

        # Bravo cannot read Alpha's docs or context
        bravo_docs = _gql(client, bravo["token"], LIST_DOCS)["data"]["knowledgeDocs"]
        assert bravo_docs == []
        bravo_ctx = _gql(client, bravo["token"], GET_CONTEXT)["data"]["knowledgeContext"]
        assert bravo_ctx == ""

        # Bravo cannot update Alpha's doc
        bravo_upd = _gql(
            client,
            bravo["token"],
            UPDATE_DOC,
            {"id": alpha_doc_id, "input": {"title": "Pwned"}},
        )
        assert "errors" in bravo_upd
        assert bravo_upd["errors"][0]["extensions"]["code"] == "NOT_FOUND"

        # Bravo cannot delete Alpha's doc
        bravo_del = _gql(client, bravo["token"], DELETE_DOC, {"id": alpha_doc_id})
        assert bravo_del["data"]["knowledgeDocDelete"] is False

        # Alpha's doc is untouched
        alpha_docs = _gql(client, alpha["token"], LIST_DOCS)["data"]["knowledgeDocs"]
        assert len(alpha_docs) == 1
        assert alpha_docs[0]["title"] == "Alpha Secret Runbook"


def test_knowledge_auto_injected_into_agent_prompt_and_agent_chat(tmp_path) -> None:
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    # Unit check on compose_prompt
    prompt_without = compose_prompt("BASE", "SNAP")
    assert "## Workspace knowledge & standards" not in prompt_without

    prompt_with = compose_prompt("BASE", "SNAP", knowledge="### [Workspace] Coding Standard\nUse ruff.")
    assert "## Workspace knowledge & standards\n### [Workspace] Coding Standard\nUse ruff." in prompt_with

    # Live build_agent run checks DB resolution
    engine, app, alpha, _bravo = _setup_two_tenants(tmp_path)
    with TestClient(app) as client:
        _gql(
            client,
            alpha["token"],
            CREATE_DOC,
            {
                "input": {
                    "scope": "workspace",
                    "title": "Security Policy",
                    "body": "Never log bearer tokens or raw secrets.",
                }
            },
        )

    seen: dict[str, str] = {}

    def scripted(messages, info: AgentInfo):
        seen["system"] = info.instructions or ""
        return ModelResponse(parts=[TextPart("acknowledged")])

    context = {
        "store": Store(engine),
        "writer": None,
        "org_id": alpha["org_id"],
        "user_id": alpha["user_id"],
        "identity_id": None,
        "browser_session": False,
    }
    agent = build_agent(FunctionModel(scripted), SCHEMA_SDL)
    agent.run_sync("Review our security guidelines", deps=AgentDeps(build_schema(), context))
    assert "## Workspace knowledge & standards" in seen["system"]
    assert "### [Workspace] Security Policy" in seen["system"]
    assert "Never log bearer tokens or raw secrets." in seen["system"]


def test_mcp_exposes_knowledge_read_and_write_tools(tmp_path) -> None:
    pytest.importorskip("mcp")
    from tests.test_mcp_agent_tools import HOST, Mcp

    path = tmp_path / "mcp-knowledge.db"
    engine = make_engine(path)
    migrate(engine)
    seed = seed_tenant(
        engine,
        org_name="Knowledge MCP",
        org_url_key="knowledge-mcp",
        team_key="ENG",
        user_name="Ada Agent",
        user_email="ada@example.test",
        demo_issues=False,
    )
    from sqlalchemy import select

    row = (
        engine.connect()
        .execute(select(db.api_key).where(db.api_key.c.token_hash == db.token_hash(seed["token"])))
        .mappings()
        .first()
    )
    provider = HojaOAuthProvider(engine, Store(engine), True)
    with engine.begin() as conn:
        conn.execute(
            db.oauth_client.insert().values(
                client_id="agent",
                created_at=now_iso(),
                client_json='{"client_id":"agent","redirect_uris":["https://client.test/cb"],"token_endpoint_auth_method":"none"}',
            )
        )
        access_token = provider._mint_pair(
            conn,
            "agent",
            row["user_id"],
            row["organization_id"],
            [READ_SCOPE, WRITE_SCOPE],
            f"https://{HOST}/mcp",
        ).access_token

    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=HOST)
    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 44321)) as client:
        mcp = Mcp(client, access_token)
        tools = mcp.tools()
        for expected in (
            "hoja_knowledgeDocs",
            "hoja_knowledgeContext",
            "hoja_knowledgeDocCreate",
            "hoja_knowledgeDocUpdate",
        ):
            assert expected in tools, f"{expected} missing from MCP tools"
        assert tools["hoja_knowledgeDocs"]["annotations"]["readOnlyHint"] is True
        assert tools["hoja_knowledgeContext"]["annotations"]["readOnlyHint"] is True

        ok, created = mcp.call(
            "hoja_knowledgeDocCreate",
            input={
                "scope": "workspace",
                "title": "MCP Standard",
                "body": "Always validate inputs at the boundary.",
            },
        )
        assert ok, created
        doc_id = created["knowledgeDocCreate"]["knowledgeDoc"]["id"]

        ok, docs = mcp.call("hoja_knowledgeDocs")
        assert ok, docs
        assert len(docs["knowledgeDocs"]) == 1
        assert docs["knowledgeDocs"][0]["title"] == "MCP Standard"

        ok, ctx = mcp.call("hoja_knowledgeContext")
        assert ok, ctx
        assert "### [Workspace] MCP Standard" in ctx["knowledgeContext"]

        ok, updated = mcp.call(
            "hoja_knowledgeDocUpdate",
            id=doc_id,
            input={"title": "Updated MCP Standard", "enabled": False},
        )
        assert ok, updated
        assert updated["knowledgeDocUpdate"]["knowledgeDoc"]["enabled"] is False

        ok, ctx_after = mcp.call("hoja_knowledgeContext")
        assert ok, ctx_after
        assert ctx_after["knowledgeContext"] == ""


def test_multi_connector_bindings_and_build_agent(tmp_path, monkeypatch) -> None:
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    engine = make_engine(tmp_path / "multi-conn.db")
    migrate(engine)
    admin = seed_tenant(
        engine,
        org_name="Conn Org",
        org_url_key="conn-org",
        user_email="owner@conn.test",
        token="conn-token",
        demo_issues=False,
    )
    store = Store(engine)

    monkeypatch.setenv("HOJA_CONNECTOR_SIGNING_KEY", "multi-connector-test-key")
    monkeypatch.setenv("HOJA_CONNECTOR_MCP_URL", "http://localhost:8000/mcp")
    monkeypatch.setenv("HOJA_GITHUB_MCP_URL", "http://localhost:8001/mcp")
    monkeypatch.setenv("HOJA_SLACK_MCP_URL", "http://localhost:8002/mcp")

    with engine.begin() as conn:
        for idx, prov in enumerate((PROVIDER, NOTION_PROVIDER, GITHUB_PROVIDER, SLACK_PROVIDER)):
            conn.execute(
                connector_account.insert().values(
                    id=f"acct-{idx}",
                    organization_id=admin["org_id"],
                    owner_user_id=admin["user_id"] if idx % 2 == 0 else None,
                    provider=prov,
                    external_account=f"acct-{prov}@conn.test",
                    scopes="",
                    encrypted_refresh_token="x",
                    status="active",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )

    bindings = connector_bindings_for(store, admin["org_id"], admin["user_id"])
    urls = [b.url for b in bindings]
    ids = [b.id for b in bindings]
    # Dedicated URLs for github and slack + unified shared URL once for google_workspace & notion
    assert "http://localhost:8001/mcp" in urls
    assert "http://localhost:8002/mcp" in urls
    assert "http://localhost:8000/mcp" in urls
    assert len(bindings) == 3
    assert set(ids) == {"github", "slack", "google-workspace"}

    # Backward-compatible single-binding helper returns the first binding
    first = connector_binding_for(engine, admin["org_id"], admin["user_id"])
    assert first == bindings[0]

    def scripted(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(
        FunctionModel(scripted),
        SCHEMA_SDL,
        connectors=bindings,
    )
    toolset_ids = [getattr(t, "id", None) for t in agent.toolsets]
    for expected_id in ("github", "slack", "google-workspace"):
        assert expected_id in toolset_ids

    # Also verify deduplication when both connector and connectors are passed
    dup_agent = build_agent(
        FunctionModel(scripted),
        SCHEMA_SDL,
        bindings[0],
        connectors=bindings,
    )
    dup_ids = [getattr(t, "id", None) for t in dup_agent.toolsets if getattr(t, "id", None) in {"github", "slack", "google-workspace"}]
    assert len(dup_ids) == 3
