"""Outgoing webhooks with Linear's wire contract.

Pipeline (every step is restart-safe):

1. Dispatch: for each organization with an enabled webhook, read ``change_event``
   after the org's ``webhook_cursor``. Each issue event becomes one frozen
   delivery per matching webhook. ``updatedFrom`` is computed against
   ``webhook_issue_snapshot`` because change events do not carry old values.
   Deliveries, snapshots and the cursor commit in one transaction, so a crash
   either redoes the whole batch or none of it: nothing is lost or skipped.
2. Send: deliveries go out first-in-first-out per webhook (a ticket's ``create``
   always arrives before its ``update``). Each POST is signed at send time:
   ``Linear-Signature`` is the hex HMAC-SHA256 of the exact body bytes, and the
   body carries ``webhookTimestamp`` (ms) for replay checks. A non-2xx reply,
   timeout or connection error is retried after 1 minute, 1 hour and 6 hours,
   then marked failed. Delivery is at-least-once; ``Linear-Delivery`` is stable
   across retries so receivers can de-duplicate.

Targets on loopback/private/link-local networks are refused unless
``HOJA_WEBHOOK_ALLOW_PRIVATE_NETWORKS=1`` (which also permits plain http).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection, Engine

from hoja_server import secrets_box, tenant_context
from hoja_server.db import (
    app_url,
    change_event,
    issue,
    issue_label_link,
    new_id,
    now_iso,
    team,
    webhook,
    webhook_cursor,
    webhook_delivery,
    webhook_issue_snapshot,
    workflow_state,
)

log = logging.getLogger("hoja_server.webhooks")

SUPPORTED_RESOURCE_TYPES = ("Issue",)
RETRY_DELAYS = (timedelta(minutes=1), timedelta(hours=1), timedelta(hours=6))
SEND_TIMEOUT_SECONDS = 5.0
LEASE = timedelta(seconds=60)
DISPATCH_BATCH = 500
SEND_BATCH = 20
ALLOW_PRIVATE_ENV = "HOJA_WEBHOOK_ALLOW_PRIVATE_NETWORKS"
_MAX_ERROR = 500

# Issue fields compared to build updatedFrom (Linear names).
_TRACKED = (
    "title", "description", "priority", "estimate", "stateId", "assigneeId", "projectId",
    "cycleId", "parentId", "teamId", "dueDate", "labelIds", "archivedAt", "completedAt",
    "canceledAt", "startedAt", "updatedAt",
)


class WebhookError(ValueError):
    """A request the caller can fix; ``field`` names the offending input."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def _private_allowed() -> bool:
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in ("1", "true", "yes")


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_target(url: str, resolve: Callable[[str, int], list[str]] | None = None) -> None:
    """Refuse URLs a webhook must never reach. Re-run before every send (DNS can change)."""
    parts = urlsplit(url)
    allow_private = _private_allowed()
    allowed_schemes = ("https", "http") if allow_private else ("https",)
    if parts.scheme not in allowed_schemes or not parts.hostname:
        raise WebhookError(
            "Webhook URL must be an absolute https URL"
            + ("" if allow_private else f" (set {ALLOW_PRIVATE_ENV}=1 to allow http on private networks)")
            + ".", "url")
    if parts.username or parts.password:
        raise WebhookError("Webhook URL must not embed credentials.", "url")
    if allow_private:
        return
    port = parts.port or (443 if parts.scheme == "https" else 80)
    resolve = resolve or _resolve
    try:
        addresses = resolve(parts.hostname, port)
    except OSError as exc:
        raise WebhookError(f"Webhook host does not resolve: {parts.hostname}", "url") from exc
    if not addresses:
        raise WebhookError(f"Webhook host does not resolve: {parts.hostname}", "url")
    for address in addresses:
        if not _is_public(ipaddress.ip_address(address.split("%", 1)[0])):
            raise WebhookError(
                f"Webhook URL resolves to a non-public address ({address}); "
                f"set {ALLOW_PRIVATE_ENV}=1 to allow private networks.", "url")


def _resolve(host: str, port: int) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- management


def _head_seq(conn: Connection, org_id: str) -> int:
    return conn.execute(select(func.coalesce(func.max(change_event.c.seq), 0)).where(
        change_event.c.organization_id == org_id)).scalar_one()


