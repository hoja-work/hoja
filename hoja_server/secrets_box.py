"""Encrypt provider credentials at rest with a key derived per organization.

Every organization gets its own AES-256-GCM key, derived with HKDF from the
server-held master key and the organization id, and the organization id is also
bound as associated data. A ciphertext copied into another organization's row
therefore fails to decrypt, even inside one shared database. Tokens written
before per-organization keys (no ``v2:`` prefix) still decrypt with the master
key until ``hoja-serve reseal-secrets`` rewrites them.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_ENV = "HOJA_SECRET_KEY"
_NONCE_BYTES = 12
_V2 = "v2:"
_SALT = b"hoja-secret-box-v2"


class SecretBoxError(RuntimeError):
    pass


def _master_key(secret: str | None = None) -> bytes:
    raw = os.environ.get(KEY_ENV, "") if secret is None else secret
    if not raw:
        raise SecretBoxError(f"{KEY_ENV} must be set to store provider credentials.")
    return hashlib.sha256(raw.encode()).digest()


def _org_key(org_id: str, secret: str | None = None) -> bytes:
    if not org_id:
        raise SecretBoxError("An organization is required to seal or open a credential.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=org_id.encode()).derive(_master_key(secret))


def encrypt_secret(plaintext: str, org_id: str, *, secret: str | None = None) -> str:
    """Seal under ``org_id``'s key; ``secret`` overrides the server's HOJA_SECRET_KEY."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_org_key(org_id, secret)).encrypt(nonce, plaintext.encode(), org_id.encode())
    return _V2 + base64.b64encode(nonce + sealed).decode()


def decrypt_secret(token: str, org_id: str, *, secret: str | None = None) -> str:
    """Open a token sealed for ``org_id``; ``secret`` overrides the server's HOJA_SECRET_KEY."""
    key = _org_key(org_id, secret)
    try:
        if token.startswith(_V2):
            blob, aad = base64.b64decode(token[len(_V2):]), org_id.encode()
        else:
            blob, aad, key = base64.b64decode(token), None, _master_key(secret)
        nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, sealed, aad).decode()
    except Exception as exc:
        raise SecretBoxError("Stored provider credential could not be decrypted.") from exc


def is_legacy(token: str) -> bool:
    return not token.startswith(_V2)
