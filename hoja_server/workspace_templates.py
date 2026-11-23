"""Workspace templates: portable bundles of workspace settings (HOJA-742).

A template is one JSON document that describes how a workspace should be set up:
workspace labels, task templates, project templates, SLA policies, and the
notification kinds a member gets until they choose for themselves. The
``knowledge`` and ``agentPolicyDefault`` sections that Hoja Cloud templates carry
are accepted and ignored, so a Cloud template or export still loads here.

Three rules shape this module.

**Portable.** A template never carries a row id. Labels, templates, policies and
documents are matched by name, workflow states by their
*type* ("backlog"), and project statuses by name. Anything that only means
something in one workspace (a team, a person, a project, a repository) is left
out, so the same file loads into any workspace, Cloud or self-hosted.

**Additive.** Applying a template creates what is missing and updates what it
names; it never archives or deletes. A field the template leaves out keeps the
workspace's value. Applying the same template twice changes nothing the second
time, which is what lets an admin re-apply after editing the file.

**One plan, two uses.** ``plan`` computes every step without writing. The preview
query returns that plan; the apply mutation computes it again inside its
transaction and executes exactly those steps, so what an admin approved is what
runs. Every validator is the one the per-setting mutation uses, called with the
same connection, so a template can never store a value the settings pages would
refuse.
"""

from __future__ import annotations

import json
from typing import Any

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server import sla as sla_module
from hoja_server import task_templates as task_templates_module
from hoja_server.db import (
    issue_label,
    new_id,
    now_iso,
    project_status,
    project_template,
    sla_policy,
    task_template,
    workflow_state,
    workspace_default,
)
from hoja_server.notifications import KINDS as NOTIFICATION_KINDS

FORMAT = "hoja.workspace-template"
VERSION = 1

NOTIFICATION_DEFAULTS_KEY = "notification_defaults"
SECTIONS = ("labels", "taskTemplates", "projectTemplates", "slaPolicies", "notificationDefaults")
# Sections a Hoja Cloud template may carry (agent knowledge and agent policy). They are
# accepted so the same file loads everywhere, and ignored because this edition has
# neither feature.
CLOUD_ONLY_SECTIONS = ("knowledge", "agentPolicyDefault")
TOP_LEVEL = {"format", "version", "id", "name", "description", *SECTIONS, *CLOUD_ONLY_SECTIONS}
MAX_ITEMS = 100

ITEM_FIELDS = {
    "labels": {"name", "color", "description", "group", "isGroup", "exclusive"},
    "taskTemplates": {"name", "description", "defaultTitle", "defaultDescription",
                      "defaultPriority", "defaultLabels"},
    "projectTemplates": {"name", "description", "color", "icon", "state"},
    "slaPolicies": {"name", "appliesPriorities", "firstResponseMinutes", "resolutionMinutes",
                    "pauseStateTypes", "cycleBound", "stopsOn", "businessDaysOnly", "enabled"},
}
STATE_TYPES = ("triage", "backlog", "unstarted", "started", "completed", "canceled")

query = QueryType()
mutation = MutationType()

SDL = gql(
    """
    "A ready-made workspace template that ships with Hoja."
    type WorkspaceTemplate {
      id: String!
      name: String!
      description: String!
      "The template document (format hoja.workspace-template, version 1)."
      document: JSON!
    }

    "One change a template makes, or would make."
    type WorkspaceTemplateStep {
      "labels | taskTemplates | projectTemplates | slaPolicies | notificationDefaults"
      section: String!
      "The item's name."
      name: String!
      "create | update | unchanged"
      action: String!
      "The fields an update changes; empty for create and unchanged."
      fields: [String!]!
    }

    type WorkspaceTemplatePlan {
      name: String!
      steps: [WorkspaceTemplateStep!]!
      creates: Int!
      updates: Int!
      unchanged: Int!
      "How many open tasks got an SLA clock (apply with startSlaClocks only)."
      startedClocks: Int!
    }

    type WorkspaceTemplateExport {
      document: JSON!
      "Team-scoped settings left out because they only mean something in this workspace."
      omitted: [String!]!
    }

    extend type Query {
      "Templates that ship with Hoja. Workspace admins only."
      workspaceTemplates: [WorkspaceTemplate!]!
      "What applying this document would change, without changing anything."
      workspaceTemplatePreview(document: JSON!): WorkspaceTemplatePlan!
      "This workspace's settings as a template document."
      workspaceTemplateExport: WorkspaceTemplateExport!
    }

    extend type Mutation {
      "Apply a template: create what is missing, update what it names, delete nothing."
      workspaceTemplateApply(document: JSON!, startSlaClocks: Boolean): WorkspaceTemplatePlan!
    }
    """
)


