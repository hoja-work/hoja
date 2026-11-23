"""Recursive issue identifiers (HOJA-472).

Grammar — authoritative copy. It is also posted as a comment on HOJA-472
before the allocator or the migration was written.

    root   := TEAM "-" top_number                     e.g. HOJA-416
    child  := parent "-" slot(parent) "-" sibling_no  e.g. HOJA-416-A-1

    depth         number of parent hops; a root has depth 0.
    slot(node)    "A" for a root. For any other node it is the node's
                  1-based index among its siblings in Excel-style letters
                  (A..Z, AA..AZ, ...), lowercased at odd depths and
                  uppercased at even depths.
    sibling_no    1-based ordinal among that parent's children, ascending.

Examples:

    HOJA-416                 root, depth 0, slot "A"
    HOJA-416-A-1             first child of HOJA-416                  depth 1
    HOJA-416-A-2             second child of HOJA-416                 depth 1
    HOJA-416-A-1-a-1         first child of HOJA-416-A-1              depth 2
    HOJA-416-A-2-b-1         first child of HOJA-416-A-2 (slot "b")   depth 2
    HOJA-416-A-1-a-1-A-1     first child of the depth-2 node          depth 3

Depth limit: MAX_DEPTH = 6 (the example tree uses 2). A create below a
node already at MAX_DEPTH is refused with InvalidReferenceError("parentId");
it is never flattened into its parent's level.

Past Z: slots continue with two letters (AA, AB, ... / aa, ab, ...), the
usual Excel column naming. They are never truncated and never reused.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

MAX_DEPTH = 6
ROOT_SLOT = "A"

# Children take their ``number`` from a disjoint range so they can never
# consume a top-level key number. The range is high enough that a team would
# need a million top-level issues to reach it, and it stays an ordinary
# INTEGER so UNIQUE(team_id, number) keeps its guarantee unchanged.
CHILD_NUMBER_BASE = 1_000_000


def _letters(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA, ... (Excel column naming)."""
    if index < 1:
        raise ValueError("slot index is 1-based")
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def slot_for(index: int, depth: int) -> str:
    """Slot letter a node at ``depth`` (>= 1) carries for its children."""
    letters = _letters(index)
    if depth % 2 == 1:
        return letters.lower()
    return letters


def child_identifier(parent_identifier: str, parent_slot: str | None, sibling_no: int) -> str:
    """Compose ``<parent>-<parent slot>-<sibling no>``."""
    return f"{parent_identifier}-{parent_slot or ROOT_SLOT}-{sibling_no}"


def child_number(child_counter: int) -> int:
    """Integer stored in ``issue.number`` for a child."""
    return CHILD_NUMBER_BASE + int(child_counter)


def is_child_number(number: int | None) -> bool:
    return number is not None and int(number) >= CHILD_NUMBER_BASE


def issue_ref_clause(org_id: str, ref: str) -> ColumnElement[bool]:
    """SQL condition matching an issue by id, live identifier, or retired alias.

    Resolve-on-read is the whole point of the alias table: a URL, commit
    message or comment that quoted the old flat identifier keeps working
    without any user-authored text being rewritten.
    """
    from hoja_server.db import issue, issue_identifier_alias

    value = str(ref)
    upper = value.upper()
    retired = select(issue_identifier_alias.c.issue_id).where(and_(
        issue_identifier_alias.c.organization_id == org_id,
        issue_identifier_alias.c.old_identifier == upper,
    ))
    # The grammar lowercases slots at odd depths (slot_for), so "HOJA-1-A-1-a-1"
    # is a real, current identifier. Comparing only against the uppercased form
    # made every depth>=2 identifier unresolvable *by its own canonical text*;
    # compare the raw value too. Both are equality tests on the identifier
    # index, so this stays a seek.
    live_forms = [value] if value == upper else [value, upper]
    return or_(
        issue.c.id == value,
        issue.c.identifier.in_(live_forms),
        issue.c.id.in_(retired),
    )


def retired_issue_ids(org_id: str, refs) -> ColumnElement:
    """Selectable of issue ids whose retired identifier is one of ``refs``.

    ``refs`` is what an ``IssueFilter.identifier`` equality comparator named.
    Only the equality forms of that filter use this: a caller who asks for
    ``HOJA-8`` is quoting one specific issue, which is exactly the case the
    alias ledger exists for. Text comparators (contains/neq/nin) keep their
    plain column meaning, because a substring search must not silently return
    issues that were renamed out of the match.
    """
    from hoja_server.db import issue_identifier_alias

    wanted = [str(ref).upper() for ref in refs]
    return select(issue_identifier_alias.c.issue_id).where(and_(
        issue_identifier_alias.c.organization_id == org_id,
        issue_identifier_alias.c.old_identifier.in_(wanted),
    ))
