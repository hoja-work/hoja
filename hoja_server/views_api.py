"""GraphQL surface for views. Read visibility and ownership are enforced in Writer/Store.

The SDL lives here rather than in schema.graphql for the same reason extensions_api.py does:
a feature owns its types, its resolvers, and its errors in one module that build_schema()
composes, so adding a view capability touches one file.
"""
from __future__ import annotations

import functools

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import view_linear
from hoja_server import views as viewdocs
from hoja_server.db import entity_url

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
      "Shown as a tab on its team's (or project's) task page."
      pinned: Boolean!
      "Order among the pinned tabs; null sorts after every ordered tab, by creation."
      sortOrder: Float
      "Runs the view: saved filters AND scope AND the optional extra filter, ordered and windowed by its display (or the overrides), keyset-paged."
      issues(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): IssueConnection!
      projects(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): ProjectConnection!
      initiatives(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): InitiativeConnection!
      archivedAt: DateTime
      createdAt: DateTime!
      updatedAt: DateTime!
      "Stable link to the view: <workspace origin>/<urlKey>/view/<slugId>."
      url: String!
      "The caller's display preferences for this view: the view's default, their own layer, and the two combined."
      preferences: ViewPreferenceState!
      "The caller's subscription to this view, or null when they do not follow it."
      subscription: ViewSubscription
      subscriberCount: Int!
    }
    "Display preferences in layers: the default everyone starts from, the caller's own overrides, and the result."
    type ViewPreferenceState {
      viewId: String
      viewType: String
      default: JSON!
      "Null when the caller has no overrides."
      user: JSON
      effective: JSON!
      hasUserOverrides: Boolean!
    }
    type ViewSubscription {
      id: ID!
      "Notify when a task starts matching the view."
      onAdded: Boolean!
      "Notify when a matching task is completed or canceled."
      onCompleted: Boolean!
      view: View
      createdAt: DateTime!
      updatedAt: DateTime!
    }
    type ViewSubscriptionPayload { success: Boolean! subscription: ViewSubscription view: View! }
    "An unsaved view run by the same engine as View.issues/projects/initiatives."
    type ViewPreview {
      kind: String!
      scope: String!
      filters: JSON!
      display: JSON!
      issues(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): IssueConnection!
      projects(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): ProjectConnection!
      initiatives(first: Int, after: String, filter: JSON, display: JSON, sort: String, reverse: Boolean): InitiativeConnection!
    }
    extend type IssueConnection {
      "How many rows match across every page. Set by view execution; null elsewhere."
      totalCount: Int
    }
    extend type ProjectConnection { totalCount: Int }
    extend type InitiativeConnection { totalCount: Int }
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
      "Hand the view to another member (owner or admin only)."
      ownerId: String
    }
    "Linear's CustomView, mapped onto the same saved views as View."
    type CustomView {
      id: ID!
      slugId: String!
      name: String!
      description: String
      icon: String
      color: String
      shared: Boolean!
      "Issue, Project or Initiative."
      modelName: String!
      filterData: JSON!
      projectFilterData: JSON
      initiativeFilterData: JSON
      owner: User
      creator: User
      updatedBy: User
      team: Team
      project: Project
      initiative: Initiative
      "The caller's effective display preferences."
      viewPreferencesValues: JSON!
      organizationViewPreferences: ViewPreferences
      userViewPreferences: ViewPreferences
      issues(first: Int, after: String, filter: JSON): IssueConnection!
      projects(first: Int, after: String, filter: JSON): ProjectConnection!
      url: String!
      createdAt: DateTime!
      updatedAt: DateTime!
      archivedAt: DateTime
    }
    "One preference layer, in Linear's shape: type is organization or user."
    type ViewPreferences {
      id: ID!
      type: String!
      viewType: String!
      preferences: JSON!
      createdAt: DateTime
      updatedAt: DateTime
    }
    type CustomViewConnection { nodes: [CustomView!]! pageInfo: PageInfo! }
    type CustomViewPayload { success: Boolean! customView: CustomView! }
    type CustomViewHasSubscribersPayload { hasSubscribers: Boolean! }
    input CustomViewCreateInput {
      name: String!
      description: String
      icon: String
      color: String
      teamId: String
      projectId: String
      initiativeId: String
      ownerId: String
      shared: Boolean
      filterData: JSON
      projectFilterData: JSON
    }
    input CustomViewUpdateInput {
      name: String
      description: String
      icon: String
      color: String
      teamId: String
      projectId: String
      initiativeId: String
      ownerId: String
      shared: Boolean
      filterData: JSON
      projectFilterData: JSON
    }
    extend type Query {
      views(kind: String, scope: String, includeArchived: Boolean): [View!]!
      view(id: String!): View!
      viewPreview(kind: String!, filters: JSON, display: JSON, scope: String, teamId: String,
                  projectId: String, initiativeId: String, first: Int, after: String): ViewPreview!
      "Preferences of a saved view (viewId) or a built-in page (viewType, e.g. my-issues, team:<id>:active, project:<id>)."
      viewPreferences(viewId: String, viewType: String): ViewPreferenceState!
      customView(id: String!): CustomView!
      customViews(first: Int, after: String, includeArchived: Boolean): CustomViewConnection!
      customViewHasSubscribers(id: String!): CustomViewHasSubscribersPayload!
    }
    extend type Mutation {
      viewCreate(input: ViewCreateInput!): ViewPayload!
      viewUpdate(id: String!, input: ViewUpdateInput!): ViewPayload!
      viewDelete(id: String!): DeletePayload!
      viewFavoriteToggle(id: String!): ViewFavoritePayload!
      "Copy a view you can read; you own the copy, which is private unless visibility says otherwise."
      viewDuplicate(id: String!, name: String, visibility: String): ViewPayload!
      "Pin a team or project view as a tab on that page (or unpin it). Anyone who can read the view may; sortOrder places the tab."
      viewPin(id: String!, pinned: Boolean!, sortOrder: Float): ViewPayload!
      "Lay values (a display document; null clears a key) over your own layer (scope user, the default) or the default for everyone (scope default)."
      viewPreferencesSet(viewId: String, viewType: String, values: JSON!, scope: String): ViewPreferenceState!
      "Drop your own layer, back to the default."
      viewPreferencesReset(viewId: String, viewType: String): ViewPreferenceState!
      "Follow a task view; with neither flag, both events."
      viewSubscribe(viewId: String!, onAdded: Boolean, onCompleted: Boolean): ViewSubscriptionPayload!
      viewUnsubscribe(viewId: String!): ViewSubscriptionPayload!
      customViewCreate(input: CustomViewCreateInput!): CustomViewPayload!
      customViewUpdate(id: String!, input: CustomViewUpdateInput!): CustomViewPayload!
      customViewDelete(id: String!): DeletePayload!
    }
    """
)

query = QueryType()
mutation = MutationType()
view_type = ObjectType("View")
preview_type = ObjectType("ViewPreview")
subscription_type = ObjectType("ViewSubscription")
custom_view_type = ObjectType("CustomView")


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


def _connection(nodes: list[dict]) -> dict:
    return {"nodes": nodes, "totalCount": len(nodes), "pageInfo": {
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


@query.field("viewPreview")
def r_view_preview(_, info, kind, filters=None, display=None, scope=None, teamId=None,  # noqa: N803
                   projectId=None, initiativeId=None, first=None, after=None):  # noqa: N803
    """Validate an unsaved view like viewCreate would, then expose the same runners."""
    _store, writer, org, uid = _ctx(info)
    scope = scope or "workspace"
    try:
        parsed_filters = viewdocs.parse_filters(kind, filters)
        parsed_display = viewdocs.parse_display(kind, display)
        target = writer.view_scope_target(org, uid, scope, {
            "teamId": teamId, "projectId": projectId, "initiativeId": initiativeId})
    except Exception as exc:
        raise _bad(exc) from exc
    return {"kind": kind, "scope": scope, "filters": parsed_filters, "display": parsed_display,
            "_team_id": target.get("team_id"), "_project_id": target.get("project_id"),
            "_initiative_id": target.get("initiative_id"), "_first": first, "_after": after}


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


@mutation.field("viewDuplicate")
def m_view_duplicate(_, info, id, name=None, visibility=None):
    store, writer, org, uid = _ctx(info)
    try:
        view_id = writer.view_duplicate(org, uid, id, {"name": name, "visibility": visibility})
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True, "view": store.view_by_id(org, uid, view_id)}


@mutation.field("viewPin")
def m_view_pin(_, info, id, pinned, sortOrder=None):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    try:
        view_id = writer.view_pin(org, uid, id, bool(pinned), sortOrder)
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True, "view": store.view_by_id(org, uid, view_id)}


# --------------------------------------------------------------- preferences

def _layers(store, org, uid, target: dict) -> dict:
    view = target.get("view")
    return store.view_preference_layers(
        org, uid, target.get("view_id"), target.get("view_type"),
        view_display=viewdocs.load(view["display"]) if view else None)


@query.field("viewPreferences")
def r_view_preferences(_, info, viewId=None, viewType=None):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    try:
        with store.engine.connect() as conn:
            target = writer._preference_target(conn, org, uid, viewId, viewType)
    except Exception as exc:
        raise _bad(exc) from exc
    return _layers(store, org, uid, target)


@mutation.field("viewPreferencesSet")
def m_view_preferences_set(_, info, values, viewId=None, viewType=None, scope=None):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    _drop_batches(info)
    try:
        target = writer.view_preferences_set(org, uid, viewId, viewType, values, scope)
        if target.get("view"):  # the default may just have changed
            target["view"] = {"display": store.view_by_id(org, uid, target["view_id"])["display"]}
    except Exception as exc:
        raise _bad(exc) from exc
    return _layers(store, org, uid, target)


@mutation.field("viewPreferencesReset")
def m_view_preferences_reset(_, info, viewId=None, viewType=None):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    _drop_batches(info)
    try:
        target = writer.view_preferences_reset(org, uid, viewId, viewType)
    except Exception as exc:
        raise _bad(exc) from exc
    return _layers(store, org, uid, target)


# ------------------------------------------------------------- subscriptions

def _subscription_payload(store, org, uid, view_id):
    return {"success": True, "subscription": store.view_subscription_for(org, uid, view_id),
            "view": store.view_by_id(org, uid, view_id)}


@mutation.field("viewSubscribe")
def m_view_subscribe(_, info, viewId, onAdded=None, onCompleted=None):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    _drop_batches(info)
    try:
        view_id = writer.view_subscribe(org, uid, viewId, onAdded, onCompleted)
    except Exception as exc:
        raise _bad(exc) from exc
    return _subscription_payload(store, org, uid, view_id)


@mutation.field("viewUnsubscribe")
def m_view_unsubscribe(_, info, viewId):  # noqa: N803
    store, writer, org, uid = _ctx(info)
    _drop_batches(info)
    try:
        view_id = writer.view_unsubscribe(org, uid, viewId)
    except Exception as exc:
        raise _bad(exc) from exc
    return _subscription_payload(store, org, uid, view_id)


@subscription_type.field("view")
def s_view(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.view_by_id(org, uid, obj["_view_id"])


# ------------------------------------------------------------------- fields

@view_type.field("url")
def v_url(obj, info):
    store, _w, org, _uid = _ctx(info)
    cache = info.context.setdefault("cache", {})
    if "view_links" not in cache:
        cache["view_links"] = store.org_link_parts(org)
    url_key, base = cache["view_links"]
    return entity_url(url_key, "view", obj["slugId"], base)


# Per-request batches: a ``views { preferences subscription subscriberCount issues }`` list
# reads each table once, not once per view. Mutations that change these rows drop the batch
# so their payload (and any later mutation in the same document) reads fresh rows.
_BATCH_KEYS = ("view_user_prefs", "view_subs", "view_sub_counts")


def _batch(info, key: str, load):
    cache = info.context.setdefault("cache", {})
    if key not in cache:
        cache[key] = load()
    return cache[key]


def _drop_batches(info) -> None:
    cache = info.context.setdefault("cache", {})
    for key in _BATCH_KEYS:
        cache.pop(key, None)


def _view_layers(info, view_id: str, display: dict | None) -> dict:
    """``Store.view_preference_layers`` for a saved view, from the request's batch."""
    store, _w, org, uid = _ctx(info)
    row = _batch(info, "view_user_prefs",
                 lambda: store.view_user_preference_rows(org, uid)).get(view_id)
    default = dict(display or {})
    mine = viewdocs.load(row["values"]) if row else None
    return {"viewId": view_id, "viewType": None, "default": default, "user": mine,
            "effective": viewdocs.layer(default, mine or {}), "hasUserOverrides": bool(mine),
            "_org_row": None, "_user_row": row}


