"""Workspace permissions and security policy (HOJA-490).

Two settings surfaces that were previously only prose:

* **Permissions** — a member-role grant per capability. The capability set is
  derived from the call sites that already existed, not invented: every action
  here was admin-only in revision 58, and a workspace that never opens the pane
  keeps exactly that behavior, because a missing ``workspace_permission`` row
  reads as the capability's default (False). ``admin`` always holds every
  capability and cannot be locked out of its own workspace.
* **Security** — session idle timeout, allowed sign-in methods and an IP
  allowlist, each enforced in the auth path (``store.resolve_browser_session``,
  ``store.validate_credentials`` and ``app._resolve_auth``), never only stored.

The refusal is the product: a member without the grant gets a GraphQL
``FORBIDDEN`` error, not a hidden button.
"""

from __future__ import annotations

import ipaddress
import json

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from hoja_server import audit
from hoja_server.db import new_id, now_iso, organization, user, workspace_permission, workspace_security_policy

query = QueryType()
mutation = MutationType()
workspace_permission_type = ObjectType("WorkspacePermission")
workspace_security_policy_type = ObjectType("WorkspaceSecurityPolicy")

#: (capability, label, description). The defaults are all False: each of these
#: actions was admin-only before this pane existed, so an untouched workspace
#: behaves identically to revision 58. Order is the order the pane renders.
CAPABILITIES = [
    ("team.create", "Create teams",
     "Create a team in this workspace, including private teams."),
    ("member.invite", "Invite and manage members",
     "Invite people, revoke invitations, and add or remove team members."),
    ("task.delete", "Delete tasks",
     "Delete or archive a task. Editing tasks is not gated by this."),
    ("label.manage", "Manage labels",
     "Create, rename, recolour and delete workspace labels."),
    ("extension.manage", "Install and manage extensions",
     "Install, update and uninstall workspace extensions."),
]
CAPABILITY_IDS = {capability for capability, _label, _description in CAPABILITIES}

SIGNIN_METHODS = {"password", "google"}
#: Upper bound is a week; a longer idle window is indistinguishable from the
#: session's absolute 14-day expiry and would read as "no timeout" to anyone
#: debugging it.
IDLE_TIMEOUT_RANGE = (1, 10080)
MAX_ALLOWLIST = 200

