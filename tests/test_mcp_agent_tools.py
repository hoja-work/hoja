"""Agent-facing Hoja MCP tools over real streamable HTTP with OAuth bearer tokens."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

from starlette.testclient import TestClient

from cliniar_server import db
from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, now_iso, seed_tenant
from cliniar_server.mcp_oauth import READ_SCOPE, WRITE_SCOPE, HojaOAuthProvider
from cliniar_server.store import Store

HOST = "mcp.example.test"


def _body(resp) -> dict:
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


class Mcp:
    def __init__(self, client: TestClient, token: str):
        from mcp.types import LATEST_PROTOCOL_VERSION

        self.client = client
        self.headers = {"Host": HOST, "Authorization": f"Bearer {token}"}
        self.version = LATEST_PROTOCOL_VERSION
        self.envelope = {"_meta": {"io.modelcontextprotocol/protocolVersion": self.version,
                                   "io.modelcontextprotocol/clientCapabilities": {}}}
        init = client.post("/mcp/", headers=self.headers, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": self.version, "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}}})
        assert init.status_code == 200, init.text
        self.instructions = _body(init)["result"].get("instructions", "")

    def _post(self, method: str, params: dict, name: str | None = None) -> dict:
        headers = {**self.headers, "MCP-Protocol-Version": self.version, "Mcp-Method": method}
        if name:
            headers["Mcp-Name"] = name
        resp = self.client.post("/mcp/", headers=headers,
                                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {**self.envelope, **params}})
        assert resp.status_code == 200, resp.text
        return _body(resp)["result"]

    def tools(self) -> dict[str, dict]:
        return {t["name"]: t for t in self._post("tools/list", {})["tools"]}

    def call(self, tool: str, /, **args) -> tuple[bool, object]:
        result = self._post("tools/call", {"name": tool, "arguments": args}, tool)
        text = result["content"][0]["text"]
        if result.get("isError"):
            return False, text
        return True, json.loads(text)


@pytest.fixture
def hoja(tmp_path):
    path = tmp_path / "mcp-agent.db"
    engine = make_engine(path)
    migrate(engine)
    seed = seed_tenant(engine, org_name="Agents", org_url_key="agents", team_key="ENG",
                       user_name="Ada Agent", user_email="ada@example.test", demo_issues=True)
    from sqlalchemy import select

    row = engine.connect().execute(select(db.api_key).where(db.api_key.c.token_hash == db.token_hash(seed["token"]))).mappings().first()
    provider = HojaOAuthProvider(engine, Store(engine), True)
    with engine.begin() as conn:
        conn.execute(db.oauth_client.insert().values(
            client_id="agent", created_at=now_iso(),
            client_json='{"client_id":"agent","redirect_uris":["https://client.test/cb"],"token_endpoint_auth_method":"none"}'))

    def token(scopes):
        with engine.begin() as conn:
            return provider._mint_pair(conn, "agent", row["user_id"], row["organization_id"], scopes,
                                       f"https://{HOST}/mcp").access_token

    app = create_app(db_path=str(path), trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=HOST)
    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 44321)) as client:
        yield lambda scopes=(READ_SCOPE, WRITE_SCOPE): Mcp(client, token(list(scopes)))


def test_tool_schemas_are_typed_and_annotated(hoja) -> None:
    mcp = hoja()
    tools = mcp.tools()
    assert "TEAMKEY-N" in mcp.instructions
    update = tools["hoja_issueUpdate"]
    props = update["inputSchema"]["properties"]
    assert {"id", "title", "stateId", "assigneeId", "labelIds"} <= set(props)
    assert "arguments" not in props
    assert update["inputSchema"]["required"] == ["id"]
    assert update["inputSchema"]["additionalProperties"] is False
    assert tools["hoja_issueCreate"]["inputSchema"]["required"] == ["title", "teamId"]
    assert tools["hoja_issues"]["annotations"]["readOnlyHint"] is True
    assert tools["hoja_issueDelete"]["annotations"]["destructiveHint"] is True
    # Credential- and admin-plumbing operations are not exposed to agents.
    for hidden in ("hoja_apiKeyCreate", "hoja_modelProviderCreate", "hoja_teamDelete", "hoja_workspaceCreate",
                   "hoja_memberDeactivate", "hoja_myApiKeys"):
        assert hidden not in tools


def test_list_and_argument_tools_return_readable_data(hoja) -> None:
    mcp = hoja()
    ok, teams = mcp.call("hoja_teams")
    assert ok, teams
    assert teams["teams"]["nodes"][0]["key"] == "ENG"
    ok, issues = mcp.call("hoja_issues", filter={"team": {"key": {"eq": "ENG"}}}, first=5)
    assert ok, issues
    first = issues["issues"]["nodes"][0]
    assert first["identifier"].startswith("ENG-") and first["title"] and first["state"]["name"]
    ok, issue = mcp.call("hoja_issue", id="eng-1")
    assert ok, issue
    assert issue["issue"]["identifier"] == "ENG-1"
    assert "comments" in issue["issue"] and issue["issue"]["team"]["key"] == "ENG"
    ok, found = mcp.call("hoja_searchIssues", term="ENG-1")
    assert ok and found["searchIssues"]["nodes"]
    ok, me = mcp.call("hoja_viewer")
    assert ok and me["viewer"]["email"] == "ada@example.test"


def test_writes_accept_friendly_references_and_return_entity(hoja) -> None:
    mcp = hoja()
    ok, created = mcp.call("hoja_issueCreate", teamId="ENG", title="Typed args", priority="high", assigneeId="me")
    assert ok, created
    issue = created["issueCreate"]["issue"]
    assert issue["identifier"].startswith("ENG-") and issue["priority"] == 2
    assert issue["assignee"]["email"] == "ada@example.test"
    ok, label = mcp.call("hoja_issueLabelCreate", name="agent", teamId="ENG")
    assert ok, label
    ok, updated = mcp.call("hoja_issueUpdate", id=issue["identifier"], stateId="In Progress", labelIds=["agent"])
    assert ok, updated
    assert updated["issueUpdate"]["issue"]["state"]["name"] == "In Progress"
    assert [n["name"] for n in updated["issueUpdate"]["issue"]["labels"]["nodes"]] == ["agent"]
    ok, comment = mcp.call("hoja_commentCreate", issueId=issue["identifier"], body="Picked up")
    assert ok and comment["commentCreate"]["comment"]["body"] == "Picked up"
    # Nested `input` objects are accepted too.
    ok, nested = mcp.call("hoja_issueUpdate", id=issue["identifier"], input={"title": "Renamed"})
    assert ok and nested["issueUpdate"]["issue"]["title"] == "Renamed"


def test_empty_placeholder_arguments_are_ignored(hoja) -> None:
    mcp = hoja()
    blank = {"eq": "", "in": [], "neq": ""}
    ok, issues = mcp.call("hoja_issues", after="", filter={"team": {"key": {**blank, "eq": "ENG"}}, "id": blank,
        "priority": {"eq": -1, "neq": -1}})
    assert ok and issues["issues"]["nodes"], issues
    ok, created = mcp.call("hoja_issueCreate", teamId="ENG", title="Blank fields", assigneeId="", projectId="", labelIds=[])
    assert ok and created["issueCreate"]["issue"]["assignee"] is None, created
    ok, updated = mcp.call("hoja_issueUpdate", id="ENG-1", priority=0, estimate=0, title="", stateId="In Progress")
    assert ok and updated["issueUpdate"]["issue"]["priority"] == 2, updated
    ok, cleared = mcp.call("hoja_issueUpdate", id="ENG-1", priority="none")
    assert ok and cleared["issueUpdate"]["issue"]["priority"] == 0, cleared


def test_errors_are_specific_and_actionable(hoja) -> None:
    mcp = hoja()
    ok, err = mcp.call("hoja_teams", workspaceId="x")
    assert not ok and "Unknown argument(s) workspaceId" in err and "Valid arguments" in err
    ok, err = mcp.call("hoja_issueUpdate", id="ENG-1", assigneeId="Bob Nobody")
    assert not ok and "No workspace member matches 'Bob Nobody'" in err and "Ada Agent" in err
    ok, err = mcp.call("hoja_issueUpdate", id="ENG-1", stateId="Shipped")
    assert not ok and "No workflow state matches 'Shipped'" in err and "In Progress" in err
    ok, err = mcp.call("hoja_issue", id="ENG-9999")
    assert not ok and "nothing found" in err and "ENG-9999" in err
    ok, err = mcp.call("hoja_issueUpdate", id="ENG-9999", title="x")
    assert not ok and "did not apply" in err
    ok, err = mcp.call("hoja_issueCreate", teamId="NOPE", title="x")
    assert not ok and "No team matches 'NOPE'" in err and "ENG" in err


def test_read_only_grant_explains_missing_write_scope(hoja) -> None:
    mcp = hoja((READ_SCOPE,))
    ok, err = mcp.call("hoja_issueCreate", teamId="ENG", title="nope")
    assert not ok and "read-only" in err and WRITE_SCOPE in err
    ok, teams = mcp.call("hoja_teams")
    assert ok, teams