def _resource_types(values: list[str] | None) -> list[str]:
    if not values:
        raise WebhookError("resourceTypes must name at least one resource type.", "resourceTypes")
    unsupported = sorted(set(values) - set(SUPPORTED_RESOURCE_TYPES))
    if unsupported:
        raise WebhookError(
            f"Unsupported resourceTypes {unsupported}; supported: {list(SUPPORTED_RESOURCE_TYPES)}.",
            "resourceTypes")
    return sorted(set(values))


def _seed(conn: Connection, org_id: str, head: int) -> None:
    """Start dispatching an org at ``head`` with a snapshot of every issue, so the
    first update of an existing issue still reports what it changed from."""
    conn.execute(webhook_issue_snapshot.delete().where(webhook_issue_snapshot.c.organization_id == org_id))
    for row in conn.execute(select(issue).where(issue.c.organization_id == org_id)).mappings():
        conn.execute(webhook_issue_snapshot.insert().values(
            organization_id=org_id, issue_id=row["id"],
            data=json.dumps(_tracked(_issue_data(conn, dict(row))))))
    conn.execute(webhook_cursor.delete().where(webhook_cursor.c.organization_id == org_id))
    conn.execute(webhook_cursor.insert().values(organization_id=org_id, seq=head, updated_at=now_iso()))


def _active_count(conn: Connection, org_id: str) -> int:
    return conn.execute(select(func.count()).select_from(webhook).where(and_(
        webhook.c.organization_id == org_id, webhook.c.enabled.is_(True),
        webhook.c.archived_at.is_(None)))).scalar_one()


def create_webhook(engine: Engine, org_id: str, creator_id: str, inp: dict[str, Any]) -> dict:
    url = (inp.get("url") or "").strip()
    check_target(url)
    types = _resource_types(inp.get("resourceTypes"))
    team_id = inp.get("teamId")
    all_public = bool(inp.get("allPublicTeams"))
    if bool(team_id) == all_public:
        raise WebhookError("Provide exactly one of teamId or allPublicTeams: true.", "teamId")
    secret = inp.get("secret") or "hoja_wh_" + secrets.token_hex(24)
    sealed = secrets_box.encrypt_secret(secret)
    enabled = inp.get("enabled", True) is not False
    ts = now_iso()
    with engine.begin() as conn:
        if team_id:
            found = conn.execute(select(team.c.id).where(and_(
                team.c.organization_id == org_id,
                (team.c.id == team_id) | (team.c.key == str(team_id).upper()),
            ))).scalar()
            if not found:
                raise WebhookError("teamId does not name a team in this workspace.", "teamId")
            team_id = found
        head = _head_seq(conn, org_id)
        if enabled and _active_count(conn, org_id) == 0:
            _seed(conn, org_id, head)
        wid = inp.get("id") or new_id()
        conn.execute(webhook.insert().values(
            id=wid, organization_id=org_id, url=url, label=inp.get("label"), enabled=enabled,
            secret_sealed=sealed, resource_types=json.dumps(types), team_id=team_id,
            all_public_teams=all_public, creator_id=creator_id, start_seq=head,
            created_at=ts, updated_at=ts))
    return get_webhook(engine, org_id, wid)


def update_webhook(engine: Engine, org_id: str, wid: str, inp: dict[str, Any]) -> dict | None:
    values: dict[str, Any] = {}
    if "url" in inp and inp["url"] is not None:
        values["url"] = inp["url"].strip()
        check_target(values["url"])
    if "resourceTypes" in inp and inp["resourceTypes"] is not None:
        values["resource_types"] = json.dumps(_resource_types(inp["resourceTypes"]))
    if "label" in inp:
        values["label"] = inp["label"]
    if inp.get("secret"):
        values["secret_sealed"] = secrets_box.encrypt_secret(inp["secret"])
    with engine.begin() as conn:
        row = conn.execute(select(webhook).where(and_(
            webhook.c.id == wid, webhook.c.organization_id == org_id,
            webhook.c.archived_at.is_(None)))).mappings().first()
        if not row:
            return None
        if "enabled" in inp and inp["enabled"] is not None and bool(inp["enabled"]) != row["enabled"]:
            values["enabled"] = bool(inp["enabled"])
            if values["enabled"]:
                head = _head_seq(conn, org_id)
                if _active_count(conn, org_id) == 0:
                    _seed(conn, org_id, head)
                # Re-enabling never replays what happened while disabled.
                values["start_seq"] = head
        values["updated_at"] = now_iso()
        conn.execute(webhook.update().where(webhook.c.id == wid).values(**values))
    return get_webhook(engine, org_id, wid)