class TemplateError(ValueError):
    """A template document that is not acceptable; ``path`` names the offending field."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path


def _bad(path: str, message: str):
    raise TemplateError(path, message)


def _gql_error(error: TemplateError) -> GraphQLError:
    return GraphQLError(f"{error.path}: {error}" if error.path else str(error),
                        extensions={"code": "BAD_USER_INPUT", "field": error.path})


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


# ---------------------------------------------------------------- the document


def _text(value, path, *, required=False, limit=200) -> str | None:
    if value is None:
        if required:
            _bad(path, "is required.")
        return None
    if not isinstance(value, str):
        _bad(path, "must be text.")
    clean = value.strip()
    if required and not clean:
        _bad(path, "must not be empty.")
    if len(clean) > limit:
        _bad(path, f"must be at most {limit} characters.")
    return clean or None


def _bool(value, path) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        _bad(path, "must be true or false.")
    return value


def _int(value, path, low, high) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _bad(path, f"must be a whole number from {low} to {high}.")
    return value


def _items(doc, section) -> list[dict]:
    raw = doc.get(section)
    if raw is None:
        return []
    if not isinstance(raw, list):
        _bad(section, "must be a list.")
    if len(raw) > MAX_ITEMS:
        _bad(section, f"may hold at most {MAX_ITEMS} items.")
    out, seen = [], set()
    name_field = "name"
    for i, item in enumerate(raw):
        path = f"{section}[{i}]"
        if not isinstance(item, dict):
            _bad(path, "must be an object.")
        unknown = set(item) - ITEM_FIELDS[section]
        if unknown:
            _bad(f"{path}.{sorted(unknown)[0]}",
                 "is not a field a template can carry in this section.")
        name = _text(item.get(name_field), f"{path}.{name_field}", required=True, limit=120)
        if _key(name) in seen:
            _bad(f"{path}.{name_field}", f"'{name}' appears twice in {section}.")
        seen.add(_key(name))
        out.append({**item, name_field: name, "_path": path})
    return out


def parse(doc: Any) -> dict:
    """Validate the document's shape. References are resolved later, against a workspace."""
    if not isinstance(doc, dict):
        _bad("", "A template must be a JSON object.")
    if doc.get("format") != FORMAT:
        _bad("format", f"must be '{FORMAT}'. Is this a Hoja workspace template?")
    if doc.get("version") != VERSION:
        _bad("version", f"must be {VERSION}; this Hoja reads version {VERSION} templates.")
    unknown = set(doc) - TOP_LEVEL
    if unknown:
        _bad(sorted(unknown)[0], "is not a section of a workspace template.")
    parsed = {
        "name": _text(doc.get("name"), "name", required=True, limit=120),
        "description": _text(doc.get("description"), "description", limit=2000) or "",
    }
    for section in ITEM_FIELDS:
        parsed[section] = _items(doc, section)
    for item in parsed["labels"]:
        if item.get("group") is not None and item.get("isGroup"):
            _bad(f"{item['_path']}.group", "A label group cannot sit inside another group.")
    for item in parsed["labels"]:
        if item.get("group") is not None:
            item["group"] = _text(item["group"], f"{item['_path']}.group", required=True)
    notif = doc.get("notificationDefaults")
    if notif is not None:
        if not isinstance(notif, dict):
            _bad("notificationDefaults", "must be an object of notification kind to true/false.")
        for kind, on in notif.items():
            if kind not in NOTIFICATION_KINDS:
                _bad(f"notificationDefaults.{kind}", "is not a notification kind.")
            _bool(on, f"notificationDefaults.{kind}")
    parsed["notificationDefaults"] = notif
    return parsed


