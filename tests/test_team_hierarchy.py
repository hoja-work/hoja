"""Team hierarchy (departments), team visibility, and single-org mode.

Written before the implementation, per IMPLEMENTATION-BRIEF.md item A. These tests
encode the five locked decisions:

1. A department is a ``team`` row with children (``team.parent_team_id``), max depth 3,
   self-parent and cycles rejected, and no access inheritance from parent to child.
2. ``team.visibility`` in ('public', 'restricted', 'private'); ``public`` = any org
   member, ``restricted`` = parent-team members may see and join, ``private`` =
   explicit members only.
3. ``team_member.role`` in ('owner', 'member'), default 'member'.
5. Adding a member to a sub-team also adds them to every ancestor; removing a parent
   member who is still a member of a descendant is refused.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from cliniar_server.app import create_app
from cliniar_server.db import (
    api_key,
    cycle,
    issue,
    issue_label,
    issue_label_link,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    user as user_table,
)

ADMIN = "hier-admin-token"
OUTSIDER = "hier-outsider-token"

CREATE_TEAM = (
    "mutation($input: TeamCreateInput!) { teamCreate(input: $input) "
    "{ success team { id key visibility parentTeamId } } }"
)
UPDATE_TEAM = (
    "mutation($id: String!, $input: TeamUpdateInput!) { teamUpdate(id: $id, input: $input) "
    "{ success team { id key visibility parentTeamId parent { id } } } }"
)
MEMBER_ADD = "mutation($t: String!, $u: String!) { teamMemberAdd(teamId: $t, userId: $u) { success } }"
MEMBER_DROP = "mutation($t: String!, $u: String!) { teamMemberRemove(teamId: $t, userId: $u) { success } }"
CREATE_ISSUE = (
    "mutation($i: IssueCreateInput!) { issueCreate(input: $i) "
    "{ success issue { id identifier title team { id key } } } }"
)
TEAMS = "{ teams(first: 100) { nodes { id key visibility } } }"
ISSUES = "{ issues(first: 100) { nodes { id identifier title team { id key } } } }"
ONE_TEAM = "query($id: String!) { team(id: $id) { id key visibility parentTeamId children(first: 50) { nodes { id key } } } }"
ONE_ISSUE = "query($id: String!) { issue(id: $id) { id identifier title } }"
SEARCH = "query($t: String!) { searchIssues(term: $t, first: 20) { nodes { id identifier } } }"
LABELS = ("query($team: ID!) { issueLabels(first: 200, filter: {team: {id: {eq: $team}}}) "
          "{ nodes { id name } } }")
CADENCE = "query($t: String!) { teamCycleCadence(teamId: $t) { enabled } }"
WORKSPACE_CREATE = (
    "mutation($input: WorkspaceCreateInput!) { workspaceCreate(input: $input) "
    "{ success organization { id name } } }"
)
WORKSPACE_SWITCH = (
    "mutation($id: String!) { workspaceSwitch(organizationId: $id) { success organization { id } } }"
)


def _gql(client, token, query, variables=None):
    return client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()


def _data(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert not body.get("errors"), body
    return body["data"]


def _make_user(engine, org_id: str, name: str, email: str, token: str, *, admin: bool = False) -> str:
    ts = now_iso()
    uid = new_id()
    with engine.begin() as conn:
        conn.execute(user_table.insert().values(
            id=uid, organization_id=org_id, name=name, email=email, active=True,
            admin=admin, created_at=ts, updated_at=ts))
        conn.execute(api_key.insert().values(
            id=new_id(), token_hash=token_hash(token), label=name,
            user_id=uid, organization_id=org_id, created_at=ts))
    return uid


def _make_team(engine, org_id: str, key: str, name: str, **values) -> str:
    ts = now_iso()
    tid = new_id()
    with engine.begin() as conn:
        conn.execute(team.insert().values(
            id=tid, organization_id=org_id, key=key.upper(), name=name, timezone="UTC",
            issue_counter=0, created_at=ts, updated_at=ts, **values))
        seed_states_for_team(conn, org_id, tid)
    return tid


@pytest.fixture()
def world(tmp_path):
    """One org, an admin, an outsider (member of the root team only), and a root team."""
    db_path = tmp_path / "hierarchy.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Hier", org_url_key="hier",
                         user_email="admin@hier.test", token=ADMIN, team_key="ENG",
                         team_name="Engineering", demo_issues=False)
    with engine.connect() as conn:
        root_team = conn.execute(
            select(team.c.id).where(team.c.organization_id == seeded["org_id"])).scalar()
    outsider = _make_user(engine, seeded["org_id"], "Outsider", "out@hier.test", OUTSIDER)
    with engine.begin() as conn:
        conn.execute(team_member.insert().values(team_id=root_team, user_id=outsider))
    app = create_app(str(db_path), open_mode=False)
    return {"engine": engine, "app": app, "org": seeded["org_id"],
            "admin": seeded["user_id"], "outsider": outsider, "root": root_team}


# ---------------------------------------------------------------- decision 2
def test_private_team_reads_are_denied_to_non_members(world) -> None:
    """A private team never leaks through teams/issues/team/issue/search/cadence."""
    engine, app = world["engine"], world["app"]
    secret = _make_team(engine, world["org"], "SEC", "Secret", visibility="private")
    member = _make_user(engine, world["org"], "Insider", "in@hier.test", "hier-insider-token")
    with engine.begin() as conn:
        conn.execute(team_member.insert().values(team_id=secret, user_id=member))

    with TestClient(app) as client:
        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Secret plan", "teamId": secret}})["issueCreate"]["issue"]
        ident = made["identifier"]

        # A non-member cannot see the team at all.
        assert secret not in [n["id"] for n in _data(client, OUTSIDER, TEAMS)["teams"]["nodes"]]
        assert _data(client, OUTSIDER, ONE_TEAM, {"id": secret})["team"] is None
        assert _data(client, OUTSIDER, ONE_TEAM, {"id": "SEC"})["team"] is None
        assert _data(client, OUTSIDER, ONE_TEAM, {"id": secret})["team"] is None
        # ... nor its issues, by id or by identifier, by list or by search.
        assert _data(client, OUTSIDER, ONE_ISSUE, {"id": ident})["issue"] is None
        assert _data(client, OUTSIDER, ONE_ISSUE, {"id": made["id"]})["issue"] is None
        found = _data(client, OUTSIDER, ISSUES)["issues"]["nodes"]
        assert made["id"] not in [n["id"] for n in found]
        assert _data(client, OUTSIDER, SEARCH, {"t": "Secret plan"})["searchIssues"]["nodes"] == []
        # ... nor its cadence snapshot.
        assert _gql(client, OUTSIDER, CADENCE, {"t": secret}).get("errors")

        # The explicit member does see it.
        assert secret in [n["id"] for n in
                          _data(client, "hier-insider-token", TEAMS)["teams"]["nodes"]]
        assert _data(client, "hier-insider-token", ONE_ISSUE, {"id": ident})["issue"]["id"] == made["id"]


def test_restricted_sub_team_is_visible_to_parent_members_but_private_is_not(world) -> None:
    engine, app = world["engine"], world["app"]
    restricted = _make_team(engine, world["org"], "RST", "Restricted",
                            visibility="restricted", parent_team_id=world["root"])
    private = _make_team(engine, world["org"], "PRV", "Private",
                         visibility="private", parent_team_id=world["root"])
    with TestClient(app) as client:
        r_issue = _data(client, ADMIN, CREATE_ISSUE,
                        {"i": {"title": "Restricted work", "teamId": restricted}})["issueCreate"]["issue"]
        p_issue = _data(client, ADMIN, CREATE_ISSUE,
                        {"i": {"title": "Private work", "teamId": private}})["issueCreate"]["issue"]

        # Outsider is a member of the parent team only.
        assert restricted in [n["id"] for n in _data(client, OUTSIDER, TEAMS)["teams"]["nodes"]]
        assert private not in [n["id"] for n in _data(client, OUTSIDER, TEAMS)["teams"]["nodes"]]
        assert _data(client, OUTSIDER, ONE_TEAM, {"id": restricted})["team"] is not None
        assert _data(client, OUTSIDER, ONE_ISSUE, {"id": r_issue["identifier"]})["issue"] is not None
        assert _data(client, OUTSIDER, ONE_ISSUE, {"id": p_issue["identifier"]})["issue"] is None


# ---------------------------------------------------------------- decision 1
def test_depth_self_parent_and_cycle_are_rejected(world) -> None:
    engine, app = world["engine"], world["app"]
    a = _make_team(engine, world["org"], "AAA", "A")
    with TestClient(app) as client:
        b = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "B", "key": "BBB", "parentTeamId": a}})["teamCreate"]["team"]["id"]
        c = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "C", "key": "CCC", "parentTeamId": b}})["teamCreate"]["team"]["id"]

        # Depth 3 is the maximum: a child of C would be depth 4.
        deep = _gql(client, ADMIN, CREATE_TEAM, {"input": {"name": "D", "key": "DDD", "parentTeamId": c}})
        assert deep.get("errors"), deep
        assert "depth" in deep["errors"][0]["message"].lower()

        # Self-parent is refused.
        selfp = _gql(client, ADMIN, UPDATE_TEAM, {"id": a, "input": {"parentTeamId": a}})
        assert selfp.get("errors"), selfp
        assert "own parent" in selfp["errors"][0]["message"].lower()

        # A cycle (A under its own descendant C) is refused.
        cycle = _gql(client, ADMIN, UPDATE_TEAM, {"id": a, "input": {"parentTeamId": c}})
        assert cycle.get("errors"), cycle
        assert "cycle" in cycle["errors"][0]["message"].lower() or "descendant" in cycle["errors"][0]["message"].lower()

        # Moving a team that has children cannot exceed depth 3 either.
        g = _make_team(engine, world["org"], "GGG", "G")
        h = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "H", "key": "HHH", "parentTeamId": g}})["teamCreate"]["team"]["id"]
        too_deep = _gql(client, ADMIN, UPDATE_TEAM, {"id": g, "input": {"parentTeamId": b}})
        assert too_deep.get("errors"), too_deep
        assert h  # silence linters about unused key

        # The legal shape is still readable as a tree.
        tree = _data(client, ADMIN, ONE_TEAM, {"id": a})["team"]
        assert [n["id"] for n in tree["children"]["nodes"]] == [b]


def test_departments_do_not_inherit_access(world) -> None:
    """A parent-team member gets no read access to a private child team."""
    engine, app = world["engine"], world["app"]
    child = _make_team(engine, world["org"], "CHD", "Child",
                       visibility="private", parent_team_id=world["root"])
    with TestClient(app) as client:
        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Child only", "teamId": child}})["issueCreate"]["issue"]
        assert child not in [n["id"] for n in _data(client, OUTSIDER, TEAMS)["teams"]["nodes"]]
        assert _data(client, OUTSIDER, ONE_ISSUE, {"id": made["identifier"]})["issue"] is None


# ---------------------------------------------------------------- decision 5
def test_adding_a_sub_team_member_adds_every_ancestor(world) -> None:
    engine, app = world["engine"], world["app"]
    with TestClient(app) as client:
        a = world["root"]
        b = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "B", "key": "BBB", "parentTeamId": a}})["teamCreate"]["team"]["id"]
        c = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "C", "key": "CCC", "parentTeamId": b}})["teamCreate"]["team"]["id"]
        assert _data(client, ADMIN, MEMBER_ADD, {"t": c, "u": world["outsider"]})["teamMemberAdd"]["success"]
    with engine.connect() as conn:
        memberships = {r[0] for r in conn.execute(
            select(team_member.c.team_id).where(team_member.c.user_id == world["outsider"]))}
    assert {a, b, c} <= memberships


def test_removing_a_parent_member_with_a_descendant_membership_is_refused(world) -> None:
    engine, app = world["engine"], world["app"]
    with TestClient(app) as client:
        a = world["root"]
        b = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "B", "key": "BBB", "parentTeamId": a}})["teamCreate"]["team"]["id"]
        c = _data(client, ADMIN, CREATE_TEAM,
                  {"input": {"name": "C", "key": "CCC", "parentTeamId": b}})["teamCreate"]["team"]["id"]
        _data(client, ADMIN, MEMBER_ADD, {"t": c, "u": world["outsider"]})

        refused = _gql(client, ADMIN, MEMBER_DROP, {"t": b, "u": world["outsider"]})
        assert refused.get("errors"), refused
        assert "sub-team" in refused["errors"][0]["message"].lower()

        # No silent cascade: the member is still on b and c.
        with engine.connect() as conn:
            still = {r[0] for r in conn.execute(
                select(team_member.c.team_id).where(team_member.c.user_id == world["outsider"]))}
        assert {b, c} <= still

        # Removing the descendant first makes the ancestor removal legal.
        assert _data(client, ADMIN, MEMBER_DROP, {"t": c, "u": world["outsider"]})["teamMemberRemove"]["success"]
        assert _data(client, ADMIN, MEMBER_DROP, {"t": b, "u": world["outsider"]})["teamMemberRemove"]["success"]


# ---------------------------------------------------------------- decision 1 + un-nest
def test_un_nest_keeps_issues_and_copies_in_use_inherited_labels(world) -> None:
    engine, app = world["engine"], world["app"]
    parent = _make_team(engine, world["org"], "PRT", "Parent")
    child = _make_team(engine, world["org"], "KID", "Child", parent_team_id=parent)
    ts = now_iso()
    used_label, unused_label = new_id(), new_id()
    with engine.begin() as conn:
        for lid, name in ((used_label, "shared"), (unused_label, "parent-only")):
            conn.execute(issue_label.insert().values(
                id=lid, organization_id=world["org"], team_id=parent, name=name,
                color="#123456", created_at=ts, updated_at=ts))

    with TestClient(app) as client:
        # While nested, the child inherits and may use the parent's labels.
        inherited = {n["name"] for n in _data(
            client, ADMIN, LABELS, {"team": child})["issueLabels"]["nodes"]}
        assert {"shared", "parent-only"} <= inherited

        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Child work", "teamId": child,
                            "labelIds": [used_label]}})["issueCreate"]["issue"]

        # Un-nest: the child becomes a root team again.
        un = _data(client, ADMIN, UPDATE_TEAM, {"id": child, "input": {"parentTeamId": None}})
        assert un["teamUpdate"]["success"] is True, un
        assert un["teamUpdate"]["team"]["parentTeamId"] is None

        # The issue survives untouched.
        still = _data(client, ADMIN, ONE_ISSUE, {"id": made["identifier"]})["issue"]
        assert still["id"] == made["id"]

        # The unused inherited label stops applying; the in-use one stays, as a child-owned copy.
        after = {n["name"]: n for n in _data(client, ADMIN, LABELS, {"team": child})["issueLabels"]["nodes"]}
        assert "parent-only" not in after
        assert "shared" in after

        # The parent's original label is no longer usable by the child.
        again = _gql(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "After", "teamId": child, "labelIds": [used_label]}})
        assert again.get("errors"), again

    with engine.connect() as conn:
        rows = conn.execute(
            select(issue_label.c.id, issue_label.c.name, issue_label.c.team_id)
            .select_from(issue_label.join(issue_label_link,
                                          issue_label_link.c.label_id == issue_label.c.id))
            .where(issue_label_link.c.issue_id == made["id"])).all()
        assert len(rows) == 1, rows
        label_id, name, owner = rows[0]
        assert name == "shared"
        assert owner == child, "in-use inherited labels must become child-owned copies"
        assert label_id != used_label
        assert conn.execute(select(issue_label.c.id).where(and_(
            issue_label.c.id == used_label, issue_label.c.team_id == parent))).first() is not None


def test_nesting_closes_the_in_flight_cycle_and_snaps_upcoming_boundaries(world) -> None:
    """Item E: an in-flight sub-team cycle closes; upcoming cycles take the parent's dates."""
    engine, app = world["engine"], world["app"]
    parent = _make_team(engine, world["org"], "PAR", "Parent")
    child = _make_team(engine, world["org"], "CHL", "Child")
    with engine.begin() as conn:
        parent_cycles, child_cycles = {}, {}
        for number in (1, 2, 3):
            pid = new_id()
            parent_cycles[number] = pid
            conn.execute(cycle.insert().values(
                id=pid, organization_id=world["org"], team_id=parent, number=number,
                name=f"Parent {number}", starts_at=f"2026-01-0{number}T00:00:00",
                ends_at=f"2026-01-0{number}T23:59:59", created_at=now_iso(), updated_at=now_iso()))
            cid = new_id()
            child_cycles[number] = cid
            conn.execute(cycle.insert().values(
                id=cid, organization_id=world["org"], team_id=child, number=number,
                name=f"Child {number}", starts_at=f"2026-06-0{number}T00:00:00",
                ends_at=f"2026-06-0{number}T23:59:59", created_at=now_iso(), updated_at=now_iso()))
        # Cycle 1 is the child's in-flight cycle.
        conn.execute(team.update().where(team.c.id == child).values(active_cycle_id=child_cycles[1]))

    with TestClient(app) as client:
        nested = _data(client, ADMIN, UPDATE_TEAM, {"id": child, "input": {"parentTeamId": parent}})
        assert nested["teamUpdate"]["success"] is True, nested

    with engine.connect() as conn:
        rows = {n: (s, e, done) for n, s, e, done in conn.execute(
            select(cycle.c.number, cycle.c.starts_at, cycle.c.ends_at, cycle.c.completed_at)
            .where(cycle.c.team_id == child))}
        active = conn.execute(select(team.c.active_cycle_id).where(team.c.id == child)).scalar()
    assert rows[1][2] is not None, "the in-flight cycle must be completed"
    assert active is None, "the team must no longer point at an active cycle"
    # Upcoming cycles keep their rows but take the nearest parent cycle's boundaries.
    assert rows[2][:2] == ("2026-01-02T00:00:00", "2026-01-02T23:59:59"), rows[2]
    assert rows[3][:2] == ("2026-01-03T00:00:00", "2026-01-03T23:59:59"), rows[3]


