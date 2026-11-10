"""Rate-limit buckets must key on the address a trusted proxy actually saw.

HOJA-43: ``_client_ip`` took the *leftmost* ``x-forwarded-for`` entry whenever
the peer was a configured proxy. nginx's ``$proxy_add_x_forwarded_for`` appends,
so that entry is whatever the caller typed: rotating it per request meant the
per-IP budget was never reached. Only the login limiter exists in OSS, so the
invite-probe half of the cloud issue has no endpoint here.

These are behavioural tests: they drive the real handlers and read the status
codes, so they fail if the bucket key changes even when the helper still looks
right.
"""

from __future__ import annotations

import itertools

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate

PROXY = "10.1.2.3"          # the reverse proxy in front of the app
CF_EDGE = "172.64.0.5"      # a Cloudflare edge address
REAL_CLIENT = "203.0.113.9"
OTHER_CLIENT = "198.51.100.7"
LOGIN_LIMIT = 10
_EMAILS = itertools.count()


def _client(tmp_path, *, proxy: str | None = CF_EDGE, peer: str = "127.0.0.1", name="ip.db"):
    db = tmp_path / name
    migrate(make_engine(db))
    kwargs = {}
    if proxy:
        kwargs = {
            "trusted_proxy_cidrs": f"{proxy}/32,{PROXY}/32",
            "trusted_public_host": "hoja.example",
        }
    app = create_app(str(db), open_mode=False, **kwargs)
    return TestClient(app, base_url="http://hoja.example", client=(peer, 50000))


def _fail_login(client: TestClient, forwarded: str, email: str | None = None) -> int:
    """One failed sign-in.

    The limiter keys on ip *and* email, so every attempt here uses a fresh
    address: otherwise the shared email key would reach the budget on its own and
    the test could pass without the IP being bucketed correctly at all.
    """
    response = client.post(
        "/auth/login",
        json={"email": email or f"user{next(_EMAILS)}@example.test", "password": "wrong-password"},
        headers={"x-forwarded-for": forwarded},
    )
    return response.status_code


def test_a_rotating_forwarded_header_still_reaches_the_login_budget(tmp_path) -> None:
    """Behind a proxy, the spoofed leftmost entry must not become the bucket."""
    client = _client(tmp_path, proxy=CF_EDGE, peer=CF_EDGE)
    statuses = [
        _fail_login(client, f"10.0.0.{n}, {REAL_CLIENT}") for n in range(1, LOGIN_LIMIT + 1)
    ]
    assert statuses == [401] * LOGIN_LIMIT, statuses
    assert _fail_login(client, f"10.0.0.99, {REAL_CLIENT}") == 429


def test_a_different_client_is_not_blocked_by_another_clients_budget(tmp_path) -> None:
    """The bucket is the real address, so reusing someone's spoofed value is free."""
    client = _client(tmp_path, proxy=CF_EDGE, peer=CF_EDGE)
    for n in range(LOGIN_LIMIT):
        _fail_login(client, f"10.9.9.{n}, {REAL_CLIENT}")
    assert _fail_login(client, f"10.9.9.1, {REAL_CLIENT}") == 429
    # Same spoofed leftmost entry, different real address: not that client's budget.
    assert _fail_login(client, f"10.9.9.1, {OTHER_CLIENT}") == 401


def test_behind_cloudflare_the_edge_is_skipped_for_the_appended_client(tmp_path) -> None:
    """Cloudflare appends what it saw; the edge address itself is a configured hop."""
    client = _client(tmp_path, proxy=CF_EDGE, peer=CF_EDGE)
    statuses = [
        _fail_login(client, f"192.0.2.{n}, {REAL_CLIENT}") for n in range(1, LOGIN_LIMIT + 1)
    ]
    assert statuses == [401] * LOGIN_LIMIT, statuses
    assert _fail_login(client, f"192.0.2.200, {REAL_CLIENT}") == 429


def test_behind_no_proxy_the_headers_are_ignored_entirely(tmp_path) -> None:
    """A direct caller cannot choose its bucket by claiming to be someone else."""
    client = _client(tmp_path, proxy=None, peer=REAL_CLIENT, name="no-proxy.db")
    for n in range(LOGIN_LIMIT):
        assert _fail_login(client, f"10.0.0.{n}") == 401
    assert _fail_login(client, "10.0.0.250") == 429


def test_an_untrusted_peer_cannot_use_the_forwarded_header(tmp_path) -> None:
    """Trust is per-peer: configured proxies do not make every caller trustworthy."""
    client = _client(tmp_path, proxy=CF_EDGE, peer=OTHER_CLIENT, name="untrusted.db")
    for n in range(LOGIN_LIMIT):
        assert _fail_login(client, f"10.0.0.{n}") == 401
    assert _fail_login(client, "10.0.0.250") == 429


def test_the_forwarded_walk_ignores_an_unparsable_chain() -> None:
    """A junk hop must not become the bucket either; fall back to the peer."""
    from hoja_server.app import _forwarded_client_ip

    import ipaddress

    networks = [ipaddress.ip_network("10.1.2.3/32")]
    assert _forwarded_client_ip("not-an-ip, also-junk", PROXY, networks) == PROXY
    assert _forwarded_client_ip(None, PROXY, networks) == PROXY
    assert _forwarded_client_ip("203.0.113.9, 10.1.2.3", PROXY, networks) == REAL_CLIENT
