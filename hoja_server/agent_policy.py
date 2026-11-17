"""What one agent member may touch (HOJA-602-A-14, WP12).

Finding F10 was that ``abac.py`` was a well-tested rule engine with no callers, so
an agent inherited the whole surface of the human it was delegated by: every MCP
tool, every team, every repo, and no ceiling on what it cost. This module is the
policy object that replaces that inheritance, and the one place a decision is
made about it.

**Deny by default, and absence is denial.** There is one ``agent_policy`` row per
agent member. No row, no grant: an agent with no policy can call no tool and be
given no lease. Every list column is an allowlist, and an empty allowlist is not
a wildcard - it is "nothing". The two values that are not lists (the limits)
default to the most restrictive thing that still lets work happen: one concurrent
run, and a queue rather than a stop when a ceiling is reached.

**One authority, three enforcement points.** ``decide_tool`` is called by the MCP
dispatcher, ``check_lease`` by ``runner_api`` when a lease is granted, and
``lease_payload`` by the same grant to build what the runner receives. All three
read the same ``PolicyView`` built from the same row, so a rule can only drift if
the row drifts. The rule engine (``abac.py``) decides *grants*; plain arithmetic
decides *limits*, because "is 240 run-minutes more than 120" is not a rule and
pretending otherwise would make the limits unauditable.

**The default branch is not grantable.** A repo grant names branches, and
``main``/``master`` (and the other names a default branch actually uses) are
refused twice: at write time, so the row can never hold one, and at decision
time, so a row edited by hand still cannot push to one. A wildcard is refused for
the same reason - it would contain the default branch without naming it.

**Secrets are named here and stored elsewhere.** ``agent_policy.secrets`` holds
names. ``agent_secret`` holds the values, sealed per organization by
``secrets_box``. A value reaches the runner only in the lease payload, only for
that lease, never in the prompt, and it is watched by value while it is live so a
log line, an audit detail or a transcript entry that quotes one is scrubbed.

**Approval is a refusal, not a prompt.** A tool that needs approval is refused to
the agent with a message naming what would approve it. There is no in-band prompt
and therefore no timeout and no way for a run to deadlock waiting on a human: the
outcome is always a refusal the agent can report.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server import abac, audit, logging_setup, secrets_box, usage_ledger
from hoja_server.db import (
    agent_lease,
    agent_member,
    agent_policy,
    agent_secret,
    agent_session,
    issue,
    new_id,
    now_iso,
)

LOG = logging.getLogger("hoja.agent_policy")

query = QueryType()
mutation = MutationType()
agent_policy_type = ObjectType("AgentPolicy")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

QUEUE = "queue"
STOP = "stop"
BUDGET_ACTIONS = (QUEUE, STOP)

#: The harness profiles the server knows, by the name each one calls itself
#: (``runner/hoja_runnerd/profiles/*.toml`` ``name =``). This is the server's copy of
#: a customer-side fact, and it is a copy on purpose: the profiles ship with the
#: runner, not with the app image, so the control plane cannot import them. A test
#: (``tests/agent/test_harness_selection.py``) reads the TOMLs in this repository and
#: fails when the two lists drift, which is the same arrangement
#: ``test_runner_conformance`` uses for the wire constants.
#:
#: Only canonical names are accepted. The runner resolves aliases itself
#: (``claude`` -> ``claude-code``), but a *lease* names its harness and the server
#: matches that name verbatim against the list each runner declared at enrollment -
#: and the daemon declares ``ProfileSet.allowlist()``, which holds canonical names.
#: Storing an alias would therefore produce a lease no normally-enrolled runner can
#: take, so an alias is refused with the canonical name in the message instead.
KNOWN_HARNESSES = ("claude-code", "codex", "gemini", "generic", "opencode", "pi")

#: What a delegation runs on when nobody chose (HOJA-602-A-22). It is the value
#: ``agent_schedule.enqueue_admitted`` hard-coded before this revision, so an
#: existing workspace's delegations queue for exactly the harness they did
#: yesterday and keep running on the runners that already exist.
FALLBACK_HARNESS = "pi"

#: Aliases, for the refusal message only - never stored. Kept in step with the
#: ``aliases =`` line of each profile by the same test that checks KNOWN_HARNESSES.
HARNESS_ALIASES = {
    "claude": "claude-code",
    "openai-codex": "codex",
    "gemini-cli": "gemini",
    "open-code": "opencode",
    "cli": "generic",
    "shell": "generic",
}

#: Names a repository's default branch actually goes by. Never grantable. A
#: repository whose default branch is something else is protected by the ``repos``
#: grant having to name branches explicitly and by the wildcard refusal below -
#: there is no grant shape that silently covers "whatever is default".
DEFAULT_BRANCH_NAMES = frozenset({"main", "master", "head", "default", "trunk", "develop", "release"})

#: Grant entries that would cover a default branch without naming it.
WILDCARD = "*"

#: Operations that always need a human's approval, whatever the policy says. The
#: policy's ``approval_tools`` column can add to this floor; it cannot remove from
#: it. Kept in step with ``mcp_tools.DESTRUCTIVE`` by a test rather than an import,
#: because the MCP tool registry is an optional extra and this module is imported
#: by the server itself.
APPROVAL_FLOOR = frozenset({
    "issueDelete", "taskDelete", "commentDelete", "issueLabelDelete", "taskLabelDelete",
    "teamMemberRemove", "teamMemberAdd", "issueArchive", "taskArchive",
    "projectArchive", "initiativeArchive", "teamArchive",
})

#: The MCP tool registry, registered by ``mcp_tools`` at import. Empty means the MCP
#: extra is not installed, so no tool can be dispatched at all and a name cannot be
#: wrong - only the type is checked.
KNOWN_TOOLS: dict[str, frozenset[str]] = {}


def register_tools(read: Iterable[str], write: Iterable[str]) -> None:
    """Record the real tool registry so a policy cannot name a tool that does not exist."""
    KNOWN_TOOLS["read"] = frozenset(read)
    KNOWN_TOOLS["write"] = frozenset(write)


class PolicyError(ValueError):
    """A policy document that is not acceptable, naming the field."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name
        self.message = message


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        # A hand-edited row is not a reason to open the gate: unreadable means empty.
        LOG.warning("agent policy holds an unreadable JSON list; treating it as empty")
        return []
    return parsed if isinstance(parsed, list) else []


def _dump(values: Iterable[Any] | None) -> str:
    return json.dumps(list(values or []), sort_keys=True)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Harness profiles (HOJA-602-A-22)
# ---------------------------------------------------------------------------


class HarnessError(PolicyError):
    """A harness an agent may not run on, naming the field and why."""


def canonical_harness(value: Any, field_name: str = "harness") -> str | None:
    """Normalise one harness name, or refuse it. None/empty means "nobody chose".

    Refusing is the whole point: a lease names its harness and is only granted to a
    runner that advertised that exact name, so a typo here is not a small mistake -
    it is a delegation that sits queued forever with nothing to explain it.
    """
    name = str(value or "").strip().lower()
    if not name:
        return None
    if name in KNOWN_HARNESSES:
        return name
    alias = HARNESS_ALIASES.get(name)
    if alias:
        raise HarnessError(
            field_name,
            f"{name} is an alias; use the profile's own name, {alias}, so the lease "
            f"names a harness a runner declares at enrollment.",
        )
    raise HarnessError(
        field_name,
        f"{name} is not a harness profile this server knows ({', '.join(KNOWN_HARNESSES)}).",
    )


