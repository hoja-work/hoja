"""Actor attribution, delegation (on_behalf_of), credential tracking, and causal trace ingestion."""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import mcp_oauth
from hoja_server.app import create_app
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    FIRST_PARTY_CLIENT_ID,
    applied_schema_revision,
    attachment,
    audit_event,
    change_event,
    make_engine,
    migrate,
    oauth_access_token,
    oauth_client,
    seed_tenant,
    sla_clock,
    sla_policy,
)
from hoja_server.store import Store
from tests import legacy_schema, rls_mode


def _gql(client: TestClient, token: str, query: str, variables: dict | None = None,
         extra_headers: dict[str, str] | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
    res = client.post("/graphql", json={"query": query, "variables": variables or {}}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "attr-test-master")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    engine, kwargs = rls_mode.workspace(tmp_path, "attr")
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="owner@alpha.test", token="owner-tok", demo_issues=False)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"),
                     open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host="hoja.test")
    client = TestClient(app, base_url="https://hoja.test", client=("127.0.0.1", 40000))
    with client:
        yield engine, client, alpha


def test_api_key_defaults_to_agent_and_supports_personal_and_updates(workspace):
    engine, client, alpha = workspace

    # 1. Default apiKeyCreate creates an agent-scoped key.
    created = _gql(client, alpha["token"], '''
        mutation {
          apiKeyCreate(label: "Claude Key", access: "read_write") {
            success
            apiKey { id label actorKind agentMemberId }
            secret
          }
        }''')
    payload = created["data"]["apiKeyCreate"]
    assert payload["success"] is True
    assert payload["apiKey"]["actorKind"] == "agent"
    agent_key_id = payload["apiKey"]["id"]
    agent_secret = payload["secret"]

    # 2. Explicit human key stays personal.
    human_created = _gql(client, alpha["token"], '''
        mutation {
          apiKeyCreate(label: "Personal CLI", access: "read_write", actorKind: "human") {
            success
            apiKey { id actorKind }
            secret
          }
        }''')
    assert human_created["data"]["apiKeyCreate"]["apiKey"]["actorKind"] == "human"
    human_key_id = human_created["data"]["apiKeyCreate"]["apiKey"]["id"]
    human_secret = human_created["data"]["apiKeyCreate"]["secret"]

    # 3. Bind a key to a specific agent_member.
    agent_res = _gql(client, alpha["token"], '''
        mutation {
          agentMemberCreate(input: { name: "Builder", displayName: "Builder" }) {
            success
            agentMember { id userId }
          }
        }''')
    agent_member = agent_res["data"]["agentMemberCreate"]["agentMember"]
    bound_res = _gql(client, alpha["token"], '''
        mutation($m: String) {
          apiKeyCreate(label: "Bound Agent", access: "read_write", actorKind: "agent", agentMemberId: $m) {
            success
            apiKey { id actorKind agentMemberId agentUserId agentName }
            secret
          }
        }''', {"m": agent_member["id"]})
    bound_key = bound_res["data"]["apiKeyCreate"]["apiKey"]
    assert bound_key["actorKind"] == "agent"
    assert bound_key["agentUserId"] == agent_member["userId"]
    bound_secret = bound_res["data"]["apiKeyCreate"]["secret"]

    # 4. Toggle existing key from human -> agent via apiKeyUpdate.
    toggled = _gql(client, alpha["token"], '''
        mutation($id: String!) {
          apiKeyUpdate(id: $id, actorKind: "agent") {
            success
            apiKey { id actorKind }
          }
        }''', {"id": human_key_id})
    assert toggled["data"]["apiKeyUpdate"]["apiKey"]["actorKind"] == "agent"

    # Toggle back to human for the write test below.
    _gql(client, alpha["token"], '''
        mutation($id: String!) { apiKeyUpdate(id: $id, actorKind: "human") { success } }
    ''', {"id": human_key_id})

    # Verify Postgres change_event attribution across keys + headers.
    if engine.dialect.name == "postgresql":
        # Create issues using each key.
        def make_issue(tok, title, extra_headers=None):
            res = _gql(client, tok, '''
                mutation($teamId: String!, $title: String!) {
                  issueCreate(input: { teamId: $teamId, title: $title }) {
                    success issue { id identifier }
                  }
                }''', {"teamId": alpha["team_id"], "title": title}, extra_headers=extra_headers)
            return res["data"]["issueCreate"]["issue"]

        i_agent = make_issue(agent_secret, "From agent key", {"X-Hoja-Session-Id": "sess-key-1"})
        i_bound = make_issue(bound_secret, "From bound agent key")
        i_human = make_issue(human_secret, "From personal key")
        i_header = make_issue(human_secret, "From personal key with agent header",
                              {"X-Hoja-Actor": "agent", "X-Hoja-Session-Id": "sess-hdr-1"})

        with engine.connect() as conn:
            def ce_for(iid):
                return conn.execute(select(change_event).where(and_(
                    change_event.c.entity == "issue",
                    change_event.c.entity_id == iid,
                    change_event.c.action == "create",
                ))).mappings().one()

            row_agent = ce_for(i_agent["id"])
            assert row_agent["actor_kind"] == "agent"
            assert row_agent["actor_id"] == alpha["user_id"]
            assert row_agent["on_behalf_of"] == alpha["user_id"]
            assert row_agent["credential_kind"] == "api_key"
            assert row_agent["credential_id"] == agent_key_id
            assert row_agent["session_id"] == "sess-key-1"

            row_bound = ce_for(i_bound["id"])
            assert row_bound["actor_kind"] == "agent"
            assert row_bound["actor_id"] == agent_member["userId"]
            assert row_bound["on_behalf_of"] == alpha["user_id"]
            assert row_bound["credential_id"] == bound_key["id"]

            row_human = ce_for(i_human["id"])
            assert row_human["actor_kind"] == "human"
            assert row_human["actor_id"] == alpha["user_id"]
            assert row_human["on_behalf_of"] is None
            assert row_human["credential_id"] == human_key_id

            row_header = ce_for(i_header["id"])
            assert row_header["actor_kind"] == "agent"
            assert row_header["on_behalf_of"] == alpha["user_id"]
            assert row_header["session_id"] == "sess-hdr-1"

            # Verify audit_event captured agent_user_id + on_behalf_of for bound key.
            _gql(client, bound_secret, '''
                mutation { apiKeyCreate(label: "Subkey") { success } }
            ''')
            arow = conn.execute(select(audit_event).where(
                audit_event.c.action == "api_key.create"
            ).order_by(audit_event.c.seq.desc())).mappings().first()
            assert arow["actor_kind"] == "agent"
            assert arow["actor_id"] == agent_member["userId"]
            assert arow["on_behalf_of"] == alpha["user_id"]


