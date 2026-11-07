"""Persistence for the hosted sign-up surface: flows, sessions, eligibility.

Deliberately separate from :class:`hoja_server.store.Store`, which owns
workspace data. Nothing here can create a workspace: the strongest row this
module writes is a ``signup_eligibility`` row with ``status='eligible'`` and
``claimed_at IS NULL`` — a *request* the provisioner may pick up later.

Concurrency notes, because they are the whole safety story:

* ``consume_flow`` marks the flow row used with a guarded ``UPDATE``; a second
  callback with the same state matches no row and is refused.
* ``turnstile_use.token_hash`` is the primary key, so spending a token twice
  fails on insert rather than passing the gate.
* ``signup_eligibility`` has unique ``slug``, unique ``idempotency_key`` and
  unique ``(google_sub, slug)``, so a retried request cannot enqueue two rows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import (
    new_id,
    now_iso,
    signup_eligibility,
    signup_flow,
    signup_identity,
    signup_invite,
    signup_session,
    token_hash,
    turnstile_use,
)


def _one(conn, stmt):
    row = conn.execute(stmt).first()
    return dict(row._mapping) if row else None


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


class EligibilityConflict(RuntimeError):
    """A unique constraint says this slug or identity is already spoken for."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def idempotency_key_for(subject: str, slug: str) -> str:
    """Stable key for one (identity, slug) request: the provisioner's guard."""
    return hashlib.sha256(f"{subject}\x00{slug}".encode()).hexdigest()


