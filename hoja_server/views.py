"""Views: durable, shareable saved queries.

Design follows the extension subsystem (``extensions.parse_manifest``): a view's filter
tree and display options are declarative documents validated field by field, and every
refusal names the offending path in ``ViewError.field``. Validation is pure — no network,
no database — so a bad document is refused before any row is written, and the writer
stores exactly what the parser returned.

Two responsibilities live here and nowhere else:

* ``parse_filters`` / ``parse_display`` — the grammar (properties, operators, limits).
* ``compile_filter`` / ``display_order`` — translation into the predicate language that
  ``store.py`` already executes, so a view never introduces a second filtering engine.

Nothing in this module reads or writes the database.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

KINDS = ("issue", "project", "initiative")
SCOPES = ("workspace", "team", "project", "initiative")
VISIBILITIES = ("private", "workspace", "team")
LAYOUTS = ("list", "board", "timeline")
# Grouping is applied by the client; the project-only keys (lead … targetDate) group a
# project list, and ``statusType`` groups by workflow category.
GROUPINGS = ("none", "status", "statusType", "assignee", "creator", "priority", "project",
             "cycle", "labels", "labelGroup", "team", "lead", "members", "health", "startDate",
             "targetDate")
# ``name``/``startDate``/``targetDate`` order projects (for tasks: title, creation, due date).
ORDERINGS = ("manual", "priority", "updated", "created", "title", "dueDate", "status",
             "estimate", "name", "startDate", "targetDate")
# ``quarter`` and ``halfYear`` are the project list's "Past 3/6 months" for closed projects.
COMPLETED_WINDOWS = ("none", "day", "week", "month", "quarter", "halfYear", "all")
NESTED_SUB_ISSUES = ("all", "matching", "hide")
# The documented default when a display leaves ``completedWindow`` out (docs/views.md).
DEFAULT_COMPLETED_WINDOW = "week"
STATE_TYPES = ("triage", "backlog", "unstarted", "started", "completed", "canceled")

MAX_DEPTH = 5
MAX_PREDICATES = 200
MAX_LIST = 100
MAX_TEXT = 200
MAX_COLUMNS = 50
DEFAULT_PAGE = 50
MAX_PAGE = 250
MIN_ZOOM, MAX_ZOOM = 0.25, 8.0

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?$")
# A relative date is an ISO-8601 duration from "now" ("-P7D" is a week ago, "P2W" two weeks
# ahead). It is stored as written and resolved when the view runs, so "created in the last
# week" stays the last week. Units are capped so a resolution never overflows a datetime.
_DURATION = re.compile(r"^([+-])?P(?=\d|T\d)(?:(\d{1,4})Y)?(?:(\d{1,4})M)?(?:(\d{1,4})W)?"
                       r"(?:(\d{1,5})D)?(?:T(?:(\d{1,6})H)?(?:(\d{1,7})M)?(?:(\d{1,9})S)?)?$")
# Keywords resolved at run time. ``overdue`` is "before today" and only means something
# for a due or target date.
DATE_KEYWORDS = ("now", "today", "yesterday", "tomorrow", "overdue")
_OVERDUE_PROPERTIES = frozenset({"dueDate", "targetDate"})
# The current user and the current user's teams, resolved when the view runs.
ME = "me"
MY_TEAMS = "myTeams"


class ViewError(ValueError):
    """A view document the caller can fix; ``field`` is the dotted path that failed."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


# ------------------------------------------------------------------ documents

def parse_json_document(raw: Any, name: str) -> dict:
    """Decode a JSON document that arrived as a dict or a string; ``{}`` when absent."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ViewError(f"{name} is not valid JSON: {exc.msg}.", name) from exc
    if not isinstance(raw, dict):
        raise ViewError(f"{name} must be a JSON object.", name)
    return raw


def _text(value: Any, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ViewError(f"{field} must be a non-empty string of at most {limit} characters.", field)
    return value.strip()


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ViewError(f"{field} must be an integer.", field)
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ViewError(f"{field} must be true or false.", field)
    return value


def _iso(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ISO.match(value):
        raise ViewError(f"{field} must be an ISO-8601 date or date-time.", field)
    try:  # accept both date-only and full timestamps; normalize date-only to midnight UTC
        datetime.fromisoformat(value.replace("Z", "+00:00")) if "T" in value or " " in value \
            else date.fromisoformat(value)
    except ValueError as exc:
        raise ViewError(f"{field} must be an ISO-8601 date or date-time.", field) from exc
    return value


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    last = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def resolve_date(value: str, now: datetime | None = None) -> str:
    """An absolute ISO value for a stored date value, relative ones resolved against ``now``.

    A duration without a time part resolves to a calendar date (day granularity, as Linear's
    relative filters are); one with hours, minutes or seconds resolves to an instant.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date()
    if value in ("today", "overdue"):
        return today.isoformat()
    if value == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if value == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if value == "now":
        return now.isoformat()
    match = _DURATION.match(value)
    if not match:
        return value
    sign = -1 if match.group(1) == "-" else 1
    years, months, weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups()[1:])
    timed = any(match.group(i) for i in (6, 7, 8))
    base = now if timed else datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    shifted = _add_months(base.date(), sign * (years * 12 + months))
    base = base.replace(year=shifted.year, month=shifted.month, day=shifted.day)
    base += sign * timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes,
                             seconds=seconds)
    return base.isoformat() if timed else base.date().isoformat()


