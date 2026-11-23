"""Ariadne resolver bindings — maps the SDL to Store/Writer.

Auth context (user_id, organization_id) is attached to `info.context` by the
FastAPI middleware in app.py. Every resolver reads ctx and stays org-scoped.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone

from ariadne import (
    MutationType,
    ObjectType,
    QueryType,
    ScalarType,
    make_executable_schema,
)
from graphql import GraphQLError

from hoja_server import mail_events
from hoja_server import notifications
from hoja_server import audit

logger = logging.getLogger(__name__)
from hoja_server import extensions
from hoja_server import agent_members, agent_policy
from hoja_server import permissions
from hoja_server import store as store_module
from hoja_server.deployment import single_org_mode
from hoja_server.github_linking import parse_github_url

query = QueryType()
mutation = MutationType()
Initiative = ObjectType("Initiative")

# Object types needing custom field resolvers
Team = ObjectType("Team")
Initiative.set_field("owner", lambda obj, info: info.context["store"].get_user(info.context["org_id"], obj.get("_owner_id")) if obj.get("_owner_id") else None)
Initiative.set_field("creator", lambda obj, info: info.context["store"].get_user(info.context["org_id"], obj.get("_creator_id")) if obj.get("_creator_id") else None)
Issue = ObjectType("Issue")
IssueActivity = ObjectType("IssueActivity")
MyIssueActivity = ObjectType("MyIssueActivity")
Project = ObjectType("Project")
ProjectUpdate = ObjectType("ProjectUpdate")
InitiativeUpdate = ObjectType("InitiativeUpdate")
Comment = ObjectType("Comment")
WorkflowState = ObjectType("WorkflowState")
Organization = ObjectType("Organization")
Organization.set_field("origin", lambda obj, info: obj.get("origin") or info.context["store"].workspace_origin(obj["id"]))
Cycle = ObjectType("Cycle")
Attachment = ObjectType("Attachment")
GithubLink = ObjectType("GithubLink")

datetime_scalar = ScalarType("DateTime")
timeless_scalar = ScalarType("TimelessDate")
json_scalar = ScalarType("JSON")


@datetime_scalar.serializer
def _ser_dt(value):
    return value  # already ISO strings in the DB


@timeless_scalar.serializer
def _ser_td(value):
    return value


@json_scalar.serializer
def _ser_json(value):
    return value


def _ctx(info):
    return info.context["store"], info.context["writer"], info.context["org_id"], info.context["user_id"]


def _memo(info, kind, key, fn):
    """Per-request memoization for reference lookups repeated across list rows.
    Turns the issues-list N+1 (one lookup per issue) into one lookup per distinct entity."""
    ctx = info.context
    cache = ctx.get("cache") if isinstance(ctx, dict) else None
    if cache is None:
        return fn()
    ck = (kind, key)
    if ck not in cache:
        cache[ck] = fn()
    return cache[ck]


def _batch_entry(info, kind):
    ctx = info.context
    cache = ctx.get("cache") if isinstance(ctx, dict) else None
    if cache is None:
        return None
    return cache.setdefault(("batch", kind), {"pending": set(), "loaded": {}})


def _prime(info, kind, ids):
    """Announce the keys a list page will resolve so the first lookup loads them all."""
    entry = _batch_entry(info, kind)
    if entry is not None:
        entry["pending"].update(i for i in ids if i and i not in entry["loaded"])


def _batched(info, kind, key, load_many, single, default=None):
    """Resolve key from a primed batch (one query for every primed key), else singly."""
    entry = _batch_entry(info, kind)
    if entry is None:
        return single()
    if key in entry["loaded"]:
        return entry["loaded"][key]
    if key in entry["pending"]:
        ids = list(entry["pending"])
        entry["pending"].clear()
        got = load_many(ids)
        for i in ids:
            entry["loaded"][i] = got.get(i, default)
        return entry["loaded"][key]
    value = single()
    entry["loaded"][key] = value
    return value


def _user_row(info, user_id):
    store, _w, org, _uid = _ctx(info)
    return _batched(info, "user_row", user_id, lambda ids: store.users_by_ids(org, ids),
                    lambda: store.get_user(org, user_id))


def _prime_issue_nodes(info, nodes) -> None:
    """Per-row lookups of an issue page (labels, subscribers, people) become one query each."""
    ids = [n["id"] for n in nodes if n]
    _prime(info, "state", [n.get("_state_id") for n in nodes if n])
    _prime(info, "issue_labels", ids)
    _prime(info, "issue_subscribers", ids)
    _prime(info, "user_row", [n.get(k) for n in nodes if n for k in ("_assignee_id", "_creator_id")])
    _prime(info, "project", [n.get("_project_id") for n in nodes if n])


_MAX_PAGE = 250


def _read_only(info) -> bool:
    return info.context.get("access") == "read"

_DEFAULT_PAGE = 50


def _page_size(value, default=_DEFAULT_PAGE) -> int:
    """Clamp first/last to 1..250; null means the default. Out-of-range values are rejected."""
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= _MAX_PAGE:
        raise GraphQLError(f"first must be an integer from 1 to {_MAX_PAGE}.",
                           extensions={"code": "BAD_USER_INPUT", "field": "first"})
    return value


def _conn(nodes: list) -> dict:
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": False, "hasPreviousPage": False,
            "startCursor": None,
            "endCursor": (nodes[-1].get("id") if nodes else None),
        },
    }


def _issue_cursor(node: dict, order_by: str) -> str:
    value = node.get("updatedAt" if order_by == "updatedAt" else "createdAt")
    payload = json.dumps([value, node["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_issue_cursor(cursor: str | None):
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(part, str) and part for part in value):
            raise ValueError
        return value[0], value[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise GraphQLError("Invalid issue cursor.", extensions={"code": "BAD_USER_INPUT", "field": "after"}) from exc


def _activity_cursor(row):
    raw = json.dumps([row["created_at"], row["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_activity_cursor(cursor):
    if not cursor:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise GraphQLError("Invalid activity cursor.", extensions={"code": "BAD_USER_INPUT", "field": "after"}) from exc


def _project_update_cursor(row):
    # Feed cursors encode the sort key: updated_at where the row has one (change
    # feeds), created_at where the caller passes it explicitly (projects, initiatives).
    sort_key = row.get("updated_at") or row["created_at"]
    raw = json.dumps([sort_key, row["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_project_update_cursor(cursor, label="project update"):
    if cursor is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) and x for x in value):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise GraphQLError(f"Invalid {label} cursor.", extensions={"code": "BAD_USER_INPUT", "field": "after"}) from exc


# ----------------------------------------------------------------- Query
@query.field("inboxNotifications")
def r_inbox_notifications(_, info, first=50, after=None, unreadOnly=False, archived=False,
                          snoozed=False):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_activity_cursor(after)
    rows = store.inbox_notifications(org, uid, cursor, first + 1, unread_only=unreadOnly,
                                     archived=archived, snoozed=snoozed)
    page = rows[:first]
    # A row whose subject the viewer can no longer see (a task or project behind a
    # team made private to them) is left out rather than shown with a blank subject.
    issue_ids = list({row["issue_id"] for row in page if row.get("issue_id")})
    visible = store.issues_by_ids(org, issue_ids, viewer_id=uid) if issue_ids else {}
    _prime_issue_nodes(info, list(visible.values()))
    entry = _batch_entry(info, "issue_obj")
    if entry is not None:
        entry["loaded"].update(visible)
    other_visible = store.visible_inbox_subjects(org, uid, page)
    nodes = []
    for row in page:
        subject_type, subject_id = notifications.subject_of(row)
        if subject_type is None or subject_id not in (visible if subject_type == "issue"
                                                       else other_visible):
            continue
        nodes.append(dict(row, createdAt=row["created_at"], readAt=row["read_at"],
                          archivedAt=row["archived_at"], snoozedUntil=row.get("snoozed_until"),
                          subjectType=subject_type, subjectId=subject_id,
                          _actor_id=row["actor_id"], _issue_id=row.get("issue_id"),
                          _comment_id=row.get("comment_id")))
    _prime(info, "user_row", [n["_actor_id"] for n in nodes])
    _prime(info, "inbox_comment", [n["_comment_id"] for n in nodes])
    for kind in ("project", "initiative", "cycle"):
        _prime(info, f"inbox_{kind}", [n.get(f"{kind}_id") for n in nodes])
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(page[0]) if page else None,
        "endCursor": _activity_cursor(page[-1]) if page else None,
    }}


@query.field("inboxUnreadCount")
def r_inbox_unread_count(_, info):
    store, _w, org, uid = _ctx(info)
    return store.inbox_unread_count(org, uid)


def _snooze_until(until):
    """Normalize a client instant to the stored UTC ISO form, so string order is time order."""
    if until in (None, ""):
        return None
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        raise GraphQLError("until must be an ISO-8601 date-time.",
                           extensions={"code": "BAD_USER_INPUT", "field": "until"}) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


@mutation.field("notificationMarkRead")
def m_notification_mark_read(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark(org, uid, id, "read_at")


@mutation.field("notificationMarkUnread")
def m_notification_mark_unread(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_clear(org, uid, id, "read_at")


@mutation.field("notificationArchive")
def m_notification_archive(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark(org, uid, id, "archived_at")


@mutation.field("notificationUnarchive")
def m_notification_unarchive(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.inbox_clear(org, uid, id, "archived_at")


@mutation.field("notificationSnooze")
def m_notification_snooze(_, info, id, until=None):
    store, _w, org, uid = _ctx(info)
    return store.inbox_snooze(org, uid, id, _snooze_until(until))


@mutation.field("notificationsMarkReadForIssue")
def m_notifications_mark_read_for_issue(_, info, issueId, read=True):
    store, _w, org, uid = _ctx(info)
    return store.inbox_issue_update(org, uid, issueId, "read", read=read)


@mutation.field("notificationsArchiveForIssue")
def m_notifications_archive_for_issue(_, info, issueId):
    store, _w, org, uid = _ctx(info)
    return store.inbox_issue_update(org, uid, issueId, "archive")


@mutation.field("notificationsSnoozeForIssue")
def m_notifications_snooze_for_issue(_, info, issueId, until=None):
    store, _w, org, uid = _ctx(info)
    return store.inbox_issue_update(org, uid, issueId, "snooze", until=_snooze_until(until))


@mutation.field("notificationsArchiveAllRead")
def m_notifications_archive_all_read(_, info):
    store, _w, org, uid = _ctx(info)
    return store.inbox_archive_all_read(org, uid)


@mutation.field("githubLinkAdd")
def m_github_link_add(_, info, issueId, url):
    """Attach a GitHub issue, PR, commit or release URL to a task as a reference."""
    store, _w, org, _uid = _ctx(info)
    reference = parse_github_url(url)
    if reference is None:
        raise GraphQLError(
            "Not a GitHub issue, pull request, commit or release URL.",
            extensions={"code": "BAD_USER_INPUT", "field": "url"})
    link = store.github_link_add(
        org, issueId, kind=reference.kind, external_id=reference.external_id,
        url=reference.url, title=reference.title)
    if link is None:
        raise GraphQLError("Issue not found.", extensions={"code": "NOT_FOUND", "field": "issueId"})
    return link


def _review_account(store, org_id, user_id):
    """The GitHub account Hoja may act through for this user.

    Prefers the user's own grant over a workspace-wide one, and never returns a revoked row: an
    action attributed to a person must run on that person's authorization.
    """
    from sqlalchemy import and_, or_, select

    from hoja_server import tenant_context
    from hoja_server.db import connector_account

    with tenant_context.scoped(org_id), store.engine.connect() as conn:
        return conn.execute(
            select(connector_account).where(and_(
                connector_account.c.organization_id == org_id,
                connector_account.c.provider == "github",
                connector_account.c.status == "active",
                connector_account.c.revoked_at.is_(None),
                or_(connector_account.c.owner_user_id == user_id,
                    connector_account.c.owner_user_id.is_(None)),
            )).order_by(connector_account.c.owner_user_id.is_(None),
                        connector_account.c.created_at).limit(1)
        ).mappings().first()


def _run_coro(coro):
    """Run one review operation to completion inside the resolver's thread.

    The HTTP schema executes every operation through `graphql_sync` in a worker thread (app.py),
    so a resolver cannot await: returning a coroutine would hand GraphQL an object it cannot
    serialize and the field would come back null. `asyncio.run` is safe here only because this
    thread owns no running loop, which is exactly the contract `run_in_threadpool` provides.
    """
    import asyncio

    return asyncio.run(coro)


async def _run_review_async(info, link_id, action, *, body=None, sha=None, method="squash"):
    """One review write, end to end.

    The row is updated only from the provider's confirmed answer, so a refusal or an ambiguous
    response leaves the review exactly as it was — there is no path here that flips a pull request
    to Merged on the strength of our own optimism.
    """
    import httpx
    from github import GitHubAppConfig
    from github.errors import ConnectorAPIError
    from github.writes import GitHubWriteClient

    from hoja_server.github_review_actions import ReviewRefused, perform
    from hoja_server.github_tokens import GitHubTokenStore

    store, _w, org, uid = _ctx(info)
    link = store.github_link_by_id(org, link_id)
    if link is None:
        raise GraphQLError("Review not found.",
                           extensions={"code": "NOT_FOUND", "field": "linkId"})
    account = _review_account(store, org, uid)
    if account is None or not account.get("encrypted_refresh_token"):
        raise GraphQLError(
            "GitHub is not connected for this workspace, so Hoja cannot act on your behalf.",
            extensions={
                "code": "not_connected",
                "hint": "Connect GitHub in Settings > Connections, then try again.",
            })
    viewer = store.viewer(org, uid) or {}
    user_name = viewer.get("displayName") or viewer.get("name") or "a teammate"
    async with httpx.AsyncClient(timeout=30.0) as http:
        tokens = GitHubTokenStore(store.engine, http, GitHubAppConfig.from_env())
        writes = GitHubWriteClient(http, tokens.source(account["id"], org))
        try:
            outcome = await perform(
                writes, link=link, action=action, user_name=user_name,
                body=body, sha=sha, method=method)
        except ReviewRefused as exc:
            raise GraphQLError(exc.message, extensions={
                "code": exc.code,
                "gate": exc.gate.as_dict() if exc.gate else None,
            }) from exc
        except ConnectorAPIError as exc:
            raise GraphQLError(str(exc), extensions={
                "code": "github_error", "hint": getattr(exc, "hint", None),
            }) from exc

    gate = outcome.get("gate")
    gate_json = gate.as_dict() if gate else None
    updated = store.github_review_recorded(
        org, link, event_type=outcome["eventType"], actor_id=uid,
        changes={
            "via": "github",
            "action": outcome["action"],
            "pullRequest": link.get("url"),
            "sha": outcome.get("sha") or None,
            "mergeSha": outcome.get("mergeSha") or None,
            "actor": outcome["actor"],
            "onBehalfOf": user_name,
            "gate": gate_json,
        },
        merged_sha=outcome.get("mergeSha") or None)
    return {
        "action": outcome["action"],
        "sha": outcome.get("sha") or None,
        "mergeSha": outcome.get("mergeSha") or None,
        "gate": gate_json,
        "actor": outcome["actor"],
        "link": updated,
    }


@mutation.field("reviewApprove")
def m_review_approve(_, info, linkId, body=None):
    return _run_coro(_run_review_async(info, linkId, "approve", body=body))


@mutation.field("reviewRequestChanges")
def m_review_request_changes(_, info, linkId, body):
    return _run_coro(_run_review_async(info, linkId, "request_changes", body=body))


@mutation.field("reviewComment")
def m_review_comment(_, info, linkId, body):
    return _run_coro(_run_review_async(info, linkId, "comment", body=body))


@mutation.field("reviewMerge")
def m_review_merge(_, info, linkId, sha, method="squash"):
    return _run_coro(_run_review_async(info, linkId, "merge", sha=sha, method=method))


@query.field("reviewGate")
def r_review_gate(_, info, linkId):
    return _run_coro(_review_gate_async(info, linkId))


@query.field("pullRequests")
def r_pull_requests(_, info, first=250):
    """The Reviews queue: every linked pull request, independent of task recency."""
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    links = store.pull_request_links(org, limit=first)
    # One batched issue lookup for the whole page instead of one per row.
    _prime(info, "issue_obj", [link.get("_issue_id") for link in links])
    return links


@mutation.field("githubFilePatchLoad")
def m_github_file_patch_load(_, info, linkId, path, sha=None):
    return _run_coro(_github_file_patch_load_async(info, linkId, path, sha))


async def _github_file_patch_load_async(info, link_id, path, sha=None):
    """Load one file's patch on demand and cache it back (HOJA-513).

    The row is written only when GitHub actually returned a patch that fits the cap,
    and only through the account's existing TokenSource — the same one credential
    discipline the review writes keep. A sha that no longer matches the link's stored
    head refuses instead of silently loading a diff from another commit.
    """
    import httpx
    from github import GitHubAppConfig
    from github.errors import ConnectorAPIError
    from github.writes import GitHubWriteClient

    from hoja_server import github_file_patches
    from hoja_server.github_review_actions import split_repository
    from hoja_server.github_tokens import GitHubTokenStore

    store, _w, org, uid = _ctx(info)
    link = store.github_link_by_id(org, link_id)
    if link is None:
        raise GraphQLError("Review not found.",
                           extensions={"code": "NOT_FOUND", "field": "linkId"})
    target = split_repository(link.get("url"))
    if target is None:
        raise GraphQLError("This link is not a pull request.",
                           extensions={"code": "not_a_pull_request", "field": "linkId"})
    if sha and link.get("sha") and sha != link.get("sha"):
        raise GraphQLError(
            "The pull request head moved since this review was read; load the diff "
            "again for the new commit.",
            extensions={"code": github_file_patches.HEAD_MOVED, "field": "sha"})
    account = _review_account(store, org, uid)
    if account is None or not account.get("encrypted_refresh_token"):
        raise GraphQLError(
            "GitHub is not connected for this workspace, so there is no diff to load.",
            extensions={
                "code": "not_connected",
                "hint": "Connect GitHub in Settings > Connections, then try again.",
            })
    repository, number = target
    stored_entry = next(
        (f for f in (link.get("files") or [])
         if isinstance(f, dict) and str(f.get("path") or "") == (path or "").strip()),
        None)
    async with httpx.AsyncClient(timeout=30.0) as http:
        tokens = GitHubTokenStore(store.engine, http, GitHubAppConfig.from_env())
        writes = GitHubWriteClient(http, tokens.source(account["id"], org))
        try:
            outcome = await github_file_patches.load_file_patch(
                writes, repository, number, path, stored_entry=stored_entry)
        except ConnectorAPIError as exc:
            raise GraphQLError(str(exc), extensions={
                "code": "github_error", "hint": getattr(exc, "hint", None),
            }) from exc
    marker = outcome["marker"]
    patch = outcome["patch"]
    cached = False
    if patch is not None:
        updated = store.github_link_cache_file_patch(org, link_id, path, patch)
        if updated is not None:
            link = updated
            cached = True
    result = github_file_patches.result_for(path, patch, marker, cached=cached,
                                            reason=outcome["reason"])
    result["link"] = link
    return result


async def _review_gate_async(info, linkId):
    """Read the gate from GitHub now.

    The merge mutation runs this same `refusal()` against the same live read, so what the UI
    offers to merge and what the server permits cannot drift apart. A disabled Merge button here
    is the server's answer, not a client-side guess.
    """
    import httpx
    from github import GitHubAppConfig
    from github.errors import ConnectorAPIError
    from github.writes import GitHubWriteClient

    from hoja_server.github_review_actions import gate_from, refusal, split_repository
    from hoja_server.github_tokens import GitHubTokenStore

    store, _w, org, uid = _ctx(info)
    link = store.github_link_by_id(org, linkId)
    if link is None:
        raise GraphQLError("Review not found.",
                           extensions={"code": "NOT_FOUND", "field": "linkId"})
    target = split_repository(link.get("url"))
    if target is None:
        raise GraphQLError("This link is not a pull request.",
                           extensions={"code": "not_a_pull_request", "field": "linkId"})
    repository, number = target
    account = _review_account(store, org, uid)
    if account is None or not account.get("encrypted_refresh_token"):
        raise GraphQLError(
            "GitHub is not connected for this workspace, so there is no gate to read.",
            extensions={
                "code": "not_connected",
                "hint": "Connect GitHub in Settings > Connections, then try again.",
            })
    async with httpx.AsyncClient(timeout=30.0) as http:
        tokens = GitHubTokenStore(store.engine, http, GitHubAppConfig.from_env())
        writes = GitHubWriteClient(http, tokens.source(account["id"], org))
        try:
            pull = await writes.fetch_pull(repository, number)
        except ConnectorAPIError as exc:
            # The pull request itself is unreadable: there is no gate to show.
            raise GraphQLError(str(exc), extensions={
                "code": "github_error", "hint": getattr(exc, "hint", None),
            }) from exc
        head = (pull.get("head") or {}).get("sha") or ""
        # A failed *check* read is different: GitHub answers 403 for an app without Checks and
        # Commit statuses permissions, which is a fact about the install, not about this pull
        # request. Losing the whole gate hid mergeability and the review decision behind
        # "cannot read that resource"; instead the gate is returned undecided, the pane keeps
        # showing what GitHub did answer, and refusal() blocks the merge with the reason.
        checks_unavailable = False
        check_runs: list[dict] = []
        combined: dict = {}
        if head:
            try:
                check_runs = await writes.fetch_check_runs(repository, head)
            except ConnectorAPIError:
                checks_unavailable = True
            try:
                combined = await writes.fetch_combined_status(repository, head)
            except ConnectorAPIError:
                checks_unavailable = True
        gate = gate_from(pull, check_runs, combined, checks_unavailable=checks_unavailable)
    blocked = refusal(gate)
    payload = gate.as_dict()
    payload["refusalCode"] = blocked[0] if blocked else None
    payload["refusalMessage"] = blocked[1] if blocked else None
    return payload


@mutation.field("notificationMarkAllRead")
def m_notification_mark_all_read(_, info):
    store, _w, org, uid = _ctx(info)
    return store.inbox_mark_all_read(org, uid)


InboxNotification = ObjectType("InboxNotification")


@GithubLink.field("issue")
def github_link_issue(obj, info):
    """The task a link belongs to, batched with the rest of the page."""
    return _issue_obj(info, obj["_issue_id"])


@InboxNotification.field("issue")
def inbox_issue(obj, info):
    return _issue_obj(info, obj["_issue_id"]) if obj.get("_issue_id") else None


def _inbox_subject(kind):
    def resolve(obj, info):
        sid = obj.get(f"{kind}_id")
        if not sid:
            return None
        store, _w, org, uid = _ctx(info)
        return _batched(info, f"inbox_{kind}", sid,
                        lambda ids: store.subjects_by_ids(org, kind, ids, viewer_id=uid),
                        lambda: store.subjects_by_ids(org, kind, [sid], viewer_id=uid).get(sid))
    return resolve


for _kind in ("project", "initiative", "cycle"):
    InboxNotification.set_field(_kind, _inbox_subject(_kind))


@InboxNotification.field("projectUpdate")
def inbox_project_update(obj, info):
    store, _w, org, _uid = _ctx(info)
    ref = obj.get("project_update_id")
    return store.project_update_by_id(org, ref) if ref else None


@InboxNotification.field("initiativeUpdate")
def inbox_initiative_update(obj, info):
    store, _w, org, _uid = _ctx(info)
    ref = obj.get("initiative_update_id")
    return store.initiative_update_by_id(org, ref) if ref else None


def _issue_obj(info, issue_id):
    store, _w, org, uid = _ctx(info)

    def load_many(ids):
        got = store.issues_by_ids(org, ids, viewer_id=uid)
        _prime_issue_nodes(info, list(got.values()))
        return got

    return _batched(info, "issue_obj", issue_id, load_many,
                    lambda: store.issue_by_id_or_identifier(org, issue_id, viewer_id=uid))


@InboxNotification.field("data")
def inbox_data(obj, _info):
    raw = obj.get("data")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@InboxNotification.field("comment")
def inbox_comment(obj, info):
    """The comment a comment or mention notification points at; null once deleted."""
    cid = obj.get("_comment_id")
    if not cid:
        return None
    store, _w, org, _uid = _ctx(info)
    return _batched(info, "inbox_comment", cid,
                    lambda ids: store.comments_by_ids(org, ids),
                    lambda: store.comments_by_ids(org, [cid]).get(cid))


@InboxNotification.field("actor")
def inbox_actor(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_actor_id"]), uid) if obj.get("_actor_id") else None


@query.field("viewer")
def r_viewer(_, info):
    store, _w, org, uid = _ctx(info)
    return store.viewer(org, uid)


@query.field("myApiKeys")
def r_my_api_keys(_, info):
    store, _w, org, uid = _ctx(info)
    return store.list_api_keys(org, uid)


@query.field("auditEvents")
def r_audit_events(_, info, first=50, after=None, actorId=None, action=None):
    """The organization's audit ledger, admin only, paged, never unbounded.

    An explicit admin check rather than a capability: reading the ledger is not
    a grant anyone hands out, and a non-admin is refused instead of being told
    the rows exist by an empty connection.
    """
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can read the audit log.",
                           extensions={"code": "FORBIDDEN"})
    return audit.read_events(store.engine, org, first=first,
                             after=int(after) if after not in (None, "") else None,
                             actor_id=actorId, action=action)


def _preference_payload(store, uid, *, success=True):
    values = store.user_preferences(uid)
    if values is None:
        return {"success": False, "theme": None, "fontSize": None}
    return {"success": success, "theme": values.get("theme", "light"),
            "fontSize": values.get("fontSize", "100")}


@query.field("myPreferences")
def r_my_preferences(_, info):
    store, _w, _org, uid = _ctx(info)
    return _preference_payload(store, uid)


def _notification_preferences(store, uid, success):
    """One boolean per kind; a kind with no stored preference is on."""
    from hoja_server.notifications import KINDS
    prefs = store.user_preferences(uid) or {}
    return {"success": success, **{kind: prefs.get(f"notification:{kind}", "true") == "true"
                                   for kind in KINDS}}


@query.field("myNotificationPreferences")
def r_my_notification_preferences(_, info):
    store, _w, _org, uid = _ctx(info)
    return _notification_preferences(store, uid, True)


@query.field("issueLabelSettings")
def r_issue_label_settings(_, info):
    store, _w, org, _uid = _ctx(info)
    return store.labels(org)


@query.field("myWorkspaces")
def r_my_workspaces(_, info):
    store, _w, org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    if not identity_id:
        membership = store.membership_for_profile(org, uid)
        identity_id = membership.get("identity_id") if membership else None
    if not identity_id:
        return []
    return store.identity_workspaces(identity_id)


@query.field("organizationMembers")
def r_organization_members(_, info, first=100):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn([store.ser_user(member, uid) for member in store.organization_members(org)[:first]])


@query.field("archivedMembers")
def r_archived_members(_, info):
    """Removed members, admin-only, so a removal can be audited and undone."""
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return []
    return [store.ser_user(row, uid) for row in store.archived_members(org)]


@query.field("workspace")
def r_workspace(_, info):
    store, _w, org, _uid = _ctx(info)
    return store.workspace(org)


@query.field("teams")
def r_teams(_, info, first=50, after=None, filter=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    key = None
    if filter and "key" in filter and "eq" in filter["key"]:
        key = filter["key"]["eq"]
    return _conn(store.teams(org, key=key, viewer_id=uid)[:first])


@query.field("team")
def r_team(_, info, id):
    store, _w, org, uid = _ctx(info)
    row = store.team_by_id_or_key(org, id, viewer_id=uid)
    if not row:
        return None
    return store.ser_team(row)


@query.field("issues")
def r_issues(_, info, filter=None, first=50, after=None, orderBy="updatedAt"):  # noqa: N803
    store, _w, org, uid = _ctx(info)
    order_by = orderBy or "updatedAt"
    if order_by not in ("updatedAt", "createdAt"):
        raise ValueError("Unsupported issue ordering.")
    first = _page_size(first)
    cursor = _decode_issue_cursor(after)
    rows = store.issues(org, uid, filter, order_by=order_by, after=cursor, limit=first + 1)
    nodes = rows[:first]
    _prime_issue_nodes(info, nodes)
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first,
        "hasPreviousPage": after is not None,
        "startCursor": _issue_cursor(nodes[0], order_by) if nodes else None,
        "endCursor": _issue_cursor(nodes[-1], order_by) if nodes else None,
    })
    return result


@query.field("issue")
def r_issue(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, id, viewer_id=uid)


@query.field("projects")
def r_projects(_, info, filter=None, first=50, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_project_update_cursor(after)
    rows = store.projects(org, filter, after=cursor, limit=first + 1, viewer_id=uid)
    nodes = rows[:first]
    _prime(info, "project_teams", [n["id"] for n in nodes])
    _prime(info, "project_progress", [n["id"] for n in nodes])
    _prime(info, "user_row", [n.get(k) for n in nodes for k in ("_lead_id", "_creator_id")])
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first,
        "hasPreviousPage": after is not None,
        "startCursor": _project_update_cursor({"created_at": nodes[0]["createdAt"], "id": nodes[0]["id"]}) if nodes else None,
        "endCursor": _project_update_cursor({"created_at": nodes[-1]["createdAt"], "id": nodes[-1]["id"]}) if nodes else None,
    })
    return result

@query.field("initiatives")
def r_initiatives(_, info, first=50, after=None, status=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_project_update_cursor(after, "initiative")
    rows = store.initiatives(org, after=cursor, status=status, limit=first + 1, viewer_id=uid)
    nodes = rows[:first]
    result = _conn(nodes)
    result["pageInfo"].update({"hasNextPage": len(rows) > first, "hasPreviousPage": after is not None, "startCursor": _project_update_cursor({"created_at": nodes[0]["createdAt"], "id": nodes[0]["id"]}) if nodes else None, "endCursor": _project_update_cursor({"created_at": nodes[-1]["createdAt"], "id": nodes[-1]["id"]}) if nodes else None})
    return result

@query.field("initiative")
def r_initiative(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.initiative_by_id(org, id, viewer_id=uid)


@query.field("projectUpdates")
def r_project_updates(_, info, first=50, after=None, filter=None):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    project_filter = (filter or {}).get("project") or {}
    project_id = (project_filter.get("id") or {}).get("eq")
    cursor = _decode_project_update_cursor(after)
    rows = store.project_updates(org, after=cursor, project_id=project_id, limit=first + 1)
    nodes = rows[:first]
    _prime(info, "project", [n.get("_project_id") for n in nodes])
    _prime(info, "user_row", [n.get("_actor_id") for n in nodes])
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _project_update_cursor(nodes[0]) if nodes else None,
        "endCursor": _project_update_cursor(nodes[-1]) if nodes else None,
    })
    return result


@query.field("initiativeUpdates")
def r_initiative_updates(_, info, first=50, after=None, filter=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    initiative_filter = (filter or {}).get("initiative") or {}
    initiative_id = (initiative_filter.get("id") or {}).get("eq")
    cursor = _decode_project_update_cursor(after, "initiative update")
    rows = store.initiative_updates(org, after=cursor, initiative_id=initiative_id, limit=first + 1)
    # Updates of an initiative led by a private team the viewer is not on stay hidden.
    visible = {i["id"] for i in store.initiatives(org, limit=100000, viewer_id=uid)}
    rows = [r for r in rows if r.get("initiative_id", r.get("_initiative_id")) in visible]
    nodes = rows[:first]
    _prime(info, "user_row", [n.get("_actor_id") for n in nodes])
    result = _conn(nodes)
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _project_update_cursor(nodes[0]) if nodes else None,
        "endCursor": _project_update_cursor(nodes[-1]) if nodes else None,
    })
    return result


@query.field("project")
def r_project(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.project_by_id(org, id, viewer_id=uid)


@query.field("workflowStates")
def r_workflow_states(_, info, filter=None, first=100, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn(store.workflow_states(org, filter, viewer_id=uid)[:first])


@query.field("cycles")
def r_cycles(_, info, filter=None, first=100, after=None):
    store, writer, org, uid = _ctx(info)
    first = _page_size(first, 100)
    if not _read_only(info) and filter and "team" in filter and "id" in filter["team"] and "eq" in filter["team"]["id"]:
        writer.reconcile_team_cadence(org, filter["team"]["id"]["eq"], viewer_id=uid)
    return _conn(store.cycles(org, filter, viewer_id=uid)[:first])


@query.field("teamCycleCadence")
def r_team_cycle_cadence(_, info, teamId):
    store, writer, org, uid = _ctx(info)
    if _read_only(info):
        return store.team_cadence_snapshot(org, teamId, uid)
    return writer.reconcile_team_cadence(org, teamId, viewer_id=uid)


@mutation.field("teamCycleCadenceEnable")
def m_team_cycle_cadence_enable(_, info, teamId, weekday):
    store, writer, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise ValueError("Only workspace admins can enable team cycles.")
    return writer.enable_team_cadence(org, teamId, weekday)


@query.field("cycle")
def r_cycle(_, info, id):
    store, _w, org, uid = _ctx(info)
    return store.cycle_by_id(org, id, viewer_id=uid)


@query.field("issueLabels")
def r_labels(_, info, filter=None, first=100, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    team_id = None
    name = None
    if filter:
        if "team" in filter and "id" in filter["team"] and "eq" in filter["team"]["id"]:
            team_id = filter["team"]["id"]["eq"]
        if "name" in filter and "eq" in filter["name"]:
            name = filter["name"]["eq"]
    return _conn(store.labels(org, team_id=team_id, name=name, viewer_id=uid)[:first])


@query.field("searchIssues")
def r_search(_, info, term, first=20, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 20)
    cursor = _decode_issue_cursor(after)
    rows = store.search_issues(org, term, viewer_id=uid, after=cursor, limit=first + 1)
    nodes = rows[:first]
    _prime_issue_nodes(info, nodes)
    result = _conn(nodes)
    # hasNextPage is only honest if the cursor it ships can be passed back as
    # `after`; agent_chat.py tells the reader to do exactly that.
    result["pageInfo"].update({
        "hasNextPage": len(rows) > first,
        "hasPreviousPage": after is not None,
        "startCursor": _issue_cursor(nodes[0], "updatedAt") if nodes else None,
        "endCursor": _issue_cursor(nodes[-1], "updatedAt") if nodes else None,
    })
    return result


@query.field("rateLimitStatus")
def r_rate_limit(_, info):
    resets = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return {
        "identifier": "local", "kind": "unlimited",
        "limit": 1000000, "remaining": 1000000, "requestsPerHour": 1000000,
        "resetsAt": resets,
    }


# ----------------------------------------------------------------- Team fields
@Team.field("states")
def t_states(obj, info, first=100):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn(store.team_states(org, obj["id"], viewer_id=uid)[:first])


@Team.field("members")
def t_members(obj, info, first=100):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn(store.team_members(org, obj["id"], viewer_id=uid)[:first])


@Team.field("parent")
def t_parent(obj, info):
    store, _w, org, uid = _ctx(info)
    parent_id = obj.get("parentTeamId")
    if not parent_id:
        return None
    row = store.team_by_id_or_key(org, parent_id, viewer_id=uid)
    return store.ser_team(row) if row else None


@Team.field("children")
def t_children(obj, info, first=100):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn(store.team_children(org, obj["id"], viewer_id=uid)[:first])


@Team.field("cycles")
def t_cycles(obj, info, first=50):
    store, writer, org, uid = _ctx(info)
    first = _page_size(first)
    if not _read_only(info):
        writer.reconcile_team_cadence(org, obj["id"], viewer_id=uid)
    return _conn(store.team_cycles(org, obj["id"], viewer_id=uid)[:first])


@Team.field("activeCycle")
def t_active_cycle(obj, info):
    store, writer, org, uid = _ctx(info)
    cid = obj.get("_active_cycle_id")
    if not cid:
        return None
    if _read_only(info):
        status = store.team_cadence_snapshot(org, obj["id"], uid)
    else:
        status = writer.reconcile_team_cadence(org, obj["id"], viewer_id=uid)
    return status["activeCycle"] if status["enabled"] else next((c for c in store.team_cycles(org, obj["id"], viewer_id=uid) if c["id"] == cid), None)


# ----------------------------------------------------------------- State/cycle fields
@WorkflowState.field("team")
@Cycle.field("team")
def reference_team(obj, info):
    store, _w, org, uid = _ctx(info)
    tid = obj.get("_team_id")
    if not tid:
        return None
    return _memo(info, "team", (org, uid, tid),
                 lambda: store.ser_team(store.raw_team(org, tid, viewer_id=uid)))


# ----------------------------------------------------------------- Issue fields
@Issue.field("state")
def i_state(obj, info):
    store, _w, org, _uid = _ctx(info)
    state_id = obj.get("_state_id")
    if not state_id:
        return None
    return _memo(info, "state", (org, state_id),
                 lambda: _batched(info, "state", state_id,
                                  lambda ids: store.states_by_ids(org, ids),
                                  lambda: store.state_by_id(org, state_id)))


@Issue.field("assignee")
def i_assignee(obj, info):
    store, _w, org, uid = _ctx(info)
    return _memo(info, "user", (org, obj["_assignee_id"]),
                 lambda: store.ser_user(_user_row(info, obj["_assignee_id"]), uid)) if obj.get("_assignee_id") else None


@Issue.field("creator")
def i_creator(obj, info):
    store, _w, org, uid = _ctx(info)
    return _memo(info, "user", (org, obj["_creator_id"]),
                 lambda: store.ser_user(_user_row(info, obj["_creator_id"]), uid)) if obj.get("_creator_id") else None


@Issue.field("delegate")
def i_delegate(obj, info):
    store, _w, org, uid = _ctx(info)
    return _memo(info, "user", (org, obj["_delegate_id"]),
                 lambda: store.ser_user(_user_row(info, obj["_delegate_id"]), uid)) if obj.get("_delegate_id") else None


@Issue.field("delegateId")
def i_delegate_id(obj, info):
    return obj.get("_delegate_id")


@Issue.field("team")
def i_team(obj, info):
    store, _w, org, uid = _ctx(info)
    return _memo(info, "team", (org, uid, obj["_team_id"]), lambda: (
        (lambda row: store.ser_team(row) if row else None)(
            store.raw_team(org, obj["_team_id"], viewer_id=uid))
    )) if obj.get("_team_id") else None


@Issue.field("project")
def i_project(obj, info):
    store, _w, org, uid = _ctx(info)
    pid = obj.get("_project_id")
    return _batched(info, "project", pid, lambda ids: store.projects_by_ids(org, ids, viewer_id=uid),
                    lambda: store.project_by_id(org, pid, viewer_id=uid)) if pid else None


@Issue.field("cycle")
def i_cycle(obj, info):
    store, _w, org, uid = _ctx(info)
    if not obj.get("_cycle_id"):
        return None
    def _load():
        with store.engine.connect() as conn:
            from sqlalchemy import and_, select

            from hoja_server.db import cycle as cyc
            r = conn.execute(select(cyc).where(and_(cyc.c.organization_id == org, cyc.c.id == obj["_cycle_id"]))).first()
            if not r:
                return None
            if not store.team_visible_for(org, uid, r._mapping["team_id"]):
                return None
            return store.ser_cycle(dict(r._mapping))
    return _memo(info, "cycle", (org, uid, obj["_cycle_id"]), _load)


@Issue.field("parent")
def i_parent(obj, info):
    store, _w, org, uid = _ctx(info)
    return _memo(info, "issue", (org, uid, obj["_parent_id"]),
                 lambda: store.issue_by_id_or_identifier(org, obj["_parent_id"], viewer_id=uid)) if obj.get("_parent_id") else None


@Issue.field("children")
def i_children(obj, info, first=50, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_issue_cursor(after)
    return _memo(
        info, "issue_children", (org, uid, obj["id"], first, cursor), lambda: _children_conn(
            store, org, uid, obj["id"], first, cursor,
        ),
    )


def _children_conn(store, org, uid, issue_id, first, cursor):
    rows = store.issue_children(org, issue_id, viewer_id=uid, after=cursor, limit=first + 1)
    nodes = rows[:first]
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": len(rows) > first,
            "hasPreviousPage": cursor is not None,
            "startCursor": _issue_cursor(nodes[0], "createdAt") if nodes else None,
            "endCursor": _issue_cursor(nodes[-1], "createdAt") if nodes else None,
        },
    }


@Issue.field("labels")
def i_labels(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    labels = _batched(info, "issue_labels", obj["id"], lambda ids: store.issue_labels_for_many(org, ids),
                      lambda: store.issue_labels_for(org, obj["id"]), default=[])
    return _conn(labels[:first])


@Issue.field("subscribers")
def i_subscribers(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    subs = _batched(info, "issue_subscribers", obj["id"],
                    lambda ids: store.issue_subscribers_for_many(org, ids),
                    lambda: store.issue_subscribers_for(org, obj["id"]), default=[])
    return _conn(subs[:first])


@Issue.field("comments")
def i_comments(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    def load():
        comments = store.comments_for(org, obj["id"], limit=first)
        _prime(info, "user_row", [c.get("_user_id") for c in comments])
        return _conn(comments)
    return _memo(info, "issue_comments", (org, obj["id"], first), load)


@Issue.field("attachments")
def i_attachments(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    return _memo(info, "issue_attachments", (org, obj["id"], first),
                 lambda: _conn(store.issue_attachments_for(org, obj["id"], limit=first)))


@Issue.field("githubLinks")
def i_github_links(obj, info):
    """The GitHub objects linked to this issue, newest first.

    Not paginated: the panel shows the handful of PRs, commits and deploys that name
    the issue, and a cap keeps a pathological history from bloating one page.
    """
    store, _w, org, _uid = _ctx(info)
    return _memo(info, "issue_github_links", (org, obj["id"]),
                 lambda: store.issue_github_links_for(org, obj["id"], limit=50))


@query.field("myIssueActivity")
def r_my_issue_activity(_, info, first=50, after=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_activity_cursor(after)
    rows = store.my_issue_activity(org, uid, cursor, first + 1)
    nodes = [dict(row, eventType=row["event_type"], createdAt=row["created_at"]) for row in rows[:first]]
    _prime(info, "issue_obj", [n["issue_id"] for n in nodes])
    _prime(info, "user_row", [n.get("actor_id") for n in nodes])
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@MyIssueActivity.field("issue")
def mia_issue(obj, info):
    return _issue_obj(info, obj["issue_id"])


@MyIssueActivity.field("actor")
def mia_actor(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["actor_id"]), uid) if obj.get("actor_id") else None


@Issue.field("activity")
def i_activity(obj, info, first=50, after=None):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_activity_cursor(after)
    rows = store.issue_activity_for(org, obj["id"], cursor, first + 1)
    nodes = [dict(row, eventType=row["event_type"], createdAt=row["created_at"]) for row in rows[:first]]
    _prime(info, "user_row", [n.get("actor_id") for n in nodes])
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@IssueActivity.field("actor")
def ia_actor(obj, info):
    store, _w, _org, _uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["actor_id"]), obj["actor_id"]) if obj.get("actor_id") else None


# ----------------------------------------------------------------- Project fields
@Project.field("lead")
def p_lead(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_lead_id"]), uid) if obj.get("_lead_id") else None


@ProjectUpdate.field("comments")
def p_comments(obj, info, first=50):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first)
    def load():
        # The newest window, reversed: the badge is a count of recent replies, and the
        # list still reads oldest-first like every other thread in the app.
        comments = store.comments_for_update(org, obj["id"], limit=first, newest_first=True)
        _prime(info, "user_row", [c.get("_user_id") for c in comments])
        return _conn(comments)
    return _memo(info, "project_update_comments", (org, obj["id"], first), load)


@ProjectUpdate.field("project")
def pu_project(obj, info):
    store, _w, org, _uid = _ctx(info)
    pid = obj["_project_id"]
    return _batched(info, "project", pid, lambda ids: store.projects_by_ids(org, ids),
                    lambda: store.project_update_project(org, pid))


@ProjectUpdate.field("actor")
def pu_actor(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_actor_id"]), uid) if obj.get("_actor_id") else None


@InitiativeUpdate.field("initiative")
def iu_initiative(obj, info):
    store, _w, org, _uid = _ctx(info)
    return store.initiative_by_id(org, obj["_initiative_id"])


@InitiativeUpdate.field("actor")
def iu_actor(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_actor_id"]), uid) if obj.get("_actor_id") else None


@Project.field("creator")
def p_creator(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_creator_id"]), uid) if obj.get("_creator_id") else None


@Project.field("members")
def p_members(obj, info, first=100):
    store, _w, org, _uid = _ctx(info)
    first = _page_size(first, 100)
    return _conn(store.project_members(org, obj["id"])[:first])


@Project.field("activity")
def p_activity(obj, info, first=50, after=None, eventTypes=None):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first)
    cursor = _decode_activity_cursor(after)
    rows = store.project_issue_activity(org, obj["id"], uid, cursor, first + 1, eventTypes or None)
    nodes = [dict(row, eventType=row["event_type"], createdAt=row["created_at"]) for row in rows[:first]]
    _prime(info, "issue_obj", [n["issue_id"] for n in nodes])
    _prime(info, "user_row", [n.get("actor_id") for n in nodes])
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": len(rows) > first, "hasPreviousPage": after is not None,
        "startCursor": _activity_cursor(nodes[0]) if nodes else None,
        "endCursor": _activity_cursor(nodes[-1]) if nodes else None,
    }}


@Project.field("progress")
def p_progress(obj, info):
    store, _w, org, _uid = _ctx(info)
    pid = obj["id"]
    return _batched(info, "project_progress", pid, lambda ids: store.project_progress_many(org, ids),
                    lambda: store.project_progress_many(org, [pid])[pid], default=0.0)


@Project.field("teams")
def p_teams(obj, info, first=100):
    store, _w, org, uid = _ctx(info)
    first = _page_size(first, 100)
    teams = _batched(info, "project_teams", obj["id"],
                     lambda ids: store.project_teams_many(org, ids, viewer_id=uid),
                     lambda: store.project_teams(org, obj["id"], viewer_id=uid), default=[])
    return _conn(teams[:first])


# ----------------------------------------------------------------- Comment fields
@Comment.field("user")
def c_user(obj, info):
    store, _w, _org, uid = _ctx(info)
    return store.ser_user(_user_row(info, obj["_user_id"]), uid) if obj.get("_user_id") else None


def _comment_can(obj, info, action) -> bool:
    from hoja_server import comment_policy
    store, _w, org, uid = _ctx(info)
    subject = info.context.get("_comment_subject")
    if subject is None:
        with store.engine.connect() as conn:
            subject = info.context["_comment_subject"] = comment_policy.subject(conn, org, uid)
    # The comment was readable, so its team is visible to this viewer.
    resource = {"author_id": obj.get("_user_id"), "archived": obj.get("_archived"), "team_visible": True}
    return comment_policy.decide(action, subject, resource).allowed


@Comment.field("canEdit")
def c_can_edit(obj, info):
    from hoja_server.comment_policy import EDIT
    return _comment_can(obj, info, EDIT)


@Comment.field("canDelete")
def c_can_delete(obj, info):
    from hoja_server.comment_policy import DELETE
    return _comment_can(obj, info, DELETE)


# ----------------------------------------------------------------- Mutations
@mutation.field("workspaceCreate")
def m_workspace_create(_, info, input):
    if single_org_mode():
        raise GraphQLError(
            "Creating a workspace is unavailable: this instance runs in single-organization mode.",
            extensions={"code": "SINGLE_ORG_MODE", "field": "input"})
    store, writer, org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    if not identity_id:
        membership = store.membership_for_profile(org, uid)
        identity_id = membership.get("identity_id") if membership else None
    if not identity_id:
        raise ValueError("Workspace identity is unavailable")
    if info.context.get("pinned_org"):
        # A workspace with its own address accepts only its own sessions, so a
        # workspace created from here could never be switched into on this host.
        raise GraphQLError(
            "Create new workspaces from the Hoja sign-up page, not from inside a workspace "
            "that has its own address.",
            extensions={"code": "WORKSPACE_HOST_PINNED", "field": "input"})
    created = writer.workspace_create(org, uid, input)
    created.pop("_identity_id", None)
    info.context["switch_target"] = {
        "user_id": created.pop("_profile_user_id"),
        "identity_id": identity_id,
        "organization_id": created["id"],
    }
    return {"success": True, "organization": created}

@mutation.field("teamCreate")
def m_team_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    tid = writer.team_create(org, uid, input)
    row = store.team_by_id_or_key(org, tid, viewer_id=uid)
    return {"success": True, "team": store.ser_team(row)}


@mutation.field("teamUpdate")
def m_team_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    tid = writer.team_update(org, uid, id, input)
    return {"success": True, "team": store.ser_team(store.team_by_id_or_key(org, tid))}


@mutation.field("teamArchive")
def m_team_archive(_, info, id):
    _s, writer, org, uid = _ctx(info)
    return {"success": writer.team_archive(org, uid, id)}


@mutation.field("teamDelete")
def m_team_delete(_, info, id, deleteIssues=False):
    _s, writer, org, uid = _ctx(info)
    return {"success": writer.team_delete(org, uid, id, delete_issues=bool(deleteIssues))}


@mutation.field("workspaceUpdate")
def m_workspace_update(_, info, input):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "organization": None}
    updated = store.update_workspace(org, input)
    return {"success": bool(updated), "organization": updated}


@mutation.field("apiKeyCreate")
def m_api_key_create(_, info, label, access="read_write", expiresAt=None):
    store, _w, org, uid = _ctx(info)
    created = store.create_api_key(org, uid, label, access, expiresAt)
    if not created:
        return {"success": False, "apiKey": None, "secret": None}
    metadata, secret = created
    # Audit after the key exists; never the secret, only what the admin chose.
    audit.record_request(store, info, user_id=uid, action="api_key.create",
                         target_kind="api_key", target_id=metadata["id"],
                         detail={"label": metadata["label"], "access": metadata["access"]})
    return {"success": True, "apiKey": metadata, "secret": secret}


@mutation.field("apiKeyRevoke")
def m_api_key_revoke(_, info, id):
    store, _w, org, uid = _ctx(info)
    revoked = store.revoke_api_key(org, uid, id)
    if revoked:
        audit.record_request(store, info, user_id=uid, action="api_key.revoke",
                             target_kind="api_key", target_id=id)
    return revoked


@mutation.field("userPreferenceSet")
def m_user_preference_set(_, info, key, value):
    store, _w, _org, uid = _ctx(info)
    success = store.set_user_preference(uid, key, value)
    return _preference_payload(store, uid, success=success)


@mutation.field("profileNameUpdate")
def m_profile_name_update(_, info, name):
    store, _w, org, uid = _ctx(info)
    updated = store.update_profile_name(org, uid, name)
    return {"success": bool(updated), "user": updated, "apiKey": None}


@mutation.field("notificationPreferenceSet")
def m_notification_preference_set(_, info, kind, enabled):
    store, _w, _org, uid = _ctx(info)
    success = store.set_notification_preference(uid, kind, enabled)
    return _notification_preferences(store, uid, success)


@mutation.field("workspaceSwitch")
def m_workspace_switch(_, info, organizationId):
    if single_org_mode():
        raise GraphQLError(
            "Switching workspace is unavailable: this instance runs in single-organization mode.",
            extensions={"code": "SINGLE_ORG_MODE", "field": "organizationId"})
    store, _w, org, uid = _ctx(info)
    if not info.context.get("browser_session"):
        return {"success": False, "organization": None}
    member = store.membership_for_profile(org, uid)
    target = store.switch_membership(member.get("identity_id") if member else None, organizationId)
    if not target:
        return {"success": False, "organization": None}
    # A workspace that lives on another host is opened there: the session here is
    # a host-only cookie, and a pinned host refuses any other organization's.
    redirect = (info.context.get("org_redirect") or (lambda _org: None))(target["organization_id"])
    if redirect:
        return {"success": True, "organization": target["organization"], "redirectUrl": redirect}
    info.context["switch_target"] = target
    return {"success": True, "organization": target["organization"]}


@mutation.field("membershipInvitationCreate")
def m_membership_invitation_create(_, info, userId):
    store, _w, org, uid = _ctx(info)
    target = store.get_user(org, userId)
    if not permissions.can(store, org, uid, "member.invite") or not target:
        return {"success": False, "user": None, "inviteCode": None, "emailQueued": False}
    code = store.create_membership_invitation(org, userId, uid)
    email_queued = False
    if code and target.get("email"):
        workspace = store.workspace(org) or {}
        # Delivery is best-effort on purpose: the invite row is already committed,
        # and the caller still gets the link to share if sending fails.
        inviter = store.get_user(org, uid) or {}
        delivery = mail_events.send_invite(
            store.engine,
            organization_id=org,
            organization_name=workspace.get("name") or "your workspace",
            inviter_name=inviter.get("name") or "",
            invitee_name=target.get("name") or "",
            invitee_email=target.get("email"),
            code=code,
            # A hosted workspace invites onto its own host: the member who
            # accepts must land in the workspace that invited them.
            workspace_url=store.workspace_origin(org),
        )
        email_queued = delivery == "sent"
        if not email_queued:
            logger.info("mail.invite_not_sent reason=%s", delivery)
        # Deliberately NOT announced here. An invitation is an offer that names
        # an address; it proves nothing about who controls it, and a hint built
        # from an unverified claim would let one tenant route a stranger's
        # sign-in to itself. The announcement happens when the address is
        # proven: on acceptance, or on a Google sign-in to this workspace.
    return {"success": bool(code), "user": store.ser_user(store.get_user(org, userId), uid),
            "inviteCode": code, "emailQueued": email_queued}


@query.field("pendingMembershipInvitations")
def q_pending_membership_invitations(_, info):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return []
    return store.pending_membership_invitations(org)


@mutation.field("membershipInvitationRevoke")
def m_membership_invitation_revoke(_, info, userId):
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False}
    return {"success": store.revoke_membership_invitation(org, userId)}


@mutation.field("memberDeactivate")
def m_member_deactivate(_, info, userId):
    return _set_member_active(info, userId, active=False)


@mutation.field("memberReactivate")
def m_member_reactivate(_, info, userId):
    return _set_member_active(info, userId, active=True)


@mutation.field("memberArchive")
def m_member_archive(_, info, userId):
    """Remove a member: archived, sessions and keys ended, re-invitable."""
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False, "user": None}
    target = store.archive_member(org, uid, userId)
    if target:
        audit.record_request(store, info, user_id=uid, action="member.archive",
                             target_kind="user", target_id=userId)
    return {"success": bool(target), "user": store.ser_user(target, uid)}


@mutation.field("memberRestore")
def m_member_restore(_, info, userId):
    """Undo a removal without making the admin re-invite the person."""
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False, "user": None}
    target = store.restore_member(org, uid, userId)
    if target:
        audit.record_request(store, info, user_id=uid, action="member.restore",
                             target_kind="user", target_id=userId)
    return {"success": bool(target), "user": store.ser_user(target, uid)}


def _set_member_active(info, user_id, *, active: bool):
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False, "user": None}
    target = store.set_member_active(org, uid, user_id, active)
    if target:
        audit.record_request(
            store, info, user_id=uid,
            action="member.reactivate" if active else "member.deactivate",
            target_kind="user", target_id=user_id, detail={"active": bool(active)})
    return {"success": bool(target), "user": store.ser_user(target, uid)}


@mutation.field("membershipInvitationAccept")
def m_membership_invitation_accept(_, info, code, inviteePassword):
    store, _w, _org, uid = _ctx(info)
    identity_id = info.context.get("identity_id")
    organization = store.accept_membership_invitation(identity_id, code, inviteePassword) if identity_id else None
    return {"success": bool(organization), "organization": organization}


@mutation.field("memberRoleUpdate")
def m_member_role_update(_, info, userId, admin):
    store, _w, org, uid = _ctx(info)
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        return {"success": False, "user": None}
    target = store.set_member_admin_role(org, userId, admin)
    if target:
        audit.record_request(store, info, user_id=uid, action="member.role.change",
                             target_kind="user", target_id=userId,
                             detail={"admin": bool(admin)})
    return {"success": bool(target), "user": store.ser_user(target, uid)}


@mutation.field("memberInvite")
def m_member_invite(_, info, input):
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False, "user": None, "apiKey": None}
    team_ids = input.get("teamIds") or []
    for team_id in team_ids:
        if not store.team_by_id_or_key(org, team_id, viewer_id=uid):
            raise GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT", "field": "teamIds"})
    try:
        created = store.invite_member(org, input.get("name", ""), input.get("email", ""),
                                      bool(input.get("admin", False)), team_ids)
    except store_module.InviteRefused as refused:
        # A stable reason so the screen can offer the one action that fixes it
        # instead of four guesses behind a single "invite failed".
        raise GraphQLError(refused.message, extensions={
            "code": "INVITE_REFUSED", "reason": refused.reason}) from refused
    if not created:
        return {"success": False, "user": None, "apiKey": None}
    user_row, api_key = created
    audit.record_request(store, info, user_id=uid, action="member.invite",
                         target_kind="user",
                         target_id=user_row["id"] if user_row is not None else None,
                         detail={"admin": bool(input.get("admin", False)),
                                 "teamIds": list(team_ids)})
    return {"success": True, "user": store.ser_user(user_row, uid), "apiKey": api_key}


def _set_team_membership(info, team_id, user_id, *, add: bool, role: str = "member"):
    store, _w, org, uid = _ctx(info)
    if not permissions.can(store, org, uid, "member.invite"):
        return {"success": False, "user": None, "apiKey": None}
    if not store.team_by_id_or_key(org, team_id, viewer_id=uid):
        raise GraphQLError("Team not found", extensions={"code": "BAD_USER_INPUT", "field": "teamId"})
    target = store.get_user(org, user_id)
    if not target:
        raise GraphQLError("User not found", extensions={"code": "BAD_USER_INPUT", "field": "userId"})
    store.set_team_membership(org, team_id, user_id, add=add, role=role)
    return {"success": True, "user": store.ser_user(store.get_user(org, user_id), uid), "apiKey": None}


@mutation.field("teamMemberAdd")
def m_team_member_add(_, info, teamId, userId, role="member"):
    return _set_team_membership(info, teamId, userId, add=True, role=role)


@mutation.field("teamMemberRemove")
def m_team_member_remove(_, info, teamId, userId):
    return _set_team_membership(info, teamId, userId, add=False)


def _extension_hooks(info, store, org, uid, event, input, current, input_type):
    """Run extension hooks; a veto becomes an EXTENSION_REJECTED error, nothing is written."""
    try:
        return extensions.run_hooks(store.engine, org, uid, event, input, current,
                                    info.schema.get_type(input_type))
    except extensions.ExtensionRejectedError as exc:
        raise GraphQLError(str(exc), extensions={
            "code": "EXTENSION_REJECTED", "extension": exc.extension_name, "reason": exc.reason}) from exc


@mutation.field("issueCreate")
def m_issue_create(_, info, input):
    store, w, org, uid = _ctx(info)
    # A templated create may carry no teamId until the template supplies one;
    # resolve the team the write will target so the extension gate still runs
    # (and still refuses) for a team the caller cannot see.
    team_ref = input.get("teamId")
    if not team_ref and input.get("templateId"):
        from hoja_server import task_templates
        team_ref = task_templates.team_id_for(store.engine, org, input["templateId"])
    if store.team_by_id_or_key(org, str(team_ref or ""), viewer_id=uid):
        # Only ask extensions about a write the caller is allowed to attempt.
        input, patches = _extension_hooks(info, store, org, uid, "issue.beforeCreate", input, None,
                                          "IssueCreateInput")
    else:
        patches = []
    iid = w.issue_create(org, uid, input)
    if not iid:
        return {"success": False, "issue": None}
    extensions.record_patches(store.engine, org, iid, patches)
    return {"success": True, "issue": store.issue_by_id_or_identifier(org, iid, viewer_id=uid)}


@mutation.field("issueUpdate")
def m_issue_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    current = store.issue_by_id_or_identifier(org, id, viewer_id=uid)
    if current is None:  # do not ask extensions about an issue the caller cannot see
        return {"success": False, "issue": None}
    input, patches = _extension_hooks(info, store, org, uid, "issue.beforeUpdate", input,
                                      extensions.issue_data(store.engine, org, current["id"]),
                                      "IssueUpdateInput")
    iid = w.issue_update(org, id, input, actor_id=uid)
    if not iid:
        return {"success": False, "issue": None}
    extensions.record_patches(store.engine, org, iid, patches)
    return {"success": True, "issue": store.issue_by_id_or_identifier(org, iid, viewer_id=uid)}


@mutation.field("issueDelete")
def m_issue_delete(_, info, id):
    # Authorization lives in writer._authorize_issue_change: creator, admin, or a
    # holder of task.delete. Demanding the capability here would take away the
    # creator's own right to delete their task.
    _s, w, org, uid = _ctx(info)
    return {"success": w.issue_delete(org, uid, id)}


@mutation.field("issueArchive")
def m_issue_archive(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.issue_archive(org, uid, id)}


@mutation.field("commentCreate")
def m_comment_create(_, info, input):
    store, w, org, uid = _ctx(info)
    cid = w.comment_create(org, uid, input)
    if not cid:
        return {"success": False, "comment": None}
    return {"success": True, "comment": _get_comment(store, org, cid)}


@mutation.field("commentUpdate")
def m_comment_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    cid = w.comment_update(org, uid, id, input)
    if not cid:
        return {"success": False, "comment": None}
    return {"success": True, "comment": _get_comment(store, org, cid)}


@mutation.field("commentDelete")
def m_comment_delete(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.comment_delete(org, uid, id)}


@mutation.field("issueLabelCreate")
def m_label_create(_, info, input):
    store, w, org, uid = _ctx(info)
    permissions.require(store, org, uid, "label.manage")
    name = str(input.get("name", "")).strip()
    if not name or len(name) > 64:
        return {"success": False, "issueLabel": None}
    input = {**input, "name": name}
    lid = w.label_create(org, input)
    labels = store.labels(org)
    node = next((label for label in labels if label["id"] == lid), None)
    return {"success": bool(lid), "issueLabel": node}


@mutation.field("issueLabelUpdate")
def m_label_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    permissions.require(store, org, uid, "label.manage")
    if not w.label_update(org, uid, id, input):
        return {"success": False, "issueLabel": None}
    node = next((label for label in store.labels(org) if label["id"] == id), None)
    return {"success": True, "issueLabel": node}


@mutation.field("issueLabelDelete")
def m_label_delete(_, info, id):
    store, w, org, uid = _ctx(info)
    permissions.require(store, org, uid, "label.manage")
    return {"success": w.label_delete(org, id)}


@mutation.field("projectCreate")
def m_project_create(_, info, input):
    store, w, org, uid = _ctx(info)
    pid = w.project_create(org, uid, input)
    return {"success": bool(pid), "project": store.project_by_id(org, pid, viewer_id=uid) if pid else None}


@mutation.field("projectUpdate")
def m_project_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    pid = w.project_update(org, uid, id, input)
    return {"success": bool(pid), "project": store.project_by_id(org, pid, viewer_id=uid) if pid else None}


@mutation.field("projectUpdateCreate")
def m_project_update_create(_, info, input):
    store, w, org, uid = _ctx(info)
    update_id = w.project_update_create(org, input["projectId"], uid, input)
    rows = store.project_updates(org, project_id=input["projectId"], limit=1) if update_id else []
    created = next((row for row in rows if row["id"] == update_id), None)
    return {"success": bool(created), "projectUpdate": created}


@mutation.field("projectUpdateUpdate")
def m_project_update_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    update_id = w.project_update_update(org, uid, id, input)
    return {"success": bool(update_id), "projectUpdate": store.project_update_by_id(org, update_id) if update_id else None}


@mutation.field("projectUpdateDelete")
def m_project_update_delete(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.project_update_delete(org, uid, id)}


@mutation.field("initiativeUpdateCreate")
def m_initiative_update_create(_, info, input):
    store, w, org, uid = _ctx(info)
    update_id = w.initiative_update_create(org, input["initiativeId"], uid, input)
    return {"success": bool(update_id), "initiativeUpdate": store.initiative_update_by_id(org, update_id) if update_id else None}


@mutation.field("initiativeUpdateUpdate")
def m_initiative_update_update(_, info, id, input):
    store, w, org, uid = _ctx(info)
    update_id = w.initiative_update_update(org, uid, id, input)
    return {"success": bool(update_id), "initiativeUpdate": store.initiative_update_by_id(org, update_id) if update_id else None}


@mutation.field("initiativeUpdateDelete")
def m_initiative_update_delete(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.initiative_update_delete(org, uid, id)}


@mutation.field("projectArchive")
def m_project_archive(_, info, id):
    _s, w, org, uid = _ctx(info)
    return {"success": w.project_archive(org, uid, id)}

@mutation.field("initiativeCreate")
def m_initiative_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    ident = writer.initiative_create(org, uid, input)
    return {"success": True, "initiative": store.initiative_by_id(org, ident)}

@mutation.field("initiativeUpdate")
def m_initiative_update(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    success = writer.initiative_update(org, uid, id, input)
    return {"success": success, "initiative": store.initiative_by_id(org, id) if success else None}

@mutation.field("initiativeArchive")
def m_initiative_archive(_, info, id):
    _store, writer, org, uid = _ctx(info)
    return {"success": writer.initiative_archive(org, uid, id)}


@mutation.field("attachmentCreate")
def m_attachment_create(_, info, input):
    _store, w, org, uid = _ctx(info)
    created = w.attachment_create(org, uid, input)
    return {"success": bool(created), "attachment": created}


def _subject_subscribers(kind):
    def resolve(obj, info, first=50):
        store, _w, org, _uid = _ctx(info)
        subs = _batched(info, f"{kind}_subscribers", obj["id"],
                        lambda ids: store.subject_subscribers_for_many(org, kind, ids),
                        lambda: store.subject_subscribers_for_many(org, kind, [obj["id"]])
                        .get(obj["id"], []), default=[])
        return _conn(subs[:_page_size(first)])
    return resolve


def _viewer_subscribed(kind):
    def resolve(obj, info):
        store, _w, org, uid = _ctx(info)
        subs = _batched(info, f"{kind}_subscribers", obj["id"],
                        lambda ids: store.subject_subscribers_for_many(org, kind, ids),
                        lambda: store.subject_subscribers_for_many(org, kind, [obj["id"]])
                        .get(obj["id"], []), default=[])
        return any(u and u.get("id") == uid for u in subs)
    return resolve


for _kind, _type in (("project", Project), ("initiative", Initiative), ("cycle", Cycle)):
    _type.set_field("subscribers", _subject_subscribers(_kind))
    _type.set_field("viewerSubscribed", _viewer_subscribed(_kind))


def _follow(kind, subscribed):
    def resolve(_, info, id):
        store, w, org, uid = _ctx(info)
        # Resolve through the same reads that decide what the viewer may see, so an
        # id from a private team behaves exactly like an id that does not exist.
        if kind == "project":
            found = store.project_by_id(org, id, viewer_id=uid) or store.project_by_slug(org, id, viewer_id=uid)
        elif kind == "initiative":
            found = store.initiative_by_id(org, id, viewer_id=uid)
        else:
            found = store.cycle_by_id(org, id, viewer_id=uid)
        if not found:
            return False
        return w.subject_subscribe(org, uid, kind, found["id"], subscribed)
    return resolve


for _kind in ("project", "initiative", "cycle"):
    mutation.set_field(f"{_kind}Subscribe", _follow(_kind, True))
    mutation.set_field(f"{_kind}Unsubscribe", _follow(_kind, False))


@mutation.field("notificationsMarkReadForSubject")
def m_notifications_mark_read_for_subject(_, info, subjectId, read=True):
    store, _w, org, uid = _ctx(info)
    return store.inbox_subject_update(org, uid, subjectId, "read", read=read)


@mutation.field("notificationsArchiveForSubject")
def m_notifications_archive_for_subject(_, info, subjectId):
    store, _w, org, uid = _ctx(info)
    return store.inbox_subject_update(org, uid, subjectId, "archive")


@mutation.field("notificationsSnoozeForSubject")
def m_notifications_snooze_for_subject(_, info, subjectId, until=None):
    store, _w, org, uid = _ctx(info)
    return store.inbox_subject_update(org, uid, subjectId, "snooze", until=_snooze_until(until))


@mutation.field("issueSubscribe")
def m_issue_subscribe(_, info, id):
    _store, w, org, uid = _ctx(info)
    return bool(w.issue_subscribe(org, uid, id))


@mutation.field("issueUnsubscribe")
def m_issue_unsubscribe(_, info, id):
    _store, w, org, uid = _ctx(info)
    return bool(w.issue_unsubscribe(org, uid, id))


@mutation.field("cycleUpdate")
def m_cycle_update(_, info, id, input):
    _store, w, org, _uid = _ctx(info)
    updated = w.cycle_update(org, id, input)
    return {"success": bool(updated), "cycle": updated}


@Issue.field("decisions")
def i_decisions(obj, info, status=None, first=None):
    store, _w, org, uid = _ctx(info)
    return store.decisions_for_issue(org, uid, obj["id"], status=status,
                                     limit=min(max(int(first or 50), 1), 200))


@Attachment.field("issue")
def a_issue(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["_issue_id"], viewer_id=uid)


def _get_comment(store, org, cid):
    from sqlalchemy import and_, select

    from hoja_server.db import comment as cmt
    with store.engine.connect() as conn:
        r = conn.execute(select(cmt).where(and_(cmt.c.organization_id == org, cmt.c.id == cid))).first()
        return store.ser_comment(dict(r._mapping)) if r else None


def build_schema():
    from pathlib import Path
    sdl = (Path(__file__).parent / "schema.graphql").read_text()
    from hoja_server import authorized_apps, decisions_api, extensions_api, knowledge, model_providers, permissions, profile_metrics, project_settings, sla, task_templates, usage_ledger, views_api, webhooks_api
    # HOJA-602-A-16 slice 5: the read-only runner/lease and claim/verdict surfaces the
    # review screens need. Both are admin-gated and org-scoped, and neither adds a table.
    from hoja_server import agent_claims, agent_runners, connect_endpoints
    # HOJA-635: the write half of the runner surface — mint and revoke enrollment
    # tokens, revoke a machine. Kept out of agent_runners.py so that module's
    # read-only invariant stays true.
    from hoja_server import agent_ops, agent_runner_admin, project_repos, tailnet_ops
    from hoja_server.agent_conversations import SDL as CONVERSATION_SDL
    from hoja_server.agent_conversations import query as conversation_query
    from hoja_server.model_providers import SDL as PROVIDER_SDL
    from hoja_server.task_aliases import bind_task_surface
    from hoja_server import initiative_api
    initiative_api.bind(Initiative, InitiativeUpdate, Project, Team)
    tasks_sdl = (Path(__file__).parent / "schema_tasks.graphql").read_text()
    # HOJA-491: bind Issue.sla before the alias surface copies Issue's resolvers
    # onto Task, so the agent-facing vocabulary carries the clock too.
    sla.bind(Issue)
    # HOJA-602-A-7: same reason — a delegated Task exposes the session a delegated
    # Issue does.
    from hoja_server import agent_sessions

    agent_sessions.bind(Issue)
    task_bindables, problems = bind_task_surface(
        query, mutation, Issue, MyIssueActivity,
        extra_roots=(views_api.query, views_api.mutation, decisions_api.query, decisions_api.mutation),
    )
    if problems:
        # A partial alias surface is worse than none: fail the whole schema instead.
        raise RuntimeError("HOJA-288 task alias wiring incomplete: " + "; ".join(problems))
    return make_executable_schema(
        [sdl, PROVIDER_SDL, CONVERSATION_SDL, webhooks_api.SDL, extensions_api.SDL,
         views_api.SDL, decisions_api.SDL, project_settings.SDL, permissions.SDL,
         task_templates.SDL, sla.SDL, agent_members.SDL, agent_sessions.SDL,
         agent_policy.SDL, agent_runners.SDL, agent_runner_admin.SDL,
         agent_claims.SDL, usage_ledger.SDL, profile_metrics.SDL, connect_endpoints.SDL, authorized_apps.SDL, agent_ops.SDL, project_repos.SDL, tailnet_ops.SDL, initiative_api.SDL, knowledge.SDL, tasks_sdl],
        query, mutation, *task_bindables,
        knowledge.query, knowledge.mutation, knowledge.knowledge_doc_type,
        model_providers.query, model_providers.mutation,
        project_settings.query, project_settings.mutation,
        permissions.query, permissions.mutation,
        authorized_apps.query, authorized_apps.mutation, authorized_apps.authorized_app_type,
        task_templates.query, task_templates.mutation,
        agent_members.query, agent_members.mutation,
        sla.query, sla.mutation,
        webhooks_api.query, webhooks_api.mutation, webhooks_api.webhook_type,
        extensions_api.query, extensions_api.mutation, extensions_api.extension_type,
        views_api.query, views_api.mutation, views_api.view_type, views_api.preview_type,
        views_api.subscription_type, views_api.custom_view_type,
        initiative_api.query, initiative_api.mutation,
        decisions_api.query, decisions_api.mutation, decisions_api.decision_type,
        conversation_query,
        model_providers.model_provider_type, project_settings.project_status_type,
        project_settings.project_template_type, task_templates.task_template_type,
        sla.sla_policy_type, sla.sla_clock_type, agent_members.agent_member_type,
        agent_sessions.query, agent_sessions.mutation,
        agent_sessions.agent_session_type, agent_sessions.agent_activity_type,
        agent_policy.query, agent_policy.mutation, agent_policy.agent_policy_type,
        agent_runners.query, agent_runners.runner_type, agent_runners.agent_lease_type,
        agent_runner_admin.query, agent_runner_admin.mutation,
        agent_ops.query, agent_ops.mutation,
        project_repos.query, project_repos.mutation, project_repos.project_type,
        tailnet_ops.query, tailnet_ops.mutation,
        connect_endpoints.query, connect_endpoints.mutation,
        connect_endpoints.connect_endpoint_type,
        agent_claims.query, agent_claims.verifier_claim_type,
        agent_claims.agent_verification_type,
        usage_ledger.query, usage_ledger.usage_summary_type,
        usage_ledger.usage_row_type, usage_ledger.usage_event_type,
        profile_metrics.query,
        Team, Initiative, InitiativeUpdate, Issue, IssueActivity, MyIssueActivity, Project, ProjectUpdate, Comment,
        permissions.workspace_permission_type, permissions.workspace_security_policy_type,
        WorkflowState, Cycle, Attachment, GithubLink, Organization,
        datetime_scalar, timeless_scalar, json_scalar, InboxNotification,
    )