class SignupStore:
    def __init__(self, engine):
        self.engine = engine

    # ---------------------------------------------------------------- flows

    def start_flow(
        self,
        *,
        state: str,
        binding: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
        return_to: str | None,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> str:
        moment = _now(now)
        flow_id = new_id()
        with self.engine.begin() as conn:
            conn.execute(
                signup_flow.insert().values(
                    id=flow_id,
                    state_hash=token_hash(state),
                    binding_hash=token_hash(binding),
                    nonce=nonce,
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                    return_to=return_to,
                    created_at=_utc_iso(moment),
                    expires_at=_utc_iso(moment + timedelta(seconds=ttl_seconds)),
                )
            )
        return flow_id

    def consume_flow(
        self,
        *,
        state: str,
        binding: str | None,
        now: datetime | None = None,
    ) -> tuple[dict | None, str]:
        """Single-use lookup for the callback. Returns ``(flow, reason)``.

        ``reason`` is ``ok``, ``state_unknown``, ``state_binding_mismatch``,
        ``state_replayed`` or ``state_expired``. A mismatched browser binding must
        not consume the flow: the rightful browser may still complete it.
        """
        if not state or not binding:
            return None, "state_unknown"
        moment = _now(now)
        marker = _utc_iso(moment)
        with self.engine.begin() as conn:
            row = _one(
                conn,
                select(signup_flow).where(signup_flow.c.state_hash == token_hash(state)),
            )
            if row is None:
                return None, "state_unknown"
            if not hmac.compare_digest(row["binding_hash"], token_hash(binding)):
                return None, "state_binding_mismatch"
            if row["consumed_at"]:
                return None, "state_replayed"
            if row["expires_at"] <= marker:
                return None, "state_expired"
            result = conn.execute(
                signup_flow.update()
                .where(
                    and_(
                        signup_flow.c.id == row["id"],
                        signup_flow.c.consumed_at.is_(None),
                    )
                )
                .values(consumed_at=marker)
            )
            if result.rowcount != 1:
                return None, "state_replayed"
        row["consumed_at"] = marker
        return row, "ok"

    # ------------------------------------------------------------- identity

    def upsert_identity(
        self,
        *,
        subject: str,
        email: str,
        email_verified: bool,
        name: str | None = None,
        picture: str | None = None,
        hosted_domain: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Insert or refresh the person. ``(provider, subject)`` is the key."""
        moment = _now(now)
        stamp = _utc_iso(moment)
        values = {
            "email": email,
            "email_verified": bool(email_verified),
            "name": name,
            "picture": picture,
            "hosted_domain": hosted_domain,
            "google_verified_at": stamp,
            "updated_at": stamp,
        }
        with self.engine.begin() as conn:
            existing = _one(
                conn,
                select(signup_identity).where(
                    and_(
                        signup_identity.c.provider == "google",
                        signup_identity.c.subject == subject,
                    )
                ),
            )
            if existing is not None:
                conn.execute(
                    signup_identity.update()
                    .where(signup_identity.c.id == existing["id"])
                    .values(**values)
                )
                return {**existing, **values}
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    signup_identity.insert().values(
                        id=new_id(),
                        provider="google",
                        subject=subject,
                        created_at=stamp,
                        **values,
                    )
                )
        except IntegrityError:
            pass  # a concurrent sign-in inserted it first; read below
        with self.engine.connect() as conn:
            row = _one(
                conn,
                select(signup_identity).where(
                    and_(
                        signup_identity.c.provider == "google",
                        signup_identity.c.subject == subject,
                    )
                ),
            )
        if row is None:  # pragma: no cover - only if the row vanished mid-flight
            raise EligibilityConflict("identity_missing", "Could not record the Google identity.")
        return row

    def identity_by_subject(self, subject: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_identity).where(
                    and_(
                        signup_identity.c.provider == "google",
                        signup_identity.c.subject == subject,
                    )
                ),
            )

    # ------------------------------------------------------------- sessions

    def create_session(
        self,
        *,
        identity_id: str,
        client_ip: str | None,
        user_agent: str | None,
        days: int,
        rotated_from: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, dict]:
        moment = _now(now)
        raw = secrets.token_urlsafe(48)
        row_id = new_id()
        with self.engine.begin() as conn:
            conn.execute(
                signup_session.insert().values(
                    id=row_id,
                    token_hash=token_hash(raw),
                    identity_id=identity_id,
                    created_at=_utc_iso(moment),
                    expires_at=_utc_iso(moment + timedelta(days=days)),
                    rotated_from=rotated_from,
                    client_ip=client_ip,
                    user_agent=(user_agent or "")[:512] or None,
                )
            )
        return raw, {
            "id": row_id,
            "identity_id": identity_id,
            "expires_at": _utc_iso(moment + timedelta(days=days)),
        }

    def rotate_session(
        self,
        *,
        previous_raw: str | None,
        identity_id: str,
        client_ip: str | None,
        user_agent: str | None,
        days: int,
        now: datetime | None = None,
    ) -> tuple[str, dict]:
        """Revoke the presented session and issue a fresh one, same transaction."""
        moment = _now(now)
        raw = secrets.token_urlsafe(48)
        row_id = new_id()
        marker = _utc_iso(moment)
        with self.engine.begin() as conn:
            if previous_raw:
                conn.execute(
                    signup_session.update()
                    .where(
                        and_(
                            signup_session.c.token_hash == token_hash(previous_raw),
                            signup_session.c.revoked_at.is_(None),
                        )
                    )
                    .values(revoked_at=marker)
                )
            conn.execute(
                signup_session.insert().values(
                    id=row_id,
                    token_hash=token_hash(raw),
                    identity_id=identity_id,
                    created_at=marker,
                    expires_at=_utc_iso(moment + timedelta(days=days)),
                    rotated_from=token_hash(previous_raw) if previous_raw else None,
                    client_ip=client_ip,
                    user_agent=(user_agent or "")[:512] or None,
                )
            )
        return raw, {
            "id": row_id,
            "identity_id": identity_id,
            "expires_at": _utc_iso(moment + timedelta(days=days)),
        }

    def resolve_session(self, raw: str | None, *, now: datetime | None = None) -> dict | None:
        """opaque cookie value → {session, identity} when live and unexpired."""
        if not raw:
            return None
        marker = _utc_iso(_now(now))
        with self.engine.connect() as conn:
            row = _one(
                conn,
                select(
                    signup_session.c.id.label("session_id"),
                    signup_session.c.expires_at.label("session_expires_at"),
                    signup_session.c.client_ip.label("session_client_ip"),
                    signup_identity.c.id.label("identity_id"),
                    signup_identity.c.subject,
                    signup_identity.c.email,
                    signup_identity.c.email_verified,
                    signup_identity.c.google_verified_at,
                    signup_identity.c.hosted_domain,
                )
                .join(signup_identity, signup_identity.c.id == signup_session.c.identity_id)
                .where(
                    and_(
                        signup_session.c.token_hash == token_hash(raw),
                        signup_session.c.revoked_at.is_(None),
                        signup_session.c.expires_at > marker,
                    )
                ),
            )
        return row

    def revoke_session(self, raw: str | None) -> None:
        if not raw:
            return
        with self.engine.begin() as conn:
            conn.execute(
                signup_session.update()
                .where(
                    and_(
                        signup_session.c.token_hash == token_hash(raw),
                        signup_session.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=now_iso())
            )

    # -------------------------------------------------------------- invites

    def active_invite_for(self, email: str, *, now: datetime | None = None) -> dict | None:
        marker = _utc_iso(_now(now))
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_invite).where(
                    and_(
                        signup_invite.c.email == (email or "").strip().lower(),
                        signup_invite.c.consumed_at.is_(None),
                        (signup_invite.c.expires_at.is_(None))
                        | (signup_invite.c.expires_at > marker),
                    )
                ),
            )

    def consume_invite(self, invite_id: str, identity_id: str, *, now: datetime | None = None) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                signup_invite.update()
                .where(
                    and_(
                        signup_invite.c.id == invite_id,
                        signup_invite.c.consumed_at.is_(None),
                    )
                )
                .values(consumed_at=_utc_iso(_now(now)), consumed_by_identity_id=identity_id)
            )
            return result.rowcount == 1

    def add_invite(
        self,
        *,
        email: str,
        note: str | None = None,
        created_by: str | None = None,
        expires_at: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        invite_id = new_id()
        with self.engine.begin() as conn:
            conn.execute(
                signup_invite.insert().values(
                    id=invite_id,
                    email=email.strip().lower(),
                    note=note,
                    created_by=created_by,
                    created_at=_utc_iso(_now(now)),
                    expires_at=expires_at,
                )
            )
        return {"id": invite_id, "email": email.strip().lower(), "expires_at": expires_at}

    # ------------------------------------------------------- human (single use)

    def consume_turnstile_token(
        self,
        *,
        token: str,
        identity_id: str | None,
        client_ip: str | None,
        hostname: str | None,
        now: datetime | None = None,
    ) -> bool:
        """Spend the token. False means it was already spent (a replay)."""
        if not token:
            return False
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    turnstile_use.insert().values(
                        token_hash=token_hash(token),
                        identity_id=identity_id,
                        client_ip=client_ip,
                        hostname=hostname,
                        consumed_at=_utc_iso(_now(now)),
                    )
                )
        except IntegrityError:
            return False
        return True

    # ------------------------------------------------------------ eligibility

    def eligibility_for(self, identity_id: str, slug: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_eligibility).where(
                    and_(
                        signup_eligibility.c.identity_id == identity_id,
                        signup_eligibility.c.slug == slug,
                    )
                ),
            )

    def latest_eligibility_for_identity(self, identity_id: str) -> dict | None:
        """The request this person should see on a fresh page load.

        An active workspace wins over a newer failed/duplicate row, so a
        completed workspace can never be hidden behind queue history.
        """
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_eligibility)
                .where(signup_eligibility.c.identity_id == identity_id)
                .order_by(
                    case((signup_eligibility.c.status == "active", 1), else_=0).desc(),
                    signup_eligibility.c.created_at.desc(),
                )
                .limit(1),
            )

    def eligibility_by_subject_slug(self, subject: str, slug: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_eligibility).where(
                    and_(
                        signup_eligibility.c.google_sub == subject,
                        signup_eligibility.c.slug == slug,
                    )
                ),
            )

    def eligibility_by_slug(self, slug: str) -> dict | None:
        with self.engine.connect() as conn:
            return _one(
                conn,
                select(signup_eligibility).where(signup_eligibility.c.slug == slug),
            )

    def eligibility_count(
        self,
        *,
        client_ip: str | None = None,
        google_sub: str | None = None,
        since: datetime | None = None,
    ) -> int:
        statement = select(func.count()).select_from(signup_eligibility)
        conditions = []
        if client_ip is not None:
            conditions.append(signup_eligibility.c.client_ip == client_ip)
        if google_sub is not None:
            conditions.append(signup_eligibility.c.google_sub == google_sub)
        if since is not None:
            conditions.append(signup_eligibility.c.created_at >= _utc_iso(since))
        if conditions:
            statement = statement.where(and_(*conditions))
        with self.engine.connect() as conn:
            return int(conn.execute(statement).scalar_one() or 0)

    def record_eligibility(
        self,
        *,
        identity: dict,
        slug: str,
        client_ip: str,
        turnstile_hostname: str | None,
        now: datetime | None = None,
    ) -> tuple[dict, bool]:
        """Enqueue one workspace request. Returns ``(row, created)``.

        ``created`` is False when an identical request was already recorded, so
        a retried POST is idempotent instead of a second queue entry. Raises
        :class:`EligibilityConflict` when the slug belongs to someone else.
        """
        subject = identity["subject"]
        identity_id = identity.get("identity_id") or identity["id"]
        key = idempotency_key_for(subject, slug)
        existing = self.eligibility_for(identity_id, slug)
        if existing is not None:
            return existing, False
        row_id = new_id()
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    signup_eligibility.insert().values(
                        id=row_id,
                        idempotency_key=key,
                        identity_id=identity_id,
                        google_sub=subject,
                        email=identity["email"],
                        slug=slug,
                        status="eligible",
                        client_ip=client_ip or "unknown",
                        turnstile_hostname=turnstile_hostname,
                        created_at=_utc_iso(_now(now)),
                    )
                )
        except IntegrityError:
            row = self.eligibility_by_slug(slug)
            if row is not None and row["google_sub"] == subject:
                return row, False
            raise EligibilityConflict(
                "slug_taken", f"{slug!r} was just taken. Pick another name."
            ) from None
        with self.engine.connect() as conn:
            row = _one(conn, select(signup_eligibility).where(signup_eligibility.c.id == row_id))
        return row, True

    def taken_slugs(self, registry_path: str | None = None) -> set[str]:
        """Slugs the provisioner already owns, plus every queued request.

        ``registry_path`` is Hoja-Cloud's ``tenants/registry.json`` when the
        deployment points ``HOJA_TENANT_REGISTRY`` at it; absent, the gate only
        knows what it has seen itself.
        """
        with self.engine.connect() as conn:
            taken = {row[0] for row in conn.execute(select(signup_eligibility.c.slug))}
        if registry_path:
            try:
                document = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = None
            tenants = document.get("tenants") if isinstance(document, dict) else None
            if isinstance(tenants, list):
                for tenant in tenants:
                    if isinstance(tenant, dict) and isinstance(tenant.get("slug"), str):
                        taken.add(tenant["slug"])
        return taken
