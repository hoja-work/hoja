"""Turn a verified Google address into an app identity on *this* instance.

Two jobs, deliberately in one place because they must agree on what "first
user" means:

* a database that has never been set up gets its first user from the verified
  address, as the workspace admin, with no password ever stored;
* a database that is already set up only ever *matches* an address. Google
  proves who you are; it does not grant access to someone else's workspace.
  An unknown or uninvited address is refused, not created.
* the one exception is the address the provisioner recorded as the workspace
  owner (``HOJA_OWNER_EMAIL``). It is authoritative for its own workspace at
  any time, not only while the database is empty, because otherwise a restored
  or previously-initialised instance locks its real owner out permanently.

The caller owns the transaction so that "is this instance empty?" and the rows
that answer it are decided atomically.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from sqlalchemy import select

from hoja_server import addresses
from hoja_server import schema_sdl  # noqa: F401  (keeps table imports honest)
from hoja_server.db import (
    global_identity,
    new_id,
    organization,
    organization_membership,
    team,
    team_member,
    user,
)
from hoja_server.db import seed_states_for_team

#: A slug Google's `hd`-free sign-in may hand us; used only to name the first
#: workspace, never as an identifier.
_NON_SLUG = re.compile(r"[^a-z0-9]+")

ACTION_BOOTSTRAPPED = "bootstrapped"
ACTION_LOGIN = "login"
ACTION_DENIED = "denied"


def _titleise(slug: str) -> str:
    words = [w for w in _NON_SLUG.split(slug.replace(".", "-").lower()) if w]
    return " ".join(w.capitalize() for w in words) or "Workspace"


def owner_email() -> str | None:
    """The Google address allowed to bootstrap this empty instance, if set.

    The provisioner writes ``HOJA_OWNER_EMAIL`` into a new tenant before its
    first request. Without this check, whoever discovered the new hostname
    first could become its admin. An unset value preserves the self-hosted
    behaviour (the first verified Google user owns a fresh instance).
    """
    value = (os.environ.get("HOJA_OWNER_EMAIL") or "").strip().lower()
    return value or None


LINKS_ENV = "HOJA_IDENTITY_LINKS"


def identity_links() -> dict[str, str]:
    """Explicit "this Google address is that profile" mappings.

    Format: ``gmail=profile`` pairs separated by ``,`` or ``;``. This is the
    durable form of an invitation for people whose profile predates Google
    sign-in: the profile keeps its history and its admin role, and the verified
    address becomes its credential. Unset means no mappings.
    """
    raw = (os.environ.get(LINKS_ENV) or "").strip()
    links: dict[str, str] = {}
    for chunk in re.split(r"[;,]", raw):
        if "=" not in chunk:
            continue
        verified, profile = (part.strip().lower() for part in chunk.split("=", 1))
        if "@" in verified and "@" in profile:
            links[verified] = profile
    return links


def linked_membership(conn, address: str) -> dict | None:
    """The active membership already attached to this verified address, if any."""
    row = conn.execute(
        select(
            organization_membership.c.profile_user_id,
            organization_membership.c.organization_id,
        )
        .join(global_identity, global_identity.c.id == organization_membership.c.identity_id)
        .where(
            global_identity.c.email == address,
            organization_membership.c.active.is_(True),
        )
        .limit(1)
    ).first()
    return {"user_id": row[0], "organization_id": row[1]} if row is not None else None


def _claim_profile(conn, *, profile, address: str, stamp: str, owner: bool = False) -> dict:
    """Attach a verified address to an existing profile, keeping its history.

    The profile's row, admin flag and data stay untouched; only the identity
    behind its single membership moves. A deactivated profile and a revoked
    membership are refused: linking a credential is not a way back in.
    """
    uid, profile_org, active = profile[0], profile[1], profile[2]
    if not active:
        return {"action": ACTION_DENIED, "reason": "account_disabled"}
    identity = conn.execute(
        select(global_identity.c.id).where(global_identity.c.email == address)
    ).first()
    identity_id = identity[0] if identity is not None else new_id()
    if identity is None:
        conn.execute(
            global_identity.insert().values(id=identity_id, email=address, created_at=stamp)
        )
    membership = conn.execute(
        select(organization_membership.c.id, organization_membership.c.active).where(
            organization_membership.c.profile_user_id == uid
        )
    ).first()
    if membership is not None and not membership[1]:
        return {"action": ACTION_DENIED, "reason": "no_membership"}
    if membership is None:
        conn.execute(
            organization_membership.insert().values(
                id=new_id(),
                identity_id=identity_id,
                organization_id=profile_org,
                profile_user_id=uid,
                active=True,
                created_at=stamp,
            )
        )
    else:
        conn.execute(
            organization_membership.update()
            .where(organization_membership.c.id == membership[0])
            .values(identity_id=identity_id, organization_id=profile_org)
        )
    result = {
        "action": ACTION_LOGIN,
        "user_id": uid,
        "organization_id": profile_org,
        "identity_id": identity_id,
        "linked": True,
    }
    if owner:
        result["owner"] = True
    return result


def _admit_owner(conn, *, address: str, display: str, stamp: str, org_id: str) -> dict:
    """Admit the address the provisioner named as this workspace's owner.

    ``HOJA_OWNER_EMAIL`` is written by the provisioner from the address that
    created (and is billed for) the workspace. Honouring it only while the
    database was empty made the owner unrecoverable the moment an organization
    existed: a restored instance, a first-run wizard, or an earlier sign-in from
    another address left the real owner refused with ``not_invited`` forever.

    The address is not a bypass. Google has already verified it, and it admits
    no other address. Order matters:

    1. an active membership already attached to this address wins (the state
       after an identity migration — never create a second profile);
    2. a profile carrying the address is claimed;
    3. with no such profile, the single active admin profile is claimed, so the
       owner keeps the history of the account they already used;
    4. only a workspace with no admin at all gets a new admin profile.
    """
    existing = linked_membership(conn, address)
    if existing is not None:
        return {
            "action": ACTION_LOGIN,
            "user_id": existing["user_id"],
            "organization_id": existing["organization_id"],
            "identity_id": conn.execute(
                select(global_identity.c.id).where(global_identity.c.email == address)
            ).scalar_one(),
            "owner": True,
            "linked": True,
        }

    profile = conn.execute(
        select(user.c.id, user.c.organization_id, user.c.active)
        .where(user.c.email == address)
        .limit(1)
    ).first()
    if profile is not None:
        return _claim_profile(conn, profile=profile, address=address, stamp=stamp, owner=True)

    admins = conn.execute(
        select(user.c.id, user.c.organization_id, user.c.active).where(
            user.c.admin.is_(True), user.c.active.is_(True)
        )
    ).all()
    if len(admins) == 1:
        return _claim_profile(conn, profile=admins[0], address=address, stamp=stamp, owner=True)
    if len(admins) > 1:
        # Ambiguous: naming the profile is an operator decision (HOJA_IDENTITY_LINKS).
        return {"action": ACTION_DENIED, "reason": "owner_profile_ambiguous"}

    uid, identity_id = new_id(), new_id()
    conn.execute(
        user.insert().values(
            id=uid,
            organization_id=org_id,
            name=display,
            display_name=display,
            email=address,
            active=True,
            admin=True,
            timezone="UTC",
            created_at=stamp,
            updated_at=stamp,
        )
    )
    conn.execute(
        global_identity.insert().values(id=identity_id, email=address, created_at=stamp)
    )
    conn.execute(
        organization_membership.insert().values(
            id=new_id(),
            identity_id=identity_id,
            organization_id=org_id,
            profile_user_id=uid,
            active=True,
            created_at=stamp,
        )
    )
    return {
        "action": ACTION_LOGIN,
        "user_id": uid,
        "organization_id": org_id,
        "identity_id": identity_id,
        "owner": True,
        "adopted": True,
    }


def _slugify(text: str) -> str:
    slug = _NON_SLUG.sub("-", text.lower()).strip("-")
    return slug or "workspace"


def _team_key(text: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9]", "", text.upper())
    return (letters or "TEAM")[:4]


def resolve_verified_email(
    conn,
    *,
    email: str,
    subject: str,
    name: str | None = None,
    picture: str | None = None,
    workspace_slug: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Resolve a verified address to an identity row set on this instance.

    Returns ``{"action": ..., "user_id", "organization_id", "identity_id"}``.
    ``action`` is one of ``bootstrapped`` (first user, admin), ``login``
    (existing member) or ``denied`` with a ``reason``.
    """
    address = addresses.canonical(email)
    if "@" not in address:
        return {"action": ACTION_DENIED, "reason": "invalid_email"}
    if not subject:
        return {"action": ACTION_DENIED, "reason": "missing_subject"}

    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat()
    display = (name or "").strip() or address.split("@", 1)[0]

    existing_org = conn.execute(select(organization.c.id).limit(1)).first()

    if existing_org is None:
        owner = owner_email()
        if owner and address != owner:
            return {"action": ACTION_DENIED, "reason": "owner_only"}
        slug_source = workspace_slug or address.split("@", 1)[1]
        workspace_key = _slugify(slug_source)
        org_id, uid, identity_id, tid = new_id(), new_id(), new_id(), new_id()
        conn.execute(
            organization.insert().values(
                id=org_id,
                name=_titleise(workspace_key),
                url_key=workspace_key,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        conn.execute(
            user.insert().values(
                id=uid,
                organization_id=org_id,
                name=display,
                display_name=display,
                email=address,
                active=True,
                admin=True,
                timezone="UTC",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        conn.execute(global_identity.insert().values(id=identity_id, email=address, created_at=stamp))
        conn.execute(
            organization_membership.insert().values(
                id=new_id(),
                identity_id=identity_id,
                organization_id=org_id,
                profile_user_id=uid,
                active=True,
                created_at=stamp,
            )
        )
        conn.execute(
            team.insert().values(
                id=tid,
                organization_id=org_id,
                key=_team_key("general"),
                name="General",
                description="General team",
                color="#5e6ad2",
                visibility="public",
                timezone="UTC",
                issue_counter=0,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        conn.execute(team_member.insert().values(team_id=tid, user_id=uid, role="owner"))
        default_state = seed_states_for_team(conn, org_id, tid)
        conn.execute(team.update().where(team.c.id == tid).values(default_state_id=default_state))
        # No identity_credential row: this account has no password to store.
        return {
            "action": ACTION_BOOTSTRAPPED,
            "user_id": uid,
            "organization_id": org_id,
            "identity_id": identity_id,
            "team_id": tid,
        }

    # Set up already. The provisioned owner address is still authoritative: it
    # resolves to its own workspace even when the profile rows disagree.
    mapped = identity_links().get(address)
    if mapped:
        # An explicit invitation: this verified address is that existing profile.
        profile = conn.execute(
            select(user.c.id, user.c.organization_id, user.c.active)
            .where(user.c.email == mapped)
            .limit(1)
        ).first()
        if profile is None:
            # A typo in the mapping must not silently admit anyone else.
            return {"action": ACTION_DENIED, "reason": "profile_not_found"}
        return _claim_profile(conn, profile=profile, address=address, stamp=stamp)

    owner = owner_email()
    if owner and address == owner:
        return _admit_owner(
            conn, address=address, display=display, stamp=stamp, org_id=existing_org[0]
        )

    # Otherwise: match, never create a workspace-less account.
    identity = conn.execute(
        select(global_identity.c.id).where(global_identity.c.email == address)
    ).first()
    if identity is not None:
        membership = conn.execute(
            select(organization_membership.c.organization_id, organization_membership.c.profile_user_id)
            .where(
                organization_membership.c.identity_id == identity[0],
                organization_membership.c.active.is_(True),
            )
            .limit(1)
        ).first()
        if membership is None:
            return {"action": ACTION_DENIED, "reason": "no_membership"}
        return {
            "action": ACTION_LOGIN,
            "user_id": membership[1],
            "organization_id": membership[0],
            "identity_id": identity[0],
        }

    # A person who already exists with this address but has no identity row yet
    # (created by an invitation or by onboarding) is linked on first Google use.
    matched_user = conn.execute(
        select(user.c.id, user.c.organization_id).where(
            user.c.email == address, user.c.active.is_(True)
        ).limit(1)
    ).first()
    if matched_user is None:
        return {"action": ACTION_DENIED, "reason": "not_invited"}
    identity_id = new_id()
    conn.execute(global_identity.insert().values(id=identity_id, email=address, created_at=stamp))
    conn.execute(
        organization_membership.insert().values(
            id=new_id(),
            identity_id=identity_id,
            organization_id=matched_user[1],
            profile_user_id=matched_user[0],
            active=True,
            created_at=stamp,
        )
    )
    return {
        "action": ACTION_LOGIN,
        "user_id": matched_user[0],
        "organization_id": matched_user[1],
        "identity_id": identity_id,
        "linked": True,
    }