@view_type.field("preferences")
def v_preferences(obj, info):
    return _view_layers(info, obj["id"], obj.get("display"))


@view_type.field("subscription")
def v_subscription(obj, info):
    store, _w, org, uid = _ctx(info)
    return _batch(info, "view_subs", lambda: store.view_subscriptions_of(org, uid)).get(obj["id"])


@view_type.field("subscriberCount")
def v_subscriber_count(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _batch(info, "view_sub_counts",
                  lambda: store.view_subscriber_counts(org)).get(obj["id"], 0)


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


# -------------------------------------------------------------- execution
# View.issues/projects/initiatives and ViewPreview share these runners, so a draft in the
# UI and a saved view return the same rows for the same documents.

def _effective_display(obj: dict, kind: str, display, sort, reverse) -> dict:
    # A saved view runs with the caller's preferences laid over its default (resolved
    # once per request by ``_with_user_layer``); a preview has no stored layers.
    merged = dict(obj.get("_effective_display") or obj.get("display") or {})
    if display is not None:
        merged.update(viewdocs.parse_display(kind, display))
    if sort is not None:
        merged["ordering"] = viewdocs.parse_display(kind, {"ordering": sort})["ordering"]
    if reverse is not None:
        merged["reverse"] = bool(reverse)
    return merged


def _scope_filter(store, org, obj: dict) -> dict:
    return store.view_scope_filter(org, obj["kind"], obj.get("scope"),
                                   obj.get("_team_id"), obj.get("_project_id"),
                                   obj.get("_initiative_id"))


def _paged(nodes: list[dict], keys: list[list], total: int, limit: int, signature: str,
           after) -> dict:
    page, page_keys = nodes[:limit], keys[:limit]
    return {"nodes": page, "totalCount": total, "pageInfo": {
        "hasNextPage": len(nodes) > limit, "hasPreviousPage": after is not None,
        "startCursor": viewdocs.encode_cursor(signature, page_keys[0]) if page else None,
        "endCursor": viewdocs.encode_cursor(signature, page_keys[-1]) if page else None,
    }}


def _defaults(obj: dict, first, after):
    return (obj.get("_first") if first is None else first,
            obj.get("_after") if after is None else after)


def _with_user_layer(store, org, uid, obj: dict, info=None) -> dict:
    if "id" not in obj or "_effective_display" in obj:
        return obj
    if info is not None:
        layers = _view_layers(info, obj["id"], obj.get("display"))
    else:
        layers = store.view_preference_layers(org, uid, obj["id"], None,
                                              view_display=obj.get("display") or {})
    return {**obj, "_effective_display": layers["effective"]}


def run_issues(obj, info, first=None, after=None, filter=None, display=None, sort=None,
               reverse=None):
    store, _w, org, uid = _ctx(info)
    if obj["kind"] != "issue":
        return _connection([])
    obj = _with_user_layer(store, org, uid, obj, info)
    first, after = _defaults(obj, first, after)
    try:
        shown = _effective_display(obj, "issue", display, sort, reverse)
        ordering, backwards = viewdocs.display_order(shown, "issue")
        extra = viewdocs.compile_filter("issue", viewdocs.parse_filters("issue", filter),
                                        viewer_id=uid) if filter else {}
        flt = viewdocs.conjoin(viewdocs.compile_filter("issue", obj.get("filters") or {},
                                                       viewer_id=uid),
                               extra, _scope_filter(store, org, obj))
        signature = f"issue:{ordering}:{int(backwards)}"
        cursor = viewdocs.decode_cursor(signature, after)
        limit = viewdocs.page_size(first)
        page = store.view_issues(
            org, uid, flt, ordering=ordering, reverse=backwards, after=cursor, limit=limit + 1,
            completed_since=viewdocs.completed_window_start(shown),
            show_sub_issues=shown.get("showSubIssues", True))
    except Exception as exc:
        raise _bad(exc) from exc
    from hoja_server.resolvers import _prime_issue_nodes

    result = _paged(page["nodes"], page["keys"], page["total"], limit, signature, after)
    _prime_issue_nodes(info, result["nodes"])
    return result


def run_projects(obj, info, first=None, after=None, filter=None, display=None, sort=None,
                 reverse=None):
    store, _w, org, uid = _ctx(info)
    if obj["kind"] != "project":
        return _connection([])
    obj = _with_user_layer(store, org, uid, obj, info)
    first, after = _defaults(obj, first, after)
    try:
        shown = _effective_display(obj, "project", display, sort, reverse)
        ordering, backwards = viewdocs.display_order(shown, "project")
        extra = viewdocs.compile_filter("project", viewdocs.parse_filters("project", filter),
                                        viewer_id=uid) if filter else {}
        flt = viewdocs.conjoin(viewdocs.compile_filter("project", obj.get("filters") or {},
                                                       viewer_id=uid),
                               extra, _scope_filter(store, org, obj))
        signature = f"project:{ordering}:{int(backwards)}"
        cursor = viewdocs.decode_cursor(signature, after)
        limit = viewdocs.page_size(first)
        page = store.view_projects(org, uid, flt, ordering=ordering, reverse=backwards,
                                   after=cursor, limit=limit + 1)
    except Exception as exc:
        raise _bad(exc) from exc
    return _paged(page["nodes"], page["keys"], page["total"], limit, signature, after)


def run_initiatives(obj, info, first=None, after=None, filter=None, display=None, sort=None,
                    reverse=None):
    """Initiative views filter and order in Python over light rows (health and labels are
    derived), then serialize only the returned page."""
    store, _w, org, uid = _ctx(info)
    if obj["kind"] != "initiative":
        return _connection([])
    obj = _with_user_layer(store, org, uid, obj, info)
    first, after = _defaults(obj, first, after)
    try:
        saved = obj.get("filters") or {}
        extra = viewdocs.parse_filters("initiative", filter) if filter else {}
        shown = _effective_display(obj, "initiative", display, None, reverse)
        if sort is not None:
            shown["ordering"] = viewdocs.parse_display("initiative", {"ordering": sort})["ordering"]
        ordering = shown.get("ordering") or "manual"
        backwards = bool(shown.get("reverse"))
        signature = f"initiative:{ordering}:{int(backwards)}"
        cursor = viewdocs.decode_cursor(signature, after)
        limit = viewdocs.page_size(first)
        statuses = None
        if "status" in saved:
            op, operand = next(iter(saved["status"].items()))
            statuses = [operand] if op == "is" else list(operand)
        within = None
        if obj.get("scope") == "initiative" and obj.get("_initiative_id"):
            within = store.initiative_descendants(org, obj["_initiative_id"])
        rows = store.view_initiative_rows(org, statuses=statuses, within=within, viewer_id=uid)
        if ordering == "health" or any(k != "status" for k in (*saved, *extra)):
            from hoja_server import initiative_api

            rows = initiative_api.with_filter_fields(store, org, rows)
        rows = [r for r in rows
                if viewdocs.initiative_passes(saved, r) and viewdocs.initiative_passes(extra, r)]
        keyed = []
        for r in rows:
            components = viewdocs.initiative_sort_components(ordering, r)
            keyed.append(([c[0] for c in components], r["id"]))
        if not keyed:
            return _paged([], [], 0, limit, signature, after)
        shape = viewdocs.initiative_sort_components(ordering, rows[0])
        if cursor is not None and len(cursor) != len(shape):
            raise viewdocs.ViewError("after belongs to a different ordering.", "after")
        compare = functools.cmp_to_key(
            lambda a, b: viewdocs.keyset_compare(a[0], b[0], shape, backwards))
        keyed.sort(key=compare)
        if cursor is not None:
            try:
                keyed = [k for k in keyed
                         if viewdocs.keyset_compare(k[0], cursor, shape, backwards) > 0]
            except TypeError as exc:
                raise viewdocs.ViewError("after is not a cursor this view returned.",
                                         "after") from exc
    except Exception as exc:
        raise _bad(exc) from exc
    window = keyed[:limit + 1]
    nodes = store.initiatives_by_ids(org, [k[1] for k in window])
    return _paged(nodes, [k[0] for k in window], len(rows), limit, signature, after)


for _bindable in (view_type, preview_type):
    _bindable.set_field("issues", run_issues)
    _bindable.set_field("projects", run_projects)
    _bindable.set_field("initiatives", run_initiatives)


# ------------------------------------------------------- Linear CustomView aliases
# customView* reads and writes the same saved_view rows as view*; only the shape differs.
# Filters cross the boundary through view_linear, which refuses what it cannot translate.

_MODEL_NAMES = {"issue": "Issue", "project": "Project", "initiative": "Initiative"}


def _custom_input(inp: dict, current: dict | None) -> dict:
    """CustomView(Create|Update)Input → ViewCreateInput / ViewUpdateInput."""
    out: dict = {k: inp[k] for k in ("name", "description", "icon", "color", "ownerId")
                 if k in inp}
    kind = current["kind"] if current else ("project" if inp.get("projectFilterData") else "issue")
    if current is None:
        out["kind"] = kind
    if "filterData" in inp and inp["filterData"] not in (None, {}):
        if kind != "issue":
            raise viewdocs.ViewError("filterData applies to issue views; this view lists "
                                     f"{kind}s.", "filterData")
        out["filters"] = view_linear.to_hoja("issue", inp["filterData"])
    elif "filterData" in inp and kind == "issue":
        out["filters"] = {}
    if "projectFilterData" in inp and inp["projectFilterData"] not in (None, {}):
        if kind != "project":
            raise viewdocs.ViewError("projectFilterData applies to project views.",
                                     "projectFilterData")
        out["filters"] = view_linear.to_hoja("project", inp["projectFilterData"])
    elif "projectFilterData" in inp and kind == "project":
        out["filters"] = {}
    placement = [k for k in ("teamId", "projectId", "initiativeId") if k in inp]
    scope = current["scope"] if current else "workspace"
    if placement or current is None:
        named = next((k for k in ("teamId", "projectId", "initiativeId") if inp.get(k)), None)
        scope = {"teamId": "team", "projectId": "project",
                 "initiativeId": "initiative", None: "workspace"}[named]
        out["scope"] = scope
        if named:
            out[named] = inp[named]
    if "shared" in inp or current is None:
        shared = bool(inp.get("shared"))
        out["visibility"] = ("team" if scope == "team" else "workspace") if shared else "private"
    elif current and current["visibility"] == "team" and scope != "team":
        out["visibility"] = "workspace"  # a team-shared view moved off its team stays shared
    return out


def _custom_view(store, org, uid, ident) -> dict:
    row = store.view_by_id(org, uid, ident)
    if not row:
        raise _not_found()
    return row


@query.field("customView")
def r_custom_view(_, info, id):
    store, _w, org, uid = _ctx(info)
    return _custom_view(store, org, uid, id)


@query.field("customViews")
def r_custom_views(_, info, first=None, after=None, includeArchived=False):  # noqa: N803
    store, _w, org, uid = _ctx(info)
    rows = store.views(org, uid, include_archived=bool(includeArchived))
    if after:
        ids = [r["id"] for r in rows]
        if after not in ids:
            raise _bad(viewdocs.ViewError("after is not a cursor this list returned.", "after"))
        rows = rows[ids.index(after) + 1:]
    limit = viewdocs.page_size(first)
    page = rows[:limit]
    return {"nodes": page, "pageInfo": {
        "hasNextPage": len(rows) > limit, "hasPreviousPage": bool(after),
        "startCursor": page[0]["id"] if page else None,
        "endCursor": page[-1]["id"] if page else None}}


@query.field("customViewHasSubscribers")
def r_custom_view_has_subscribers(_, info, id):
    store, _w, org, uid = _ctx(info)
    row = _custom_view(store, org, uid, id)
    return {"hasSubscribers": store.view_subscriber_count(org, row["id"]) > 0}


@mutation.field("customViewCreate")
def m_custom_view_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    try:
        # ownerId is applied inside view_create's transaction: a bad owner leaves no view.
        view_id = writer.view_create(org, uid, _custom_input(input or {}, None))
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True, "customView": store.view_by_id(org, uid, view_id)}


