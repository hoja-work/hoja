"""The claim and verdict read surface (HOJA-602-A-16, WP13 slice 5).

WP10 (``agent_verify.py``) writes two things: a claim, which a runner submits at
finish and which is immutable once stored, and verdict rows in ``agent_verification``,
which are append-only because a verdict that can be edited is not evidence. The
session carries the latest verdict as a column, so a run row can show it without a
second read; the ledger is what proves *who* signed *what*, with *which model*, on
*which re-read evidence*.

What was missing is the read. ``agentSession``/``workspaceAgentSessions`` carry
`verdict`, `claimAt` and `verifierId`, but nothing served the per-check evidence, so a
review screen could say "uncertain" and never say why. Two reads close that:

* ``verifierClaims`` — the claims themselves, newest first, admin-gated and paged.
  ``awaiting: true`` narrows to the sessions that are still ``verifying``, which is
  the queue a verifier works from. Every row carries its own verdict rows.
* ``agentVerifications`` — one claim's verdict ledger, or the whole workspace's when
  no session is named.

Both are read-only and both go through the tenant context: the explicit
``organization_id`` predicate plus row-level security, the same belt-and-braces
``audit.read_events`` uses. A non-admin is refused rather than handed an empty
connection, because an empty list reads as "nothing is waiting on a verifier".
"""
from __future__ import annotations

import json

from ariadne import ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, desc, or_, select

from hoja_server import agent_sessions
from hoja_server.db import agent_session, agent_verification

query = QueryType()
verifier_claim_type = ObjectType("VerifierClaim")
agent_verification_type = ObjectType("AgentVerification")

MAX_PAGE = 200
MAX_VERDICTS_PER_CLAIM = 50

AWAITING_STATE = agent_sessions.VERIFYING

