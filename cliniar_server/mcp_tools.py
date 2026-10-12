"""Agent-facing MCP tools generated from the Hoja GraphQL schema.

Each allowlisted GraphQL root field becomes one MCP tool with:
- a typed JSON input schema (GraphQL args; a single `input` object is flattened),
- friendly references (team keys, issue identifiers, "me"/emails/names, state,
  label and project names) resolved to ids before execution,
- a server-chosen selection that returns the readable entity, so agents can
  verify what they read or changed,
- specific errors (unknown args, missing scope, not found, invalid reference)
  raised as ToolError so the SDK forwards the text to the model.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from graphql import (
    GraphQLEnumType,
    GraphQLError,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    Undefined,
    execute,
    parse,
    validate,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import ToolAnnotations
from pydantic import ConfigDict

from cliniar_server.denials import DENY_HINT

log = logging.getLogger("hoja.mcp")

READ_TOOLS: dict[str, str] = {
    "viewer": "The signed-in user (id, name, email, admin).",
    "workspace": "The current workspace (organization).",
    "organizationMembers": "People in the workspace. Use to find someone before assigning.",
    "teams": "Teams with their keys. Issue identifiers are TEAMKEY-N (e.g. ENG-12).",
    "team": "One team by key or id, with its workflow states, members and active cycle.",
    "issues": (
        "List issues, newest first. Example filter: "
        '{"team": {"key": {"eq": "ENG"}}, "state": {"type": {"nin": ["completed", "canceled"]}}}. '
        "State types: backlog, unstarted, started, completed, canceled."
    ),
    "issue": "One issue by identifier (ENG-12) or id, with state, assignee, labels and comments.",
    "searchIssues": "Text search over issue titles, descriptions and identifiers.",
    "myIssueActivity": "Recent activity on issues involving the signed-in user.",
    "inboxNotifications": "The signed-in user's inbox notifications.",
    "inboxUnreadCount": "Number of unread inbox notifications.",
    "projects": "List projects.",
    "project": "One project by id or name.",
    "projectUpdates": "Status updates posted on projects.",
    "initiatives": "List initiatives.",
    "initiative": "One initiative by id.",
    "workflowStates": "Workflow states. States are per team; filter with {\"team\": {\"key\": {\"eq\": \"ENG\"}}}.",
    "cycles": "List cycles (sprints).",
    "cycle": "One cycle by id.",
    "teamCycleCadence": "Whether cycles are enabled for a team, plus its active and upcoming cycles.",
    "issueLabels": "Issue labels (workspace-wide and per team).",
}

WRITE_TOOLS: dict[str, str] = {
    "issueCreate": "Create an issue. Returns the created issue including its identifier.",
    "issueUpdate": (
        "Update an issue: title, description, priority, state (by name), assignee "
        "('me', email or name), project, cycle, labels, estimate, due date. Send ONLY the fields to change; "
        "every field you send is written (labelIds replaces all labels). Returns the updated issue."
    ),
    "issueArchive": "Archive an issue (reversible; prefer this over delete).",
    "issueDelete": "Permanently delete an issue. Confirm with the user first.",
    "commentCreate": "Add a comment to an issue.",
    "commentUpdate": "Edit a comment.",
    "commentDelete": "Delete a comment.",
    "issueLabelCreate": "Create a label, optionally scoped to a team.",
    "issueLabelUpdate": "Rename or recolor a label.",
    "issueLabelDelete": "Delete a label.",
    "projectCreate": "Create a project, optionally linked to teams.",
    "projectUpdate": "Update a project.",
    "projectArchive": "Archive a project.",
    "projectUpdateCreate": "Post a status update on a project.",
    "initiativeCreate": "Create an initiative.",
    "initiativeUpdate": "Update an initiative.",
    "initiativeArchive": "Archive an initiative.",
    "attachmentCreate": "Attach a link to an issue.",
    "teamCreate": "Create a team.",
    "teamUpdate": "Rename a team or change its key or description.",
    "teamArchive": "Archive a team.",
    "teamMemberAdd": "Add a workspace member to a team (admin only).",
    "teamMemberRemove": "Remove a member from a team (admin only).",
    "teamCycleCadenceEnable": "Enable weekly cycles for a team (admin only). weekday: 0=Monday ... 6=Sunday.",
    "notificationMarkRead": "Mark an inbox notification read.",
    "notificationArchive": "Archive an inbox notification.",
    "notificationMarkAllRead": "Mark every inbox notification read.",
}

DESTRUCTIVE = {"issueDelete", "commentDelete", "issueLabelDelete", "teamMemberRemove",
               "issueArchive", "projectArchive", "initiativeArchive", "teamArchive"}
IDEMPOTENT_PREFIXES = ("notificationMark", "notificationArchive")

FIELD_HINTS = {
    "teamId": "Team key (e.g. \"ENG\") or id.",
    "teamIds": "Team keys or ids.",
    "stateId": "Workflow state name (e.g. \"In Progress\") or id. States belong to the issue's team.",
    "assigneeId": "\"me\", an email, an exact member name, or a user id.",
    "leadId": "\"me\", an email, an exact member name, or a user id.",
    "ownerId": "\"me\", an email, an exact member name, or a user id.",
    "userId": "\"me\", an email, an exact member name, or a user id.",
    "labelIds": "Label names or ids. Replaces the issue's labels.",
    "projectId": "Project name or id.",
    "projectIds": "Project names or ids.",
    "issueId": "Issue identifier (e.g. ENG-12) or id.",
    "parentId": "Parent issue id.",
    "priority": "1 urgent, 2 high, 3 medium, 4 low (or those names). On updates use \"none\" to clear; 0 is ignored.",
    "first": "Page size.",
    "after": "Cursor from a previous pageInfo.endCursor.",
    "dueDate": "ISO-8601 date-time.",
}
ID_HINTS = {
    "issue": "Issue identifier (e.g. ENG-12) or id.",
    "team": "Team key (e.g. ENG) or id.",
    "project": "Project name or id.",
    "issueLabel": "Label name or id.",
}
PRIORITY_NAMES = {"none": 0, "no priority": 0, "urgent": 1, "high": 2, "medium": 3, "normal": 3, "low": 4}

_SCALARS = {
    "String": {"type": "string"}, "ID": {"type": "string"}, "Int": {"type": "integer"},
    "Float": {"type": "number"}, "Boolean": {"type": "boolean"},
    "DateTime": {"type": "string", "description": "ISO-8601 date-time."},
    "TimelessDate": {"type": "string", "description": "YYYY-MM-DD."},
}

# Readable fields kept when an object is referenced from another one.
_REF_FIELDS = ("id", "identifier", "key", "name", "displayName", "email", "title", "type",
               "number", "startsAt", "endsAt", "status", "state", "priorityLabel", "body", "url")
# Noisy fields dropped from list rows.
_ROW_SKIP = {"description", "branchName", "avatarUrl", "color", "icon", "statusEmoji",
             "statusLabel", "position", "changes"}
# Never selected: credentials and noisy relations.
_NEVER = {"apiKey", "secret", "inviteCode", "activity", "subscribers"}


def _named(t):
    while isinstance(t, (GraphQLNonNull, GraphQLList)):
        t = t.of_type
    return t


def _is_object(t) -> bool:
    return isinstance(_named(t), GraphQLObjectType)


def _is_connection(t) -> bool:
    n = _named(t)
    return isinstance(n, GraphQLObjectType) and "nodes" in n.fields and "pageInfo" in n.fields


def _usable(field) -> bool:
    return not any(isinstance(a.type, GraphQLNonNull) and a.default_value is Undefined for a in field.args.values())


def _selection(t, level: str) -> str:
    """level: ref (identity only), row (list item), full (single entity)."""
    obj = _named(t)
    parts: list[str] = []
    for name, field in obj.fields.items():
        if name in _NEVER or not _usable(field):
            continue
        if not _is_object(field.type):
            if level == "ref" and name not in _REF_FIELDS:
                continue
            if level == "row" and name in _ROW_SKIP:
                continue
            parts.append(name)
        elif level == "ref":
            continue
        elif _is_connection(field.type):
            if level == "full":
                inner = _selection(_named(field.type).fields["nodes"].type, "row")
                if inner:
                    parts.append(f"{name} {{ nodes {{ {inner} }} }}")
        else:
            inner = _selection(field.type, "ref")
            if inner:
                parts.append(f"{name} {{ {inner} }}")
    if not parts and level == "ref":
        leaves = [n for n, f in obj.fields.items() if not _is_object(f.type) and n not in _NEVER]
        parts = leaves[:1]
    return " ".join(parts)


def _root_selection(field) -> str:
    t = field.type
    if not _is_object(t):
        return ""
    if _is_connection(t):
        rows = _selection(_named(t).fields["nodes"].type, "row")
        return f"{{ nodes {{ {rows} }} pageInfo {{ hasNextPage endCursor }} }}"
    obj = _named(t)
    if "success" in obj.fields:  # mutation payload: success + affected entity
        parts = ["success"]
        for name, sub in obj.fields.items():
            if name == "success" or name in _NEVER:
                continue
            if _is_object(sub.type):
                parts.append(f"{name} {{ {_selection(sub.type, 'full')} }}")
            else:
                parts.append(name)
        return "{ " + " ".join(parts) + " }"
    level = "row" if isinstance(t, GraphQLList) or (isinstance(t, GraphQLNonNull) and isinstance(t.of_type, GraphQLList)) else "full"
    return "{ " + _selection(t, level) + " }"


def _json_schema(t, defs: dict) -> dict:
    if isinstance(t, GraphQLNonNull):
        return _json_schema(t.of_type, defs)
    if isinstance(t, GraphQLList):
        return {"type": "array", "items": _json_schema(t.of_type, defs)}
    if isinstance(t, GraphQLEnumType):
        return {"type": "string", "enum": list(t.values)}
    if isinstance(t, GraphQLInputObjectType):
        if t.name not in defs:
            defs[t.name] = {}  # placeholder for recursive inputs
            props, required = {}, []
            for fname, f in t.fields.items():
                props[fname] = _json_schema(f.type, defs)
                if isinstance(f.type, GraphQLNonNull) and f.default_value is Undefined:
                    required.append(fname)
            defs[t.name] = {"type": "object", "properties": props, "additionalProperties": False,
                            **({"required": required} if required else {})}
        return {"$ref": f"#/$defs/{t.name}"}
    return dict(_SCALARS.get(getattr(t, "name", ""), {}))


def _entity_of(op: str) -> str | None:
    for prefix in ("issueLabel", "issue", "team", "project"):
        if op.startswith(prefix):
            return prefix
    return None


class _RawArgs(ArgModelBase):
    """Accept any JSON object; validation happens against the GraphQL types."""

    model_config = ConfigDict(extra="allow")

    def model_dump_one_level(self) -> dict[str, Any]:
        return {"args": dict(self.model_extra or {})}


def _prune(value):
    """Drop placeholder values some clients send for every optional field.

    Removes "", [] and {} and comparator objects with eq == neq, which can never match.
    """
    if isinstance(value, dict):
        pruned = {k: _prune(v) for k, v in value.items()}
        pruned = {k: v for k, v in pruned.items() if v not in ("", [], {})}
        if "eq" in pruned and "neq" in pruned and pruned["eq"] == pruned["neq"]:
            return {}
        return pruned
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


class _Spec:
    def __init__(self, schema: GraphQLSchema, op: str, write: bool, description: str):
        self.op, self.write = op, write
        self.field = schema.get_type("Mutation" if write else "Query").fields[op]
        self.flat_input: GraphQLInputObjectType | None = None
        input_arg = self.field.args.get("input")
        if input_arg is not None and isinstance(_named(input_arg.type), GraphQLInputObjectType):
            fields = _named(input_arg.type).fields
            if not set(fields) & (set(self.field.args) - {"input"}):
                self.flat_input = _named(input_arg.type)

        defs: dict = {}
        props, required = {}, []
        entity = _entity_of(op)
        for name, arg in self.field.args.items():
            if name == "input" and self.flat_input is not None:
                for fname, f in self.flat_input.fields.items():
                    props[fname] = _json_schema(f.type, defs)
                    if isinstance(input_arg.type, GraphQLNonNull) and isinstance(f.type, GraphQLNonNull):
                        required.append(fname)
                continue
            props[name] = _json_schema(arg.type, defs)
            if isinstance(arg.type, GraphQLNonNull) and arg.default_value is Undefined:
                required.append(name)
        for name, schema_part in props.items():
            hint = ID_HINTS.get(entity) if name == "id" and entity else FIELD_HINTS.get(name)
            if op in ("team", "teamCycleCadence", "teamCycleCadenceEnable") and name in ("id", "teamId"):
                hint = ID_HINTS["team"]
            if hint and "$ref" not in schema_part:
                schema_part["description"] = hint
        self.parameters = {"type": "object", "properties": props, "additionalProperties": False}
        if required:
            self.parameters["required"] = required
        if defs:
            self.parameters["$defs"] = defs
        self.description = description

        decls = ", ".join(f"$v_{n}: {a.type}" for n, a in self.field.args.items())
        uses = ", ".join(f"{n}: $v_{n}" for n in self.field.args)
        kind = "mutation" if write else "query"
        self.query = (f"{kind} Hoja{('(' + decls + ')') if decls else ''} "
                      f"{{ {op}{('(' + uses + ')') if uses else ''} {_root_selection(self.field)} }}")
        self.document = parse(self.query)
        errors = validate(schema, self.document)
        if errors:  # a bug in selection generation; fail at startup, not per call
            raise RuntimeError(f"MCP tool {op} has an invalid generated query: {errors[0].message}")

    def assemble(self, args: dict) -> dict:
        allowed = set(self.parameters["properties"]) | ({"input"} if self.flat_input else set())
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ToolError(
                f"Unknown argument(s) {', '.join(unknown)} for hoja_{self.op}. "
                f"Valid arguments: {', '.join(sorted(self.parameters['properties'])) or '(none)'}."
            )
        flat = _prune(args)
        if self.flat_input is not None and isinstance(flat.get("input"), dict):
            flat = {**flat.pop("input"), **flat}
        if self.op.endswith("Update"):
            # Clients that fill every field send 0 for untouched numbers; clearing needs an explicit "none".
            flat = {k: v for k, v in flat.items() if not (k in ("priority", "estimate") and v == 0 and v is not False)}
        for key, value in list(flat.items()):
            gtype = self._type_of(key)
            if isinstance(value, str) and gtype is not None and (
                isinstance(_named(gtype), GraphQLInputObjectType) or isinstance(_strip(gtype), GraphQLList)
            ):
                with contextlib.suppress(ValueError):
                    flat[key] = json.loads(value)
        if isinstance(flat.get("priority"), str):
            p = flat["priority"].strip().lower()
            flat["priority"] = int(p) if p.isdigit() else PRIORITY_NAMES.get(p, flat["priority"])
        return flat

    def _type_of(self, key: str):
        if key in self.field.args:
            return self.field.args[key].type
        if self.flat_input is not None and key in self.flat_input.fields:
            return self.flat_input.fields[key].type
        return None

    def variables(self, flat: dict) -> dict:
        variables = {}
        input_fields = {}
        for key, value in flat.items():
            if self.flat_input is not None and key in self.flat_input.fields and key not in self.field.args:
                input_fields[key] = value
            else:
                variables["v_" + key] = value
        if self.flat_input is not None and (input_fields or isinstance(self.field.args["input"].type, GraphQLNonNull)):
            variables["v_input"] = input_fields
        return variables


def _strip(t):
    return t.of_type if isinstance(t, GraphQLNonNull) else t


class _Resolver:
    """Map friendly references (keys, names, emails, 'me') to ids via the same GraphQL context."""

    def __init__(self, run: Callable[[str, dict | None], dict], viewer_id: str):
        self.run, self.viewer_id = run, viewer_id
        self._cache: dict[str, list[dict]] = {}

    def _list(self, key: str, query: str, variables: dict | None = None) -> list[dict]:
        cache_key = key + json.dumps(variables or {}, sort_keys=True)
        if cache_key not in self._cache:
            data = self.run(query, variables)
            node = next(iter(data.values())) if data else None
            self._cache[cache_key] = (node or {}).get("nodes", []) if isinstance(node, dict) else []
        return self._cache[cache_key]

    @staticmethod
    def _pick(kind: str, value: str, rows: list[dict], fields: tuple[str, ...], show: Callable[[dict], str]) -> str:
        if any(r["id"] == value for r in rows):
            return value
        wanted = value.strip().lower()
        hits = [r for r in rows if any(str(r.get(f) or "").lower() == wanted for f in fields)]
        if len(hits) == 1:
            return hits[0]["id"]
        options = ", ".join(show(r) for r in rows[:25]) or "(none visible)"
        if hits:
            raise ToolError(f"'{value}' matches several {kind}s: {', '.join(show(r) for r in hits)}. Use an id.")
        raise ToolError(f"No {kind} matches '{value}'. Available {kind}s: {options}.")

    def team(self, value: str) -> str:
        rows = self._list("teams", "{ teams(first: 250) { nodes { id key name } } }")
        return self._pick("team", value, rows, ("key", "name"), lambda r: f"{r['key']} ({r['name']})")

    def user(self, value: str) -> str:
        if value.strip().lower() in ("me", "myself", "self", "viewer"):
            return self.viewer_id
        rows = self._list("users", "{ organizationMembers(first: 250) { nodes { id name displayName email } } }")
        return self._pick("workspace member", value, rows, ("email", "name", "displayName"),
                          lambda r: f"{r.get('name')} <{r.get('email')}>")

    def state(self, value: str, team_id: str | None) -> str:
        if not team_id:
            return value
        rows = self._list("states", "query($t: ID) { workflowStates(filter: {team: {id: {eq: $t}}}) { nodes { id name type } } }",
                          {"t": team_id})
        return self._pick("workflow state", value, rows, ("name",), lambda r: f"{r['name']} ({r['type']})")

    def label(self, value: str) -> str:
        rows = self._list("labels", "{ issueLabels(first: 250) { nodes { id name } } }")
        return self._pick("label", value, rows, ("name",), lambda r: r["name"])

    def project(self, value: str) -> str:
        rows = self._list("projects", "{ projects(first: 250) { nodes { id name } } }")
        return self._pick("project", value, rows, ("name",), lambda r: r["name"])

    def issue_team(self, ident: str) -> str | None:
        data = self.run("query($i: String!) { issue(id: $i) { team { id } } }", {"i": ident})
        return ((data.get("issue") or {}).get("team") or {}).get("id")

    def apply(self, op: str, flat: dict) -> dict:
        out = dict(flat)
        entity = _entity_of(op)
        for key in ("teamId",):
            if isinstance(out.get(key), str):
                out[key] = self.team(out[key])
        if isinstance(out.get("teamIds"), list):
            out["teamIds"] = [self.team(v) for v in out["teamIds"]]
        for key in ("assigneeId", "leadId", "ownerId", "userId"):
            if isinstance(out.get(key), str):
                out[key] = self.user(out[key])
        if isinstance(out.get("projectId"), str):
            out["projectId"] = self.project(out["projectId"])
        if isinstance(out.get("projectIds"), list):
            out["projectIds"] = [self.project(v) for v in out["projectIds"]]
        if isinstance(out.get("labelIds"), list):
            out["labelIds"] = [self.label(v) for v in out["labelIds"]]
        if isinstance(out.get("id"), str):
            if entity == "team" or op in ("teamCycleCadence",):
                out["id"] = self.team(out["id"])
            elif entity == "project" and op != "projectUpdateCreate":
                out["id"] = self.project(out["id"])
            elif entity == "issueLabel":
                out["id"] = self.label(out["id"])
        if isinstance(out.get("stateId"), str):
            team_id = out.get("teamId") if op == "issueCreate" else (
                self.issue_team(out["id"]) if op == "issueUpdate" and isinstance(out.get("id"), str) else None)
            out["stateId"] = self.state(out["stateId"], team_id)
        return out


_REF_HINTS = {
    "teamId": "List teams with hoja_teams.",
    "stateId": "See the team's states with hoja_team.",
    "assigneeId": "List people with hoja_organizationMembers.",
    "labelIds": "List labels with hoja_issueLabels; labels must belong to the issue's team or the workspace.",
    "projectId": "List projects with hoja_projects.",
    "parentId": "The parent must be an issue in the same team.",
    "cycleId": "List cycles with hoja_cycles.",
}


def _error_text(op: str, errors: list[GraphQLError]) -> str:
    messages = []
    for err in errors:
        original = err.original_error
        if original is not None and not isinstance(original, (GraphQLError, ValueError)):
            log.error("MCP tool %s failed", op, exc_info=original)
            messages.append("internal error")
            continue
        message = err.message.replace("$v_", "$").replace("'v_", "'")
        field = (err.extensions or {}).get("field")
        if field and message == "Invalid reference":
            message = f"Invalid reference in '{field}': it does not exist or is not visible to you."
            if field in _REF_HINTS:
                message += " " + _REF_HINTS[field]
        elif field and field not in message:
            message += f" (field: {field})"
        messages.append(message)
    text = f"hoja_{op} failed: " + "; ".join(dict.fromkeys(messages))
    if any((err.extensions or {}).get("code") in ("FORBIDDEN", "AUTHENTICATION_ERROR") for err in errors):
        text += " " + DENY_HINT
    return text


def build_tools(schema: GraphQLSchema, principal: Callable[[bool], dict]) -> list[Tool]:
    """principal(write) -> GraphQL context for the authenticated caller; raises ToolError."""
    tools = []
    specs = [(op, False, d) for op, d in READ_TOOLS.items()] + [(op, True, d) for op, d in WRITE_TOOLS.items()]
    for op, write, description in specs:
        spec = _Spec(schema, op, write, description)
        tools.append(_make_tool(schema, spec, principal))
    return tools


def _make_tool(schema: GraphQLSchema, spec: _Spec, principal: Callable[[bool], dict]) -> Tool:
    async def run(args: dict) -> str:
        context = principal(spec.write)

        def internal(query: str, variables: dict | None) -> dict:
            result = execute(schema, parse(query), variable_values=variables or {}, context_value=context)
            return result.data or {}

        flat = spec.assemble(args)
        flat = _Resolver(internal, context["user_id"]).apply(spec.op, flat)
        result = execute(schema, spec.document, variable_values=spec.variables(flat), context_value=context)
        if result.errors:
            raise ToolError(_error_text(spec.op, result.errors))
        value = (result.data or {}).get(spec.op)
        target = flat.get("id") or flat.get("issueId") or flat.get("teamId")
        if value is None and spec.field.args:
            subject = f" '{args.get('id', target)}'" if (args.get("id") or target) else ""
            raise ToolError(f"hoja_{spec.op}: nothing found for{subject}. It may not exist or may not be visible to you.")
        if value is False or (isinstance(value, dict) and value.get("success") is False):
            subject = f" for '{args.get('id', target)}'" if (args.get("id") or target) else ""
            raise ToolError(
                f"hoja_{spec.op} did not apply{subject}: the target was not found or you are not allowed to change it. "
                "Nothing was modified."
            )
        return json.dumps(result.data, ensure_ascii=False, default=str)

    run.__name__ = "hoja_" + spec.op
    destructive = spec.op in DESTRUCTIVE
    annotations = ToolAnnotations(
        title=spec.op,
        read_only_hint=not spec.write,
        destructive_hint=destructive if spec.write else None,
        idempotent_hint=True if not spec.write or spec.op.endswith("Update") or spec.op.startswith(IDEMPOTENT_PREFIXES) else None,
        open_world_hint=False,
    )
    return Tool(
        fn=run,
        name="hoja_" + spec.op,
        description=spec.description + ("" if not spec.write else " Requires the hoja:write scope."),
        parameters=spec.parameters,
        fn_metadata=FuncMetadata(arg_model=_RawArgs),
        is_async=True,
        annotations=annotations,
    )


INSTRUCTIONS = (
    "Hoja is a Linear-compatible work tracker. Issues are referenced as TEAMKEY-N (e.g. ENG-12) and every "
    "issue argument accepts that identifier. Start with hoja_viewer and hoja_teams to learn who you are and "
    "which team keys exist; hoja_team(id: KEY) shows a team's workflow states and members. Reference fields "
    "accept friendly values: teamId takes a team key; assigneeId/leadId/userId take 'me', an email or an exact "
    "member name; stateId takes a state name like 'In Progress'; labelIds and projectId take names. Results "
    "contain the affected entity, so read them to confirm what changed. Errors name the argument that was "
    "wrong; fix it instead of guessing other parameters. Omit optional arguments you do not need: empty "
    "strings/lists and 0 for priority/estimate on updates are ignored (use priority \"none\" to clear it). "
    "If a person, team or state does not exist, tell the "
    "user rather than substituting another. Write tools require the hoja:write scope; confirm with the user "
    "before deleting anything."
)
