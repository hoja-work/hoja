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

SEED = re.compile(r"^(\d{1,3}:)?[0-9a-f]{16}$")


def test_new_seeds_are_random_hex_and_not_the_name():
    seeds = {new_avatar_seed() for _ in range(200)}
    assert len(seeds) == 200
    assert all(SEED.match(seed) for seed in seeds)


def test_the_current_revision_includes_the_avatar_seed_work():
    assert CURRENT_SCHEMA_REVISION == 86


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


def test_new_users_in_one_workspace_get_distinct_hues(tmp_path):
    from hoja_server.db import avatar_hue_of

    engine = make_engine(tmp_path / "hues.db")
    migrate(engine)
    admin = seed_tenant(engine, org_name="Hues", org_url_key="hues",
                        user_email="a@hues.test", token="hues-token", demo_issues=False)
    with engine.begin() as conn:
        for i in range(6):
            conn.execute(insert(user).values(
                id=new_id(), organization_id=admin["org_id"], name=f"Person {i}",
                display_name=f"Person {i}", created_at=now_iso(), updated_at=now_iso()))
    with engine.connect() as conn:
        seeds = conn.execute(select(user.c.avatar_seed).where(
            user.c.organization_id == admin["org_id"])).scalars().all()
    hues = [avatar_hue_of(seed) for seed in seeds]
    assert all(hue is not None for hue in hues)
    assert len(set(hues)) == len(hues)
    # Six hues spread over the wheel: the closest pair is still far apart.
    ordered = sorted(hues)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])] + [360 - ordered[-1] + ordered[0]]
    assert min(gaps) >= 45


def test_revision_86_gives_existing_seeds_a_distinct_hue_per_workspace(tmp_path):
    from hoja_server.db import _apply_user_avatar_hue, avatar_hue_of

    engine = make_engine(tmp_path / "hue-backfill.db")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            'CREATE TABLE "user" (id VARCHAR PRIMARY KEY, organization_id VARCHAR, '
            'created_at VARCHAR, avatar_seed VARCHAR)')
        conn.exec_driver_sql(
            "INSERT INTO \"user\" VALUES ('u1','o1','2026-01-01','aaaa1111bbbb2222'),"
            "('u2','o1','2026-01-02','cccc3333dddd4444'),('u3','o2','2026-01-01','eeee5555ffff6666')")
        _apply_user_avatar_hue(conn)
    with engine.connect() as conn:
        seeds = dict(conn.execute(text('SELECT id, avatar_seed FROM "user"')).all())
    assert seeds["u1"].endswith(":aaaa1111bbbb2222") and avatar_hue_of(seeds["u1"]) is not None
    assert seeds["u2"].endswith(":cccc3333dddd4444")
    assert avatar_hue_of(seeds["u1"]) != avatar_hue_of(seeds["u2"])
    # Replaying keeps every hue.
    with engine.begin() as conn:
        _apply_user_avatar_hue(conn)
    with engine.connect() as conn:
        assert dict(conn.execute(text('SELECT id, avatar_seed FROM "user"')).all()) == seeds
