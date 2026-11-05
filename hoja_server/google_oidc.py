"""Google OIDC: authorization-code flow with PKCE S256, and strict ID-token checks.

Everything here is deliberately boring and explicit. The two rules that matter:

* **Only RS256 is accepted.** ``alg: none`` and every HMAC algorithm are
  refused by name, before any key lookup, because those are the two ways a JWT
  validator is historically confused into trusting attacker-chosen bytes.
* **A token is valid only if every claim is right**: ``iss``, ``aud``, ``exp``,
  ``iat``, ``nonce`` and ``email_verified`` — the last one because the whole
  point of the identity layer is that the address was proven.

Network calls are two module-level seams (:func:`get_jwks` and
:func:`exchange_code`, both built on :func:`_http_post_form`) so tests can drive
the flow without a socket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

GOOGLE_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

#: Google mints both spellings in the wild; both name Google and nothing else.
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

#: The only signature algorithm this deployment will accept.
ALLOWED_ALGORITHMS = frozenset({"RS256"})

#: Claims Google sends; a token missing any of these is not one of ours.
REQUIRED_CLAIMS = ("iss", "aud", "sub", "exp", "iat")

DEFAULT_LEEWAY_SECONDS = 120
JWKS_TTL_SECONDS = 3600
SCOPE = "openid email profile"


class OIDCError(ValueError):
    """A token, exchange or state value was refused. ``reason`` is stable."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------------------- #
# base64url helpers
# --------------------------------------------------------------------------- #


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(segment: str) -> bytes:
    if not isinstance(segment, str) or not segment:
        raise OIDCError("malformed", "Empty base64url segment.")
    if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
           for ch in segment):
        raise OIDCError("malformed", "Segment is not base64url.")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, TypeError) as exc:
        raise OIDCError("malformed", "Segment is not valid base64url.") from exc


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OIDCError("malformed", f"{what} is not JSON.") from exc
    if not isinstance(value, dict):
        raise OIDCError("malformed", f"{what} is not a JSON object.")
    return value


# --------------------------------------------------------------------------- #
# PKCE and the authorization request
# --------------------------------------------------------------------------- #


def pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = secrets.token_urlsafe(48)  # 64 chars, inside RFC 7636's 43..128
    challenge = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def new_secret() -> str:
    """32 bytes of URL-safe randomness, for state, nonce and the flow binding."""
    return secrets.token_urlsafe(32)


def authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    hosted_domain: str | None = None,
    prompt: str | None = None,
    endpoint: str = GOOGLE_AUTHORIZE_ENDPOINT,
) -> str:
    """The 302 target. No token ever comes back in a URL fragment (code flow)."""
    if not code_challenge:
        raise OIDCError("pkce_missing", "PKCE is mandatory.")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if hosted_domain:
        params["hd"] = hosted_domain  # a hint for the chooser, never a check
    if prompt:
        params["prompt"] = prompt
    return f"{endpoint}?{urlencode(params)}"


# --------------------------------------------------------------------------- #
# JWKS
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Jwks:
    """Google's signing keys, keyed by ``kid``. Only RSA signature keys are kept."""

    keys: dict[str, Any]

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Jwks:
        from cryptography.hazmat.primitives.asymmetric import rsa

        raw_keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(raw_keys, list):
            raise OIDCError("jwks_malformed", "JWKS document has no 'keys' list.")
        keys: dict[str, Any] = {}
        for entry in raw_keys:
            if not isinstance(entry, dict):
                continue
            if entry.get("kty") != "RSA" or entry.get("use", "sig") != "sig":
                continue
            kid, modulus, exponent = entry.get("kid"), entry.get("n"), entry.get("e")
            if not (isinstance(kid, str) and isinstance(modulus, str) and isinstance(exponent, str)):
                continue
            try:
                numbers = rsa.RSAPublicNumbers(
                    int.from_bytes(b64url_decode(exponent), "big"),
                    int.from_bytes(b64url_decode(modulus), "big"),
                )
                keys[kid] = numbers.public_key()
            except (OIDCError, ValueError):
                continue
        if not keys:
            raise OIDCError("jwks_malformed", "JWKS document has no usable RSA key.")
        return cls(keys=keys)

    def verify(self, kid: str | None, signing_input: bytes, signature: bytes) -> None:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not kid:
            raise OIDCError("unknown_key", "ID token header has no 'kid'.")
        key = self.keys.get(kid)
        if key is None:
            raise OIDCError("unknown_key", f"No JWKS key for kid {kid!r}.")
        try:
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise OIDCError("bad_signature", "ID token signature does not verify.") from exc


_JWKS_CACHE: dict[str, tuple[float, Jwks]] = {}


def clear_jwks_cache() -> None:
    _JWKS_CACHE.clear()


