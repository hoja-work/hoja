"""Signed GitHub webhook receiver for pull_request and push.

Hoja's own webhooks (``hoja_server/webhooks.py``) are *outgoing*. This module is the incoming
half for GitHub linking: it authenticates a delivery, drops duplicates, and records the links
that ``hoja_server/github_linking.py`` detects.

Authentication is the GitHub App webhook secret over the **raw** body:
``X-Hub-Signature-256: sha256=<hex(HMAC-SHA256(secret, body))>``. Nothing is parsed before that
check passes, because the bytes GitHub signed are the bytes we must hash.

Two defences, and their honest limits:

* **Delivery dedupe.** ``X-GitHub-Delivery`` is a primary key in ``github_delivery``. A second
  delivery of the same id does no work. This defeats a replayed request verbatim.
* **Replay window.** GitHub's signature covers the body but *not* the delivery id, so a captured
  body could in principle be re-sent under a fresh id. There is no signed timestamp header, so
  the only usable clock is the event's own timestamp inside the payload. When the payload carries
  one (``pull_request.updated_at``, ``head_commit.timestamp``) we reject it as stale outside
  ``REPLAY_WINDOW_SECONDS``. When it carries none we fall back to dedupe alone. This is stated
  plainly rather than pretended away.

With ``HOJA_GITHUB_WEBHOOK_SECRET`` unset every delivery is refused: a receiver that silently
accepts unauthenticated writes is worse than one that is switched off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hoja_server.db import (
    connector_account,
    github_delivery,
    github_link,
    new_id,
    now_iso,
    team,
)
from hoja_server.github_linking import (
    identifiers_in_commit,
    identifiers_in_pull_request,
)

WEBHOOK_SECRET_ENV = "HOJA_GITHUB_WEBHOOK_SECRET"
WEBHOOK_PATH = "/api/connectors/github/webhook"
GITHUB_PROVIDER = "github"

# How old an event may be before it is treated as a replay.
REPLAY_WINDOW_SECONDS = 300

# Events this receiver acts on. Others are acknowledged and ignored, so GitHub is not asked to
# retry an event we intentionally do not handle.
HANDLED_EVENTS = frozenset({"pull_request", "push"})


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _secret() -> str:
    return (os.environ.get(WEBHOOK_SECRET_ENV) or "").strip()


def signature_ok(secret: str, raw: bytes, header: str | None) -> bool:
    """Constant-time check of ``sha256=<hex>`` over the raw body."""
    given = (header or "").strip()
    if not given.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(given[len("sha256="):], expected)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_timestamp(event: str, payload: dict) -> datetime | None:
    """The event's own timestamp, when the payload carries one we can trust."""
    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        return _parse_time(pr.get("updated_at")) or _parse_time(pr.get("created_at"))
    if event == "push":
        head = payload.get("head_commit") or {}
        return _parse_time(head.get("timestamp"))
    return None


def is_stale(stamp: datetime | None, now: datetime | None = None) -> bool:
    if stamp is None:
        return False
    now = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp > timedelta(seconds=REPLAY_WINDOW_SECONDS)


def _record_delivery(engine: Engine, delivery_id: str, event: str) -> bool:
    """Insert the delivery id. True when it is new, False when we already had it."""
    try:
        with engine.begin() as conn:
            conn.execute(
                github_delivery.insert().values(
                    delivery_id=delivery_id, event=event, received_at=now_iso()
                )
            )
    except IntegrityError:
        return False
    return True


def _organization_for_installation(engine: Engine, installation_id: str) -> str | None:
    """The organization that installed the app, or None for an unknown installation."""
    if not installation_id:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(connector_account.c.organization_id)
            .where(
                and_(
                    connector_account.c.provider == GITHUB_PROVIDER,
                    connector_account.c.external_id == str(installation_id),
                    connector_account.c.status == "active",
                )
            )
            .limit(1)
        ).first()
    return row[0] if row else None


def _team_keys(engine: Engine, organization_id: str) -> frozenset[str]:
    """The organization's team keys. Detection matches only these, so ISO-9001 cannot link."""
    with engine.connect() as conn:
        keys = conn.execute(
            select(team.c.key).where(team.c.organization_id == organization_id)
        ).scalars().all()
    return frozenset(key for key in keys if key)


def _issue_by_identifier(engine: Engine, organization_id: str, identifier: str):
    """Resolve one identifier to an issue id, or None."""
    from hoja_server.db import issue

    with engine.connect() as conn:
        row = conn.execute(
            select(issue.c.id)
            .where(
                and_(
                    issue.c.organization_id == organization_id,
                    issue.c.identifier == identifier,
                )
            )
            .limit(1)
        ).first()
    return row[0] if row else None