@mutation.field("customViewUpdate")
def m_custom_view_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    current = _custom_view(store, org, uid, id)
    try:
        writer.view_update(org, uid, current["id"],
                           _custom_input(input or {}, current))
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True, "customView": _custom_view(store, org, uid, current["id"])}


@mutation.field("customViewDelete")
def m_custom_view_delete(_, info, id):
    store, writer, org, uid = _ctx(info)
    current = _custom_view(store, org, uid, id)
    try:
        writer.view_delete(org, uid, current["id"])
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True}


@custom_view_type.field("modelName")
def c_model_name(obj, _info):
    return _MODEL_NAMES.get(obj["kind"], "Issue")


@custom_view_type.field("filterData")
def c_filter_data(obj, _info):
    return view_linear.to_linear("issue", obj.get("filters") or {}) if obj["kind"] == "issue" else {}


@custom_view_type.field("projectFilterData")
def c_project_filter_data(obj, _info):
    return view_linear.to_linear("project", obj.get("filters") or {}) \
        if obj["kind"] == "project" else None


@custom_view_type.field("initiativeFilterData")
def c_initiative_filter_data(obj, _info):
    # Initiative filters already use Linear's property names (status, health, owner…).
    return obj.get("filters") or {} if obj["kind"] == "initiative" else None