def is_relative_date(value: Any) -> bool:
    return isinstance(value, str) and (value in DATE_KEYWORDS or bool(_DURATION.match(value)))


def _date_value(value: Any, field: str) -> str:
    """An absolute ISO date/date-time, an ISO-8601 duration from now, or a date keyword."""
    if is_relative_date(value):
        try:
            resolve_date(value)
        except (OverflowError, ValueError) as exc:
            raise ViewError(f"{field} is a duration too large to resolve.", field) from exc
        return value
    if not isinstance(value, str) or not _ISO.match(value):
        raise ViewError(
            f"{field} must be an ISO-8601 date or date-time, an ISO-8601 duration from now "
            f"such as \"-P7D\", or one of {list(DATE_KEYWORDS)}.", field)
    return _iso(value, field)


def _number(value: Any, field: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ViewError(f"{field} must be a number.", field)
    return value


def _values(value: Any, field: str, item) -> list:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST:
        raise ViewError(
            f"{field} must be a list of 1 to {MAX_LIST} values.", field)
    return [item(v, f"{field}[{i}]") for i, v in enumerate(value)]


# ---------------------------------------------------------------- filter grammar

_OPERATORS = {
    "is": "eq",
    "isNot": "neq",
    "isEitherOf": "in",
    "isNeitherOf": "nin",
    "contains": "contains",
    "containsIgnoreCase": "containsIgnoreCase",
    "equalsIgnoreCase": "eqIgnoreCase",
    "before": "lt",
    "after": "gt",
    "onOrBefore": "lte",
    "onOrAfter": "gte",
    "isGreaterThan": "gt",
    "isGreaterThanOrEqual": "gte",
    "isLessThan": "lt",
    "isLessThanOrEqual": "lte",
    "includesAny": "in",
    "includesEither": "in",
    "includesNone": "nin",
    "includesNeither": "nin",
    "includesAll": "all",
    # Grammar-only operators: compiled by hand, never through ``_cmp``.
    "isMe": "isMe",
    "isNull": "null",
}
_LIST_OPERATORS = frozenset({"isEitherOf", "isNeitherOf", "includesAny", "includesEither",
                             "includesNone", "includesNeither", "includesAll"})
_NEGATIVE_OPERATORS = frozenset({"isNot", "isNeitherOf", "includesNone", "includesNeither"})
# Operators that may name ``null`` ("No assignee") on a nullable property.
_NULL_OPERATORS = frozenset({"is", "isNot", "isEitherOf", "isNeitherOf", "includesAny",
                             "includesEither", "includesNone", "includesNeither"})

_IDENTITY_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf")
_USER_OPS = (*_IDENTITY_OPS, "isMe")
_NUMBER_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf", "isGreaterThan",
               "isGreaterThanOrEqual", "isLessThan", "isLessThanOrEqual")
_TEXT_OPS = ("is", "isNot", "contains", "containsIgnoreCase", "equalsIgnoreCase")
_DATE_OPS = ("is", "before", "after", "onOrBefore", "onOrAfter")
_LABEL_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf", "includesAny", "includesEither",
              "includesNone", "includesNeither", "includesAll")
_COLLECTION_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf", "includesAny", "includesEither",
                   "includesNone", "includesNeither", "includesAll")

# property -> (operators, value kind). The value kind decides which reference column a
# human-readable value compiles to: "team" tries id then key, "user" tries id, email,
# then display name; anything else is a literal.
_ISSUE_PROPERTIES = {
    "team": (_IDENTITY_OPS, "team"),
    "state": (_IDENTITY_OPS, "state"),
    "stateType": (_IDENTITY_OPS, "stateType"),
    "assignee": (_USER_OPS, "user"),
    # Linear's custom views filter on delegate too, and delegation is a separate
    # column from assignment, so the stored view needs its own property (HOJA-712).
    "delegate": (_USER_OPS, "user"),
    "creator": (_USER_OPS, "user"),
    # Anyone following the task (issue_subscriber); "is" means "is one of its subscribers".
    "subscriber": ((*_COLLECTION_OPS, "isMe"), "subscriber"),
    "project": (_IDENTITY_OPS, "project"),
    "cycle": (_IDENTITY_OPS, "cycle"),
    "parent": (("is", "isNot", "isNull"), "issue"),
    "hasSubIssues": (("is",), "boolean"),
    "isSubIssue": (("is",), "boolean"),
    "priority": (_NUMBER_OPS, "number"),
    "estimate": (_NUMBER_OPS, "float"),
    "number": (_NUMBER_OPS, "number"),
    "identifier": (_TEXT_OPS, "text"),
    "title": (_TEXT_OPS, "text"),
    "description": (_TEXT_OPS, "text"),
    "createdAt": (_DATE_OPS, "date"),
    "updatedAt": (_DATE_OPS, "date"),
    "dueDate": (_DATE_OPS, "date"),
    "startedAt": (_DATE_OPS, "date"),
    "completedAt": (_DATE_OPS, "date"),
    "canceledAt": (_DATE_OPS, "date"),
    "labels": (_LABEL_OPS, "label"),
}
_PROJECT_PROPERTIES = {
    "id": (_IDENTITY_OPS, "text"),
    "name": (_TEXT_OPS, "text"),
    # Hoja's project has one description field; Linear calls the short one "summary".
    "summary": (_TEXT_OPS, "text"),
    "slugId": (("is", "isNot"), "text"),
    "state": (_IDENTITY_OPS, "text"),
    # The configured project status by name, case-insensitively (project_settings).
    "status": (_IDENTITY_OPS, "projectStatus"),
    "team": (_IDENTITY_OPS, "team"),
    "lead": (_USER_OPS, "user"),
    "creator": (_USER_OPS, "user"),
    "members": ((*_COLLECTION_OPS, "isMe"), "member"),
    "health": (_IDENTITY_OPS, "health"),
    "startDate": (_DATE_OPS, "date"),
    "targetDate": (_DATE_OPS, "date"),
    "initiative": (_COLLECTION_OPS, "initiativeRef"),
}
_INITIATIVE_PROPERTIES = {
    "status": (("is", "isEitherOf"), "text"),
    # Revision 88: the initiatives list filters. Applied by ``initiative_matches``.
    "health": (("is", "isEitherOf"), "text"),
    "owner": (("is", "isEitherOf"), "text"),
    "leadTeam": (("is", "isEitherOf"), "text"),
    "label": (("is", "isEitherOf"), "text"),
}
_PROPERTIES = {"issue": _ISSUE_PROPERTIES, "project": _PROJECT_PROPERTIES,
               "initiative": _INITIATIVE_PROPERTIES}