# ---------------------------------------------------------------- reading the workspace


def _live(conn, table, org, *extra):
    cond = [table.c.organization_id == org]
    if "archived_at" in table.c:
        cond.append(table.c.archived_at.is_(None))
    return [dict(r._mapping) for r in conn.execute(select(table).where(and_(*cond, *extra))).all()]


def _by_name(rows, field="name"):
    return {_key(r[field]): r for r in rows}


def get_default(conn, org: str, key: str):
    row = conn.execute(select(workspace_default.c.value).where(and_(
        workspace_default.c.organization_id == org, workspace_default.c.key == key))).first()
    return json.loads(row[0]) if row else None


def _set_default(conn, org, key, value, uid, at):
    exists = conn.execute(select(workspace_default.c.key).where(and_(
        workspace_default.c.organization_id == org, workspace_default.c.key == key))).first()
    payload = json.dumps(value, sort_keys=True)
    if exists:
        conn.execute(workspace_default.update().where(and_(
            workspace_default.c.organization_id == org, workspace_default.c.key == key)).values(
                value=payload, updated_by=uid, updated_at=at))
    else:
        conn.execute(workspace_default.insert().values(
            organization_id=org, key=key, value=payload, updated_by=uid, updated_at=at))


def notification_defaults(conn, org: str) -> dict[str, bool]:
    """The workspace's default per notification kind; kinds it does not name are on."""
    return dict(get_default(conn, org, NOTIFICATION_DEFAULTS_KEY) or {})


def _state_ids_of_types(conn, org, types):
    if not types:
        return []
    return [r[0] for r in conn.execute(select(workflow_state.c.id).where(and_(
        workflow_state.c.organization_id == org,
        workflow_state.c.type.in_(types))).order_by(workflow_state.c.id)).all()]


# ---------------------------------------------------------------- planning


def _diff(current: dict, wanted: dict) -> list[str]:
    return sorted(k for k, v in wanted.items() if current.get(k) != v)


def _step(section, name, current, values, **extra):
    if current is None:
        return {"section": section, "name": name, "action": "create", "fields": [],
                "values": values, **extra}
    # Matching is by name in any case, so the name itself is never an update: re-applying
    # "bug" keeps the workspace's "Bug" rather than renaming it.
    values = {k: v for k, v in values.items() if k not in ("name", "title")}
    fields = _diff(current, values)
    return {"section": section, "name": name, "action": "update" if fields else "unchanged",
            "fields": fields, "values": {k: values[k] for k in fields}, "id": current["id"], **extra}


def _wrap(path, fn, *args, **kwargs):
    """Run a per-setting validator and re-raise its error against the template path."""
    try:
        return fn(*args, **kwargs)
    except GraphQLError as error:
        field = (error.extensions or {}).get("field")
        _bad(path, f"{error.message}" + (f" ({field})" if field else ""))


