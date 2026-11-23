"""Linear's ``CustomView.filterData`` / ``projectFilterData`` ↔ Hoja's view filter grammar.

Linear stores a view's filter as an ``IssueFilter`` / ``ProjectFilter`` document:
properties hold comparator objects (``{"priority": {"lte": 2}}``), references hold a filter
of the referenced row (``{"assignee": {"isMe": {"eq": true}}}``), to-many relations hold
``some``/``none`` (``{"labels": {"some": {"name": {"in": ["Bug"]}}}}``), and ``and``/``or``
group. Hoja stores views in its own grammar (``hoja_server.views``). The ``customView*``
aliases translate in both directions so a Linear client reads and writes the shape it
knows while the row keeps one canonical document.

``to_hoja`` refuses what it cannot translate with ``ViewError`` naming the path, rather than
dropping a predicate: a view that silently selects more than asked is worse than an error.
``to_linear`` is total over the Hoja grammar, using Linear's comparator names.
"""
from __future__ import annotations

import re
from typing import Any

from hoja_server.views import ME, MY_TEAMS, ViewError

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_IDENTITY = {"eq": "is", "neq": "isNot", "in": "isEitherOf", "nin": "isNeitherOf"}
_NUMBER = {**_IDENTITY, "lt": "isLessThan", "lte": "isLessThanOrEqual",
           "gt": "isGreaterThan", "gte": "isGreaterThanOrEqual"}
_TEXT = {"eq": "is", "neq": "isNot", "contains": "contains",
         "containsIgnoreCase": "containsIgnoreCase", "eqIgnoreCase": "equalsIgnoreCase"}
_DATE = {"eq": "is", "lt": "before", "gt": "after", "lte": "onOrBefore", "gte": "onOrAfter"}

# Linear property -> (Hoja property, shape). Shapes: "ref" (a referenced row filtered by
# id/key/name/email/displayName), "user" (ref plus isMe), "number", "text", "date",
# "collection" (some/none of a referenced row), "userCollection".
_ISSUE = {
    "team": ("team", "team"), "state": ("state", "state"),
    "assignee": ("assignee", "user"), "creator": ("creator", "user"),
    "project": ("project", "ref"), "cycle": ("cycle", "ref"),
    "parent": ("parent", "parent"), "children": ("hasSubIssues", "children"),
    "priority": ("priority", "number"), "estimate": ("estimate", "number"),
    "number": ("number", "number"),
    "title": ("title", "text"), "description": ("description", "text"),
    "createdAt": ("createdAt", "date"), "updatedAt": ("updatedAt", "date"),
    "dueDate": ("dueDate", "date"), "startedAt": ("startedAt", "date"),
    "completedAt": ("completedAt", "date"), "canceledAt": ("canceledAt", "date"),
    "labels": ("labels", "collection"), "subscribers": ("subscriber", "userCollection"),
}
_PROJECT = {
    "id": ("id", "idcmp"), "name": ("name", "text"), "slugId": ("slugId", "text"),
    "description": ("summary", "text"), "summary": ("summary", "text"),
    "state": ("state", "idcmp"), "status": ("status", "status"),
    "lead": ("lead", "user"), "creator": ("creator", "user"),
    "members": ("members", "userCollection"), "health": ("health", "idcmp"),
    "startDate": ("startDate", "date"), "targetDate": ("targetDate", "date"),
    "initiatives": ("initiative", "collection"), "accessibleTeams": ("team", "teamCollection"),
}
_KINDS = {"issue": _ISSUE, "project": _PROJECT}
_REFERENCE_FIELDS = ("id", "key", "name", "email", "displayName")


def _refuse(path: str, what: str) -> ViewError:
    return ViewError(f"{path}: {what} has no Hoja equivalent, so this view cannot be "
                     "saved without changing what it selects.", path)


def _ops(cmp: Any, table: dict, path: str) -> dict:
    if not isinstance(cmp, dict) or not cmp:
        raise ViewError(f"{path} must be a comparator object such as {{\"eq\": …}}.", path)
    out = {}
    for op, value in cmp.items():
        if op == "null":
            out["is" if value else "isNot"] = None
            continue
        if op not in table:
            raise _refuse(f"{path}.{op}", f"comparator '{op}'")
        out[table[op]] = value
    return out