# Linear filter properties Hoja knows but cannot execute, with the reason the parser gives,
# so a view never silently ignores part of what it was asked to select.
_RELATIONS = ("Hoja does not track issue relations (blocked by, blocking, related, "
              "duplicate) yet, so a view cannot filter on them.")
_UNSUPPORTED = {
    "issue": {
        "blocked": _RELATIONS, "blocking": _RELATIONS, "hasRelations": _RELATIONS,
        "related": _RELATIONS, "duplicate": _RELATIONS, "relations": _RELATIONS,
        "triagedAt": "Hoja does not record when a task left triage.",
        "autoClosedAt": "Hoja does not auto-close tasks, so there is no auto-closed date.",
        "milestone": "Hoja projects have no milestones yet.",
        "customer": "Hoja has no customer records.",
        "sla": "SLA status is not a view filter yet; filter the SLA screen instead.",
    },
    "project": {
        "priority": "Hoja projects have no priority.",
        "labels": "Hoja projects have no labels.",
        "milestones": "Hoja projects have no milestones yet.",
        "relations": "Hoja does not track project relations.",
    },
    "initiative": {},
}
# Value kinds that name another row, and therefore compile to a reference column.
_REFERENCE_KINDS = frozenset({"team", "state", "user", "project", "cycle", "issue"})
# Properties whose column may be empty: ``null`` is "No assignee", "No project", "No labels"
# and so on, and a negative predicate on them keeps the empty rows.
_NULLABLE_PROPERTIES = {
    "issue": frozenset({"assignee", "delegate", "creator", "project", "cycle", "parent",
                        "labels", "dueDate", "estimate", "subscriber", "startedAt",
                        "completedAt", "canceledAt"}),
    "project": frozenset({"lead", "creator", "members", "health", "startDate", "targetDate",
                          "initiative"}),
    "initiative": frozenset(),
}
# Calendar-date columns: a timestamp value compares by its day.
_DATE_ONLY = frozenset({"dueDate", "startDate", "targetDate"})
HEALTH_VALUES = {"onTrack": "on track", "atRisk": "at risk", "offTrack": "off track",
                 "on track": "on track", "at risk": "at risk", "off track": "off track",
                 "none": "none"}


