"""Create a workspace inside the shared database (HOJA-226).

Used by the provisioner for a verified sign-up and by an operator for an
enterprise workspace. One transaction writes the organization, the owner's
admin profile, the owner's membership, a General team and, optionally, the
included model provider.

The owner is admitted deterministically: the provisioned address gets an
active ``organization_membership`` here, so ``google_login.resolve_member``
admits it through its membership step on the workspace's own host. No other
address gains anything, and the membership is scoped to this organization.

A workspace that should answer on its own host (``<slug>.<base domain>``) gets
``organization.vanity_host``; the Traefik route for that host is written by
``infra/scripts/hoja-tenant route``.

Repeating the call with the same slug and owner returns the existing workspace
and fills in what an earlier version of this code left out (vanity host, owner
membership, control-plane directory entry). Arguments that contradict the
stored workspace are refused.
"""

from __future__ import annotations

import os
import re

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from hoja_server import addresses, secrets_box, signup_policy, tenant_context
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
    tenant_membership_hint,
    user,
)

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PROVIDER_FIELDS = ("name", "provider", "model_id", "base_url", "context_window", "api_key")
BASE_DOMAIN_ENV = "HOJA_TENANT_BASE_DOMAIN"
DEFAULT_BASE_DOMAIN = "hoja.work"


class ProvisionError(Exception):
    def __init__(self, reason: str, message: str | None = None):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


def provider_row_id(org_id: str, offering_row_id: str) -> str:
    """Offering row ids carry the managed prefix; the organization makes them unique in one database."""
    return f"{offering_row_id}-{org_id}"


def tenant_base_domain(environ=None) -> str:
    env = os.environ if environ is None else environ
    return (env.get(BASE_DOMAIN_ENV) or DEFAULT_BASE_DOMAIN).strip().lower()


def validate_vanity_host(host: str, *, slug: str, base_domain: str | None = None) -> str:
    """Return ``host`` if it can serve workspace ``slug``, else raise :class:`ProvisionError`.

    The host must be exactly ``<slug>.<base domain>``: the wildcard certificate
    covers one label, and keeping the host's label equal to the workspace slug
    keeps sign-up slugs, routes (``tenant-<slug>.yml``) and the control-plane
    directory (``workspace_directory``) naming the same thing.
    """
    base = (base_domain or tenant_base_domain()).strip().lower()
    raw = (host or "").strip()
    if not raw:
        raise ProvisionError("invalid_vanity_host", "the vanity host is empty")
    if raw != raw.lower():
        raise ProvisionError("invalid_vanity_host", f"vanity host {raw!r} must be lowercase")
    if not raw.endswith("." + base):
        raise ProvisionError("invalid_vanity_host",
                             f"vanity host {raw!r} is not under the tenant base domain {base!r}")
    label = raw[: -len(base) - 1]
    if "." in label:
        raise ProvisionError("invalid_vanity_host",
                             f"vanity host {raw!r} has more than one label before {base!r}; "
                             f"the wildcard certificate covers only *.{base}")
    if label in signup_policy.RESERVED_SLUGS:
        raise ProvisionError("reserved_vanity_host", f"{label!r} is reserved on {base}")
    try:
        signup_policy.validate_slug(label)
    except signup_policy.SlugRefused as exc:
        raise ProvisionError("invalid_vanity_host", str(exc)) from exc
    if label != slug:
        raise ProvisionError("vanity_host_slug_mismatch",
                             f"vanity host {raw!r} must be {slug}.{base}: the control plane sends "
                             f"members of {slug!r} to that host")
    return raw


def _host_owner(conn, host: str) -> str | None:
    row = conn.execute(select(organization.c.id).where(func.lower(organization.c.vanity_host) == host)).first()
    return row[0] if row else None


def _ensure_owner_membership(conn, *, org_id: str, profile_id: str, address: str, stamp: str) -> str:
    """Give the owner's profile an active membership. Returns ``created``, ``present`` or ``inactive``.

    A revoked membership is reported, never reactivated: removing the owner was
    a decision and re-running provisioning is not a way back in.
    """
    existing = conn.execute(select(organization_membership.c.active).where(
        organization_membership.c.profile_user_id == profile_id)).first()
    if existing is not None:
        return "present" if existing[0] else "inactive"
    identity = conn.execute(select(global_identity.c.id).where(global_identity.c.email == address)
                            .order_by(global_identity.c.created_at, global_identity.c.id)).first()
    identity_id = identity[0] if identity else new_id()
    if identity is None:
        conn.execute(global_identity.insert().values(id=identity_id, email=address, created_at=stamp))
    elif conn.execute(select(organization_membership.c.id).where(and_(
            organization_membership.c.identity_id == identity_id,
            organization_membership.c.organization_id == org_id))).first():
        # The address already belongs to this workspace through another profile.
        return "present"
    conn.execute(organization_membership.insert().values(
        id=new_id(), identity_id=identity_id, organization_id=org_id, profile_user_id=profile_id,
        active=True, created_at=stamp))
    return "created"


