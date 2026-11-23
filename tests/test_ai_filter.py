"""AI filters: the plain-language evaluation set and the model tier's contract.

``fixtures/ai_filter_eval.json`` holds 54 requests a triage lead types, each with the filter
it must produce, written before the parser. Six are too vague to filter on; for those the
right answer is no filter. The rules tier must get at least 90% of the set right and must
never answer a vague request. The score is written to $AI_FILTER_EVAL_OUT when set
(artifacts/runs/20261123-views-depth/proof/ai_filter_eval.json in this run).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select

from hoja_server import ai_filter
from hoja_server import views as viewdocs
from hoja_server.db import (
    issue_label,
    make_engine,
    migrate,
    new_id,
    now_iso,
    project,
    seed_tenant,
    team,
    team_member,
)
from hoja_server.db import user as user_table
from hoja_server.store import Store

EVAL = json.loads((Path(__file__).parent / "fixtures" / "ai_filter_eval.json").read_text())


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    engine = make_engine(tmp_path_factory.mktemp("ai") / "ai.db")
    migrate(engine)
    seeded = seed_tenant(engine, org_name="AI", org_url_key="ai", user_name="Lead",
                         user_email="lead@ai.test", demo_issues=False)
    org, me = seeded["org_id"], seeded["user_id"]
    ids = {"user": {"Lead": me}, "label": {}, "state": {}, "project": {}}
    ts = now_iso()
    with engine.begin() as conn:
        tid = conn.execute(select(team.c.id).where(team.c.organization_id == org)).scalar()
        conn.execute(team_member.insert().prefix_with("OR IGNORE").values(team_id=tid, user_id=me, role="member"))
        for name in ("Alice", "Bob"):
            uid = new_id()
            ids["user"][name] = uid
            conn.execute(user_table.insert().values(
                id=uid, organization_id=org, name=name, display_name=name,
                email=f"{name.lower()}@ai.test", active=True, admin=False,
                created_at=ts, updated_at=ts))
        from hoja_server.db import workflow_state
        for sid, name in conn.execute(select(workflow_state.c.id, workflow_state.c.name)
                                      .where(workflow_state.c.team_id == tid)):
            ids["state"][name] = sid
        for spec in EVAL["workspace"]["labels"]:
            if isinstance(spec, str):
                lid = new_id()
                ids["label"][spec] = lid
                conn.execute(issue_label.insert().values(id=lid, organization_id=org, name=spec,
                                                         created_at=ts, updated_at=ts))
            else:
                gid = new_id()
                ids["label"][spec["group"]] = gid
                conn.execute(issue_label.insert().values(id=gid, organization_id=org,
                                                         name=spec["group"], is_group=True,
                                                         created_at=ts, updated_at=ts))
                for child in spec["children"]:
                    lid = new_id()
                    ids["label"][child] = lid
                    conn.execute(issue_label.insert().values(
                        id=lid, organization_id=org, name=child, parent_id=gid,
                        created_at=ts, updated_at=ts))
        for name in EVAL["workspace"]["projects"]:
            pid = new_id()
            ids["project"][name] = pid
            conn.execute(project.insert().values(
                id=pid, organization_id=org, name=name, slug_id=name.lower().replace(" ", "-"),
                state="started", progress=0, created_at=ts, updated_at=ts))
    return {"store": Store(engine), "org": org, "me": me, "team": tid, "ids": ids}


def _resolve(value, ids):
    if isinstance(value, str) and value.startswith("$") and ":" in value:
        kind, name = value[1:].split(":", 1)
        return ids[kind][name]
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


_SINGLE = {"isEitherOf": "is", "isNeitherOf": "isNot"}


def _canonical(doc):
    """Order-insensitive, and a one-item isEitherOf/isNeitherOf is the is/isNot it means."""
    if isinstance(doc, dict):
        doc = {(_SINGLE[k] if k in _SINGLE and isinstance(v, list) and len(v) == 1 else k):
               (v[0] if k in _SINGLE and isinstance(v, list) and len(v) == 1 else v)
               for k, v in doc.items()}
        return {k: _canonical(v) for k, v in sorted(doc.items())}
    if isinstance(doc, list):
        items = [_canonical(v) for v in doc]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
    return doc


def test_the_evaluation_set_is_valid_grammar(ws):
    for case in EVAL["cases"]:
        if case["expected"] is not None:
            viewdocs.parse_filters("issue", _resolve(case["expected"], ws["ids"]))


def test_rules_tier_accuracy(ws):
    results, right, abstained_wrongly = [], 0, []
    started = time.perf_counter()
    for case in EVAL["cases"]:
        got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], case["q"], provider={})
        want = case["expected"]
        if want is None:
            ok = got.filters is None
            if not ok:
                abstained_wrongly.append(case["q"])
        else:
            want = viewdocs.parse_filters("issue", _resolve(want, ws["ids"]))
            ok = got.filters is not None and _canonical(got.filters) == _canonical(want)
        right += ok
        results.append({"q": case["q"], "ok": ok, "got": got.filters, "unmatched": got.unmatched,
                        "source": got.source})
    elapsed_ms = (time.perf_counter() - started) * 1000
    score = right / len(EVAL["cases"])
    out = os.environ.get("AI_FILTER_EVAL_OUT")
    if out:
        Path(out).write_text(json.dumps({"cases": len(EVAL["cases"]), "right": right, "accuracy": score,
                                         "ms_total": round(elapsed_ms, 1),
                                         "results": results}, indent=1, default=str))
    misses = [r for r in results if not r["ok"]]
    assert not abstained_wrongly, abstained_wrongly
    assert score >= 0.9, (score, misses)


class _FakeModel:
    """An OpenAI-compatible client whose replies are scripted."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": text})()})()]})()


