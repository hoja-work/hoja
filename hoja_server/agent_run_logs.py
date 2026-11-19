"""One timeline per agent run: runner events, runner logs, server notes (HOJA-602).

Debugging an agent run used to mean the machine's terminal: a runner that crashed mid-run left
nothing in Hoja but a later "finish replayed by recovery". This table is where everything about
a run lands, so GraphQL can answer "what happened" (``agentRun { logs }``, ``agentRunLogs``):

* **events** the runner already streams (``/runner/events``), copied here with the lease id;
* **log lines** the runner ships (``/runner/logs``): its warnings and errors, with the traceback
  when something broke, tagged with the lease it held, or with none;
* **server notes**: what the control plane decided about the run (a refused transition, a
  failed finish, a lost lease), which otherwise only reached the server's own log.

Every write is best-effort: a log that cannot be stored never fails the call that produced it.
Values are scrubbed of the lease's secrets before they are stored, like the activity stream.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Any, Mapping

from sqlalchemy import and_, delete, desc, select

from hoja_server.db import agent_run_log, new_id, now_iso

LOG = logging.getLogger("hoja_server.agent_run_logs")

LEVELS = ("event", "debug", "info", "warning", "error")
SOURCES = ("runner", "server")
MAX_MESSAGE = 4000
MAX_DETAIL = 16000
MAX_LINES_PER_BATCH = 200
#: Lines kept per runner; older ones are pruned now and then, not on every write.
KEEP_PER_RUNNER = 5000
PRUNE_EVERY = 50


def _level(value: Any) -> str:
    level = str(value or "info").strip().lower()
    if level in ("warn",):
        level = "warning"
    if level in ("critical", "fatal", "exception"):
        level = "error"
    return level if level in LEVELS else "info"


def record(conn, org_id: str, *, message: str, level: str = "info", source: str = "server",
           runner_id: str | None = None, lease_id: str | None = None, kind: str | None = None,
           detail: Mapping[str, Any] | None = None, at: str | None = None,
           scrub: Any = None) -> None:
    """Append one line. ``scrub`` is a callable that removes secret values from text."""
    text = str(message or "")[:MAX_MESSAGE]
    body = json.dumps(dict(detail), sort_keys=True, default=str)[:MAX_DETAIL] if detail else None
    if scrub is not None:
        text = scrub(text)
        body = scrub(body) if body else body
    conn.execute(agent_run_log.insert().values(
        id=new_id(), organization_id=org_id, runner_id=runner_id, lease_id=lease_id,
        source=source if source in SOURCES else "server", level=_level(level),
        kind=(str(kind)[:120] if kind else None), message=text or "(empty)", detail=body,
        created_at=at or now_iso(),
    ))
    if runner_id and random.randrange(PRUNE_EVERY) == 0:
        _prune(conn, org_id, runner_id)


def _prune(conn, org_id: str, runner_id: str) -> None:
    cutoff = conn.execute(select(agent_run_log.c.created_at).where(and_(
        agent_run_log.c.organization_id == org_id,
        agent_run_log.c.runner_id == runner_id,
    )).order_by(desc(agent_run_log.c.created_at)).offset(KEEP_PER_RUNNER).limit(1)).scalar()
    if cutoff:
        conn.execute(delete(agent_run_log).where(and_(
            agent_run_log.c.organization_id == org_id,
            agent_run_log.c.runner_id == runner_id,
            agent_run_log.c.created_at <= cutoff,
        )))


def note(engine, org_id: str, lease: Mapping[str, Any] | None, message: str, *,
         level: str = "info", kind: str | None = None,
         detail: Mapping[str, Any] | None = None) -> None:
    """A server-side note about a run, in its own transaction. Never raises."""
    from hoja_server import tenant_context

    try:
        with tenant_context.scoped(org_id), engine.begin() as conn:
            record(conn, org_id, message=message, level=level, source="server", kind=kind,
                   runner_id=(lease or {}).get("runner_id"), lease_id=(lease or {}).get("id"),
                   detail=detail)
    except Exception:  # noqa: BLE001 - a note must never fail what it describes
        LOG.debug("could not store a run note", exc_info=True)


def ingest(conn, org_id: str, runner_id: str, lease: Mapping[str, Any] | None,
           lines: list[Any], *, scrub: Any = None) -> int:
    """Store a batch the runner shipped. Returns how many lines were kept."""
    kept = 0
    for line in lines[:MAX_LINES_PER_BATCH]:
        if not isinstance(line, Mapping):
            continue
        detail = {k: v for k, v in line.items()
                  if k in ("traceback", "logger", "pid", "phase", "harness", "version")}
        record(conn, org_id, message=str(line.get("message") or ""),
               level=line.get("level"), source="runner", runner_id=runner_id,
               lease_id=(lease or {}).get("id"), kind=line.get("logger"),
               detail=detail or None, at=str(line.get("at") or "") or None, scrub=scrub)
        kept += 1
    return kept


def serialize(row: Mapping[str, Any], *, runner_name: str | None = None) -> dict[str, Any]:
    detail = None
    if row.get("detail"):
        try:
            detail = json.loads(row["detail"])
        except (TypeError, ValueError):
            detail = {"raw": row["detail"]}
    return {
        "id": row["id"], "leaseId": row.get("lease_id"), "runnerId": row.get("runner_id"),
        "runnerName": runner_name, "source": row["source"], "level": row["level"],
        "kind": row.get("kind"), "message": row["message"], "detail": detail,
        "createdAt": row["created_at"],
    }
