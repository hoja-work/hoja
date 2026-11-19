"""Automatic verification: the platform signs a claim when a run finishes.

WP10 built the two halves of a completion — the runner's claim, and a separate
principal's verdict — and then joined them with nothing but a GraphQL mutation a
person had to know about. The recorded consequence is the one this module removes:
a delegated run finished, ``agent_sessions.submit_claim`` moved the session to
``verifying``, and no code path ever invoked the verifier, so delegated work waited
for a human who was never told there was anything to do. ``verifying`` was a state
with no exit but a hand-written mutation.

**The verifier is a principal, not a code path.** This module does not judge
anything itself. It resolves the workspace's system verifier identity (one ``user``
row per workspace, ``kind='agent'``, no membership, no ``agent_member``, so it never
holds a seat and is never a delegation target) and hands the claim to
``agent_verify.record_verdict`` — the same function the mutation calls. Every
load-bearing property of that module therefore still holds, because none of it is
bypassed:

* separation — the system verifier has its own user id and its own model name, and
  ``independence_problems`` refuses it if either matches the worker's. A refusal
  writes no verdict row at all; the session keeps waiting and the reason is put on
  the transcript once;
* no CI — the checks re-read Hoja's own records, exactly as before;
* uncertainty — the requested verdict is always ``met``, and ``ceiling`` decides:
  a contradiction records ``not_met``, a gap records ``uncertain``. Nothing here
  can talk the evidence up.

**The trigger.** A finished attempt reaches ``verifying`` inside the finish
transaction, so the verdict is never written by the runner's own call. Two things
then run the same idempotent function:

1. a background task attached to the ``/runner/finish`` response, which runs
   *after* the response has been written, so the runner's latency does not include
   verification;
2. the existing lease reaper loop (``runner_api.run_reaper_forever``), which sweeps
   for claims nobody judged every ``REAP_INTERVAL_S``. This is the safety net for a
   process that died between the commit and the background task, and it is the same
   shape as the lease reaper: Hoja's own clock, its own records, no dependency on a
   runner or on the optional DBOS half of the deployment being alive.

The trigger is deliberately not a DBOS workflow: DBOS is enabled only when
``HOJA_AGENT_DBOS_URL`` is set, and a verification that happens only on durable
deployments would leave the others with the same silence this module exists to end.
The check is a handful of SELECTs plus one ledger row, so in-process is enough, and
the sweep is what makes "in-process" safe to lose.

**Idempotency.** One claim, one verdict. A claim is stored once per session
(``CLAIM_IMMUTABLE``), so the session is the claim's identity here. The guard is a
single conditional UPDATE — ``state='verifying' AND verdict IS NULL AND verifier_id
IS NULL`` — taken in the same transaction as the verdict row. On PostgreSQL the
UPDATE takes the row lock, so a second concurrent trigger blocks, re-evaluates its
predicate against the committed row, and matches nothing; on SQLite, which is the
single-writer local mode, the write lock does the same job. A trigger that finds a
verdict already recorded — or a ``verifier_id`` already stamped — does nothing and
returns why. A trigger that dies mid-transaction rolls the stamp back with it, so
the sweep retries instead of the claim being stranded half-judged.
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import and_, desc, select

from hoja_server import agent_sessions as sessions
from hoja_server import agent_verify, tenant_context
from hoja_server.db import (
    agent_activity,
    agent_session,
    new_id,
    now_iso,
    organization,
    user,
)

LOG = logging.getLogger("hoja_server.agent_autoverify")

# Off switch, for a deployment that wants a person to judge every claim. Default on:
# the recorded failure is work that waited for a verifier nobody invoked.
ENABLED_ENV = "HOJA_AUTO_VERIFY"
# Which checker judged the claim. The checks ``agent_verify`` runs are deterministic
# — no model is consulted — so this names the check engine and is recorded verbatim
# as ``agent_verification.verifier_model``. A deployment that replaces the checks
# with a model-backed verifier names that model here, and separation then compares a
# real model name against the worker's.
MODEL_ENV = "HOJA_VERIFIER_MODEL"
DEFAULT_VERIFIER_MODEL = "hoja-checks-v1"

# The identity the verdict is signed by. A local, non-routable address on purpose:
# nobody should be able to sign in as the verifier or invite it.
VERIFIER_NAME = "Hoja Verifier"
VERIFIER_EMAIL = "verifier@agents.hoja.invalid"

# The verifier asks for the strongest verdict and lets the evidence take it down.
REQUESTED_VERDICT = "met"
# A sweep is a bounded catch-up, not a queue drain: the next sweep continues.
SWEEP_LIMIT = 50
# How many recent activities are read when deciding whether the "blocked" note for
# this claim has already been written.
BLOCKED_NOTE_LIMIT = 20
BLOCKED_KEY = "auto_verify"
BLOCKED_VALUE = "blocked"

RATIONALE = ("Automatic verification: the platform re-read the claim's evidence with its "
             "deterministic checks, and the verdict recorded is the weakest that evidence "
             "supports.")


def enabled() -> bool:
    """Is automatic verification on for this deployment?"""
    return os.environ.get(ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no")


def verifier_model() -> str:
    """The model (or check engine) name this verifier signs with."""
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_VERIFIER_MODEL


# --------------------------------------------------------------------------- identity
def _identity(conn, org_id: str) -> str | None:
    """The workspace's system verifier user id, oldest first for determinism."""
    return conn.execute(select(user.c.id).where(and_(
        user.c.organization_id == org_id,
        user.c.email == VERIFIER_EMAIL,
    )).order_by(user.c.created_at, user.c.id).limit(1)).scalars().first()


