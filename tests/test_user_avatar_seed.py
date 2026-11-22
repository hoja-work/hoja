"""user.avatar_seed: every user has a random blobatar seed, stored once (revision 85).

The seed is random, not derived from the name, so a rename keeps the face and two
users with the same name still get different ones.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import insert, select, text

from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    _apply_user_avatar_seed,
    make_engine,
    migrate,
    new_avatar_seed,
    new_id,
    now_iso,
    seed_tenant,
    user,
)

SEED = re.compile(r"^[0-9a-f]{16}$")


def test_new_seeds_are_random_hex_and_not_the_name():
    seeds = {new_avatar_seed() for _ in range(200)}
    assert len(seeds) == 200
    assert all(SEED.match(seed) for seed in seeds)


def test_revision_85_is_the_current_revision():
    assert CURRENT_SCHEMA_REVISION == 85


def test_backfill_gives_each_existing_user_a_distinct_seed_and_keeps_it(tmp_path):
    engine = make_engine(tmp_path / "legacy-user.db")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id VARCHAR PRIMARY KEY, name VARCHAR)')
        conn.exec_driver_sql("INSERT INTO \"user\" (id, name) VALUES ('u1', 'Same'), ('u2', 'Same'), ('u3', 'Other')")
        _apply_user_avatar_seed(conn)

    with engine.connect() as conn:
        first = dict(conn.execute(text('SELECT id, avatar_seed FROM "user"')).all())
    assert set(first) == {"u1", "u2", "u3"}
    assert all(SEED.match(seed) for seed in first.values())
    assert len(set(first.values())) == 3

    # A replay must not reroll anybody's face.
    with engine.begin() as conn:
        _apply_user_avatar_seed(conn)
    with engine.connect() as conn:
        again = dict(conn.execute(text('SELECT id, avatar_seed FROM "user"')).all())
    assert again == first


def test_new_users_get_a_seed_from_the_column_default(tmp_path):
    engine = make_engine(tmp_path / "default.db")
    migrate(engine)
    admin = seed_tenant(engine, org_name="Faces", org_url_key="faces",
                        user_email="a@faces.test", token="faces-token", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(insert(user).values(
            id=new_id(), organization_id=admin["org_id"], name="Ada", display_name="Ada",
            created_at=now_iso(), updated_at=now_iso()))

    with engine.connect() as conn:
        seeds = dict(conn.execute(select(user.c.id, user.c.avatar_seed)).all())
    assert admin["user_id"] in seeds
    assert all(seed and SEED.match(seed) for seed in seeds.values())
    assert len(set(seeds.values())) == len(seeds)


def test_graphql_serves_the_stored_seed_for_viewer_and_members(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    import hoja_server.db as db_module
    from hoja_server.app import create_app

    monkeypatch.setattr(db_module, "default_db_dir", lambda: tmp_path)
    db_path = tmp_path / "graphql-seed.db"
    engine = make_engine(db_path)
    migrate(engine)
    token = "avatar-seed-token"
    seeded = seed_tenant(engine, org_name="Faces", org_url_key="faces",
                         user_email="ada@faces.test", token=token, demo_issues=False)
    with engine.connect() as conn:
        stored = conn.execute(select(user.c.avatar_seed).where(user.c.id == seeded["user_id"])).scalar_one()

    app = create_app(str(db_path), open_mode=False)
    with TestClient(app) as client:
        body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"}, json={
            "query": "{ viewer { id avatarSeed } organizationMembers(first: 10) { nodes { id avatarSeed } } }",
        }).json()
    assert not body.get("errors"), body
    assert body["data"]["viewer"]["avatarSeed"] == stored
    members = {node["id"]: node["avatarSeed"] for node in body["data"]["organizationMembers"]["nodes"]}
    assert members[seeded["user_id"]] == stored
