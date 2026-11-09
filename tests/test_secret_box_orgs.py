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


def _legacy(plaintext: str) -> str:
    key = hashlib.sha256(b"test-master-key").digest()
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


def test_legacy_master_key_tokens_still_open():
    token = _legacy("old-secret")
    assert secrets_box.is_legacy(token)
    assert decrypt_secret(token, "org-a") == "old-secret"


def test_an_organization_is_required():
    with pytest.raises(SecretBoxError):
        encrypt_secret("x", "")
    with pytest.raises(SecretBoxError):
        decrypt_secret(encrypt_secret("x", "org-a"), "")


def test_reseal_rewrites_legacy_tokens_under_the_organization_key(tmp_path):
    from sqlalchemy import insert, select

    from hoja_server.cli import main
    from hoja_server.db import make_engine, migrate, model_provider, new_id, now_iso, seed_tenant

    db = tmp_path / "reseal.db"
    engine = make_engine(db)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Reseal", org_url_key="reseal", user_email="o@reseal.test",
                        token="reseal-tok", demo_issues=False)
    now = now_iso()
    legacy_id, current_id = new_id(), new_id()
    current = encrypt_secret("new-key", admin["org_id"])
    with engine.begin() as conn:
        for pid, token in ((legacy_id, _legacy("old-key")), (current_id, current)):
            conn.execute(insert(model_provider).values(
                id=pid, organization_id=admin["org_id"], name=pid, provider="openai", model_id="m",
                base_url=None, encrypted_api_key=token, enabled=True, created_at=now, updated_at=now))

    main(["reseal-secrets", "--db", str(db)])

    with engine.connect() as conn:
        rows = dict(conn.execute(select(model_provider.c.id, model_provider.c.encrypted_api_key)).all())
    assert not secrets_box.is_legacy(rows[legacy_id])
    assert decrypt_secret(rows[legacy_id], admin["org_id"]) == "old-key"
    assert rows[current_id] == current
