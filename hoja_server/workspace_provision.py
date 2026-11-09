"""Create a workspace inside the shared database for a verified sign-up (HOJA-226).

The owner is a profile carrying the verified address; their first Google sign-in links it.
Repeating the call for the same slug and owner returns the existing workspace.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select

from hoja_server import secrets_box, tenant_context
from hoja_server.db import (
    global_identity,
    model_provider,
    new_id,
    now_iso,
    organization,
    organization_membership,
    seed_states_for_team,
    team,
    team_member,
    user,
)

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PROVIDER_FIELDS = ("name", "provider", "model_id", "base_url", "context_window", "api_key")


class ProvisionError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def provider_row_id(org_id: str, offering_row_id: str) -> str:
    """Offering row ids carry the managed prefix; the organization makes them unique in one database."""
    return f"{offering_row_id}-{org_id}"


def provision(engine, *, slug: str, owner_email: str, name: str | None = None,
              provider: dict | None = None) -> dict:
    slug, owner_email = slug.strip().lower(), owner_email.strip().lower()
    if not SLUG.match(slug) or "--" in slug:
        raise ProvisionError("invalid_slug")
    if not EMAIL.match(owner_email):
        raise ProvisionError("invalid_email")
    if provider is not None and set(provider) - set(PROVIDER_FIELDS) - {"row_id"}:
        raise ProvisionError("invalid_provider")
    stamp = now_iso()
    with tenant_context.system(), engine.begin() as conn:
        found = conn.execute(select(organization.c.id).where(func.lower(organization.c.url_key) == slug)).first()
        if found:
            owner = conn.execute(select(user.c.id).where(
                user.c.organization_id == found[0], user.c.email == owner_email, user.c.admin.is_(True))).first()
            if not owner:
                raise ProvisionError("slug_taken")
            return {"created": False, "organization_id": found[0], "url_key": slug}
        org_id, uid, tid = new_id(), new_id(), new_id()
        title = name.strip() if name and name.strip() else " ".join(w.capitalize() for w in slug.split("-"))
        display = owner_email.split("@", 1)[0]
        conn.execute(organization.insert().values(id=org_id, name=title, url_key=slug,
                                                  created_at=stamp, updated_at=stamp))
        conn.execute(user.insert().values(id=uid, organization_id=org_id, name=display, display_name=display,
                                          email=owner_email, active=True, admin=True, timezone="UTC",
                                          created_at=stamp, updated_at=stamp))
        identity = conn.execute(select(global_identity.c.id).where(global_identity.c.email == owner_email)).first()
        if identity:
            conn.execute(organization_membership.insert().values(
                id=new_id(), identity_id=identity[0], organization_id=org_id, profile_user_id=uid,
                active=True, created_at=stamp))
        conn.execute(team.insert().values(id=tid, organization_id=org_id, key="GEN", name="General",
                                          description="General team", color="#5e6ad2", visibility="public",
                                          timezone="UTC", issue_counter=0, created_at=stamp, updated_at=stamp))
        conn.execute(team_member.insert().values(team_id=tid, user_id=uid, role="owner"))
        conn.execute(team.update().where(team.c.id == tid).values(
            default_state_id=seed_states_for_team(conn, org_id, tid)))
        if provider:
            conn.execute(model_provider.insert().values(
                id=provider_row_id(org_id, provider.get("row_id") or "default-included"), organization_id=org_id,
                name=provider["name"], provider=provider["provider"], model_id=provider["model_id"],
                base_url=provider.get("base_url"), context_window=provider.get("context_window"),
                encrypted_api_key=secrets_box.encrypt_secret(provider["api_key"], org_id),
                enabled=True, created_at=stamp, updated_at=stamp))
    return {"created": True, "organization_id": org_id, "url_key": slug}
