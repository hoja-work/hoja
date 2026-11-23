"""Project and initiative updates: the rows Pulse renders, and who may change them.

Acceptance for HOJA-505: a project update carries project, author, health/status,
body and created/updated timestamps; create/update/delete exist over GraphQL with
a paginated query ordered by updatedAt descending; an update is never readable
across organizations; the migration is additive and reversible.

Ordering is a correctness property, not a presentation detail: keyset pagination
on (updated_at, id) with the id tie-break is exercised with equal timestamps and
replayed page-by-page, because a feed that drops or duplicates rows at a page
boundary looks fine in a screenshot and wrong to a reader.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import inspect, select
from starlette.testclient import TestClient

from hoja_server import db as dbm
from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project_update,
    schema_revision,
    seed_tenant,
    token_hash,
    user as user_table,
)

ADMIN = "pulse-admin-token"
MEMBER = "pulse-member-token"
OTHER = "pulse-other-token"

PROJECT_CREATE = ("mutation($i: ProjectCreateInput!) { projectCreate(input: $i) { success project { id } } }")
INITIATIVE_CREATE = ("mutation($i: InitiativeCreateInput!) { initiativeCreate(input: $i) { success initiative { id } } }")
UPDATE_CREATE = ("mutation($i: ProjectUpdateCreateInput!) { projectUpdateCreate(input: $i) "
                 "{ success projectUpdate { id body status health createdAt updatedAt actor { id } project { id } } } }")
UPDATE_EDIT = ("mutation($id: String!, $i: ProjectUpdateUpdateInput!) { projectUpdateUpdate(id: $id, input: $i) "
               "{ success projectUpdate { id body status health createdAt updatedAt } } }")
UPDATE_DELETE = "mutation($id: String!) { projectUpdateDelete(id: $id) { success } }"
UPDATES = ("query($first: Int, $after: String) { projectUpdates(first: $first, after: $after) "
           "{ nodes { id body updatedAt createdAt } pageInfo { hasNextPage endCursor } } }")
IUPDATE_CREATE = ("mutation($i: InitiativeUpdateCreateInput!) { initiativeUpdateCreate(input: $i) "
                  "{ success initiativeUpdate { id body health createdAt updatedAt initiative { id } actor { id } } } }")
IUPDATE_EDIT = ("mutation($id: String!, $i: InitiativeUpdateUpdateInput!) { initiativeUpdateUpdate(id: $id, input: $i) "
                "{ success initiativeUpdate { id body health updatedAt } } }")
IUPDATE_DELETE = "mutation($id: String!) { initiativeUpdateDelete(id: $id) { success } }"
IUPDATES = "query { initiativeUpdates(first: 50) { nodes { id body health updatedAt } } }"
IINITIATIVE_EDIT = ("mutation($id: String!, $i: InitiativeUpdateInput!) { initiativeUpdate(id: $id, input: $i) "
                    "{ success initiative { id name } } }")


def _data(client, token, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert not body.get("errors"), body
    return body["data"]


def _add_member(engine, org_id, email, token, admin=False):
    ts = now_iso()
    uid = new_id()
    with engine.begin() as conn:
        conn.execute(user_table.insert().values(
            id=uid, organization_id=org_id, name=email.split("@")[0], email=email,
            active=True, admin=admin, created_at=ts, updated_at=ts))
        conn.execute(api_key.insert().values(
            id=new_id(), token_hash=token_hash(token), label=email,
            user_id=uid, organization_id=org_id, created_at=ts))
    return uid


def _project(client, token, name="Roadmap"):
    return _data(client, token, PROJECT_CREATE, {"i": {"name": name}})["projectCreate"]["project"]["id"]


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "pulse.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Pulse", org_url_key="pulse",
                         user_email="admin@pulse.test", token=ADMIN, demo_issues=False)
    member = _add_member(engine, seeded["org_id"], "member@pulse.test", MEMBER)
    other = seed_tenant(engine, org_name="Other", org_url_key="other",
                        user_email="admin@other.test", token=OTHER, demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    return {"engine": engine, "app": app, "org": seeded["org_id"],
            "admin": seeded["user_id"], "member": member, "other_org": other["org_id"]}


def test_project_update_lifecycle_over_graphql(world) -> None:
    """Create, read, edit, delete — the exact surface Pulse consumes."""
    with TestClient(world["app"]) as client:
        pid = _project(client, ADMIN)
        created = _data(client, ADMIN, UPDATE_CREATE, {"i": {
            "projectId": pid, "body": "First honest update.", "health": "on track", "status": "published"}})
        assert created["projectUpdateCreate"]["success"]
        node = created["projectUpdateCreate"]["projectUpdate"]
        uid = node["id"]
        assert node["project"]["id"] == pid
        assert node["actor"]["id"] == world["admin"]
        assert node["body"] == "First honest update."
        assert node["createdAt"] and node["updatedAt"]

        listed = _data(client, ADMIN, UPDATES, {"first": 50})["projectUpdates"]["nodes"]
        assert [n["id"] for n in listed] == [uid]

        edited = _data(client, ADMIN, UPDATE_EDIT, {"id": uid, "i": {"body": "Edited honestly.", "health": "at risk"}})
        assert edited["projectUpdateUpdate"]["success"]
        assert edited["projectUpdateUpdate"]["projectUpdate"]["body"] == "Edited honestly."

        deleted = _data(client, ADMIN, UPDATE_DELETE, {"id": uid})
        assert deleted["projectUpdateDelete"]["success"]
        assert _data(client, ADMIN, UPDATES, {"first": 50})["projectUpdates"]["nodes"] == []
        # A second delete of the same id is a clean false, not an error: the row is absent.
        assert _data(client, ADMIN, UPDATE_DELETE, {"id": uid})["projectUpdateDelete"]["success"] is False


def test_edit_bumps_updated_at_and_preserves_created_at(world) -> None:
    """The feed orders by last change; an edit that did not move updated_at would be invisible."""
    with TestClient(world["app"]) as client:
        pid = _project(client, ADMIN)
        uid = _data(client, ADMIN, UPDATE_CREATE, {"i": {"projectId": pid, "body": "orig"}})[
            "projectUpdateCreate"]["projectUpdate"]["id"]
    old_created = "2020-01-01T00:00:00Z"
    old_updated = "2020-01-02T00:00:00Z"
    with world["engine"].begin() as conn:
        conn.execute(project_update.update().where(project_update.c.id == uid).values(
            created_at=old_created, updated_at=old_updated))
    with TestClient(world["app"]) as client:
        edited = _data(client, ADMIN, UPDATE_EDIT, {"id": uid, "i": {"body": "new"}})
        node = edited["projectUpdateUpdate"]["projectUpdate"]
        assert node["createdAt"] == old_created
        assert node["updatedAt"] > old_updated


def test_author_may_edit_admin_may_only_delete(world) -> None:
    """comment_policy's matrix applies to update posts: words carry their author's name."""
    with TestClient(world["app"]) as client:
        pid = _project(client, ADMIN)
        uid = _data(client, ADMIN, UPDATE_CREATE, {"i": {"projectId": pid, "body": "mine"}})[
            "projectUpdateCreate"]["projectUpdate"]["id"]

        # A plain member who is not the author can neither edit nor delete.
        assert _data(client, MEMBER, UPDATE_EDIT, {"id": uid, "i": {"body": "hijack"}})[
            "projectUpdateUpdate"]["success"] is False
        assert _data(client, MEMBER, UPDATE_DELETE, {"id": uid})["projectUpdateDelete"]["success"] is False

        # The admin is the author here, so editing is theirs; a member's post is not.
        pid2 = _project(client, ADMIN, "Second")
        muid = _data(client, MEMBER, UPDATE_CREATE, {"i": {"projectId": pid2, "body": "member words"}})[
            "projectUpdateCreate"]["projectUpdate"]["id"]
        assert _data(client, ADMIN, UPDATE_EDIT, {"id": muid, "i": {"body": "admin rewrite"}})[
            "projectUpdateUpdate"]["success"] is False
        listed = _data(client, ADMIN, UPDATES, {"first": 50})["projectUpdates"]["nodes"]
        assert next(n for n in listed if n["id"] == muid)["body"] == "member words"
        assert _data(client, ADMIN, UPDATE_DELETE, {"id": muid})["projectUpdateDelete"]["success"] is True


def test_pagination_orders_by_updated_at_with_stable_ties(world) -> None:
    """Equal timestamps must not drop or duplicate a row across a page boundary."""
    with TestClient(world["app"]) as client:
        pid = _project(client, ADMIN)
    rows = [
        (new_id(), "2026-01-01T00:00:00Z", "oldest"),
        (new_id(), "2026-01-02T00:00:00Z", "tie-b"),
        (new_id(), "2026-01-02T00:00:00Z", "tie-a"),
        (new_id(), "2026-01-03T00:00:00Z", "newest"),
    ]
    with world["engine"].begin() as conn:
        for rid, updated, body in rows:
            conn.execute(project_update.insert().values(
                id=rid, organization_id=world["org"], project_id=pid, body=body,
                created_at="2026-01-01T00:00:00Z", updated_at=updated, actor_id=world["admin"]))
    expected = [rid for rid, _u, _b in sorted(rows, key=lambda r: (r[1], r[0]), reverse=True)]
    with TestClient(world["app"]) as client:
        seen, after = [], None
        while True:
            page = _data(client, ADMIN, UPDATES, {"first": 2, "after": after})["projectUpdates"]
            seen.extend(n["id"] for n in page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        assert seen == expected
        assert len(set(seen)) == len(rows)


def test_updates_never_cross_organizations(world) -> None:
    """A foreign id must look absent, and no org sees another org's feed."""
    with TestClient(world["app"]) as client:
        own_pid = _project(client, ADMIN, "Own")
        own = _data(client, ADMIN, UPDATE_CREATE, {"i": {"projectId": own_pid, "body": "own words"}})[
            "projectUpdateCreate"]["projectUpdate"]["id"]
        foreign_pid = _project(client, OTHER, "Foreign")
        foreign = _data(client, OTHER, UPDATE_CREATE, {"i": {"projectId": foreign_pid, "body": "foreign words"}})[
            "projectUpdateCreate"]["projectUpdate"]["id"]

        listed = _data(client, ADMIN, UPDATES, {"first": 50})["projectUpdates"]["nodes"]
        assert [n["id"] for n in listed] == [own]
        assert _data(client, OTHER, UPDATES, {"first": 50})["projectUpdates"]["nodes"][0]["id"] == foreign

        # A foreign update id is indistinguishable from a deleted one.
        assert _data(client, ADMIN, UPDATE_EDIT, {"id": foreign, "i": {"body": "x"}})[
            "projectUpdateUpdate"]["success"] is False
        assert _data(client, ADMIN, UPDATE_DELETE, {"id": foreign})["projectUpdateDelete"]["success"] is False
        # Creating an update on a foreign project is refused.
        assert _data(client, ADMIN, UPDATE_CREATE, {"i": {"projectId": foreign_pid, "body": "y"}})[
            "projectUpdateCreate"]["success"] is False