@custom_view_type.field("viewPreferencesValues")
def c_view_preferences_values(obj, info):
    return v_preferences(obj, info)["effective"]


@custom_view_type.field("organizationViewPreferences")
def c_organization_view_preferences(obj, _info):
    return {"id": f"{obj['id']}:organization", "type": "organization",
            "viewType": "customView", "preferences": obj.get("display") or {},
            "createdAt": obj.get("createdAt"), "updatedAt": obj.get("updatedAt")}


@custom_view_type.field("userViewPreferences")
def c_user_view_preferences(obj, info):
    layers = v_preferences(obj, info)
    row = layers.get("_user_row")
    if not row:
        return None
    return {"id": row["id"], "type": "user", "viewType": "customView",
            "preferences": layers["user"] or {}, "createdAt": row["created_at"],
            "updatedAt": row["updated_at"]}


def _linear_filter(kind: str, flt):
    try:
        return view_linear.to_hoja(kind, flt) if flt else None
    except viewdocs.ViewError as exc:
        raise _bad(exc) from exc


@custom_view_type.field("issues")
def c_issues(obj, info, first=None, after=None, filter=None):
    return run_issues(obj, info, first=first, after=after,
                      filter=_linear_filter("issue", filter))


@custom_view_type.field("projects")
def c_projects(obj, info, first=None, after=None, filter=None):
    return run_projects(obj, info, first=first, after=after,
                        filter=_linear_filter("project", filter))