def test_oauth_grant_attribution_and_sla_first_response(workspace):
    engine, client, alpha = workspace

    # Invite a second human teammate so we can verify SLA first-response behavior.
    invited = _gql(client, alpha["token"], '''
        mutation { memberInvite(input: { name: "Teammate", email: "mate@alpha.test" }) { success user { id } } }
    ''')
    mate_id = invited["data"]["memberInvite"]["user"]["id"]
    store = Store(engine)
    _, mate_agent_tok = store.create_api_key(alpha["org_id"], mate_id, "Mate Agent", "read_write", actor_kind="agent")

    # Seed an SLA policy so new issues get a first-response clock.
    with engine.begin() as conn:
        conn.execute(sla_policy.insert().values(
            id="sla-1", organization_id=alpha["org_id"], team_id=alpha["team_id"],
            name="Default SLA", first_response_minutes=60, resolution_minutes=240,
            enabled=True, created_at="2026-11-23T00:00:00+00:00", updated_at="2026-11-23T00:00:00+00:00",
        ))

    # Mint a third-party MCP OAuth grant (defaults to actor_kind='agent') and a first-party CLI grant.
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(hours=1)).isoformat()
    with engine.begin() as conn:
        conn.execute(oauth_client.insert().values(
            client_id="mcp-third-party", client_json=json.dumps({"client_id": "mcp-third-party", "client_name": "Claude Code"}),
            created_at=now.isoformat(),
        ))
        if not conn.execute(select(oauth_client.c.client_id).where(
                oauth_client.c.client_id == FIRST_PARTY_CLIENT_ID)).first():
            conn.execute(oauth_client.insert().values(
                client_id=FIRST_PARTY_CLIENT_ID, client_json=json.dumps({"client_id": FIRST_PARTY_CLIENT_ID, "client_name": "Hoja CLI"}),
                created_at=now.isoformat(),
            ))
        third_grant = mcp_oauth.upsert_grant(
            conn, org_id=alpha["org_id"], user_id=alpha["user_id"],
            client_id="mcp-third-party", client_name="Claude Code", label=None,
            scopes=[mcp_oauth.READ_SCOPE, mcp_oauth.WRITE_SCOPE], user_agent="claude/1.0",
        )
        cli_grant = mcp_oauth.upsert_grant(
            conn, org_id=alpha["org_id"], user_id=alpha["user_id"],
            client_id=FIRST_PARTY_CLIENT_ID, client_name="Hoja CLI", label="laptop",
            scopes=[mcp_oauth.READ_SCOPE, mcp_oauth.WRITE_SCOPE], user_agent="hoja-cli/1.0",
        )
        conn.execute(oauth_access_token.insert().values(
            token_hash=mcp_oauth._digest("oauth-cli-tok"),
            client_id=FIRST_PARTY_CLIENT_ID, user_id=alpha["user_id"],
            organization_id=alpha["org_id"], scopes=json.dumps([mcp_oauth.READ_SCOPE, mcp_oauth.WRITE_SCOPE]),
            expires_at=exp, grant_id=cli_grant, resource="https://hoja.test/graphql",
        ))
        conn.execute(oauth_access_token.insert().values(
            token_hash=mcp_oauth._digest("oauth-mcp-tok"),
            client_id="mcp-third-party", user_id=alpha["user_id"],
            organization_id=alpha["org_id"], scopes=json.dumps([mcp_oauth.READ_SCOPE, mcp_oauth.WRITE_SCOPE]),
            expires_at=exp, grant_id=third_grant, resource="https://hoja.test/mcp",
        ))

    # Create an issue as owner (via CLI OAuth token).
    created = _gql(client, "oauth-cli-tok", '''
        mutation($t: String!) {
          issueCreate(input: { teamId: $t, title: "SLA test" }) { success issue { id identifier } }
        }''', {"t": alpha["team_id"]}, extra_headers={"Host": "hoja.test", "X-Forwarded-Proto": "https"})
    issue_id = created["data"]["issueCreate"]["issue"]["id"]

    # Bind an agent member to an owner's key and reply to the owner's own issue:
    # SLA first_responded_at MUST remain NULL because on_behalf_of == creator_id.
    agent_res = _gql(client, alpha["token"], '''
        mutation { agentMemberCreate(input: { name: "OwnerAgent" }) { agentMember { id userId } } }
    ''')
    owner_agent_mid = agent_res["data"]["agentMemberCreate"]["agentMember"]["id"]
    _, owner_agent_tok = store.create_api_key(
        alpha["org_id"], alpha["user_id"], "Owner Agent Key", "read_write",
        actor_kind="agent", agent_member_id=owner_agent_mid,
    )
    _gql(client, owner_agent_tok, '''
        mutation($i: String!) { commentCreate(input: { issueId: $i, body: "Working on it" }) { success } }
    ''', {"i": issue_id})

    with engine.connect() as conn:
        clk = conn.execute(select(sla_clock).where(sla_clock.c.issue_id == issue_id)).mappings().one()
        assert clk["first_responded_at"] is None

    # Now teammate's agent replies -> on_behalf_of == mate_id != creator -> SLA first_responded_at IS recorded!
    _gql(client, mate_agent_tok, '''
        mutation($i: String!) { commentCreate(input: { issueId: $i, body: "Reviewed by teammate agent" }) { success } }
    ''', {"i": issue_id})
    with engine.connect() as conn:
        clk = conn.execute(select(sla_clock).where(sla_clock.c.issue_id == issue_id)).mappings().one()
        assert clk["first_responded_at"] is not None

    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            ce = conn.execute(select(change_event).where(and_(
                change_event.c.entity == "issue",
                change_event.c.entity_id == issue_id,
                change_event.c.action == "create",
            ))).mappings().one()
            assert ce["actor_kind"] == "human"
            assert ce["credential_kind"] == "oauth"
            assert ce["credential_id"] == cli_grant

        # Verify third-party MCP OAuth token records actor_kind='agent' + on_behalf_of when calling MCP tool.
        mcp_res = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer oauth-mcp-tok",
                "Host": "hoja.test",
                "X-Forwarded-Proto": "https",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "X-Hoja-Session-Id": "mcp-sess-99",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "hoja_issueCreate", "arguments": {"teamId": alpha["team_id"], "title": "From MCP OAuth"}},
            },
        )
        assert mcp_res.status_code == 200, mcp_res.text
        with engine.connect() as conn:
            mcp_ce = conn.execute(select(change_event).where(
                change_event.c.session_id == "mcp-sess-99",
            )).mappings().first()
            assert mcp_ce is not None
            assert mcp_ce["actor_kind"] == "agent"
            assert mcp_ce["on_behalf_of"] == alpha["user_id"]
            assert mcp_ce["credential_kind"] == "oauth"
            assert mcp_ce["credential_id"] == third_grant