def test_initiative_update_lifecycle_and_tenancy(world) -> None:
    """Initiatives exist; initiative updates become rows of their own (Linear's InitiativeUpdate)."""
    with TestClient(world["app"]) as client:
        iid = _data(client, ADMIN, INITIATIVE_CREATE, {"i": {"name": "Company bet"}})[
            "initiativeCreate"]["initiative"]["id"]
        created = _data(client, ADMIN, IUPDATE_CREATE, {"i": {
            "initiativeId": iid, "body": "Kicking off.", "health": "on track"}})
        assert created["initiativeUpdateCreate"]["success"]
        uid = created["initiativeUpdateCreate"]["initiativeUpdate"]["id"]
        assert created["initiativeUpdateCreate"]["initiativeUpdate"]["initiative"]["id"] == iid

        nodes = _data(client, ADMIN, IUPDATES)["initiativeUpdates"]["nodes"]
        assert [n["id"] for n in nodes] == [uid]
        edited = _data(client, ADMIN, IUPDATE_EDIT, {"id": uid, "i": {"body": "Still on."}})
        assert edited["initiativeUpdateUpdate"]["initiativeUpdate"]["body"] == "Still on."

        # The pre-existing initiativeUpdate field editor still edits the initiative itself.
        assert _data(client, ADMIN, IINITIATIVE_EDIT, {"id": iid, "i": {"name": "Company bet v2"}})[
            "initiativeUpdate"]["success"]

        foreign_iid = _data(client, OTHER, INITIATIVE_CREATE, {"i": {"name": "Foreign bet"}})[
            "initiativeCreate"]["initiative"]["id"]
        assert _data(client, ADMIN, IUPDATE_CREATE, {"i": {"initiativeId": foreign_iid, "body": "x"}})[
            "initiativeUpdateCreate"]["success"] is False

        assert _data(client, ADMIN, IUPDATE_DELETE, {"id": uid})["initiativeUpdateDelete"]["success"]
        assert _data(client, ADMIN, IUPDATES)["initiativeUpdates"]["nodes"] == []