def _stored_harness(value: Any) -> str | None:
    """Read-time normalisation: an unknown stored name reads as "not set", not as an
    error. The write path (:func:`canonical_harness`) is what refuses it, so this
    branch is only reachable for a row edited outside the API."""
    try:
        return canonical_harness(value)
    except PolicyError:
        LOG.warning("agent policy holds an unknown harness %r; treating it as not set", value)
        return None


@dataclass(frozen=True)
class HarnessChoice:
    """The harness a run will queue for, and which rung of the chain named it.

    ``source`` is carried so the answer can be explained: ``delegation`` when the
    caller named it, ``policy`` when the agent's policy did, ``allowlist`` when only
    the allowlist could choose, and ``default`` when nothing chose and
    :data:`FALLBACK_HARNESS` applies.
    """

    harness: str
    source: str


def resolve_harness(view: PolicyView | None, *,
                    requested: str | None = None) -> HarnessChoice:
    """Which harness this run executes under (HOJA-602-A-22).

    Four answers, in this order, and each one is checked against the policy's
    allowlist before it is accepted:

    1. ``requested`` - the delegation named it. An unknown name, or one outside the
       allowlist, is **refused**: a caller that asks for something the agent may not
       run has made a mistake, and quietly running something else would be a
       different run than the one it asked for.
    2. ``view.default_harness`` - what the agent's policy says.
    3. ``pi`` - the value every delegation used before this revision, so an existing
       workspace keeps working.
    4. the allowlist's own first entry, sorted - the only rung that can answer when
       the policy allows a subset that excludes ``pi`` and names no default (an
       agent that may *only* run on codex is exactly the case A-22 exists for).
       Deterministic, and the same rule HOJA-602-A-20 used to name a runner's
       enrolled default: first entry of the allowlist, sorted, so flag order cannot
       change the answer.

    Rungs 2-4 are policy-derived, so they never raise: a policy that cannot name a
    harness at all still queues for :data:`FALLBACK_HARNESS`, which the runner side
    reports as ``harness_not_allowed`` rather than the delegation failing silently.
    """
    allowlist = view.harness_allowlist if view is not None else frozenset()

    def permitted(name: str | None) -> bool:
        return bool(name) and (not allowlist or name in allowlist)

    wanted = canonical_harness(requested) if requested else None
    if wanted:
        if not permitted(wanted):
            raise HarnessError(
                "harness",
                f"{wanted} is outside this agent's harness allowlist "
                f"({', '.join(sorted(allowlist))}).",
            )
        return HarnessChoice(wanted, "delegation")
    if view is not None and permitted(view.default_harness):
        return HarnessChoice(str(view.default_harness), "policy")
    if permitted(FALLBACK_HARNESS):
        return HarnessChoice(FALLBACK_HARNESS, "default")
    if allowlist:
        return HarnessChoice(sorted(allowlist)[0], "allowlist")
    return HarnessChoice(FALLBACK_HARNESS, "default")


# ---------------------------------------------------------------------------
# The policy, as a value
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoGrant:
    repo: str
    branches: tuple[str, ...]

    def as_document(self) -> dict[str, Any]:
        return {"repo": self.repo, "branches": list(self.branches)}


@dataclass(frozen=True)
class ConnectGrant:
    host: str
    port: int


@dataclass(frozen=True)
class PolicyView:
    """One ``agent_policy`` row, parsed. Frozen: a decision cannot edit the policy."""

    agent_member_id: str
    version: int
    read_tools: frozenset[str] = frozenset()
    write_tools: frozenset[str] = frozenset()
    approval_tools: frozenset[str] = frozenset()
    teams: frozenset[str] = frozenset()
    projects: frozenset[str] = frozenset()
    tasks_delegated_only: bool = True
    repos: tuple[RepoGrant, ...] = ()
    connectors: frozenset[str] = frozenset()
    secrets: tuple[str, ...] = ()
    egress: tuple[str, ...] = ()
    connect: tuple[ConnectGrant, ...] = ()
    max_concurrency: int = 1
    run_minute_limit: int | None = None
    monthly_credit_budget: int | None = None
    budget_action: str = QUEUE
    delegators: frozenset[str] = frozenset()
    approvers: frozenset[str] = frozenset()
    #: The harness this agent's runs execute under; None means "nobody chose", which
    #: resolves to :data:`FALLBACK_HARNESS` (HOJA-602-A-22).
    default_harness: str | None = None
    #: The harnesses this agent may run on. Empty means "no allowlist": the list
    #: bounds the choice, it does not make one.
    harness_allowlist: frozenset[str] = frozenset()

    def as_document(self) -> dict[str, Any]:
        """The row as JSON, for an audit detail's before/after. Never a secret value."""
        return {
            "agentMemberId": self.agent_member_id,
            "version": self.version,
            "readTools": sorted(self.read_tools),
            "writeTools": sorted(self.write_tools),
            "approvalTools": sorted(self.approval_tools),
            "teams": sorted(self.teams),
            "projects": sorted(self.projects),
            "tasksDelegatedOnly": self.tasks_delegated_only,
            "repos": [grant.as_document() for grant in self.repos],
            "connectors": sorted(self.connectors),
            "secrets": list(self.secrets),
            "egress": list(self.egress),
            "connect": [{"host": c.host, "port": c.port} for c in self.connect],
            "maxConcurrency": self.max_concurrency,
            "runMinuteLimit": self.run_minute_limit,
            "monthlyCreditBudget": self.monthly_credit_budget,
            "budgetAction": self.budget_action,
            "delegators": sorted(self.delegators),
            "approvers": sorted(self.approvers),
            "defaultHarness": self.default_harness,
            "harnessAllowlist": sorted(self.harness_allowlist),
        }