# ---------------------------------------------------------------- decision 4
def test_single_org_mode_refuses_workspace_create_and_switch(world, monkeypatch) -> None:
    monkeypatch.setenv("HOJA_SINGLE_ORG", "1")
    app = create_app(str(world["engine"].url.database), open_mode=False)
    with TestClient(app) as client:
        created = _gql(client, ADMIN, WORKSPACE_CREATE,
                       {"input": {"name": "Second", "urlKey": "second"}})
        assert created.get("errors"), created
        assert "single" in created["errors"][0]["message"].lower()

        switched = _gql(client, ADMIN, WORKSPACE_SWITCH, {"id": world["org"]})
        assert switched.get("errors") or switched["data"]["workspaceSwitch"]["success"] is False, switched

        # The ordinary product flow is unaffected.
        made = _data(client, ADMIN, CREATE_ISSUE,
                     {"i": {"title": "Still works", "teamId": world["root"]}})["issueCreate"]
        assert made["success"] is True, made
        assert [n["id"] for n in _data(client, ADMIN, ISSUES)["issues"]["nodes"]] == [made["issue"]["id"]]
        assert [n["key"] for n in _data(client, ADMIN, TEAMS)["teams"]["nodes"]] == ["ENG"]

    with world["engine"].connect() as conn:
        from cliniar_server.db import organization
        # The organization table and the organization_id scope are kept.
        assert conn.execute(select(organization.c.id)).all() == [(world["org"],)]
        assert conn.execute(select(issue.c.organization_id).where(
            issue.c.id == made["issue"]["id"])).scalar() == world["org"]


def test_multi_workspace_mode_still_creates_a_workspace(world, monkeypatch) -> None:
    monkeypatch.delenv("HOJA_SINGLE_ORG", raising=False)
    app = create_app(str(world["engine"].url.database), open_mode=False)
    with TestClient(app) as client:
        created = _gql(client, ADMIN, WORKSPACE_CREATE,
                       {"input": {"name": "Second", "urlKey": "second"}})
        assert created.get("errors") is None, created
        assert created["data"]["workspaceCreate"]["success"] is True, created


# ---------------------------------------------------------------- decision 3
def test_team_member_rows_carry_a_role(world) -> None:
    """The creator holds 'owner'; anyone added later defaults to 'member'."""
    engine = world["engine"]
    with engine.connect() as conn:
        roles = dict(conn.execute(
            select(team_member.c.user_id, team_member.c.role)
            .where(team_member.c.team_id == world["root"])).all())
    assert roles[world["admin"]] == "owner"
    assert roles[world["outsider"]] == "member"
