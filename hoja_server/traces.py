"""Causal, idempotent session-trace ingestion (`POST /api/traces`).

Plugins upload ``hoja.trace/v1`` JSON once per turn or session end. When the
caller also passed ``X-Hoja-Session-Id`` on MCP/GraphQL writes, ``change_event``
already records every issue/comment touched by that session — so traces link
automatically even when local ``TaskManage`` tasks were never used.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import and_, select

from hoja_server import identifiers, logging_setup, tenant_context, uploads
from hoja_server.db import attachment, change_event, comment, issue, new_id, now_iso

TRACE_SCHEMA = "hoja.trace/v1"

# Defense-in-depth token scrubbing in addition to client-side redaction and
# logging_setup.scrub_secrets. Never logs raw matches.
_TOKEN_PATTERNS = (
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/=]{16,}", re.IGNORECASE),
    re.compile(r"\b(hoja_[A-Za-z0-9_\-]{12,}|lin_api_[A-Za-z0-9_\-]{12,})\b"),
    re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{20,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{16,})\b"),
)


def scrub_trace_json(raw_json: str) -> str:
    """Scrub watched runtime secrets and common credential tokens from JSON text."""
    text = logging_setup.scrub_secrets(raw_json)
    text = _TOKEN_PATTERNS[0].sub(r"\1" + logging_setup.REDACTED, text)
    for pattern in _TOKEN_PATTERNS[1:]:
        text = pattern.sub(logging_setup.REDACTED, text)
    return text


def _dict_field(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    val = mapping.get(key)
    return val if isinstance(val, dict) else {}


def _subtitle(trace: dict[str, Any]) -> str:
    source = _dict_field(trace, "source")
    host = str(source.get("host") or "agent")
    host_name = {
        "claude-code": "Claude Code",
        "codex": "Codex",
        "antigravity": "Antigravity",
    }.get(host, host)
    totals = _dict_field(trace, "totals")
    session = _dict_field(trace, "session")
    ms = int(session.get("durationMs") or 0)
    dur = f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s" if ms < 60_000 else f"{ms // 60_000}m {(ms % 60_000) // 1000}s"
    turns = int(totals.get("turns") or 0)
    tools = int(totals.get("toolCalls") or 0)
    parts = [
        f"{host_name} trace",
        dur,
        f"{turns} turn{'' if turns == 1 else 's'}",
        f"{tools} tool call{'' if tools == 1 else 's'}",
    ]
    if totals.get("toolErrors"):
        parts.append(f"{int(totals['toolErrors'])} failed")
    return " · ".join(parts)


def _filename(trace: dict[str, Any], reason: str | None) -> str:
    source = _dict_field(trace, "source")
    session = _dict_field(trace, "session")
    slug = re.sub(r"[^a-z0-9-]", "-", str(source.get("host") or "agent").lower()).strip("-") or "agent"
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(session.get("id") or "session"))[:16] or "session"
    suffix = f"-{re.sub(r'[^a-z0-9-]', '', reason.lower())}" if reason else ""
    return f"{slug}{suffix}-{sid}.trace.json"


def _correlated_issue_ids(conn: Any, org_id: str, session_id: str) -> list[str]:
    """Issues touched by ``session_id`` according to ``change_event``."""
    rows = conn.execute(
        select(change_event.c.entity, change_event.c.entity_id)
        .where(and_(
            change_event.c.organization_id == org_id,
            change_event.c.session_id == session_id,
            change_event.c.entity.in_(("issue", "comment")),
        ))
        .order_by(change_event.c.seq.asc())
    ).all()
    issue_ids: list[str] = []
    comment_ids: list[str] = []
    seen: set[str] = set()
    for entity, entity_id in rows:
        if entity == "issue" and entity_id not in seen:
            seen.add(entity_id)
            issue_ids.append(entity_id)
        elif entity == "comment":
            comment_ids.append(entity_id)
    if comment_ids:
        c_rows = conn.execute(
            select(comment.c.issue_id).where(and_(
                comment.c.organization_id == org_id,
                comment.c.id.in_(comment_ids),
                comment.c.issue_id.is_not(None),
            ))
        ).scalars().all()
        for iid in c_rows:
            if iid and iid not in seen:
                seen.add(iid)
                issue_ids.append(iid)
    return issue_ids


def ingest_trace(store: Any, *, org_id: str, viewer_id: str, trace: dict[str, Any],
                 explicit_issues: list[str] | None = None,
                 reason: str | None = None) -> dict[str, Any]:
    """Validate, scrub, store, and idempotently attach a ``hoja.trace/v1`` trace."""
    if not isinstance(trace, dict) or trace.get("schema") != TRACE_SCHEMA:
        raise ValueError("trace.schema must be 'hoja.trace/v1'")
    session = trace.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not session["id"].strip():
        raise ValueError("trace.session.id is required")
    if not isinstance(trace.get("spans"), list):
        raise ValueError("trace.spans must be a list")
    session_id = tenant_context.clean_session_id(session["id"])
    if not session_id:
        raise ValueError("trace.session.id contains unsupported characters")

    visible_teams = store.visible_team_ids(org_id, viewer_id)
    refs = [str(r).strip() for r in (explicit_issues or []) if isinstance(r, str) and r.strip()]

    with store.engine.connect() as conn:
        correlated = _correlated_issue_ids(conn, org_id, session_id)
        target_rows: list[tuple[str, str, str | None]] = []
        seen_ids: set[str] = set()
        for ref in refs:
            row = conn.execute(select(issue.c.id, issue.c.identifier, issue.c.team_id).where(and_(
                issue.c.organization_id == org_id,
                identifiers.issue_ref_clause(org_id, ref),
            ))).first()
            if row and (visible_teams is None or row.team_id is None or row.team_id in visible_teams) and row.id not in seen_ids:
                seen_ids.add(row.id)
                target_rows.append((row.id, row.identifier, row.team_id))
        if correlated:
            corr_rows = conn.execute(select(issue.c.id, issue.c.identifier, issue.c.team_id).where(and_(
                issue.c.organization_id == org_id,
                issue.c.id.in_(correlated),
            ))).all()
            for row in corr_rows:
                if (visible_teams is None or row.team_id is None or row.team_id in visible_teams) and row.id not in seen_ids:
                    seen_ids.add(row.id)
                    target_rows.append((row.id, row.identifier, row.team_id))

    if not target_rows:
        return {"url": None, "issues": [], "attachments": []}

    scrubbed = scrub_trace_json(json.dumps(trace, ensure_ascii=False, separators=(",", ":")))
    body_bytes = scrubbed.encode("utf-8")
    if len(body_bytes) > uploads.MAX_BYTES:
        raise OverflowError("Trace exceeds 10 MB upload limit")

    title = _filename(trace, reason)
    subtitle = _subtitle(trace)
    info = uploads.save_blob_with_meta(org_id, title, "application/json", body_bytes)
    url = info["url"]
    content_type = uploads.content_type_for_url(url) or "application/json"

    attachments_out: list[dict[str, Any]] = []
    with store.engine.begin() as conn:
        for issue_id, identifier, _team_id in target_rows:
            existing = conn.execute(select(attachment.c.id).where(and_(
                attachment.c.organization_id == org_id,
                attachment.c.issue_id == issue_id,
                attachment.c.session_id == session_id,
            ))).first()
            if existing:
                conn.execute(attachment.update().where(attachment.c.id == existing.id).values(
                    url=url, title=title, subtitle=subtitle, content_type=content_type,
                ))
                attachments_out.append({
                    "id": existing.id, "issue": identifier, "url": url,
                    "title": title, "subtitle": subtitle, "updated": True,
                })
            else:
                aid = new_id()
                conn.execute(attachment.insert().values(
                    id=aid, organization_id=org_id, issue_id=issue_id, creator_id=viewer_id,
                    url=url, title=title, subtitle=subtitle, content_type=content_type,
                    created_at=now_iso(), session_id=session_id,
                ))
                attachments_out.append({
                    "id": aid, "issue": identifier, "url": url,
                    "title": title, "subtitle": subtitle, "updated": False,
                })

    return {
        "url": url,
        "title": title,
        "subtitle": subtitle,
        "issues": [identifier for _, identifier, _ in target_rows],
        "attachments": attachments_out,
    }
