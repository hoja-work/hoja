"""HOJA-288: the task* surface.

Every name here is an alias of an existing issue* resolver — no new resolver logic
exists in this module, by design. That is what makes the rename additive: if the
underlying resolver changes, both names change together and cannot drift.
"""
from __future__ import annotations

from ariadne import ObjectType

# root field -> the issue* field whose resolver it reuses
ROOT_ALIASES: dict[str, str] = {
    "task": "issue",
    "tasks": "issues",
    "searchTasks": "searchIssues",
    "taskCreate": "issueCreate",
    "taskUpdate": "issueUpdate",
    "taskDelete": "issueDelete",
    "taskArchive": "issueArchive",
    "taskSubscribe": "issueSubscribe",
    "taskUnsubscribe": "issueUnsubscribe",
    "myTaskActivity": "myIssueActivity",
    "taskLabels": "issueLabels",
    "taskLabelCreate": "issueLabelCreate",
    "taskLabelUpdate": "issueLabelUpdate",
    "taskLabelDelete": "issueLabelDelete",
    "taskLabelSettings": "issueLabelSettings",
}

# aliased types that carry their own field resolvers (everything else is a plain
# wrapper whose fields read straight off the serialized dict)
TYPE_ALIASES: dict[str, str] = {
    "Task": "Issue",
    "MyTaskActivity": "MyIssueActivity",
}
# member renames inside an aliased type: (new type, new field) -> (old type, old field)
FIELD_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("MyTaskActivity", "task"): ("MyIssueActivity", "issue"),
}

# Aliased types that need no copied resolvers, only a member pointing at the old key.
SIMPLE_FIELD_ALIASES: dict[str, tuple[str, str]] = {
    "TaskPayload": ("task", "issue"),
}


def bind_task_surface(query, mutation, issue, my_issue_activity) -> tuple[list, list[str]]:
    """Return (bindables, problems). problems is non-empty if a mapping target is missing."""
    problems: list[str] = []
    bindables: list = []
    sources = {"Issue": issue, "MyIssueActivity": my_issue_activity}

    for new_type, old_type in TYPE_ALIASES.items():
        src = sources.get(old_type)
        if src is None:
            problems.append(f"missing source ObjectType for {old_type}")
            continue
        # Members renamed away inside this type are rebound by FIELD_ALIASES below;
        # copying them here would bind a field the aliased type does not declare.
        renamed_away = {old for (nt, _n), (ot, old) in FIELD_ALIASES.items() if nt == new_type and ot == old_type}
        task_type = ObjectType(new_type)
        for field, fn in list(src._resolvers.items()):
            if field in renamed_away:
                continue
            task_type.set_field(field, fn)
        bindables.append(task_type)

    for (new_type, new_field), (old_type, old_field) in FIELD_ALIASES.items():
        src = sources.get(old_type)
        fn = (src._resolvers.get(old_field) if src else None)
        if fn is None:
            problems.append(f"missing resolver {old_type}.{old_field} for {new_type}.{new_field}")
            continue
        target = next((b for b in bindables if b.name == new_type), None)
        if target is None:
            problems.append(f"missing aliased type {new_type}")
            continue
        target.set_field(new_field, fn)

    for new_type, (new_field, old_field) in SIMPLE_FIELD_ALIASES.items():
        task_type = ObjectType(new_type)
        task_type.set_alias(new_field, old_field)
        bindables.append(task_type)

    for new, old in ROOT_ALIASES.items():
        for root in (query, mutation):
            fn = root._resolvers.get(old)
            if fn is not None:
                root.set_field(new, fn)
                break
        else:
            problems.append(f"missing root resolver for {old}")

    return bindables, problems
