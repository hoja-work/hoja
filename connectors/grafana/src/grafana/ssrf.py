"""Customer-supplied Grafana base-URL validator and connect-time address pinning.

Policy (fail closed), ported from the provisioning verifier and extended with the connect path:

  * scheme must be https; no userinfo, query or fragment; the URL is an origin (path empty or "/");
  * the host is normalised (unicode dots, trailing dot) before it is resolved;
  * **every** resolved address must be globally routable — loopback, private, link-local,
    multicast, reserved and unspecified are refused, and an IPv4-mapped IPv6 address is unwrapped;
  * the validated address is **pinned** for the request: the client connects to that IP while
    sending the original Host header and TLS SNI, so a second DNS answer cannot be used
    (rebinding);
  * redirects are never followed — a 3xx is an error, and `validate_redirect` is available for a
    caller that ever needs to check a hop explicitly.

The residual risk is stated in the README: a public hostname can still be a reverse proxy in front
of an internal Grafana. That is the customer's own instance by definition; what must never happen is
Hoja fetching an arbitrary internal address, which this module prevents.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from grafana.errors import UnsafeInstanceError

Resolver = Callable[[str, int], list[str]]

_UNICODE_DOTS = ("\u3002", "\uff0e", "\uff61")


@dataclass(frozen=True)
class Instance:
    """A validated Grafana origin. `addresses` are the pinned, already-checked answers."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def normalise_host(host: str) -> str:
    host = host.strip()
    for odd in _UNICODE_DOTS:
        host = host.replace(odd, ".")
    if host.endswith("."):
        host = host[:-1]
    return host.lower()


def address_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeInstanceError(f"The instance host does not resolve: {exc}") from exc
    return sorted({info[4][0] for info in infos})


def validate_instance_url(url: str, *, resolver: Resolver | None = None) -> Instance:
    """Validate and resolve a customer URL, or raise `UnsafeInstanceError`."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeInstanceError("Enter the Grafana instance URL.")
    if any(char.isspace() for char in url):
        raise UnsafeInstanceError("The instance URL must not contain whitespace.")
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise UnsafeInstanceError(
            "The instance URL must start with https://",
            hint="Grafana Cloud and self-managed instances both serve HTTPS; plain HTTP is refused.",
        )
    if parts.username or parts.password:
        raise UnsafeInstanceError("The instance URL must not contain a username or password.")
    if parts.fragment:
        raise UnsafeInstanceError("The instance URL must not contain a fragment.")
    if parts.query:
        raise UnsafeInstanceError("The instance URL must not contain a query string.")
    if not parts.hostname:
        raise UnsafeInstanceError("The instance URL is missing a host.")
    if parts.path not in ("", "/"):
        raise UnsafeInstanceError(
            "Enter the instance origin only, for example https://example.grafana.net"
        )
    host = normalise_host(parts.hostname)
    port = parts.port or 443
    addresses = (resolver or _system_resolver)(host, port)
    if not addresses:
        raise UnsafeInstanceError("The instance host does not resolve.")
    blocked = [address for address in addresses if not address_is_public(address)]
    if blocked:
        raise UnsafeInstanceError(
            "The instance URL resolves to a private or reserved address and is refused.",
            hint="Use the public URL of the Grafana instance, not a loopback, private or metadata address.",
        )
    return Instance(
        url=f"https://{host}" if port == 443 else f"https://{host}:{port}",
        host=host,
        port=port,
        addresses=tuple(addresses),
    )


def _host_for_url(address: str) -> str:
    return f"[{address}]" if ":" in address else address


def pinned_target(instance: Instance, path: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """The URL to actually request, the headers, and httpx extensions.

    The connection goes to the checked IP; `Host` and TLS SNI carry the customer's hostname so the
    certificate is verified for that name and virtual hosting still works.
    """
    address = _host_for_url(instance.addresses[0])
    url = f"https://{address}:{instance.port}{path}"
    host_header = instance.host if instance.port == 443 else f"{instance.host}:{instance.port}"
    return url, {"Host": host_header}, {"sni_hostname": instance.host}


def validate_redirect(instance: Instance, location: str) -> None:
    """A redirect may only stay on the same origin. The client refuses to follow any."""
    parts = urlsplit(location)
    host = normalise_host(parts.hostname or "")
    port = parts.port or 443
    if parts.scheme != "https" or host != instance.host or port != instance.port:
        raise UnsafeInstanceError("The instance redirected to another origin; it was not followed.")


CORPUS: tuple[tuple[str, bool], ...] = (
    ("https://snazzyocelot2741.grafana.net", True),
    ("https://1.1.1.1", True),
    ("http://snazzyocelot2741.grafana.net", False),
    ("https://127.0.0.1", False),
    ("https://127.0.0.1:3000", False),
    ("https://localhost", False),
    ("https://169.254.169.254", False),
    ("https://169.254.169.254/latest/meta-data/", False),
    ("https://10.0.0.5", False),
    ("https://192.168.1.10", False),
    ("https://172.16.0.1", False),
    ("https://[::1]", False),
    ("https://[fd00::1]", False),
    ("https://[fe80::1]", False),
    ("https://[::ffff:127.0.0.1]", False),
    ("https://[::ffff:7f00:1]", False),
    ("https://0.0.0.0", False),
    ("https://255.255.255.255", False),
    ("https://224.0.0.1", False),
    ("https://2130706433", False),
    ("https://0x7f000001", False),
    ("https://0177.0.0.1", False),
    ("https://127.0.0.1.", False),
    ("https://127\u30020\u30020\u30021", False),
    ("https://user:pass@grafana.example.com", False),
    ("https://grafana.example.com@127.0.0.1", False),
    ("https://127.0.0.1.nip.io", False),
    ("https://metadata.google.internal", False),
    ("file:///etc/passwd", False),
    ("ftp://example.com", False),
    ("https://", False),
    ("", False),
    ("https://grafana.example.com/sub/path", False),
    ("https://grafana.example.com/?x=1", False),
)
