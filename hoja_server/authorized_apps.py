"""Connected apps: OAuth grants and browser sessions a person can see and revoke.

An authorized app is one ``oauth_grant`` row: one approval of one client by one
person in one workspace (per device label). Its id is the ``grant_id`` every
token of that approval carries, so revoking the row revokes them all. Members
see and revoke their own; admins see and revoke the workspace's.
"""

from __future__ import annotations

import base64
import json

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server.db import (
    FIRST_PARTY_CLIENT_ID,
    browser_session,
    now_iso,
    oauth_grant,
    token_hash,
    user,
)

query = QueryType()
mutation = MutationType()
authorized_app_type = ObjectType("AuthorizedApp")

SDL = gql(
    """
    "An app a person approved through OAuth: the Hoja CLI, an MCP client, or another app."
    type AuthorizedApp {
      id: ID!
      clientId: String!
      clientName: String
      "True for Hoja's own apps (the Hoja CLI)."
      firstParty: Boolean!
      scopes: [String!]!
      "The device the app named when it was approved, e.g. hoja-cli on laptop."
      deviceLabel: String
      createdAt: DateTime!
      lastUsedAt: DateTime
      lastUsedIp: String
      user: User
    }

    type AuthorizedAppConnection {
      nodes: [AuthorizedApp!]!
      pageInfo: PageInfo!
    }

    "A signed-in browser."
    type BrowserSession {
      id: ID!
      createdAt: DateTime!
      lastSeenAt: DateTime
      expiresAt: DateTime!
      "True for the browser making this request."
      current: Boolean!
    }

    extend type Query {
      "Apps the caller approved in this workspace that still have access."
      myAuthorizedApps: [AuthorizedApp!]!
      "Admins only: every app with access to this workspace."
      workspaceAuthorizedApps(first: Int, after: String): AuthorizedAppConnection!
      "The caller's signed-in browsers in this workspace."
      mySessions: [BrowserSession!]!
    }

    extend type Mutation {
      "Revoke one of the caller's apps: every token of that approval stops working."
      authorizedAppRevoke(id: String!): Boolean!
      "Admins only: revoke any app's access to this workspace."
      workspaceAuthorizedAppRevoke(id: String!): Boolean!
      "Sign out one of the caller's browsers."
      sessionRevoke(id: String!): Boolean!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_browser_or_write(info) -> None:
    # Revoking is a write; a read-only credential may list but not revoke.
    if info.context.get("access") == "read":
        raise GraphQLError("This credential is read-only.", extensions={"code": "FORBIDDEN"})


def _is_admin(store, org: str, uid: str) -> bool:
    actor = store.get_user(org, uid)
    return bool(actor and actor.get("admin"))


def _node(row, people: dict) -> dict:
    return {
        "id": row["id"],
        "clientId": row["client_id"],
        "clientName": row["client_name"] or row["client_id"],
        "firstParty": row["client_id"] == FIRST_PARTY_CLIENT_ID,
        "scopes": json.loads(row["scopes"] or "[]"),
        "deviceLabel": row["device_label"],
        "createdAt": row["created_at"],
        "lastUsedAt": row["last_used_at"],
        "lastUsedIp": row["last_used_ip"],
        "_user_id": row["user_id"],
        "_people": people,
    }


@authorized_app_type.field("user")
def r_app_user(obj, info):
    person = obj["_people"].get(obj["_user_id"])
    if person is None:
        store, org, _uid = _ctx(info)
        person = store.get_user(org, obj["_user_id"])
    return person


def _people(store, org: str, ids) -> dict:
    ids = list({i for i in ids if i})
    return store.users_by_ids(org, ids) if ids else {}


@query.field("myAuthorizedApps")
def r_my_authorized_apps(_, info):
    store, org, uid = _ctx(info)
    with store.engine.connect() as conn:
        rows = conn.execute(select(oauth_grant).where(and_(
            oauth_grant.c.organization_id == org, oauth_grant.c.user_id == uid,
            oauth_grant.c.revoked_at.is_(None))).order_by(oauth_grant.c.created_at.desc())).mappings().all()
    people = _people(store, org, [uid])
    return [_node(r, people) for r in rows]


def _cursor(row) -> str:
    return base64.urlsafe_b64encode(json.dumps([row["created_at"], row["id"]]).encode()).decode()


@query.field("workspaceAuthorizedApps")
def r_workspace_authorized_apps(_, info, first=50, after=None):
    store, org, uid = _ctx(info)
    if not _is_admin(store, org, uid):
        raise GraphQLError("Only workspace admins can list every connected app.",
                           extensions={"code": "FORBIDDEN"})
    if type(first) is not int or not 1 <= first <= 250:
        raise GraphQLError("first must be an integer from 1 to 250.",
                           extensions={"code": "BAD_USER_INPUT", "field": "first"})
    where = [oauth_grant.c.organization_id == org, oauth_grant.c.revoked_at.is_(None)]
    if after:
        try:
            created, ident = json.loads(base64.urlsafe_b64decode(after.encode()))
        except (ValueError, TypeError) as exc:
            raise GraphQLError("Invalid cursor.", extensions={"code": "BAD_USER_INPUT", "field": "after"}) from exc
        where.append((oauth_grant.c.created_at < created)
                     | and_(oauth_grant.c.created_at == created, oauth_grant.c.id < ident))
    with store.engine.connect() as conn:
        rows = conn.execute(select(oauth_grant).where(and_(*where)).order_by(
            oauth_grant.c.created_at.desc(), oauth_grant.c.id.desc()).limit(first + 1)).mappings().all()
    page, more = rows[:first], len(rows) > first
    people = _people(store, org, [r["user_id"] for r in page])
    return {
        "nodes": [_node(r, people) for r in page],
        "pageInfo": {"hasNextPage": more, "hasPreviousPage": bool(after),
                     "startCursor": _cursor(page[0]) if page else None,
                     "endCursor": _cursor(page[-1]) if page else None},
    }


def _revoke(store, org: str, grant_id: str, *, owner: str | None) -> bool:
    from hoja_server.mcp_oauth import revoke_grant

    with store.engine.begin() as conn:
        where = [oauth_grant.c.id == grant_id, oauth_grant.c.organization_id == org,
                 oauth_grant.c.revoked_at.is_(None)]
        if owner is not None:
            where.append(oauth_grant.c.user_id == owner)
        if conn.execute(select(oauth_grant.c.id).where(and_(*where))).first() is None:
            return False
        revoke_grant(conn, grant_id)
    return True


@mutation.field("authorizedAppRevoke")
def m_authorized_app_revoke(_, info, id):
    store, org, uid = _ctx(info)
    _require_browser_or_write(info)
    return _revoke(store, org, id, owner=uid)


@mutation.field("workspaceAuthorizedAppRevoke")
def m_workspace_authorized_app_revoke(_, info, id):
    store, org, uid = _ctx(info)
    _require_browser_or_write(info)
    if not _is_admin(store, org, uid):
        raise GraphQLError("Only workspace admins can revoke another person's app.",
                           extensions={"code": "FORBIDDEN"})
    return _revoke(store, org, id, owner=None)


def _current_session_hash(info) -> str | None:
    request = info.context.get("request")
    raw = request.cookies.get("hoja_session") if request is not None else None
    return token_hash(raw) if raw and info.context.get("browser_session") else None


@query.field("mySessions")
def r_my_sessions(_, info):
    store, org, uid = _ctx(info)
    current = _current_session_hash(info)
    now = now_iso()
    with store.engine.connect() as conn:
        rows = conn.execute(select(browser_session).join(user, and_(
            user.c.id == browser_session.c.user_id, user.c.organization_id == browser_session.c.organization_id,
        )).where(and_(browser_session.c.organization_id == org, browser_session.c.user_id == uid,
                      browser_session.c.revoked_at.is_(None), browser_session.c.expires_at > now))
            .order_by(browser_session.c.created_at.desc())).mappings().all()
    return [{"id": r["id"], "createdAt": r["created_at"], "lastSeenAt": r["last_seen_at"],
             "expiresAt": r["expires_at"], "current": r["token_hash"] == current} for r in rows]


@mutation.field("sessionRevoke")
def m_session_revoke(_, info, id):
    store, org, uid = _ctx(info)
    _require_browser_or_write(info)
    with store.engine.begin() as conn:
        changed = conn.execute(browser_session.update().where(and_(
            browser_session.c.id == id, browser_session.c.organization_id == org,
            browser_session.c.user_id == uid, browser_session.c.revoked_at.is_(None),
        )).values(revoked_at=now_iso())).rowcount
    return changed == 1