def _upsert_link(engine: Engine, values: dict) -> str:
    """Insert or update one link row, keyed by (organization_id, kind, external_id)."""
    with engine.begin() as conn:
        existing = conn.execute(
            select(github_link.c.id).where(
                and_(
                    github_link.c.organization_id == values["organization_id"],
                    github_link.c.kind == values["kind"],
                    github_link.c.external_id == values["external_id"],
                )
            )
        ).first()
        if existing:
            conn.execute(
                update(github_link)
                .where(github_link.c.id == existing[0])
                .values({k: v for k, v in values.items() if k != "id"})
            )
            return existing[0]
        conn.execute(github_link.insert().values(**values))
    return values["id"]


def _links_for_pull_request(engine: Engine, org_id: str, payload: dict) -> int:
    pr = payload.get("pull_request") or {}
    if not isinstance(pr, dict):
        return 0
    keys = _team_keys(engine, org_id)
    if not keys:
        return 0
    head = pr.get("head") or {}
    refs = identifiers_in_pull_request(
        pr.get("title"),
        pr.get("body"),
        (head.get("ref") if isinstance(head, dict) else None),
        keys,
    )
    written = 0
    for reference in refs:
        issue_id = _issue_by_identifier(engine, org_id, reference.identifier)
        if not issue_id:
            continue
        state = "merged" if pr.get("merged") else str(pr.get("state") or "")
        _upsert_link(
            engine,
            {
                "id": new_id(),
                "organization_id": org_id,
                "issue_id": issue_id,
                "kind": "pr",
                "external_id": str(pr.get("id") or pr.get("number") or ""),
                "url": str(pr.get("html_url") or ""),
                "title": str(pr.get("title") or ""),
                "state": state,
                "author": str((pr.get("user") or {}).get("login") or ""),
                "sha": str((head.get("sha") if isinstance(head, dict) else "") or ""),
                "environment": None,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        written += 1
    return written


def _links_for_push(engine: Engine, org_id: str, payload: dict) -> int:
    keys = _team_keys(engine, org_id)
    if not keys:
        return 0
    commits = payload.get("commits")
    if not isinstance(commits, list):
        commits = []
    head = payload.get("head_commit")
    if isinstance(head, dict):
        commits = [*commits, head]
    written = 0
    seen: set[str] = set()
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        sha = str(commit.get("id") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        for reference in identifiers_in_commit(commit.get("message"), keys):
            issue_id = _issue_by_identifier(engine, org_id, reference.identifier)
            if not issue_id:
                continue
            _upsert_link(
                engine,
                {
                    "id": new_id(),
                    "organization_id": org_id,
                    "issue_id": issue_id,
                    "kind": "commit",
                    "external_id": sha,
                    "url": str(commit.get("url") or ""),
                    "title": str((commit.get("message") or "").splitlines()[0] if commit.get("message") else ""),
                    "state": None,
                    "author": str((commit.get("author") or {}).get("name") or ""),
                    "sha": sha,
                    "environment": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )
            written += 1
    return written


def github_webhook_routes(engine: Engine) -> list[Route]:
    """Mount the GitHub App webhook receiver."""

    async def github_webhook(request: Request):
        secret = _secret()
        if not secret:
            # Switched off rather than open: refuse every delivery.
            return _error("GitHub webhook secret is not configured.", 503)

        raw = await request.body()
        if not signature_ok(secret, raw, request.headers.get("X-Hub-Signature-256")):
            return _error("Invalid webhook signature.", 401)

        delivery_id = (request.headers.get("X-GitHub-Delivery") or "").strip()
        if not delivery_id:
            return _error("Missing X-GitHub-Delivery.", 400)

        event = (request.headers.get("X-GitHub-Event") or "").strip()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return _error("Webhook body is not JSON.", 400)
        if not isinstance(payload, dict):
            return _error("Webhook body is not a JSON object.", 400)

        # Dedupe before anything expensive: a repeat must be a no-op.
        if not _record_delivery(engine, delivery_id, event):
            return JSONResponse({"ok": True, "duplicate": True})

        if is_stale(event_timestamp(event, payload)):
            return _error("Stale delivery outside the replay window.", 400)

        if event not in HANDLED_EVENTS:
            return JSONResponse({"ok": True, "ignored": event})

        installation_id = str((payload.get("installation") or {}).get("id") or "")
        org_id = _organization_for_installation(engine, installation_id)
        if org_id is None:
            # Unknown installation: acknowledge so GitHub does not retry forever, do no work.
            return JSONResponse({"ok": True, "unknown_installation": True}, status_code=202)

        if event == "pull_request":
            links = _links_for_pull_request(engine, org_id, payload)
        else:
            links = _links_for_push(engine, org_id, payload)
        return JSONResponse({"ok": True, "links": links})

    return [Route(WEBHOOK_PATH, github_webhook, methods=["POST"])]
