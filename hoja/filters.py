"""Filter DSL → Linear's IssueFilter input.

Maps friendly CLI flags (--team ENG, --state "In Progress") onto Linear's
GraphQL filter input shape.

References:
- https://linear.app/developers/filtering
- IssueFilter type in schema/linear-schema.json
"""

from __future__ import annotations

from typing import Any


def build_issue_filter(
    *,
    team: str | None = None,
    state: str | list[str] | None = None,
    not_state: str | list[str] | None = None,
    state_type: str | list[str] | None = None,
    not_state_type: str | list[str] | None = None,
    assignee: str | None = None,
    creator: str | None = None,
    project: str | None = None,
    cycle: str | None = None,
    label: str | list[str] | None = None,
    priority: int | None = None,
    contains: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    viewer_id: str | None = None,
) -> dict[str, Any]:
    """Build an IssueFilter dict from CLI flags.

    `viewer_id` is required if any field resolves to "me".
    """
    f: dict[str, Any] = {}

    if team:
        # Accept either team key (ENG) or team UUID
        if _looks_like_uuid(team):
            f["team"] = {"id": {"eq": team}}
        else:
            f["team"] = {"key": {"eq": team.upper()}}

    if state is not None:
        f["state"] = _str_or_list_filter("name", state)
    if not_state is not None:
        f.setdefault("state", {}).update(_str_or_list_negate("name", not_state))

    if state_type is not None:
        f["state"] = {**f.get("state", {}), **_str_or_list_filter("type", state_type)}
    if not_state_type is not None:
        f["state"] = {**f.get("state", {}), **_str_or_list_negate("type", not_state_type)}

    if assignee:
        f["assignee"] = _user_subfilter(assignee, viewer_id)

    if creator:
        f["creator"] = _user_subfilter(creator, viewer_id)

    if project:
        if _looks_like_uuid(project):
            f["project"] = {"id": {"eq": project}}
        else:
            f["project"] = {"name": {"containsIgnoreCase": project}}

    if cycle:
        if cycle.lower() == "current":
            # Linear supports cycle: { isActive: { eq: true } } — but only via Team scope
            # For simplicity expose via cycle filter using number from the resolved cycle id
            f["cycle"] = {"isActive": {"eq": True}}
        elif cycle.lower() == "next":
            f["cycle"] = {"isNext": {"eq": True}}
        elif _looks_like_uuid(cycle):
            f["cycle"] = {"id": {"eq": cycle}}
        else:
            f["cycle"] = {"name": {"containsIgnoreCase": cycle}}

    if label is not None:
        f["labels"] = {"name": _str_or_list_op(label)}

    if priority is not None:
        f["priority"] = {"eq": priority}

    if contains:
        # Linear's free-text on issue is title/description — use `or`
        f["or"] = [
            {"title": {"containsIgnoreCase": contains}},
            {"description": {"containsIgnoreCase": contains}},
        ]

    if updated_after:
        f["updatedAt"] = {**f.get("updatedAt", {}), "gt": updated_after}
    if updated_before:
        f["updatedAt"] = {**f.get("updatedAt", {}), "lt": updated_before}

    if due_after:
        f["dueDate"] = {**f.get("dueDate", {}), "gt": due_after}
    if due_before:
        f["dueDate"] = {**f.get("dueDate", {}), "lt": due_before}

    return f


def _looks_like_uuid(value: str) -> bool:
    """Cheap UUID detector — Linear IDs are 36-char UUIDs with hyphens."""
    return len(value) == 36 and value.count("-") == 4


def _str_or_list_op(value: str | list[str]) -> dict[str, Any]:
    """Produce eq/in clause depending on type."""
    if isinstance(value, str):
        if "," in value:
            return {"in": [v.strip() for v in value.split(",")]}
        return {"eq": value}
    return {"in": value}


def _str_or_list_filter(field: str, value: str | list[str]) -> dict[str, Any]:
    return {field: _str_or_list_op(value)}


def _str_or_list_negate(field: str, value: str | list[str]) -> dict[str, Any]:
    op = _str_or_list_op(value)
    if "eq" in op:
        return {field: {"neq": op["eq"]}}
    if "in" in op:
        return {field: {"nin": op["in"]}}
    return {}


def _user_subfilter(value: str, viewer_id: str | None) -> dict[str, Any]:
    """Resolve 'me', a UUID, or an email to a Linear user filter."""
    v = value.strip()
    if v.lower() == "me":
        if not viewer_id:
            raise ValueError(
                "Used 'me' as assignee/creator but viewer hasn't been resolved"
            )
        return {"id": {"eq": viewer_id}}
    if _looks_like_uuid(v):
        return {"id": {"eq": v}}
    if "@" in v:
        return {"email": {"eq": v}}
    # Fall back: display name contains
    return {"displayName": {"containsIgnoreCase": v}}
