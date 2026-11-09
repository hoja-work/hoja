"""The SSRF guard: adversarial corpus, connect-time pinning and redirect refusal.

The resolver is stubbed so the suite is deterministic and offline; the corpus is the one from the
provisioning verifier (`run-38368f5b9c3f8d03b0e0/ssrf-validate.py`), extended with the query-string
case and the rebinding test the connect path needs.
"""

from __future__ import annotations

import httpx
import pytest
from grafana.client import GrafanaClient
from grafana.errors import ConnectorAPIError, UnsafeInstanceError
from grafana.ssrf import (
    CORPUS,
    address_is_public,
    pinned_target,
    validate_instance_url,
    validate_redirect,
)

PUBLIC = "93.184.216.34"
LOOPBACK = "127.0.0.1"
METADATA = "169.254.169.254"


def _resolver(host: str, port: int) -> list[str]:
    host = host.strip().lower().rstrip(".")
    literal = {
        "2130706433": LOOPBACK,
        "0x7f000001": LOOPBACK,
        "0177.0.0.1": LOOPBACK,
        "1.1.1.1": "1.1.1.1",
        "127.0.0.1": LOOPBACK,
        "0.0.0.0": "0.0.0.0",
        "255.255.255.255": "255.255.255.255",
        "224.0.0.1": "224.0.0.1",
        "169.254.169.254": METADATA,
        "10.0.0.5": "10.0.0.5",
        "192.168.1.10": "192.168.1.10",
        "172.16.0.1": "172.16.0.1",
        "::1": "::1",
        "fd00::1": "fd00::1",
        "fe80::1": "fe80::1",
        "::ffff:127.0.0.1": "::ffff:127.0.0.1",
        "::ffff:7f00:1": "::ffff:7f00:1",
        "localhost": LOOPBACK,
        "metadata.google.internal": METADATA,
        "127.0.0.1.nip.io": LOOPBACK,
        "snazzyocelot2741.grafana.net": PUBLIC,
        "grafana.example.com": PUBLIC,
        "example.com": PUBLIC,
    }
    return [literal.get(host, PUBLIC)]


@pytest.mark.parametrize(("url", "accepted"), CORPUS)
def test_adversarial_corpus(url: str, accepted: bool) -> None:
    if accepted:
        instance = validate_instance_url(url, resolver=_resolver)
        assert instance.host
    else:
        with pytest.raises(UnsafeInstanceError):
            validate_instance_url(url, resolver=_resolver)


def test_public_address_classification_unwraps_ipv4_mapped() -> None:
    assert address_is_public(PUBLIC)
    assert not address_is_public(LOOPBACK)
    assert not address_is_public(METADATA)
    assert not address_is_public("::ffff:127.0.0.1")
    assert not address_is_public("::ffff:7f00:1")


def test_the_validated_address_is_pinned_and_carried_as_sni_and_host() -> None:
    instance = validate_instance_url("https://grafana.example.com", resolver=_resolver)
    target, headers, extensions = pinned_target(instance, "/api/user")
    assert target == f"https://{PUBLIC}:443/api/user"
    assert headers["Host"] == "grafana.example.com"
    assert extensions["sni_hostname"] == "grafana.example.com"


def test_a_custom_port_is_kept_in_the_host_header() -> None:
    instance = validate_instance_url("https://grafana.example.com:8443", resolver=_resolver)
    target, headers, _ = pinned_target(instance, "/api/health")
    assert target == f"https://{PUBLIC}:8443/api/health"
    assert headers["Host"] == "grafana.example.com:8443"


class _Token:
    async def access_token(self) -> str:
        return "glsa_test"

    async def invalidate(self) -> None:
        return None


async def test_rebinding_is_closed_by_resolving_at_request_time(monkeypatch) -> None:
    """A second DNS answer cannot redirect the fetch: the client validates once per request and
    uses exactly the address it checked."""
    answers = iter([[PUBLIC], [LOOPBACK]])
    counter = {"n": 0}

    def rebinding_resolver(host: str, port: int) -> list[str]:
        counter["n"] += 1
        return next(answers)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"login": "sa-1-hoja-connector"})

    client = GrafanaClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        _Token(),
        "https://grafana.example.com",
        resolver=rebinding_resolver,
    )
    user = await client.get_current_user()
    assert user.login == "sa-1-hoja-connector"
    assert seen == [PUBLIC]

    # The next request resolves to loopback and is refused before any connection is made.
    with pytest.raises(UnsafeInstanceError):
        await client.get_current_user()
    assert seen == [PUBLIC]
    assert counter["n"] == 2


async def test_a_redirect_is_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    client = GrafanaClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        _Token(),
        "https://grafana.example.com",
        resolver=_resolver,
    )
    with pytest.raises(ConnectorAPIError) as caught:
        await client.get_current_user()
    assert "redirect" in str(caught.value)


def test_a_redirect_that_leaves_the_origin_is_rejected_by_the_hop_check() -> None:
    instance = validate_instance_url("https://grafana.example.com", resolver=_resolver)
    validate_redirect(instance, "https://grafana.example.com/login")
    with pytest.raises(UnsafeInstanceError):
        validate_redirect(instance, "https://evil.example/login")
