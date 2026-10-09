"""Admin-managed model providers for the agent. Keys are encrypted at rest and never returned."""

from __future__ import annotations

from ariadne import ObjectType, QueryType, MutationType, gql
from sqlalchemy import and_, select

from cliniar_server.db import model_provider, new_id, now_iso
from cliniar_server.secrets_box import encrypt_secret

SDL = gql(
    """
    type ModelProvider {
      id: String!
      name: String!
      provider: String!
      modelId: String!
      baseUrl: String
      enabled: Boolean!
      hasApiKey: Boolean!
      createdAt: DateTime!
      updatedAt: DateTime!
    }

    extend type Query {
      modelProviders: [ModelProvider!]!
    }

    extend type Mutation {
      modelProviderCreate(name: String!, provider: String!, modelId: String!, baseUrl: String, apiKey: String!): ModelProvider!
      modelProviderUpdate(id: String!, name: String, modelId: String, baseUrl: String, apiKey: String, enabled: Boolean): ModelProvider!
      modelProviderDelete(id: String!): Boolean!
    }
    """
)

query = QueryType()
mutation = MutationType()
model_provider_type = ObjectType("ModelProvider")


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise ValueError("Only workspace admins can manage model providers.")


def _serialize(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "provider": row["provider"],
        "modelId": row["model_id"],
        "baseUrl": row["base_url"],
        "enabled": bool(row["enabled"]),
        "hasApiKey": bool(row["encrypted_api_key"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _rows(store, org):
    with store.engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            select(model_provider).where(model_provider.c.organization_id == org)
            .order_by(model_provider.c.created_at))]


def _row(store, org, pid):
    with store.engine.connect() as conn:
        r = conn.execute(select(model_provider).where(
            and_(model_provider.c.organization_id == org, model_provider.c.id == pid))).first()
    return dict(r._mapping) if r else None


@query.field("modelProviders")
def r_model_providers(_, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    return [_serialize(r) for r in _rows(store, org)]


@mutation.field("modelProviderCreate")
def m_model_provider_create(_, info, name, provider, modelId, apiKey, baseUrl=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    now = now_iso()
    values = {
        "id": new_id(), "organization_id": org, "name": name, "provider": provider,
        "model_id": modelId, "base_url": baseUrl, "encrypted_api_key": encrypt_secret(apiKey),
        "enabled": True, "created_at": now, "updated_at": now,
    }
    with store.engine.begin() as conn:
        conn.execute(model_provider.insert().values(**values))
    return _serialize(values)


@mutation.field("modelProviderUpdate")
def m_model_provider_update(_, info, id, name=None, modelId=None, baseUrl=None, apiKey=None, enabled=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    current = _row(store, org, id)
    if not current:
        raise ValueError("Model provider not found.")
    changes = {"updated_at": now_iso()}
    if name is not None:
        changes["name"] = name
    if modelId is not None:
        changes["model_id"] = modelId
    if baseUrl is not None:
        changes["base_url"] = baseUrl
    if apiKey is not None:
        changes["encrypted_api_key"] = encrypt_secret(apiKey)
    if enabled is not None:
        changes["enabled"] = enabled
    with store.engine.begin() as conn:
        conn.execute(model_provider.update().where(model_provider.c.id == id).values(**changes))
    return _serialize(_row(store, org, id))


@mutation.field("modelProviderDelete")
def m_model_provider_delete(_, info, id):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    with store.engine.begin() as conn:
        result = conn.execute(model_provider.delete().where(
            and_(model_provider.c.organization_id == org, model_provider.c.id == id)))
    return result.rowcount > 0
