"""GraphQL surface for views. Read visibility and ownership are enforced in Writer/Store.

The SDL lives here rather than in schema.graphql for the same reason extensions_api.py does:
a feature owns its types, its resolvers, and its errors in one module that build_schema()
composes, so adding a view capability touches one file.
"""
from __future__ import annotations

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import views as viewdocs

SDL = gql(
    """
    type View {
      id: ID!
      name: String!
      description: String
      icon: String
      color: String
      slugId: String!
      kind: String!
      scope: String!
      visibility: String!
      shared: Boolean!
      team: Team
      project: Project
      initiative: Initiative
      owner: User
      creator: User
      updatedBy: User
      filters: JSON!
      display: JSON!
      favorite: Boolean!
      issues(first: Int): IssueConnection!
      projects(first: Int): ProjectConnection!
      initiatives(first: Int): InitiativeConnection!
      archivedAt: DateTime
      createdAt: DateTime!
      updatedAt: DateTime!
    }
    type ViewPayload { success: Boolean! view: View }
    type ViewFavoritePayload { success: Boolean! favorite: Boolean! view: View! }
    input ViewCreateInput {
      name: String!
      description: String
      icon: String
      color: String
      kind: String
      scope: String
      teamId: String
      projectId: String
      initiativeId: String
      visibility: String
      filters: JSON
      display: JSON
    }
    input ViewUpdateInput {
      kind: String
      name: String
      description: String
      icon: String
      color: String
      scope: String
      teamId: String
      projectId: String
      initiativeId: String
      visibility: String
      filters: JSON
      display: JSON
      archived: Boolean
    }
    extend type Query {
      views(kind: String, scope: String, includeArchived: Boolean): [View!]!
      view(id: String!): View!
    }
    extend type Mutation {
      viewCreate(input: ViewCreateInput!): ViewPayload!
      viewUpdate(id: String!, input: ViewUpdateInput!): ViewPayload!
      viewDelete(id: String!): DeletePayload!
      viewFavoriteToggle(id: String!): ViewFavoritePayload!
    }
    """
)

query = QueryType()
mutation = MutationType()
view_type = ObjectType("View")

_DEFAULT_PAGE = 50
_MAX_PAGE = 100


def _ctx(info):
    ctx = info.context
    return ctx["store"], ctx["writer"], ctx["org_id"], ctx["user_id"]


def _bad(exc: Exception) -> GraphQLError:
    if isinstance(exc, viewdocs.ViewError):
        return GraphQLError(str(exc), extensions={"code": "BAD_USER_INPUT", "field": exc.field})
    if isinstance(exc, GraphQLError):
        return exc
    return GraphQLError(str(exc), extensions={"code": "INTERNAL_SERVER_ERROR"})


def _not_found() -> GraphQLError:
    return GraphQLError("View not found.", extensions={"code": "NOT_FOUND"})


def _page(first) -> int:
    if first is None:
        return _DEFAULT_PAGE
    return min(max(int(first), 1), _MAX_PAGE)


def _connection(nodes: list[dict]) -> dict:
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": False, "hasPreviousPage": False,
        "startCursor": nodes[0]["id"] if nodes else None,
        "endCursor": nodes[-1]["id"] if nodes else None,
    }}


@query.field("views")
def r_views(_, info, kind=None, scope=None, includeArchived=False):  # noqa: N803
    store, _w, org, uid = _ctx(info)
    if kind is not None and kind not in viewdocs.KINDS:
        raise _bad(viewdocs.ViewError(f"kind must be one of {list(viewdocs.KINDS)}.", "kind"))
    return store.views(org, uid, kind=kind, scope=scope, include_archived=bool(includeArchived))


@query.field("view")
def r_view(_, info, id):
    store, _w, org, uid = _ctx(info)
    row = store.view_by_id(org, uid, id)
    if not row:
        raise _not_found()
    return row