PROVIDER = {"model_id": "fake", "base_url": "http://model.invalid", "encrypted_api_key": ""}


def test_model_tier_is_used_for_leftover_words_and_validated(ws):
    fake = _FakeModel(['{"filters": {"mood": {"is": "tense"}}}',  # invalid: retried with the reason
                       '{"filters": {"priority": {"is": 1}}}'])
    got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], "the stuff blocking the launch",
                            provider=PROVIDER, client_factory=lambda: fake)
    assert got.source == "model" and got.filters == {"priority": {"is": 1}}
    assert len(fake.calls) == 2
    assert "rejected" in fake.calls[1]["messages"][-1]["content"]
    vocab = json.loads(fake.calls[0]["messages"][1]["content"])["vocabulary"]
    assert "bug" in vocab["labels"] and "todo" in vocab["states"]


def test_model_tier_can_abstain_and_failures_fall_back(ws):
    fake = _FakeModel(['{"filters": null}'])
    got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], "what should I work on next",
                            provider=PROVIDER, client_factory=lambda: fake)
    assert got.filters is None and got.source == "none"
    fake = _FakeModel(["not json", "still not json"])
    got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], "unassigned bugs about payments",
                            provider=PROVIDER, client_factory=lambda: fake)
    assert got.source == "rules" and got.filters["assignee"] == {"is": None} and got.message

    def boom():
        raise RuntimeError("down")
    got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], "unassigned bugs about payments",
                            provider=PROVIDER, client_factory=boom)
    assert got.filters and got.message == "The model did not answer."


def test_rules_never_call_the_model_when_everything_was_understood(ws):
    fake = _FakeModel([])
    got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], "overdue tasks assigned to me",
                            provider=PROVIDER, client_factory=lambda: fake)
    assert got.source == "rules" and not fake.calls


def test_graphql_surfaces(tmp_path):
    from starlette.testclient import TestClient

    from hoja_server.app import create_app

    db = tmp_path / "g.db"
    engine = make_engine(db)
    migrate(engine)
    seed_tenant(engine, org_name="G", org_url_key="g", token="g-token", demo_issues=False)
    with TestClient(create_app(str(db), open_mode=False)) as client:
        def q(query, variables):
            return client.post("/graphql", headers={"Authorization": "Bearer g-token"},
                               json={"query": query, "variables": variables}).json()
        body = q("query($p: String!) { viewFilterSuggestion(prompt: $p) { filters source unmatched message } }",
                 {"p": "unassigned urgent tasks created in the last 2 weeks"})
        got = body["data"]["viewFilterSuggestion"]
        assert got["source"] == "rules" and got["unmatched"] == []
        assert got["filters"] == {"assignee": {"is": None}, "priority": {"is": 1},
                                  "createdAt": {"after": "-P2W"}}
        linear = q("query($p: String!) { issueFilterSuggestion(prompt: $p) { filter logId } }",
                   {"p": "unassigned urgent tasks"})["data"]["issueFilterSuggestion"]["filter"]
        assert "assignee" in json.dumps(linear) and "priority" in json.dumps(linear)
        none = q("query($p: String!) { viewFilterSuggestion(prompt: $p) { filters source message } }",
                 {"p": "whatever is on fire"})["data"]["viewFilterSuggestion"]
        assert none["filters"] is None and none["source"] == "none" and none["message"]


@pytest.mark.skipif(not os.environ.get("AI_FILTER_HELDOUT"), reason="held-out set is scored on demand")
def test_heldout_report(ws):
    """Score a held-out set written without access to the parser; reports, never fails."""
    held = json.loads(Path(os.environ["AI_FILTER_HELDOUT"]).read_text())
    rows, right = [], 0
    for case in held["cases"]:
        got = ai_filter.suggest(ws["store"], ws["org"], ws["me"], case["q"], provider={})
        want = case["expected"]
        if want is None:
            ok = got.filters is None
        else:
            want = viewdocs.parse_filters("issue", _resolve(want, ws["ids"]))
            ok = got.filters is not None and _canonical(got.filters) == _canonical(want)
        right += ok
        outcome = "right" if ok else ("abstained" if got.filters is None else "wrong")
        rows.append({"q": case["q"], "ok": ok, "outcome": outcome, "got": got.filters,
                     "want": want, "unmatched": got.unmatched, "note": case.get("note")})
    out = os.environ.get("AI_FILTER_HELDOUT_OUT")
    if out:
        tally = {k: sum(r["outcome"] == k for r in rows) for k in ("right", "abstained", "wrong")}
        Path(out).write_text(json.dumps({"cases": len(rows), "right": right, **{f"n_{k}": v for k, v in tally.items()},
                                         "accuracy": right / len(rows), "results": rows},
                                        indent=1, default=str))