def view_from_row(row: Mapping[str, Any]) -> PolicyView:
    """Parse a stored row. Anything unreadable reads as grant-nothing."""
    repos: list[RepoGrant] = []
    for entry in _json_list(row.get("repos")):
        if not isinstance(entry, Mapping):
            continue
        branches = tuple(str(b) for b in (entry.get("branches") or []) if str(b))
        repos.append(RepoGrant(repo=str(entry.get("repo") or ""), branches=branches))
    connect: list[ConnectGrant] = []
    for entry in _json_list(row.get("connect")):
        if not isinstance(entry, Mapping):
            continue
        try:
            port = int(entry.get("port"))
        except (TypeError, ValueError):
            continue
        connect.append(ConnectGrant(host=str(entry.get("host") or ""), port=port))
    budget_action = str(row.get("budget_action") or QUEUE)
    return PolicyView(
        agent_member_id=str(row["agent_member_id"]),
        version=int(row.get("version") or 1),
        read_tools=frozenset(str(t) for t in _json_list(row.get("read_tools"))),
        write_tools=frozenset(str(t) for t in _json_list(row.get("write_tools"))),
        approval_tools=frozenset(str(t) for t in _json_list(row.get("approval_tools"))),
        teams=frozenset(str(t) for t in _json_list(row.get("teams"))),
        projects=frozenset(str(t) for t in _json_list(row.get("projects"))),
        tasks_delegated_only=bool(row.get("tasks_delegated_only", True)),
        repos=tuple(repos),
        connectors=frozenset(str(c) for c in _json_list(row.get("connectors"))),
        secrets=tuple(str(s) for s in _json_list(row.get("secrets"))),
        egress=tuple(str(e) for e in _json_list(row.get("egress"))),
        connect=tuple(connect),
        max_concurrency=max(1, int(row.get("max_concurrency") or 1)),
        run_minute_limit=row.get("run_minute_limit"),
        monthly_credit_budget=row.get("monthly_credit_budget"),
        budget_action=budget_action if budget_action in BUDGET_ACTIONS else QUEUE,
        delegators=frozenset(str(d) for d in _json_list(row.get("delegators"))),
        approvers=frozenset(str(a) for a in _json_list(row.get("approvers"))),
        # A hand-edited row is read the way ``_json_list`` reads every other list: an
        # unknown or unreadable name is dropped rather than believed, because the
        # lease it would produce is for a runner that cannot exist. The write path
        # refuses such a name, so the only way here is a row edited outside the API.
        default_harness=_stored_harness(row.get("default_harness")),
        harness_allowlist=frozenset(
            h for h in (_stored_harness(v) for v in _json_list(row.get("harness_allowlist")))
            if h),
    )


# ---------------------------------------------------------------------------
# The rule engine (abac.py) - one evaluator, every grant action
# ---------------------------------------------------------------------------


def _bags(view: PolicyView, **resource: Any) -> tuple[dict, dict, dict]:
    subject = {
        "member_id": view.agent_member_id,
        "read_tools": view.read_tools,
        "write_tools": view.write_tools,
        "approval_tools": view.approval_tools | APPROVAL_FLOOR,
        "teams": view.teams,
        "projects": view.projects,
        "tasks_delegated_only": view.tasks_delegated_only,
        "repos": view.repos,
        "connectors": view.connectors,
        "secrets": view.secrets,
        "egress": view.egress,
        "connect": view.connect,
        "delegators": view.delegators,
        "approvers": view.approvers,
    }
    environment = {"policy_version": view.version}
    return subject, resource, environment


def _branch_granted(view: PolicyView, repo: str, branch: str) -> bool:
    return any(grant.repo == repo and branch in grant.branches for grant in view.repos)


def _connect_granted(view: PolicyView, host: str, port: int) -> bool:
    return any(grant.host == host and grant.port == int(port) for grant in view.connect)


RULES = (
    # Tools: a grant is the only way in. Read and write are separate lists, so an
    # agent that may read a surface cannot change it by accident.
    abac.permit(
        "tool-read-granted", "tool.read",
        lambda c: bool(c.r.tool) and c.r.tool in (c.s.read_tools or ()),
        reason="granted for read",
    ),
    abac.permit(
        "tool-write-granted", "tool.write",
        lambda c: c.r.write is True and bool(c.r.tool) and c.r.tool in (c.s.write_tools or ()),
        reason="granted for write",
    ),
    # Approval. Irreversible operations (the floor) and anything the policy names
    # are refused unless this call carries a human's approval for that tool.
    abac.deny(
        "tool-irreversible-needs-approval", "tool.approval",
        lambda c: bool(c.r.irreversible) and c.r.tool not in (c.r.approvals or ()),
        reason="is irreversible and needs a human's approval",
    ),
    abac.deny(
        "tool-policy-needs-approval", "tool.approval",
        lambda c: c.r.tool in (c.s.approval_tools or ()) and c.r.tool not in (c.r.approvals or ()),
        reason="is marked as needing approval by this agent's policy",
    ),
    abac.permit("tool-approval-satisfied", "tool.approval", lambda c: True,
                reason="no approval requirement applies"),
    # Scope. A delegated task is the grant itself; anything else must fall inside
    # the team/project allowlist, and the member may forbid that entirely.
    abac.deny(
        "task-not-delegated", "task.touch",
        lambda c: c.s.tasks_delegated_only is True and c.r.delegated is not True,
        reason="this agent may only touch tasks delegated to it",
    ),
    abac.permit("task-delegated", "task.touch", lambda c: c.r.delegated is True,
                reason="delegated to this agent"),
    abac.permit(
        "task-in-scope", "task.touch",
        lambda c: (bool(c.r.team) and c.r.team in (c.s.teams or ()))
        or (bool(c.r.project) and c.r.project in (c.s.projects or ())),
        reason="team or project is in this agent's scope",
    ),
    # Repos. The default branch is refused before any grant is consulted, so a
    # hand-edited row cannot push to one.
    abac.deny(
        "repo-default-branch", "repo.push",
        lambda c: bool(c.r.branch) and c.r.branch in DEFAULT_BRANCH_NAMES,
        reason="is a default branch name and can never be granted",
    ),
    abac.deny(
        "repo-wildcard", "repo.push",
        lambda c: c.r.branch in (WILDCARD, "") or c.r.repo in (WILDCARD, ""),
        reason="a wildcard repo or branch is never granted",
    ),
    abac.permit("repo-branch-granted", "repo.push",
                lambda c: c.r.repo_granted is True,
                reason="branch is granted for this repo"),
    # Secrets, connectors, network, Connect.
    abac.permit("secret-granted", "secret.inject",
                lambda c: bool(c.r.secret) and c.r.secret in (c.s.secrets or ()),
                reason="secret is named by this policy"),
    abac.permit("connector-granted", "connector.use",
                lambda c: bool(c.r.connector) and c.r.connector in (c.s.connectors or ()),
                reason="connector is granted"),
    abac.permit("egress-granted", "network.egress",
                lambda c: c.r.egress_matched is True,
                reason="host is on the egress allowlist"),
    abac.permit("connect-granted", "connect.expose",
                lambda c: c.r.connect_granted is True,
                reason="host and port are granted for Hoja Connect"),
    # People.
    abac.permit("delegate-granted", "delegate.to",
                lambda c: bool(c.r.actor) and c.r.actor in (c.s.delegators or ()),
                reason="is a delegator of this agent"),
    abac.permit("approve-granted", "approve.for",
                lambda c: bool(c.r.actor) and c.r.actor in (c.s.approvers or ()),
                reason="approves this agent's work"),
)

ABAC = abac.Policy(RULES)


def _decide(view: PolicyView, action: str, **resource: Any) -> abac.Decision:
    subject, resource, environment = _bags(view, **resource)
    return ABAC.decide(action, subject, resource, environment)


