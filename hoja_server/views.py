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

import json
import re
from datetime import date, datetime
from typing import Any

KINDS = ("issue", "project", "initiative")
SCOPES = ("workspace", "team", "project", "initiative")
VISIBILITIES = ("private", "workspace", "team")
LAYOUTS = ("list", "board", "timeline")
GROUPINGS = ("none", "status", "assignee", "creator", "priority", "project", "cycle",
             "labels", "team")
ORDERINGS = ("manual", "priority", "updated", "created", "title", "dueDate")
COMPLETED_WINDOWS = ("none", "week", "month", "all")

MAX_DEPTH = 5
MAX_PREDICATES = 200
MAX_LIST = 100
MAX_TEXT = 200
MAX_COLUMNS = 50
MIN_ZOOM, MAX_ZOOM = 0.25, 8.0

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?$")


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
}
_LIST_OPERATORS = frozenset({"isEitherOf", "isNeitherOf", "includesAny", "includesEither",
                             "includesNone", "includesNeither"})

_IDENTITY_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf")
_NUMBER_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf", "isGreaterThan",
               "isGreaterThanOrEqual", "isLessThan", "isLessThanOrEqual")
_TEXT_OPS = ("is", "isNot", "contains", "containsIgnoreCase", "equalsIgnoreCase")
_DATE_OPS = ("is", "before", "after", "onOrBefore", "onOrAfter")
_LABEL_OPS = ("is", "isNot", "isEitherOf", "isNeitherOf", "includesAny", "includesEither",
              "includesNone", "includesNeither")

# property -> (operators, value kind). The value kind decides which reference column a
# human-readable value compiles to: "team" tries id then key, "user" tries id, email,
# then display name; anything else is a literal.
_ISSUE_PROPERTIES = {
    "team": (_IDENTITY_OPS, "team"),
    "state": (_IDENTITY_OPS, "state"),
    "assignee": (_IDENTITY_OPS, "user"),
    "creator": (_IDENTITY_OPS, "user"),
    "project": (_IDENTITY_OPS, "project"),
    "cycle": (_IDENTITY_OPS, "cycle"),
    "parent": (("is", "isNot"), "issue"),
    "priority": (_NUMBER_OPS, "number"),
    "number": (_NUMBER_OPS, "number"),
    "identifier": (_TEXT_OPS, "text"),
    "title": (_TEXT_OPS, "text"),
    "description": (_TEXT_OPS, "text"),
    "createdAt": (_DATE_OPS, "date"),
    "updatedAt": (_DATE_OPS, "date"),
    "dueDate": (_DATE_OPS, "date"),
    "labels": (_LABEL_OPS, "label"),
}
_PROJECT_PROPERTIES = {
    "name": (_TEXT_OPS, "text"),
    "slugId": (("is", "isNot"), "text"),
    "state": (_IDENTITY_OPS, "text"),
    "team": (_IDENTITY_OPS, "team"),
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
# Value kinds that name another row, and therefore compile to a reference column.
_REFERENCE_KINDS = frozenset({"team", "state", "user", "project", "cycle", "issue"})


def _value(kind: str, op: str, value: Any, field: str) -> Any:
    item = {"team": _text, "state": _text, "user": _text, "project": _text,
            "cycle": _text, "issue": _text, "label": _text, "text": _text,
            "number": _integer, "date": _iso}[kind]
    return _values(value, field, item) if op in _LIST_OPERATORS else item(value, field)


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
            predicates[op] = _value(value_kind, op, value, op_field)
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

def _cmp(op: str, value: Any) -> dict:
    return {_OPERATORS[op]: value}


def _reference(key: str, op: str, value: Any) -> dict:
    """Compile one identity predicate to the reference column the value names."""
    def column(v: str) -> str:
        if key == "team":
            return "id" if _UUID.match(v) else "key"
        if key == "user":
            if _UUID.match(v):
                return "id"
            return "email" if "@" in v else "displayName"
        # Store._resolve_state_ids resolves states by name, type and team only.
        if key == "state":
            return "name"
        if key in ("project", "cycle"):
            return "id" if _UUID.match(v) else "name"
        return "id"

    if isinstance(value, list):
        columns = {column(v) for v in value}
        if len(columns) != 1:
            raise ViewError(
                f"A single predicate cannot mix {key} ids and names; split it into separate predicates.",
                key)
        return {columns.pop(): _cmp(op, value)}
    return {column(value): _cmp(op, value)}


def _compile_group(kind: str, group: dict) -> dict:
    properties = _PROPERTIES[kind]
    out: dict[str, Any] = {}
    for key, value in group.items():
        if key in ("and", "or"):
            out[key] = [_compile_group(kind, entry) for entry in value]
            continue
        value_kind = properties[key][1]
        compiled: dict[str, Any] = {}
        for op, operand in value.items():
            if value_kind in _REFERENCE_KINDS:
                compiled.update(_reference(value_kind, op, operand))
            else:
                compiled.update(_cmp(op, operand))
        out[key] = compiled
    return out


def compile_filter(kind: str, filters: dict) -> dict:
    """Translate a validated filter tree into ``store.py``'s predicate language.

    ``initiative`` has a single string predicate (``status``), which the caller passes
    straight to ``Store.initiatives``; see ``initiative_statuses``.
    """
    if not filters:
        return {}
    if kind == "initiative":
        statuses = initiative_statuses(filters)
        return {"status": {"in": statuses}} if statuses else {}
    return _compile_group(kind, filters)


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


def display_order(display: dict) -> tuple[str, bool]:
    """The ``Store.issues`` ordering a display asks for, plus whether to reverse the page."""
    ordering = display.get("ordering", "updated")
    order_by = {"updated": "updatedAt", "created": "createdAt"}.get(ordering, "updatedAt")
    return order_by, bool(display.get("reverse"))


# ------------------------------------------------------------------- display

_VISIBLE_PROPERTIES = {
    "issue": ("identifier", "title", "status", "assignee", "creator", "priority", "labels",
              "project", "cycle", "team", "dueDate", "estimate", "createdAt", "updatedAt"),
    "project": ("name", "status", "lead", "teams", "startDate", "targetDate", "health",
                "progress"),
    "initiative": ("name", "status", "owner", "targetDate", "projects"),
}
_DISPLAY_FIELDS = frozenset({"layout", "grouping", "subGrouping", "ordering", "reverse",
                             "visibleProperties", "timelineZoom", "completedWindow",
                             "boardColumnOrder", "showEmptyGroups"})


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
    if "ordering" in document:
        out["ordering"] = _enum(document["ordering"], ORDERINGS, "display.ordering")
    if "reverse" in document:
        out["reverse"] = _boolean(document["reverse"], "display.reverse")
    if "showEmptyGroups" in document:
        out["showEmptyGroups"] = _boolean(document["showEmptyGroups"], "display.showEmptyGroups")
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
