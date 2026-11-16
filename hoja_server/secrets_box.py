"""Encrypt provider credentials at rest with a key derived per organization.

Every organization gets its own AES-256-GCM key, derived with HKDF from the
server-held master key and the organization id, and the organization id is also
bound as associated data. A ciphertext copied into another organization's row
therefore fails to decrypt, even inside one shared database.

Tokens written before per-organization keys carry no ``v2:`` prefix. They were
sealed with the master key and no associated data, so the same blob opens inside
*every* organization; that is the whole of finding F5. They are refused here
unless the caller is the reseal path itself (``allow_legacy=True``), which is
reached only by ``hoja-serve reseal-secrets``, the revision that re-seals them,
and the tenant merge. After those have run, no code path in the server can open
an unbound ciphertext at all.

``SEALED_COLUMNS`` is the single inventory of every column that holds a seal.
The reseal script, the reseal migration and the tests all read it, because a
column sealed in one place and forgotten in another is how a legacy blob
survives the rewrite.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_ENV = "HOJA_SECRET_KEY"
_NONCE_BYTES = 12
_V2 = "v2:"
_SALT = b"hoja-secret-box-v2"
_LEGACY_REFUSED = (
    "Stored credential predates per-organization sealing and cannot be opened in this "
    "organization; run `hoja-serve reseal-secrets` (or re-enter the credential)."
)

# table -> sealed columns. Any credential a row carries must be listed here, or the
# rewrite will leave it bound to nothing while reporting success.
SEALED_COLUMNS: dict[str, tuple[str, ...]] = {
    "connector_account": ("encrypted_refresh_token", "encrypted_access_token"),
    "model_provider": ("encrypted_api_key",),
    "webhook": ("secret_sealed",),
    "extension": ("secret_sealed",),
    # HOJA-602-A-14 / WP12: a named secret an agent policy may grant. Listed here
    # or the reseal pass would report success while this column stayed sealed to
    # nothing. It is a revision-69 table, so no stored row can be legacy.
    "agent_secret": ("secret_sealed",),
}


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


def decrypt_secret(token: str, org_id: str, *, secret: str | None = None,
                   allow_legacy: bool = False) -> str:
    """Open a token sealed for ``org_id``; ``secret`` overrides the server's HOJA_SECRET_KEY.

    ``allow_legacy`` is for the rewrite paths only (reseal script, reseal revision,
    tenant merge). Every request-serving caller leaves it false, so an unbound
    ciphertext is a hard error there instead of an organization-agnostic open.
    """
    if not org_id:
        raise SecretBoxError("An organization is required to seal or open a credential.")
    legacy = not token.startswith(_V2)
    if legacy and not allow_legacy:
        raise SecretBoxError(_LEGACY_REFUSED)
    if legacy:
        key, aad, body = _master_key(secret), None, token
    else:
        key, aad, body = _org_key(org_id, secret), org_id.encode(), token[len(_V2):]
    try:
        blob = base64.b64decode(body)
        nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, sealed, aad).decode()
    except Exception as exc:
        raise SecretBoxError("Stored provider credential could not be decrypted.") from exc


def is_legacy(token: str) -> bool:
    return not token.startswith(_V2)


@dataclass
class ResealReport:
    """What a rewrite pass saw, per sealed column."""

    resealed: dict[str, int] = field(default_factory=dict)
    unopenable: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.resealed.values())


def _sealed_tables(conn):
    """Reflect the sealed tables that exist here, so an older database is tolerated."""
    from sqlalchemy import MetaData, Table, inspect

    found = []
    present = set(inspect(conn).get_table_names())
    for table_name, columns in SEALED_COLUMNS.items():
        if table_name in present:
            found.append((Table(table_name, MetaData(), autoload_with=conn), columns))
    return found


def count_legacy(conn) -> dict[str, int]:
    """How many unbound ciphertexts each sealed column still holds; no key required."""
    from sqlalchemy import select

    counts: dict[str, int] = {}
    for table, columns in _sealed_tables(conn):
        for name in columns:
            column = table.c[name]
            rows = conn.execute(select(column).where(column.is_not(None))).scalars()
            counts[f"{table.name}.{name}"] = sum(1 for token in rows if is_legacy(token))
    return counts


def reseal_legacy(conn, *, dry_run: bool = False, allow_unopenable: bool = False) -> ResealReport:
    """Rewrite every unbound ciphertext under its own organization's key.

    Idempotent: an already-sealed token is skipped, so a second pass reports zero.
    Rows that cannot be opened (a different master key, a corrupt blob) stop the
    pass unless ``allow_unopenable`` is set, because a rewrite that silently skips
    them reports success while the blob is still openable in every tenant.
    """
    from sqlalchemy import select, update

    report = ResealReport()
    for table, columns in _sealed_tables(conn):
        null_org = conn.execute(select(table.c.id, table.c.organization_id).where(
            table.c.organization_id.is_(None)).limit(1)).first()
        if null_org:
            raise SecretBoxError(
                f"{table.name} row {null_org[0]} has no organization, so its credential "
                "cannot be bound; assign the row to an organization before resealing."
            )
        for name in columns:
            column = table.c[name]
            rows = conn.execute(select(table.c.id, table.c.organization_id, column)
                                .where(column.is_not(None))).all()
            for row_id, org_id, token in rows:
                if not is_legacy(token):
                    continue
                try:
                    plain = decrypt_secret(token, org_id, allow_legacy=True)
                except SecretBoxError as exc:
                    if not allow_unopenable:
                        raise SecretBoxError(
                            f"{table.name}.{name} row {row_id} (org {org_id}) predates "
                            "per-organization sealing and does not open with this "
                            f"{KEY_ENV}; re-enter it, or set "
                            "HOJA_RESEAL_SKIP_UNOPENABLE=1 to leave it sealed to nothing."
                        ) from exc
                    report.unopenable.append((table.name, row_id, str(exc)))
                    continue
                if not dry_run:
                    conn.execute(update(table).where(table.c.id == row_id)
                                 .values({name: encrypt_secret(plain, org_id)}))
                report.resealed[f"{table.name}.{name}"] = report.resealed.get(f"{table.name}.{name}", 0) + 1
    return report