def plan(conn, org: str, parsed: dict) -> list[dict]:
    steps: list[dict] = []

    # Labels: groups first, so a child can name a group this template creates.
    labels = _by_name(_live(conn, issue_label, org, issue_label.c.team_id.is_(None)))
    template_groups = {_key(i["name"]) for i in parsed["labels"] if i.get("isGroup")}
    for item in sorted(parsed["labels"], key=lambda i: not i.get("isGroup")):
        path = item["_path"]
        values: dict[str, Any] = {"name": item["name"]}
        for src, col in (("color", "color"), ("description", "description")):
            if item.get(src) is not None:
                values[col] = _text(item[src], f"{path}.{src}", limit=500)
        if item.get("isGroup") is not None:
            values["is_group"] = _bool(item["isGroup"], f"{path}.isGroup")
        if item.get("exclusive") is not None:
            values["exclusive"] = _bool(item["exclusive"], f"{path}.exclusive")
        group_name = item.get("group")
        if group_name is not None:
            gkey = _key(group_name)
            existing = labels.get(gkey)
            if gkey not in template_groups and not (existing and existing["is_group"]):
                _bad(f"{path}.group", f"'{group_name}' is not a label group in this template "
                                      "or this workspace.")
            values["_group"] = gkey
        current = labels.get(_key(item["name"]))
        if current is not None:
            # A label does not become a group, or stop being one, by re-applying a template:
            # its tasks would lose or gain meaning silently. Only the label pages do that.
            values.pop("is_group", None)
            current = {**current, "_group": _group_key(current, labels)}
            if values.get("_group") and current["is_group"]:
                _bad(f"{path}.group", f"'{item['name']}' is a label group in this workspace, "
                                      "and a group cannot sit inside another group.")
        steps.append(_step("labels", item["name"], current, values))

    # Task templates: workspace level only, labels by name.
    templates = _by_name(_live(conn, task_template, org, task_template.c.team_id.is_(None)))
    label_names = {_key(r["name"]): r for r in labels.values()}
    template_label_names = {_key(i["name"]) for i in parsed["labels"]}
    for item in parsed["taskTemplates"]:
        path = item["_path"]
        values = {"name": item["name"]}
        if "description" in item:
            values["description"] = _text(item["description"], f"{path}.description", limit=2000)
        if "defaultTitle" in item:
            values["default_title"] = _wrap(f"{path}.defaultTitle",
                                            task_templates_module._title_as_authored,
                                            item["defaultTitle"])
        elif _key(item["name"]) not in templates:
            _bad(f"{path}.defaultTitle", "is required for a new task template.")
        if "defaultDescription" in item:
            values["default_description"] = _text(item["defaultDescription"],
                                                   f"{path}.defaultDescription", limit=20000)
        if "defaultPriority" in item:
            values["default_priority"] = _int(item["defaultPriority"], f"{path}.defaultPriority", 0, 4)
        if "defaultLabels" in item:
            names = item["defaultLabels"] or []
            if not isinstance(names, list):
                _bad(f"{path}.defaultLabels", "must be a list of label names.")
            for n in names:
                if not isinstance(n, str) or (_key(n) not in label_names
                                              and _key(n) not in template_label_names):
                    _bad(f"{path}.defaultLabels", f"'{n}' is not a workspace label in this "
                                                  "template or this workspace.")
            values["_labels"] = sorted(_key(n) for n in names)
        current = templates.get(_key(item["name"]))
        if current is not None:
            ids = json.loads(current["default_label_ids"]) if current["default_label_ids"] else []
            by_id = {r["id"]: r for r in labels.values()}
            current = {**current, "_labels": sorted(_key(by_id[i]["name"]) for i in ids if i in by_id)}
        steps.append(_step("taskTemplates", item["name"], current, values))

    # Project templates: no teams, no lead, no task set; status by name.
    statuses = {_key(r["name"]): r["name"] for r in conn.execute(select(project_status.c.name).where(
        project_status.c.organization_id == org)).mappings().all()}
    if not statuses:
        # A workspace that never opened project settings has its defaults seeded on
        # first use; ``execute`` seeds them before writing a project template.
        from hoja_server.project_settings import DEFAULT_PROJECT_STATUSES
        statuses = {_key(name): name for name, _category in DEFAULT_PROJECT_STATUSES}
    ptemplates = _by_name(_live(conn, project_template, org))
    for item in parsed["projectTemplates"]:
        path = item["_path"]
        values = {"name": item["name"]}
        for field in ("description", "color", "icon"):
            if field in item:
                values[field] = _text(item[field], f"{path}.{field}", limit=2000)
        if item.get("state") is not None:
            state = _text(item["state"], f"{path}.state", required=True)
            if _key(state) not in statuses:
                _bad(f"{path}.state", f"'{state}' is not a project status in this workspace. "
                                      f"Configured: {', '.join(statuses.values()) or 'none'}.")
            values["state"] = statuses[_key(state)]
        steps.append(_step("projectTemplates", item["name"], ptemplates.get(_key(item["name"])), values))

    # SLA policies: workspace level, pause states by type across every team.
    policies = _by_name(_live(conn, sla_policy, org, sla_policy.c.team_id.is_(None)))
    for item in parsed["slaPolicies"]:
        path = item["_path"]
        types = item.get("pauseStateTypes") or []
        if not isinstance(types, list) or any(t not in STATE_TYPES for t in types):
            _bad(f"{path}.pauseStateTypes", "must list workflow state types: "
                                            + ", ".join(STATE_TYPES) + ".")
        current = policies.get(_key(item["name"]))
        inp = {"name": item["name"]}
        mapping = {"appliesPriorities": "appliesPriorities",
                   "firstResponseMinutes": "firstResponseMinutes",
                   "resolutionMinutes": "resolutionMinutes", "cycleBound": "cycleBound",
                   "stopsOn": "stopsOn", "businessDaysOnly": "businessDaysOnly",
                   "enabled": "enabled"}
        for src, dst in mapping.items():
            if src in item:
                inp[dst] = item[src]
        # The SLA validator needs a complete input; an update fills gaps from the row.
        if current is not None:
            inp.setdefault("firstResponseMinutes", current["first_response_minutes"])
            inp.setdefault("resolutionMinutes", current["resolution_minutes"])
            inp.setdefault("appliesPriorities", json.loads(current["applies_priorities"])
                           if current["applies_priorities"] else None)
            inp.setdefault("cycleBound", current["cycle_bound"])
            inp.setdefault("stopsOn", current["stops_on"])
            inp.setdefault("businessDaysOnly", current["business_days_only"])
        if "pauseStateTypes" in item or current is None:
            inp["pauseStateIds"] = _state_ids_of_types(conn, org, types)
        elif current["pause_state_ids"]:
            inp["pauseStateIds"] = json.loads(current["pause_state_ids"])
        values = _wrap(path, sla_module._values, conn, org, inp, creating=current is None)
        steps.append(_step("slaPolicies", item["name"], current, values))

    # Workspace defaults: one document each, replaced as a whole.
    for section, key, label in (("notificationDefaults", NOTIFICATION_DEFAULTS_KEY, "New members"),):
        wanted = parsed.get(section)
        if wanted is None:
            continue
        current = get_default(conn, org, key)
        action = "create" if current is None else ("unchanged" if current == wanted else "update")
        fields = [] if action != "update" else sorted(
            k for k in set(current) | set(wanted) if current.get(k) != wanted.get(k))
        steps.append({"section": section, "name": label, "action": action, "fields": fields,
                      "values": wanted, "_key": key})
    return steps