SDL = gql(
    """
    "One check a verifier re-read: Hoja's own records, never a CI conclusion."
    type VerificationCheck {
      "principal_separation | acceptance_list | pull_request | merged_on_main | deployed"
      name: String!
      "passed | failed | uncertain"
      outcome: String!
      detail: String!
    }

    "One verdict row: append-only evidence of who signed a claim, and what they re-read."
    type AgentVerification {
      id: ID!
      sessionId: ID!
      issueId: ID!
      "met | not_met | uncertain — what was recorded."
      verdict: String!
      "What the verifier asked for; it differs from `verdict` when the evidence was weaker than the ask."
      requestedVerdict: String!
      verifierId: ID!
      "human | agent | bot"
      verifierKind: String!
      verifierModel: String
      "The principal that produced the work, and the model it ran."
      workerPrincipalId: ID!
      workerModel: String
      checks: [VerificationCheck!]!
      rationale: String
      createdAt: DateTime!
    }

    "One finish claim, waiting on a principal who did not do the work."
    type VerifierClaim {
      "The session the claim belongs to: `agentSessionVerify` takes this id."
      sessionId: ID!
      issueId: ID!
      "The task, in the issue page's own vocabulary."
      issue: Issue
      agentUserId: ID!
      "What the runner claimed at finish: the summary, the evidence and the acceptance answers."
      claim: JSON!
      claimAt: DateTime!
      "The model that produced the work, as the runner reported it."
      workerModel: String
      "met | not_met | uncertain — null while the claim still waits on a verifier."
      verdict: String
      verifiedAt: DateTime
      verifierId: ID
      verifierModel: String
      "true while the session is still `verifying`: no verdict has settled it."
      awaiting: Boolean!
      "Every verdict recorded on this claim, newest first."
      verifications(first: Int): [AgentVerification!]!
    }

    type VerifierClaimConnection { nodes: [VerifierClaim!]! pageInfo: PageInfo! }

    extend type Query {
      "Claims a runner submitted, newest first, admin only, paged. `awaiting: true` narrows to the ones still waiting on a verifier."
      verifierClaims(first: Int = 50, after: String, verdict: String, awaiting: Boolean,
                     agentId: String): VerifierClaimConnection!
      "The verdict ledger: one claim's rows when `sessionId` is given, the workspace's otherwise. Newest first."
      agentVerifications(sessionId: String, first: Int = 50): [AgentVerification!]!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org_id: str, user_id: str) -> None:
    actor = store.get_user(org_id, user_id)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can read the claims waiting on a verifier.",
            extensions={"code": "FORBIDDEN"})


def _bad(field: str, message: str) -> None:
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def parse_checks(value) -> list[dict]:
    """The ``checks`` column as the list of ``{name, outcome, detail}`` it holds.

    An unreadable column yields no checks rather than failing the read: the verdict
    itself is still evidence, and a screen saying "no checks were readable" is more
    honest than a 500.
    """
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        out.append({
            "name": str(entry.get("name") or "check"),
            "outcome": str(entry.get("outcome") or "uncertain"),
            "detail": str(entry.get("detail") or ""),
        })
    return out


def serialize_verification(row: dict) -> dict:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "issueId": row["issue_id"],
        "verdict": row["verdict"],
        "requestedVerdict": row["requested_verdict"],
        "verifierId": row["verifier_id"],
        "verifierKind": row["verifier_kind"],
        "verifierModel": row.get("verifier_model"),
        "workerPrincipalId": row["worker_principal_id"],
        "workerModel": row.get("worker_model"),
        "checks": parse_checks(row.get("checks")),
        "rationale": row.get("rationale"),
        "createdAt": row["created_at"],
        "_row": row,
    }


def verdicts_for(conn, org_id: str, session_ids: list[str], *,
                 first: int = MAX_VERDICTS_PER_CLAIM) -> dict[str, list[dict]]:
    """The verdict rows of every claim on a page, in one query rather than one per row."""
    if not session_ids:
        return {}
    rows = conn.execute(select(agent_verification).where(and_(
        agent_verification.c.organization_id == org_id,
        agent_verification.c.session_id.in_(tuple(set(session_ids))),
    )).order_by(desc(agent_verification.c.created_at),
                desc(agent_verification.c.id))).mappings().all()
    grouped: dict[str, list[dict]] = {}
    limit = max(1, min(int(first or MAX_VERDICTS_PER_CLAIM), MAX_VERDICTS_PER_CLAIM))
    for row in rows:
        bucket = grouped.setdefault(row["session_id"], [])
        if len(bucket) < limit:
            bucket.append(serialize_verification(dict(row)))
    return grouped


def serialize_claim(row: dict, verdicts: list[dict] | None = None) -> dict:
    return {
        "sessionId": row["id"],
        "issueId": row["issue_id"],
        "agentUserId": row["agent_user_id"],
        "claim": agent_sessions.parse_claim(row) or {},
        "claimAt": row["claim_at"],
        "workerModel": row.get("worker_model"),
        "verdict": row.get("verdict"),
        "verifiedAt": row.get("verdict_at"),
        "verifierId": row.get("verifier_id"),
        "verifierModel": row.get("verifier_model"),
        "awaiting": row["state"] == AWAITING_STATE,
        "verifications": verdicts or [],
    }


def cursor_of(row: dict) -> str:
    return f"{row['claim_at']}|{row['id']}"


def _split_cursor(cursor: str) -> tuple[str, str]:
    stamp, _, row_id = str(cursor).partition("|")
    if not stamp or not row_id:
        _bad("after", "The cursor does not name a claim in this workspace.")
    return stamp, row_id


def read_claims(conn, org_id: str, *, first=50, after: str | None = None,
                verdict: str | None = None, awaiting: bool | None = None,
                agent_id: str | None = None) -> dict:
    """One bounded page of submitted claims, newest first.

    ``claim_at`` is the sort key, not ``created_at``: a claim is what this screen
    lists, and the column is set exactly once (``submit_claim`` refuses a second
    one), so paging over it cannot repeat a row the way a re-written column would.
    """
    size = max(1, min(int(first or 50), MAX_PAGE))
    stmt = select(agent_session).where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.claim_at.is_not(None),
    ))
    if verdict:
        if verdict not in ("met", "not_met", "uncertain"):
            _bad("verdict", "verdict must be met, not_met or uncertain")
        stmt = stmt.where(agent_session.c.verdict == verdict)
    if awaiting is not None:
        stmt = stmt.where(agent_session.c.state == AWAITING_STATE) if awaiting else \
            stmt.where(agent_session.c.state != AWAITING_STATE)
    if agent_id:
        stmt = stmt.where(agent_session.c.agent_user_id == agent_id)
    if after:
        stamp, row_id = _split_cursor(after)
        stmt = stmt.where(or_(
            agent_session.c.claim_at < stamp,
            and_(agent_session.c.claim_at == stamp, agent_session.c.id < row_id),
        ))
    rows = [dict(row) for row in conn.execute(
        stmt.order_by(desc(agent_session.c.claim_at), desc(agent_session.c.id))
        .limit(size + 1)).mappings().all()]
    has_next = len(rows) > size
    rows = rows[:size]
    grouped = verdicts_for(conn, org_id, [row["id"] for row in rows])
    return {
        "nodes": [serialize_claim(row, grouped.get(row["id"], [])) for row in rows],
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": bool(after),
            "startCursor": cursor_of(rows[0]) if rows else None,
            "endCursor": cursor_of(rows[-1]) if rows else None,
        },
    }


def read_verdicts(conn, org_id: str, *, session_id: str | None = None,
                  first=50) -> list[dict]:
    """The verdict ledger, newest first, optionally narrowed to one claim."""
    size = max(1, min(int(first or 50), MAX_PAGE))
    stmt = select(agent_verification).where(
        agent_verification.c.organization_id == org_id)
    if session_id:
        stmt = stmt.where(agent_verification.c.session_id == session_id)
    rows = conn.execute(stmt.order_by(desc(agent_verification.c.created_at),
                                      desc(agent_verification.c.id))
                        .limit(size)).mappings().all()
    return [serialize_verification(dict(row)) for row in rows]


@query.field("verifierClaims")
def r_verifier_claims(_obj, info, first=None, after=None, verdict=None, awaiting=None,
                      agentId=None):  # noqa: N803 - GraphQL argument names
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_claims(conn, org, first=first or 50, after=after, verdict=verdict,
                           awaiting=awaiting, agent_id=agentId)


@query.field("agentVerifications")
def r_agent_verifications(_obj, info, sessionId=None, first=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return read_verdicts(conn, org, session_id=sessionId, first=first or 50)


@verifier_claim_type.field("issue")
def r_claim_issue(obj, info):
    store, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["issueId"], uid)


@verifier_claim_type.field("verifications")
def r_claim_verifications(obj, info, first=None):
    """Already loaded with the page: one query per page, not one per claim."""
    rows = obj.get("verifications") or []
    limit = max(1, min(int(first or MAX_VERDICTS_PER_CLAIM), MAX_VERDICTS_PER_CLAIM))
    return rows[:limit]


@agent_verification_type.field("checks")
def r_verification_checks(obj, info):
    return obj.get("checks") or []