def system_verifier(conn, org_id: str) -> agent_verify.Principal:
    """The distinct principal a ``met`` from this module is signed by.

    Created once per workspace and then reused, so every automatic verdict names the
    same identity. The first creation is serialized on the organization row on
    PostgreSQL (a second concurrent creator re-reads after taking the lock and finds
    the row), which is what stops two rows from appearing the one time it matters.
    """
    identity = _identity(conn, org_id)
    if identity is None:
        if conn.dialect.name == "postgresql":
            conn.execute(select(organization.c.id).where(
                organization.c.id == org_id).with_for_update()).first()
            identity = _identity(conn, org_id)
        if identity is None:
            stamp = now_iso()
            identity = new_id()
            conn.execute(user.insert().values(
                id=identity, organization_id=org_id, name=VERIFIER_NAME,
                display_name=VERIFIER_NAME, email=VERIFIER_EMAIL, kind="agent",
                active=True, admin=False, created_at=stamp, updated_at=stamp))
    return agent_verify.Principal(id=str(identity), kind="agent",
                                  model=verifier_model(), read_only=True)


# ------------------------------------------------------------------------ the one run
def _blocked_already_noted(conn, org_id: str, session_id: str) -> bool:
    """Has the transcript already said why this claim cannot be verified?

    A separation failure writes no verdict, so the session keeps entering the sweep.
    Without this the transcript would grow one identical thought per tick, which is
    noise in place of an answer.
    """
    rows = conn.execute(select(agent_activity.c.payload).where(and_(
        agent_activity.c.organization_id == org_id,
        agent_activity.c.session_id == session_id,
    )).order_by(desc(agent_activity.c.created_at)).limit(BLOCKED_NOTE_LIMIT)).scalars().all()
    for raw in rows:
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get(BLOCKED_KEY) == BLOCKED_VALUE:
            return True
    return False


def _claim_verification(conn, org_id: str, session: dict, verifier: agent_verify.Principal,
                        at: str) -> bool:
    """Take the one-verdict lock for this claim. True when this caller owns it.

    The predicate is the whole idempotency story: a claim that already has a verdict,
    or one a verifier has already picked up, does not match. It is a single UPDATE so
    the database, not the caller, decides the winner. It is written in the same
    transaction as the verdict, so a crash before the verdict rolls the stamp back
    and the next trigger is free to try again.
    """
    claimed = conn.execute(agent_session.update().where(and_(
        agent_session.c.organization_id == org_id,
        agent_session.c.id == session["id"],
        agent_session.c.state == sessions.VERIFYING,
        agent_session.c.verdict.is_(None),
        agent_session.c.verifier_id.is_(None),
    )).values(verifier_id=verifier.id, verifier_model=verifier.model, updated_at=at))
    return claimed.rowcount == 1