def delete_webhook(engine: Engine, org_id: str, wid: str) -> bool:
    with engine.begin() as conn:
        conn.execute(webhook_delivery.delete().where(and_(
            webhook_delivery.c.webhook_id == wid, webhook_delivery.c.organization_id == org_id)))
        result = conn.execute(webhook.delete().where(and_(
            webhook.c.id == wid, webhook.c.organization_id == org_id)))
    return result.rowcount > 0


def _ser(conn: Connection, row: dict) -> dict:
    failures = conn.execute(select(webhook_delivery).where(and_(
        webhook_delivery.c.webhook_id == row["id"], webhook_delivery.c.status == "failed",
    )).order_by(webhook_delivery.c.updated_at.desc()).limit(50)).mappings().all()
    return {
        "id": row["id"], "url": row["url"], "label": row["label"], "enabled": row["enabled"],
        "secret": secrets_box.decrypt_secret(row["secret_sealed"]),
        "resourceTypes": json.loads(row["resource_types"]),
        "allPublicTeams": row["all_public_teams"],
        "teamIds": [row["team_id"]] if row["team_id"] else None,
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "archivedAt": row["archived_at"],
        "_team_id": row["team_id"], "_creator_id": row["creator_id"],
        "failures": [{
            "id": f["id"], "createdAt": f["updated_at"], "executionId": f["id"],
            "httpStatus": f["last_status"], "responseOrError": f["last_error"], "url": row["url"],
        } for f in failures],
    }


def get_webhook(engine: Engine, org_id: str, wid: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(webhook).where(and_(
            webhook.c.id == wid, webhook.c.organization_id == org_id))).mappings().first()
        return _ser(conn, dict(row)) if row else None


def list_webhooks(engine: Engine, org_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(webhook).where(and_(
            webhook.c.organization_id == org_id, webhook.c.archived_at.is_(None),
        )).order_by(webhook.c.created_at, webhook.c.id)).mappings().all()
        return [_ser(conn, dict(r)) for r in rows]


# ------------------------------------------------------------------ dispatch


def _issue_data(conn: Connection, row: dict) -> dict:
    state = conn.execute(select(workflow_state).where(
        workflow_state.c.id == row["state_id"])).mappings().first() if row.get("state_id") else None
    team_row = conn.execute(select(team.c.id, team.c.key, team.c.name).where(
        team.c.id == row["team_id"])).mappings().first() if row.get("team_id") else None
    labels = sorted(conn.execute(select(issue_label_link.c.label_id).where(
        issue_label_link.c.issue_id == row["id"])).scalars())
    return {
        "id": row["id"], "identifier": row["identifier"], "number": row["number"],
        "title": row["title"], "description": row.get("description"),
        "priority": row.get("priority") or 0, "priorityLabel": row.get("priority_label"),
        "estimate": row.get("estimate"), "branchName": row.get("branch_name"),
        "url": row.get("url") or f"{app_url()}/issue/{row['identifier']}",
        "stateId": row.get("state_id"),
        "state": {"id": state["id"], "name": state["name"], "type": state["type"],
                  "color": state["color"]} if state else None,
        "teamId": row.get("team_id"),
        "team": dict(team_row) if team_row else None,
        "assigneeId": row.get("assignee_id"), "creatorId": row.get("creator_id"),
        "projectId": row.get("project_id"), "cycleId": row.get("cycle_id"),
        "parentId": row.get("parent_id"), "labelIds": labels, "dueDate": row.get("due_date"),
        "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
        "archivedAt": row.get("archived_at"), "startedAt": row.get("started_at"),
        "completedAt": row.get("completed_at"), "canceledAt": row.get("canceled_at"),
    }


def _tracked(data: dict) -> dict:
    return {k: data.get(k) for k in _TRACKED} | {"_data": data}


def _matching(hooks: list[dict], seq: int, team_id: str | None, visibility: dict[str, str]) -> list[dict]:
    out = []
    for hook in hooks:
        if seq <= hook["start_seq"] or "Issue" not in json.loads(hook["resource_types"]):
            continue
        if hook["team_id"]:
            if hook["team_id"] != team_id:
                continue
        elif visibility.get(team_id or "", "public") != "public":
            continue
        out.append(hook)
    return out


