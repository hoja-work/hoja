"""Which workspaces a verified identity may be sent to, read from the workspaces themselves.

The control plane (``app.hoja.work``) is where Google sign-in happens, and it has
to answer one question before it can hand anybody over: *which workspace?* In a
dedicated deployment it cannot see a tenant's database, so it relies on what it
recorded at sign-up (``signup_eligibility``) and on what tenants announce
(``tenant_membership_hint``) — see :mod:`hoja_server.signup_store`.

In the shared tier the control plane and every enterprise workspace run in one
deployment against one database. There the membership rows *are* readable, and a
second-hand copy can only be staler than the original: an invited member whose
announcement never arrived looked like a stranger ("No workspace yet", or the
sign-up form offering to create a workspace). So this module reads the real
rows.

Isolation rules, because this is a deliberate cross-tenant read:

* every read runs in an explicit :func:`tenant_context.system` block — it is an
  enumerated system path, like credential lookups;
* the only input is the address Google verified (``email_verified`` is required
  upstream); the result is exactly the workspaces where that address holds an
  active membership on an active profile, plus workspaces with a pending,
  unexpired invitation to that address, labelled ``invited``;
* only workspaces with their own host (``organization.vanity_host``) are listed,
  each with that host as its origin — the only origin a Google handoff can reach.
  A guessed ``<slug>.<apex>`` is never produced here;
* nothing about any other organization, member or address is returned.

The tenant does not store the Google subject (``global_identity`` is keyed by
address), so the subject cannot widen this match. It still drives the
sign-up-era rows in ``signup_store``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, select

from hoja_server import addresses, tenant_context
from hoja_server.db import (
    global_identity,
    membership_invitation,
    organization,
    organization_membership,
    user,
)

ACTIVE, INVITED = "active", "invited"


def origin_for_host(host: str) -> str:
    return f"https://{host.strip().lower()}/"


def workspaces_for_email(engine, email: str | None, *, now: datetime | None = None) -> list[dict]:
    """The pinned workspaces this verified address belongs to, by display name.

    Each entry is ``{"organization_id", "name", "slug", "host", "origin",
    "status", "role"}``. ``status`` is ``invited`` when the membership exists
    only because of an invitation that has not been accepted yet.
    """
    address = addresses.canonical(email)
    if "@" not in address:
        return []
    marker = (now or datetime.now(timezone.utc)).isoformat()
    with tenant_context.system(), engine.connect() as conn:
        rows = conn.execute(
            select(
                organization.c.id,
                organization.c.name,
                organization.c.url_key,
                organization.c.vanity_host,
                user.c.id.label("profile_id"),
                user.c.admin,
            )
            .select_from(
                organization_membership
                .join(global_identity, global_identity.c.id == organization_membership.c.identity_id)
                .join(organization, organization.c.id == organization_membership.c.organization_id)
                .join(user, and_(user.c.id == organization_membership.c.profile_user_id,
                                 user.c.organization_id == organization_membership.c.organization_id))
            )
            .where(and_(
                global_identity.c.email == address,
                organization_membership.c.active.is_(True),
                user.c.active.is_(True),
                user.c.archived_at.is_(None),
                organization.c.vanity_host.isnot(None),
            ))
        ).mappings().all()
        profiles = [row["profile_id"] for row in rows]
        pending = set()
        if profiles:
            pending = {
                row[0] for row in conn.execute(
                    select(membership_invitation.c.profile_user_id).where(and_(
                        membership_invitation.c.profile_user_id.in_(profiles),
                        membership_invitation.c.accepted_at.is_(None),
                        membership_invitation.c.expires_at > marker,
                    ))
                )
            }
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        host = str(row["vanity_host"] or "").strip().lower()
        if not host or host in seen:
            continue
        seen.add(host)
        result.append({
            "organization_id": row["id"],
            "name": row["name"] or row["url_key"],
            "slug": host.split(".", 1)[0],
            "host": host,
            "origin": origin_for_host(host),
            "status": INVITED if row["profile_id"] in pending else ACTIVE,
            "role": "admin" if row["admin"] else "member",
        })
    result.sort(key=lambda item: (item["status"] != ACTIVE, str(item["name"]).lower()))
    return result


def workspace_for_host(engine, host: str | None) -> dict | None:
    """The workspace that owns ``host`` (its name and origin), or None."""
    name = (host or "").strip().lower()
    if not name:
        return None
    with tenant_context.system(), engine.connect() as conn:
        row = conn.execute(
            select(organization.c.id, organization.c.name, organization.c.vanity_host)
            .where(organization.c.vanity_host == name)
        ).first()
    if row is None:
        return None
    return {"organization_id": row[0], "name": row[1], "host": name, "origin": origin_for_host(name)}


def workspace_for_slug(engine, slug: str | None) -> dict | None:
    """The pinned workspace a sign-up slug became, matched by URL key or host label."""
    key = (slug or "").strip().lower()
    if not key:
        return None
    with tenant_context.system(), engine.connect() as conn:
        rows = conn.execute(
            select(organization.c.id, organization.c.name, organization.c.url_key,
                   organization.c.vanity_host)
            .where(organization.c.vanity_host.isnot(None))
        ).all()
    for org_id, name, url_key, host in rows:
        label = str(host).split(".", 1)[0].lower()
        if key in (label, str(url_key or "").lower()):
            return {"organization_id": org_id, "name": name, "host": str(host).lower(),
                    "origin": origin_for_host(str(host))}
    return None
