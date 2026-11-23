"""Manual order: ranks, moves, placement on status change, and their invariants.

What Linear does is the contract (artifacts/runs/20261123-views-depth/SPEC.md §1, captured
traffic in linear-capture/): order lives on the task, not the view; a drag writes one row;
a drag across groups changes the grouped field in the same write; a status change made
elsewhere moves the task to the top of its new group (team setting ``first``); ordering by
priority uses a separate rank. The property tests drive random move sequences, including
concurrent ones, and check after every step that the ranks stay distinct, valid and
consistent with what each move asked for.
"""

from __future__ import annotations

import random
import threading

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server import ranking
from hoja_server.app import create_app
from hoja_server.db import issue, make_engine, migrate, seed_tenant, team, workflow_state
from hoja_server.writer import Writer

TOKEN = "order-token"


def gql(client, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert "errors" not in body, body
    return body["data"]


def gql_err(client, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert body.get("errors"), body
    return body["errors"][0]


CREATE = "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { id identifier rank } } }"
MOVE = """mutation($id: String!, $i: TaskMoveInput!) { taskMove(id: $id, input: $i) {
  success task { identifier rank priorityRank state { name } priority } } }"""
UPDATE = "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { issue { rank } } }"
PREVIEW = """query($f: JSON, $d: JSON) { viewPreview(kind: "issue", filters: $f, display: $d) {
  issues(first: 250) { nodes { title } } } }"""


@pytest.fixture()
def world(tmp_path):
    db = tmp_path / "order.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Order", org_url_key="order", user_email="lead@order.test",
                         token=TOKEN, demo_issues=False)
    with engine.connect() as conn:
        tid = conn.execute(select(team.c.id).where(team.c.organization_id == seeded["org_id"])).scalar()
        states = {r.name: r.id for r in conn.execute(select(workflow_state.c.name, workflow_state.c.id)
                                                      .where(workflow_state.c.team_id == tid))}
    app = create_app(str(db), open_mode=False)
    with TestClient(app) as client:
        yield {"client": client, "engine": engine, "team": tid, "states": states, **seeded}


def make(world, title, state="Todo", **extra):
    return gql(world["client"], CREATE, {"i": {"title": title, "teamId": world["team"],
                                               "stateId": world["states"][state], **extra}}
               )["issueCreate"]["issue"]


def manual(world, filters=None, **display):
    rows = gql(world["client"], PREVIEW, {"f": filters or {}, "d": {
        "ordering": "manual", "completedWindow": "all", **display}})
    return [n["title"] for n in rows["viewPreview"]["issues"]["nodes"]]


def move(world, ident, **inp):
    return gql(world["client"], MOVE, {"id": ident, "i": inp})["taskMove"]["task"]


# ------------------------------------------------------------------ ranking module

def test_key_between_is_strictly_between_and_valid():
    rng = random.Random(1)
    keys = [ranking.key_between(None, None)]
    for _ in range(2000):
        i = rng.randrange(len(keys) + 1)
        a = keys[i - 1] if i > 0 else None
        b = keys[i] if i < len(keys) else None
        k = ranking.key_between(a, b)
        ranking.validate(k)
        assert (a is None or a < k) and (b is None or k < b)
        keys.insert(i, k)
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_prepend_and_append_stay_short():
    head = tail = ranking.key_between(None, None)
    for _ in range(10000):
        head = ranking.key_between(None, head)
        tail = ranking.key_between(tail, None)
    assert len(head) <= 5 and len(tail) <= 5


def test_same_spot_grows_slowly():
    a, b = "a0", "a1"
    for _ in range(200):
        b = ranking.key_between(a, b)
    assert len(b) < 45


@pytest.mark.parametrize("bad", ["", "a", "a00", "A" + "0" * 26, "a0!", "#x"])
def test_invalid_keys_are_refused(bad):
    with pytest.raises(ranking.RankError):
        ranking.validate(bad)


def test_keys_between_spreads_evenly():
    out = ranking.keys_between("a0", "a1", 50)
    assert out == sorted(out) and len(set(out)) == 50 and all("a0" < k < "a1" for k in out)
    assert ranking.keys_between(None, None, 0) == []


# ------------------------------------------------------------------ behaviour (Linear's)

def test_new_tasks_go_on_top_and_manual_follows_rank(world):
    for t in ("T1", "T2", "T3"):
        make(world, t)
    assert manual(world) == ["T3", "T2", "T1"]


def test_move_between_writes_one_row(world):
    t1, t2, t3 = (make(world, t) for t in ("T1", "T2", "T3"))
    before = {r.id: r.sort_order for r in world["engine"].connect().execute(select(issue.c.id, issue.c.sort_order))}
    moved = move(world, t1["identifier"], afterId=t3["identifier"], beforeId=t2["identifier"])
    after = {r.id: r.sort_order for r in world["engine"].connect().execute(select(issue.c.id, issue.c.sort_order))}
    assert [k for k in after if after[k] != before[k]] == [t1["id"]]
    assert moved["rank"] == after[t1["id"]]
    assert manual(world) == ["T3", "T1", "T2"]


def test_move_to_top_and_bottom(world):
    t1, _t2, t3 = (make(world, t) for t in ("T1", "T2", "T3"))
    move(world, t1["identifier"], beforeId=t3["identifier"])
    assert manual(world) == ["T1", "T3", "T2"]
    move(world, t1["identifier"], afterId=_t2["identifier"])
    assert manual(world) == ["T3", "T2", "T1"]


def test_after_only_lands_directly_after_even_with_hidden_rows(world):
    """A view shows a subset; "after X" must still put the task right after X everywhere."""
    t1, _t2, _t3, t4 = (make(world, t) for t in ("T1", "T2", "T3", "T4"))  # manual: T4 T3 T2 T1
    move(world, t1["identifier"], afterId=t4["identifier"])
    assert manual(world) == ["T4", "T1", "T3", "T2"]


def test_drag_across_groups_sets_state_and_rank_together(world):
    """Linear: one IssueUpdate {sortOrder, stateId} (linear-capture/s3-drag-across-groups)."""
    b1 = make(world, "B1", state="Backlog")
    t1, t2 = make(world, "T1"), make(world, "T2")
    moved = move(world, b1["identifier"], afterId=t2["identifier"], beforeId=t1["identifier"],
                 stateId=world["states"]["Todo"])
    assert moved["state"]["name"] == "Todo"
    assert manual(world, {"state": {"is": "Todo"}}) == ["T2", "B1", "T1"]


def test_priority_ordering_moves_priority_rank_only(world):
    """Linear: a drag under priority ordering writes prioritySortOrder alone (s2-*)."""
    # A new task's priority rank is appended (Linear's prioritySortOrder grows with creation,
    # proof/linear-diff.json S2): A B C within the priority. Move A after B.
    a, b, _c = (make(world, t, priority=2) for t in ("A", "B", "C"))
    moved = move(world, a["identifier"], afterId=b["identifier"], field="prioritySortOrder")
    assert moved["rank"] == a["rank"]  # sortOrder untouched
    rows = gql(world["client"], PREVIEW, {"f": {}, "d": {"ordering": "priority", "completedWindow": "all"}})
    assert [n["title"] for n in rows["viewPreview"]["issues"]["nodes"]] == ["B", "A", "C"]
    assert manual(world) == ["C", "B", "A"]  # the manual list did not move


@pytest.mark.parametrize("placement,expected", [("first", ["T1", "T3", "T2"]),
                                                ("last", ["T3", "T2", "T1"]),
                                                ("noAction", ["T3", "T2", "T1"])])
def test_status_change_placement_follows_team_setting(world, placement, expected):
    gql(world["client"], "mutation($id: String!, $i: TeamUpdateInput!) { teamUpdate(id: $id, input: $i) { success } }",
        {"id": world["team"], "i": {"issueSortOrderOnStateChange": placement}})
    t1, _t2, _t3 = (make(world, t) for t in ("T1", "T2", "T3"))
    gql(world["client"], UPDATE, {"id": t1["identifier"], "i": {"stateId": world["states"]["In Progress"]}})
    assert manual(world) == expected


def test_bad_inputs(world):
    t1, t2 = make(world, "T1"), make(world, "T2")
    assert gql_err(world["client"], MOVE, {"id": t1["identifier"], "i": {"afterId": t1["identifier"]}})
    assert gql_err(world["client"], MOVE, {"id": t1["identifier"], "i": {"afterId": "NOPE-999"}})
    assert gql_err(world["client"], MOVE, {"id": t1["identifier"], "i": {"field": "title"}})
    assert gql_err(world["client"], "mutation($id: String!, $i: TeamUpdateInput!) { teamUpdate(id: $id, input: $i) { success } }",
                   {"id": world["team"], "i": {"issueSortOrderOnStateChange": "middle"}})
    assert t2


def test_duplicate_ranks_are_repaired_on_the_next_move(world):
    t1, t2, t3 = (make(world, t) for t in ("T1", "T2", "T3"))
    with world["engine"].begin() as conn:  # an import that wrote the same key twice
        conn.execute(issue.update().where(issue.c.id.in_([t2["id"], t1["id"]])).values(sort_order="a5"))
    move(world, t3["identifier"], afterId=t2["identifier"], beforeId=t1["identifier"])
    assert manual(world).index("T2") < manual(world).index("T3") < manual(world).index("T1")


def test_stale_before_is_ignored(world):
    t1, t2, t3 = (make(world, t) for t in ("T1", "T2", "T3"))  # T3 T2 T1
    move(world, t2["identifier"], afterId=t1["identifier"], beforeId=t3["identifier"])  # before is above after
    assert manual(world) == ["T3", "T1", "T2"]


def test_rows_without_rank_get_one_when_named(world):
    t1, t2 = make(world, "T1"), make(world, "T2")
    with world["engine"].begin() as conn:
        conn.execute(issue.update().where(issue.c.id == t1["id"]).values(sort_order=None))
    move(world, t2["identifier"], afterId=t1["identifier"])
    assert manual(world) == ["T1", "T2"]


def test_migration_backfills_newest_first(tmp_path):
    from hoja_server import db as dbmod
    engine = make_engine(tmp_path / "m.db")
    migrate(engine)
    seed_tenant(engine, demo_issues=True)
    with engine.begin() as conn:
        conn.execute(issue.update().values(sort_order=None, priority_sort_order=None))
        dbmod._apply_views_depth(conn)
        rows = conn.execute(select(issue.c.created_at, issue.c.sort_order, issue.c.priority_sort_order)
                            .order_by(issue.c.sort_order)).all()
    assert rows and all(r.sort_order and r.priority_sort_order for r in rows)
    created = [r.created_at for r in rows]
    assert created == sorted(created, reverse=True)
    by_priority_rank = sorted(rows, key=lambda r: r.priority_sort_order)
    assert [r.created_at for r in by_priority_rank] == sorted(created)


def test_new_task_lands_on_top_of_its_status_not_the_workspace(world):
    """Linear's new-issue sortOrder is the top of its status column (proof/linear-diff.json S1):
    a new Backlog task goes above the Backlog tasks, below every Todo task created earlier."""
    t1 = make(world, "T1")
    b1 = make(world, "B1", state="Backlog")
    t2 = make(world, "T2")
    b2 = make(world, "B2", state="Backlog")
    ranks = {x["identifier"]: x["rank"] for x in (t1, b1, t2, b2)}
    assert ranks[t2["identifier"]] < ranks[t1["identifier"]]
    assert ranks[b2["identifier"]] < ranks[b1["identifier"]]
    assert manual(world, {"state": {"is": "Todo"}}) == ["T2", "T1"]
    assert manual(world, {"state": {"is": "Backlog"}}) == ["B2", "B1"]


# ------------------------------------------------------------------ properties

def _ranks(engine, org_id):
    with engine.connect() as conn:
        return dict(conn.execute(select(issue.c.identifier, issue.c.sort_order).where(and_(
            issue.c.organization_id == org_id, issue.c.archived_at.is_(None)))).all())


@pytest.mark.parametrize("seed", range(25))
def test_random_move_sequences_keep_ranks_distinct_and_honour_each_move(world, seed):
    rng = random.Random(seed)
    w = Writer(world["engine"])
    idents = [make(world, f"P{i}")["identifier"] for i in range(8)]
    for step in range(60):
        order = sorted(_ranks(world["engine"], world["org_id"]).items(), key=lambda kv: kv[1])
        names = [k for k, _ in order]
        mover = rng.choice(idents)
        others = [n for n in names if n != mover]
        i = rng.randrange(len(others) + 1)
        after = others[i - 1] if i > 0 else None
        before = others[i] if i < len(others) else None
        choice = rng.random()
        if choice < 0.2:
            after = None
        elif choice < 0.4:
            before = None
        w.issue_update(world["org_id"], mover, {"_move": {"afterId": after, "beforeId": before}},
                       actor_id=world["user_id"])
        ranks = _ranks(world["engine"], world["org_id"])
        keys = list(ranks.values())
        assert len(set(keys)) == len(keys), (seed, step, ranks)
        for k in keys:
            ranking.validate(k)
        if after:
            assert ranks[after] < ranks[mover], (seed, step)
            between = [n for n, k in ranks.items() if ranks[after] < k < ranks[mover]]
            assert between == [], (seed, step, "something landed between after and mover")
        if before:
            assert ranks[mover] < ranks[before], (seed, step)


@pytest.mark.parametrize("seed", range(5))
def test_concurrent_moves_into_the_same_gap_never_collide(tmp_path, seed):
    """Four movers drop different tasks into the same gap at the same time. Computed in the
    browser from one snapshot they would all get the same key (prototype: 100% collisions);
    computed in the write transaction they never do."""
    db = tmp_path / "c.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="C", org_url_key="c", token=TOKEN, demo_issues=False)
    org, uid = seeded["org_id"], seeded["user_id"]
    w = Writer(engine)
    with engine.connect() as conn:
        tid = conn.execute(select(team.c.id).where(team.c.organization_id == org)).scalar()
    idents = [w.issue_create(org, uid, {"title": f"C{i}", "teamId": tid}) for i in range(8)]
    with engine.connect() as conn:
        by_id = dict(conn.execute(select(issue.c.id, issue.c.identifier)).all())
    names = [by_id[i] for i in idents]
    by_rank = sorted(names, key=lambda n: _ranks(engine, org)[n])
    gap_after, gap_before, movers = by_rank[0], by_rank[1], by_rank[2:6]
    rng = random.Random(seed)
    rng.shuffle(movers)
    errors: list = []
    barrier = threading.Barrier(len(movers))

    def run(m):
        try:
            barrier.wait()
            for _ in range(5):
                try:
                    w.issue_update(org, m, {"_move": {"afterId": gap_after, "beforeId": gap_before}},
                                   actor_id=uid)
                    return
                except Exception as exc:  # SQLite "database is locked" under contention: retry
                    if "locked" not in str(exc):
                        raise
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(m,)) for m in movers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    ranks = _ranks(engine, org)
    keys = list(ranks.values())
    assert len(set(keys)) == len(keys), ranks
    for m in movers:
        assert ranks[gap_after] < ranks[m] < ranks[gap_before]
