"""Agent members: a workspace principal whose identity is a ``user`` row (HOJA-602-A-3).

Finding F4: an agent had no identity of its own — MCP and durable turns carried the
human's ``user_id`` and nothing said who acted. The fix is a principal, not a new
kind of account: the identity is ``user`` with ``kind='agent'``, so every existing
``actor_id`` / ``assignee_id`` / ``delegate_id`` keeps pointing at one table.

This module owns what an agent needs beyond identity — concurrency capacity, a
monthly credit budget, working hours and timezone, the model it runs and the runner
pool that may execute it — plus the GraphQL surface an admin manages them through.

Agents are not billable seats. Seat counting reads ``organization_membership``, and
an agent never gets one: it is a principal inside the workspace, not a person who
can sign in.
"""
from __future__ import annotations

import json

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import agent_member, new_id, now_iso, user

query = QueryType()
mutation = MutationType()
agent_member_type = ObjectType("AgentMember")

SDL = gql(
    """
    type AgentMember {
      id: ID!
      userId: ID!
      user: User!
      "How many turns or delegations this agent may run concurrently."
      capacity: Int!
      "Credits per calendar month; null means the workspace default, not unlimited."
      monthlyBudgetCredits: Int
      "Opaque working-hours document, honored by the runner (WP06/WP07)."
      workingHours: JSON
      timezone: String
      model: String
      runnerPool: String
      archivedAt: DateTime
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type AgentMemberPayload { success: Boolean! agentMember: AgentMember }

    input AgentMemberCreateInput {
      "The identity's name; the agent's user row is created with it."
      name: String!
      displayName: String
      capacity: Int
      monthlyBudgetCredits: Int
      workingHours: JSON
      timezone: String
      model: String
      runnerPool: String
    }

    input AgentMemberUpdateInput {
      name: String
      displayName: String
      capacity: Int
      monthlyBudgetCredits: Int
      workingHours: JSON
      timezone: String
      model: String
      runnerPool: String
      archived: Boolean
    }

    extend type Query {
      "Every agent member in the workspace, archived ones included."
      agentMembers: [AgentMember!]!
      agentMember(id: String!): AgentMember
    }

    extend type Mutation {
      agentMemberCreate(input: AgentMemberCreateInput!): AgentMemberPayload!
      agentMemberUpdate(id: String!, input: AgentMemberUpdateInput!): AgentMemberPayload!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can manage agent members.",
            extensions={"code": "FORBIDDEN"},
        )


def _bad(field, message):
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def serialize(store, org_id: str, row: dict) -> dict:
    """One ``agent_member`` row in its GraphQL shape."""
    hours = row.get("working_hours")
    try:
        parsed_hours = json.loads(hours) if hours else None
    except (TypeError, ValueError):
        # Never fail a read over a hand-edited document: the runner treats an
        # unreadable policy as "no restriction", which is what it did before.
        parsed_hours = None
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "user": store.ser_user(store.get_user(org_id, row["user_id"]), row["user_id"]),
        "capacity": int(row.get("capacity") or 1),
        "monthlyBudgetCredits": row.get("monthly_budget_credits"),
        "workingHours": parsed_hours,
        "timezone": row.get("timezone"),
        "model": row.get("model"),
        "runnerPool": row.get("runner_pool"),
        "archivedAt": row.get("archived_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _clean(input: dict, *, creating: bool) -> dict:
    """Validate a create/update input into ``agent_member`` column values."""
    values: dict = {}
    if creating or "name" in input:
        name = (input.get("name") or "").strip()
        if not name:
            _bad("name", "name must not be empty.")
        values["_name"] = name
    if "displayName" in input:
        values["_display_name"] = input.get("displayName")
    if "capacity" in input and input.get("capacity") is not None:
        capacity = int(input["capacity"])
        if capacity < 1:
            _bad("capacity", "capacity must be at least 1.")
        values["capacity"] = capacity
    if "monthlyBudgetCredits" in input:
        credits = input.get("monthlyBudgetCredits")
        if credits is not None:
            credits = int(credits)
            if credits < 0:
                _bad("monthlyBudgetCredits", "monthlyBudgetCredits must not be negative.")
        values["monthly_budget_credits"] = credits
    for field, column in (("timezone", "timezone"), ("model", "model"),
                          ("runnerPool", "runner_pool")):
        if field in input:
            values[column] = input.get(field)
    if "workingHours" in input:
        hours = input.get("workingHours")
        values["working_hours"] = json.dumps(hours, sort_keys=True) if hours is not None else None
    if "archived" in input:
        values["archived_at"] = now_iso() if input.get("archived") else None
    return values


def _row(engine, org_id: str, member_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(agent_member).where(and_(
            agent_member.c.organization_id == org_id,
            agent_member.c.id == member_id))).mappings().first()
    return dict(row) if row else None


@query.field("agentMembers")
def r_agent_members(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(
            select(agent_member).where(agent_member.c.organization_id == org)
            .order_by(agent_member.c.created_at)).all()]
    return [serialize(store, org, row) for row in rows]


@query.field("agentMember")
def r_agent_member(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    row = _row(store.engine, org, id)
    return serialize(store, org, row) if row else None


@mutation.field("agentMemberCreate")
def m_create(_, info, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    values = _clean(input, creating=True)
    ts = now_iso()
    member_id, user_id = new_id(), new_id()
    identity = {
        "name": values.pop("_name"),
        "display_name": values.pop("_display_name", None),
    }
    with store.engine.begin() as conn:
        # The identity first: an agent_member row without its user is unreadable
        # (user_id is a foreign key), and both rows commit or roll back together.
        conn.execute(user.insert().values(
            id=user_id, organization_id=org, kind="agent", active=True,
            admin=False, created_at=ts, updated_at=ts, **identity))
        try:
            conn.execute(agent_member.insert().values(
                id=member_id, organization_id=org, user_id=user_id,
                created_at=ts, updated_at=ts, **values))
        except IntegrityError:
            _bad("name", "That agent identity already has a member row.")
        # HOJA-742: a workspace template may set the policy every new agent starts
        # with. Without one the agent is granted nothing, exactly as before.
        from hoja_server import agent_policy
        from hoja_server.workspace_templates import AGENT_POLICY_KEY, get_default
        default_policy = get_default(conn, org, AGENT_POLICY_KEY)
        if default_policy:
            agent_policy.save_policy(conn, org, member_id, default_policy, actor_id=uid, at=ts)
        row = dict(conn.execute(select(agent_member).where(
            agent_member.c.id == member_id)).first()._mapping)
    return {"success": True, "agentMember": serialize(store, org, row)}


@mutation.field("agentMemberUpdate")
def m_update(_, info, id, input):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    current = _row(store.engine, org, id)
    if current is None:
        raise GraphQLError("Agent member not found.", extensions={"code": "NOT_FOUND"})
    values = _clean(input, creating=False)
    # Both identity columns are optional on an update: `name` is only present when the
    # caller sent it, and an absent `displayName` must leave the stored one alone rather
    # than blank it. Reading them with `pop` keyed on presence (not on a default) is what
    # keeps a capacity-only edit from raising KeyError and from wiping the display name.
    identity = {}
    if "_name" in values:
        identity["name"] = values.pop("_name")
    if "_display_name" in values:
        identity["display_name"] = values.pop("_display_name")
    with store.engine.begin() as conn:
        if identity:
            conn.execute(user.update().where(and_(
                user.c.organization_id == org, user.c.id == current["user_id"])).values(
                    updated_at=now_iso(), **identity))
        if values:
            conn.execute(agent_member.update().where(and_(
                agent_member.c.organization_id == org, agent_member.c.id == id)).values(
                    updated_at=now_iso(), **values))
        row = dict(conn.execute(select(agent_member).where(
            agent_member.c.id == id)).first()._mapping)
    return {"success": True, "agentMember": serialize(store, org, row)}


# ------------------------------------------------------------- write-side helpers

def is_delegate_target(conn, org_id: str, delegate_id: str) -> bool:
    """True when ``delegate_id`` names an active agent member of exactly this org.

    The organization predicate is the cross-tenant boundary: an agent member of
    another workspace is not a row this query can return, so delegating across
    tenants fails even for an id the caller knows.
    """
    row = conn.execute(select(agent_member.c.id).join(
        user, user.c.id == agent_member.c.user_id).where(and_(
            agent_member.c.organization_id == org_id,
            agent_member.c.archived_at.is_(None),
            user.c.id == delegate_id,
            user.c.organization_id == org_id,
            user.c.kind == "agent",
            user.c.active.is_(True),
            user.c.archived_at.is_(None),
        ))).first()
    return row is not None


def turn_agent(engine, org_id: str) -> str | None:
    """The agent a turn in ``org_id`` acts as, or None when the choice is ambiguous.

    A workspace with exactly one active agent member has an unambiguous agent, and
    that is what a chat turn acts as (with the human as ``on_behalf_of``). Binding a
    conversation to a *specific* agent is delegation/session work (WP06); until then
    a workspace with several agents records the human rather than guessing.
    """
    with engine.connect() as conn:
        rows = conn.execute(select(agent_member.c.user_id).join(
            user, user.c.id == agent_member.c.user_id).where(and_(
                agent_member.c.organization_id == org_id,
                agent_member.c.archived_at.is_(None),
                user.c.active.is_(True),
                user.c.archived_at.is_(None),
            ))).scalars().all()
    return rows[0] if len(rows) == 1 else None