@mutation.field("viewCreate")
def m_view_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    try:
        view_id = writer.view_create(org, uid, input or {})
    except Exception as exc:  # ViewError, InvalidReferenceError and GraphQLError share one wire shape
        raise _bad(exc) from exc
    return {"success": True, "view": store.view_by_id(org, uid, view_id)}


@mutation.field("viewUpdate")
def m_view_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    try:
        changed = writer.view_update(org, uid, id, input or {})
    except Exception as exc:
        raise _bad(exc) from exc
    if not changed:
        raise _not_found()
    return {"success": True, "view": store.view_by_id(org, uid, id)}


@mutation.field("viewDelete")
def m_view_delete(_, info, id):
    _store, writer, org, uid = _ctx(info)
    if not writer.view_delete(org, uid, id):
        raise _not_found()
    return {"success": True}


@mutation.field("viewFavoriteToggle")
def m_view_favorite_toggle(_, info, id):
    store, writer, org, uid = _ctx(info)
    favorite = writer.view_favorite_toggle(org, uid, id)
    return {"success": True, "favorite": favorite, "view": store.view_by_id(org, uid, id)}


# ------------------------------------------------------------------- fields

def _user(store, org, user_id):
    return store.get_user(org, user_id) if user_id else None


@view_type.field("owner")
def v_owner(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _user(store, org, obj.get("_owner_id"))


@view_type.field("creator")
def v_creator(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _user(store, org, obj.get("_creator_id"))


@view_type.field("updatedBy")
def v_updated_by(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _user(store, org, obj.get("_updated_by_id"))


@view_type.field("team")
def v_team(obj, info):
    store, _w, org, uid = _ctx(info)
    team_id = obj.get("_team_id")
    return store.team_by_id_or_key(org, team_id, viewer_id=uid) if team_id else None


@view_type.field("project")
def v_project(obj, info):
    store, _w, org, uid = _ctx(info)
    project_id = obj.get("_project_id")
    return store.project_by_id(org, project_id, viewer_id=uid) if project_id else None


@view_type.field("initiative")
def v_initiative(obj, info):
    store, _w, org, _uid = _ctx(info)
    initiative_id = obj.get("_initiative_id")
    return store.initiative_by_id(org, initiative_id) if initiative_id else None


@view_type.field("issues")
def v_issues(obj, info, first=None):
    store, _w, org, uid = _ctx(info)
    if obj["kind"] != "issue":
        return _connection([])
    limit = _page(first)
    order_by, reverse = viewdocs.display_order(obj.get("display") or {})
    rows = store.issues(org, uid, viewdocs.compile_filter("issue", obj.get("filters") or {}),
                        order_by=order_by, limit=limit + 1)
    nodes = rows[:limit]
    if reverse:
        nodes.reverse()
    result = _connection(nodes)
    result["pageInfo"]["hasNextPage"] = len(rows) > limit
    return result


@view_type.field("projects")
def v_projects(obj, info, first=None):
    store, _w, org, uid = _ctx(info)
    if obj["kind"] != "project":
        return _connection([])
    limit = _page(first)
    rows = store.projects(org, viewdocs.compile_filter("project", obj.get("filters") or {}),
                          limit=limit + 1, viewer_id=uid)
    nodes = rows[:limit]
    if (obj.get("display") or {}).get("reverse"):
        nodes.reverse()
    result = _connection(nodes)
    result["pageInfo"]["hasNextPage"] = len(rows) > limit
    return result


@view_type.field("initiatives")
def v_initiatives(obj, info, first=None):
    store, _w, org, _uid = _ctx(info)
    if obj["kind"] != "initiative":
        return _connection([])
    limit = _page(first)
    filters = obj.get("filters") or {}
    statuses = viewdocs.initiative_statuses(filters) if filters else None
    rows = store.initiatives(org, statuses=statuses, limit=limit + 1)
    nodes = rows[:limit]
    if (obj.get("display") or {}).get("reverse"):
        nodes.reverse()
    result = _connection(nodes)
    result["pageInfo"]["hasNextPage"] = len(rows) > limit
    return result
