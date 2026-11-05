"""Turn a verified Google address into an app identity on *this* instance.

Two jobs, deliberately in one place because they must agree on what "first
user" means:

* a database that has never been set up gets its first user from the verified
  address, as the workspace admin, with no password ever stored;
* a database that is already set up only ever *matches* an address. Google
  proves who you are; it does not grant access to someone else's workspace.
  An unknown or uninvited address is refused, not created.

The caller owns the transaction so that "is this instance empty?" and the rows
that answer it are decided atomically.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

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
    address = (email or "").strip().lower()
    if "@" not in address:
        return {"action": ACTION_DENIED, "reason": "invalid_email"}
    if not subject:
        return {"action": ACTION_DENIED, "reason": "missing_subject"}

    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat()
    display = (name or "").strip() or address.split("@", 1)[0]

    existing_org = conn.execute(select(organization.c.id).limit(1)).first()

    if existing_org is None:
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

    # Set up already: match, never create a workspace-less account.
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