def _reference(prop: str, shape: str, body: Any, path: str) -> list[dict]:
    """``{"id": {"eq": …}}``-style reference filters as Hoja predicates on ``prop``."""
    if not isinstance(body, dict) or not body:
        raise ViewError(f"{path} must be a filter object.", path)
    out: list[dict] = []
    for field, cmp in body.items():
        where = f"{path}.{field}"
        if field == "null":
            out.append({prop: {"is" if cmp else "isNot": None}})
        elif field == "isMe" and shape in ("user", "userCollection"):
            if not isinstance(cmp, dict) or set(cmp) != {"eq"}:
                raise _refuse(where, "an isMe comparator other than eq")
            out.append({prop: {"isMe": bool(cmp["eq"])}})
        elif field == "type" and shape == "state":
            out.append({"stateType": _ops(cmp, _IDENTITY, where)})
        elif field == "members" and shape == "team":
            # Linear's "my teams": {"team": {"members": {"some": {"isMe": {"eq": true}}}}}
            if body.get("members") == {"some": {"isMe": {"eq": True}}}:
                out.append({"team": {"is": MY_TEAMS}})
            else:
                raise _refuse(where, "a team members filter other than \"my teams\"")
        elif field in _REFERENCE_FIELDS:
            if shape == "team" and field == "name":
                raise _refuse(where, "a team name filter (use the team id or key)")
            out.append({prop: _ops(cmp, _IDENTITY, where)})
        elif field in ("and", "or"):
            raise _refuse(where, f"a nested {field} inside a reference")
        else:
            raise _refuse(where, f"'{field}'")
    return out


def _collection(prop: str, shape: str, body: Any, path: str) -> list[dict]:
    if not isinstance(body, dict) or not body:
        raise ViewError(f"{path} must be a filter object.", path)
    out: list[dict] = []
    ref_shape = "user" if shape == "userCollection" else "team" if shape == "teamCollection" \
        else "ref"
    for field, inner in body.items():
        where = f"{path}.{field}"
        if field == "null":
            out.append({prop: {"is" if inner else "isNot": None}})
        elif field in ("some", "none"):
            for predicate in _reference(prop, ref_shape, inner, where):
                ops = predicate[prop]
                if field == "none":
                    flipped = {}
                    for op, value in ops.items():
                        if op == "isMe":
                            flipped[op] = not value
                        elif op in ("is", "isEitherOf"):
                            flipped["isNot" if op == "is" else "isNeitherOf"] = value
                        else:
                            raise _refuse(where, "a negative comparator inside none")
                    ops = flipped
                out.append({prop: ops if shape == "teamCollection" else _as_collection(ops)})
        elif field in _REFERENCE_FIELDS and shape == "collection":
            out.append({prop: _as_collection(_ops(inner, _IDENTITY, where))})
        else:
            raise _refuse(where, f"'{field}' on a collection")
    return out


def _as_collection(ops: dict) -> dict:
    """Identity operators on one related row as Hoja's collection operators."""
    out = {}
    for op, value in ops.items():
        if op == "isMe" or value is None:
            out[op] = value
            continue
        values = value if isinstance(value, list) else [value]
        out["includesNone" if op in ("isNot", "isNeitherOf") else "includesAny"] = values
    return out


def _group(kind: str, raw: Any, path: str) -> dict:
    if not isinstance(raw, dict):
        raise ViewError(f"{path or 'filterData'} must be a filter object.", path or "filterData")
    table = _KINDS[kind]
    predicates: list[dict] = []
    for key, body in raw.items():
        where = f"{path}{key}"
        if key in ("and", "or"):
            if not isinstance(body, list) or not body:
                raise ViewError(f"{where} must be a non-empty list.", where)
            predicates.append({key: [_group(kind, entry, f"{where}[{i}].")
                                     for i, entry in enumerate(body)]})
            continue
        if key not in table:
            raise _refuse(where, f"the {kind} filter '{key}'")
        prop, shape = table[key]
        if shape in ("team", "state", "user", "ref"):
            predicates += _reference(prop, shape, body, where)
        elif shape == "parent":
            # ``isNull`` is the IssueParentFilter extension Hoja's issues query accepts
            # (the CLI's --no-parent sends it); Linear itself spells it ``null``.
            if isinstance(body, dict) and len(body) == 1 and set(body) <= {"null", "isNull"} \
                    and isinstance(next(iter(body.values())), bool):
                predicates.append({"parent": {"isNull": next(iter(body.values()))}})
            else:
                predicates += _reference("parent", "ref", body, where)
        elif shape == "children":
            if body in ({"some": {}}, {"null": False}):
                predicates.append({"hasSubIssues": {"is": True}})
            elif body in ({"none": {}}, {"null": True}):
                predicates.append({"hasSubIssues": {"is": False}})
            else:
                raise _refuse(where, "a children filter other than some/none")
        elif shape == "number":
            predicates.append({prop: _ops(body, _NUMBER, where)})
        elif shape == "text":
            predicates.append({prop: _ops(body, _TEXT, where)})
        elif shape == "date":
            predicates.append({prop: _ops(body, _DATE, where)})
        elif shape == "idcmp":
            predicates.append({prop: _ops(body, _IDENTITY, where)})
        elif shape == "status":
            if not isinstance(body, dict) or set(body) - {"name"}:
                raise _refuse(where, "a project status filter other than by name")
            predicates.append({prop: _ops(body["name"], _IDENTITY, f"{where}.name")})
        else:  # collection, userCollection, teamCollection
            predicates += _collection(prop, shape, body, where)
    return _fold(predicates)


