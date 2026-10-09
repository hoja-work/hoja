"""Encrypt provider credentials at rest with a server-held master key."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ENV = "HOJA_SECRET_KEY"
_NONCE_BYTES = 12


class SecretBoxError(RuntimeError):
    pass


def _master_key() -> bytes:
    raw = os.environ.get(KEY_ENV, "")
    if not raw:
        raise SecretBoxError(f"{KEY_ENV} must be set to store provider credentials.")
    return hashlib.sha256(raw.encode()).digest()


def encrypt_secret(plaintext: str) -> str:
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + sealed).decode()


def decrypt_secret(token: str) -> str:
    blob = base64.b64decode(token)
    nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        return AESGCM(_master_key()).decrypt(nonce, sealed, None).decode()
    except Exception as exc:
        raise SecretBoxError("Stored provider credential could not be decrypted.") from exc
