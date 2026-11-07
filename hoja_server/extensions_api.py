"""GraphQL surface for extensions. Managing them is admin-only; running actions is for members."""
from __future__ import annotations

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import extensions, secrets_box

SDL = gql(
    """
    type Extension {
      id: ID!
      key: String!
      name: String!
      version: String!
      url: String!
      access: String!
      enabled: Boolean!
      manifest: JSON!
      botUser: User
      createdAt: DateTime!
      updatedAt: DateTime!
      hooks: [ExtensionHook!]!
      actions: [ExtensionAction!]!
      failures: [ExtensionFailure!]!
    }
    type ExtensionHook { key: String! on: String! failurePolicy: String! position: Int! }
    type ExtensionAction {
      id: ID!
      key: String!
      title: String!
      entity: String!
      extensionId: ID!
      extensionName: String!
    }
    type ExtensionFailure { at: DateTime! kind: String! key: String httpStatus: Float message: String }
    type ExtensionInstallPayload {
      success: Boolean!
      extension: Extension
      apiKey: String
      signingSecret: String
    }
    type ExtensionPayload { success: Boolean! extension: Extension }
    type ExtensionActionRunPayload { success: Boolean! runId: ID! status: String! message: String }
    extend type Query {
      extensions: [Extension!]!
      extension(id: String!): Extension!
      extensionActions(entity: String!): [ExtensionAction!]!
    }
    extend type Mutation {
      extensionInstall(manifest: JSON!): ExtensionInstallPayload!
      extensionUpdate(id: String!, manifest: JSON!): ExtensionPayload!
      extensionUninstall(id: String!): DeletePayload!
      extensionActionRun(actionId: String!, entityId: String!): ExtensionActionRunPayload!
    }
    """
)

query = QueryType()
mutation = MutationType()
extension_type = ObjectType("Extension")


def _admin(info):
    ctx = info.context
    store, org, uid = ctx["store"], ctx["org_id"], ctx["user_id"]
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can manage extensions.", extensions={"code": "FORBIDDEN"})
    return store, org, uid


def _bad(exc: Exception) -> GraphQLError:
    if isinstance(exc, extensions.ExtensionError):
        return GraphQLError(str(exc), extensions={"code": "BAD_USER_INPUT", "field": exc.field})
    return GraphQLError(str(exc), extensions={"code": "INTERNAL_SERVER_ERROR"})


def _not_found():
    return GraphQLError("Extension not found.", extensions={"code": "NOT_FOUND"})


@query.field("extensions")
def q_extensions(_, info):
    store, org, _uid = _admin(info)
    return extensions.list_installed(store.engine, org)


@query.field("extension")
def q_extension(_, info, id):
    store, org, _uid = _admin(info)
    found = extensions.get(store.engine, org, id)
    if not found:
        raise _not_found()
    return found


@query.field("extensionActions")
def q_extension_actions(_, info, entity):
    ctx = info.context
    return extensions.list_actions(ctx["store"].engine, ctx["org_id"], entity)


@mutation.field("extensionInstall")
def m_extension_install(_, info, manifest):
    store, org, uid = _admin(info)
    try:
        result = extensions.install(store.engine, org, uid, manifest)
    except (extensions.ExtensionError, secrets_box.SecretBoxError) as exc:
        raise _bad(exc) from None  # the message is the whole story; a chain only slows error logging
    return {"success": True, **result}


@mutation.field("extensionUpdate")
def m_extension_update(_, info, id, manifest):
    store, org, _uid = _admin(info)
    try:
        updated = extensions.update(store.engine, org, id, manifest)
    except (extensions.ExtensionError, secrets_box.SecretBoxError) as exc:
        raise _bad(exc) from None  # the message is the whole story; a chain only slows error logging
    if not updated:
        raise _not_found()
    return {"success": True, "extension": updated}


@mutation.field("extensionUninstall")
def m_extension_uninstall(_, info, id):
    store, org, _uid = _admin(info)
    return {"success": extensions.uninstall(store.engine, org, id)}


@mutation.field("extensionActionRun")
def m_extension_action_run(_, info, **args):
    ctx = info.context
    try:
        return extensions.run_action(ctx["store"].engine, ctx["store"], ctx["org_id"], ctx["user_id"],
                                     args["actionId"], args["entityId"])
    except extensions.ExtensionError as exc:
        raise _bad(exc) from None  # the message is the whole story; a chain only slows error logging


@extension_type.field("botUser")
def e_bot_user(obj, info):
    store = info.context["store"]
    row = store.get_user(info.context["org_id"], obj["_bot_user_id"]) if obj.get("_bot_user_id") else None
    return store.ser_user(row, info.context["user_id"]) if row else None