def _group_key(row, labels):
    if not row.get("parent_id"):
        return None
    for key, other in labels.items():
        if other["id"] == row["parent_id"]:
            return key
    return None


# ---------------------------------------------------------------- applying


def execute(conn, org: str, uid: str, steps: list[dict], *, start_sla_clocks: bool) -> int:
    at = now_iso()
    started = 0
    if any(s["section"] == "projectTemplates" and s["action"] != "unchanged" for s in steps):
        from hoja_server.project_settings import ensure_default_statuses
        ensure_default_statuses(conn, org)
    label_ids = {_key(r["name"]): r["id"] for r in _live(conn, issue_label, org,
                                                         issue_label.c.team_id.is_(None))}
    for step in steps:
        if step["action"] == "unchanged":
            continue
        section, values = step["section"], dict(step["values"])
        if section == "labels":
            group = values.pop("_group", None)
            if group is not None:
                values["parent_id"] = label_ids[group]
            if step["action"] == "create":
                lid = new_id()
                conn.execute(issue_label.insert().values(
                    id=lid, organization_id=org, team_id=None,
                    is_group=bool(values.pop("is_group", False)),
                    exclusive=values.pop("exclusive", True),
                    created_at=at, updated_at=at, **values))
                label_ids[_key(step["name"])] = lid
            else:
                conn.execute(issue_label.update().where(and_(
                    issue_label.c.organization_id == org, issue_label.c.id == step["id"])).values(
                        updated_at=at, **values))
        elif section == "taskTemplates":
            names = values.pop("_labels", None)
            if names is not None:
                ids = [label_ids[n] for n in names]
                values["default_label_ids"] = json.dumps(ids) if ids else None
            if step["action"] == "create":
                conn.execute(task_template.insert().values(
                    id=new_id(), organization_id=org, team_id=None, created_at=at,
                    updated_at=at, **values))
            else:
                conn.execute(task_template.update().where(and_(
                    task_template.c.organization_id == org,
                    task_template.c.id == step["id"])).values(updated_at=at, **values))
        elif section == "projectTemplates":
            if step["action"] == "create":
                conn.execute(project_template.insert().values(
                    id=new_id(), organization_id=org, created_at=at, updated_at=at, **values))
            else:
                conn.execute(project_template.update().where(and_(
                    project_template.c.organization_id == org,
                    project_template.c.id == step["id"])).values(updated_at=at, **values))
        elif section == "slaPolicies":
            if step["action"] == "create":
                pid = new_id()
                conn.execute(sla_policy.insert().values(
                    id=pid, organization_id=org, created_at=at, updated_at=at, **values))
            else:
                pid = step["id"]
                conn.execute(sla_policy.update().where(and_(
                    sla_policy.c.organization_id == org, sla_policy.c.id == pid)).values(
                        updated_at=at, **values))
            if start_sla_clocks:
                row = dict(conn.execute(select(sla_policy).where(sla_policy.c.id == pid)).first()._mapping)
                if row["enabled"]:
                    started += sla_module.apply_to_open(conn, org, row, at)
        else:
            _set_default(conn, org, step["_key"], values, uid, at)
    return started


