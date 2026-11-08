"""Tell the control plane that this workspace now knows an address.

The control plane cannot read a tenant's membership rows, so it learned about
invited people only by their absence from its own sign-up directory. This is
the tenant's half of the fix: after issuing an invitation it says so, signed
with the key derived for its own hostname.

Best effort by design. An invitation must not fail because the control plane is
unreachable, and the tenant remains the source of truth — the announcement is a
hint the control plane may act on, never an authority it can lean on.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

log = logging.getLogger(__name__)


def signature(tenant_key: str, body: bytes) -> str:
    """The one signature shape both ends must agree on."""
    return hmac.new(tenant_key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def announce(
    *,
    tenant_key: str | None,
    control_plane_url: str | None,
    slug: str,
    email: str,
    role: str = "member",
    expires_at: str | None = None,
    timeout: float = 3.0,
) -> bool:
    """Announce one membership. Returns False rather than raising, always."""
    address = (email or "").strip().lower()
    if not tenant_key or not control_plane_url or not slug or "@" not in address:
        return False
    body = json.dumps({
        "slug": slug, "email": address, "role": role, "expires_at": expires_at,
    }).encode("utf-8")
    try:
        import httpx

        response = httpx.post(
            control_plane_url.rstrip("/") + "/internal/membership",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hoja-signature": signature(tenant_key, body),
            },
            timeout=timeout,
        )
        return response.status_code == 200
    except Exception:  # noqa: BLE001 - a failed announcement is not an error the caller can act on
        log.warning("membership.announce_failed slug=%s", slug, exc_info=True)
        return False
