"""OAuth as the way Hoja's own apps sign in: the hoja-cli client, the device flow,
GraphQL bearer tokens, and the authorized apps people can see and revoke.

Shapes asserted here are the contract the CLI and plugins build against.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

pytest.importorskip("mcp")

from hoja_server import db
from hoja_server.mcp_oauth import ALL_SCOPES, DEVICE_GRANT, READ_SCOPE, WRITE_SCOPE
from tests import test_mcp_onboarding as onboarding
from tests.test_mcp_onboarding import (
    CANONICAL,
    CHALLENGE,
    PINNED,
    REDIRECT,
    VERIFIER,
    _authorize,
    _consent,
    _mcp,
    _refresh,
    _register,
    _session,
)

# The same three-workspace deployment the onboarding tests use.
hosted = onboarding.hosted

CLI = "hoja-cli"
LOOPBACK = "http://127.0.0.1:51234/callback"
VIEWER = "{ viewer { id } workspace { id origin } }"


def _cli_authorize(client, host=CANONICAL, redirect=LOOPBACK, **extra):
    params = {"client_id": CLI, "redirect_uri": redirect, "response_type": "code",
              "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "cli-state",
              "scope": f"{READ_SCOPE} {WRITE_SCOPE}", "resource": f"https://{host}/graphql",
              "device_name": "hoja-cli on laptop", **extra}
    return client.get("/mcp/authorize", params=params, headers={"Host": host}, follow_redirects=False)


def _cli_login(hosted, seeded, host=CANONICAL, access="write"):
    client = hosted["client"]
    res = _cli_authorize(client, host)
    assert res.status_code == 302, res.text
    _page, approved = _consent(client, res.headers["location"], _session(hosted["engine"], seeded), host,
                               decision="allow", access=access)
    assert approved.status_code == 303, approved.text
    back = parse_qs(urlsplit(approved.headers["location"]).query)
    assert back["state"] == ["cli-state"] and back["iss"] == [f"https://{host}/mcp"]
    token = client.post("/mcp/token", headers={"Host": host}, data={
        "grant_type": "authorization_code", "code": back["code"][0], "redirect_uri": LOOPBACK,
        "client_id": CLI, "code_verifier": VERIFIER, "resource": f"https://{host}/graphql"})
    assert token.status_code == 200, token.text
    return token.json()


def _gql(client, token, host=CANONICAL, query=VIEWER, variables=None):
    return client.post("/graphql", json={"query": query, "variables": variables or {}},
                       headers={"Host": host, "Authorization": f"Bearer {token}"})


# --------------------------------------------------------------------------- metadata


def test_metadata_advertises_device_flow_and_iss_in_both_copies(hosted):
    client = hosted["client"]
    for path in ("/.well-known/oauth-authorization-server/mcp", "/mcp/.well-known/oauth-authorization-server"):
        meta = client.get(path, headers={"Host": CANONICAL})
        assert meta.status_code == 200, (path, meta.text)
        body = meta.json()
        assert body["device_authorization_endpoint"] == f"https://{CANONICAL}/mcp/device"
        assert body["authorization_response_iss_parameter_supported"] is True
        assert DEVICE_GRANT in body["grant_types_supported"]
        assert body["issuer"] == f"https://{CANONICAL}/mcp"


# --------------------------------------------------------------------------- hoja-cli, loopback


def test_cli_login_returns_workspace_and_user_and_works_on_both_endpoints(hosted):
    a = hosted["a"]
    token = _cli_login(hosted, a)
    assert token["hoja_workspace"] == {"id": a["org_id"], "name": "Alpha", "url_key": "alpha",
                                       "origin": f"https://{CANONICAL}"}
    assert token["hoja_user"] == {"id": a["user_id"], "email": "o@alpha.test", "name": "Local User"}
    assert set(token["scope"].split()) == set(ALL_SCOPES)
    client = hosted["client"]
    res = _gql(client, token["access_token"])
    assert res.status_code == 200 and res.json()["data"]["viewer"]["id"] == a["user_id"]
    assert res.json()["data"]["workspace"]["origin"].startswith("http")
    # One token for both endpoints: plugins reuse the CLI's token on /mcp.
    assert _mcp(client, token["access_token"]).status_code == 200


def test_cli_redirect_is_any_loopback_port_and_nothing_else(hosted):
    client = hosted["client"]
    assert _cli_authorize(client, redirect="http://127.0.0.1:61000/callback").status_code == 302
    assert _cli_authorize(client, redirect="http://[::1]:61000/callback").status_code == 302
    for bad in ("http://127.0.0.1/callback", "http://127.0.0.1:61000/other", "https://evil.test/callback",
                "http://localhost:61000/callback"):
        assert _cli_authorize(client, redirect=bad).status_code == 400, bad


def test_a_read_only_cli_grant_reads_but_cannot_write(hosted):
    token = _cli_login(hosted, hosted["a"], access="read")
    client = hosted["client"]
    assert _gql(client, token["access_token"]).status_code == 200
    res = _gql(client, token["access_token"], query='mutation { teamCreate(input: {name: "X", key: "XX"}) { success } }')
    assert "errors" in res.json()


def test_refresh_keeps_the_grant_resources_and_identity(hosted):
    token = _cli_login(hosted, hosted["a"])
    client = hosted["client"]
    fresh = _refresh(client, CLI, token["refresh_token"])
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["hoja_workspace"]["origin"] == f"https://{CANONICAL}"
    with hosted["engine"].connect() as conn:
        resource = conn.execute(select(db.oauth_access_token.c.resource).where(
            db.oauth_access_token.c.token_hash == db.token_hash(body["access_token"]))).scalar_one()
    assert resource == f"https://{CANONICAL}/graphql"
    assert _gql(client, body["access_token"]).status_code == 200
    assert _mcp(client, body["access_token"]).status_code == 200


def test_a_third_party_mcp_token_is_refused_on_graphql(hosted):
    client = hosted["client"]
    cid = _register(client)
    url = _authorize(client, cid)
    _page, res = _consent(client, url, _session(hosted["engine"], hosted["a"]), decision="allow", access="read")
    code = parse_qs(urlsplit(res.headers["location"]).query)["code"][0]
    token = client.post("/mcp/token", headers={"Host": CANONICAL}, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": cid, "code_verifier": VERIFIER}).json()["access_token"]
    assert _mcp(client, token).status_code == 200
    res = _gql(client, token)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_a_third_party_client_cannot_ask_for_the_graphql_resource(hosted):
    client = hosted["client"]
    cid = _register(client)
    res = client.get("/mcp/authorize", headers={"Host": CANONICAL}, follow_redirects=False, params={
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "code_challenge": CHALLENGE,
        "code_challenge_method": "S256", "state": "s", "resource": f"https://{CANONICAL}/graphql"})
    assert res.status_code == 302
    query = parse_qs(urlsplit(res.headers["location"]).query)
    assert query["error"] == ["invalid_target"]
    assert query["iss"] == [f"https://{CANONICAL}/mcp"]  # RFC 9207 on error redirects too


def test_a_cli_token_from_one_workspace_is_refused_on_another_host(hosted):
    token = _cli_login(hosted, hosted["a"])["access_token"]
    client = hosted["client"]
    for res, path in ((_gql(client, token, PINNED), "/graphql"), (_mcp(client, token, PINNED), "/mcp")):
        assert res.status_code == 403
        body = res.json()
        assert body["code"] == "wrong_workspace_host" and body["url"] == f"https://{CANONICAL}{path}"


def test_an_expired_or_revoked_oauth_token_gets_invalid_token(hosted):
    token = _cli_login(hosted, hosted["a"])["access_token"]
    client = hosted["client"]
    with hosted["engine"].begin() as conn:
        conn.execute(update(db.oauth_access_token).values(
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    for res in (_gql(client, token), _mcp(client, token)):
        assert res.status_code == 401
        assert 'error="invalid_token"' in res.headers["www-authenticate"]


# --------------------------------------------------------------------------- device flow


def _device_start(client, host=CANONICAL, **extra):
    res = client.post("/mcp/device", headers={"Host": host}, data={
        "client_id": CLI, "scope": f"{READ_SCOPE} {WRITE_SCOPE}", "resource": f"https://{host}/graphql",
        "device_name": "hoja-cli on build-box", **extra})
    assert res.status_code == 200, res.text
    return res.json()


def _device_poll(client, device_code, host=CANONICAL):
    return client.post("/mcp/token", headers={"Host": host}, data={
        "grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": CLI})


def _device_page(hosted, seeded, user_code, **form):
    client = hosted["client"]
    cookie = _session(hosted["engine"], seeded)
    page = client.get(f"/device?user_code={user_code}", headers={"Host": CANONICAL},
                      cookies={"hoja_session": cookie}, follow_redirects=False)
    if not form:
        return page, None
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    done = client.post(f"/device?user_code={user_code}", headers={"Host": CANONICAL},
                       cookies={"hoja_session": cookie}, data={"csrf_token": csrf, **form},
                       follow_redirects=False)
    return page, done


def test_device_flow_happy_path_with_pending_and_slow_down(hosted):
    client = hosted["client"]
    start = _device_start(client)
    assert re.fullmatch(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}", start["user_code"])
    assert start["verification_uri"] == f"https://{CANONICAL}/device"
    assert start["verification_uri_complete"] == f"https://{CANONICAL}/device?user_code={start['user_code']}"
    assert (start["expires_in"], start["interval"]) == (600, 5)
    first = _device_poll(client, start["device_code"])
    assert first.status_code == 400 and first.json()["error"] == "authorization_pending"
    fast = _device_poll(client, start["device_code"])
    assert fast.json()["error"] == "slow_down" and fast.json()["interval"] == 10
    page, done = _device_page(hosted, hosted["a"], start["user_code"].lower().replace("-", ""),
                              decision="allow", access="write")
    assert page.status_code == 200 and "Allow Hoja CLI" in page.text
    assert f"hoja-cli on build-box · code {start['user_code']}" in page.text
    assert done.status_code == 200 and "Device connected" in done.text
    with hosted["engine"].begin() as conn:
        conn.execute(update(db.oauth_device_code).values(last_polled_at=None))
    token = _device_poll(client, start["device_code"])
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["hoja_workspace"]["id"] == hosted["a"]["org_id"]
    assert set(body["scope"].split()) == set(ALL_SCOPES)
    assert _gql(client, body["access_token"]).status_code == 200
    again = _device_poll(client, start["device_code"])
    assert again.json()["error"] == "invalid_grant"


def test_device_flow_denied_and_expired(hosted):
    client = hosted["client"]
    denied = _device_start(client)
    _page, done = _device_page(hosted, hosted["a"], denied["user_code"], decision="deny")
    assert "Request denied" in done.text
    assert _device_poll(client, denied["device_code"]).json()["error"] == "access_denied"
    expired = _device_start(client)
    with hosted["engine"].begin() as conn:
        conn.execute(update(db.oauth_device_code).where(
            db.oauth_device_code.c.user_code == expired["user_code"]).values(
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    assert _device_poll(client, expired["device_code"]).json()["error"] == "expired_token"
    page, _ = _device_page(hosted, hosted["a"], expired["user_code"])
    assert page.status_code == 400 and "expired" in page.text


def test_device_codes_are_for_the_cli_only_and_the_page_needs_a_session(hosted):
    client = hosted["client"]
    res = client.post("/mcp/device", headers={"Host": CANONICAL}, data={"client_id": "someone-else"})
    assert res.status_code == 400 and res.json()["error"] == "unauthorized_client"
    start = _device_start(client)
    page = client.get(f"/device?user_code={start['user_code']}", headers={"Host": CANONICAL},
                      follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == f"/auth/login?next=%2Fdevice%3Fuser_code%3D{start['user_code']}"
    bearer = client.post(f"/device?user_code={start['user_code']}", headers={
        "Host": CANONICAL, "Authorization": "Bearer alpha-tok"}, data={"decision": "allow"})
    assert bearer.status_code == 401


# --------------------------------------------------------------------------- authorized apps


APPS = "{ myAuthorizedApps { id clientId clientName firstParty scopes deviceLabel lastUsedAt user { id } } }"


def test_authorized_apps_are_listed_and_revoking_kills_every_token(hosted):
    a, client = hosted["a"], hosted["client"]
    token = _cli_login(hosted, a)
    assert _gql(client, token["access_token"]).status_code == 200  # records last use
    apps = _gql(client, "alpha-tok", query=APPS).json()["data"]["myAuthorizedApps"]
    assert len(apps) == 1
    app = apps[0]
    assert (app["clientId"], app["clientName"], app["firstParty"]) == (CLI, "Hoja CLI", True)
    assert app["deviceLabel"] == "hoja-cli on laptop" and set(app["scopes"]) == set(ALL_SCOPES)
    assert app["lastUsedAt"] and app["user"]["id"] == a["user_id"]
    revoked = _gql(client, "alpha-tok", query="mutation($id:String!){ authorizedAppRevoke(id:$id) }",
                   variables={"id": app["id"]})
    assert revoked.json()["data"]["authorizedAppRevoke"] is True
    assert _gql(client, token["access_token"]).status_code == 401
    assert _refresh(client, CLI, token["refresh_token"]).status_code == 400
    assert _gql(client, "alpha-tok", query=APPS).json()["data"]["myAuthorizedApps"] == []


def test_revoking_a_refresh_token_revokes_the_grant(hosted):
    client = hosted["client"]
    token = _cli_login(hosted, hosted["a"])
    res = client.post("/mcp/revoke", headers={"Host": CANONICAL}, data={
        "token": token["refresh_token"], "client_id": CLI, "client_secret": ""})
    assert res.status_code == 200, res.text
    assert _gql(client, token["access_token"]).status_code == 401
    assert _gql(client, "alpha-tok", query=APPS).json()["data"]["myAuthorizedApps"] == []


def test_workspace_apps_are_admin_only_and_paginate(hosted):
    a, client, engine = hosted["a"], hosted["client"], hosted["engine"]
    _cli_login(hosted, a)
    query = "{ workspaceAuthorizedApps(first: 1) { nodes { id user { id } } pageInfo { hasNextPage } } }"
    data = _gql(client, "alpha-tok", query=query).json()["data"]["workspaceAuthorizedApps"]
    assert len(data["nodes"]) == 1 and data["nodes"][0]["user"]["id"] == a["user_id"]
    with engine.begin() as conn:
        conn.execute(update(db.user).where(db.user.c.id == a["user_id"]).values(admin=False))
    refused = _gql(client, "alpha-tok", query=query).json()
    assert refused["errors"][0]["extensions"]["code"] == "FORBIDDEN"


def test_sessions_are_listed_with_the_current_one_flagged_and_revocable(hosted):
    a, client, engine = hosted["a"], hosted["client"], hosted["engine"]
    other = _session(engine, a)
    mine = _session(engine, a)
    res = client.post("/graphql", json={"query": "{ mySessions { id current } }"},
                      headers={"Host": CANONICAL, "Origin": f"https://{CANONICAL}"},
                      cookies={"hoja_session": mine})
    sessions = res.json()["data"]["mySessions"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    with engine.connect() as conn:
        other_id = conn.execute(select(db.browser_session.c.id).where(
            db.browser_session.c.token_hash == db.token_hash(other))).scalar_one()
    gone = client.post("/graphql", json={"query": "mutation($id:String!){ sessionRevoke(id:$id) }",
                                         "variables": {"id": other_id}},
                       headers={"Host": CANONICAL, "Origin": f"https://{CANONICAL}"},
                       cookies={"hoja_session": mine})
    assert gone.json()["data"]["sessionRevoke"] is True
    assert client.post("/graphql", json={"query": VIEWER}, headers={"Host": CANONICAL},
                       cookies={"hoja_session": other}).status_code == 401


def test_first_party_policy_blocks_third_party_apps_at_consent(hosted):
    a, client = hosted["a"], hosted["client"]
    res = _gql(client, "alpha-tok", query='mutation { workspaceSecurityUpdate(input: {oauthClients: "first_party"}) { oauthClients } }')
    assert res.json()["data"]["workspaceSecurityUpdate"]["oauthClients"] == "first_party"
    url = _authorize(client, _register(client))
    _page, refused = _consent(client, url, _session(hosted["engine"], a), decision="allow", access="read")
    assert refused.status_code == 403 and "only Hoja" in refused.text
    assert _cli_login(hosted, a)["access_token"]  # Hoja's own CLI is always allowed
    bad = _gql(client, "alpha-tok", query='mutation { workspaceSecurityUpdate(input: {oauthClients: "some"}) { oauthClients } }')
    assert bad.json()["errors"][0]["extensions"]["code"] == "BAD_USER_INPUT"