def _summary(name, steps, started=0) -> dict:
    public = [{k: s[k] for k in ("section", "name", "action", "fields")} for s in steps]
    count = lambda a: sum(1 for s in steps if s["action"] == a)  # noqa: E731
    return {"name": name, "steps": public, "creates": count("create"),
            "updates": count("update"), "unchanged": count("unchanged"),
            "startedClocks": started}


# ---------------------------------------------------------------- exporting


def export(conn, org: str) -> tuple[dict, list[str]]:
    omitted: list[str] = []
    labels = _live(conn, issue_label, org)
    by_id = {r["id"]: r for r in labels}
    team_labels = [r for r in labels if r["team_id"]]
    if team_labels:
        omitted.append(f"{len(team_labels)} team labels")
    out_labels = []
    for r in sorted((r for r in labels if not r["team_id"]), key=lambda r: (not r["is_group"], r["name"])):
        item = {"name": r["name"]}
        if r["color"]:
            item["color"] = r["color"]
        if r["description"]:
            item["description"] = r["description"]
        if r["is_group"]:
            item["isGroup"] = True
            item["exclusive"] = bool(r["exclusive"])
        if r["parent_id"] and r["parent_id"] in by_id:
            item["group"] = by_id[r["parent_id"]]["name"]
        out_labels.append(item)

    out_templates = []
    for r in sorted(_live(conn, task_template, org), key=lambda r: r["name"]):
        if r["team_id"]:
            omitted.append(f"task template '{r['name']}' (team)")
            continue
        item = {"name": r["name"], "defaultTitle": r["default_title"]}
        if r["description"]:
            item["description"] = r["description"]
        if r["default_description"]:
            item["defaultDescription"] = r["default_description"]
        if r["default_priority"] is not None:
            item["defaultPriority"] = r["default_priority"]
        ids = json.loads(r["default_label_ids"]) if r["default_label_ids"] else []
        if ids:
            item["defaultLabels"] = [by_id[i]["name"] for i in ids if i in by_id]
        out_templates.append(item)

    out_projects = []
    for r in sorted(_live(conn, project_template, org), key=lambda r: r["name"]):
        item = {"name": r["name"]}
        for field in ("description", "color", "icon", "state"):
            if r[field]:
                item[field] = r[field]
        out_projects.append(item)

    types = {r["id"]: r["type"] for r in conn.execute(select(workflow_state.c.id, workflow_state.c.type).where(
        workflow_state.c.organization_id == org)).mappings().all()}
    out_policies = []
    for r in sorted(_live(conn, sla_policy, org), key=lambda r: r["name"]):
        if r["team_id"]:
            omitted.append(f"SLA policy '{r['name']}' (team)")
            continue
        item: dict[str, Any] = {"name": r["name"], "enabled": bool(r["enabled"])}
        if r["applies_priorities"]:
            item["appliesPriorities"] = json.loads(r["applies_priorities"])
        if r["first_response_minutes"]:
            item["firstResponseMinutes"] = r["first_response_minutes"]
        if r["resolution_minutes"]:
            item["resolutionMinutes"] = r["resolution_minutes"]
        ids = json.loads(r["pause_state_ids"]) if r["pause_state_ids"] else []
        item["pauseStateTypes"] = sorted({types[i] for i in ids if i in types})
        item["cycleBound"] = bool(r["cycle_bound"])
        if r["stops_on"]:
            item["stopsOn"] = r["stops_on"]
        item["businessDaysOnly"] = bool(r["business_days_only"])
        out_policies.append(item)

    document: dict[str, Any] = {
        "format": FORMAT, "version": VERSION,
        "name": "Exported workspace settings",
        "description": "",
        "labels": out_labels, "taskTemplates": out_templates,
        "projectTemplates": out_projects, "slaPolicies": out_policies,
    }
    notif = get_default(conn, org, NOTIFICATION_DEFAULTS_KEY)
    if notif is not None:
        document["notificationDefaults"] = notif
    return document, omitted


