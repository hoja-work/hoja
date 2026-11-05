"""Hand a verified Google identity from the control plane to a tenant origin.

Google does not accept wildcard redirect URIs, so a per-tenant origin cannot
own its own OAuth client redirect. The control plane therefore completes the
code exchange exactly once, at the single registered redirect, and hands the
*already verified* claims to the tenant through a short-lived signed token.
The tenant redeems that token on its own host and mints its own browser
session; no Google secret is ever needed on a tenant.

The token is a bearer value, so it is treated like one: single audience, short
life, constant-time signature check, no algorithm field to negotiate (there is
exactly one construction, so `alg=none`-style confusion cannot be expressed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

#: Prefix keeps the format versionable and makes a stray value easy to reject.
PREFIX = "hjt1"

#: A transfer only has to outlive one browser redirect.
DEFAULT_TTL_SECONDS = 120

#: No key, no transfer. Anything shorter than this is not a secret.
MIN_KEY_BYTES = 32


class TransferError(Exception):
    """A transfer token was refused. ``reason`` is safe to log and return."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _check_key(key: str | bytes | None) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not key or len(key) < MIN_KEY_BYTES:
        raise TransferError("key_missing", "A transfer key of at least 32 bytes is required.")
    return key


def _signature(key: bytes, signing_input: bytes) -> bytes:
    return hmac.new(key, signing_input, hashlib.sha256).digest()


def issue(
    *,
    key: str | bytes | None,
    audience: str,
    email: str,
    subject: str,
    name: str | None = None,
    picture: str | None = None,
    email_verified: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint a transfer token for exactly one audience (a tenant hostname)."""
    signing_key = _check_key(key)
    if not audience or not email or not subject:
        raise TransferError("claims_missing", "audience, email and subject are required.")
    if not email_verified:
        # A tenant must never have to re-derive this: only verified addresses
        # are ever handed across, so an unverified one is a programming error.
        raise TransferError("email_unverified", "Only a verified address may be transferred.")
    moment = time.time() if now is None else now
    payload = {
        "aud": audience.strip().lower(),
        "email": email.strip().lower(),
        "sub": subject,
        "name": name,
        "picture": picture,
        "ev": True,
        "iat": int(moment),
        "exp": int(moment) + max(1, int(ttl_seconds)),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{PREFIX}.{body}".encode("ascii")
    return f"{PREFIX}.{body}.{_b64e(_signature(signing_key, signing_input))}"


def redeem(
    *,
    key: str | bytes | None,
    token: str | None,
    audience: str,
    now: float | None = None,
) -> dict:
    """Verify a transfer token for this origin and return its claims.

    Every failure raises :class:`TransferError`; the caller must not learn more
    than ``reason`` (a bad signature and a wrong audience both mean "no").
    """
    signing_key = _check_key(key)
    if not token or token.count(".") != 2:
        raise TransferError("malformed", "Token is not in the expected format.")
    prefix, body, signature = token.split(".")
    if prefix != PREFIX:
        raise TransferError("malformed", "Unknown token prefix.")
    signing_input = f"{prefix}.{body}".encode("ascii")
    try:
        given = _b64d(signature)
    except Exception as exc:  # noqa: BLE001 - any decode failure is a refusal
        raise TransferError("malformed", "Signature is not valid base64url.") from exc
    if not hmac.compare_digest(given, _signature(signing_key, signing_input)):
        raise TransferError("bad_signature", "Signature does not match.")
    try:
        claims = json.loads(_b64d(body))
    except Exception as exc:  # noqa: BLE001
        raise TransferError("malformed", "Payload is not valid JSON.") from exc
    if not isinstance(claims, dict):
        raise TransferError("malformed", "Payload is not an object.")
    moment = time.time() if now is None else now
    if int(claims.get("exp", 0)) < int(moment):
        raise TransferError("expired", "Token has expired.")
    if str(claims.get("aud", "")).lower() != audience.strip().lower():
        # A token minted for one tenant is useless on another.
        raise TransferError("wrong_audience", "Token was issued for another host.")
    if claims.get("ev") is not True:
        raise TransferError("email_unverified", "Address was never verified.")
    if not claims.get("email") or not claims.get("sub"):
        raise TransferError("claims_missing", "Token is missing email or subject.")
    return claims