def decide_tool(view: PolicyView | None, tool: str, *, write: bool, irreversible: bool,
                approvals: Iterable[str] = ()) -> abac.Decision:
    """May this agent call ``tool``? A refusal names the missing grant.

    Evaluated in two steps so a refusal can say which one failed: the grant first,
    then the approval requirement. Both read the same ``PolicyView``.
    """
    if view is None:
        return abac.Decision(
            False,
            f"the agent has no policy, so no tool is granted; the missing grant is "
            f"{'write' if write else 'read'}Tools + {tool}",
            "no-policy",
        )
    approved = frozenset(approvals)
    granted = _decide(view, "tool.write" if write else "tool.read",
                      tool=tool, write=write, irreversible=irreversible, approvals=approved)
    if not granted.allowed:
        kind = "write" if write else "read"
        return abac.Decision(
            False,
            f"agent policy does not grant {tool} for {kind}; "
            f"the missing grant is {kind}Tools + {tool}",
            granted.rule,
        )
    approval = _decide(view, "tool.approval", tool=tool, irreversible=irreversible,
                       approvals=approved)
    if not approval.allowed:
        return abac.Decision(False, f"{tool} {approval.reason}", approval.rule)
    return abac.Decision(True, granted.reason, granted.rule)


def decide_repo(view: PolicyView | None, repo: str, branch: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no repo is granted",
                             "no-policy")
    return _decide(view, "repo.push", repo=repo, branch=branch,
                   repo_granted=_branch_granted(view, repo, branch))


def decide_task(view: PolicyView | None, *, delegated: bool, team: str | None = None,
                project: str | None = None) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no task is in scope",
                             "no-policy")
    return _decide(view, "task.touch", delegated=delegated, team=team, project=project)


def decide_secret(view: PolicyView | None, name: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no secret is granted",
                             "no-policy")
    return _decide(view, "secret.inject", secret=name)


def decide_connector(view: PolicyView | None, connector_id: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no connector is granted",
                             "no-policy")
    return _decide(view, "connector.use", connector=connector_id)


def decide_egress(view: PolicyView | None, host: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no egress is granted",
                             "no-policy")
    return _decide(view, "network.egress", host=host, egress_matched=host_in_allowlist(view, host))


def decide_connect(view: PolicyView | None, host: str, port: int) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so no Connect endpoint is granted",
                             "no-policy")
    return _decide(view, "connect.expose", host=host, port=port,
                   connect_granted=_connect_granted(view, host, port))


def decide_delegate(view: PolicyView | None, actor_id: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so nobody may delegate to it",
                             "no-policy")
    return _decide(view, "delegate.to", actor=actor_id)


def decide_approve(view: PolicyView | None, actor_id: str) -> abac.Decision:
    if view is None:
        return abac.Decision(False, "the agent has no policy, so nobody may approve for it",
                             "no-policy")
    return _decide(view, "approve.for", actor=actor_id)


def host_in_allowlist(view: PolicyView, host: str) -> bool:
    """Exact host, suffix domain (``.corp.example``) or CIDR membership."""
    import ipaddress

    target = (host or "").strip().lower()
    if not target:
        return False
    for entry in view.egress:
        allowed = entry.strip().lower()
        if not allowed:
            continue
        if allowed == target:
            return True
        if allowed.startswith(".") and target.endswith(allowed):
            return True
        try:
            network = ipaddress.ip_network(allowed, strict=False)
        except ValueError:
            continue
        try:
            if ipaddress.ip_address(target) in network:
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Reading the policy
# ---------------------------------------------------------------------------


def row_for_member(conn, org_id: str, member_id: str) -> dict | None:
    row = conn.execute(select(agent_policy).where(and_(
        agent_policy.c.organization_id == org_id,
        agent_policy.c.agent_member_id == member_id))).mappings().first()
    return dict(row) if row else None


def view_for_member(conn, org_id: str, member_id: str) -> PolicyView | None:
    row = row_for_member(conn, org_id, member_id)
    return view_from_row(row) if row else None


def member_row_for_user(conn, org_id: str, user_id: str) -> dict | None:
    row = conn.execute(select(agent_member).where(and_(
        agent_member.c.organization_id == org_id,
        agent_member.c.user_id == user_id))).mappings().first()
    return dict(row) if row else None


def view_for_user(conn, org_id: str, user_id: str) -> tuple[dict | None, PolicyView | None]:
    """(member row, policy view) for an agent principal; (None, None) for a human."""
    member = member_row_for_user(conn, org_id, user_id)
    if member is None:
        return None, None
    return member, view_for_member(conn, org_id, member["id"])


# ---------------------------------------------------------------------------
# Named secrets
# ---------------------------------------------------------------------------


def secret_rows(conn, org_id: str, names: Sequence[str] | None = None) -> list[dict]:
    stmt = select(agent_secret).where(agent_secret.c.organization_id == org_id)
    if names is not None:
        if not names:
            return []
        stmt = stmt.where(agent_secret.c.name.in_(list(names)))
    return [dict(row) for row in conn.execute(stmt).mappings().all()]


def secret_values(conn, org_id: str, view: PolicyView | None) -> dict[str, str]:
    """Open the values this policy grants, by name.

    A name that does not resolve is skipped rather than raised: a policy may name a
    secret an operator later deleted, and the honest outcome is a lease without it,
    which the runner sees as a missing variable - not a server error that blocks
    the run. The refusal that matters (``decide_secret``) happened at the policy
    layer.
    """
    if view is None or not view.secrets:
        return {}
    values: dict[str, str] = {}
    for row in secret_rows(conn, org_id, view.secrets):
        try:
            values[row["name"]] = secrets_box.decrypt_secret(row["secret_sealed"], org_id)
        except secrets_box.SecretBoxError:
            LOG.warning("agent secret %s could not be opened in this workspace; skipped",
                        row["name"])
    return values


def set_secret(conn, org_id: str, name: str, value: str, *, actor_id: str | None,
               at: str | None = None) -> str:
    """Create or replace a named secret; return its id. The value is sealed here."""
    stamp = at or now_iso()
    sealed = secrets_box.encrypt_secret(value, org_id)
    existing = conn.execute(select(agent_secret).where(and_(
        agent_secret.c.organization_id == org_id,
        agent_secret.c.name == name))).mappings().first()
    if existing:
        conn.execute(agent_secret.update().where(agent_secret.c.id == existing["id"]).values(
            secret_sealed=sealed, updated_at=stamp, updated_by=actor_id))
        secret_id = str(existing["id"])
    else:
        secret_id = new_id()
        conn.execute(agent_secret.insert().values(
            id=secret_id, organization_id=org_id, name=name, secret_sealed=sealed,
            created_at=stamp, updated_at=stamp, updated_by=actor_id))
    logging_setup.watch_secret(value)
    return secret_id


def delete_secret(conn, org_id: str, name: str) -> bool:
    existing = conn.execute(select(agent_secret.c.id).where(and_(
        agent_secret.c.organization_id == org_id,
        agent_secret.c.name == name))).scalar()
    if existing is None:
        return False
    conn.execute(agent_secret.delete().where(agent_secret.c.id == existing))
    return True


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(text: str | None, values: Iterable[str]) -> str | None:
    if text is None:
        return None
    for value in sorted((v for v in values if v), key=len, reverse=True):
        if value in text:
            text = text.replace(value, logging_setup.REDACTED)
    return text


def redact_payload(payload: Any, values: Iterable[str]) -> Any:
    """Redact a JSON-shaped payload without changing its shape."""
    watched = [v for v in values if v]
    if not watched:
        return payload
    if isinstance(payload, str):
        return redact(payload, watched)
    if isinstance(payload, Mapping):
        return {k: redact_payload(v, watched) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact_payload(v, watched) for v in payload]
    return payload