for _field in ("owner", "creator", "updatedBy", "team", "project", "initiative", "url"):
    custom_view_type.set_field(_field, view_type._resolvers[_field])


# ------------------------------------------------------------------ CSV export
# GET /api/views/<id or slug>/export.csv (app.py). The view runs exactly as View.issues does
# for the caller — saved filter, scope, team visibility, the caller's effective display —
# and the first rows are written as CSV. Members get at most 250 rows and admins 2,000,
# Linear's limits.

EXPORT_COLUMNS = ("identifier", "title", "status", "priority", "assignee", "labels", "project",
                  "cycle", "dueDate", "createdAt", "updatedAt", "url")
MEMBER_EXPORT_LIMIT = 250
ADMIN_EXPORT_LIMIT = 2000


def _csv_cell(value) -> str:
    """Neutralize spreadsheet formulas: a title is user text, not a formula to evaluate."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(store, org: str, uid: str, ident: str) -> dict | None:
    """``{"csv", "filename", "total", "limit"}`` for a task view the caller can read, or
    None when there is no such view (or it is not a task view) for them."""
    import csv
    import io

    row = store.view_by_id(org, uid, ident)
    if not row or row["kind"] != "issue":
        return None
    caller = store.get_user(org, uid) or {}
    limit = ADMIN_EXPORT_LIMIT if caller.get("admin") else MEMBER_EXPORT_LIMIT
    shown = _with_user_layer(store, org, uid, row)["_effective_display"]
    ordering, backwards = viewdocs.display_order(shown, "issue")
    flt = viewdocs.conjoin(viewdocs.compile_filter("issue", row.get("filters") or {},
                                                   viewer_id=uid),
                           _scope_filter(store, org, row))
    page = store.view_issues(org, uid, flt, ordering=ordering, reverse=backwards, limit=limit,
                             completed_since=viewdocs.completed_window_start(shown),
                             show_sub_issues=shown.get("showSubIssues", True))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for record in store.view_export_rows(org, page["nodes"]):
        writer.writerow([_csv_cell(record[c]) for c in EXPORT_COLUMNS])
    return {"csv": buffer.getvalue(), "filename": f"{row['slugId'] or 'view'}.csv",
            "total": page["total"], "limit": limit}
