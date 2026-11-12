"""Recursive identifiers, the alias ledger and the migration (HOJA-472)."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import identifiers
from hoja_server.app import create_app
from hoja_server.db import (
    issue,
    issue_identifier_alias,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
    team,
)
from hoja_server.identifier_migration import backward, forward, verify
from hoja_server.store import Store
from hoja_server.writer import InvalidReferenceError, Writer


def _seeded(tmp_path):
    engine = make_engine(tmp_path / "recursive.db")
    migrate(engine)
    seeded = seed_tenant(
        engine,
        org_name="Recursive",
        org_url_key="recursive",
        team_key="HOJA",
        team_name="Hoja",
        user_name="Recursive User",
        user_email="recursive@example.test",
        demo_issues=False,
    )
    return engine, seeded


def _create(writer, seeded, title, parent=None):
    values = {"teamId": seeded["team_id"], "title": title}
    if parent:
        values["parentId"] = parent
    return writer.issue_create(seeded["org_id"], seeded["user_id"], values)


def _identifier(store, seeded, issue_id):
    return store.issue_by_id_or_identifier(seeded["org_id"], issue_id)["identifier"]


def test_grammar_matches_the_examples_on_the_issue() -> None:
    assert identifiers.ROOT_SLOT == "A"
    assert identifiers.child_identifier("HOJA-416", "A", 1) == "HOJA-416-A-1"
    assert identifiers.child_identifier("HOJA-416-A-1", "a", 1) == "HOJA-416-A-1-a-1"
    # The slot is the node's own sibling ordinal, so a second sibling is "b".
    assert identifiers.slot_for(2, 1) == "b"
    assert identifiers.child_identifier("HOJA-416-A-2", "b", 1) == "HOJA-416-A-2-b-1"
    # Past Z the slot grows instead of repeating or truncating.
    assert identifiers.slot_for(26, 1) == "z"
    assert identifiers.slot_for(27, 1) == "aa"
    assert identifiers.slot_for(27, 2) == "AA"
    assert identifiers.slot_for(1, 3) == "a"


def test_allocator_composes_recursive_identifiers(tmp_path) -> None:
    engine, seeded = _seeded(tmp_path)
    writer, store = Writer(engine), Store(engine)
    root = _create(writer, seeded, "Root")
    first = _create(writer, seeded, "First", parent=root)
    second = _create(writer, seeded, "Second", parent=root)
    grand = _create(writer, seeded, "Grand", parent=first)

    assert _identifier(store, seeded, root) == "HOJA-1"
    assert _identifier(store, seeded, first) == "HOJA-1-A-1"
    assert _identifier(store, seeded, second) == "HOJA-1-A-2"
    assert _identifier(store, seeded, grand) == "HOJA-1-A-1-a-1"

    numbers = {
        row["id"]: row["number"]
        for row in _issue_rows(engine, seeded, ("id", "number"))
    }
    for child in (first, second, grand):
        assert identifiers.is_child_number(numbers[child])
    # A child never consumed a top-level number: the next root is HOJA-2.
    assert _identifier(store, seeded, _create(writer, seeded, "Next root")) == "HOJA-2"


def test_depth_limit_is_refused_not_flattened(tmp_path) -> None:
    engine, seeded = _seeded(tmp_path)
    writer = Writer(engine)
    parent = _create(writer, seeded, "Depth 0")
    for level in range(1, identifiers.MAX_DEPTH + 1):
        parent = _create(writer, seeded, f"Depth {level}", parent=parent)
    with pytest.raises(InvalidReferenceError):
        _create(writer, seeded, "Too deep", parent=parent)


def test_migration_is_idempotent_reversible_and_keeps_old_urls_resolving(tmp_path) -> None:
    engine, seeded = _seeded(tmp_path)
    writer, store = Writer(engine), Store(engine)
    root = _create(writer, seeded, "Root")
    # A legacy child: flat identifier and number, no depth, exactly what the
    # 209 live sub-tasks look like after PR #176.
    flat = _insert_flat_child(engine, seeded, root, "HOJA-517", 517)

    first = forward(engine, apply=True)
    assert first["renumbered"] == 1
    assert verify(engine) == []
    assert _identifier(store, seeded, flat) == "HOJA-1-A-1"
    # Old URL / commit message / GitHub link still resolves, without rewriting
    # anything a person wrote.
    assert store.issue_by_id_or_identifier(seeded["org_id"], "HOJA-517")["id"] == flat

    # UNIQUE(team_id, number) is still enforced after the migration.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(issue.insert().values(
            id=new_id(), organization_id=seeded["org_id"],
            team_id=seeded["team_id"], number=1, identifier="HOJA-DUP",
            title="Duplicate number", created_at=now_iso(), updated_at=now_iso(),
        ))
    second = forward(engine, apply=True)
    assert second["renumbered"] == 0
    assert second["skipped"] == 1
    assert verify(engine) == []

    back = backward(engine, apply=True)
    assert back["restored"] == 1
    assert _identifier(store, seeded, flat) == "HOJA-517"
    with engine.connect() as conn:
        assert conn.execute(select(issue_identifier_alias)).all() == []
        assert conn.execute(select(issue.c.number).where(issue.c.id == flat)).scalar() == 517


def test_retired_identifier_redirects_in_the_web_app(tmp_path) -> None:
    engine, seeded = _seeded(tmp_path)
    writer = Writer(engine)
    root = _create(writer, seeded, "Root")
    _insert_flat_child(engine, seeded, root, "HOJA-517", 517)
    forward(engine, apply=True)
    client = TestClient(create_app(str(tmp_path / "recursive.db")))
    response = client.get("/recursive/issue/HOJA-517", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/recursive/issue/HOJA-1-A-1"


def _insert_flat_child(engine, seeded, parent_id, identifier, number):
    iid = new_id()
    ts = now_iso()
    with engine.begin() as conn:
        state_id = conn.execute(select(issue.c.state_id).where(
            issue.c.id == parent_id)).scalar()
        conn.execute(issue.insert().values(
            id=iid, organization_id=seeded["org_id"], team_id=seeded["team_id"],
            number=number, identifier=identifier, title="Legacy child",
            state_id=state_id, parent_id=parent_id, creator_id=seeded["user_id"],
            created_at=ts, updated_at=ts,
        ))
        conn.execute(team.update().where(team.c.id == seeded["team_id"]).values(
            issue_counter=number))
    return iid


def _issue_rows(engine, seeded, columns):
    names = tuple(getattr(issue.c, name) for name in columns)
    with engine.connect() as conn:
        return conn.execute(select(*names).where(
            issue.c.organization_id == seeded["org_id"])).mappings().all()