def _ensure_directory_hint(conn, *, slug: str, address: str, stamp: str) -> bool:
    """Tell the control-plane directory that the owner belongs to ``slug``. True when written."""
    found = conn.execute(select(tenant_membership_hint.c.id).where(and_(
        tenant_membership_hint.c.slug == slug, tenant_membership_hint.c.email == address))).first()
    if found:
        return False
    conn.execute(tenant_membership_hint.insert().values(
        id=new_id(), slug=slug, email=address, role="admin", expires_at=None,
        created_at=stamp, updated_at=stamp))
    return True


def provision(engine, *, slug: str, owner_email: str, name: str | None = None,
              provider: dict | None = None, vanity_host: str | None = None) -> dict:
    slug, owner_email = slug.strip().lower(), addresses.canonical(owner_email)
    if not SLUG.match(slug) or "--" in slug:
        raise ProvisionError("invalid_slug")
    if slug in signup_policy.RESERVED_SLUGS:
        raise ProvisionError("reserved_slug", f"{slug!r} is reserved")
    if not EMAIL.match(owner_email):
        raise ProvisionError("invalid_email")
    if provider is not None and set(provider) - set(PROVIDER_FIELDS) - {"row_id"}:
        raise ProvisionError("invalid_provider")
    host = validate_vanity_host(vanity_host, slug=slug) if vanity_host is not None else None
    stamp = now_iso()
    try:
        with tenant_context.system(), engine.begin() as conn:
            return _provision(conn, slug=slug, owner_email=owner_email, name=name, provider=provider,
                              host=host, stamp=stamp)
    except IntegrityError as exc:
        # A concurrent run took the slug or the host between the checks and the insert.
        raise ProvisionError("conflict", "another workspace took this slug or host; re-run to see which") from exc


def _provision(conn, *, slug, owner_email, name, provider, host, stamp) -> dict:
    found = conn.execute(select(organization.c.id, organization.c.vanity_host)
                         .where(func.lower(organization.c.url_key) == slug)).first()
    if host is not None:
        holder = _host_owner(conn, host)
        if holder is not None and (found is None or holder != found[0]):
            raise ProvisionError("vanity_host_taken", f"{host} already serves another workspace")
    repaired: list[str] = []
    if found:
        org_id, current_host = found
        owner = conn.execute(select(user.c.id).where(
            user.c.organization_id == org_id, user.c.email == owner_email, user.c.admin.is_(True))).first()
        if not owner:
            raise ProvisionError("slug_taken", f"{slug!r} exists and {owner_email} is not one of its admins")
        if host is not None and current_host and current_host.lower() != host:
            raise ProvisionError("vanity_host_conflict",
                                 f"{slug!r} is already served on {current_host}, not {host}")
        if host is not None and not current_host:
            conn.execute(organization.update().where(organization.c.id == org_id)
                         .values(vanity_host=host, updated_at=stamp))
            repaired.append("vanity_host")
            current_host = host
        membership = _ensure_owner_membership(conn, org_id=org_id, profile_id=owner[0],
                                              address=owner_email, stamp=stamp)
        if membership == "created":
            repaired.append("owner_membership")
        if _ensure_directory_hint(conn, slug=slug, address=owner_email, stamp=stamp):
            repaired.append("directory_hint")
        return {"created": False, "organization_id": org_id, "url_key": slug, "vanity_host": current_host,
                "owner_email": owner_email, "owner_membership": "active" if membership != "inactive"
                else "inactive", "repaired": repaired}

    org_id, uid, tid = new_id(), new_id(), new_id()
    title = name.strip() if name and name.strip() else " ".join(w.capitalize() for w in slug.split("-"))
    display = owner_email.split("@", 1)[0]
    conn.execute(organization.insert().values(id=org_id, name=title, url_key=slug, vanity_host=host,
                                              created_at=stamp, updated_at=stamp))
    conn.execute(user.insert().values(id=uid, organization_id=org_id, name=display, display_name=display,
                                      email=owner_email, active=True, admin=True, timezone="UTC",
                                      created_at=stamp, updated_at=stamp))
    _ensure_owner_membership(conn, org_id=org_id, profile_id=uid, address=owner_email, stamp=stamp)
    _ensure_directory_hint(conn, slug=slug, address=owner_email, stamp=stamp)
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
    return {"created": True, "organization_id": org_id, "url_key": slug, "vanity_host": host,
            "owner_email": owner_email, "owner_membership": "active", "repaired": repaired}