SDL = gql(
    """
    type WorkspacePermission {
      capability: String!
      label: String!
      description: String!
      "Whether a member (not an admin) currently holds this capability."
      memberAllowed: Boolean!
    }

    type WorkspaceSecurityPolicy {
      "Minutes of inactivity after which a browser session is refused; null = no idle timeout."
      idleTimeoutMinutes: Int
      "password | google. Empty means every method the server offers."
      allowedSigninMethods: [String!]!
      "CIDR strings. Empty means no restriction."
      ipAllowlist: [String!]!
      "Whether Hoja Connect may open tunnels for this workspace (WP09). True until an admin says otherwise."
      connectEnabled: Boolean!
      updatedAt: DateTime
    }

    input WorkspaceSecurityPolicyInput {
      idleTimeoutMinutes: Int
      allowedSigninMethods: [String!]
      ipAllowlist: [String!]
      "The workspace-wide Connect kill switch. Omit to leave it as it is."
      connectEnabled: Boolean
    }

    "Workspace-wide switches every member reads, because they decide what the app shows."
    type WorkspaceFeatures {
      "Whether this workspace shows the Agents screens. Off until an admin turns it on."
      agentsEnabled: Boolean!
    }

    extend type Query {
      workspacePermissions: [WorkspacePermission!]!
      workspaceSecurityPolicy: WorkspaceSecurityPolicy!
      workspaceFeatures: WorkspaceFeatures!
    }

    extend type Mutation {
      workspacePermissionUpdate(capability: String!, memberAllowed: Boolean!): WorkspacePermission!
      workspaceSecurityUpdate(input: WorkspaceSecurityPolicyInput!): WorkspaceSecurityPolicy!
      "Admins only; every change is written to the audit log."
      workspaceFeaturesUpdate(agentsEnabled: Boolean!): WorkspaceFeatures!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org: str, uid: str):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage permissions and security.",
            extensions={"code": "FORBIDDEN"},
        )
    return actor


# ------------------------------------------------------------------- grants
def member_grants(engine, org_id: str) -> dict[str, bool]:
    """Effective member grants. A missing row is the capability's default."""
    grants = {capability: False for capability in CAPABILITY_IDS}
    with engine.connect() as conn:
        rows = conn.execute(
            select(workspace_permission.c.capability, workspace_permission.c.member_allowed)
            .where(workspace_permission.c.organization_id == org_id)
        ).all()
    for capability, allowed in rows:
        if capability in grants:
            grants[capability] = bool(allowed)
    return grants


def can_for_engine(engine, org_id: str, uid: str, capability: str) -> bool:
    """``can`` for callers that hold the engine rather than a Store (writer/API)."""
    if capability not in CAPABILITY_IDS:
        raise GraphQLError(
            f"Unknown capability '{capability}'.",
            extensions={"code": "BAD_USER_INPUT", "field": "capability"},
        )
    with engine.connect() as conn:
        actor = conn.execute(select(user.c.id, user.c.admin).where(and_(
            user.c.id == uid, user.c.organization_id == org_id,
            user.c.active.is_(True), user.c.archived_at.is_(None)))).first()
    if not actor:
        return False
    if actor[1]:
        return True
    return member_grants(engine, org_id).get(capability, False)


def can(store, org_id: str, uid: str, capability: str) -> bool:
    """True when the caller holds ``capability``. Admins always do."""
    return can_for_engine(store.engine, org_id, uid, capability)


def require(store, org_id: str, uid: str, capability: str) -> None:
    """Refuse the mutation unless the caller holds ``capability``.

    The message names the capability rather than the role, because a member who
    has been granted four of five capabilities needs to know which one is
    missing; the code stays ``FORBIDDEN`` so clients handle it uniformly.
    """
    if can(store, org_id, uid, capability):
        return
    label = next((lbl for cap, lbl, _d in CAPABILITIES if cap == capability), capability)
    raise GraphQLError(
        f"Your workspace role does not allow this: {label}.",
        extensions={"code": "FORBIDDEN", "capability": capability},
    )


# ------------------------------------------------------------------ security
#: Callables run with the org id after a policy save. The auth path caches the
#: policy per process, and a settings save must be visible on the next request
#: there — a 10-second stale allowlist is a real refusal window.
POLICY_INVALIDATORS: list = []


def _invalidate(org_id: str) -> None:
    for invalidate in list(POLICY_INVALIDATORS):
        try:
            invalidate(org_id)
        except Exception:  # a stale cache entry must not fail the save
            pass


def security_policy(engine, org_id: str) -> dict:
    """The stored policy, or the conservative default when none was saved."""
    with engine.connect() as conn:
        row = conn.execute(
            select(workspace_security_policy).where(
                workspace_security_policy.c.organization_id == org_id)
        ).mappings().first()
    if not row:
        return {
            "idleTimeoutMinutes": None,
            "allowedSigninMethods": [],
            "ipAllowlist": [],
            # Revision 71: "no row" and NULL both mean "never decided", which is enabled. A
            # workspace that has not turned Connect off keeps it on.
            "connectEnabled": True,
            "updatedAt": None,
        }
    return {
        "idleTimeoutMinutes": row["idle_timeout_minutes"],
        "allowedSigninMethods": json.loads(row["allowed_signin_methods"] or "[]"),
        "ipAllowlist": json.loads(row["ip_allowlist"] or "[]"),
        "connectEnabled": row.get("connect_enabled") is not False,
        "updatedAt": row["updated_at"],
    }


def _parse_allowlist(entries) -> list:
    """Validated networks. An entry that is not an IP or CIDR is refused here,
    at write time, so the auth path never has to guess what an entry means."""
    networks = []
    for raw in entries or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            raise GraphQLError(
                f"'{text}' is not an IP address or CIDR range.",
                extensions={"code": "BAD_USER_INPUT", "field": "ipAllowlist"},
            ) from None
    if len(networks) > MAX_ALLOWLIST:
        raise GraphQLError(
            f"An IP allowlist is limited to {MAX_ALLOWLIST} entries.",
            extensions={"code": "BAD_USER_INPUT", "field": "ipAllowlist"},
        )
    return networks


def ip_allowed(policy: dict, client_ip: str | None) -> bool:
    """Whether ``client_ip`` may use the workspace at all.

    An empty allowlist allows everything. A non-empty allowlist that cannot
    resolve the caller's address refuses — fail closed, because the whole point
    of the control is that an address we cannot check is not on the list.
    """
    entries = policy.get("ipAllowlist") or []
    if not entries:
        return True
    try:
        address = ipaddress.ip_address(str(client_ip or ""))
    except ValueError:
        return False
    return any(address in network for network in _parse_allowlist(entries))


def signin_method_allowed(policy: dict, method: str) -> bool:
    allowed = policy.get("allowedSigninMethods") or []
    return not allowed or method in allowed


def session_idle_ok(policy: dict, last_seen_at: str | None, created_at: str | None) -> bool:
    """Whether a session is inside the idle window.

    ``last_seen_at`` is NULL on a row written before revision 59; it reads as
    ``created_at`` so a pre-existing session keeps the lifetime it was created
    with instead of being refused the moment a policy is saved.
    """
    minutes = policy.get("idleTimeoutMinutes")
    if not minutes:
        return True
    from datetime import datetime, timedelta, timezone

    seen = last_seen_at or created_at
    if not seen:
        return True
    try:
        when = datetime.fromisoformat(seen)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when <= timedelta(minutes=int(minutes))


# ----------------------------------------------------------------- resolvers
@query.field("workspacePermissions")
def r_workspace_permissions(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    grants = member_grants(store.engine, org)
    return [
        {"capability": capability, "label": label, "description": description,
         "memberAllowed": grants[capability]}
        for capability, label, description in CAPABILITIES
    ]


@mutation.field("workspacePermissionUpdate")
def m_workspace_permission_update(_, info, capability, memberAllowed):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    if capability not in CAPABILITY_IDS:
        raise GraphQLError(
            f"Unknown capability '{capability}'.",
            extensions={"code": "BAD_USER_INPUT", "field": "capability"},
        )
    ts = now_iso()
    with store.engine.begin() as conn:
        updated = conn.execute(
            workspace_permission.update()
            .where(and_(workspace_permission.c.organization_id == org,
                        workspace_permission.c.capability == capability))
            .values(member_allowed=bool(memberAllowed), updated_by=uid, updated_at=ts)
        ).rowcount
        if not updated:
            try:
                conn.execute(workspace_permission.insert().values(
                    id=new_id(), organization_id=org, capability=capability,
                    member_allowed=bool(memberAllowed), updated_by=uid, updated_at=ts))
            except IntegrityError:  # a concurrent save won; apply the last write
                conn.execute(
                    workspace_permission.update()
                    .where(and_(workspace_permission.c.organization_id == org,
                                workspace_permission.c.capability == capability))
                    .values(member_allowed=bool(memberAllowed), updated_by=uid, updated_at=ts)
                )
    label = next(lbl for cap, lbl, _d in CAPABILITIES if cap == capability)
    description = next(d for cap, _l, d in CAPABILITIES if cap == capability)
    return {"capability": capability, "label": label, "description": description,
            "memberAllowed": bool(memberAllowed)}


@query.field("workspaceSecurityPolicy")
def r_workspace_security_policy(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    return security_policy(store.engine, org)


@mutation.field("workspaceSecurityUpdate")
def m_workspace_security_update(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    idle = input.get("idleTimeoutMinutes")
    if idle is not None:
        low, high = IDLE_TIMEOUT_RANGE
        if not isinstance(idle, int) or not low <= idle <= high:
            raise GraphQLError(
                f"Idle timeout must be {low}–{high} minutes, or null for none.",
                extensions={"code": "BAD_USER_INPUT", "field": "idleTimeoutMinutes"},
            )
    methods = input.get("allowedSigninMethods")
    if methods is not None:
        unknown = [m for m in methods if m not in SIGNIN_METHODS]
        if unknown:
            raise GraphQLError(
                f"Unknown sign-in method(s): {', '.join(unknown)}.",
                extensions={"code": "BAD_USER_INPUT", "field": "allowedSigninMethods"},
            )
    allowlist = input.get("ipAllowlist")
    normalized = None
    if allowlist is not None:
        normalized = [str(n) for n in _parse_allowlist(allowlist)]
    ts = now_iso()
    values = {
        "idle_timeout_minutes": idle,
        "allowed_signin_methods": json.dumps(methods or []),
        "ip_allowlist": json.dumps(normalized or []),
        "updated_by": uid,
        "updated_at": ts,
    }
    # WP09's kill switch is written only when the caller sends it, unlike the three fields above
    # (whose omission has always meant "clear"). A client that predates this field must not turn
    # Connect back on for a workspace whose admin turned it off.
    if input.get("connectEnabled") is not None:
        values["connect_enabled"] = bool(input["connectEnabled"])
    with store.engine.begin() as conn:
        updated = conn.execute(
            workspace_security_policy.update()
            .where(workspace_security_policy.c.organization_id == org)
            .values(**values)
        ).rowcount
        if not updated:
            try:
                conn.execute(workspace_security_policy.insert().values(
                    organization_id=org, **values))
            except IntegrityError:
                conn.execute(
                    workspace_security_policy.update()
                    .where(workspace_security_policy.c.organization_id == org)
                    .values(**values)
                )
    _invalidate(org)
    return security_policy(store.engine, org)


# ------------------------------------------------------------------- features
def agents_enabled(engine, org_id: str) -> bool:
    """NULL (every workspace before revision 76) and False both read as off."""
    with engine.connect() as conn:
        value = conn.execute(select(organization.c.agents_enabled).where(
            organization.c.id == org_id)).scalar()
    return value is True


@query.field("workspaceFeatures")
def r_workspace_features(_, info):
    # Any member: the sidebar asks this to decide whether to show Agents at all.
    store, org, _uid = _ctx(info)
    return {"agentsEnabled": agents_enabled(store.engine, org)}


@mutation.field("workspaceFeaturesUpdate")
def m_workspace_features_update(_, info, agentsEnabled):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    before = agents_enabled(store.engine, org)
    after = bool(agentsEnabled)
    with store.engine.begin() as conn:
        conn.execute(organization.update().where(organization.c.id == org).values(
            agents_enabled=after, updated_at=now_iso()))
    if before != after:
        audit.record_request(store, info, user_id=uid, action="workspace.features.change",
                             target_kind="organization", target_id=org,
                             detail={"agentsEnabled": {"before": before, "after": after}})
    return {"agentsEnabled": after}
