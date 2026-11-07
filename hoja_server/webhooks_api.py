"""GraphQL surface for outgoing webhooks, using Linear's wire names.

Only workspace admins can read or manage webhooks (Linear has the same rule):
the signing secret is part of the Webhook type.
"""
from __future__ import annotations

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import secrets_box, webhooks

SDL = gql(
    """
    type Webhook {
      id: ID!
      url: String
      label: String
      enabled: Boolean!
      secret: String
      resourceTypes: [String!]!
      allPublicTeams: Boolean!
      team: Team
      teamIds: [String!]
      creator: User
      failures: [WebhookFailureEvent!]!
      createdAt: DateTime!
      updatedAt: DateTime!
      archivedAt: DateTime
    }
    type WebhookFailureEvent {
      id: ID!
      createdAt: DateTime!
      executionId: String!
      httpStatus: Float
      responseOrError: String
      url: String!
    }
    type WebhookConnection { nodes: [Webhook!]! pageInfo: PageInfo! }
    type WebhookPayload { success: Boolean! lastSyncId: Float! webhook: Webhook }
    input WebhookCreateInput {
      id: String
      url: String!
      label: String
      enabled: Boolean
      secret: String
      resourceTypes: [String!]!
      teamId: String
      allPublicTeams: Boolean
    }
    input WebhookUpdateInput {
      url: String
      label: String
      enabled: Boolean
      secret: String
      resourceTypes: [String!]
    }
    extend type Query {
      webhooks(first: Int = 50, after: String): WebhookConnection!
      webhook(id: String!): Webhook!
    }
    extend type Mutation {
      webhookCreate(input: WebhookCreateInput!): WebhookPayload!
      webhookUpdate(id: String!, input: WebhookUpdateInput!): WebhookPayload!
      webhookDelete(id: String!): DeletePayload!
    }
    """
)

query = QueryType()
mutation = MutationType()
webhook_type = ObjectType("Webhook")


def _admin_ctx(info):
    ctx = info.context
    store, org, uid = ctx["store"], ctx["org_id"], ctx["user_id"]
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can manage webhooks.", extensions={"code": "FORBIDDEN"})
    return store, org, uid


def _user_error(exc: Exception) -> GraphQLError:
    if isinstance(exc, webhooks.WebhookError):
        return GraphQLError(str(exc), extensions={"code": "BAD_USER_INPUT", "field": exc.field})
    return GraphQLError(str(exc), extensions={"code": "INTERNAL_SERVER_ERROR"})


@query.field("webhooks")
def q_webhooks(_, info, first=50, after=None):
    store, org, _uid = _admin_ctx(info)
    nodes = webhooks.list_webhooks(store.engine, org)
    return {"nodes": nodes, "pageInfo": {"hasNextPage": False, "hasPreviousPage": False,
                                         "startCursor": None, "endCursor": None}}


@query.field("webhook")
def q_webhook(_, info, id):
    store, org, _uid = _admin_ctx(info)
    found = webhooks.get_webhook(store.engine, org, id)
    if not found:
        raise GraphQLError("Webhook not found.", extensions={"code": "NOT_FOUND"})
    return found


@mutation.field("webhookCreate")
def m_webhook_create(_, info, input):
    store, org, uid = _admin_ctx(info)
    try:
        created = webhooks.create_webhook(store.engine, org, uid, input)
    except (webhooks.WebhookError, secrets_box.SecretBoxError) as exc:
        raise _user_error(exc) from exc
    return {"success": True, "lastSyncId": 0, "webhook": created}


@mutation.field("webhookUpdate")
def m_webhook_update(_, info, id, input):
    store, org, _uid = _admin_ctx(info)
    try:
        updated = webhooks.update_webhook(store.engine, org, id, input)
    except (webhooks.WebhookError, secrets_box.SecretBoxError) as exc:
        raise _user_error(exc) from exc
    if not updated:
        raise GraphQLError("Webhook not found.", extensions={"code": "NOT_FOUND"})
    return {"success": True, "lastSyncId": 0, "webhook": updated}


@mutation.field("webhookDelete")
def m_webhook_delete(_, info, id):
    store, org, _uid = _admin_ctx(info)
    return {"success": webhooks.delete_webhook(store.engine, org, id)}


@webhook_type.field("team")
def w_team(obj, info):
    if not obj.get("_team_id"):
        return None
    store = info.context["store"]
    return store.ser_team(store.team_by_id_or_key(info.context["org_id"], obj["_team_id"]))


@webhook_type.field("creator")
def w_creator(obj, info):
    if not obj.get("_creator_id"):
        return None
    store = info.context["store"]
    return store.ser_user(store.get_user(info.context["org_id"], obj["_creator_id"]), info.context["user_id"])
