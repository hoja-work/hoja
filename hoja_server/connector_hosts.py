"""Which host a connector may use, and how a grant gets back to it (ADR-003 shared tenancy).

One app now serves every organization, so a connector's OAuth redirect can no
longer be one value read from the environment. It is derived per request from
the host the request actually arrived on, and only after that host has been
resolved to a real workspace:

* the canonical control-plane host (``HOJA_TRUSTED_PUBLIC_HOST``), or
* a workspace's own host, i.e. a ``organization.vanity_host`` row.

A raw ``Host`` header is never trusted on its own. A host that is not in the
database (or the canonical name) resolves to nothing, and a provider whose
callback is not registered on that host reports "not available on this
workspace host" instead of sending the browser to a consent screen that will
fail with ``redirect_uri_mismatch``.

Two shapes of provider:

* **per-tenant** (Google Workspace, Notion, Slack, Zoom): each workspace host
  is registered on the provider app, so the callback is
  ``https://<host>/api/connectors/<provider>/callback``. Registration is a
  provider-side fact this process cannot discover, so the hosts that have one
  are stated in ``HOJA_CONNECTOR_REDIRECT_HOSTS``.
* **broker** (Calendly, Sentry): the provider app accepts exactly one redirect
  URI. It points at ``HOJA_CONNECTOR_BROKER_URL`` (``https://api.hoja.work``),
  and the broker forwards to the workspace that started the flow. The broker
  host carries no session cookie, so the state carries the workspace in a
  readable prefix and the sealed payload stays bound to the organization's key.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.engine import Engine

from hoja_server import tenant_context
from hoja_server.db import organization
from hoja_server.identity_transfer import tenant_target

BROKER_URL_ENV = "HOJA_CONNECTOR_BROKER_URL"
REDIRECT_HOSTS_ENV = "HOJA_CONNECTOR_REDIRECT_HOSTS"
#: Set by the compose file; without it the broker is not routable and broker providers are off.
BROKER_HOST_ENV = "HOJA_CONNECTOR_BROKER_HOST"

#: The path is fixed by the provider app; only the origin varies.
CALLBACK_PATHS = {
    "google_workspace": "/api/connectors/google/callback",
    "notion": "/api/connectors/notion/callback",
    "slack": "/api/connectors/slack/callback",
    "zoom": "/api/connectors/zoom/callback",
    "calendly": "/api/connectors/calendly/callback",
    "sentry": "/api/connectors/sentry/callback",
}
PER_TENANT = frozenset({"google_workspace", "notion", "slack", "zoom"})
BROKER = frozenset({"calendly", "sentry"})
FINISH_PATHS = {
    "calendly": "/api/connectors/calendly/finish",
    "sentry": "/api/connectors/sentry/finish",
}

UNAVAILABLE = "not available on this workspace host"
#: Bumped if the state envelope ever changes shape.
STATE_PREFIX = "hcs1"


def bare(host: str | None) -> str:
    return (host or "").strip().lower().split(":")[0]


def request_host(request) -> str:
    """The bare host the request claims. Callers must still resolve it."""
    return bare(request.headers.get("host"))


def canonical_host() -> str:
    return bare(os.environ.get("HOJA_TRUSTED_PUBLIC_HOST", ""))


def broker_origin() -> str:
    return (os.environ.get(BROKER_URL_ENV) or "").strip().rstrip("/")


def broker_host() -> str:
    return bare(os.environ.get(BROKER_HOST_ENV)) or bare(broker_origin())


def registered_redirect_hosts() -> frozenset[str]:
    """Hosts whose per-tenant callbacks are registered on the provider apps."""
    raw = os.environ.get(REDIRECT_HOSTS_ENV, "")
    return frozenset(bare(part) for part in raw.replace(",", " ").split() if bare(part))


def tenant_apex() -> str:
    """The suffix workspace hosts live under, from config rather than from an assumption."""
    configured = bare(os.environ.get("HOJA_TENANT_BASE_DOMAIN", ""))
    if configured:
        return configured
    canonical = canonical_host()
    return ".".join(canonical.split(".")[1:]) if canonical.count(".") >= 2 else ""


def org_for_host(engine: Engine, host: str) -> str | None:
    """The organization that owns ``host``, from the registry.

    Two facts identify a workspace host: ``organization.vanity_host`` (the row
    ADR-003 pins the host to) and the ``<url_key>.<apex>`` form the provisioner
    derives. Both are read from the database; nothing is inferred from the header
    alone. The canonical host is shared, so it resolves to no single organization
    and callers that need one take it from the authenticated session.
    """
    name = bare(host)
    if not name or name == canonical_host():
        return None
    # An enumerated cross-tenant read: a Host arrives before any tenant is known, so
    # this is the same case TenantHosts.hosts() elevates for. Under row-level security
    # it must run in the system context or it would see no organization at all.
    with tenant_context.system(), engine.connect() as conn:
        found = conn.execute(
            select(organization.c.id).where(organization.c.vanity_host == name)
        ).scalar()
        if found:
            return found
        apex = tenant_apex()
        if apex and name.endswith("." + apex):
            key = name[: -(len(apex) + 1)]
            if key and "." not in key:
                return conn.execute(
                    select(organization.c.id).where(organization.c.url_key == key)
                ).scalar()
    return None


def is_canonical(host: str) -> bool:
    return bool(canonical_host()) and bare(host) == canonical_host()


def workspace_origin(engine: Engine, request) -> str | None:
    """``https://<host>`` for the request's own host, when that host is really ours.

    A host resolves if it is the canonical control-plane name or a workspace host
    that exists in the database. A one-host install resolves its own address. Anything
    else returns None: the caller refuses rather than invent a redirect from an
    unvalidated header.
    """
    host = request_host(request)
    if not host:
        return None
    if workspace_origin_from_host(engine, host) is None:
        # Not an address this deployment serves. Never invented from a header: the
        # only relaxation is `callback_url`'s single-host fallback, and that only
        # decides which registered URI to *ask* for, not where a grant is returned.
        return None
    return f"https://{host}"


#: The single redirect URI a one-host install registers with each provider app. It is
#: only read when the deployment declares no canonical host (``single_host``), because
#: that is the install shape where "the deployment's own callback" is a meaningful thing
#: to have: there is exactly one host, so there is nothing to derive per request.
SINGLE_HOST_REDIRECT_ENV = {
    "google_workspace": "HOJA_GOOGLE_REDIRECT_URI",
    "notion": "HOJA_NOTION_REDIRECT_URI",
    "slack": "HOJA_SLACK_REDIRECT_URI",
    "zoom": "HOJA_ZOOM_REDIRECT_URI",
    "calendly": "HOJA_CALENDLY_REDIRECT_URI",
    "sentry": "HOJA_SENTRY_REDIRECT_URI",
}


def single_host() -> bool:
    """True for an install that has not declared a canonical host.

    With no ``HOJA_TRUSTED_PUBLIC_HOST`` there is one address serving everything, so
    the redirect is the one the provider env names. A shared-tenancy deployment sets
    that variable and every redirect is derived from the request's own validated host.
    """
    return not canonical_host()


def _single_host_redirect(provider: str) -> str | None:
    value = (os.environ.get(SINGLE_HOST_REDIRECT_ENV.get(provider, ""), "") or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc:
        return None
    return value


def callback_url(engine: Engine, request, provider: str) -> str | None:
    """The redirect URI to send to ``provider`` for *this* request, or None if unavailable."""
    host = request_host(request)
    if not host:
        return None
    if provider in BROKER:
        origin = broker_origin()
        if not origin:
            # No broker on this deployment: the install registers its own callback,
            # and every workspace host carries it (see `_single_host_redirect`).
            return _single_host_redirect(provider)
        return f"{origin}{CALLBACK_PATHS[provider]}"
    if provider not in CALLBACK_PATHS:
        return None
    if workspace_origin_from_host(engine, host) is None:
        # Not an address this deployment serves. A one-host install has no way to tell
        # hosts apart, so it answers with the callback it registered; a shared one refuses.
        return _single_host_redirect(provider) if single_host() else None
    registered = registered_redirect_hosts()
    if registered and host not in registered:
        return None
    return f"https://{host}{CALLBACK_PATHS[provider]}"


def unavailable_detail(provider: str, host: str, engine: Engine | None = None) -> str:
    """A sentence the Settings page can show instead of failing at the provider."""
    name = bare(host) or "this address"
    if provider in BROKER:
        if not broker_origin():
            return f"{UNAVAILABLE}: {provider} needs HOJA_CONNECTOR_BROKER_URL on this deployment."
        return f"{UNAVAILABLE}: {provider} uses the shared broker at {broker_origin()}."
    registered = sorted(registered_redirect_hosts())
    if engine is not None and workspace_origin_from_host(engine, name) is None:
        return f"{UNAVAILABLE}: {name} is not a workspace this deployment serves."
    return (
        f"{UNAVAILABLE}: {provider} has no callback registered for {name}. "
        + (f"Registered workspaces: {', '.join(registered)}." if registered else "")
    ).strip()


def workspace_origin_from_host(engine: Engine, host: str) -> str | None:
    if not host:
        return None
    if is_canonical(host) or org_for_host(engine, host):
        return f"https://{host}"
    return None


def tenant_origin(engine: Engine, value: str | None) -> tuple[str, str] | None:
    """Validate a claimed workspace origin; return ``(origin, organization_id)`` or None.

    Used by the broker, which has no session: the claimed host must be a real
    workspace host in this deployment, and the organization it resolves to is the
    only key that can open the sealed state.
    """
    target = tenant_target(value)
    if target is None:
        return None
    parts = urlsplit(target)
    host = bare(parts.hostname)
    org = org_for_host(engine, host)
    if not org:
        # No registry row, no key to open the state with: refuse rather than guess.
        return None
    return f"https://{host}", org


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def wrap_state(sealed: str, hint: str | None) -> str:
    """``hcs1.<b64url(hint)>.<sealed>`` when a host must survive a provider that has no session.

    The hint is the workspace origin, which is public (it is the address the
    person is using). It is not trusted: it only selects the key to try, and the
    sealed payload must still authenticate under that organization's AAD, so a
    rewritten hint turns into a decryption failure rather than a redirect.
    """
    if not hint:
        return sealed
    return f"{STATE_PREFIX}.{_b64e(hint.encode())}.{sealed}"


def unwrap_state(state: str) -> tuple[str, str | None]:
    """Return ``(sealed, hint)``; pairs with :func:`wrap_state`."""
    if not state.startswith(STATE_PREFIX + "."):
        return state, None
    _, hint, sealed = state.split(".", 2)
    try:
        return sealed, _b64d(hint).decode()
    except Exception:
        return "", None