def values_for_session(conn, org_id: str, session_id: str) -> list[str]:
    """The secret values a session's agent was granted, for scrubbing its transcript."""
    member_id = conn.execute(select(agent_member.c.id).join(
        agent_session, agent_session.c.agent_user_id == agent_member.c.user_id).where(and_(
            agent_session.c.organization_id == org_id,
            agent_session.c.id == session_id,
            agent_member.c.organization_id == org_id))).scalar()
    if member_id is None:
        return []
    view = view_for_member(conn, org_id, str(member_id))
    if view is None:
        return []
    return list(secret_values(conn, org_id, view).values())


def scrub_activity(conn, org_id: str, session_id: str, body: str | None,
                   payload: Any) -> tuple[str | None, Any]:
    """Transcript redaction, applied at the single writer (``append_activity``)."""
    values = values_for_session(conn, org_id, session_id)
    if not values:
        return body, payload
    return redact(body, values), redact_payload(payload, values)


def values_for_lease(conn, org_id: str, lease: Mapping[str, Any]) -> list[str]:
    agent_user_id = agent_user_for_task(conn, org_id, str(lease.get("task_id") or ""))
    if not agent_user_id:
        return []
    _, view = view_for_user(conn, org_id, agent_user_id)
    if view is None:
        return []
    return list(secret_values(conn, org_id, view).values())


def scrub_lease_text(conn, org_id: str, lease: Mapping[str, Any], text: str) -> str:
    """Event-stream redaction, applied where a runner's payload is stored."""
    values = values_for_lease(conn, org_id, lease)
    return redact(text, values) or ""


# ---------------------------------------------------------------------------
# Lease-time enforcement (T053)
# ---------------------------------------------------------------------------

CONCURRENCY = "concurrency"
RUN_MINUTES = "run_minutes"
BUDGET = "budget"
NO_POLICY = "no_policy"
OUT_OF_SCOPE = "scope"

# States whose lease is over: its minutes are spent but it holds no slot.
CLOSED_LEASE_STATES = ("finished", "released", "canceled", "lost")


@dataclass(frozen=True)
class LeaseVerdict:
    allowed: bool
    action: str = "grant"
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    agent_member_id: str | None = None
    policy_version: int | None = None

    def refusal_code(self) -> str:
        return f"policy_{self.reason}" if self.reason else "policy_refused"


def agent_user_for_task(conn, org_id: str, task_id: str) -> str | None:
    """The agent a task is delegated to, or None when no agent owns it."""
    if not task_id:
        return None
    return conn.execute(select(issue.c.delegate_id).where(and_(
        issue.c.organization_id == org_id,
        issue.c.id == task_id))).scalar()


def _task_scope(conn, org_id: str, task_id: str) -> dict[str, str | None]:
    row = conn.execute(select(issue.c.team_id, issue.c.project_id).where(and_(
        issue.c.organization_id == org_id,
        issue.c.id == task_id))).mappings().first()
    return dict(row) if row else {"team_id": None, "project_id": None}


def _month_start(stamp: datetime) -> datetime:
    return stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _agent_leases(conn, org_id: str, agent_user_id: str) -> list[dict]:
    rows = conn.execute(select(
        agent_lease.c.state, agent_lease.c.granted_at, agent_lease.c.finished_at,
    ).join(issue, issue.c.id == agent_lease.c.task_id).where(and_(
        agent_lease.c.organization_id == org_id,
        issue.c.delegate_id == agent_user_id,
    ))).mappings().all()
    return [dict(r) for r in rows]


def run_minutes_this_month(conn, org_id: str, agent_user_id: str,
                           now: datetime | None = None) -> float:
    """Minutes already run this calendar month by this agent's leases.

    This is the *lease* clock, which is what refuses a run before it starts: it
    measures every attempt Hoja granted, including one a runner abandoned without
    reporting. The ledger (WP14) is the other side of the same question — the
    minutes a run actually billed, floored for managed compute — and the two are
    deliberately not the same number: a ceiling on what may be spent cannot wait for
    a report that may never arrive. A lease with no grant time is not counted:
    inventing one would overstate spend and stop work that never happened.
    """
    now = now or datetime.now(timezone.utc)
    start = _month_start(now)
    total = 0.0
    for lease in _agent_leases(conn, org_id, agent_user_id):
        granted = _parse_iso(lease.get("granted_at"))
        if granted is None or granted < start:
            continue
        ended = _parse_iso(lease.get("finished_at")) or now
        total += max(0.0, (ended - granted).total_seconds() / 60.0)
    return round(total, 3)


def active_leases(conn, org_id: str, agent_user_id: str) -> int:
    return sum(1 for lease in _agent_leases(conn, org_id, agent_user_id)
               if lease["state"] == "active")


def check_lease(conn, org_id: str, lease: Mapping[str, Any], *,
                now: datetime | None = None) -> LeaseVerdict:
    """May this specific lease be granted? The authority for the lease call site.

    A lease with no delegated agent is not this module's business: a runner
    executing an undeligated task keeps exactly the behaviour it had before WP12,
    which is what keeps "humans unchanged" true here as well.
    """
    task_id = str(lease.get("task_id") or "")
    agent_user_id = agent_user_for_task(conn, org_id, task_id)
    if not agent_user_id:
        return LeaseVerdict(True, detail={"agent": None})
    member, view = view_for_user(conn, org_id, agent_user_id)
    if member is None:
        # A delegate that is not an agent member cannot be policy-checked, and an
        # unchecked agent is exactly finding F10. Refuse.
        return LeaseVerdict(False, action=STOP, reason=NO_POLICY,
                            detail={"agent": agent_user_id,
                                    "why": "delegate is not an agent member"})
    if view is None:
        return LeaseVerdict(False, action=STOP, reason=NO_POLICY,
                            detail={"agent": agent_user_id, "member": member["id"]})
    scope = _task_scope(conn, org_id, task_id)
    # An empty team AND project allowlist means "no scope granted": a delegated
    # task still has to land somewhere the policy names.
    if not view.teams and not view.projects:
        return LeaseVerdict(False, action=view.budget_action, reason=OUT_OF_SCOPE,
                            detail={"agent": agent_user_id, "member": member["id"],
                                    "why": "policy grants no team or project"},
                            agent_member_id=member["id"], policy_version=view.version)
    scope_decision = decide_task(view, delegated=True, team=scope.get("team_id"),
                                 project=scope.get("project_id"))
    if not scope_decision.allowed:
        return LeaseVerdict(False, action=view.budget_action, reason=OUT_OF_SCOPE,
                            detail={"agent": agent_user_id, "member": member["id"],
                                    "why": scope_decision.reason},
                            agent_member_id=member["id"], policy_version=view.version)
    limit = max(1, int(view.max_concurrency or member.get("capacity") or 1))
    running = active_leases(conn, org_id, agent_user_id)
    if running >= limit:
        return LeaseVerdict(
            False, action=view.budget_action, reason=CONCURRENCY,
            detail={"agent": agent_user_id, "member": member["id"],
                    "active": running, "max_concurrency": limit},
            agent_member_id=member["id"], policy_version=view.version)
    if view.run_minute_limit is not None:
        spent = run_minutes_this_month(conn, org_id, agent_user_id, now=now)
        if view.run_minute_limit <= spent:
            return LeaseVerdict(
                False, action=view.budget_action, reason=RUN_MINUTES,
                detail={"agent": agent_user_id, "member": member["id"],
                        "run_minutes": spent, "run_minute_limit": view.run_minute_limit},
                agent_member_id=member["id"], policy_version=view.version)
    budget = view.monthly_credit_budget
    if budget is None:
        budget = member.get("monthly_budget_credits")
    if budget is not None:
        # WP14 meters it: the spend is the sum of this month's ledger rows for this
        # agent, so the ceiling is compared against work that actually happened rather
        # than against a configuration value. A zero budget is therefore exhausted
        # before the first run (0 credits spent >= 0 credited), and NULL still means
        # "the workspace stated no ceiling", exactly as the schedule check reads it.
        spent = usage_ledger.budget_status(conn, org_id, agent_user_id)
        if int(budget) <= spent["used"]:
            return LeaseVerdict(
                False, action=view.budget_action, reason=BUDGET,
                detail={"agent": agent_user_id, "member": member["id"],
                        "monthly_credit_budget": int(budget),
                        "credits_used": spent["used"], "month": spent["month"]},
                agent_member_id=member["id"], policy_version=view.version)
    return LeaseVerdict(True, detail={"agent": agent_user_id, "member": member["id"]},
                        agent_member_id=member["id"], policy_version=view.version)