def test_causal_trace_ingestion_correlates_by_session_id_and_upserts(workspace):
    engine, client, alpha = workspace
    _, agent_tok = Store(engine).create_api_key(alpha["org_id"], alpha["user_id"], "Plugin", "read_write")

    # Issue created with session correlation header (Postgres records session_id on change_event;
    # on SQLite explicit issues list also works, and we test both paths).
    created = _gql(client, agent_tok, '''
        mutation($t: String!) {
          issueCreate(input: { teamId: $t, title: "No TaskManage issue" }) { success issue { id identifier } }
        }''', {"t": alpha["team_id"]}, extra_headers={"X-Hoja-Session-Id": "conv-abc-1"})
    ident = created["data"]["issueCreate"]["issue"]["identifier"]
    iid = created["data"]["issueCreate"]["issue"]["id"]

    trace_doc = {
        "schema": "hoja.trace/v1",
        "id": "conv-abc-1@2026-11-23T17:00:00Z",
        "createdAt": "2026-11-23T17:00:00Z",
        "source": {"host": "claude-code", "plugin": "hoja@0.5.3", "detail": "full"},
        "session": {
            "id": "conv-abc-1",
            "title": "Fix bug with sk-ant-api03-1234567890abcdef123456",
            "models": ["claude-sonnet-4"],
            "start": "2026-11-23T16:59:00Z",
            "end": "2026-11-23T17:00:00Z",
            "durationMs": 60000,
            "basis": "transcript",
        },
        "totals": {
            "turns": 1, "llmCalls": 1, "toolCalls": 2, "toolErrors": 0, "blocked": 0,
            "subagents": 0, "compactions": 0,
            "tokens": {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_creation_tokens": 0},
            "tokensByModel": {}, "llmMs": 500, "toolMs": 100, "filesTouched": [],
        },
        "tools": [], "tasks": [], "issues": [], "spans": [], "events": [],
    }

    # On Postgres, omitting `issues` still correlates via change_event.session_id!
    # On SQLite, pass explicit issues if session_id wasn't captured by trigger.
    explicit = [] if engine.dialect.name == "postgresql" else [ident]
    res1 = client.post(
        "/api/traces",
        headers={"Authorization": f"Bearer {agent_tok}"},
        json={"trace": trace_doc, "issues": explicit, "reason": "turn"},
    )
    assert res1.status_code == 200, res1.text
    body1 = res1.json()
    assert body1["issues"] == [ident]
    assert len(body1["attachments"]) == 1
    assert body1["attachments"][0]["updated"] is False

    # Re-ingesting updated trace for the same session updates in place (no duplicate attachment).
    trace_doc["totals"]["turns"] = 2
    res2 = client.post(
        "/api/traces",
        headers={"Authorization": f"Bearer {agent_tok}"},
        json={"trace": trace_doc, "issues": [ident], "reason": "session-end"},
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["attachments"][0]["updated"] is True
    assert body2["attachments"][0]["id"] == body1["attachments"][0]["id"]

    # Verify stored attachment and server-side secret scrubbing.
    with engine.connect() as conn:
        rows = conn.execute(select(attachment).where(attachment.c.issue_id == iid)).mappings().all()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "conv-abc-1"
    blob_res = client.get(body2["url"], headers={"Authorization": f"Bearer {agent_tok}"})
    assert blob_res.status_code == 200
    assert "sk-ant-api03" not in blob_res.text
    assert "<redacted>" in blob_res.text


def test_revision_97_to_98_upgrade_succeeds(tmp_path):
    engine = make_engine(tmp_path / "upgrade98.db")
    legacy_schema.create_schema(engine, 97)
    with engine.begin() as conn:
        for tbl, cols in (("api_key", ("actor_kind", "agent_user_id")),
                          ("attachment", ("session_id",)),
                          ("change_event", ("credential_kind", "credential_id", "session_id"))):
            for col in cols:
                with contextlib.suppress(Exception):
                    conn.exec_driver_sql(f"ALTER TABLE {tbl} DROP COLUMN {col}")
    assert applied_schema_revision(engine) == 97
    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION == 98
