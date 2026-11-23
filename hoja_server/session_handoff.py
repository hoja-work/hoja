"""Sign in on your phone: hand a signed-in browser's membership to the Hoja app (HOJA-733).

The computer is the trusted device and the phone is new, so the computer shows
the code and the phone scans it::

    create (browser session) -> pending --claim (phone)--> claimed
        --approve (same browser)--> approved --redeem (phone + verifier)--> consumed

* The QR carries ``<id>.<secret>`` in a URL *fragment*, so it never reaches a
  server log. Only hashes are stored.
* The first claim wins. A second claim from another phone (another challenge)
  burns the handoff: the secret has leaked, and nobody gets in.
* Approval names the phone, and must come from the browser session that made
  the code. The workspace IP allowlist is checked against the phone's address.
* Redeeming needs the PKCE verifier (RFC 7636 S256) whose challenge the phone
  sent on claim, so a photographed code is useless without the phone that
  claimed it.
* Every transition is one guarded ``UPDATE ... WHERE status = <expected>``;
  a rowcount other than 1 means somebody else got there first.

This is not a new *sign-in method*: it copies a session that already passed the
workspace's sign-in policy, so ``allowedSigninMethods`` does not gate it.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import and_, select, update

from hoja_server import identity_transfer
from hoja_server.db import organization, session_handoff, user

#: Seconds a fresh code can be claimed in. The browser rotates it every
#: ROTATE_AFTER seconds while the dialog is open, so a code on screen is
#: usually much younger than this.
PENDING_TTL = 90
ROTATE_AFTER = 30
#: Seconds the browser has to approve once a phone has claimed the code.
CLAIMED_TTL = 120
#: Seconds the phone has to redeem once the browser approved.
APPROVED_TTL = 60
POLL_INTERVAL = 2
#: Codes a single browser session may create per minute (rotation included).
CREATE_LIMIT = 5
#: The same alphabet as the device grant: no vowels, nothing that reads like another letter.
CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
CODE_LENGTH = 8
MAX_DEVICE_NAME = 64
PLATFORMS = ("android", "ios")
#: RFC 7636 verifier: 43-128 unreserved characters.
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")

PENDING, CLAIMED, APPROVED, CONSUMED = "pending", "claimed", "approved", "consumed"
DENIED, CANCELLED, BURNED, EXPIRED = "denied", "cancelled", "burned", "expired"
TERMINAL = frozenset({CONSUMED, DENIED, CANCELLED, BURNED, EXPIRED})


@dataclass
class HandoffError(Exception):
    """A refusal the HTTP layer turns into ``{"error", "reason"}``."""

    reason: str
    message: str
    status: int

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.reason}: {self.message}"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: object) -> str | None:
    """``bcdf-ghjk`` / ``BCDF GHJK`` -> ``BCDFGHJK``; anything else -> None."""
    text = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    if len(text) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in text):
        return None
    return text


def device_name(raw: object) -> str | None:
    text = " ".join(str(raw or "").split())[:MAX_DEVICE_NAME]
    return text or None


def session_label(row: dict) -> str:
    name = row.get("device_name") or ("Android phone" if row.get("platform") == "android"
                                      else "iPhone" if row.get("platform") == "ios" else "Phone")
    return f"{name} · Hoja app · via QR"


def _effective_status(row: dict, now: float) -> str:
    if row["status"] not in TERMINAL and row["expires_at"] <= now:
        return EXPIRED
    return row["status"]


def _public(row: dict, now: float) -> dict:
    status = _effective_status(row, now)
    device = None
    if row.get("claimed_at"):
        device = {"name": row.get("device_name"), "platform": row.get("platform"),
                  "ip": row.get("claim_ip"), "userAgent": row.get("claim_user_agent")}
    return {"id": row["id"], "status": status, "expiresAt": row["expires_at"], "device": device}


def _transition(conn, handoff_id: str, expected: str | tuple[str, ...], **values) -> bool:
    states = (expected,) if isinstance(expected, str) else expected
    return conn.execute(update(session_handoff).where(and_(
        session_handoff.c.id == handoff_id,
        session_handoff.c.status.in_(states))).values(**values)).rowcount == 1


def _load(conn, handoff_id: str, *, lock: bool = False) -> dict | None:
    query = select(session_handoff).where(session_handoff.c.id == str(handoff_id or ""))
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    return dict(row) if row else None


# --------------------------------------------------------------- computer side

def create(engine, *, session: dict, creator_ip: str | None, origin: str,
           replaces: str | None = None, now: float | None = None) -> dict:
    """A new code for ``session`` (a resolved browser_session row with identity_id)."""
    moment = time.time() if now is None else now
    with engine.begin() as conn:
        recent = conn.execute(select(session_handoff.c.id).where(and_(
            session_handoff.c.creator_session_id == session["id"],
            session_handoff.c.created_at > moment - 60))).all()
        if len(recent) >= CREATE_LIMIT:
            raise HandoffError("rate_limited", "Too many codes. Wait a minute and try again.", 429)
        if replaces:
            # Rotation: the code being replaced stops working, but only if this
            # same browser session made it.
            conn.execute(update(session_handoff).where(and_(
                session_handoff.c.id == str(replaces),
                session_handoff.c.creator_session_id == session["id"],
                session_handoff.c.status == PENDING)).values(status=CANCELLED, decided_at=moment))
        # Housekeeping: rows a day past expiry carry nothing anyone will read.
        conn.execute(session_handoff.delete().where(session_handoff.c.expires_at < moment - 86400))
        for _attempt in range(8):
            code = _new_code()
            if not conn.execute(select(session_handoff.c.id).where(
                    session_handoff.c.code == code)).first():
                break
        else:  # pragma: no cover - 20^8 codes, at most a few live at once
            raise HandoffError("busy", "Could not make a code. Try again.", 503)
        handoff_id = secrets.token_urlsafe(16)
        secret = secrets.token_urlsafe(32)
        conn.execute(session_handoff.insert().values(
            id=handoff_id, secret_hash=_digest(secret), code=code,
            organization_id=session["organization_id"], user_id=session["user_id"],
            identity_id=session.get("identity_id"), creator_session_id=session["id"],
            status=PENDING, creator_ip=creator_ip, created_at=moment,
            expires_at=moment + PENDING_TTL))
    return {
        "id": handoff_id, "secret": secret, "code": f"{code[:4]}-{code[4:]}",
        "qrUrl": f"{origin}/m/pair#{handoff_id}.{secret}",
        "expiresAt": moment + PENDING_TTL, "rotateAfter": ROTATE_AFTER,
    }


def status(engine, handoff_id: str, *, creator_session_id: str, now: float | None = None) -> dict:
    moment = time.time() if now is None else now
    with engine.connect() as conn:
        row = _load(conn, handoff_id)
    if row is None or row["creator_session_id"] != creator_session_id:
        raise HandoffError("unknown", "No such code.", 404)
    return _public(row, moment)


def decide(engine, handoff_id: str, decision: str, *, creator_session_id: str,
           approve_check=None, now: float | None = None) -> dict:
    """Approve, deny or cancel. ``approve_check(row)`` returns a HandoffError or None."""
    moment = time.time() if now is None else now
    if decision not in ("approve", "deny", "cancel"):
        raise HandoffError("bad_request", "decision must be approve, deny or cancel.", 400)
    refusal = None
    with engine.begin() as conn:
        row = _load(conn, handoff_id, lock=True)
        if row is None or row["creator_session_id"] != creator_session_id:
            raise HandoffError("unknown", "No such code.", 404)
        current = _effective_status(row, moment)
        if decision == "approve":
            if current != CLAIMED:
                raise HandoffError("wrong_state", f"This code is {current}.", 409)
            refusal = approve_check(row) if approve_check else None
            if refusal is not None:
                # The phone must hear "no", so the denial commits; the
                # refusal is raised once the transaction is closed.
                ok = _transition(conn, handoff_id, CLAIMED, status=DENIED, decided_at=moment)
                new = DENIED
            else:
                ok = _transition(conn, handoff_id, CLAIMED, status=APPROVED, decided_at=moment,
                                 expires_at=moment + APPROVED_TTL)
                new = APPROVED
        else:
            if current not in (PENDING, CLAIMED):
                raise HandoffError("wrong_state", f"This code is {current}.", 409)
            new = DENIED if decision == "deny" else CANCELLED
            ok = _transition(conn, handoff_id, (PENDING, CLAIMED), status=new, decided_at=moment)
        if not ok:
            raise HandoffError("wrong_state", "This code changed. Try again.", 409)
    if refusal is not None:
        raise refusal
    return {"status": new}


# ------------------------------------------------------------------ phone side

def claim(engine, *, handoff_id: str | None, secret: str | None, code: str | None,
          challenge: str | None, name: str | None, platform: str | None,
          ip: str | None, user_agent: str | None, now: float | None = None) -> dict:
    moment = time.time() if now is None else now
    challenge = identity_transfer.valid_challenge(challenge)
    if not challenge:
        raise HandoffError("bad_request", "challenge must be an S256 code challenge.", 400)
    burned = False
    with engine.begin() as conn:
        if code is not None:
            normalized = normalize_code(code)
            row = None
            if normalized:
                row = conn.execute(select(session_handoff).where(
                    session_handoff.c.code == normalized).with_for_update()).mappings().first()
                row = dict(row) if row else None
        else:
            row = _load(conn, handoff_id or "", lock=True)
            if row is not None and not hmac.compare_digest(_digest(str(secret or "")), row["secret_hash"]):
                row = None
        if row is None:
            raise HandoffError("unknown", "This code isn't valid. Show a new one on your computer.", 404)
        current = _effective_status(row, moment)
        if current in (CLAIMED, APPROVED) and row.get("challenge") == challenge:
            # The same phone retrying (lost response): answer again, change nothing.
            pass
        elif current in (CLAIMED, APPROVED):
            # Somebody else holds this code. It has leaked: nobody gets in. The
            # burn commits with this transaction; the refusal is raised after.
            _transition(conn, row["id"], (CLAIMED, APPROVED), status=BURNED, decided_at=moment)
            burned = True  # refused even if a racing request moved the row first
        elif current != PENDING:
            raise HandoffError("expired" if current == EXPIRED else current,
                               "This code has expired. Show a new one on your computer.", 410)
        elif not _transition(conn, row["id"], PENDING, status=CLAIMED, challenge=challenge,
                             device_name=device_name(name),
                             platform=platform if platform in PLATFORMS else None,
                             claim_ip=ip, claim_user_agent=(user_agent or "")[:512] or None,
                             claimed_at=moment, expires_at=moment + CLAIMED_TTL):
            raise HandoffError("already_claimed", "Another device already used this code.", 409)
        if burned:
            person = workspace = None
        else:
            person = conn.execute(select(
                user.c.email, user.c.name, user.c.display_name, user.c.avatar_url).where(and_(
                    user.c.id == row["user_id"],
                    user.c.organization_id == row["organization_id"]))).mappings().first()
            workspace = conn.execute(select(organization.c.name, organization.c.url_key).where(
                organization.c.id == row["organization_id"])).mappings().first()
    if burned:
        raise HandoffError("already_claimed",
                           "Another device already used this code. Show a new one on your computer.", 409)
    return {
        "id": row["id"], "status": CLAIMED, "interval": POLL_INTERVAL,
        "expiresAt": moment + CLAIMED_TTL if current == PENDING else row["expires_at"],
        "account": {"email": person["email"] if person else None,
                    "name": (person["display_name"] or person["name"]) if person else None,
                    "avatarUrl": person["avatar_url"] if person else None},
        "workspace": {"name": workspace["name"] if workspace else None,
                      "urlKey": workspace["url_key"] if workspace else None},
    }


def redeem(engine, *, handoff_id: str, verifier: str | None, now: float | None = None) -> dict:
    """``{"status": "claimed"}`` while waiting, or the consumed row to mint a session from."""
    moment = time.time() if now is None else now
    with engine.begin() as conn:
        row = _load(conn, handoff_id, lock=True)
        if row is None:
            raise HandoffError("unknown", "No such code.", 404)
        if (not isinstance(verifier, str) or not _VERIFIER.fullmatch(verifier) or not row.get("challenge")
                or not hmac.compare_digest(identity_transfer.challenge_for(verifier), row["challenge"])):
            raise HandoffError("verifier_mismatch", "This sign-in was started on another device.", 400)
        current = _effective_status(row, moment)
        if current == CLAIMED:
            return {"status": CLAIMED}
        if current == DENIED:
            raise HandoffError("denied", "The sign-in was declined on your computer.", 403)
        if current != APPROVED:
            raise HandoffError(current, f"This code is {current}. Show a new one on your computer.", 410)
        if not _transition(conn, handoff_id, APPROVED, status=CONSUMED, consumed_at=moment):
            raise HandoffError("consumed", "This code was already used.", 410)
    row["status"] = CONSUMED
    return row