def _fold(predicates: list[dict]) -> dict:
    """Merge single-property predicates into one group; a repeated key moves into ``and``."""
    out: dict[str, Any] = {}
    for predicate in predicates:
        (key, value), = predicate.items()
        if key in ("and", "or") and key in out:
            out[key] = out[key] + value
        elif key not in out:
            out[key] = value
        elif key not in ("and", "or") and not set(out[key]) & set(value):
            out[key] = {**out[key], **value}
        else:
            out.setdefault("and", []).append(predicate)
    return out


def to_hoja(kind: str, filter_data: Any) -> dict:
    """Translate Linear ``filterData`` (issue) or ``projectFilterData`` (project)."""
    if filter_data in (None, {}):
        return {}
    if kind not in _KINDS:
        raise ViewError("Only issue and project filters translate from Linear's shape.",
                        "filterData")
    return _group(kind, filter_data, "")


# ------------------------------------------------------------------ reverse

_REV_IDENTITY = {v: k for k, v in _IDENTITY.items()}
_REV_NUMBER = {v: k for k, v in _NUMBER.items()}
_REV_TEXT = {v: k for k, v in _TEXT.items()}
_REV_DATE = {v: k for k, v in _DATE.items()}


def _ref_field(prop_shape: str, value: str) -> str:
    if _UUID.match(value):
        return "id"
    if prop_shape == "team":
        return "key"
    if prop_shape in ("user", "userCollection"):
        return "email" if "@" in value else "displayName"
    return "name"


def _rev_reference(shape: str, ops: dict) -> dict:
    out: dict[str, Any] = {}
    for op, value in ops.items():
        if op == "isMe":
            out["isMe"] = {"eq": bool(value)}
            continue
        if op == "isNull":
            out["null"] = bool(value)
            continue
        if value is None:
            out["null"] = op == "is"
            continue
        values = value if isinstance(value, list) else [value]
        if ME in values and shape in ("user", "userCollection"):
            out["isMe"] = {"eq": op in ("is", "isEitherOf", "includesAny", "includesEither",
                                        "includesAll")}
            values = [v for v in values if v != ME]
        for v in values:
            if v is None:
                out["null"] = op in ("is", "isEitherOf")
                continue
            field = _ref_field(shape, v)
            cmp = _REV_IDENTITY.get(op, "eq")
            bucket = out.setdefault(field, {})
            if isinstance(value, list):
                bucket.setdefault(cmp, []).append(v)
            else:
                bucket[cmp] = v
    return out


_ISSUE_REV = {hoja: (linear, shape) for linear, (hoja, shape) in _ISSUE.items()}
_PROJECT_REV = {hoja: (linear, shape) for linear, (hoja, shape) in _PROJECT.items()
                if linear not in ("description",)}


_POSITIVE = frozenset({"is", "isEitherOf", "includesAny", "includesEither"})


def _put(out: dict, linear: str, body: dict) -> None:
    """Lay one constraint on ``linear`` into ``out``. Linear ANDs the fields of one filter
    object, so a constraint whose fields clash with what is there goes into ``and``."""
    if linear not in out:
        out[linear] = body
    elif not set(out[linear]) & set(body):
        out[linear] = {**out[linear], **body}
    else:
        out.setdefault("and", []).append({linear: body})


