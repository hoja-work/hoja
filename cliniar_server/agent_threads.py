"""REST thread endpoints for the agent chat sidebar (shape expected by restStorage)."""

from __future__ import annotations

from sqlalchemy import and_, delete, func, select, update
from starlette.responses import JSONResponse

from cliniar_server.agent_conversations import save_message, start_conversation
from cliniar_server.db import agent_conversation, agent_message, now_iso

TITLE_LIMIT = 60


def _owned(conn, org_id: str, uid: str, thread_id: str):
    return conn.execute(select(agent_conversation).where(and_(
        agent_conversation.c.id == thread_id,
        agent_conversation.c.organization_id == org_id,
        agent_conversation.c.user_id == uid,
    ))).first()


def _title_from(text: str) -> str:
    text = " ".join(text.split())
    return text[:TITLE_LIMIT] or "New chat"


def _thread_json(row) -> dict:
    return {"id": row.id, "title": row.title or "New chat", "createdAt": row.created_at}


def list_threads(store, org_id: str, uid: str) -> dict:
    with store.engine.connect() as conn:
        rows = conn.execute(select(agent_conversation).where(and_(
            agent_conversation.c.organization_id == org_id,
            agent_conversation.c.user_id == uid,
        )).order_by(agent_conversation.c.updated_at.desc())).all()
    return {"threads": [_thread_json(r) for r in rows]}


def create_thread(store, org_id: str, uid: str, messages: list[dict]) -> dict:
    first = next((m.get("content") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    with store.engine.begin() as conn:
        cid = start_conversation(conn, org_id, uid, title=_title_from(first))
        for m in messages:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                save_message(conn, cid, m["role"], m["content"])
        row = conn.execute(select(agent_conversation).where(agent_conversation.c.id == cid)).one()
    return _thread_json(row)


def get_messages(store, org_id: str, uid: str, thread_id: str) -> list[dict] | None:
    with store.engine.connect() as conn:
        if _owned(conn, org_id, uid, thread_id) is None:
            return None
        rows = conn.execute(select(agent_message.c.role, agent_message.c.content).where(
            agent_message.c.conversation_id == thread_id).order_by(agent_message.c.created_at, agent_message.c.id)).all()
    return [{"role": r.role, "content": r.content} for r in rows]


def update_thread(store, org_id: str, uid: str, thread_id: str, title: str | None) -> dict | None:
    with store.engine.begin() as conn:
        if _owned(conn, org_id, uid, thread_id) is None:
            return None
        if title is not None:
            conn.execute(update(agent_conversation).where(agent_conversation.c.id == thread_id)
                         .values(title=title[:TITLE_LIMIT], updated_at=now_iso()))
        row = conn.execute(select(agent_conversation).where(agent_conversation.c.id == thread_id)).one()
    return _thread_json(row)


def delete_thread(store, org_id: str, uid: str, thread_id: str) -> bool:
    with store.engine.begin() as conn:
        if _owned(conn, org_id, uid, thread_id) is None:
            return False
        conn.execute(delete(agent_message).where(agent_message.c.conversation_id == thread_id))
        conn.execute(delete(agent_conversation).where(agent_conversation.c.id == thread_id))
    return True
