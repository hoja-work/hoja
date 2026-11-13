"""The 72-tool MCP surface under row-level security, over streamable HTTP.

An OAuth token and an API key each serve one workspace: every tool call sees its
own organization's rows and cannot reach another organization's issue by id.
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from hoja_server import db
from hoja_server.db import now_iso, seed_tenant
from hoja_server.mcp_oauth import READ_SCOPE, WRITE_SCOPE, HojaOAuthProvider
from hoja_server.store import Store
from tests import rls_mode

pytestmark = pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS + Postgres")
HOST = "mcp.probe.test"


def _body(resp):
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


class Mcp:
    def __init__(self, client, token):
        from mcp.types import LATEST_PROTOCOL_VERSION
        self.client, self.version = client, LATEST_PROTOCOL_VERSION
        self.headers = {"Host": HOST, "Authorization": f"Bearer {token}"}
        self.envelope = {"_meta": {"io.modelcontextprotocol/protocolVersion": self.version,
                                   "io.modelcontextprotocol/clientCapabilities": {}}}
        init = client.post("/mcp/", headers=self.headers, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": self.version, "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "1"}}})
        assert init.status_code == 200, init.text

    def _post(self, method, params, name=None):
        headers = {**self.headers, "MCP-Protocol-Version": self.version, "Mcp-Method": method}
        if name:
            headers["Mcp-Name"] = name
        resp = self.client.post("/mcp/", headers=headers,
                                json={"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": {**self.envelope, **params}})
        assert resp.status_code == 200, resp.text
        return _body(resp)["result"]

    def call(self, tool, **args):
        result = self._post("tools/call", {"name": tool, "arguments": args}, tool)
        text = result["content"][0]["text"]
        if result.get("isError"):
            return False, text
        return True, json.loads(text)


@pytest.fixture
def rls_app(tmp_path):
    from hoja_server.app import create_app

    owner, kwargs = rls_mode.workspace(tmp_path, "mcp-probe")
    a = seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                    user_email="a@alpha.test", token="probe-a", demo_issues=True)
    b = seed_tenant(owner, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                    user_email="b@bravo.test", token="probe-b", demo_issues=True)
    with owner.begin() as conn:
        conn.execute(db.oauth_client.insert().values(
            client_id="probe", created_at=now_iso(),
            client_json='{"client_id":"probe","redirect_uris":["https://client.test/cb"],'
                        '"token_endpoint_auth_method":"none"}'))
    provider = HojaOAuthProvider(owner, Store(owner), True, public_host=HOST)

    def token(scopes):
        with owner.begin() as conn:
            return provider._mint_pair(conn, "probe", a["user_id"], a["org_id"], list(scopes),
                                       f"https://{HOST}/mcp").access_token

    app = create_app(**kwargs, open_mode=False, trusted_proxy_cidrs="127.0.0.1/32",
                     trusted_public_host=HOST)
    with TestClient(app, base_url=f"https://{HOST}", client=("127.0.0.1", 40123)) as client:
        yield {"client": client, "token": token, "a": a, "b": b, "owner": owner}


def test_oauth_mcp_reads_only_its_own_workspace_under_rls(rls_app):
    mcp = Mcp(rls_app["client"], rls_app["token"]((READ_SCOPE, WRITE_SCOPE)))
    ok, viewer = mcp.call("hoja_viewer")
    assert ok, viewer
    assert viewer["viewer"]["email"] == "a@alpha.test"
    ok, tasks = mcp.call("hoja_tasks", first=250)
    assert ok, tasks
    ids = [n["identifier"] for n in tasks["tasks"]["nodes"]]
    assert ids and all(i.startswith("ALP-") for i in ids), ids


def test_oauth_mcp_cannot_touch_another_workspace_issue_under_rls(rls_app):
    mcp = Mcp(rls_app["client"], rls_app["token"]((READ_SCOPE, WRITE_SCOPE)))
    ok, out = mcp.call("hoja_task", id="BRV-1")
    assert ok is False, out
    ok, out = mcp.call("hoja_taskUpdate", id="BRV-1", title="pwned")
    assert ok is False, out
    ok, out = mcp.call("hoja_taskDelete", id="BRV-1")
    assert ok is False, out
    with rls_app["owner"].connect() as conn:
        from sqlalchemy import select
        title = conn.execute(select(db.issue.c.title).where(db.issue.c.identifier == "BRV-1")).scalar()
    assert title != "pwned"


def test_api_key_mcp_reads_only_its_own_workspace_under_rls(rls_app):
    mcp = Mcp(rls_app["client"], rls_app["a"]["token"])
    ok, tasks = mcp.call("hoja_tasks", first=250)
    assert ok, tasks
    ids = [n["identifier"] for n in tasks["tasks"]["nodes"]]
    assert ids and all(i.startswith("ALP-") for i in ids), ids