def _put_any(out: dict, linear: str, bodies: list[dict]) -> None:
    """At least one of ``bodies`` holds: Linear's fields AND, so a disjunction is an ``or``."""
    if len(bodies) == 1:
        _put(out, linear, bodies[0])
    else:
        out.setdefault("and", []).append({"or": [{linear: b} for b in bodies]})


def _split_fields(body: dict) -> list[dict]:
    return [{field: cmp} for field, cmp in body.items()]


def _rev_ref_property(out: dict, linear: str, shape: str, ops: dict) -> None:
    for op, value in ops.items():
        body = _rev_reference(shape, {op: value})
        if op in _POSITIVE and len(body) > 1:
            # "assignee is me or nobody": each field is an alternative, not a conjunct.
            _put_any(out, linear, _split_fields(body))
        else:
            _put(out, linear, body)


def _rev_collection_property(out: dict, linear: str, shape: str, ops: dict) -> None:
    ref_shape = "userCollection" if shape == "userCollection" else \
        "team" if shape == "teamCollection" else "ref"
    for op, v in ops.items():
        if v is None:
            _put(out, linear, {"null": op in ("is", "isEitherOf", "includesAny",
                                              "includesEither")})
            continue
        if op == "isMe":
            _put(out, linear, {"some" if v else "none": {"isMe": {"eq": True}}})
            continue
        values = v if isinstance(v, list) else [v]
        nulls = [x for x in values if x is None]
        values = [x for x in values if x is not None]
        negative = op in ("isNot", "isNeitherOf", "includesNone", "includesNeither")
        if op == "includesAll":
            # Every value must be present: one ``some`` per value, all of them required.
            for x in values:
                _put(out, linear, {"some": _rev_reference(ref_shape, {"is": x})})
            continue
        parts = _split_fields(_rev_reference(ref_shape, {"isEitherOf": values})) \
            if values else []
        if negative:
            # None of the values: every field's ``none`` must hold.
            for part in parts:
                _put(out, linear, {"none": part})
            if nulls:
                _put(out, linear, {"null": False})
        else:
            alternatives = [{"some": part} for part in parts]
            if nulls:
                alternatives.append({"null": True})
            _put_any(out, linear, alternatives)


def _rev_group(kind: str, group: dict) -> dict:
    table = _ISSUE_REV if kind == "issue" else _PROJECT_REV
    out: dict[str, Any] = {}
    for key, ops in group.items():
        if key in ("and", "or"):
            out[key] = out.get(key, []) + [_rev_group(kind, g) for g in ops]
            continue
        if key == "stateType":
            _put(out, "state", {"type": {_REV_IDENTITY.get(op, "eq"): v
                                         for op, v in ops.items()}})
            continue
        if key == "isSubIssue":
            _put(out, "parent", {"null": not ops.get("is")})
            continue
        if key == "team" and ops.get("is") == MY_TEAMS:
            _put(out, "team", {"members": {"some": {"isMe": {"eq": True}}}})
            continue
        if key == "health":
            linear_health = {"on track": "onTrack", "at risk": "atRisk", "off track": "offTrack"}
            ops = {op: ([linear_health.get(x, x) for x in v] if isinstance(v, list)
                        else linear_health.get(v, v)) for op, v in ops.items()}
        linear, shape = table.get(key, (key, "text"))
        if shape in ("team", "state", "user", "ref", "parent"):
            _rev_ref_property(out, linear, shape, ops)
        elif shape == "children":
            _put(out, linear, {"some": {}} if ops.get("is") else {"none": {}})
        elif shape == "number":
            _put(out, linear, {("null" if v is None else _REV_NUMBER[op]):
                               (op == "is" if v is None else v) for op, v in ops.items()})
        elif shape == "text" or shape == "idcmp":
            _put(out, linear, {(_REV_TEXT.get(op) or _REV_IDENTITY[op]): v
                               for op, v in ops.items()})
        elif shape == "status":
            _put(out, linear, {"name": {_REV_IDENTITY[op]: v for op, v in ops.items()}})
        elif shape == "date":
            _put(out, linear, {("null" if v is None else _REV_DATE[op]):
                               (op == "is" if v is None else v) for op, v in ops.items()})
        else:  # collections
            _rev_collection_property(out, linear, shape, ops)
    return out


def to_linear(kind: str, filters: dict) -> dict:
    """Hoja's stored filter in Linear's ``filterData`` / ``projectFilterData`` shape."""
    if not filters or kind not in _KINDS:
        return {}
    return _rev_group(kind, filters)