def dispatch_once(engine: Engine) -> int:
    """Turn committed change events into queued deliveries. Returns deliveries queued."""
    queued = 0
    with tenant_context.system():
        with engine.connect() as conn:
            orgs = list(conn.execute(select(webhook.c.organization_id).where(and_(
                webhook.c.enabled.is_(True), webhook.c.archived_at.is_(None))).distinct()).scalars())
        for org_id in orgs:
            queued += _dispatch_org(engine, org_id)
    return queued


def _dispatch_org(engine: Engine, org_id: str) -> int:
    queued = 0
    with engine.begin() as conn:
        cursor_q = select(webhook_cursor.c.seq).where(webhook_cursor.c.organization_id == org_id)
        if conn.dialect.name == "postgresql":
            cursor_q = cursor_q.with_for_update(skip_locked=True)
        cursor = conn.execute(cursor_q).scalar()
        if cursor is None:  # another node holds it, or nothing seeded yet
            return 0
        events = conn.execute(select(change_event).where(and_(
            change_event.c.organization_id == org_id, change_event.c.seq > cursor,
        )).order_by(change_event.c.seq).limit(DISPATCH_BATCH)).mappings().all()
        if not events:
            return 0
        hooks = [dict(r) for r in conn.execute(select(webhook).where(and_(
            webhook.c.organization_id == org_id, webhook.c.enabled.is_(True),
            webhook.c.archived_at.is_(None)))).mappings()]
        visibility = dict(conn.execute(select(team.c.id, team.c.visibility).where(
            team.c.organization_id == org_id)).all())
        ts = now_iso()
        for ev in events:
            if ev["entity"] != "issue":
                continue
            snap_row = conn.execute(select(webhook_issue_snapshot.c.data).where(
                webhook_issue_snapshot.c.issue_id == ev["entity_id"])).scalar()
            previous = json.loads(snap_row) if snap_row else None
            row = conn.execute(select(issue).where(and_(
                issue.c.id == ev["entity_id"], issue.c.organization_id == org_id))).mappings().first()
            if row is None:
                if previous is None:
                    continue
                action, data, updated_from = "remove", previous["_data"], None
                conn.execute(webhook_issue_snapshot.delete().where(
                    webhook_issue_snapshot.c.issue_id == ev["entity_id"]))
            else:
                data = _issue_data(conn, dict(row))
                current = _tracked(data)
                if previous is None:
                    action, updated_from = "create", None
                else:
                    updated_from = {k: previous.get(k) for k in _TRACKED if previous.get(k) != current[k]}
                    if not updated_from:
                        continue  # already reported (several events of one transaction)
                    action = "update"
                payload_snapshot = json.dumps(current)
                if previous is None:
                    conn.execute(webhook_issue_snapshot.insert().values(
                        organization_id=org_id, issue_id=row["id"], data=payload_snapshot))
                else:
                    conn.execute(webhook_issue_snapshot.update().where(
                        webhook_issue_snapshot.c.issue_id == row["id"]).values(data=payload_snapshot))
            for hook in _matching(hooks, ev["seq"], data.get("teamId") or ev["team_id"], visibility):
                payload = {
                    "action": action, "type": "Issue", "actor": None,
                    "createdAt": ev["created_at"], "data": data, "url": data["url"],
                    "organizationId": org_id, "webhookId": hook["id"],
                }
                if updated_from is not None:
                    payload["updatedFrom"] = updated_from
                conn.execute(webhook_delivery.insert().values(
                    id=new_id(), organization_id=org_id, webhook_id=hook["id"], seq=ev["seq"],
                    event="Issue", payload=json.dumps(payload), status="pending", attempts=0,
                    next_attempt_at=ts, created_at=ts, updated_at=ts))
                queued += 1
        conn.execute(webhook_cursor.update().where(webhook_cursor.c.organization_id == org_id).values(
            seq=events[-1]["seq"], updated_at=ts))
    return queued


