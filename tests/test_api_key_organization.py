"""api_key.organization_id is a tenant column: backfilled from the owner, then mandatory (F8).

These exercise the *upgrade* path — a database whose ledger stops before the
constraint — because a fresh database gets NOT NULL from the table declaration and
never runs the backfill at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import (
    api_key,
    applied_schema_revision,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
    token_hash,
)
from tests import legacy_schema


@pytest.fixture
def upgraded(tmp_path):
    """A revision-61 database with one tenant and one admin."""
    engine = make_engine(tmp_path / "api-keys.db")
    legacy_schema.create_schema(engine, 61)
    admin = seed_tenant(engine, org_name="Keys", org_url_key="keys", user_email="a@keys.test",
                        token="keys-token", demo_issues=False)
    return engine, admin


def _plant_key(engine, *, user_id, organization_id, label="k"):
    key_id = new_id()
    with engine.begin() as conn:
        conn.execute(insert(api_key).values(
            id=key_id, token_hash=token_hash(f"{key_id}-tok"), label=label, access="read_write",
            user_id=user_id, organization_id=organization_id, created_at=now_iso()))
    return key_id


def _org_of(engine, key_id):
    with engine.connect() as conn:
        return conn.execute(select(api_key.c.organization_id).where(api_key.c.id == key_id)).scalar_one()


def _nullable(engine) -> bool:
    columns = {column["name"]: column for column in inspect(engine).get_columns("api_key")}
    return bool(columns["organization_id"]["nullable"])


def test_backfills_from_the_owner_and_then_makes_the_column_mandatory(upgraded):
    engine, admin = upgraded
    key_id = _plant_key(engine, user_id=admin["user_id"], organization_id=None)
    assert _org_of(engine, key_id) is None
    migrate(engine)
    assert applied_schema_revision(engine) == 63
    assert _org_of(engine, key_id) == admin["org_id"]
    assert _nullable(engine) is False
    # The key resolves again: the backfill took the same organization resolve_token joins on.
    with engine.connect() as conn:
        assert conn.execute(select(api_key.c.organization_id).where(
            api_key.c.id == key_id)).scalar_one() == admin["org_id"]


def test_a_writer_that_omits_the_organization_now_fails(upgraded):
    engine, admin = upgraded
    migrate(engine)
    with pytest.raises(IntegrityError):
        _plant_key(engine, user_id=admin["user_id"], organization_id=None)


def test_a_key_with_no_owner_blocks_the_revision_and_names_itself(upgraded, monkeypatch):
    """The revision must not invent an organization for a row it cannot attribute."""
    engine, _admin = upgraded
    monkeypatch.delenv("HOJA_MIGRATION_PRUNE_ORPHAN_API_KEYS", raising=False)
    orphan = _plant_key(engine, user_id=None, organization_id=None, label="orphan-key")
    with pytest.raises(RuntimeError) as failure:
        migrate(engine)
    assert orphan in str(failure.value)
    assert "orphan-key" in str(failure.value)
    assert applied_schema_revision(engine) == 61  # the revision rolled back, ledger untouched
    assert _nullable(engine) is True


def test_the_orphan_can_be_pruned_explicitly(upgraded, monkeypatch):
    engine, admin = upgraded
    monkeypatch.setenv("HOJA_MIGRATION_PRUNE_ORPHAN_API_KEYS", "1")
    orphan = _plant_key(engine, user_id=None, organization_id=None)
    kept = _plant_key(engine, user_id=admin["user_id"], organization_id=None)
    migrate(engine)
    with engine.connect() as conn:
        ids = set(conn.execute(select(api_key.c.id)).scalars())
    assert orphan not in ids and kept in ids
    assert _org_of(engine, kept) == admin["org_id"]
    assert _nullable(engine) is False


def test_the_rebuild_keeps_the_token_hash_index(upgraded):
    """Rebuilding the table on SQLite must not drop the uniqueness resolve_token relies on."""
    engine, admin = upgraded
    migrate(engine)
    names = {index["name"] for index in inspect(engine).get_indexes("api_key")}
    assert "uq_api_key_token_hash" in names
    shared = token_hash("same-token")
    with engine.begin() as conn:
        conn.execute(insert(api_key).values(
            id=new_id(), token_hash=shared, label="a", access="read_write",
            user_id=admin["user_id"], organization_id=admin["org_id"], created_at=now_iso()))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(api_key).values(
            id=new_id(), token_hash=shared, label="b", access="read_write",
            user_id=admin["user_id"], organization_id=admin["org_id"], created_at=now_iso()))
