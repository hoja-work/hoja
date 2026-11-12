import base64
import hashlib
import secrets

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hoja_server import secrets_box
from hoja_server.secrets_box import SecretBoxError, decrypt_secret, encrypt_secret


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _legacy(plaintext: str, master: str = "test-master-key") -> str:
    """A pre-v2 ciphertext: master key, no associated data, openable in any organization."""
    key = hashlib.sha256(master.encode()).digest()
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), None)).decode()


def test_round_trip_within_one_organization():
    token = encrypt_secret("sk-live", "org-a")
    assert token.startswith("v2:")
    assert "sk-live" not in token
    assert decrypt_secret(token, "org-a") == "sk-live"


def test_another_organization_cannot_open_the_ciphertext():
    token = encrypt_secret("sk-live", "org-a")
    with pytest.raises(SecretBoxError):
        decrypt_secret(token, "org-b")


def test_organizations_get_different_keys():
    assert secrets_box._org_key("org-a") != secrets_box._org_key("org-b")


def test_tampered_ciphertext_is_rejected():
    token = encrypt_secret("sk-live", "org-a")
    blob = bytearray(base64.b64decode(token[3:]))
    blob[-1] ^= 1
    with pytest.raises(SecretBoxError):
        decrypt_secret("v2:" + base64.b64encode(bytes(blob)).decode(), "org-a")


def test_legacy_master_key_tokens_are_refused_in_every_organization():
    """F5: the same pre-v2 blob used to open under both organizations; now it opens under neither."""
    token = _legacy("old-secret")
    assert secrets_box.is_legacy(token)
    for org in ("org-a", "org-b"):
        with pytest.raises(SecretBoxError):
            decrypt_secret(token, org)
    # The blob really is organization-agnostic; only the explicit rewrite path may open it.
    assert decrypt_secret(token, "org-b", allow_legacy=True) == "old-secret"


def test_an_organization_is_required():
    with pytest.raises(SecretBoxError):
        encrypt_secret("x", "")
    with pytest.raises(SecretBoxError):
        decrypt_secret(encrypt_secret("x", "org-a"), "")


def _world(tmp_path, name="reseal", **overrides):
    """A migrated database with one tenant and a legacy blob in every sealed column."""
    from sqlalchemy import insert

    from hoja_server.db import (
        connector_account,
        extension,
        make_engine,
        migrate,
        model_provider,
        new_id,
        now_iso,
        seed_tenant,
        webhook,
    )

    db = tmp_path / f"{name}.db"
    engine = make_engine(db)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Reseal", org_url_key=f"reseal-{name}",
                        user_email="o@reseal.test", token="reseal-tok", demo_issues=False)
    org, now = admin["org_id"], now_iso()
    key = overrides.get("master", "test-master-key")
    planted: dict[str, str] = {}

    def plant(table, column, token, **values):
        row_id = new_id()
        with engine.begin() as conn:
            conn.execute(insert(table).values(
                id=row_id, organization_id=org, created_at=now, updated_at=now,
                **{column: token}, **values))
        planted[f"{table.name}.{column}"] = row_id

    plant(connector_account, "encrypted_refresh_token", _legacy("connector-refresh", key),
          provider="google", external_account="acct", scopes="[]", status="connected")
    plant(connector_account, "encrypted_access_token", _legacy("connector-access", key),
          provider="google", external_account="acct2", scopes="[]", status="connected",
          encrypted_refresh_token=_legacy("connector-refresh-2", key))
    plant(model_provider, "encrypted_api_key", _legacy("provider-key", key),
          name="legacy", provider="openai", model_id="m", base_url=None, enabled=True)
    plant(webhook, "secret_sealed", _legacy("hook-secret", key),
          url="https://example.test/h", resource_types="[]", all_public_teams=False, start_seq=0)
    plant(extension, "secret_sealed", _legacy("ext-secret", key),
          key="ext", name="Ext", version="1", url="https://example.test/e",
          access="read", manifest="{}")
    return db, engine, admin, planted


def _run_cli(argv):
    from hoja_server.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    return exit_info.value.code


def _sealed_values(engine, planted):
    """Read every planted row back as ``{"table.column": token}`` through the live schema."""
    from sqlalchemy import MetaData, Table, select

    values = {}
    with engine.connect() as conn:
        for key, row_id in planted.items():
            table_name, column_name = key.split(".")
            table = Table(table_name, MetaData(), autoload_with=conn)
            values[key] = conn.execute(select(table.c[column_name])
                                       .where(table.c.id == row_id)).scalar_one()
    return values


