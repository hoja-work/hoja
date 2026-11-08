"""Decisions: an agent asks, a human answers, the agent reads it back (docs/decisions.md).

These tests pin the four properties the mechanism exists for: the answer is recorded once
and only once (idempotent replay, a different answer refused), the issue visibly flags an
open question and stops flagging it when the last one is settled, every read and write is
scoped to the organization, and the agent-facing read path returns the answer plus the
change-feed events a poller or a browser can wait on.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import (
    api_key,
    inbox_notification,
    issue_decision,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    workflow_state,
)
from hoja_server.db import issue as issue_table
from hoja_server.db import user as user_table

ADMIN = "decisions-admin-token"
MEMBER = "decisions-member-token"
OUTSIDER = "decisions-outsider-token"
READER = "decisions-reader-token"

CREATE = """mutation($i: IssueDecisionCreateInput!) { issueDecisionCreate(input: $i) {
  success decision { id question options context status answered askedByAgent
    askedBy { id } createdAt updatedAt } } }"""
ANSWER = """mutation($id: String!, $i: IssueDecisionAnswerInput!) { issueDecisionAnswer(id: $id, input: $i) {
  success replayed decision { id status answered answer chosenOption answeredBy { id } answeredAt } } }"""
CANCEL = """mutation($id: String!) { issueDecisionCancel(id: $id) { success decision { id status } } }"""
LIST = """query($issueId: String!, $status: String) { issueDecisions(issueId: $issueId, status: $status) {
  id question options status answer chosenOption } }"""
ONE = """query($id: String!) { issueDecision(id: $id) {
  id question status answered answer chosenOption answeredBy { id name } askedByAgent } }"""
ISSUE = """query($id: String!) { issue(id: $id) { id identifier decisionNeeded
  decisions(status: "open") { id question status } } }"""
CREATE_ISSUE = """mutation($i: IssueCreateInput!) { issueCreate(input: $i) {
  success issue { id identifier decisionNeeded } } }"""


def _gql(client, token, query, variables=None):
    return client.post("/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "variables": variables or {}}).json()


def ok(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert "errors" not in body, body
    return body["data"]


def err(client, token, query, variables=None):
    body = _gql(client, token, query, variables)
    assert body.get("errors"), body
    return body["errors"][0]


@pytest.fixture()
def world(tmp_path):
    db_path = tmp_path / "decisions.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Decisions", org_url_key="decisions",
                         user_email="admin@decisions.test", token=ADMIN, demo_issues=False)
    ts = now_iso()
    ids = {"org": seeded["org_id"], "admin": seeded["user_id"]}
    with engine.begin() as conn:
        for name, token in (("member", MEMBER), ("outsider", OUTSIDER), ("reader", READER)):
            uid = new_id()
            ids[name] = uid
            conn.execute(user_table.insert().values(
                id=uid, organization_id=seeded["org_id"], name=name,
                email=f"{name}@decisions.test", active=True, admin=False,
                created_at=ts, updated_at=ts))
            conn.execute(api_key.insert().values(
                id=new_id(), token_hash=token_hash(token), label=name,
                user_id=uid, organization_id=seeded["org_id"], created_at=ts))
        # A private team the outsider cannot see, and a public team everybody can.
        for key, name, visibility, member in (("SEC", "Secret", "private", "member"),
                                             ("OPS", "Ops", "public", None)):
            tid = new_id()
            ids[key.lower()] = tid
            conn.execute(team.insert().values(
                id=tid, organization_id=seeded["org_id"], key=key, name=name,
                visibility=visibility, timezone="UTC", issue_counter=0,
                created_at=ts, updated_at=ts))
            seed_states_for_team(conn, seeded["org_id"], tid)
            if member:
                conn.execute(team_member.insert().values(team_id=tid, user_id=ids[member]))
        ids["ops_state"] = conn.execute(select(workflow_state.c.id).where(and_(
            workflow_state.c.team_id == ids["ops"],
            workflow_state.c.name == "In Progress"))).scalar()
    # A second, unrelated organization: its rows must stay invisible to this one.
    other = seed_tenant(engine, org_name="Other", org_url_key="other",
                        user_email="admin@other.test", token="decisions-other-token",
                        demo_issues=False)
    ids["other_org"] = other["org_id"]
    ids["other_user"] = other["user_id"]
    with engine.begin() as conn:
        ids["other_team"] = conn.execute(select(team.c.id).where(and_(
            team.c.organization_id == other["org_id"], team.c.key == "ENG"))).scalar()
    return {"engine": engine, "app": create_app(str(db_path), open_mode=False), **ids}


def make_issue(client, team_key, title="Ship it"):
    data = ok(client, ADMIN, CREATE_ISSUE, {"i": {"teamId": team_key, "title": title}})
    assert data["issueCreate"]["success"]
    return data["issueCreate"]["issue"]


def ask(client, token, issue_ref, **payload):
    body = {"issueId": issue_ref, **payload}
    data = ok(client, token, CREATE, {"i": body})
    assert data["issueDecisionCreate"]["success"]
    return data["issueDecisionCreate"]["decision"]


# --------------------------------------------------------------- model + flag

def test_create_and_answer_round_trip_flags_the_issue(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS", "Which Mor label wins?")
        assert issue["decisionNeeded"] is False

        created = ask(client, ADMIN, issue["identifier"],
                      question="Are there two Mor labels, or one reused?",
                      options=[{"key": "two", "label": "Two labels", "description": "Keep both"},
                               {"key": "one", "label": "One label"}],
                      context="HOJA-98 names two different concepts 'Mor'.",
                      agent="claw:run-30d5")
        assert created["status"] == "open" and created["answered"] is False
        assert created["askedByAgent"] == "claw:run-30d5"
        assert [o["key"] for o in created["options"]] == ["two", "one"]

        flagged = ok(client, ADMIN, ISSUE, {"id": issue["identifier"]})["issue"]
        assert flagged["decisionNeeded"] is True
        assert [d["id"] for d in flagged["decisions"]] == [created["id"]]

        answered = ok(client, MEMBER, ANSWER,
                      {"id": created["id"], "i": {"option": "two"}})["issueDecisionAnswer"]
        assert answered["success"] and answered["replayed"] is False
        assert answered["decision"]["status"] == "answered"
        assert answered["decision"]["answered"] is True
        assert answered["decision"]["chosenOption"] == "two"
        # Choosing an option records its label as the answer, so the record reads on its own.
        assert answered["decision"]["answer"] == "Two labels"
        assert answered["decision"]["answeredBy"]["id"] == world["member"]
        assert answered["decision"]["answeredAt"]

        settled = ok(client, ADMIN, ISSUE, {"id": issue["identifier"]})["issue"]
        assert settled["decisionNeeded"] is False
        assert settled["decisions"] == []


def test_free_form_decision_requires_an_answer_and_keeps_text(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Which repo owns this?")
        assert created["options"] == []

        refusal = err(client, MEMBER, ANSWER, {"id": created["id"], "i": {}})
        assert "answer is required" in refusal["message"]
        assert refusal["extensions"]["field"] == "answer"

        answered = ok(client, MEMBER, ANSWER,
                      {"id": created["id"], "i": {"answer": "hoja-cloud"}})["issueDecisionAnswer"]
        assert answered["decision"]["answer"] == "hoja-cloud"
        assert answered["decision"]["chosenOption"] is None


def test_an_option_is_required_when_the_decision_offers_options(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Pick one",
                      options=[{"label": "Alpha"}, {"label": "Beta"}])
        # Keys are generated from the labels when the caller names only labels.
        assert [o["key"] for o in created["options"]] == ["alpha-1", "beta-2"]

        refusal = err(client, MEMBER, ANSWER,
                      {"id": created["id"], "i": {"answer": "just text"}})
        assert "option is required" in refusal["message"]
        assert refusal["extensions"]["code"] == "BAD_USER_INPUT"

        bad = err(client, MEMBER, ANSWER, {"id": created["id"], "i": {"option": "gamma"}})
        assert "is not one of" in bad["message"]

        # A label resolves to its stored key.
        answered = ok(client, MEMBER, ANSWER,
                      {"id": created["id"], "i": {"option": "Beta"}})["issueDecisionAnswer"]
        assert answered["decision"]["chosenOption"] == "beta-2"
        assert answered["decision"]["answer"] == "Beta"


# ------------------------------------------------------------------ idempotency

def test_answering_twice_is_idempotent_and_a_different_answer_is_refused(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Proceed?",
                      options=[{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}])

        first = ok(client, MEMBER, ANSWER,
                   {"id": created["id"], "i": {"option": "yes"}})["issueDecisionAnswer"]
        assert first["replayed"] is False
        answered_at = first["decision"]["answeredAt"]

        replay = ok(client, MEMBER, ANSWER,
                    {"id": created["id"], "i": {"option": "yes"}})["issueDecisionAnswer"]
        assert replay["success"] is True and replay["replayed"] is True
        assert replay["decision"]["answeredAt"] == answered_at
        assert replay["decision"]["answeredBy"]["id"] == world["member"]

        # A different answer does not overwrite the human's choice.
        conflict = err(client, READER, ANSWER,
                       {"id": created["id"], "i": {"option": "no"}})
        assert conflict["extensions"]["code"] == "ALREADY_ANSWERED"
        stored = ok(client, READER, ONE, {"id": created["id"]})["issueDecision"]
        assert stored["chosenOption"] == "yes"
        assert stored["answeredBy"]["id"] == world["member"]


def test_only_the_last_open_decision_clears_the_flag(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        one = ask(client, ADMIN, issue["identifier"], question="First?",
                  options=[{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
        two = ask(client, ADMIN, issue["identifier"], question="Second?",
                  options=[{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])

        ok(client, MEMBER, ANSWER, {"id": one["id"], "i": {"option": "a"}})
        still = ok(client, ADMIN, ISSUE, {"id": issue["identifier"]})["issue"]
        assert still["decisionNeeded"] is True
        assert [d["id"] for d in still["decisions"]] == [two["id"]]

        ok(client, MEMBER, ANSWER, {"id": two["id"], "i": {"option": "b"}})
        settled = ok(client, ADMIN, ISSUE, {"id": issue["identifier"]})["issue"]
        assert settled["decisionNeeded"] is False


# ------------------------------------------------------------- authorization

def test_a_canceled_decision_cannot_be_answered_and_clears_the_flag(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Still needed?")

        # A member who did not ask and is not an admin may not withdraw it.
        forbidden = err(client, MEMBER, CANCEL, {"id": created["id"]})
        assert forbidden["extensions"]["code"] == "FORBIDDEN"

        canceled = ok(client, ADMIN, CANCEL, {"id": created["id"]})["issueDecisionCancel"]
        assert canceled["success"] and canceled["decision"]["status"] == "canceled"

        refusal = err(client, MEMBER, ANSWER, {"id": created["id"], "i": {"answer": "late"}})
        assert "canceled" in refusal["message"]
        assert ok(client, ADMIN, ISSUE, {"id": issue["identifier"]})["issue"]["decisionNeeded"] is False


def test_team_visibility_governs_read_create_and_answer(world):
    with TestClient(world["app"]) as client:
        secret_issue = make_issue(client, "SEC", "Secret work")
        created = ask(client, MEMBER, secret_issue["identifier"], question="Which way?",
                      options=[{"key": "left", "label": "Left"}, {"key": "right", "label": "Right"}])

        # The outsider cannot see the issue, so the decision does not exist for them.
        assert err(client, OUTSIDER, ONE, {"id": created["id"]})["extensions"]["code"] == "NOT_FOUND"
        assert ok(client, OUTSIDER, LIST, {"issueId": secret_issue["identifier"]})["issueDecisions"] == []
        assert err(client, OUTSIDER, ANSWER,
                   {"id": created["id"], "i": {"option": "left"}})["extensions"]["code"] == "NOT_FOUND"
        assert err(client, OUTSIDER, CREATE,
                   {"i": {"issueId": secret_issue["identifier"],
                          "question": "can I ask?"}})["extensions"]["code"] == "NOT_FOUND"

        # The member on the team can ask and answer on their own issue.
        mine = ask(client, MEMBER, secret_issue["identifier"], question="And now?")
        ok(client, MEMBER, ANSWER, {"id": mine["id"], "i": {"answer": "fine"}})


def test_another_organization_decision_is_not_found(world):
    other_issue_id, foreign = new_id(), new_id()
    with world["engine"].begin() as conn:
        conn.execute(issue_table.insert().values(
            id=other_issue_id, organization_id=world["other_org"], team_id=world["other_team"],
            number=1, identifier="ENG-1", title="Other org work", priority=0,
            created_at=now_iso(), updated_at=now_iso()))
        conn.execute(issue_decision.insert().values(
            id=foreign, organization_id=world["other_org"], issue_id=other_issue_id,
            question="leak?", options="[]", status="open",
            created_at=now_iso(), updated_at=now_iso()))

    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Scoped?")

        assert err(client, ADMIN, ONE, {"id": foreign})["extensions"]["code"] == "NOT_FOUND"
        assert err(client, ADMIN, ANSWER,
                   {"id": foreign, "i": {"answer": "no"}})["extensions"]["code"] == "NOT_FOUND"
        names = [d["id"] for d in ok(client, ADMIN, LIST,
                                     {"issueId": issue["identifier"]})["issueDecisions"]]
        assert names == [created["id"]]


# --------------------------------------------------------------- agent read path

def test_the_waiting_agent_reads_the_answer_and_the_change_feed_sees_it(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        created = ask(client, ADMIN, issue["identifier"], question="Is View first-class?",
                      agent="claw:run-30d5")

        # Poll shape: open, unanswered.
        pending = ok(client, MEMBER, ONE, {"id": created["id"]})["issueDecision"]
        assert pending["status"] == "open" and pending["answered"] is False
        assert pending["askedByAgent"] == "claw:run-30d5"

        ok(client, MEMBER, ANSWER, {"id": created["id"], "i": {"option": "first-class"}}
           if False else {"id": created["id"], "i": {"answer": "first-class"}})

        # The same poll now returns the answer and who gave it.
        answered = ok(client, MEMBER, ONE, {"id": created["id"]})["issueDecision"]
        assert answered["status"] == "answered" and answered["answered"] is True
        assert answered["answer"] == "first-class"
        assert answered["answeredBy"]["name"] == "member"

        # The change feed carries the decision row and the issue flag it flipped.
        from hoja_server.db import change_event

        with world["engine"].connect() as conn:
            events = conn.execute(select(change_event.c.entity, change_event.c.action,
                                          change_event.c.entity_id)
                                  .where(change_event.c.organization_id == world["org"])
                                  .order_by(change_event.c.seq)).all()
        decision_events = [(e, a) for e, a, _ in events if e == "issueDecision"]
        assert ("issueDecision", "create") in decision_events
        assert ("issueDecision", "update") in decision_events
        assert any(e == "issue" and a == "update" and i == issue["id"] for e, a, i in events)


def test_decision_is_announced_in_the_inbox(world):
    with TestClient(world["app"]) as client:
        # The member created the issue, so the admin's question lands in the member's inbox.
        data = ok(client, MEMBER, CREATE_ISSUE, {"i": {"teamId": "OPS", "title": "Member work"}})
        issue = data["issueCreate"]["issue"]
        ask(client, ADMIN, issue["identifier"], question="Heads up?")

        with world["engine"].connect() as conn:
            kinds = [r[0] for r in conn.execute(select(inbox_notification.c.kind).where(and_(
                inbox_notification.c.organization_id == world["org"],
                inbox_notification.c.issue_id == issue["id"]))).all()]
        assert "issueDecision" in kinds


def test_sync_snapshot_carries_the_flag(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        ask(client, ADMIN, issue["identifier"], question="Flagged?")

        response = client.get("/sync/bootstrap", headers={"Authorization": f"Bearer {ADMIN}"})
        assert response.status_code == 200, response.text
        body = response.json()
        columns = body["columns"]
        at = columns.index("decisionNeeded")
        row = next(r for r in body["issues"] if r[0] == issue["id"])
        assert row[at] in (True, 1)


# ------------------------------------------------------------------ validation

def test_create_validation_names_the_offending_field(world):
    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")

        blank = err(client, ADMIN, CREATE, {"i": {"issueId": issue["identifier"], "question": "  "}})
        assert blank["extensions"]["field"] == "question"

        too_long = err(client, ADMIN, CREATE, {"i": {
            "issueId": issue["identifier"], "question": "x" * 501}})
        assert "at most 500" in too_long["message"]

        duplicate = err(client, ADMIN, CREATE, {"i": {
            "issueId": issue["identifier"], "question": "dup keys",
            "options": [{"key": "same", "label": "A"}, {"key": "same", "label": "B"}]}})
        assert "used twice" in duplicate["message"]

        missing = err(client, ADMIN, CREATE, {"i": {
            "issueId": "ENG-999999", "question": "gone?"}})
        assert missing["extensions"]["code"] == "NOT_FOUND"


def test_an_issue_has_a_ceiling_of_open_decisions(world):
    from hoja_server import decisions as decisiondocs

    with TestClient(world["app"]) as client:
        issue = make_issue(client, "OPS")
        for n in range(decisiondocs.MAX_OPEN_PER_ISSUE):
            ask(client, ADMIN, issue["identifier"], question=f"q{n}")
        refusal = err(client, ADMIN, CREATE, {"i": {
            "issueId": issue["identifier"], "question": "one too many"}})
        assert "open decisions" in refusal["message"]