def _state_type(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in STATE_TYPES:
        raise ViewError(f"{field} must be one of {list(STATE_TYPES)}.", field)
    return value


def _health(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in HEALTH_VALUES:
        raise ViewError(f"{field} must be one of onTrack, atRisk, offTrack or none.", field)
    return HEALTH_VALUES[value]


def _value(kind: str, op: str, value: Any, field: str, nullable: bool = False) -> Any:
    if op == "isMe" or kind == "boolean" or op == "isNull":
        return _boolean(value, field)
    base = {"team": _text, "state": _text, "user": _text, "project": _text,
            "cycle": _text, "issue": _text, "label": _text, "text": _text,
            "subscriber": _text, "member": _text, "initiativeRef": _text,
            "projectStatus": _text, "health": _health,
            "number": _integer, "float": _number, "date": _date_value,
            "stateType": _state_type}[kind]

    def item(v: Any, where: str) -> Any:
        if v is None and nullable and op in _NULL_OPERATORS and kind != "date":
            return None
        return base(v, where)

    if op in _LIST_OPERATORS:
        return _values(value, field, item)
    if value is None and nullable and op in ("is", "isNot"):
        return None
    return item(value, field)


def _parse_group(kind: str, raw: dict, path: str, depth: int, budget: list[int]) -> dict:
    if depth > MAX_DEPTH:
        raise ViewError(f"filters nest more than {MAX_DEPTH} levels deep.", "filters")
    properties = _PROPERTIES[kind]
    out: dict[str, Any] = {}
    for key in raw:
        where = f"{path}{key}"
        if key in ("and", "or"):
            entries = raw[key]
            if not isinstance(entries, list) or not entries:
                raise ViewError(f"{where} must be a non-empty list of filter objects.", where)
            out[key] = []
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ViewError(f"{where}[{i}] must be a filter object.", f"{where}[{i}]")
                out[key].append(_parse_group(kind, entry, f"{where}[{i}].", depth + 1, budget))
            continue
        if key in _UNSUPPORTED[kind]:
            raise ViewError(f"{kind}.{key} cannot be filtered: {_UNSUPPORTED[kind][key]}", where)
        if key not in properties:
            raise ViewError(
                f"Unknown {kind} filter property '{key}'; expected one of {sorted(properties)} or and/or.",
                where)
        allowed, value_kind = properties[key]
        if not isinstance(raw[key], dict) or not raw[key]:
            raise ViewError(f"{where} must be an object of operators, for example {{\"is\": value}}.", where)
        predicates: dict[str, Any] = {}
        for op, value in raw[key].items():
            op_field = f"{where}.{op}"
            if op not in _OPERATORS or op not in allowed:
                raise ViewError(
                    f"Operator '{op}' is not supported for {kind}.{key}; expected one of {list(allowed)}.",
                    op_field)
            budget[0] += 1
            if budget[0] > MAX_PREDICATES:
                raise ViewError(f"filters contain more than {MAX_PREDICATES} predicates.", "filters")
            parsed = _value(value_kind, op, value, op_field,
                            key in _NULLABLE_PROPERTIES[kind])
            if value_kind == "date" and parsed == "overdue" and (
                    key not in _OVERDUE_PROPERTIES or op != "is"):
                raise ViewError(f"\"overdue\" is only a value of {sorted(_OVERDUE_PROPERTIES)} "
                                "with the is operator.", op_field)
            predicates[op] = parsed
        out[key] = predicates
    return out


def parse_filters(kind: str, raw: Any) -> dict:
    """Validate a filter tree for ``kind`` and return the normalized document."""
    if kind not in KINDS:
        raise ViewError(f"kind must be one of {list(KINDS)}.", "kind")
    document = parse_json_document(raw, "filters")
    if not document:
        return {}
    return _parse_group(kind, document, "", 0, [0])


# --------------------------------------------------------------- compilation
#
# A property predicate compiles to one or more *clauses*, each a complete filter object in
# ``store.py``'s language (``{"assignee": {...}}``, ``{"or": [...]}``). ``_merge`` folds the
# clauses of one group into a single object, moving any clause whose key is already taken
# into ``and`` so two predicates on one property (``state`` and ``stateType``, two date
# bounds) never overwrite each other.

def _cmp(op: str, value: Any) -> dict:
    return {_OPERATORS[op]: value}


def _merge(clauses: list[dict]) -> dict:
    out: dict[str, Any] = {}
    for clause in clauses:
        if len(clause) == 1:
            key = next(iter(clause))
            if key == "and":
                out.setdefault("and", []).extend(clause["and"])
                continue
            if key not in out:
                out[key] = clause[key]
                continue
        out.setdefault("and", []).append(clause)
    return out


# Nobody's id: "me" with no viewer (an internal, unscoped run) matches no row.
_NOBODY = "__nobody__"


class _Context:
    """What a stored filter is resolved against when it runs: the viewer and the clock."""

    def __init__(self, viewer_id: str | None, now: datetime | None) -> None:
        self.viewer_id = viewer_id or _NOBODY
        self.now = now or datetime.now(timezone.utc)


def _reference_column(key: str, v: str) -> str:
    if key == "team":
        return "id" if _UUID.match(v) else "key"
    if key == "user":
        if _UUID.match(v):
            return "id"
        return "email" if "@" in v else "displayName"
    if key == "state":
        # The web client keys a state as "Name|type" (main.tsx ``stateKey``), so the same
        # status picked on two teams selects both teams' rows.
        if _UUID.match(v):
            return "id"
        return "key" if "|" in v else "name"
    if key in ("project", "cycle", "label", "initiative"):
        return "id" if _UUID.match(v) else "name"
    return "id"


def _resolved(ref: str, v: str, ctx: _Context) -> tuple[str, str]:
    """(column, value) for one reference value, with "me" and "myTeams" resolved."""
    if ref == "user" and v == ME:
        return "id", ctx.viewer_id
    if ref == "team" and v == MY_TEAMS:
        return "memberOf", ctx.viewer_id
    return _reference_column(ref, v), v


def _split(op: str, operand: Any) -> tuple[bool, list, bool]:
    """(negative, non-null values, whether null was named) for one predicate."""
    values = operand if isinstance(operand, list) else [operand]
    return op in _NEGATIVE_OPERATORS, [v for v in values if v is not None], None in values


def _combine(negative: bool, parts: list[dict]) -> list[dict]:
    """Positive alternatives are a union (``or``); negative ones must all hold (``and``)."""
    if len(parts) <= 1:
        return parts
    return [{"and": parts}] if negative else [{"or": parts}]


def _by_column(ref: str, values: list, ctx: _Context) -> dict[str, list]:
    out: dict[str, list] = {}
    for v in values:
        column, resolved = _resolved(ref, v, ctx)
        out.setdefault(column, []).append(resolved)
    return out


def _reference_clauses(prop: str, ref: str, op: str, operand: Any, ctx: _Context) -> list[dict]:
    if op == "isMe":
        op, operand = ("is" if operand else "isNot"), ME
    negative, values, named_null = _split(op, operand)
    parts: list[dict] = []
    if named_null:
        parts.append({prop: {"null": not negative}})
    for column, vs in _by_column(ref, values, ctx).items():
        cmp = {"nin" if negative else "in": vs} if len(vs) > 1 or isinstance(operand, list) \
            else {"neq" if negative else "eq": vs[0]}
        parts.append({prop: {column: cmp}})
    return _combine(negative, parts)


def _collection_clauses(prop: str, ref: str, op: str, operand: Any,
                        ctx: _Context) -> list[dict]:
    """A to-many property (labels, subscribers, members, initiatives) in Linear's collection
    grammar: ``some``/``none`` take a filter of the related row, ``null`` means "has none"."""
    if op == "isMe":
        op, operand = ("is" if operand else "isNot"), ME

    def row(column: str, cmp: dict) -> dict:
        # A label also matches through its group, in Linear's own shape: "Labels include
        # area" is ``{or: [{name: eq area}, {parent: {name: eq area}}]}`` (SPEC.md §4,
        # linear-capture/s6-filter-labelgroup-save.jsonl). A plain label has no children,
        # so the second branch only ever widens a group.
        if prop != "labels":
            return {column: cmp}
        return {"or": [{column: cmp}, {"parent": {column: cmp}}]}

    if op == "includesAll":
        return [{prop: {"some": row(column, {"eq": v})}}
                for v in operand for column, v in [_resolved(ref, v, ctx)]]
    negative, values, named_null = _split(op, operand)
    parts: list[dict] = []
    if named_null:
        parts.append({prop: {"null": not negative}})
    for column, vs in _by_column(ref, values, ctx).items():
        parts.append({prop: {"none" if negative else "some": row(column, {"in": vs})}})
    return _combine(negative, parts)


def _day_after(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _utc_timestamp(value: str) -> str:
    """A full timestamp normalized to how ``now_iso`` stores one, so string order is time order."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _date_clauses(prop: str, op: str, value: Any, ctx: _Context) -> list[dict]:
    if value is None:  # dueDate is / isNot null: "No due date"
        return [{prop: {"null": op == "is"}}]
    if value == "overdue":  # due before today
        return [{prop: {"lt": resolve_date("today", ctx.now)}}]
    value = resolve_date(value, ctx.now)
    if prop in _DATE_ONLY:
        # Due, start and target dates are stored as YYYY-MM-DD, so a timestamp compares
        # by its day.
        return [{prop: _cmp(op, value[:10])}]
    if len(value) > 10:
        return [{prop: _cmp(op, _utc_timestamp(value))}]
    # A date-only value on a timestamp column names the whole UTC day.
    start, end = value, _day_after(value)
    bounds = {"is": [("gte", start), ("lt", end)], "before": [("lt", start)],
              "after": [("gte", end)], "onOrBefore": [("lt", end)],
              "onOrAfter": [("gte", start)]}[op]
    return [{prop: {cmp: bound}} for cmp, bound in bounds]


def _nullable_scalar_clauses(prop: str, op: str, operand: Any) -> list[dict]:
    """A number column that may be empty (estimate): a negative predicate keeps the empty
    rows and ``null`` names them, as for references."""
    negative, values, named_null = _split(op, operand)
    parts: list[dict] = []
    if named_null:
        parts.append({prop: {"null": not negative}})
    if values:
        value = values if isinstance(operand, list) else values[0]
        parts.append({prop: _cmp(op, value)})
    if negative and not named_null and values:
        return [{"or": [{prop: {"null": True}}, *parts]}]
    return _combine(negative, parts)


def _compile_predicate(kind: str, key: str, op: str, operand: Any, ctx: _Context) -> list[dict]:
    value_kind = _PROPERTIES[kind][key][1]
    if value_kind == "label":
        return _collection_clauses("labels", "label", op, operand, ctx)
    if value_kind == "subscriber":
        return _collection_clauses("subscribers", "user", op, operand, ctx)
    if value_kind == "member":
        return _collection_clauses("members", "user", op, operand, ctx)
    if value_kind == "initiativeRef":
        return _collection_clauses("initiatives", "initiative", op, operand, ctx)
    if value_kind == "stateType":
        return [{"state": {"type": _cmp(op, operand)}}]
    if value_kind == "date":
        return _date_clauses(key, op, operand, ctx)
    if key == "parent" and op == "isNull":
        return [{"parent": {"null": operand}}]
    if key == "hasSubIssues":
        return [{"hasChildren": operand}]
    if key == "isSubIssue":
        return [{"parent": {"null": not operand}}]
    if value_kind == "float":
        return _nullable_scalar_clauses(key, op, operand)
    if value_kind in ("projectStatus", "health"):
        values = operand if isinstance(operand, list) else [operand]
        # A project without an update has no health: "is null" names it, as "none" does.
        lowered = [v.lower() for v in values] if value_kind == "projectStatus" \
            else ["none" if v is None else v for v in values]
        return [{key: {"nin" if op in _NEGATIVE_OPERATORS else "in": lowered}}]
    if value_kind in _REFERENCE_KINDS:
        return _reference_clauses(key, value_kind, op, operand, ctx)
    if key == "summary":
        return [{"description": _cmp(op, operand)}]
    return [{key: _cmp(op, operand)}]


def _compile_group(kind: str, group: dict, ctx: _Context) -> dict:
    clauses: list[dict] = []
    for key, value in group.items():
        if key == "and":
            clauses.append({"and": [_compile_group(kind, entry, ctx) for entry in value]})
        elif key == "or":
            clauses.append({"or": [_compile_group(kind, entry, ctx) for entry in value]})
        else:
            for op, operand in value.items():
                clauses.extend(_compile_predicate(kind, key, op, operand, ctx))
    return _merge(clauses)


def compile_filter(kind: str, filters: dict, *, viewer_id: str | None = None,
                   now: datetime | None = None) -> dict:
    """Translate a validated filter tree into ``store.py``'s predicate language.

    ``viewer_id`` is who "me" and "myTeams" mean and ``now`` what relative dates are
    measured from; both are run-time inputs, so a stored view stays personal and current.
    ``initiative`` has a single string predicate (``status``), which the caller passes
    straight to ``Store.initiatives``; see ``initiative_statuses``.
    """
    if not filters:
        return {}
    if kind == "initiative":
        statuses = initiative_statuses(filters)
        return {"status": {"in": statuses}} if statuses else {}
    return _compile_group(kind, filters, _Context(viewer_id, now))


def conjoin(*filters: dict | None) -> dict:
    """AND compiled filters together; empty ones drop out."""
    present = [f for f in filters if f]
    if not present:
        return {}
    return present[0] if len(present) == 1 else {"and": present}


def initiative_statuses(filters: dict) -> list[str] | None:
    """The status list an initiative view selects; None when it does not narrow status."""
    for key, value in filters.items():
        if key == "status":
            op, operand = next(iter(value.items()))
            if op == "is":
                return [operand]
            return list(operand)
    if not any(key in _INITIATIVE_PROPERTIES for key in filters):
        raise ViewError(
            "An initiative view filters on status, health, owner, leadTeam or label, for "
            "example {\"status\": {\"is\": \"active\"}}.", "filters")
    return None


def initiative_matches(filters: dict, row: dict) -> bool:
    """Whether a serialized initiative (with health, ownerId, leadTeamId, labelIds
    resolved by the caller) passes the non-status predicates of a view."""
    def wanted(value: dict) -> list:
        op, operand = next(iter(value.items()))
        return [operand] if op == "is" else list(operand)
    for key, value in filters.items():
        if key == "health" and (row.get("health") or "none") not in wanted(value):
            return False
        if key == "owner" and (row.get("ownerId") or "none") not in wanted(value):
            return False
        if key == "leadTeam" and (row.get("leadTeamId") or "none") not in wanted(value):
            return False
        if key == "label" and not set(row.get("labelIds") or []) & set(wanted(value)):
            return False
    return True


def display_order(display: dict, kind: str = "issue") -> tuple[str, bool]:
    """The ordering a display asks for and whether it runs in reverse.

    Issues default to ``updated`` (newest activity first); projects keep their
    historical creation order, which is what ``manual`` means until rows carry a rank.
    """
    ordering = display.get("ordering") or ("updated" if kind == "issue" else "manual")
    return ordering, bool(display.get("reverse"))


def completed_window_start(display: dict, now: datetime | None = None) -> str | None:
    """The earliest completion time a display keeps; None keeps every completed row.

    Returns ``""`` for ``none`` (hide completed and canceled rows entirely).
    """
    window = display.get("completedWindow", DEFAULT_COMPLETED_WINDOW)
    if window == "all":
        return None
    if window == "none":
        return ""
    days = {"day": 1, "week": 7, "month": 30, "quarter": 91, "halfYear": 182}[window]
    return ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()


def page_size(first: Any) -> int:
    if first is None:
        return DEFAULT_PAGE
    return min(max(int(first), 1), MAX_PAGE)


def encode_cursor(signature: str, keys: list) -> str:
    """An opaque cursor: the sort it belongs to plus the last row's sort key and id."""
    payload = json.dumps({"s": signature, "k": keys}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(signature: str, cursor: str | None) -> list | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise ViewError("after is not a cursor this view returned.", "after") from exc
    if not isinstance(value, dict) or not isinstance(value.get("k"), list) \
            or not value["k"] or value.get("s") != signature:
        raise ViewError("after belongs to a different filter or ordering; start again "
                        "without it.", "after")
    # A cursor is client input: only the scalars a sort key holds may reach SQL, so a
    # hand-made cursor cannot turn into a driver error that echoes the statement.
    if any(isinstance(k, bool) or not isinstance(k, (str, int, float)) for k in value["k"]):
        raise ViewError("after is not a cursor this view returned.", "after")
    return value["k"]


# Initiative views run in Python: health and labels are derived, and an organization has
# few initiatives. These mirror the initiatives list's sorter (InitiativesList.tsx) and
# give it the same keyset contract as the SQL orderings: (value, descending, follows
# reverse) per component, the id last, empty values last in both directions.
_HEALTH_RANK = {"off track": 0, "at risk": 1, "on track": 2}


def initiative_sort_components(ordering: str, row: dict) -> list[tuple]:
    def empty_last(value):
        return (1 if value in (None, "", 0) else 0, False, False)

    if ordering == "name":
        keys = [((row.get("name") or "").lower(), False, True)]
    elif ordering == "targetDate":
        keys = [empty_last(row.get("targetDate")), (row.get("targetDate") or "", False, True)]
    elif ordering == "created":
        keys = [(row.get("createdAt") or "", True, True)]
    elif ordering == "health":
        rank = _HEALTH_RANK.get(row.get("health") or "")
        keys = [empty_last(None if rank is None else "x"), (rank or 0, False, True)]
    elif ordering == "priority":
        keys = [empty_last(row.get("priority")), (row.get("priority") or 0, False, True)]
    else:  # manual: the dragged rank, then newest first
        rank = row.get("sortOrder")
        keys = [empty_last(None if rank is None else "x"), (rank or 0, False, True)]
    if ordering != "created":  # ties keep the list's newest-first order
        keys.append((row.get("createdAt") or "", True, True))
    return [*keys, (row["id"], keys[-1][1], True)]


def keyset_compare(a: list, b: list, components: list[tuple], reverse: bool) -> int:
    """Compare two sort keys under ``components``' directions (-1, 0, 1)."""
    for x, y, (_v, desc, follows) in zip(a, b, components, strict=True):
        if x == y:
            continue
        descending = desc != reverse if follows else desc
        return (1 if x < y else -1) if descending else (-1 if x < y else 1)
    return 0


def initiative_passes(filters: dict, row: dict) -> bool:
    """Whether an initiative row (with health, ownerId, leadTeamId and labelIds attached)
    passes a validated initiative filter tree, ``and``/``or`` included."""
    if not filters:
        return True
    for key, value in filters.items():
        if key == "and" and not all(initiative_passes(f, row) for f in value):
            return False
        if key == "or" and not any(initiative_passes(f, row) for f in value):
            return False
        if key == "status":
            op, operand = next(iter(value.items()))
            if row.get("status") not in ([operand] if op == "is" else list(operand)):
                return False
    plain = {k: v for k, v in filters.items() if k not in ("and", "or", "status")}
    return initiative_matches(plain, row)


# ------------------------------------------------------------------- display

_VISIBLE_PROPERTIES = {
    "issue": ("identifier", "id", "title", "status", "assignee", "creator", "priority", "labels",
              "project", "milestone", "cycle", "team", "dueDate", "estimate", "links",
              "timeInStatus", "pullRequests", "createdAt", "updatedAt", "created", "updated"),
    "project": ("name", "status", "lead", "teams", "startDate", "targetDate", "health",
                "progress", "id", "summary", "members", "issues", "createdAt", "updatedAt",
                "completedAt"),
    "initiative": ("name", "status", "owner", "targetDate", "projects"),
}
_DISPLAY_FIELDS = frozenset({"layout", "grouping", "subGrouping", "ordering", "reverse",
                             "visibleProperties", "timelineZoom", "completedWindow",
                             "boardColumnOrder", "showEmptyGroups", "showSubIssues",
                             "nestedSubIssues", "orderCompletedByRecency", "hiddenColumns",
                             "collapsedGroups", "groupingLabelGroupId",
                             "subGroupingLabelGroupId"})


# The initiatives list groups and orders by initiative properties, nests sub-initiatives
# under their parent and can hide summaries; its display document is its own.
INITIATIVE_GROUPINGS = ("none", "status", "health", "owner", "leadTeam")
INITIATIVE_ORDERINGS = ("manual", "name", "targetDate", "created", "health", "priority")


_INITIATIVE_DISPLAY = ("grouping", "ordering", "nested", "summary")


def _parse_initiative_display(document: dict) -> dict:
    out: dict[str, Any] = {}
    if "grouping" in document:
        out["grouping"] = _enum(document["grouping"], INITIATIVE_GROUPINGS, "display.grouping")
    if "ordering" in document:
        out["ordering"] = _enum(document["ordering"], INITIATIVE_ORDERINGS, "display.ordering")
    for name in ("nested", "summary"):
        if name in document:
            out[name] = _boolean(document[name], f"display.{name}")
    return out


def _enum(value: Any, allowed: tuple, field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ViewError(f"{field} must be one of {list(allowed)}.", field)
    return value


def parse_display(kind: str, raw: Any) -> dict:
    """Validate a display-options document for ``kind`` and return the normalized document."""
    if kind not in KINDS:
        raise ViewError(f"kind must be one of {list(KINDS)}.", "kind")
    document = parse_json_document(raw, "display")
    if kind == "initiative":
        own = {k: document[k] for k in _INITIATIVE_DISPLAY if k in document}
        rest = {k: v for k, v in document.items() if k not in _INITIATIVE_DISPLAY}
        return {**(parse_display("project", rest) if rest else {}), **_parse_initiative_display(own)}
    unknown = sorted(set(document) - _DISPLAY_FIELDS)
    if unknown:
        raise ViewError(f"Unknown display fields: {unknown}.", f"display.{unknown[0]}")
    out: dict[str, Any] = {}
    if "layout" in document:
        out["layout"] = _enum(document["layout"], LAYOUTS, "display.layout")
    for name in ("grouping", "subGrouping"):
        if name in document:
            out[name] = _enum(document[name], GROUPINGS, f"display.{name}")
    # Linear's issueGroupingLabelGroupId: which label group's labels become the groups when
    # grouping by ``labelGroup`` (the client falls back to the first group when it is unset).
    for name in ("groupingLabelGroupId", "subGroupingLabelGroupId"):
        if name in document:
            value = document[name]
            if value is not None and (not isinstance(value, str) or not value):
                raise ViewError(f"display.{name} must be a label group id or null.",
                                f"display.{name}")
            out[name] = value
    if "ordering" in document:
        out["ordering"] = _enum(document["ordering"], ORDERINGS, "display.ordering")
    if "reverse" in document:
        out["reverse"] = _boolean(document["reverse"], "display.reverse")
    for name in ("showEmptyGroups", "showSubIssues", "orderCompletedByRecency"):
        if name in document:
            out[name] = _boolean(document[name], f"display.{name}")
    if "nestedSubIssues" in document:
        out["nestedSubIssues"] = _enum(document["nestedSubIssues"], NESTED_SUB_ISSUES,
                                       "display.nestedSubIssues")
    if "completedWindow" in document:
        out["completedWindow"] = _enum(document["completedWindow"], COMPLETED_WINDOWS,
                                       "display.completedWindow")
    if "timelineZoom" in document:
        zoom = document["timelineZoom"]
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) \
                or not MIN_ZOOM <= float(zoom) <= MAX_ZOOM:
            raise ViewError(
                f"display.timelineZoom must be a number from {MIN_ZOOM} to {MAX_ZOOM}.",
                "display.timelineZoom")
        out["timelineZoom"] = float(zoom)
    if "visibleProperties" in document:
        allowed = _VISIBLE_PROPERTIES[kind]
        values = document["visibleProperties"]
        if not isinstance(values, list):
            raise ViewError("display.visibleProperties must be a list of property names.",
                            "display.visibleProperties")
        if len(set(values)) != len(values):
            raise ViewError("display.visibleProperties must not repeat a property.",
                            "display.visibleProperties")
        for i, name in enumerate(values):
            if not isinstance(name, str) or name not in allowed:
                raise ViewError(
                    f"display.visibleProperties[{i}] must be one of {list(allowed)} "
                    f"for a {kind} view.",
                    f"display.visibleProperties[{i}]")
        out["visibleProperties"] = list(values)
    if "boardColumnOrder" in document:
        values = document["boardColumnOrder"]
        if not isinstance(values, list) or len(values) > MAX_COLUMNS:
            raise ViewError(
                f"display.boardColumnOrder must be a list of at most {MAX_COLUMNS} group keys.",
                "display.boardColumnOrder")
        out["boardColumnOrder"] = [_text(v, f"display.boardColumnOrder[{i}]", 80)
                                   for i, v in enumerate(values)]
    for name in ("hiddenColumns", "collapsedGroups"):
        if name in document:
            values = document[name]
            if not isinstance(values, list) or len(values) > MAX_COLUMNS:
                raise ViewError(
                    f"display.{name} must be a list of at most {MAX_COLUMNS} group keys.",
                    f"display.{name}")
            out[name] = list(dict.fromkeys(_text(v, f"display.{name}[{i}]", 80)
                                           for i, v in enumerate(values)))
    return out


def dump(document: dict) -> str:
    """Serialize a validated document for storage, deterministically."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def load(value: Any) -> dict:
    """Read a stored document; a corrupt row reads as empty rather than crashing a query."""
    if not value:
        return {}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------- preferences
#
# Display preferences are layered the way Linear's ViewPreferences are: a default every
# viewer starts from (a saved view's own ``display``, or an organization row for a built-in
# page), and one row per user on top of it. ``effective`` is the default with the user's
# keys laid over it. Both layers are display documents validated by ``parse_display``.

_VIEW_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_.-]+){0,4}$")
PREFERENCE_SCOPES = ("user", "default")


def parse_view_type(value: Any) -> str:
    """A built-in page's preference key: ``my-issues``, ``team:<id>:active``, ``project:<id>``."""
    if not isinstance(value, str) or len(value) > 200 or not _VIEW_TYPE.match(value):
        raise ViewError("viewType must look like \"my-issues\", \"team:<teamId>:active\" or "
                        "\"project:<projectId>\" (letters, digits, '-', '_', '.', ':').",
                        "viewType")
    return value


def view_type_kind(view_type: str) -> str:
    """Which entity a built-in page lists: a ``projects`` or ``initiatives`` segment names
    those lists; everything else is a task list."""
    segments = view_type.split(":")
    if "initiatives" in segments:
        return "initiative"
    if "projects" in segments:
        return "project"
    return "issue"


def parse_preference_values(kind: str, raw: Any) -> tuple[dict, list[str]]:
    """(keys to set, keys to clear). A ``null`` value clears that key from the layer."""
    document = parse_json_document(raw, "values")
    cleared = sorted(k for k, v in document.items() if v is None)
    present = {k: v for k, v in document.items() if v is not None}
    try:
        parsed = parse_display(kind, present)
    except ViewError as exc:
        field = (exc.field or "").replace("display.", "values.", 1) or "values"
        raise ViewError(str(exc).replace("display.", "values."), field) from exc
    unknown = [k for k in cleared if k not in _DISPLAY_FIELDS and k not in _INITIATIVE_DISPLAY]
    if unknown:
        raise ViewError(f"Unknown display fields: {unknown}.", f"values.{unknown[0]}")
    return parsed, cleared


def layer(base: dict, values: dict, cleared: list[str] | tuple = ()) -> dict:
    """``base`` with ``values`` laid over it and ``cleared`` keys removed."""
    out = {k: v for k, v in (base or {}).items() if k not in cleared}
    out.update(values or {})
    return out
