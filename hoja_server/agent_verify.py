"""Verification: a claim is not a completion until a separate principal signs it.

The recorded failure this module answers is not "agents lie". It is that a run's
completion was *self-declared*: 153 of 166 Claw runs were never judged
(``goal.ts:203``), five runs a judge had passed were later recorded as cancelled,
and three runs shipped to production while the ledger called them lost
(``agent-security/03-scenarios.md`` §1, classes D1a and D2). A finish therefore has
two parts, and they belong to two different principals:

* the **claim** — what the runner says it did, with the references it points at.
  ``agent_sessions.submit_claim`` stores it once and moves the session to
  ``verifying``; nothing on that path can reach ``done``.
* the **verdict** — what a verifier who did *not* do the work finds when it re-reads
  that evidence. ``met`` is the only verdict that moves the task to In Review.

Three properties are load-bearing and each is enforced here rather than documented:

**Separation.** The verifier must be a different principal from the worker and must
not be the same model. A verifier that shares either is refused outright, before any
row is written, because a shared principal is not a second opinion.

**No CI.** This project runs no CI (``app/AGENTS.md``: branch protection, no
dependency on GitHub Actions), so a verifier that read check status would be reading
a signal that does not exist. The checks re-read Hoja's own records — the linked pull
request and its diff, the issue's acceptance list, whether the pull request is merged
on main, and which revision is deployed — and a check whose evidence is absent
reports ``uncertain``, never ``met``.

**Uncertainty is a real answer.** Missing evidence produces ``uncertain`` and leaves
the session awaiting verification rather than defaulting to success. A verdict can be
weaker than what the verifier asked for, never stronger.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from graphql import GraphQLError
from sqlalchemy import and_, desc, select

from hoja_server import agent_sessions as sessions
from hoja_server import writer as writer_module
from hoja_server.db import (
    agent_member,
    agent_session,
    agent_verification,
    github_link,
    issue,
    new_id,
    now_iso,
    user,
    workflow_state,
)

# The three verdicts. ``met`` reaches In Review; ``not_met`` fails the session;
# ``uncertain`` is recorded and the session keeps waiting. There is no fourth.
VERDICTS = ("met", "not_met", "uncertain")
# How much each verdict can claim. A recorded verdict is the weaker of what the
# verifier asked for and what the evidence supports, so evidence can never be
# outvoted by confidence.
STRENGTH = {"met": 2, "uncertain": 1, "not_met": 0}

PASSED, FAILED, MISSING = "passed", "failed", "uncertain"
# The checks a verdict carries. Deliberately no CI entry: there is no CI.
CHECK_ORDER = ("principal_separation", "acceptance_list", "pull_request",
               "merged_on_main", "deployed")

# Where the deployment reports the revision it is serving. Absent means the check
# cannot be answered, which is ``uncertain`` — never a pass.
LIVE_REVISION_ENV = "HOJA_LIVE_REVISION"

IN_REVIEW_STATE = "In Review"

_ACCEPTANCE_HEADING = re.compile(r"^#{1,6}\s*acceptance(\s+criteria)?\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")


class VerificationRefusedError(GraphQLError):
    """A verdict this module will not record, with the reason as structured data."""


def _refuse(why: str, **extensions) -> None:
    raise VerificationRefusedError(
        f"verification refused: {why}",
        extensions={"code": "FORBIDDEN", "field": "verifier", "reason": why, **extensions},
    )


@dataclass(frozen=True)
class Principal:
    """Who is acting, and with what.

    ``read_only`` is the property the spec names. It is a declaration about the path
    the principal may take: the verifier re-reads evidence and writes one ledger row,
    and there is no function in this module that edits the work product.
    """

    id: str
    kind: str  # "human" | "agent" | "bot"
    model: str | None = None
    read_only: bool = False


def principal_for(conn, org_id: str, user_id: str, *, model: str | None = None) -> Principal:
    """The caller as a verifier candidate, with the model Hoja knows about it.

    An agent member's configured model is the fallback when the caller does not
    name one: a client cannot make itself look independent by omitting a field.
    """
    row = conn.execute(select(user.c.id).where(user.c.id == user_id)).first()
    if not row:
        _refuse("the caller is not a principal in this workspace")
    member = conn.execute(select(agent_member.c.model).where(and_(
        agent_member.c.organization_id == org_id,
        agent_member.c.user_id == user_id,
    ))).first()
    if member is not None:
        return Principal(id=str(user_id), kind="agent", model=model or member[0], read_only=True)
    return Principal(id=str(user_id), kind="human", model=model, read_only=True)


def worker_principal(conn, session: dict) -> Principal:
    """The principal that produced the claim.

    The model the runner reported wins over the member's configured model, because
    the claim is about the run that happened; configuration is only the fallback.
    """
    model = session.get("worker_model")
    if not model:
        row = conn.execute(select(agent_member.c.model).where(and_(
            agent_member.c.organization_id == session["organization_id"],
            agent_member.c.user_id == session["agent_user_id"],
        ))).first()
        model = row[0] if row else None
    return Principal(id=str(session["agent_user_id"]), kind="agent", model=model)


def independence_problems(verifier: Principal, worker: Principal) -> list[str]:
    """Every reason this verifier is not a second opinion. Empty means it is one."""
    problems: list[str] = []
    if not verifier.id or not worker.id:
        return ["the worker principal is unknown, so separation cannot be proved"]
    if not verifier.read_only:
        problems.append("the verifier may write, so it is not a read-only reviewer")
    if verifier.kind == "bot":
        problems.append("a bot may not verify: it has no judgement to lend")
    if verifier.id == worker.id:
        problems.append("the verifier is the worker's own principal")
    if verifier.model and worker.model and verifier.model == worker.model:
        problems.append("the verifier runs the worker's model")
    if verifier.kind == "agent":
        # Model separation is about a model grading itself, so it is the agent
        # verifier's obligation. A person reviewing the work is not a model, and
        # requiring them to declare one would be a form to fill in, not a check.
        if not verifier.model:
            problems.append("the verifier's model is unknown")
        if not worker.model:
            problems.append("the worker's model is unknown, so model separation cannot be proved")
    return problems


def acceptance_items(description: str | None) -> list[str]:
    """The bullet list under an "Acceptance" heading, verbatim.

    The issue body is the authority for what counts as done (the work-package
    prompt says so explicitly), so the verifier reads the list from the task rather
    than from the claim. A description with no such heading yields no items, which
    makes the check ``uncertain`` instead of vacuously true.
    """
    if not description:
        return []
    items: list[str] = []
    inside = False
    for line in description.splitlines():
        if _ACCEPTANCE_HEADING.match(line.strip()):
            inside = True
            continue
        if inside and _HEADING.match(line.strip()):
            break
        if inside:
            match = _BULLET.match(line)
            if match:
                items.append(match.group(1))
    return items


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _claimed_items(claim: dict) -> list[tuple[str, str]]:
    """The claim's acceptance answers as (item, evidence) pairs.

    Both shapes a caller might send are accepted — a list of strings and a list of
    ``{item, evidence}`` objects — because the finish route is the runner's, and a
    shape mismatch there should not silently turn into "everything was covered".
    """
    out: list[tuple[str, str]] = []
    raw = claim.get("acceptance")
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if isinstance(entry, str):
            out.append((entry, ""))
        elif isinstance(entry, dict):
            out.append((str(entry.get("item") or ""), str(entry.get("evidence") or "")))
    return out


def _pull_request_link(conn, org_id: str, issue_id: str, claim: dict) -> dict | None:
    """The pull request this task is linked to, preferring the one the claim names."""
    rows = conn.execute(select(github_link).where(and_(
        github_link.c.organization_id == org_id,
        github_link.c.issue_id == issue_id,
    )).order_by(desc(github_link.c.updated_at))).mappings().all()
    links = [dict(r) for r in rows if str(r["kind"]).lower() in ("pull_request", "pull-request")]
    if not links:
        return None
    wanted = str((claim.get("pull_request") or {}).get("url")
                 or (claim.get("pull_request") or {}).get("number") or "")
    if not wanted:
        return links[0]
    for link in links:
        if wanted == str(link.get("url")) or wanted in str(link.get("url", "")):
            return link
        if wanted == str(link.get("external_id")):
            return link
    return None


def _diff_files(link: dict) -> list:
    try:
        parsed = json.loads(link.get("files") or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def check_claim(conn, org_id: str, session: dict, *, live_revision: str | None = None,
                verifier: Principal | None = None) -> list[dict]:
    """Re-read the evidence the claim points at, and say what each check found.

    Read-only by construction: every check is a SELECT against Hoja's own records.
    No check consults GitHub Actions, check suites or workflow runs, because this
    project has none; the pull request row it reads is the link Hoja captured from
    the webhook, and its ``files`` column is the diff an enrichment pass recorded.
    """
    claim = sessions.parse_claim(session) or {}
    checks: list[dict] = []

    def add(name: str, outcome: str, detail: str) -> None:
        checks.append({"name": name, "outcome": outcome, "detail": detail})

    if verifier is not None:
        worker = worker_principal(conn, session)
        add("principal_separation", PASSED,
            f"verifier {verifier.id} ({verifier.kind}, model {verifier.model or 'undeclared'}) "
            f"is not the worker {worker.id} (model {worker.model or 'unknown'})")

    # 2. The issue's acceptance list, answered item by item.
    description = conn.execute(select(issue.c.description).where(and_(
        issue.c.organization_id == org_id, issue.c.id == session["issue_id"]))).scalar()
    expected = acceptance_items(description)
    answered = {_norm(item): evidence for item, evidence in _claimed_items(claim)}
    if not expected:
        add("acceptance_list", MISSING,
            "the task carries no acceptance list, so there is nothing to check a claim against")
    else:
        missing = [item for item in expected if _norm(item) not in answered]
        if missing:
            add("acceptance_list", FAILED,
                "the claim does not answer: " + "; ".join(missing[:5]))
        else:
            add("acceptance_list", PASSED,
                f"all {len(expected)} acceptance item(s) are answered by the claim")

    # 3. The pull request, re-read now rather than trusted as claimed (class D8).
    pr_claim = claim.get("pull_request") if isinstance(claim.get("pull_request"), dict) else {}
    link = _pull_request_link(conn, org_id, session["issue_id"], claim)
    if link is None:
        add("pull_request", MISSING,
            "no pull-request link is recorded for this task, so the claim cannot be re-read")
    else:
        claimed_state = str(pr_claim.get("state") or "").strip().lower()
        merged = bool(link.get("merged_at")) or str(link.get("state") or "").upper() == "MERGED"
        files = _diff_files(link)
        if claimed_state in ("open", "draft") and merged:
            add("pull_request", FAILED,
                f"the claim says {claimed_state} but the link is merged at {link['merged_at']}")
        elif not files:
            add("pull_request", MISSING,
                f"the link {link.get('url')} carries no recorded diff, so nothing was re-read")
        else:
            add("pull_request", PASSED,
                f"{link.get('url')} is {'merged' if merged else str(link.get('state') or 'open')} "
                f"with {len(files)} changed file(s)")

        # 4. Merged on main: a claim of completion means the work is on the trunk.
        merged_sha = str(pr_claim.get("merged_sha") or claim.get("merged_sha")
                         or link.get("sha") or "")
        if not merged:
            add("merged_on_main", FAILED,
                "the pull request is not merged, so nothing is on main")
        elif not merged_sha:
            add("merged_on_main", MISSING,
                "the link records no merge commit, so main cannot be tied to it")
        else:
            add("merged_on_main", PASSED,
                f"merged at {link.get('merged_at')} as {merged_sha}")

    # 5. Deployed: the revision the deployment reports, never a CI conclusion.
    live = live_revision or os.environ.get(LIVE_REVISION_ENV) or None
    claimed_deploy = str(claim.get("deployed_revision") or "")
    if not live:
        add("deployed", MISSING,
            f"no deployed revision is configured ({LIVE_REVISION_ENV}), so deployment is unproved")
    elif claimed_deploy and not live.startswith(claimed_deploy) and claimed_deploy != live:
        add("deployed", FAILED, f"the deployment serves {live}, the claim says {claimed_deploy}")
    else:
        add("deployed", PASSED, f"the deployment serves {live}")

    order = {name: index for index, name in enumerate(CHECK_ORDER)}
    checks.sort(key=lambda c: order.get(c["name"], len(order)))
    return checks


def ceiling(checks: list[dict]) -> str:
    """The strongest verdict the evidence supports: a contradiction fails, a gap doubts."""
    if any(check["outcome"] == FAILED for check in checks):
        return "not_met"
    if any(check["outcome"] == MISSING for check in checks):
        return "uncertain"
    return "met"


def _move_task_to_in_review(conn, org_id: str, issue_id: str, actor_id: str | None,
                            at: str) -> dict:
    """Move the task to In Review — the one and only consequence of a ``met`` verdict.

    A team that renamed its In Review state still gets the move: the state is found
    by name first, then by the started-type state the seed order puts first. When
    neither exists the task is left where it is and the caller is told, rather than
    the verdict silently failing to reach the board.
    """
    team_id = conn.execute(select(issue.c.team_id).where(and_(
        issue.c.organization_id == org_id, issue.c.id == issue_id))).scalar()
    state_id = conn.execute(select(workflow_state.c.id).where(and_(
        workflow_state.c.organization_id == org_id,
        workflow_state.c.team_id == team_id,
        workflow_state.c.name == IN_REVIEW_STATE,
        workflow_state.c.archived_at.is_(None),
    ))).scalars().first()
    if not state_id:
        return {"moved": False, "stateId": None, "from": None,
                "reason": f"this team has no {IN_REVIEW_STATE!r} state"}
    return writer_module.move_issue_to_state(conn, org_id, issue_id, state_id, actor_id, at)


def record_verdict(conn, session: dict, *, verdict: str, verifier: Principal,
                   rationale: str | None = None, live_revision: str | None = None,
                   at: str | None = None) -> dict:
    """Record one verifier's verdict and apply the only effect a verdict may have.

    Order matters and is deliberate: separation is checked before anything is read,
    the evidence is re-read before anything is written, and the session transition is
    written before the task moves. A reader therefore never sees the task in In Review
    without a verdict row explaining why.
    """
    at = at or now_iso()
    verdict = str(verdict or "")
    if verdict not in VERDICTS:
        _refuse(f"{verdict!r} is not one of {', '.join(VERDICTS)}", field="verdict")
    if not sessions.under_verification(session):
        _refuse(f"only a session awaiting verification takes a verdict; this one is "
                f"{session['state']}", field="id")
    worker = worker_principal(conn, session)
    problems = independence_problems(verifier, worker)
    if problems:
        _refuse("; ".join(problems), problems=problems)

    checks = check_claim(conn, session["organization_id"], session, live_revision=live_revision,
                         verifier=verifier)
    supported = ceiling(checks)
    recorded = verdict if STRENGTH[verdict] <= STRENGTH[supported] else supported

    org_id = session["organization_id"]
    row = {
        "id": new_id(),
        "organization_id": org_id,
        "session_id": session["id"],
        "issue_id": session["issue_id"],
        "verdict": recorded,
        "requested_verdict": verdict,
        "verifier_id": verifier.id,
        "verifier_kind": verifier.kind,
        "verifier_model": verifier.model,
        "worker_principal_id": worker.id,
        "worker_model": worker.model,
        "checks": json.dumps(checks, sort_keys=True, default=str),
        "rationale": (rationale or "")[:4000] or None,
        "created_at": at,
    }
    conn.execute(agent_verification.insert().values(**row))
    conn.execute(agent_session.update().where(agent_session.c.id == session["id"]).values(
        verdict=recorded, verdict_at=at, verifier_id=verifier.id,
        verifier_model=verifier.model, updated_at=at))
    session.update({"verdict": recorded, "verdict_at": at, "verifier_id": verifier.id,
                    "verifier_model": verifier.model, "updated_at": at})

    moved: dict | None = None
    if recorded == "met":
        session = sessions.transition(conn, session, "verify_pass", at=at)
        moved = _move_task_to_in_review(conn, org_id, session["issue_id"], verifier.id, at)
    elif recorded == "not_met":
        session = sessions.transition(
            conn, session, "verify_fail", at=at,
            reason=rationale or "the verifier found the claim unmet")
    else:
        # Uncertain keeps the session where it is: the claim is neither confirmed nor
        # rejected, and a timer may not settle it (``SETTLE_EXEMPT``).
        gaps = [c["name"] for c in checks if c["outcome"] != PASSED]
        sessions.append_activity(
            conn, org_id, session["id"], "thought",
            body="Verification is inconclusive: " + ", ".join(gaps) + ".",
            payload={"verdict": recorded, "requested": verdict,
                     "gaps": gaps, "checks": checks},
            at=at)

    return {"session": session, "verification": row, "requested": verdict,
            "verdict": recorded, "checks": checks, "supported": supported,
            "task": moved}


def latest_verdict(conn, org_id: str, session_id: str) -> dict | None:
    """The most recent verdict row for one session, or None."""
    row = conn.execute(select(agent_verification).where(and_(
        agent_verification.c.organization_id == org_id,
        agent_verification.c.session_id == session_id,
    )).order_by(desc(agent_verification.c.created_at)).limit(1)).mappings().first()
    if not row:
        return None
    item = dict(row)
    try:
        item["checks"] = json.loads(item["checks"])
    except (TypeError, ValueError):
        item["checks"] = []
    return item


def session_for_lease(conn, org_id: str, lease: dict) -> dict | None:
    """The session a lease belongs to: by the join first, then the task's live claim."""
    row = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.lease_id == lease["id"],
    )).order_by(desc(agent_session.c.created_at)).limit(1)).mappings().first()
    if row:
        return dict(row)
    # A re-queued attempt carries a new lease id, so the join can miss a session
    # that was opened for the same task. The task's own live claim is the fallback.
    row = conn.execute(select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.issue_id == lease["task_id"],
        ~agent_session.c.state.in_(tuple(sessions.TERMINAL)),
    )).order_by(desc(agent_session.c.created_at)).limit(1)).mappings().first()
    return dict(row) if row else None
