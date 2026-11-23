"""The one writer of ``issue_activity`` rows (HOJA-728).

A task's timeline is where people read who did what to it. A row with no actor
renders as "Someone ...", which reads as an unknown person editing the task: in
production that was every archive (the writer knew the archiving user and dropped
it), every runner event (hardcoded ``actor_id=None``) and every SLA event.

So the rule lives here, in one place, instead of at each call site:

* An event a person or an agent caused **must** name its actor. ``record``
  refuses a row without one rather than writing a nameless edit.
* Only the events the system raises by itself may have no actor: the SLA clock
  (``SYSTEM_EVENT_TYPES``) and runner transport events (``runner.*``), which are
  attributed to the delegated agent whenever the task has one, and stay
  actor-less only for a run nobody delegated.

``tests/test_activity_actor.py`` fails if any module inserts into
``issue_activity`` without going through ``record``, so a new call site cannot
quietly reintroduce an anonymous row.
"""
from __future__ import annotations

import json
from typing import Any

from hoja_server.db import issue_activity, new_id

# Events the server raises on its own, with no person behind them. The timeline
# shows these as "Hoja", never as "Someone".
SYSTEM_EVENT_TYPES = frozenset({
    "slaStarted",
    "slaBreached",
    "slaResolved",
    "slaDetached",
    "slaRetargeted",
})
RUNNER_EVENT_PREFIX = "runner."


class ActivityActorMissingError(RuntimeError):
    """A user-driven activity row was about to be written with no actor."""


def is_system_event(event_type: str) -> bool:
    return event_type in SYSTEM_EVENT_TYPES or event_type.startswith(RUNNER_EVENT_PREFIX)


def record(conn, *, org_id: str, issue_id: str, actor_id: str | None, event_type: str,
           changes: Any = None, created_at: str) -> str:
    """Insert one activity row and return its id.

    ``changes`` may be a dict (serialized compactly here) or an already-encoded
    JSON string (runner payloads are scrubbed as text before they get here).
    """
    if not actor_id and not is_system_event(event_type):
        raise ActivityActorMissingError(
            f"refusing to record {event_type!r} on issue {issue_id} with no actor")
    if changes is not None and not isinstance(changes, str):
        changes = json.dumps(changes, separators=(",", ":"), default=str) if changes else None
    row_id = new_id()
    conn.execute(issue_activity.insert().values(
        id=row_id, organization_id=org_id, issue_id=issue_id,
        actor_id=actor_id or None, event_type=event_type,
        changes=changes, created_at=created_at,
    ))
    return row_id
