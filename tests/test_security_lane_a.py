"""Regression tests for lane A hardening: auth precedence, read-only keys, CSRF, limits, headers."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cliniar_server.app import create_app
from cliniar_server.db import make_engine, migrate, seed_tenant
from cliniar_server.store import Store

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _setup(tmp_path, name="sec"):
    db_path = tmp_path / f"{name}.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Sec Org", org_url_key=f"{name}-org", team_key="ENG",
                         user_name="Admin", user_email=f"{name}@sec.test", token=f"{name}-admin-token",
                         demo_issues=False)
    return db_path, engine, seeded


def _admin_client(db_path, seeded, **kwargs):
    # localhost over http is the dev exemption from the Secure cookie flag, so cookies round-trip.
    return TestClient(create_app(str(db_path), open_mode=kwargs.pop("open_mode", False), **kwargs),
                      base_url="http://localhost")


def _read_key(client, admin_token) -> str:
    body = client.post("/graphql", headers={"Authorization": f"Bearer {admin_token}"},
                       json={"query": 'mutation{ apiKeyCreate(label:"ro", access:"read"){ secret } }'}).json()
    return body["data"]["apiKeyCreate"]["secret"]


def _browser_login(client, email: str) -> None:
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


# --- Fix 1: bearer wins over cookie -------------------------------------------------

def test_read_only_bearer_cannot_escalate_with_browser_cookie(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "bearer")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(client, "bearer@sec.test")
    ro = _read_key(client, seeded["token"])
    # Cookie session is still present on this client; the read-only bearer must still be read-only.
    resp = client.post("/graphql", headers={"Authorization": f"Bearer {ro}"},
                       json={"query": 'mutation{ teamCreate(input:{name:"X", key:"XX"}){ success } }'})
    assert resp.status_code == 403, resp.text
    threads = client.post("/api/agent/threads/create", headers={"Authorization": f"Bearer {ro}"},
                          json={"messages": [{"role": "user", "content": "hi"}]})
    assert threads.status_code == 403


def test_cookie_path_keeps_full_access_when_no_bearer(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "cookiefull")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(client, "cookiefull@sec.test")
    resp = client.post("/graphql", json={"query": '{ viewer { email } }'},
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["data"]["viewer"]["email"] == "cookiefull@sec.test"


# --- Fix 2: read-only keys cannot write through agent endpoints or agent graphql tool -------

def test_read_only_key_rejected_on_agent_thread_writes(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "agentwrite")
    client = _admin_client(db_path, seeded)
    created = client.post("/api/agent/threads/create", headers={"Authorization": f"Bearer {seeded['token']}"},
                          json={"messages": [{"role": "user", "content": "hi"}]}).json()
    thread_id = created["id"]
    ro = _read_key(client, seeded["token"])
    headers = {"Authorization": f"Bearer {ro}"}
    assert client.post("/api/agent/threads/create", headers=headers,
                       json={"messages": [{"role": "user", "content": "x"}]}).status_code == 403
    assert client.patch(f"/api/agent/threads/update/{thread_id}", headers=headers,
                        json={"title": "pwned"}).status_code == 403
    assert client.delete(f"/api/agent/threads/delete/{thread_id}", headers=headers).status_code == 403
    assert client.get("/api/agent/threads/get", headers=headers).status_code == 200


def test_agent_graphql_tool_refuses_mutation_for_read_access(tmp_path) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from cliniar_server.agent_chat import AgentDeps, build_agent
    from cliniar_server.resolvers import build_schema
    from cliniar_server.schema_sdl import SCHEMA_SDL

    db_path, engine, seeded = _setup(tmp_path, "agenttool")
    seen = {}

    def scripted(messages, info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("graphql", {
                "query": 'mutation { apiKeyCreate(label:"x", access:"read"){ secret } }', "variables": {}})])
        seen["tool_result"] = str(messages[-1].parts[0].content)
        return ModelResponse(parts=[TextPart("done")])

    context = {"store": Store(engine), "writer": None, "org_id": seeded["org_id"],
               "user_id": seeded["user_id"], "identity_id": None, "browser_session": False,
               "access": "read"}
    agent = build_agent(FunctionModel(scripted), SCHEMA_SDL)
    agent.run_sync("make a key", deps=AgentDeps(build_schema(), context))
    assert "read-only" in seen["tool_result"]
    assert "secret" not in seen["tool_result"]


def test_agent_graphql_tool_blocks_mutations_for_read_access(tmp_path) -> None:
    from cliniar_server.agent_chat import read_only_violation

    assert read_only_violation("mutation { teamCreate(input:{}) { success } }", "read") is True
    assert read_only_violation("{ viewer { id } }", "read") is False
    assert read_only_violation("mutation { teamCreate(input:{}) { success } }", "read_write") is False


def test_graphql_read_key_mutation_rejected_with_403(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "rokey")
    client = _admin_client(db_path, seeded)
    ro = _read_key(client, seeded["token"])
    resp = client.post("/graphql", headers={"Authorization": f"Bearer {ro}"},
                       json={"query": 'mutation{ apiKeyCreate(label:"x", access:"read"){ secret } }'})
    assert resp.status_code == 403


# --- Fix 3: /auth/setup cannot overwrite password via bearer --------------------------

def test_bearer_cannot_overwrite_existing_password(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "setup")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    resp = client.post("/auth/setup", json={"token": seeded["token"], "password": "attacker password!!"})
    assert resp.status_code == 403
    assert client.post("/auth/login", json={"email": "setup@sec.test", "password": PASSWORD}).status_code == 200


def test_password_change_requires_session_and_current_password(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "pwchange")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(client, "pwchange@sec.test")
    wrong = client.post("/auth/setup", json={"password": "new password value", "currentPassword": "nope nope nope"})
    assert wrong.status_code == 401
    ok = client.post("/auth/setup", json={"password": "new password value", "currentPassword": PASSWORD})
    assert ok.status_code == 201
    assert client.post("/auth/login", json={"email": "pwchange@sec.test", "password": "new password value"}).status_code == 200


# --- Fix 4: GraphQL hardening -----------------------------------------------------------

def _gql(client, token, body, raw=False):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if raw:
        return client.post("/graphql", headers=headers, content=body)
    return client.post("/graphql", headers=headers, json=body)


def test_graphql_body_size_limit_and_bad_json(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "gqlbody")
    client = _admin_client(db_path, seeded)
    big = '{"query": "{ viewer { id } }", "pad": "' + ("a" * 1_100_000) + '"}'
    assert _gql(client, seeded["token"], big, raw=True).status_code == 413
    assert _gql(client, seeded["token"], "{not json", raw=True).status_code == 400
    assert _gql(client, seeded["token"], "[1,2]", raw=True).status_code == 400
    deep = "[" * 100000
    assert _gql(client, seeded["token"], deep, raw=True).status_code == 400


def test_graphql_rejects_non_string_query_and_non_dict_variables(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "gqltypes")
    client = _admin_client(db_path, seeded)
    assert _gql(client, seeded["token"], {"query": ["{ viewer { id } }"]}).status_code == 400
    assert _gql(client, seeded["token"], {"query": "{ viewer { id } }", "variables": "x"}).status_code == 400


def test_graphql_depth_and_alias_limits(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "gqllimits")
    client = _admin_client(db_path, seeded)
    deep = "{ a" + "{ a" * 20 + " }" * 21
    resp = _gql(client, seeded["token"], {"query": deep})
    assert resp.status_code == 400
    assert "depth" in resp.json()["errors"][0]["message"]
    aliases = "{ " + " ".join(f"a{i}: viewer {{ id }}" for i in range(101)) + " }"
    resp = _gql(client, seeded["token"], {"query": aliases})
    assert resp.status_code == 400
    ok = _gql(client, seeded["token"], {"query": "{ viewer { id } }"})
    assert ok.status_code == 200 and "data" in ok.json()


def test_graphql_formatter_masks_internal_but_keeps_intentional_errors() -> None:
    from graphql import GraphQLError

    from cliniar_server.app import _format_graphql_error

    internal = GraphQLError("secret sql detail", original_error=RuntimeError("db password is hunter2"))
    assert _format_graphql_error(internal) == {
        "message": "Internal error", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}
    intentional = GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT"})
    assert _format_graphql_error(intentional)["message"] == "Team not found"
    value_error = GraphQLError("first must be non-negative.", original_error=ValueError("first must be non-negative."))
    assert _format_graphql_error(value_error)["message"] == "first must be non-negative."


def test_graphql_internal_exception_is_masked_in_response(tmp_path, monkeypatch) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "gqlmask")
    client = _admin_client(db_path, seeded)
    def boom(*_a, **_k):
        raise RuntimeError("db password is hunter2")

    monkeypatch.setattr(Store, "membership_for_profile", boom)
    resp = _gql(client, seeded["token"], {"query": "{ viewer { id } }"})
    text = resp.text
    assert "hunter2" not in text


def test_graphql_intentional_error_message_is_preserved(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "gqlintent")
    client = _admin_client(db_path, seeded)
    resp = _gql(client, seeded["token"], {"query": "{ issues(first: -1) { nodes { id } } }"})
    assert resp.status_code == 200
    assert resp.json()["errors"][0]["message"] == "first must be an integer from 1 to 250."


# --- Fix 5: CSRF for cookie-authenticated writes ----------------------------------------

def test_cookie_write_requires_json_content_type(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "csrfct")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(client, "csrfct@sec.test")
    resp = client.post("/graphql", content=b'{"query":"{ viewer { id } }"}',
                       headers={"Content-Type": "text/plain"})
    assert resp.status_code == 403
    resp = client.post("/api/agent/threads/create", content=b'{"messages":[]}',
                       headers={"Content-Type": "text/plain"})
    assert resp.status_code == 403


def test_cookie_write_rejects_foreign_origin(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "csrforigin")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(client, "csrforigin@sec.test")
    resp = client.post("/graphql", json={"query": "{ viewer { id } }"},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    ok = client.post("/graphql", json={"query": "{ viewer { id } }"},
                     headers={"Origin": "http://localhost"})
    assert ok.status_code == 200


def test_login_flow_not_blocked_by_csrf_rules(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "csrflogin")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    resp = client.post("/auth/login", json={"email": "csrflogin@sec.test", "password": PASSWORD},
                       headers={"Origin": "http://localhost"})
    assert resp.status_code == 200


# --- Fix 6: websocket origin and revocation ---------------------------------------------

def test_websocket_rejects_foreign_origin(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "wsorigin")
    client = _admin_client(db_path, seeded)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?after=0",
                                      headers={"Authorization": f"Bearer {seeded['token']}",
                                               "Origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_websocket_closes_when_key_revoked(tmp_path, monkeypatch) -> None:
    import cliniar_server.app as app_module

    db_path, engine, seeded = _setup(tmp_path, "wsrevoke")
    monkeypatch.setattr(app_module, "_WS_REVALIDATE_SECONDS", 0)
    client = _admin_client(db_path, seeded)
    from sqlalchemy import delete

    from cliniar_server.db import api_key, token_hash

    with client.websocket_connect("/ws?after=0", headers={"Authorization": f"Bearer {seeded['token']}"}) as ws:
        with engine.begin() as conn:
            conn.execute(delete(api_key).where(api_key.c.token_hash == token_hash(seeded["token"])))
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(20):
                ws.receive_json()
        assert closed.value.code == 4401


# --- Fix 7: login rate limiting --------------------------------------------------------

def test_login_rate_limit_per_email(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "ratelimit")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    statuses = [client.post("/auth/login", json={"email": "ratelimit@sec.test", "password": "wrong wrong wrong"}).status_code
                for _ in range(12)]
    assert statuses[:10] == [401] * 10
    assert 429 in statuses[10:]
    blocked = client.post("/auth/login", json={"email": "ratelimit@sec.test", "password": PASSWORD})
    assert blocked.status_code == 429


def test_limiter_does_not_block_other_accounts_or_successful_logins(tmp_path) -> None:
    from cliniar_server.app import _FailureLimiter

    engine = make_engine(tmp_path / "limiter.db")
    migrate(engine)
    limiter = _FailureLimiter(engine, "test", limit=2, window_seconds=60)
    limiter.record(["email:a@x"]); limiter.record(["email:a@x"])
    assert limiter.blocked(["email:a@x"]) is True
    assert limiter.blocked(["email:b@x"]) is False


# --- Fix 8: /auth/workspaces requires password ----------------------------------------

def test_workspaces_enumeration_requires_password(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "wsenum")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    assert client.post("/auth/workspaces", json={"email": "wsenum@sec.test"}).status_code == 401
    assert client.post("/auth/workspaces", json={"email": "wsenum@sec.test", "password": "wrong wrong wrong"}).status_code == 401
    assert client.post("/auth/workspaces", json={"email": "nobody@sec.test", "password": PASSWORD}).status_code == 401
    ok = client.post("/auth/workspaces", json={"email": "wsenum@sec.test", "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["workspaces"][0]["urlKey"] == "wsenum-org"


# --- Fix 9: security headers ------------------------------------------------------------

def test_security_headers_on_api_and_html(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "headers")
    client = _admin_client(db_path, seeded)
    api = client.get("/health")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["referrer-policy"] == "same-origin"
    assert "content-security-policy" not in api.headers
    html = client.get("/anything-not-a-route")
    assert "frame-ancestors 'none'" in html.headers["content-security-policy"]
    assert "object-src 'none'" in html.headers["content-security-policy"]


# --- Fix 10: Secure cookie flag -----------------------------------------------------------

def test_session_cookie_secure_unless_localhost_http(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "cookiesecure")
    client = _admin_client(db_path, seeded)
    assert client.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    local = client.post("/auth/login", json={"email": "cookiesecure@sec.test", "password": PASSWORD})
    assert "secure" not in local.headers["set-cookie"].lower()
    assert "httponly" in local.headers["set-cookie"].lower()
    remote = TestClient(create_app(str(db_path), open_mode=False), base_url="http://hoja.example.test")
    assert remote.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code in (201, 403)
    remote_login = remote.post("/auth/login", json={"email": "cookiesecure@sec.test", "password": PASSWORD})
    assert "secure" in remote_login.headers["set-cookie"].lower()


# --- Fix 11: /health and open mode ----------------------------------------------------------

def test_health_does_not_expose_open_mode(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "health")
    client = _admin_client(db_path, seeded, open_mode=True)
    assert "open_mode" not in client.get("/health").json()


def test_open_mode_refuses_non_loopback_clients(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "openremote")
    remote = TestClient(create_app(str(db_path), open_mode=True), client=("203.0.113.9", 50000))
    assert remote.get("/auth/me").status_code == 401
    assert remote.post("/graphql", json={"query": "{ viewer { id } }"}).status_code == 401
    local = TestClient(create_app(str(db_path), open_mode=True), client=("127.0.0.1", 50000))
    assert local.post("/graphql", json={"query": "{ viewer { id } }"}).status_code == 200


# --- Verification follow-ups ---------------------------------------------------------------

def test_password_change_revokes_other_sessions(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "pwrevoke")
    app = create_app(str(db_path), open_mode=False)
    first = TestClient(app, base_url="http://localhost")
    second = TestClient(app, base_url="http://localhost")
    assert first.post("/auth/setup", json={"token": seeded["token"], "password": PASSWORD}).status_code == 201
    _browser_login(first, "pwrevoke@sec.test")
    _browser_login(second, "pwrevoke@sec.test")
    assert second.get("/auth/me").status_code == 200
    changed = first.post("/auth/setup", json={"password": "a brand new password", "currentPassword": PASSWORD})
    assert changed.status_code == 201
    assert second.get("/auth/me").status_code == 401
    assert first.get("/auth/me").status_code == 401


def test_websocket_bearer_subprotocol_authenticates(tmp_path) -> None:
    db_path, _engine, seeded = _setup(tmp_path, "wssub")
    client = _admin_client(db_path, seeded)
    with client.websocket_connect("/ws?after=0", subprotocols=["bearer", seeded["token"]]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        ws.close()


def test_failure_limiter_is_bounded(tmp_path) -> None:
    from sqlalchemy import func, select

    from cliniar_server import db
    from cliniar_server.app import _FailureLimiter

    engine = make_engine(tmp_path / "bounded.db")
    migrate(engine)
    limiter = _FailureLimiter(engine, "test", limit=10, window_seconds=0.05)
    for i in range(500):
        limiter.record([f"ip:{i}"])
    time.sleep(0.1)
    limiter.record(["ip:final"])
    with engine.connect() as conn:
        rows = conn.execute(select(func.count()).select_from(db.rate_limit_event)).scalar_one()
    assert rows == 1