# ---------------------------------------------------------------------- send


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _claim(engine: Engine, now: datetime) -> list[dict]:
    """Lease the oldest due delivery of each webhook (strict per-webhook order)."""
    now_s = _iso(now)
    claimed = []
    with engine.begin() as conn:
        heads = conn.execute(select(webhook_delivery.c.webhook_id, func.min(webhook_delivery.c.seq)).where(
            webhook_delivery.c.status.in_(("pending", "sending"))).group_by(
            webhook_delivery.c.webhook_id).limit(SEND_BATCH)).all()
        for wid, seq in heads:
            row = conn.execute(select(webhook_delivery).where(and_(
                webhook_delivery.c.webhook_id == wid, webhook_delivery.c.seq == seq,
                webhook_delivery.c.status.in_(("pending", "sending")),
            )).order_by(webhook_delivery.c.created_at, webhook_delivery.c.id).limit(1)).mappings().first()
            if not row or row["next_attempt_at"] > now_s:
                continue
            if row["status"] == "sending" and (row["lease_until"] or "") > now_s:
                continue
            result = conn.execute(webhook_delivery.update().where(and_(
                webhook_delivery.c.id == row["id"], webhook_delivery.c.status == row["status"],
                webhook_delivery.c.attempts == row["attempts"],
            )).values(status="sending", lease_until=_iso(now + LEASE), updated_at=now_s))
            if result.rowcount == 1:
                claimed.append(dict(row))
    return claimed


Poster = Callable[[str, bytes, dict[str, str]], int]


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> int:
    import httpx

    response = httpx.post(url, content=body, headers=headers, timeout=SEND_TIMEOUT_SECONDS,
                          follow_redirects=False)
    return response.status_code


def build_request(secret: str, payload: dict, delivery_id: str, event: str,
                  now_ms: int | None = None) -> tuple[bytes, dict[str, str]]:
    stamp = now_ms if now_ms is not None else int(time.time() * 1000)
    body = json.dumps(dict(payload, webhookTimestamp=stamp), separators=(",", ":")).encode()
    return body, {
        "Accept-Charset": "utf-8",
        "Content-Type": "application/json; charset=utf-8",
        "Linear-Delivery": delivery_id,
        "Linear-Event": event,
        "Linear-Signature": sign(secret, body),
        "Linear-Timestamp": str(stamp),
        "User-Agent": "Hoja-Webhook",
    }


def deliver_due(engine: Engine, post: Poster | None = None, now: datetime | None = None) -> int:
    """Send due deliveries once. Returns the number delivered."""
    post = post or _http_post
    delivered = 0
    with tenant_context.system():
        for row in _claim(engine, now or datetime.now(timezone.utc)):
            delivered += _send_one(engine, row, post)
    return delivered


def _send_one(engine: Engine, row: dict, post: Poster) -> int:
    with engine.connect() as conn:
        hook = conn.execute(select(webhook).where(webhook.c.id == row["webhook_id"])).mappings().first()
    status_code, error = None, None
    if hook is None:
        _finish(engine, row, "canceled", None, "webhook deleted")
        return 0
    try:
        check_target(hook["url"])
        body, headers = build_request(secrets_box.decrypt_secret(hook["secret_sealed"]),
                                      json.loads(row["payload"]), row["id"], row["event"])
        status_code = post(hook["url"], body, headers)
        if 200 <= status_code < 300:
            _finish(engine, row, "delivered", status_code, None)
            return 1
        error = f"HTTP {status_code}"
    except Exception as exc:  # network, TLS, timeout, refused target
        error = f"{type(exc).__name__}: {exc}"
    attempts = row["attempts"] + 1
    if attempts > len(RETRY_DELAYS):
        _finish(engine, row, "failed", status_code, error, attempts=attempts)
    else:
        _finish(engine, row, "pending", status_code, error, attempts=attempts,
                next_at=datetime.now(timezone.utc) + RETRY_DELAYS[attempts - 1])
    log.warning("webhook %s delivery %s failed (attempt %s): %s", row["webhook_id"], row["id"], attempts, error)
    return 0


def _finish(engine: Engine, row: dict, status: str, code: int | None, error: str | None,
            attempts: int | None = None, next_at: datetime | None = None) -> None:
    values: dict[str, Any] = {"status": status, "last_status": code, "lease_until": None,
                              "last_error": error[:_MAX_ERROR] if error else None, "updated_at": now_iso()}
    if attempts is not None:
        values["attempts"] = attempts
    if next_at is not None:
        values["next_attempt_at"] = _iso(next_at)
    with engine.begin() as conn:
        conn.execute(webhook_delivery.update().where(webhook_delivery.c.id == row["id"]).values(**values))


async def run_forever(engine: Engine, interval: float = 1.0) -> None:
    """Background loop started by the app lifespan."""
    while True:
        try:
            await asyncio.to_thread(dispatch_once, engine)
            await asyncio.to_thread(deliver_due, engine)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("webhook loop iteration failed")
        await asyncio.sleep(interval)