def _http_get_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """The single outbound GET seam. Tests replace it; production uses httpx."""
    import httpx

    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    document = response.json()
    if not isinstance(document, dict):
        raise OIDCError("jwks_malformed", "JWKS response is not a JSON object.")
    return document


def get_jwks(uri: str = GOOGLE_JWKS_URI, *, ttl: int = JWKS_TTL_SECONDS) -> Jwks:
    """Fetch (and briefly cache) Google's keys. One GET per hour, not per login."""
    now = time.monotonic()
    cached = _JWKS_CACHE.get(uri)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]
    keys = Jwks.from_document(_http_get_json(uri))
    _JWKS_CACHE[uri] = (now, keys)
    return keys


# --------------------------------------------------------------------------- #
# ID token validation
# --------------------------------------------------------------------------- #


def _check_alg(header: dict[str, Any]) -> None:
    alg = header.get("alg")
    if not isinstance(alg, str) or not alg:
        raise OIDCError("alg_missing", "ID token header has no 'alg'.")
    if alg.lower() == "none":
        raise OIDCError("alg_none", "Unsigned tokens (alg=none) are refused.")
    if alg not in ALLOWED_ALGORITHMS:
        raise OIDCError("alg_not_allowed", f"Signing algorithm {alg!r} is not allowed.")


def verify_id_token(
    token: str,
    *,
    jwks: Jwks,
    client_id: str,
    nonce: str,
    now: float | None = None,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
) -> dict[str, Any]:
    """Return the validated claims, or raise :class:`OIDCError`.

    The order matters: header/alg first (no key material touched), signature
    second, claims third.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        raise OIDCError("malformed", "ID token is not a compact JWS with three parts.")
    header_b64, payload_b64, signature_b64 = token.split(".")
    header = _json_object(b64url_decode(header_b64), "ID token header")
    _check_alg(header)
    if header.get("typ") not in (None, "JWT"):
        raise OIDCError("malformed", "ID token header 'typ' is not JWT.")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    jwks.verify(header.get("kid"), signing_input, b64url_decode(signature_b64))

    claims = _json_object(b64url_decode(payload_b64), "ID token payload")
    for claim in REQUIRED_CLAIMS:
        if claim not in claims:
            raise OIDCError("claim_missing", f"ID token has no {claim!r} claim.")

    if claims["iss"] not in GOOGLE_ISSUERS:
        raise OIDCError("issuer_mismatch", f"Unexpected issuer {claims['iss']!r}.")

    audience = claims["aud"]
    if isinstance(audience, list):
        if client_id not in audience:
            raise OIDCError("audience_mismatch", "Token audience does not include this client.")
        if len(audience) > 1 and claims.get("azp") != client_id:
            raise OIDCError("audience_mismatch", "Multi-audience token without a matching azp.")
    elif audience != client_id:
        raise OIDCError("audience_mismatch", "Token audience is another client.")

    current = time.time() if now is None else float(now)
    try:
        expires = float(claims["exp"])
        issued = float(claims["iat"])
    except (TypeError, ValueError) as exc:
        raise OIDCError("malformed", "exp/iat are not numeric.") from exc
    if expires <= current - leeway:
        raise OIDCError("token_expired", "ID token has expired.")
    if issued > current + leeway:
        raise OIDCError("token_not_yet_valid", "ID token was issued in the future.")

    if not isinstance(nonce, str) or not nonce or claims.get("nonce") != nonce:
        raise OIDCError("nonce_mismatch", "ID token nonce does not match this flow.")

    if claims.get("email_verified") is not True:
        raise OIDCError("email_unverified", "The Google account's email is not verified.")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise OIDCError("subject_missing", "ID token has no usable 'sub'.")
    return claims


# --------------------------------------------------------------------------- #
# Code exchange
# --------------------------------------------------------------------------- #


def _http_post_form(url: str, data: dict[str, str], *, timeout: float = 10.0) -> dict[str, Any]:
    """The single outbound POST seam. Tests replace it."""
    import httpx

    response = httpx.post(url, data=data, timeout=timeout)
    try:
        document = response.json()
    except ValueError as exc:
        raise OIDCError("exchange_failed", "Token endpoint returned non-JSON.") from exc
    if not isinstance(document, dict):
        raise OIDCError("exchange_failed", "Token endpoint returned a non-object.")
    return document


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    endpoint: str = GOOGLE_TOKEN_ENDPOINT,
) -> dict[str, Any]:
    """Redeem the authorization code, bound to the PKCE verifier and redirect URI."""
    document = _http_post_form(
        endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    if document.get("error"):
        raise OIDCError("exchange_failed", str(document.get("error_description") or document["error"]))
    if not document.get("id_token"):
        raise OIDCError("exchange_failed", "Token response has no id_token.")
    return document