def test_the_inventory_covers_every_sealed_column_in_the_schema(tmp_path):
    """A credential column the rewrite does not know about survives as an unbound blob."""
    from sqlalchemy import inspect

    from hoja_server.db import make_engine, migrate

    engine = make_engine(tmp_path / "inventory.db")
    migrate(engine)
    inspector = inspect(engine)
    in_schema = {f"{table}.{column['name']}"
                 for table in inspector.get_table_names()
                 for column in inspector.get_columns(table)
                 if column["name"].endswith("_sealed") or column["name"].startswith("encrypted_")}
    declared = {f"{table}.{column}" for table, columns in secrets_box.SEALED_COLUMNS.items()
                for column in columns}
    assert declared == in_schema


def test_reseal_rewrites_every_sealed_column_and_is_idempotent(tmp_path):
    db, engine, admin, planted = _world(tmp_path)
    assert _run_cli(["reseal-secrets", "--db", str(db), "--check"]) == 1  # inventory before
    assert _run_cli(["reseal-secrets", "--db", str(db)]) == 0
    tokens = _sealed_values(engine, planted)
    for key, token in tokens.items():
        assert not secrets_box.is_legacy(token), key
    assert decrypt_secret(tokens["model_provider.encrypted_api_key"], admin["org_id"]) == "provider-key"
    assert decrypt_secret(tokens["connector_account.encrypted_access_token"], admin["org_id"]) \
        == "connector-access"
    assert decrypt_secret(tokens["webhook.secret_sealed"], admin["org_id"]) == "hook-secret"
    assert decrypt_secret(tokens["extension.secret_sealed"], admin["org_id"]) == "ext-secret"
    # And they are bound: another organization cannot open what is now stored there.
    with pytest.raises(SecretBoxError):
        decrypt_secret(tokens["model_provider.encrypted_api_key"], "org-other")
    assert _run_cli(["reseal-secrets", "--db", str(db), "--check"]) == 0
    assert _run_cli(["reseal-secrets", "--db", str(db)]) == 0  # nothing left to rewrite
    assert _sealed_values(engine, planted) == tokens


def test_reseal_refuses_a_blob_it_cannot_open_and_writes_nothing(tmp_path):
    """Blobs sealed with a key this server does not hold must stop the pass, not be skipped."""
    db, engine, _admin, planted = _world(tmp_path, name="unopenable", master="a-key-we-lost")
    before = _sealed_values(engine, planted)
    assert _run_cli(["reseal-secrets", "--db", str(db)]) == 1
    assert _sealed_values(engine, planted) == before  # the transaction rolled back
    assert all(secrets_box.is_legacy(token) for token in before.values())


def test_reseal_can_leave_unopenable_rows_explicitly(tmp_path, monkeypatch):
    """The operator escape hatch reports what it skipped instead of claiming success."""
    db, engine, _admin, planted = _world(tmp_path, name="skipped", master="a-key-we-lost")
    monkeypatch.setenv("HOJA_RESEAL_SKIP_UNOPENABLE", "1")
    assert _run_cli(["reseal-secrets", "--db", str(db)]) == 1
    assert all(secrets_box.is_legacy(token) for token in _sealed_values(engine, planted).values())


def test_the_reseal_revision_rewrites_legacy_rows_and_is_rerunnable(tmp_path, monkeypatch):
    """Revision 63 is the deploy-time path; a second run must be a no-op."""
    from hoja_server.db import applied_schema_revision, migrate, schema_revision

    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    _db, engine, admin, planted = _world(tmp_path, name="revision")
    with engine.begin() as conn:  # step the ledger back to 61: this is an upgrade, not a fresh DB
        conn.execute(schema_revision.delete().where(schema_revision.c.revision.in_((62, 63))))
    assert applied_schema_revision(engine) == 61
    migrate(engine)
    assert applied_schema_revision(engine) == 63
    tokens = _sealed_values(engine, planted)
    assert all(not secrets_box.is_legacy(token) for token in tokens.values())
    assert decrypt_secret(tokens["webhook.secret_sealed"], admin["org_id"]) == "hook-secret"
    snapshot = dict(tokens)
    with engine.begin() as conn:  # a second pass over already-sealed rows changes nothing
        conn.execute(schema_revision.delete().where(schema_revision.c.revision == 63))
    migrate(engine)
    assert _sealed_values(engine, planted) == snapshot