def lease_payload(conn, org_id: str, view: PolicyView) -> dict[str, Any]:
    """What the runner is given beyond the prompt (T053).

    Secrets by value, egress and Connect by endpoint, and the tool lists so the
    runner can explain a refusal locally. Nothing here is written to the prompt: the
    prompt is the task text, and a secret in it would be a secret in the transcript.
    """
    values = secret_values(conn, org_id, view)
    logging_setup.watch_secrets(values.values())
    return {
        "version": view.version,
        "tools": {"read": sorted(view.read_tools), "write": sorted(view.write_tools),
                  "approval": sorted(view.approval_tools | APPROVAL_FLOOR)},
        "secrets": values,
        "egress": list(view.egress),
        "connect": [{"host": c.host, "port": c.port} for c in view.connect],
        "max_concurrency": view.max_concurrency,
        "run_minute_limit": view.run_minute_limit,
        "budget_action": view.budget_action,
    }


def record_refusal(engine, *, organization_id: str, actor_kind: str, actor_id: str,
                   reason: str, detail: Mapping[str, Any], target_id: str,
                   target_kind: str = "agent_lease") -> None:
    """Audit a refusal. Never fails the caller: the refusal already happened."""
    try:
        audit.record(engine, organization_id=organization_id, action="agent.lease.refuse",
                     actor_kind=actor_kind, actor_id=actor_id, target_kind=target_kind,
                     target_id=target_id, detail={**dict(detail), "reason": reason})
    except Exception:
        LOG.warning("could not audit a policy refusal for %s %s", target_kind, target_id,
                    exc_info=True)


# ---------------------------------------------------------------------------
# The MCP dispatcher call site (T052)
# ---------------------------------------------------------------------------


def dispatch_check(engine, context: Mapping[str, Any], tool: str, *, write: bool,
                   irreversible: bool) -> abac.Decision | None:
    """Check one MCP tool call for an agent principal.

    Returns ``None`` when the caller is not an agent: humans keep exactly the
    surface they had. For an agent it returns the decision, and a refusal happens
    before any resolver runs - the check cannot be talked past by a tool argument.
    """
    from hoja_server import tenant_context

    member_id = context.get("agent_member_id") if isinstance(context, Mapping) else None
    if not member_id:
        return None
    org_id = str(context.get("org_id") or "")
    with tenant_context.scoped(org_id), engine.connect() as conn:
        view = view_for_member(conn, org_id, str(member_id))
        decision = decide_tool(view, tool, write=write, irreversible=irreversible)
        version = view.version if view is not None else None
    if decision.allowed:
        return decision
    try:
        audit.record(
            engine, organization_id=org_id, action="agent.tool.call",
            actor_kind="agent", actor_id=str(context.get("user_id") or member_id),
            target_kind="agent_member", target_id=str(member_id),
            detail={"tool": tool, "write": write, "allowed": False,
                    "reason": decision.reason, "rule": decision.rule,
                    "policy_version": version},
        )
    except Exception:
        LOG.warning("could not audit a refused tool call (%s)", tool, exc_info=True)
    return decision


def refusal_message(tool: str, decision: abac.Decision) -> str:
    """The text an agent sees. It names the grant, and it says not to retry."""
    return (
        f"hoja_{tool} was refused by this agent's policy: {decision.reason}. "
        "Nothing was read or changed. This is a policy boundary, not an error to "
        "retry: ask a workspace admin to grant it on the agent's policy, or ask the "
        "person who delegated the work to do it themselves."
    )


# ---------------------------------------------------------------------------
# Writing the policy: validation and storage
# ---------------------------------------------------------------------------


