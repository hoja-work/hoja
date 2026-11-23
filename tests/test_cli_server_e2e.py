"""The real CLI OAuth client (hoja.oauth) against the real authorization server, in process.

The CLI's own tests use a fake authorization server and the server's tests drive
HTTP by hand; this is where the two halves have to agree, with no fake on either
side: discovery, the device grant, both resources with one token, refresh and
revocation.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import update

pytest.importorskip("mcp")

from hoja import oauth
from hoja_server import db
from tests import test_mcp_onboarding as onboarding
from tests.test_mcp_onboarding import CANONICAL, PINNED
import re

from tests.test_mcp_onboarding import _session

hosted = onboarding.hosted


def _approve_device(hosted, seeded, user_code, host):
    """The person, signed in to the workspace at ``host``, approves the code in a browser."""
    client, cookie = hosted["client"], _session(hosted["engine"], seeded)
    page = client.get(f"/device?user_code={user_code}", headers={"Host": host},
                      cookies={"hoja_session": cookie}, follow_redirects=False)
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post(f"/device?user_code={user_code}", headers={"Host": host},
                       cookies={"hoja_session": cookie},
                       data={"csrf_token": csrf, "decision": "allow", "access": "write"},
                       follow_redirects=False)


def _sign_in_with_device(hosted, origin, seeded):
    client = hosted["client"]
    meta = oauth.discover(origin, client)
    shown: list[str] = []

    def sleep(_seconds):
        # The person approves in a browser while the CLI waits; the poll timer is
        # cleared so the server's slow_down check measures this test, not wall time.
        if len(shown) == 1:
            code = next(line for line in shown if "-" in line).split()[-1]
            done = _approve_device(hosted, seeded, code, urlsplit(origin).hostname)
            assert done.status_code == 200, done.text
            shown.append("approved")
        with hosted["engine"].begin() as conn:
            conn.execute(update(db.oauth_device_code).values(last_polled_at=None))

    def echo(message: str):
        if not shown:
            codes = [w for w in message.split() if len(w) == 9 and w[4] == "-"]
            if codes:
                shown.append(f"code {codes[0]}")

    tokens = oauth.device_login(meta, client, scope="hoja:read hoja:write", echo=echo, sleep=sleep)
    return meta, tokens


def test_cli_device_login_then_graphql_mcp_refresh_and_revoke(hosted):
    client = hosted["client"]
    origin = f"https://{CANONICAL}"
    meta, tokens = _sign_in_with_device(hosted, origin, hosted["a"])

    assert meta.issuer == f"{origin}/mcp"
    assert tokens.workspace["origin"] == origin
    assert tokens.workspace["id"] == hosted["a"]["org_id"]

    # One token, both endpoints (contract amendment 1).
    auth = {"Authorization": f"Bearer {tokens.access_token}", "Host": CANONICAL}
    gql = client.post("/graphql", json={"query": "{ viewer { id } }"}, headers=auth)
    assert gql.status_code == 200 and gql.json()["data"]["viewer"]["id"], gql.text
    mcp = client.post("/mcp", headers={**auth, "Accept": "application/json, text/event-stream",
                                       "Content-Type": "application/json"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "e2e", "version": "0"}}})
    assert mcp.status_code == 200, mcp.text

    # Refresh keeps the workspace and the /graphql resource (amendment 4).
    fresh = oauth.refresh(meta.token_endpoint, tokens.refresh_token, origin=origin, http=client)
    assert fresh.access_token != tokens.access_token
    assert client.post("/graphql", json={"query": "{ viewer { id } }"},
                       headers={"Authorization": f"Bearer {fresh.access_token}", "Host": CANONICAL}
                       ).status_code == 200

    # Logout: the CLI's revocation must be accepted, and the token must then be dead (amendment 5).
    oauth.revoke(meta.revocation_endpoint, fresh.refresh_token, "refresh_token", http=client)
    dead = client.post("/graphql", json={"query": "{ viewer { id } }"},
                       headers={"Authorization": f"Bearer {fresh.access_token}", "Host": CANONICAL})
    assert dead.status_code == 401
    assert "invalid_token" in dead.headers.get("www-authenticate", "")


def test_cli_login_on_a_vanity_host_is_pinned_to_that_workspace(hosted):
    origin = f"https://{PINNED}"
    _meta, tokens = _sign_in_with_device(hosted, origin, hosted["b"])
    assert tokens.workspace["origin"] == origin
    # The same token on the canonical host names its own workspace's address.
    refused = hosted["client"].post("/graphql", json={"query": "{ viewer { id } }"},
                                    headers={"Authorization": f"Bearer {tokens.access_token}",
                                             "Host": CANONICAL})
    assert refused.status_code == 403
    assert refused.json()["code"] == "wrong_workspace_host"
    assert refused.json()["url"] == f"{origin}/graphql"


@pytest.mark.parametrize("path", ["/sync/bootstrap?closedDays=0", "/sync/delta?after=0&scope=all"])
def test_rest_endpoints_name_the_right_host_for_a_vanity_workspace_key(hosted, path):
    """The mobile app syncs over REST: a vanity workspace's key on the apex must say where to go,
    not claim the key is invalid."""
    res = hosted["client"].get(path, headers={"Authorization": "Bearer bravo-tok", "Host": CANONICAL})
    assert res.status_code == 403, res.text
    body = res.json()
    assert body["code"] == "wrong_workspace_host"
    assert urlsplit(body["url"]).netloc == PINNED
    unknown = hosted["client"].get(path, headers={"Authorization": "Bearer nope", "Host": CANONICAL})
    assert unknown.status_code == 401
