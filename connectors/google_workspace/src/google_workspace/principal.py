"""Server-signed connector principal. The MCP server trusts only this, never a user id from tool arguments."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from google_workspace.errors import ConnectorAuthError, ConnectorConfigError

SECRET_ENV = "CLINIAR_CONNECTOR_SIGNING_KEY"
DEFAULT_TTL_SECONDS = 300


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    scope: str  # "read" or "write"
    expires_at: int


def _key(env_value: str | None) -> bytes:
    if not env_value:
        raise ConnectorConfigError(
            f"{SECRET_ENV} must be set to sign connector tokens.",
            hint="Set it to a long random value on the server.",
        )
    return hashlib.sha256(env_value.encode()).digest()


def sign_principal(
    user_id: str,
    org_id: str,
    scope: str,
    *,
    key: str | None,
    now: int | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> str:
    issued = int(time.time()) if now is None else now
    body = json.dumps(
        {"u": user_id, "o": org_id, "s": scope, "exp": issued + ttl},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    sig = hmac.new(_key(key), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def verify_principal(token: str, *, key: str | None, now: int | None = None) -> Principal:
    try:
        body_part, sig_part = token.split(".", 1)
        body = _unb64(body_part)
        sig = _unb64(sig_part)
    except (ValueError, TypeError) as exc:
        raise ConnectorAuthError("Malformed connector token.") from exc
    expected = hmac.new(_key(key), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ConnectorAuthError("Connector token signature is invalid.")
    claims = json.loads(body)
    current = int(time.time()) if now is None else now
    if claims["exp"] < current:
        raise ConnectorAuthError(
            "Connector token has expired.", hint="Ask the agent to retry the request."
        )
    return Principal(
        user_id=claims["u"], org_id=claims["o"], scope=claims["s"], expires_at=claims["exp"]
    )


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
