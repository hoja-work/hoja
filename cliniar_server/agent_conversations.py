"""Persists agent chat turns per user and summarizes them for the agent settings page."""

from __future__ import annotations

from ariadne import QueryType, gql
from sqlalchemy import func, select

from cliniar_server.db import agent_conversation, agent_message, new_id, now_iso

MAX_MESSAGE_BYTES = 32 * 1024

SDL = gql(
    """
    type AgentConversationSummary {
      id: String!
      messageCount: Int!
      bytes: Int!
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    type AgentConversations {
      count: Int!
      totalBytes: Int!
      conversations: [AgentConversationSummary!]!
    }

    extend type Query {
      agentConversations: AgentConversations!
    }
    """
)

query = QueryType()


def start_conversation(conn, org_id: str, user_id: str, title: str | None = None) -> str:
    cid = new_id()
    ts = now_iso()
    conn.execute(agent_conversation.insert().values(
        id=cid, organization_id=org_id, user_id=user_id, title=title, created_at=ts, updated_at=ts))
    return cid


def save_message(conn, conversation_id: str, role: str, content: str) -> None:
    if len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Each message must be at most {MAX_MESSAGE_BYTES} bytes.")
    ts = now_iso()
    conn.execute(agent_message.insert().values(
        id=new_id(), conversation_id=conversation_id, role=role, content=content, created_at=ts))
    conn.execute(agent_conversation.update()
                 .where(agent_conversation.c.id == conversation_id)
                 .values(updated_at=ts))


@query.field("agentConversations")
def r_agent_conversations(_, info):
    store = info.context["store"]
    org = info.context["org_id"]
    uid = info.context["user_id"]
    byte_len = func.octet_length(agent_message.c.content)
    stmt = (
        select(
            agent_conversation.c.id,
            agent_conversation.c.created_at,
            agent_conversation.c.updated_at,
            func.count(agent_message.c.id).label("message_count"),
            func.coalesce(func.sum(byte_len), 0).label("bytes"),
        )
        .select_from(agent_conversation.outerjoin(
            agent_message, agent_message.c.conversation_id == agent_conversation.c.id))
        .where(agent_conversation.c.organization_id == org, agent_conversation.c.user_id == uid)
        .group_by(agent_conversation.c.id)
        .order_by(agent_conversation.c.updated_at.desc())
    )
    with store.engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(stmt)]
    conversations = [{
        "id": r["id"],
        "messageCount": int(r["message_count"]),
        "bytes": int(r["bytes"]),
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
    } for r in rows]
    return {
        "count": len(conversations),
        "totalBytes": sum(c["bytes"] for c in conversations),
        "conversations": conversations,
    }
