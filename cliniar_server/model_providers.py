"""Admin-managed model providers for the agent. Keys are encrypted at rest and never returned."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from ariadne import MutationType, ObjectType, QueryType, gql
from sqlalchemy import and_, select

from cliniar_server.db import model_provider, new_id, now_iso
from cliniar_server.secrets_box import encrypt_secret

ALLOW_INSECURE_ENV = "CLINIAR_ALLOW_INSECURE_MODEL_URL"
MAX_BASE_URL_LENGTH = 2048

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


def _is_internal_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_model_base_url(base_url: str | None) -> str | None:
    """Reject model base URLs that could reach internal services (SSRF).

    Requires https. With CLINIAR_ALLOW_INSECURE_MODEL_URL=1, http is allowed for
    localhost and loopback/private/link-local/unique-local targets are permitted.
    Hostnames are resolved at validation time; request-time code should re-check.
    """
    if base_url is None:
        return None
    if len(base_url) > MAX_BASE_URL_LENGTH:
        raise ValueError("Model base URL is too long.")
    allow_insecure = os.environ.get(ALLOW_INSECURE_ENV) == "1"
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        _ = parts.port  # raises ValueError on malformed port
    except ValueError as exc:
        raise ValueError("Model base URL is malformed.") from exc
    if not host or parts.username or parts.password:
        raise ValueError("Model base URL must include a host and no credentials.")
    if parts.scheme == "http":
        if not (allow_insecure and _is_loopback_host(host)):
            raise ValueError("Model base URL must use https.")
    elif parts.scheme != "https":
        raise ValueError("Model base URL must use https.")
    if allow_insecure:
        return base_url
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ValueError("Model base URL must not point to a local or private address.")
    try:
        addresses = [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror as exc:
        raise ValueError("Model base URL host could not be resolved.") from exc
    for address in addresses:
        if _is_internal_address(address.split("%", 1)[0]):
            raise ValueError("Model base URL must not point to a local or private address.")
    return base_url




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
    baseUrl = validate_model_base_url(baseUrl)
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
        changes["base_url"] = validate_model_base_url(baseUrl)
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