# ---------------------------------------------------------------- GraphQL


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can use workspace templates.",
                           extensions={"code": "FORBIDDEN"})


@query.field("workspaceTemplates")
def r_templates(_, info):
    from hoja_server.workspace_template_builtins import BUILTINS
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    # The built-ins are shared with Hoja Cloud; serve them without the sections this
    # edition ignores, so nothing offers what applying would not do.
    return [{"id": t["id"], "name": t["document"]["name"],
             "description": t["document"]["description"],
             "document": {k: v for k, v in t["document"].items() if k not in CLOUD_ONLY_SECTIONS}}
            for t in BUILTINS]


@query.field("workspaceTemplatePreview")
def r_preview(_, info, document):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        parsed = parse(document)
        with store.engine.connect() as conn:
            steps = plan(conn, org, parsed)
    except TemplateError as error:
        raise _gql_error(error) from None
    return _summary(parsed["name"], steps)


@query.field("workspaceTemplateExport")
def r_export(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        document, omitted = export(conn, org)
    return {"document": document, "omitted": omitted}


@mutation.field("workspaceTemplateApply")
def m_apply(_, info, document, **arguments):
    from hoja_server import audit
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        parsed = parse(document)
        with store.engine.begin() as conn:
            steps = plan(conn, org, parsed)
            started = execute(conn, org, uid, steps,
                              start_sla_clocks=bool(arguments.get("startSlaClocks")))
    except TemplateError as error:
        raise _gql_error(error) from None
    result = _summary(parsed["name"], steps, started)
    if result["creates"] or result["updates"]:
        audit.record_request(store, info, user_id=uid, action="workspace.template.apply",
                             target_kind="workspace", target_id=org,
                             detail={"template": parsed["name"], "creates": result["creates"],
                                     "updates": result["updates"],
                                     "startedClocks": started,
                                     "steps": [f"{s['section']}:{s['name']}:{s['action']}"
                                               for s in steps if s["action"] != "unchanged"]})
    return result


__all__ = [
    "SDL",
    "execute",
    "export",
    "get_default",
    "mutation",
    "notification_defaults",
    "parse",
    "plan",
    "query",
]