def test_migration_57_is_additive_reversible_and_backfills(tmp_path, monkeypatch) -> None:
    """Revision 57 adds updated_at and initiative_update; down and up again must match."""
    engine = make_engine(tmp_path / "migrate57.db")
    # create_all always builds the newest metadata, so reach the true rev-56 shape by
    # reversing 57 on a fresh database before seeding the legacy row.
    monkeypatch.setattr(dbm, "CURRENT_SCHEMA_REVISION", 56)
    migrate(engine)
    with engine.begin() as conn:
        dbm._down_pulse_updates(conn)
    assert "updated_at" not in {c["name"] for c in inspect(engine).get_columns("project_update")}
    assert not inspect(engine).has_table("initiative_update")

    seeded = seed_tenant(engine, org_name="Mig", org_url_key="mig",
                         user_email="a@mig.test", token="mig-token", demo_issues=False)
    ts = now_iso()
    pid, rid = new_id(), new_id()
    with engine.begin() as conn:
        conn.execute(dbm.project.insert().values(
            id=pid, organization_id=seeded["org_id"], name="Legacy", created_at=ts, updated_at=ts))
        conn.execute(dbm.project_update.insert().values(
            id=rid, organization_id=seeded["org_id"], project_id=pid, body="legacy row",
            created_at="2025-12-31T00:00:00Z"))

    monkeypatch.setattr(dbm, "CURRENT_SCHEMA_REVISION", 57)
    migrate(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("project_update")}
    assert "updated_at" in columns
    assert inspect(engine).has_table("initiative_update")
    # SQLite reflection omits expression indexes, so read the catalog directly.
    with engine.connect() as conn:
        feed_index = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_project_update_feed'").first()
    assert feed_index is not None
    with engine.connect() as conn:
        legacy_updated = conn.execute(select(project_update.c.updated_at).where(
            project_update.c.id == rid)).scalar()
    # No backfill UPDATE: on the production table it queues deferred capture-trigger
    # events and PostgreSQL then refuses in-transaction DDL. NULL reads as created_at.
    assert legacy_updated is None
    with TestClient(create_app(str(tmp_path / "migrate57.db"), open_mode=False)) as client:
        nodes = _data(client, "mig-token", UPDATES, {"first": 10})["projectUpdates"]["nodes"]
    assert [n["id"] for n in nodes] == [rid]
    assert nodes[0]["updatedAt"] == "2025-12-31T00:00:00Z"

    def snapshot():
        insp = inspect(engine)
        return (
            sorted(c["name"] for c in insp.get_columns("project_update")),
            insp.has_table("initiative_update"),
            sorted(ix["name"] for ix in insp.get_indexes("project_update")),
            sorted(c["name"] for c in insp.get_columns("initiative_update"))
                   if insp.has_table("initiative_update") else None,
        )

    after_up = snapshot()
    with engine.begin() as conn:
        dbm._down_pulse_updates(conn)
    assert "updated_at" not in {c["name"] for c in inspect(engine).get_columns("project_update")}
    assert not inspect(engine).has_table("initiative_update")
    assert inspect(engine).has_table("schema_revision")
    with engine.connect() as conn:
        assert conn.execute(select(schema_revision.c.revision).where(
            schema_revision.c.revision == 57)).first() is None, "down must clear its ledger row"
    migrate(engine)
    assert snapshot() == after_up, "up -> down -> up must be schema-identical"
