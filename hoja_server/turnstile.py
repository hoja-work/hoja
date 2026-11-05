"""Server-side Turnstile verification — the *human* layer, separate from identity.

Two things this module refuses to do:

* trust a client-side success flag (the token is always presented to
  ``siteverify`` from the server, with the secret and the client's address), and
* accept a token whose ``hostname`` is not the one we expect.

The siteverify API has no hostname *input* parameter, so "expected hostname" is
enforced on the response: Cloudflare echoes the hostname the widget was served
from, and a token minted for another site is refused here. Single use is
enforced in the database (``turnstile_use``) *and* by Cloudflare itself, which
answers ``timeout-or-duplicate`` for a token it has already seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


@dataclass(frozen=True)
class TurnstileResult:
    """Outcome of one siteverify call. ``reason`` is stable for logging/tests."""

    success: bool
    reason: str
    hostname: str | None = None
    error_codes: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _http_post_form(url: str, data: dict[str, str], *, timeout: float) -> dict[str, Any]:
    """The single outbound POST seam. Tests replace it; nothing else may call out."""
    import httpx

    response = httpx.post(url, data=data, timeout=timeout)
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError("siteverify returned a non-object")
    return document


def verify_turnstile(
    *,
    secret: str,
    token: str,
    remote_ip: str | None,
    expected_hostname: str,
    expected_action: str | None = None,
    timeout: float = 8.0,
) -> TurnstileResult:
    """Verify one token with Cloudflare. Never raises on a refusal."""
    if not secret:
        return TurnstileResult(False, "not_configured")
    if not token or not isinstance(token, str):
        return TurnstileResult(False, "missing_token")

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        document = _http_post_form(SITEVERIFY_URL, payload, timeout=timeout)
    except Exception as exc:  # network, TLS, JSON: fail closed, never open
        return TurnstileResult(False, "network_error", raw={"error": str(exc)})

    raw_codes = document.get("error-codes") or []
    codes = tuple(str(code) for code in raw_codes) if isinstance(raw_codes, list) else ()
    hostname = document.get("hostname")
    hostname = hostname.lower() if isinstance(hostname, str) else None

    if document.get("success") is not True:
        return TurnstileResult(False, "verification_failed", hostname, codes, document)
    if hostname != (expected_hostname or "").lower():
        return TurnstileResult(False, "hostname_mismatch", hostname, codes, document)
    action = document.get("action")
    if expected_action and action != expected_action:
        return TurnstileResult(False, "action_mismatch", hostname, codes, document)
    return TurnstileResult(True, "ok", hostname, codes, document)