def verify_session(engine, org_id: str, session_id: str, *,
                   at: str | None = None) -> dict:
    """Judge this session's claim once, automatically, or explain why not.

    Idempotent by construction (see ``_claim_verification``): calling it twice for one
    claim records one verdict, and the second call answers ``already_judged``.
    """
    at = at or now_iso()
    with tenant_context.scoped(org_id), engine.begin() as conn:
        session = sessions.get_session(conn, org_id, session_id)
        if session is None:
            return {"status": "missing", "sessionId": session_id}
        if not sessions.under_verification(session):
            return {"status": "not_verifying", "sessionId": session_id,
                    "state": session["state"]}
        if session.get("verifier_id") or agent_verify.latest_verdict(conn, org_id, session_id):
            return {"status": "already_judged", "sessionId": session_id,
                    "verdict": session.get("verdict")}

        verifier = system_verifier(conn, org_id)
        problems = agent_verify.independence_problems(
            verifier, agent_verify.worker_principal(conn, session))
        if problems:
            # Separation is refused *before* any row is written, exactly as the
            # mutation path refuses it. The claim stays in verifying for a person.
            if not _blocked_already_noted(conn, org_id, session_id):
                sessions.append_activity(
                    conn, org_id, session_id, "thought",
                    body="Automatic verification is blocked: " + "; ".join(problems) + ".",
                    payload={BLOCKED_KEY: BLOCKED_VALUE, "problems": problems,
                             "verifierId": verifier.id, "verifierModel": verifier.model},
                    author_kind="agent", author_id=verifier.id, ephemeral=True, at=at)
            return {"status": "blocked", "sessionId": session_id, "problems": problems}

        if not _claim_verification(conn, org_id, session, verifier, at):
            return {"status": "already_judged", "sessionId": session_id,
                    "verdict": session.get("verdict")}

        # Re-read under the take, so ``record_verdict`` reads the row the lock holds
        # rather than the copy from before it.
        session = sessions.get_session(conn, org_id, session_id) or session
        try:
            result = agent_verify.record_verdict(
                conn, session, verdict=REQUESTED_VERDICT, verifier=verifier,
                rationale=RATIONALE, at=at)
        except agent_verify.VerificationRefusedError as refused:
            # ``record_verdict`` refuses a state it will not sign (a stop that landed
            # between the claim and the call, say). The take is released here rather
            # than left stamped, so a session that is still awaiting verification is
            # picked up again instead of being recorded as judged by nobody.
            conn.execute(agent_session.update().where(and_(
                agent_session.c.organization_id == org_id,
                agent_session.c.id == session_id,
            )).values(verifier_id=None, verifier_model=None, updated_at=at))
            sessions.append_activity(
                conn, org_id, session_id, "thought",
                body=f"Automatic verification did not run: {refused}",
                payload={BLOCKED_KEY: BLOCKED_VALUE, "reason": str(refused),
                         "verifierId": verifier.id, "verifierModel": verifier.model},
                author_kind="agent", author_id=verifier.id, ephemeral=True, at=at)
            return {"status": "refused", "sessionId": session_id, "reason": str(refused)}

        return {
            "status": "recorded",
            "sessionId": session_id,
            "verdict": result["verdict"],
            "supported": result["supported"],
            "verifierId": verifier.id,
            "verifierModel": verifier.model,
            "checks": result["checks"],
            "task": result["task"],
        }


def verify_after_finish(engine, org_id: str, session_id: str) -> None:
    """The finish route's post-response hook. Never fails the finish that caused it."""
    if not enabled():
        return
    try:
        result = verify_session(engine, org_id, session_id)
        if result.get("status") == "recorded":
            LOG.info("automatic verification recorded %s for session %s",
                     result.get("verdict"), session_id)
        else:
            LOG.debug("automatic verification of session %s: %s",
                      session_id, result.get("status"))
    except Exception:  # the sweep is the retry; a claim is never lost to a hook
        LOG.warning("automatic verification of session %s failed; the sweep will retry",
                    session_id, exc_info=True)


def verify_pending(engine, *, limit: int = SWEEP_LIMIT) -> list[dict]:
    """Judge every claim nobody has judged yet, oldest first.

    Runs across tenants — finding work is the one place that needs system context —
    and then re-enters each tenant's own scope to write, so the verdict row is written
    under exactly one organization, like every other write.
    """
    if not enabled():
        return []
    with tenant_context.system(), engine.connect() as conn:
        pending = [
            (str(row[0]), str(row[1])) for row in conn.execute(
                select(agent_session.c.organization_id, agent_session.c.id).where(and_(
                    agent_session.c.state == sessions.VERIFYING,
                    agent_session.c.verdict.is_(None),
                    agent_session.c.verifier_id.is_(None),
                )).order_by(agent_session.c.updated_at).limit(max(1, int(limit))))
        ]
    results: list[dict] = []
    for org_id, session_id in pending:
        try:
            results.append(verify_session(engine, org_id, session_id))
        except Exception:
            LOG.warning("automatic verification of session %s failed; next tick retries",
                        session_id, exc_info=True)
    return results