def _as_json(value: Any, field_name: str) -> Any:
    """Accept a JSON-shaped field as a document or as its JSON text.

    ``repos`` and ``connect`` are JSON scalars, and a client that builds its query
    as a string can only send a string literal. Parsing both here keeps the
    validation below the only thing that has to understand the shape.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            raise PolicyError(field_name, f"{field_name} must be a JSON list.") from None
    return value


def _clean_name_list(field_name: str, values: Any, known: frozenset[str] | None) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PolicyError(field_name, f"{field_name} must be a list of names.")
    cleaned: list[str] = []
    for value in values:
        name = str(value).strip()
        if not name:
            raise PolicyError(field_name, f"{field_name} must not contain an empty name.")
        if known is not None and name not in known:
            raise PolicyError(field_name, f"{name} is not a registered MCP tool.")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def clean_repos(values: Any) -> list[dict[str, Any]]:
    """Validate repo grants; the default branch and wildcards are refused here too."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PolicyError("repos", "repos must be a list of {repo, branches}.")
    grants: list[dict[str, Any]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            raise PolicyError("repos", "each entry must be {repo, branches}.")
        repo = str(entry.get("repo") or "").strip()
        if not repo or repo == WILDCARD:
            raise PolicyError("repos", "a repo must be named explicitly; '*' is not grantable.")
        branches = entry.get("branches") or []
        if not isinstance(branches, (list, tuple)) or not branches:
            raise PolicyError("repos", f"{repo} must name the branches that are granted.")
        clean: list[str] = []
        for branch in branches:
            name = str(branch).strip()
            if not name:
                raise PolicyError("repos", "a branch must not be empty.")
            if name == WILDCARD:
                raise PolicyError(
                    "repos",
                    f"{repo}: '*' is not grantable, because it would include the default branch.",
                )
            if name in DEFAULT_BRANCH_NAMES:
                raise PolicyError(
                    "repos",
                    f"{repo}: {name} is a default branch name and can never be granted.",
                )
            if name not in clean:
                clean.append(name)
        grants.append({"repo": repo, "branches": clean})
    return grants


def clean_egress(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PolicyError("egress", "egress must be a list of hosts, addresses or CIDRs.")
    cleaned: list[str] = []
    for value in values:
        entry = str(value).strip()
        if not entry:
            continue
        if entry in (WILDCARD, "0.0.0.0/0", "::/0"):
            raise PolicyError("egress", f"{entry} is the whole internet, not an allowlist.")
        if entry not in cleaned:
            cleaned.append(entry)
    return cleaned


def clean_connect(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PolicyError("connect", "connect must be a list of {host, port}.")
    cleaned: list[dict[str, Any]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            raise PolicyError("connect", "each entry must be {host, port}.")
        host = str(entry.get("host") or "").strip()
        if not host or host == WILDCARD:
            raise PolicyError("connect", "a Connect host must be named explicitly.")
        try:
            port = int(entry.get("port"))
        except (TypeError, ValueError):
            raise PolicyError("connect", "a Connect port must be an integer.") from None
        if not 1 <= port <= 65535:
            raise PolicyError("connect", "a Connect port must be between 1 and 65535.")
        if {"host": host, "port": port} not in cleaned:
            cleaned.append({"host": host, "port": port})
    return cleaned


def _limit(value: Any, field_name: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PolicyError(field_name, f"{field_name} must be an integer.") from None
    if number < minimum:
        raise PolicyError(field_name, f"{field_name} must be at least {minimum}.")
    return number


def clean_input(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one policy document into column values.

    Only the fields present in ``document`` are returned, so an update that sends
    one field leaves the rest of the row untouched - the difference between "clear
    the grants" and "do not mention them" is the presence of the key, not an empty
    value.
    """
    values: dict[str, Any] = {}
    read = KNOWN_TOOLS.get("read")
    write = KNOWN_TOOLS.get("write")
    if "readTools" in document:
        values["read_tools"] = _dump(_clean_name_list("readTools", document["readTools"], read))
    if "writeTools" in document:
        values["write_tools"] = _dump(_clean_name_list("writeTools", document["writeTools"], write))
    if "approvalTools" in document:
        values["approval_tools"] = _dump(
            _clean_name_list("approvalTools", document["approvalTools"], None))
    for field_name, column in (("teams", "teams"), ("projects", "projects"),
                               ("delegators", "delegators"), ("approvers", "approvers"),
                               ("connectors", "connectors"), ("secrets", "secrets")):
        if field_name in document:
            values[column] = _dump(
                _clean_name_list(field_name, document[field_name], None))
    if "egress" in document:
        values["egress"] = _dump(clean_egress(document["egress"]))
    if "repos" in document:
        values["repos"] = _dump(clean_repos(_as_json(document["repos"], "repos")))
    if "connect" in document:
        values["connect"] = _dump(clean_connect(_as_json(document["connect"], "connect")))
    if "tasksDelegatedOnly" in document:
        values["tasks_delegated_only"] = bool(document["tasksDelegatedOnly"])
    if "maxConcurrency" in document:
        concurrency = _limit(document["maxConcurrency"], "maxConcurrency", minimum=1)
        values["max_concurrency"] = concurrency if concurrency is not None else 1
    if "runMinuteLimit" in document:
        values["run_minute_limit"] = _limit(document["runMinuteLimit"], "runMinuteLimit")
    if "monthlyCreditBudget" in document:
        values["monthly_credit_budget"] = _limit(document["monthlyCreditBudget"],
                                                 "monthlyCreditBudget")
    if "budgetAction" in document:
        action = str(document["budgetAction"] or "").strip().lower()
        if action not in BUDGET_ACTIONS:
            raise PolicyError("budgetAction", "budgetAction must be 'queue' or 'stop'.")
        values["budget_action"] = action
    # Harness (HOJA-602-A-22). Validated together, because the two fields constrain
    # each other: a default the allowlist forbids is a contradiction the queue would
    # otherwise have to resolve at run time, when nobody is looking.
    if "harnessAllowlist" in document:
        values["harness_allowlist"] = _dump(
            _clean_harness_list(document["harnessAllowlist"]))
    if "defaultHarness" in document:
        values["default_harness"] = canonical_harness(document["defaultHarness"],
                                                      "defaultHarness")
    if "default_harness" in values or "harness_allowlist" in values:
        _check_harness_pair(values)
    return values


def _clean_harness_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PolicyError("harnessAllowlist", "harnessAllowlist must be a list of profile names.")
    cleaned: list[str] = []
    for value in values:
        name = canonical_harness(value, "harnessAllowlist")
        if name is None:
            raise PolicyError("harnessAllowlist",
                              "harnessAllowlist must not contain an empty name.")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def _check_harness_pair(values: Mapping[str, Any]) -> None:
    """Refuse a default the allowlist in the same write forbids.

    Only the pair present in *this* document is checked; a merged check would read
    the stored row, and a partial update (one field) must not be refused for a
    contradiction it did not create. ``save_policy`` holds the same rule for the
    merged row, where the two halves come from different writes.
    """
    default = values.get("default_harness")
    allowlist = values.get("harness_allowlist")
    if default is None or allowlist is None:
        return
    if _json_list(allowlist) and default not in _json_list(allowlist):
        raise PolicyError(
            "defaultHarness",
            f"defaultHarness {default} is not in harnessAllowlist "
            f"({', '.join(sorted(_json_list(allowlist)))}).",
        )


def save_policy(conn, org_id: str, member_id: str, document: Mapping[str, Any],
                *, actor_id: str | None, at: str | None = None) -> tuple[dict, dict]:
    """Apply a validated document; return (before, after) row dicts.

    The version moves by one on every write that changes something, which is what
    lets a lease prove which policy it was granted under.
    """
    stamp = at or now_iso()
    values = clean_input(document)
    current = row_for_member(conn, org_id, member_id)
    before = dict(current) if current else {}
    # The same contradiction can be created across two writes - a default set
    # yesterday, an allowlist that excludes it today - so the merged row is checked
    # here as well, before either half is stored. Reads the stored columns, never a
    # guess, and only when one of the two fields is in this write.
    if "default_harness" in values or "harness_allowlist" in values:
        merged = {
            "default_harness": values.get("default_harness", (current or {}).get("default_harness")),
            "harness_allowlist": values.get("harness_allowlist",
                                            (current or {}).get("harness_allowlist")),
        }
        _check_harness_pair(merged)
    if current is None:
        conn.execute(agent_policy.insert().values(
            id=new_id(), organization_id=org_id, agent_member_id=member_id, version=1,
            created_at=stamp, updated_at=stamp, updated_by=actor_id, **values))
    elif values:
        conn.execute(agent_policy.update().where(agent_policy.c.id == current["id"]).values(
            updated_at=stamp, updated_by=actor_id,
            version=int(current.get("version") or 1) + 1, **values))
    after = row_for_member(conn, org_id, member_id) or {}
    return before, after


# ---------------------------------------------------------------------------
# GraphQL surface (T051/T054) - admin only, every write audited
# ---------------------------------------------------------------------------

SDL = gql(
    """
    type AgentPolicy {
      id: ID!
      agentMemberId: ID!
      "Moves by one on every change; a lease records the version it ran under."
      version: Int!
      "MCP tool names granted for read."
      readTools: [String!]!
      "MCP tool names granted for write."
      writeTools: [String!]!
      approvalTools: [String!]!
      teams: [String!]!
      projects: [String!]!
      tasksDelegatedOnly: Boolean!
      "Grants as [{repo, branches}]. A default branch can never appear here."
      repos: JSON
      connectors: [String!]!
      "Secret NAMES. The values are never readable through this API."
      secrets: [String!]!
      egress: [String!]!
      "Hoja Connect endpoints as [{host, port}]."
      connect: JSON
      maxConcurrency: Int!
      runMinuteLimit: Int
      monthlyCreditBudget: Int
      "queue | stop - what happens when a limit is reached."
      budgetAction: String!
      delegators: [String!]!
      approvers: [String!]!
      "The harness profile this agent's runs queue for (HOJA-602-A-22). Null means nobody chose, so the queue falls back to pi."
      defaultHarness: String
      "Harnesses this agent may run on. Empty means no allowlist: any known profile."
      harnessAllowlist: [String!]!
      updatedAt: DateTime!
      updatedBy: ID
    }

    type AgentPolicyPayload { success: Boolean! agentPolicy: AgentPolicy }

    input AgentPolicyInput {
      "An absent field leaves the stored list alone; an empty list clears it."
      readTools: [String!]
      writeTools: [String!]
      approvalTools: [String!]
      teams: [String!]
      projects: [String!]
      tasksDelegatedOnly: Boolean
      repos: JSON
      connectors: [String!]
      secrets: [String!]
      egress: [String!]
      connect: JSON
      maxConcurrency: Int
      runMinuteLimit: Int
      monthlyCreditBudget: Int
      budgetAction: String
      delegators: [String!]
      approvers: [String!]
      "A profile name this server knows (see agentHarnesses). Unknown names and aliases are refused."
      defaultHarness: String
      harnessAllowlist: [String!]
    }

    extend type Query {
      "The policy for one agent member; null when none is configured (deny by default)."
      agentPolicy(agentMemberId: String!): AgentPolicy
      "Secret names configured in this workspace. Never a value."
      agentSecretNames: [String!]!
      "The harness profiles this server knows, canonical names, sorted. What defaultHarness may hold."
      agentHarnesses: [String!]!
    }

    extend type Mutation {
      "Create or update the policy for one agent member."
      agentPolicyUpdate(agentMemberId: String!, input: AgentPolicyInput!): AgentPolicyPayload!
      "Store a named secret. The value is sealed and never returned."
      agentSecretSet(name: String!, value: String!): Boolean!
      agentSecretDelete(name: String!): Boolean!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage an agent's policy.",
            extensions={"code": "FORBIDDEN"},
        )


def _member_or_404(conn, org, member_id):
    member = conn.execute(select(agent_member).where(and_(
        agent_member.c.organization_id == org,
        agent_member.c.id == member_id))).mappings().first()
    if member is None:
        raise GraphQLError("Agent member not found.", extensions={"code": "NOT_FOUND"})
    return dict(member)


def serialize(row: Mapping[str, Any]) -> dict[str, Any]:
    view = view_from_row(row)
    return {
        "id": row["id"],
        "agentMemberId": view.agent_member_id,
        "version": view.version,
        "readTools": sorted(view.read_tools),
        "writeTools": sorted(view.write_tools),
        "approvalTools": sorted(view.approval_tools),
        "teams": sorted(view.teams),
        "projects": sorted(view.projects),
        "tasksDelegatedOnly": view.tasks_delegated_only,
        "repos": [g.as_document() for g in view.repos],
        "connectors": sorted(view.connectors),
        "secrets": list(view.secrets),
        "egress": list(view.egress),
        "connect": [{"host": c.host, "port": c.port} for c in view.connect],
        "maxConcurrency": view.max_concurrency,
        "runMinuteLimit": view.run_minute_limit,
        "monthlyCreditBudget": view.monthly_credit_budget,
        "budgetAction": view.budget_action,
        "delegators": sorted(view.delegators),
        "approvers": sorted(view.approvers),
        "defaultHarness": view.default_harness,
        "harnessAllowlist": sorted(view.harness_allowlist),
        "updatedAt": row.get("updated_at"),
        "updatedBy": row.get("updated_by"),
    }


@query.field("agentPolicy")
def r_agent_policy(_, info, agentMemberId):  # noqa: N803 - the GraphQL argument name
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        row = row_for_member(conn, org, agentMemberId)
    return serialize(row) if row else None


@query.field("agentSecretNames")
def r_agent_secret_names(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        return [row["name"] for row in secret_rows(conn, org)]


@query.field("agentHarnesses")
def r_agent_harnesses(_, info):
    """The names ``defaultHarness`` may hold. Static, but admin-gated like its siblings
    so the surface has one rule rather than an exception to remember."""
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    return sorted(KNOWN_HARNESSES)


@mutation.field("agentPolicyUpdate")
def m_agent_policy_update(_, info, agentMemberId, input):  # noqa: N803 - GraphQL argument
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        with store.engine.begin() as conn:
            _member_or_404(conn, org, agentMemberId)
            before, after = save_policy(conn, org, agentMemberId, input, actor_id=uid)
    except PolicyError as bad:
        raise GraphQLError(bad.message,
                           extensions={"code": "BAD_USER_INPUT", "field": bad.field}) from None
    # After the commit, never inside it: an audit row that vanished with a rollback
    # would claim a change that did not happen (WP05's rule).
    audit.record_request(
        store, info, user_id=uid, action="agent.policy.change",
        target_kind="agent_member", target_id=agentMemberId,
        detail={
            "before": view_from_row(before).as_document() if before else None,
            "after": view_from_row(after).as_document(),
        },
    )
    return {"success": True, "agentPolicy": serialize(after)}


@mutation.field("agentSecretSet")
def m_agent_secret_set(_, info, name, value):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    clean = (name or "").strip()
    if not clean:
        raise GraphQLError("name must not be empty.",
                           extensions={"code": "BAD_USER_INPUT", "field": "name"})
    if not value:
        raise GraphQLError("value must not be empty.",
                           extensions={"code": "BAD_USER_INPUT", "field": "value"})
    with store.engine.begin() as conn:
        set_secret(conn, org, clean, value, actor_id=uid)
    # The NAME is audited; the value is not, and never reaches this call.
    audit.record_request(store, info, user_id=uid, action="agent.policy.secret",
                         target_kind="agent_secret", target_id=clean,
                         detail={"name": clean, "operation": "set"})
    return True


@mutation.field("agentSecretDelete")
def m_agent_secret_delete(_, info, name):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    clean = (name or "").strip()
    with store.engine.begin() as conn:
        removed = delete_secret(conn, org, clean)
    if not removed:
        raise GraphQLError("No such secret.", extensions={"code": "NOT_FOUND"})
    audit.record_request(store, info, user_id=uid, action="agent.policy.secret",
                         target_kind="agent_secret", target_id=clean,
                         detail={"name": clean, "operation": "delete"})
    return True
