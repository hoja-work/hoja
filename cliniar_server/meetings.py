"""Meeting service: scheduling, guest links, waiting room, moderation, chat and SFU bookkeeping.

Every state change goes through ``meeting_policy`` (ABAC); this module only gathers the
attributes, applies the change and reports *who must be told* via ``Event`` objects (the
realtime hub in ``meetings_api`` delivers them). All methods are synchronous and are meant
to be run in a worker thread.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Engine

from cliniar_server import meeting_policy as P
from cliniar_server.abac import Decision
from cliniar_server.db import (
    meeting,
    meeting_chat,
    meeting_invitee,
    meeting_ring,
    new_id,
    now_iso,
    token_hash,
    user,
)
from cliniar_server.db import (
    meeting_participant as mp,
)
from cliniar_server.store import team_visible, visible_team_clause

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz"
CODE_RE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{4}$")
TRACK_RE = re.compile(r"^(mic|cam|screen)-[A-Za-z0-9]{4,24}$")
TRACK_ACTION = {"mic": P.AUDIO, "cam": P.VIDEO, "screen": P.SCREEN}
ACCESS = ("invited", "team", "org")
SCREEN_SHARE = ("all", "host")
RECORDING = ("off", "allowed", "recording")
EMOJI = ("👍", "👏", "❤️", "😂", "🎉", "😮", "🙏", "✋")
DEFAULT_MAX_PARTICIPANTS = 25
MAX_TITLE = 200
MAX_CHAT = 2000
MAX_NAME = 80
CHAT_HISTORY = 100

SETTING_FIELDS = {
    "waitingRoom": "waiting_room", "joinBeforeHost": "join_before_host", "muteOnEntry": "mute_on_entry",
    "allowUnmute": "allow_unmute", "screenShare": "screen_share", "chatEnabled": "chat_enabled",
    "locked": "locked",
}
MANAGE_FIELDS = {
    "title": "title", "description": "description", "scheduledStart": "scheduled_start",
    "durationMinutes": "duration_minutes", "access": "access", "teamId": "team_id", "issueId": "issue_id",
    "projectId": "project_id", "linkEnabled": "link_enabled", "maxParticipants": "max_participants",
    "recording": "recording",
}


def participant_cap() -> int:
    try:
        return max(2, int(os.environ.get("CLINIAR_MEETING_MAX_PARTICIPANTS", "100")))
    except ValueError:
        return 100


class MeetingError(Exception):
    status = 400


class NotFound(MeetingError):
    status = 404


class Invalid(MeetingError):
    status = 400


class Conflict(MeetingError):
    """The request is fine but needs a decision first (e.g. already in the meeting on another device)."""

    status = 409

    def __init__(self, message: str, payload: dict) -> None:
        super().__init__(message)
        self.payload = payload


class Denied(MeetingError):
    status = 403

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass
class Principal:
    kind: str  # "member" | "guest"
    user_id: str | None = None
    org_id: str | None = None
    key_access: str | None = None
    guest_token: str | None = None
    seat_token: str | None = None  # a member's companion-device seat (their main seat needs none)


@dataclass
class Event:
    """A message for people *in* the meeting (delivered by participant id over the room socket)."""

    to: frozenset
    msg: dict


@dataclass
class Notice:
    """A message for people *outside* the room (delivered by user id over the personal inbox socket)."""

    users: frozenset
    msg: dict


RING_SECONDS = 45
REMINDER_MINUTES = 5
RSVP_VALUES = ("accepted", "declined", "tentative")
EGRESS_USD_PER_GB = 0.05


def new_code() -> str:
    pick = lambda n: "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"{pick(3)}-{pick(4)}-{pick(4)}"


def normalize_code(code: str) -> str:
    code = (code or "").strip().lower()
    if not CODE_RE.match(code):
        raise NotFound("Meeting not found.")
    return code


def new_passcode() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _passcode_hash(meeting_id: str, passcode: str) -> str:
    return token_hash(f"{meeting_id}:{passcode}")


def _clean_text(value, limit: int, field: str, *, required=False) -> str | None:
    if value is None:
        if required:
            raise Invalid(f"{field} is required.")
        return None
    text = "".join(ch for ch in str(value) if ch.isprintable() or ch in "\n\t").strip()
    if required and not text:
        raise Invalid(f"{field} is required.")
    if len(text) > limit:
        raise Invalid(f"{field} must be at most {limit} characters.")
    return text


def _seconds(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()))
    except ValueError:
        return None


def _ics_text(value: str) -> str:
    return (str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def _ics_fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    raw, out = line.encode(), []
    while len(raw) > 75:
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # never split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode())
        raw = b" " + raw[cut:]
    out.append(raw.decode())
    return "\r\n".join(out)


def _parse_start(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise Invalid("scheduledStart must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class MeetingService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ------------------------------------------------------------------ attributes
    def _meeting(self, conn, code: str):
        row = conn.execute(select(meeting).where(meeting.c.code == normalize_code(code))).mappings().first()
        if row is None:
            raise NotFound("Meeting not found.")
        return row

    def _me(self, conn, m, principal: Principal):
        if principal.kind == "member":
            if principal.seat_token:  # a companion device presents the seat it was given
                return conn.execute(select(mp).where(and_(
                    mp.c.meeting_id == m["id"], mp.c.user_id == principal.user_id,
                    mp.c.token_hash == token_hash(principal.seat_token)))).mappings().first()
            return conn.execute(select(mp).where(and_(
                mp.c.meeting_id == m["id"], mp.c.user_id == principal.user_id, mp.c.device_of.is_(None)))
                .order_by(mp.c.created_at).limit(1)).mappings().first()
        if principal.guest_token:
            return conn.execute(select(mp).where(and_(mp.c.meeting_id == m["id"],
                                                      mp.c.token_hash == token_hash(principal.guest_token)))
                                ).mappings().first()
        return None

    def _subject(self, conn, m, principal: Principal, me, *, passcode: str | None = None) -> dict:
        s: dict = {"kind": principal.kind, "bound": me is not None,
                   "passcode_ok": bool(passcode) and m["passcode_hash"] == _passcode_hash(m["id"], passcode)}
        if me is not None:
            s.update(role=me["role"], state=me["state"], was_admitted=me["joined_at"] is not None,
                     forced_mute=bool(me["muted_by_host"]) and not m["allow_unmute"])
        if principal.kind != "member":
            return s
        u = conn.execute(select(user.c.admin, user.c.email, user.c.active).where(and_(
            user.c.id == principal.user_id, user.c.organization_id == principal.org_id))).mappings().first()
        same_org = bool(u and u["active"] is not False and principal.org_id == m["organization_id"])
        s.update(same_org=same_org, admin=bool(u and u["admin"]), key_access=principal.key_access,
                 is_host=principal.user_id == m["host_id"])
        if same_org:
            clauses = [meeting_invitee.c.user_id == principal.user_id]
            if u["email"]:
                clauses.append(func.lower(meeting_invitee.c.email) == u["email"].lower())
            inv = conn.execute(select(meeting_invitee.c.role).where(and_(
                meeting_invitee.c.meeting_id == m["id"], or_(*clauses))).limit(1)).mappings().first()
            s.update(invited=inv is not None, invite_role=inv["role"] if inv else None,
                     team_visible=bool(m["team_id"]) and team_visible(
                         conn, m["organization_id"], principal.user_id, m["team_id"]))
        return s

    def _resource(self, conn, m, *, target=None, actor=None) -> dict:
        counts = conn.execute(select(mp.c.role, func.count()).where(and_(
            mp.c.meeting_id == m["id"], mp.c.state == "admitted")).group_by(mp.c.role)).all()
        admitted = sum(n for _, n in counts)
        r = {"status": m["status"], "access": m["access"], "locked": m["locked"], "link_enabled": m["link_enabled"],
             "waiting_room": m["waiting_room"], "join_before_host": m["join_before_host"],
             "passcode_required": bool(m["passcode_hash"]), "full": admitted >= m["max_participants"],
             "host_present": any(role in ("host", "cohost") for role, _ in counts),
             "screen_share": m["screen_share"], "chat_enabled": m["chat_enabled"], "recording": m["recording"]}
        if target is not None:
            r["target_role"] = target["role"]
            r["target_self"] = actor is not None and target["id"] == actor["id"]
        return r

    def _require(self, action: str, subject: dict, resource: dict) -> Decision:
        decision = P.decide(action, subject, resource)
        if not decision:
            raise Denied(decision)
        return decision

    def _audience(self, conn, m) -> dict[str, frozenset]:
        rows = conn.execute(select(mp.c.id, mp.c.role, mp.c.state).where(and_(
            mp.c.meeting_id == m["id"], mp.c.state.in_(("admitted", "waiting"))))).all()
        return {
            "room": frozenset(i for i, _, st in rows if st == "admitted"),
            "mods": frozenset(i for i, ro, st in rows if st == "admitted" and ro in ("host", "cohost")),
            "everyone": frozenset(i for i, _, _ in rows),
        }

    # ------------------------------------------------------------------ serializers
    @staticmethod
    def pub(p) -> dict:
        return {"id": p["id"], "name": p["display_name"], "kind": p["kind"], "role": p["role"], "state": p["state"],
                "muted": bool(p["muted_by_host"]), "handRaised": bool(p["hand_raised"]),
                "audio": bool(p["audio_on"]), "video": bool(p["video_on"]), "screen": bool(p["screen_on"]),
                "sessionId": p["sfu_session_id"], "tracks": json.loads(p["tracks"] or "[]"),
                "deviceOf": p["device_of"], "device": p["device_label"]}

    def ser_meeting(self, conn, m, *, detail: bool = True) -> dict:
        host = conn.execute(select(user.c.name, user.c.display_name).where(user.c.id == m["host_id"])
                            ).mappings().first() or {}
        out = {"id": m["id"], "code": m["code"], "title": m["title"], "description": m["description"],
               "hostId": m["host_id"], "hostName": host.get("display_name") or host.get("name"),
               "teamId": m["team_id"], "issueId": m["issue_id"], "projectId": m["project_id"],
               "access": m["access"], "linkEnabled": bool(m["link_enabled"]), "hasPasscode": bool(m["passcode_hash"]),
               "status": m["status"], "scheduledStart": m["scheduled_start"], "durationMinutes": m["duration_minutes"],
               "startedAt": m["started_at"], "endedAt": m["ended_at"], "createdAt": m["created_at"],
               "durationSeconds": _seconds(m["started_at"], m["ended_at"]), "joinPath": f"/meet/{m['code']}",
               "settings": {"waitingRoom": bool(m["waiting_room"]), "joinBeforeHost": bool(m["join_before_host"]),
                            "muteOnEntry": bool(m["mute_on_entry"]), "allowUnmute": bool(m["allow_unmute"]),
                            "screenShare": m["screen_share"], "chatEnabled": bool(m["chat_enabled"]),
                            "locked": bool(m["locked"]), "recording": m["recording"],
                            "maxParticipants": m["max_participants"]}}
        if detail:
            out["participantCount"] = conn.execute(select(func.count()).where(and_(
                mp.c.meeting_id == m["id"], mp.c.state == "admitted"))).scalar()
        return out

    # ------------------------------------------------------------------ create / read
    def create(self, principal: Principal, data: dict) -> tuple[dict, str | None, list]:
        if principal.kind != "member":
            raise Denied(Decision(False, "Sign in to create a meeting."))
        with self.engine.begin() as conn:
            subject = self._subject(conn, {"organization_id": principal.org_id, "host_id": None, "id": None,
                                           "passcode_hash": None, "allow_unmute": True, "team_id": None},
                                    principal, None)
            self._require(P.CREATE, subject, {})
            values, passcode = self._fresh_values(conn, principal, data)
            for _ in range(8):
                values["code"] = new_code()
                if conn.execute(select(meeting.c.id).where(meeting.c.code == values["code"])).first() is None:
                    break
            else:
                raise Invalid("Could not allocate a meeting code. Try again.")
            conn.execute(meeting.insert().values(**values))
            if passcode:
                conn.execute(update(meeting).where(meeting.c.id == values["id"]).values(
                    passcode_hash=_passcode_hash(values["id"], passcode)))
            m = conn.execute(select(meeting).where(meeting.c.id == values["id"])).mappings().first()
            fresh = self._add_invitees(conn, m, principal.user_id, data.get("invitees") or [], "attendee")
            return self.ser_meeting(conn, m), passcode, self._invited_notices(conn, m, principal.user_id, fresh)

    def _fresh_values(self, conn, principal: Principal, data: dict) -> tuple[dict, str | None]:
        ts = now_iso()
        v = {"id": new_id(), "organization_id": principal.org_id, "host_id": principal.user_id,
             "title": _clean_text(data.get("title") or "Meeting", MAX_TITLE, "title", required=True),
             "description": _clean_text(data.get("description"), 5000, "description"), "access": "invited",
             "link_enabled": False, "waiting_room": True, "join_before_host": False, "mute_on_entry": True,
             "allow_unmute": True, "screen_share": "all", "chat_enabled": True, "locked": False,
             "recording": "off", "max_participants": DEFAULT_MAX_PARTICIPANTS, "status": "scheduled",
             "scheduled_start": None, "duration_minutes": None, "team_id": None, "issue_id": None,
             "project_id": None, "created_at": ts, "updated_at": ts}
        v.update(self._clean_fields(conn, principal, {**{k: data[k] for k in SETTING_FIELDS if k in data},
                                                       **{k: data[k] for k in MANAGE_FIELDS
                                                          if k in data and k != "title" and k != "description"}}))
        if v["access"] == "team" and not v["team_id"]:
            raise Invalid("A team is required for team meetings.")
        passcode = self._wanted_passcode(data.get("passcode"))
        if v["link_enabled"] and not v["waiting_room"] and not passcode:
            passcode = new_passcode()  # a public link needs a waiting room or a passcode
        return v, passcode

    @staticmethod
    def _wanted_passcode(value) -> str | None:
        if value is True:
            return new_passcode()
        if isinstance(value, str) and value:
            if not 4 <= len(value) <= 10 or not value.isalnum():
                raise Invalid("Passcode must be 4-10 letters or digits.")
            return value
        return None

    def _clean_fields(self, conn, principal: Principal, patch: dict) -> dict:
        out: dict = {}
        for key, col in {**SETTING_FIELDS, **MANAGE_FIELDS}.items():
            if key not in patch:
                continue
            val = patch[key]
            if col in ("waiting_room", "join_before_host", "mute_on_entry", "allow_unmute", "chat_enabled",
                       "locked", "link_enabled"):
                if not isinstance(val, bool):
                    raise Invalid(f"{key} must be true or false.")
            elif col == "access" and val not in ACCESS:
                raise Invalid(f"access must be one of {', '.join(ACCESS)}.")
            elif col == "screen_share" and val not in SCREEN_SHARE:
                raise Invalid("screenShare must be 'all' or 'host'.")
            elif col == "recording" and val not in RECORDING[:2]:
                raise Invalid("recording must be 'off' or 'allowed'.")
            elif col == "scheduled_start":
                val = _parse_start(val)
            elif col == "duration_minutes":
                if val is not None and (not isinstance(val, int) or isinstance(val, bool) or not 5 <= val <= 1440):
                    raise Invalid("durationMinutes must be between 5 and 1440.")
            elif col == "max_participants":
                if not isinstance(val, int) or isinstance(val, bool) or not 2 <= val <= participant_cap():
                    raise Invalid(f"maxParticipants must be between 2 and {participant_cap()}.")
            elif col == "team_id":
                if val is not None and not team_visible(conn, principal.org_id, principal.user_id, str(val)):
                    raise Invalid("Unknown team.")
            elif col == "title":
                val = _clean_text(val, MAX_TITLE, "title", required=True)
            elif col == "description":
                val = _clean_text(val, 5000, "description")
            elif col in ("issue_id", "project_id"):
                val = str(val) if val else None
            out[col] = val
        return out

    def get(self, code: str, principal: Principal) -> dict:
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            self._require(P.VIEW, subject, self._resource(conn, m))
            out = self.ser_meeting(conn, m)
            if m["status"] in ("ended", "cancelled"):
                out["recap"] = self._recap(conn, m)
            if subject.get("is_host") or subject.get("admin"):
                out["usage"] = self._usage(conn, m)
            out["isHost"] = bool(subject.get("is_host"))
            if subject.get("kind") == "member" and subject.get("same_org"):
                out["myResponse"] = "accepted" if subject.get("is_host") else self._my_responses(
                    conn, principal).get(m["id"])
            return out

    def _recap(self, conn, m) -> dict:
        """After a meeting: when it ran, for how long and who came (each device listed once)."""
        rows = conn.execute(select(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.joined_at.is_not(None)))
                            .order_by(mp.c.joined_at)).mappings().all()
        end = m["ended_at"]
        people = []
        for r in rows:
            left = r["left_at"] or end
            people.append({"name": r["display_name"], "kind": r["kind"], "role": r["role"],
                           "device": r["device_label"] if r["device_of"] else None,
                           "joinedAt": r["joined_at"], "leftAt": left, "seconds": _seconds(r["joined_at"], left)})
        return {"startedAt": m["started_at"], "endedAt": end, "durationSeconds": _seconds(m["started_at"], end),
                "attendees": people,
                "peopleCount": len({r["user_id"] or r["id"] for r in rows})}

    def _usage(self, conn, m) -> dict:
        """What this meeting cost: Cloudflare bills the bytes it sends *to* participants."""
        down, up = conn.execute(select(func.coalesce(func.sum(mp.c.bytes_in), 0), func.coalesce(
            func.sum(mp.c.bytes_out), 0)).where(mp.c.meeting_id == m["id"])).one()
        return {"egressBytes": int(down), "ingressBytes": int(up),
                "estimatedCostUsd": round(int(down) / 1e9 * EGRESS_USD_PER_GB, 4)}

    def preview(self, code: str) -> dict:
        """What an anonymous visitor with the link may see; uniform 404 otherwise."""
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            if not P.decide(P.PREVIEW, {"kind": "guest"}, self._resource(conn, m)):
                raise NotFound("Meeting not found.")
            host = conn.execute(select(user.c.name, user.c.display_name).where(user.c.id == m["host_id"])
                                ).mappings().first() or {}
            return {"code": m["code"], "title": m["title"], "status": m["status"],
                    "endedAt": m["ended_at"], "durationSeconds": _seconds(m["started_at"], m["ended_at"]),
                    "hostName": host.get("display_name") or host.get("name"),
                    "scheduledStart": m["scheduled_start"], "passcodeRequired": bool(m["passcode_hash"]),
                    "waitingRoom": bool(m["waiting_room"])}

    def _blank_meeting(self, principal: Principal) -> dict:
        return {"organization_id": principal.org_id, "host_id": None, "id": None, "passcode_hash": None,
                "allow_unmute": True, "team_id": None}

    def _involved(self, conn, principal: Principal):
        """SQL clause for meetings this member may see: hosting, invited, took part, workspace- or team-wide."""
        email = conn.execute(select(user.c.email).where(user.c.id == principal.user_id)).scalar()
        invited = select(meeting_invitee.c.meeting_id).where(or_(
            meeting_invitee.c.user_id == principal.user_id,
            *([func.lower(meeting_invitee.c.email) == email.lower()] if email else [])))
        took_part = select(mp.c.meeting_id).where(mp.c.user_id == principal.user_id)
        team_clause = visible_team_clause(conn, principal.org_id, principal.user_id, meeting.c.team_id)
        team_access = and_(meeting.c.access == "team", meeting.c.team_id.is_not(None),
                           team_clause if team_clause is not None else True)
        return and_(meeting.c.organization_id == principal.org_id,
                    or_(meeting.c.host_id == principal.user_id, meeting.c.id.in_(invited),
                        meeting.c.id.in_(took_part), meeting.c.access == "org", team_access))

    def _member_listing(self, conn, principal: Principal) -> None:
        if principal.kind != "member":
            raise Denied(Decision(False, "Sign in to list meetings."))
        self._require(P.LIST, self._subject(conn, self._blank_meeting(principal), principal, None), {})

    def _my_responses(self, conn, principal: Principal) -> dict[str, str]:
        email = conn.execute(select(user.c.email).where(user.c.id == principal.user_id)).scalar()
        rows = conn.execute(select(meeting_invitee.c.meeting_id, meeting_invitee.c.response).where(or_(
            meeting_invitee.c.user_id == principal.user_id,
            *([func.lower(meeting_invitee.c.email) == email.lower()] if email else [])))).all()
        return {mid: resp for mid, resp in rows}

    def list(self, principal: Principal, scope: str = "upcoming", limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.engine.connect() as conn:
            self._member_listing(conn, principal)
            states = ("scheduled", "live") if scope == "upcoming" else ("ended", "cancelled")
            order = (meeting.c.scheduled_start.asc().nulls_last(), meeting.c.created_at.desc()) \
                if scope == "upcoming" else (meeting.c.created_at.desc(),)
            rows = conn.execute(select(meeting).where(and_(
                self._involved(conn, principal), meeting.c.status.in_(states)))
                .order_by(*order).limit(limit)).mappings().all()
            mine = self._my_responses(conn, principal)
            return [self._with_me(conn, m, principal, mine) for m in rows]

    def _with_me(self, conn, m, principal: Principal, mine: dict) -> dict:
        out = self.ser_meeting(conn, m, detail=False)
        out["isHost"] = m["host_id"] == principal.user_id
        out["myResponse"] = "accepted" if out["isHost"] else mine.get(m["id"])
        return out

    def calendar(self, principal: Principal, start: str, end: str) -> list[dict]:
        """Meetings that start (or, when unscheduled, were created) in [start, end)."""
        lo, hi = _parse_start(start), _parse_start(end)
        if not lo or not hi or hi <= lo:
            raise Invalid("from and to must be ISO-8601 timestamps with from < to.")
        if datetime.fromisoformat(hi) - datetime.fromisoformat(lo) > timedelta(days=100):
            raise Invalid("The calendar range is limited to 100 days.")
        with self.engine.connect() as conn:
            self._member_listing(conn, principal)
            rows = conn.execute(select(meeting).where(and_(
                self._involved(conn, principal), meeting.c.status != "cancelled",
                or_(and_(meeting.c.scheduled_start >= lo, meeting.c.scheduled_start < hi),
                    and_(meeting.c.scheduled_start.is_(None), meeting.c.created_at >= lo,
                         meeting.c.created_at < hi)))).order_by(meeting.c.scheduled_start.asc().nulls_last(),
                                                                 meeting.c.created_at).limit(500)).mappings().all()
            mine = self._my_responses(conn, principal)
            return [self._with_me(conn, m, principal, mine) for m in rows]

    def invitations(self, principal: Principal) -> list[dict]:
        """Meetings I was invited to and have not answered yet."""
        with self.engine.connect() as conn:
            self._member_listing(conn, principal)
            email = conn.execute(select(user.c.email).where(user.c.id == principal.user_id)).scalar()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
            rows = conn.execute(
                select(meeting, meeting_invitee.c.invited_by).select_from(
                    meeting.join(meeting_invitee, meeting_invitee.c.meeting_id == meeting.c.id)).where(and_(
                        meeting.c.organization_id == principal.org_id, meeting_invitee.c.response == "needsAction",
                        meeting.c.status.in_(("scheduled", "live")), meeting.c.host_id != principal.user_id,
                        or_(meeting.c.scheduled_start.is_(None), meeting.c.scheduled_start >= cutoff),
                        or_(meeting_invitee.c.user_id == principal.user_id,
                            *([func.lower(meeting_invitee.c.email) == email.lower()] if email else []))))
                .order_by(meeting.c.scheduled_start.asc().nulls_last()).limit(100)).mappings().all()
            out = []
            for r in rows:
                item = self.ser_meeting(conn, r, detail=False)
                item["myResponse"] = "needsAction"
                inviter = conn.execute(select(user.c.name, user.c.display_name).where(
                    user.c.id == r["invited_by"])).mappings().first() or {}
                item["invitedBy"] = inviter.get("display_name") or inviter.get("name")
                out.append(item)
            return out

    def people(self, principal: Principal) -> list[dict]:
        with self.engine.connect() as conn:
            self._member_listing(conn, principal)
            rows = conn.execute(select(user.c.id, user.c.name, user.c.display_name, user.c.email, user.c.avatar_url)
                                .where(and_(user.c.organization_id == principal.org_id,
                                            user.c.active.is_not(False), user.c.archived_at.is_(None)))
                                .order_by(func.lower(user.c.name)).limit(1000)).mappings().all()
            return [{"id": r["id"], "name": r["display_name"] or r["name"], "email": r["email"],
                     "avatarUrl": r["avatar_url"]} for r in rows]

    # ------------------------------------------------------------------ update / invite
    def update(self, code: str, principal: Principal, patch: dict) -> tuple[dict, str | None, list[Event]]:
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            resource = self._resource(conn, m)
            manage_only = {k for k in patch if k in MANAGE_FIELDS} | ({"passcode"} if "passcode" in patch else set())
            unknown = set(patch) - set(MANAGE_FIELDS) - set(SETTING_FIELDS) - {"passcode"}
            if unknown:
                raise Invalid(f"Unknown fields: {', '.join(sorted(unknown))}.")
            self._require(P.MANAGE if manage_only else P.SETTINGS, subject, resource)
            values = self._clean_fields(conn, principal, patch)
            passcode = None
            if "passcode" in patch:
                if patch["passcode"] in (False, None, ""):
                    values["passcode_hash"] = None
                else:
                    passcode = self._wanted_passcode(patch["passcode"])
                    values["passcode_hash"] = _passcode_hash(m["id"], passcode)
            access = values.get("access", m["access"])
            team_id = values.get("team_id", m["team_id"])
            if access == "team" and not team_id:
                raise Invalid("A team is required for team meetings.")
            link = values.get("link_enabled", m["link_enabled"])
            room = values.get("waiting_room", m["waiting_room"])
            has_code = values["passcode_hash"] if "passcode_hash" in values else m["passcode_hash"]
            if link and not room and not has_code:
                passcode = new_passcode()
                values["passcode_hash"] = _passcode_hash(m["id"], passcode)
            if values:
                values["updated_at"] = now_iso()
                conn.execute(update(meeting).where(meeting.c.id == m["id"]).values(**values))
            before = m
            m = conn.execute(select(meeting).where(meeting.c.id == m["id"])).mappings().first()
            events: list = [Event(self._audience(conn, m)["everyone"],
                                  {"t": "meeting", "meeting": self.ser_meeting(conn, m)})]
            if any(before[k] != m[k] for k in ("title", "scheduled_start", "duration_minutes")):
                events.append(self._notice_invitees(conn, m, {"t": "meeting_updated",
                                                              "meeting": self.ser_meeting(conn, m, detail=False)},
                                                    exclude={principal.user_id}))
            return self.ser_meeting(conn, m), passcode, events

    def cancel(self, code: str, principal: Principal) -> list[Event]:
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            self._require(P.MANAGE, self._subject(conn, m, principal, me), self._resource(conn, m))
            events = self._finish(conn, m, "cancelled" if m["status"] == "scheduled" else "ended")
            events.append(self._notice_invitees(conn, m, {"t": "meeting_cancelled", "code": m["code"],
                                                          "title": m["title"]}, exclude={principal.user_id}))
            return events

    def add_invitees(self, code: str, principal: Principal, invitees: list,
                     role: str = "attendee") -> tuple[list[dict], list]:
        if role not in ("attendee", "cohost"):
            raise Invalid("role must be 'attendee' or 'cohost'.")
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            self._require(P.INVITE, subject, self._resource(conn, m))
            if role == "cohost":
                self._require(P.MANAGE, subject, self._resource(conn, m))
            fresh = self._add_invitees(conn, m, principal.user_id, invitees, role)
            return self._invitees(conn, m), self._invited_notices(conn, m, principal.user_id, fresh)

    def _add_invitees(self, conn, m, inviter_id, invitees: list, role: str) -> list[str]:
        """Record invitees; returns the workspace members newly invited (they get a notification)."""
        fresh: list[str] = []
        if not isinstance(invitees, list) or len(invitees) > 200:
            raise Invalid("invitees must be a list of at most 200 emails or user ids.")
        for raw in invitees:
            ref = str(raw or "").strip()
            if not ref:
                continue
            row = conn.execute(select(user.c.id).where(and_(
                user.c.organization_id == m["organization_id"],
                or_(user.c.id == ref, func.lower(user.c.email) == ref.lower())))).first()
            if row is None and "@" not in ref:
                raise Invalid(f"Unknown invitee {ref!r}.")
            uid, email = (row[0], None) if row else (None, ref.lower()[:254])
            dup = conn.execute(select(meeting_invitee.c.id).where(and_(
                meeting_invitee.c.meeting_id == m["id"],
                meeting_invitee.c.user_id == uid if uid else func.lower(meeting_invitee.c.email) == email))).first()
            if dup:
                if role == "cohost":
                    conn.execute(update(meeting_invitee).where(meeting_invitee.c.id == dup[0]).values(role=role))
                continue
            conn.execute(meeting_invitee.insert().values(
                id=new_id(), meeting_id=m["id"], user_id=uid, email=email, role=role, invited_by=inviter_id,
                created_at=now_iso()))
            if uid and uid != inviter_id:
                fresh.append(uid)
        return fresh

    def _invitees(self, conn, m) -> list[dict]:
        rows = conn.execute(select(meeting_invitee, user.c.name, user.c.email.label("user_email")).select_from(
            meeting_invitee.outerjoin(user, user.c.id == meeting_invitee.c.user_id)).where(
            meeting_invitee.c.meeting_id == m["id"]).order_by(meeting_invitee.c.created_at)).mappings().all()
        return [{"id": r["id"], "userId": r["user_id"], "email": r["user_email"] or r["email"], "name": r["name"],
                 "role": r["role"], "response": r["response"]} for r in rows]

    def invitees(self, code: str, principal: Principal) -> list[dict]:
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            self._require(P.INVITE, self._subject(conn, m, principal, me), self._resource(conn, m))
            return self._invitees(conn, m)

    def remove_invitee(self, code: str, principal: Principal, invitee_id: str) -> None:
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            self._require(P.MANAGE, self._subject(conn, m, principal, me), self._resource(conn, m))
            conn.execute(meeting_invitee.delete().where(and_(
                meeting_invitee.c.id == invitee_id, meeting_invitee.c.meeting_id == m["id"])))

    # ------------------------------------------------------------------ join / leave
    def join(self, code: str, principal: Principal, *, display_name: str | None = None,
             passcode: str | None = None, device_id: str | None = None, device_label: str | None = None,
             mode: str | None = None) -> dict:
        """Join (or rejoin). A member already in on another device must choose: ``switch`` takes the seat
        over, ``companion`` adds this device as a second seat (e.g. a phone for the mic)."""
        if mode not in (None, "switch", "companion"):
            raise Invalid("mode must be 'switch' or 'companion'.")
        device_id = (device_id or "")[:64] or None
        device_label = _clean_text(device_label, 24, "deviceLabel") or None
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            if principal.kind == "member" and principal.org_id != m["organization_id"]:
                principal = Principal("guest")  # a member of another workspace joins as a guest
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me, passcode=passcode)
            resource = self._resource(conn, m)
            self._require(P.JOIN, subject, resource)
            events: list[Event] = []
            primary = None
            if principal.kind == "member" and me is not None and me["device_of"] is None and not principal.seat_token:
                active = me["state"] in ("admitted", "waiting")
                if active and me["device_id"] != device_id:  # another device holds the seat
                    if mode is None:
                        raise Conflict("You are already in this meeting on another device.", {
                            "code": "already_joined", "device": me["device_label"] or "another device",
                            "since": me["joined_at"] or me["created_at"], "canCompanion": me["state"] == "admitted"})
                    if mode == "companion":
                        if me["state"] != "admitted":
                            raise Invalid("Wait to be admitted on your main device first.")
                        primary, me = me, None
                        subject = {**subject, "companion": True, "bound": False, "role": None, "state": None}
                    else:  # switch: the old device is told to stand down
                        events.append(Event(frozenset({me["id"]}), {"t": "taken_over"}))
                elif mode == "companion" and not active:
                    raise Invalid("Join on your main device first, then add this one.")
            elif principal.kind == "member" and me is None and mode == "companion":
                raise Invalid("Join on your main device first, then add this one.")
            entered = bool(P.decide(P.ENTER, subject, resource))
            if principal.kind == "member":
                if me is not None and me["device_of"]:
                    name = me["display_name"]  # a companion keeps its label
                else:
                    row = conn.execute(select(user.c.display_name, user.c.name).where(
                        user.c.id == principal.user_id)).mappings().first() or {}
                    name = row.get("display_name") or row.get("name") or "Member"
                    if primary is not None:
                        name = f"{name} ({device_label or 'Other device'})"
            else:
                name = _clean_text(display_name, MAX_NAME, "name") or (me["display_name"] if me else None)
                if not name:
                    raise Invalid("A display name is required.")
            if primary is not None or (me is not None and me["device_of"]):
                role = "attendee"  # companions never carry moderation powers
            elif subject.get("is_host"):
                role = "host"
            elif subject.get("invite_role") == "cohost" or (me and me["role"] in ("host", "cohost")):
                role = "cohost"
            else:
                role = "attendee"
            entered = entered or bool(me and me["state"] == "admitted")  # a refresh must not demote anyone
            state = "admitted" if entered else "waiting"
            ts = now_iso()
            raw_token = None
            if me is None:
                pid = new_id()
                if principal.kind == "guest" or primary is not None:
                    raw_token = secrets.token_urlsafe(32)
                conn.execute(mp.insert().values(
                    id=pid, meeting_id=m["id"], user_id=principal.user_id if principal.kind == "member" else None,
                    kind=principal.kind, display_name=name, role=role, state=state,
                    token_hash=token_hash(raw_token) if raw_token else None, muted_by_host=False,
                    hand_raised=False, created_at=ts,
                    device_of=primary["id"] if primary is not None else None))
            else:
                pid = me["id"]
            first_entry = state == "admitted" and (me is None or me["joined_at"] is None)
            conn.execute(update(mp).where(mp.c.id == pid).values(
                state=state, role=role, display_name=name, left_at=None, hand_raised=False,
                device_id=device_id, device_label=device_label,
                **({"joined_at": ts, "muted_by_host": bool(m["mute_on_entry"]) and role == "attendee"}
                   if first_entry else {})))
            released: list[str] = []
            if state == "admitted":
                self._go_live(conn, m, ts)
                if role in ("host", "cohost") and not m["waiting_room"]:
                    released = self._release_waiting(conn, m, ts)
            me_row = conn.execute(select(mp).where(mp.c.id == pid)).mappings().first()
            audience = self._audience(conn, m)
            if state == "admitted":
                events.append(Event(audience["room"] - {pid}, {"t": "joined", "participant": self.pub(me_row)}))
            else:
                events.append(Event(audience["mods"], {"t": "waiting", "participant": self.pub(me_row)}))
            events.extend(Event(frozenset({r}), {"t": "resync"}) for r in released)
            events.extend(Event(audience["room"] - {r}, {"t": "joined", "participant": self.pub(
                conn.execute(select(mp).where(mp.c.id == r)).mappings().first())}) for r in released)
            m = conn.execute(select(meeting).where(meeting.c.id == m["id"])).mappings().first()
            return {"participant": self.pub(me_row), "guestToken": raw_token if principal.kind == "guest" else None,
                    "seatToken": raw_token if primary is not None else None, "state": state,
                    "meeting": self.ser_meeting(conn, m), "events": events,
                    "reason": None if entered else (P.decide(P.ENTER, subject, resource).reason)}

    def _go_live(self, conn, m, ts: str) -> None:
        if m["status"] == "scheduled":
            conn.execute(update(meeting).where(meeting.c.id == m["id"]).values(
                status="live", started_at=m["started_at"] or ts, updated_at=ts))

    def _release_waiting(self, conn, m, ts: str) -> list[str]:
        """Admit everyone parked while the host was away (no waiting room configured)."""
        capacity = m["max_participants"] - conn.execute(select(func.count()).where(and_(
            mp.c.meeting_id == m["id"], mp.c.state == "admitted"))).scalar()
        rows = conn.execute(select(mp.c.id, mp.c.role, mp.c.joined_at).where(and_(
            mp.c.meeting_id == m["id"], mp.c.state == "waiting")).order_by(mp.c.created_at)).all()
        out = []
        for pid, role, joined in rows[:max(capacity, 0)]:
            conn.execute(update(mp).where(mp.c.id == pid).values(
                state="admitted", **({"joined_at": ts, "muted_by_host": bool(m["mute_on_entry"])
                                      and role == "attendee"} if joined is None else {})))
            out.append(pid)
        return out

    def snapshot(self, code: str, principal: Principal) -> dict:
        """Full state for one connected participant (waiting or admitted)."""
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            resource = self._resource(conn, m)
            self._require(P.VIEW, subject, resource)
            if me is None or me["state"] not in ("waiting", "admitted"):
                raise Denied(Decision(False, "Join the meeting first."))
            out = {"meeting": self.ser_meeting(conn, m), "you": self.pub(me), "participants": [], "waiting": [],
                   "chat": []}
            if me["state"] == "admitted":
                rows = conn.execute(select(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.state == "admitted"))
                                    .order_by(mp.c.joined_at)).mappings().all()
                out["participants"] = [self.pub(p) for p in rows]
                if me["role"] in ("host", "cohost"):
                    wait = conn.execute(select(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.state == "waiting"))
                                        .order_by(mp.c.created_at)).mappings().all()
                    out["waiting"] = [self.pub(p) for p in wait]
                chat = conn.execute(select(meeting_chat).where(and_(
                    meeting_chat.c.meeting_id == m["id"],
                    or_(meeting_chat.c.recipient_id.is_(None), meeting_chat.c.recipient_id == me["id"],
                        meeting_chat.c.sender_id == me["id"]))).order_by(meeting_chat.c.created_at.desc())
                    .limit(CHAT_HISTORY)).mappings().all()
                out["chat"] = [self._chat(c) for c in reversed(chat)]
            return out

    @staticmethod
    def _chat(c) -> dict:
        return {"id": c["id"], "from": c["sender_id"], "to": c["recipient_id"], "body": c["body"],
                "at": c["created_at"]}

    def leave(self, code: str, principal: Principal) -> list[Event]:
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            if me is None or me["state"] not in ("waiting", "admitted"):
                return []
            return self._leave(conn, m, me)

    def leave_by_id(self, participant_id: str) -> list[Event]:
        """Used by the hub when a participant's last socket stayed closed past the grace period."""
        with self.engine.begin() as conn:
            me = conn.execute(select(mp).where(mp.c.id == participant_id)).mappings().first()
            if me is None or me["state"] not in ("waiting", "admitted"):
                return []
            m = conn.execute(select(meeting).where(meeting.c.id == me["meeting_id"])).mappings().first()
            return self._leave(conn, m, me)

    def _leave(self, conn, m, me) -> list[Event]:
        was_admitted = me["state"] == "admitted"
        conn.execute(update(mp).where(mp.c.id == me["id"]).values(
            state="left", left_at=now_iso(), hand_raised=False, sfu_session_id=None, sfu_sub_session_id=None,
            tracks=None, audio_on=False, video_on=False, screen_on=False))
        audience = self._audience(conn, m)
        events = [Event(audience["room"] if was_admitted else audience["mods"],
                        {"t": "left", "id": me["id"]})]
        if was_admitted and not audience["room"] and m["status"] == "live":
            events += self._finish(conn, m, "scheduled" if m["scheduled_start"] else "ended", audience=audience)
        return events

    def _finish(self, conn, m, status: str, audience=None) -> list:
        audience = audience or self._audience(conn, m)
        ts = now_iso()
        conn.execute(update(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.state == "admitted")).values(
            state="left", left_at=ts, sfu_session_id=None, sfu_sub_session_id=None, tracks=None, hand_raised=False,
            audio_on=False, video_on=False, screen_on=False))
        ended = status in ("ended", "cancelled")
        if ended:
            conn.execute(update(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.state == "waiting")
                                          ).values(state="denied"))
        conn.execute(update(meeting).where(meeting.c.id == m["id"]).values(
            status=status, updated_at=ts, locked=False, **({"ended_at": ts} if ended else {})))
        recipients = audience["everyone"] if ended else audience["room"]
        events: list = [Event(recipients, {"t": "ended" if ended else "emptied", "status": status})]
        if ended:
            events.extend(self._end_rings(conn, m, "cancelled"))
        return events

    # ------------------------------------------------------------------ in-meeting actions
    def perform(self, code: str, principal: Principal, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        handler = getattr(self, f"_act_{action}", None)
        if handler is None or action.startswith("_"):
            raise Invalid(f"Unknown action {action!r}.")
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            ctx = {"m": m, "me": me, "subject": subject, "principal": principal}
            result, events = handler(conn, ctx, payload)
            return {"result": result or {}, "events": events}

    def _target(self, conn, ctx, payload):
        tid = str(payload.get("target") or "")
        row = conn.execute(select(mp).where(and_(mp.c.id == tid, mp.c.meeting_id == ctx["m"]["id"]))
                           ).mappings().first() if tid else None
        if row is None:
            raise Invalid("Unknown participant.")
        return row

    def _check(self, conn, ctx, action, target=None):
        resource = self._resource(conn, ctx["m"], target=target, actor=ctx["me"])
        self._require(action, ctx["subject"], resource)
        return resource

    def _fresh(self, conn, pid):
        return conn.execute(select(mp).where(mp.c.id == pid)).mappings().first()

    def _updated(self, conn, ctx, pid, to="room") -> Event:
        return Event(self._audience(conn, ctx["m"])[to], {"t": "participant", "participant": self.pub(self._fresh(conn, pid))})

    def _act_admit(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        resource = self._check(conn, ctx, P.ADMIT, target)
        if target["state"] != "waiting":
            raise Invalid("That participant is not waiting.")
        if resource["full"]:
            raise Invalid("The meeting is full.")
        ts = now_iso()
        self._admit(conn, ctx["m"], target, ts)
        audience = self._audience(conn, ctx["m"])
        fresh = self._fresh(conn, target["id"])
        return {}, [Event(frozenset({target["id"]}), {"t": "resync"}),
                    Event(audience["room"] - {target["id"]}, {"t": "joined", "participant": self.pub(fresh)}),
                    Event(audience["mods"], {"t": "waiting_done", "id": target["id"]})]

    def _admit(self, conn, m, target, ts):
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(
            state="admitted", **({"joined_at": ts, "muted_by_host": bool(m["mute_on_entry"])
                                  and target["role"] == "attendee"} if target["joined_at"] is None else {})))
        self._go_live(conn, m, ts)

    def _act_admit_all(self, conn, ctx, payload):
        self._check(conn, ctx, P.ADMIT)
        ts = now_iso()
        released = self._release_waiting(conn, ctx["m"], ts)
        audience = self._audience(conn, ctx["m"])
        events = [Event(frozenset({r}), {"t": "resync"}) for r in released]
        events += [Event(audience["room"] - {r}, {"t": "joined", "participant": self.pub(self._fresh(conn, r))})
                   for r in released]
        events.append(Event(audience["mods"], {"t": "waiting_done", "id": "*"}))
        return {"admitted": len(released)}, events

    def _act_deny(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.DENY_ENTRY, target)
        if target["state"] != "waiting":
            raise Invalid("That participant is not waiting.")
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(state="denied"))
        return {}, [Event(frozenset({target["id"]}), {"t": "denied"}),
                    Event(self._audience(conn, ctx["m"])["mods"], {"t": "waiting_done", "id": target["id"]})]

    def _act_remove(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.REMOVE, target)
        audience = self._audience(conn, ctx["m"])
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(
            state="removed", left_at=now_iso(), sfu_session_id=None, sfu_sub_session_id=None, tracks=None,
            hand_raised=False, audio_on=False, video_on=False, screen_on=False))
        return {}, [Event(frozenset({target["id"]}), {"t": "removed"}),
                    Event(audience["room"] - {target["id"]}, {"t": "left", "id": target["id"]})]

    def _act_mute(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.MUTE, target)
        muted = bool(payload.get("muted", True))
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(
            muted_by_host=muted, **({"audio_on": False} if muted else {})))
        return {}, [Event(frozenset({target["id"]}), {"t": "mute", "muted": muted, "by": ctx["me"]["id"]}),
                    self._updated(conn, ctx, target["id"])]

    def _act_mute_all(self, conn, ctx, payload):
        self._check(conn, ctx, P.MUTE_ALL)
        allow = bool(payload.get("allowUnmute", True))
        m = ctx["m"]
        conn.execute(update(mp).where(and_(mp.c.meeting_id == m["id"], mp.c.state == "admitted",
                                           mp.c.role == "attendee")).values(muted_by_host=True, audio_on=False))
        conn.execute(update(meeting).where(meeting.c.id == m["id"]).values(allow_unmute=allow, updated_at=now_iso()))
        fresh = conn.execute(select(meeting).where(meeting.c.id == m["id"])).mappings().first()
        audience = self._audience(conn, m)
        return {}, [Event(audience["room"], {"t": "mute_all", "allowUnmute": allow}),
                    Event(audience["everyone"], {"t": "meeting", "meeting": self.ser_meeting(conn, fresh)})]

    def _act_lower_hand(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.LOWER_HAND, target)
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(hand_raised=False))
        return {}, [self._updated(conn, ctx, target["id"])]

    def _act_hand(self, conn, ctx, payload):
        self._check(conn, ctx, P.HAND)
        conn.execute(update(mp).where(mp.c.id == ctx["me"]["id"]).values(hand_raised=bool(payload.get("raised", True))))
        return {}, [self._updated(conn, ctx, ctx["me"]["id"])]

    def _act_react(self, conn, ctx, payload):
        self._check(conn, ctx, P.REACT)
        emoji = payload.get("emoji")
        if emoji not in EMOJI:
            raise Invalid("Unsupported reaction.")
        return {}, [Event(self._audience(conn, ctx["m"])["room"], {"t": "react", "from": ctx["me"]["id"], "emoji": emoji})]

    def _act_chat(self, conn, ctx, payload):
        self._check(conn, ctx, P.CHAT)
        body = _clean_text(payload.get("body"), MAX_CHAT, "message", required=True)
        to = payload.get("to")
        audience = self._audience(conn, ctx["m"])
        recipients = audience["room"]
        if to:
            if to not in audience["room"]:
                raise Invalid("Recipient is not in the meeting.")
            recipients = frozenset({to, ctx["me"]["id"]})
        row = {"id": new_id(), "meeting_id": ctx["m"]["id"], "sender_id": ctx["me"]["id"], "recipient_id": to or None,
               "body": body, "created_at": now_iso()}
        conn.execute(meeting_chat.insert().values(**row))
        return {}, [Event(recipients, {"t": "chat", "message": self._chat(row)})]

    def _act_promote(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.PROMOTE, target)
        role = payload.get("role", "cohost")
        if role not in ("cohost", "attendee"):
            raise Invalid("role must be 'cohost' or 'attendee'.")
        if target["state"] != "admitted":
            raise Invalid("That participant is not in the meeting.")
        if role == "cohost" and target["kind"] != "member":
            raise Invalid("Only workspace members can be co-hosts.")
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(role=role))
        return {}, [self._updated(conn, ctx, target["id"])]

    def _act_transfer_host(self, conn, ctx, payload):
        target = self._target(conn, ctx, payload)
        self._check(conn, ctx, P.TRANSFER_HOST, target)
        if target["kind"] != "member" or target["state"] != "admitted":
            raise Invalid("The new host must be a workspace member in the meeting.")
        conn.execute(update(mp).where(mp.c.id == target["id"]).values(role="host"))
        conn.execute(update(mp).where(mp.c.id == ctx["me"]["id"]).values(role="cohost"))
        conn.execute(update(meeting).where(meeting.c.id == ctx["m"]["id"]).values(
            host_id=target["user_id"], updated_at=now_iso()))
        fresh = conn.execute(select(meeting).where(meeting.c.id == ctx["m"]["id"])).mappings().first()
        audience = self._audience(conn, ctx["m"])
        return {}, [self._updated(conn, ctx, target["id"]), self._updated(conn, ctx, ctx["me"]["id"]),
                    Event(audience["everyone"], {"t": "meeting", "meeting": self.ser_meeting(conn, fresh)})]

    def _act_settings(self, conn, ctx, payload):
        self._check(conn, ctx, P.SETTINGS)
        unknown = set(payload) - set(SETTING_FIELDS)
        if unknown:
            raise Invalid(f"Unknown settings: {', '.join(sorted(unknown))}.")
        values = self._clean_fields(conn, ctx["principal"], payload)
        if values:
            conn.execute(update(meeting).where(meeting.c.id == ctx["m"]["id"]).values(
                updated_at=now_iso(), **values))
        fresh = conn.execute(select(meeting).where(meeting.c.id == ctx["m"]["id"])).mappings().first()
        return {}, [Event(self._audience(conn, ctx["m"])["everyone"],
                          {"t": "meeting", "meeting": self.ser_meeting(conn, fresh)})]

    def _act_lock(self, conn, ctx, payload):
        return self._act_settings(conn, ctx, {"locked": bool(payload.get("locked", True))})

    def _act_record(self, conn, ctx, payload):
        self._check(conn, ctx, P.RECORD)
        state = "recording" if payload.get("state") == "start" else "allowed"
        conn.execute(update(meeting).where(meeting.c.id == ctx["m"]["id"]).values(recording=state, updated_at=now_iso()))
        return {}, [Event(self._audience(conn, ctx["m"])["room"],
                          {"t": "recording", "state": state, "by": ctx["me"]["display_name"]})]

    def _act_media(self, conn, ctx, payload):
        """Tell the room what my mic/camera/screen are doing (drives the tiles' mute and camera-off icons)."""
        self._check(conn, ctx, P.MEDIA_STATE)
        values = {}
        for key, col in (("audio", "audio_on"), ("video", "video_on"), ("screen", "screen_on")):
            if key in payload:
                values[col] = bool(payload[key])
        if values.get("audio_on") and ctx["subject"].get("forced_mute"):
            raise Invalid("The host muted you and disabled unmuting.")
        if values:
            conn.execute(update(mp).where(mp.c.id == ctx["me"]["id"]).values(**values))
        return {}, [self._updated(conn, ctx, ctx["me"]["id"])]

    def _act_end(self, conn, ctx, payload):
        self._check(conn, ctx, P.END)
        return {}, self._finish(conn, ctx["m"], "ended")

    def _act_leave(self, conn, ctx, payload):
        me = ctx["me"]
        if me is None or me["state"] not in ("waiting", "admitted"):
            return {}, []
        return {}, self._leave(conn, ctx["m"], me)

    # ------------------------------------------------------------------ notifications
    def _summary(self, conn, m) -> dict:
        return self.ser_meeting(conn, m, detail=False)

    def _name_of(self, conn, user_id) -> str | None:
        row = conn.execute(select(user.c.display_name, user.c.name).where(user.c.id == user_id)).mappings().first()
        return (row or {}).get("display_name") or (row or {}).get("name")

    def _invited_notices(self, conn, m, inviter_id, user_ids: list[str]) -> list:
        if not user_ids:
            return []
        return [Notice(frozenset(user_ids), {"t": "invited", "meeting": self._summary(conn, m),
                                             "by": self._name_of(conn, inviter_id)})]

    def _invitee_user_ids(self, conn, m, exclude=()) -> frozenset:
        rows = conn.execute(select(meeting_invitee.c.user_id).where(and_(
            meeting_invitee.c.meeting_id == m["id"], meeting_invitee.c.user_id.is_not(None),
            meeting_invitee.c.response != "declined"))).all()
        return frozenset(r[0] for r in rows) - set(exclude)

    def _notice_invitees(self, conn, m, msg: dict, exclude=()) -> Notice:
        return Notice(self._invitee_user_ids(conn, m, exclude), msg)

    def rsvp(self, code: str, principal: Principal, response: str) -> list:
        if response not in RSVP_VALUES:
            raise Invalid(f"response must be one of {', '.join(RSVP_VALUES)}.")
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            subject = self._subject(conn, m, principal, self._me(conn, m, principal))
            self._require(P.RSVP, subject, self._resource(conn, m))
            email = conn.execute(select(user.c.email).where(user.c.id == principal.user_id)).scalar()
            conn.execute(update(meeting_invitee).where(and_(meeting_invitee.c.meeting_id == m["id"], or_(
                meeting_invitee.c.user_id == principal.user_id,
                *([func.lower(meeting_invitee.c.email) == email.lower()] if email else [])))).values(
                response=response, user_id=principal.user_id))
            return [Notice(frozenset({m["host_id"]}), {
                "t": "rsvp", "code": m["code"], "title": m["title"], "response": response,
                "by": self._name_of(conn, principal.user_id)})]

    def ring(self, code: str, principal: Principal, user_ids: list) -> tuple[list[dict], list]:
        """Call people: invite them if needed and ring them for RING_SECONDS."""
        if not isinstance(user_ids, list) or not 0 < len(user_ids) <= 50:
            raise Invalid("userIds must list 1-50 workspace members.")
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            subject = self._subject(conn, m, principal, self._me(conn, m, principal))
            resource = self._resource(conn, m)
            self._require(P.INVITE, subject, resource)
            if m["status"] not in ("scheduled", "live"):
                raise Invalid("This meeting is over.")
            if principal.kind != "member":
                raise Denied(Decision(False, "Only members can place calls."))
            targets = [r[0] for r in conn.execute(select(user.c.id).where(and_(
                user.c.organization_id == m["organization_id"], user.c.active.is_not(False),
                user.c.id.in_([str(u) for u in user_ids]), user.c.id != principal.user_id)))]
            if not targets:
                raise Invalid("No one to call.")
            self._add_invitees(conn, m, principal.user_id, targets, "attendee")
            now = datetime.now(timezone.utc)
            conn.execute(update(meeting_ring).where(and_(
                meeting_ring.c.meeting_id == m["id"], meeting_ring.c.to_id.in_(targets),
                meeting_ring.c.state == "ringing")).values(state="cancelled", responded_at=now.isoformat()))
            caller = {"id": principal.user_id, "name": self._name_of(conn, principal.user_id)}
            summary = self._summary(conn, m)
            rings, notices = [], []
            for uid in targets:
                row = {"id": new_id(), "meeting_id": m["id"], "organization_id": m["organization_id"],
                       "from_id": principal.user_id, "to_id": uid, "state": "ringing",
                       "created_at": now.isoformat(), "expires_at": (now + timedelta(seconds=RING_SECONDS)).isoformat()}
                conn.execute(meeting_ring.insert().values(**row))
                rings.append({"id": row["id"], "userId": uid, "expiresAt": row["expires_at"]})
                notices.append(Notice(frozenset({uid}), {"t": "ring", "ring": {
                    "id": row["id"], "from": caller, "meeting": summary, "expiresAt": row["expires_at"]}}))
            return rings, notices

    def respond_ring(self, ring_id: str, principal: Principal, answer: str) -> tuple[dict, list]:
        if answer not in ("accept", "decline"):
            raise Invalid("answer must be 'accept' or 'decline'.")
        if principal.kind != "member":
            raise Denied(Decision(False, "Sign in to answer calls."))
        with self.engine.begin() as conn:
            r = conn.execute(select(meeting_ring).where(and_(
                meeting_ring.c.id == ring_id, meeting_ring.c.to_id == principal.user_id,
                meeting_ring.c.organization_id == principal.org_id))).mappings().first()
            if r is None:
                raise NotFound("Call not found.")
            m = conn.execute(select(meeting).where(meeting.c.id == r["meeting_id"])).mappings().first()
            if r["state"] != "ringing" or r["expires_at"] <= datetime.now(timezone.utc).isoformat() \
                    or m["status"] not in ("scheduled", "live"):
                raise Invalid("This call has ended.")
            state = "accepted" if answer == "accept" else "declined"
            conn.execute(update(meeting_ring).where(meeting_ring.c.id == ring_id).values(
                state=state, responded_at=now_iso()))
            if answer == "accept":
                conn.execute(update(meeting_invitee).where(and_(
                    meeting_invitee.c.meeting_id == m["id"], meeting_invitee.c.user_id == principal.user_id)
                ).values(response="accepted"))
            notices = [Notice(frozenset({r["from_id"]}), {
                "t": "ring_answered", "id": ring_id, "answer": answer, "code": m["code"],
                "by": self._name_of(conn, principal.user_id)}),
                Notice(frozenset({principal.user_id}), {"t": "ring_ended", "id": ring_id, "reason": state})]
            return {"code": m["code"], "answer": answer}, notices

    def expire_ring(self, ring_id: str) -> list:
        with self.engine.begin() as conn:
            r = conn.execute(select(meeting_ring).where(meeting_ring.c.id == ring_id)).mappings().first()
            if r is None or r["state"] != "ringing":
                return []
            conn.execute(update(meeting_ring).where(meeting_ring.c.id == ring_id).values(
                state="missed", responded_at=now_iso()))
            m = conn.execute(select(meeting).where(meeting.c.id == r["meeting_id"])).mappings().first()
            return [Notice(frozenset({r["to_id"]}), {"t": "ring_ended", "id": ring_id, "reason": "missed"}),
                    Notice(frozenset({r["from_id"]}), {"t": "ring_answered", "id": ring_id, "answer": "missed",
                                                       "code": m["code"], "by": self._name_of(conn, r["to_id"])})]

    def _end_rings(self, conn, m, reason: str) -> list:
        rows = conn.execute(select(meeting_ring.c.id, meeting_ring.c.to_id).where(and_(
            meeting_ring.c.meeting_id == m["id"], meeting_ring.c.state == "ringing"))).all()
        if not rows:
            return []
        conn.execute(update(meeting_ring).where(and_(
            meeting_ring.c.meeting_id == m["id"], meeting_ring.c.state == "ringing")).values(
            state=reason, responded_at=now_iso()))
        return [Notice(frozenset({to}), {"t": "ring_ended", "id": rid, "reason": reason}) for rid, to in rows]

    def pending_rings(self, principal: Principal) -> list[dict]:
        """Calls still ringing for me (sent on inbox connect so a reload does not lose them)."""
        if principal.kind != "member":
            return []
        with self.engine.connect() as conn:
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute(select(meeting_ring, meeting).select_from(
                meeting_ring.join(meeting, meeting.c.id == meeting_ring.c.meeting_id)).where(and_(
                    meeting_ring.c.to_id == principal.user_id, meeting_ring.c.state == "ringing",
                    meeting_ring.c.expires_at > now, meeting.c.status.in_(("scheduled", "live"))))).mappings().all()
            return [{"id": r["id"], "from": {"id": r["from_id"], "name": self._name_of(conn, r["from_id"])},
                     "meeting": self._summary(conn, r), "expiresAt": r["expires_at"]} for r in rows]

    def due_reminders(self, now: datetime | None = None) -> list:
        """'Starting soon' notices, once per meeting, for the host and everyone who has not declined."""
        now = now or datetime.now(timezone.utc)
        horizon = (now + timedelta(minutes=REMINDER_MINUTES)).isoformat()
        with self.engine.begin() as conn:
            rows = conn.execute(select(meeting).where(and_(
                meeting.c.status == "scheduled", meeting.c.reminded_at.is_(None),
                meeting.c.scheduled_start.is_not(None), meeting.c.scheduled_start <= horizon,
                meeting.c.scheduled_start >= (now - timedelta(minutes=2)).isoformat()))).mappings().all()
            notices = []
            for m in rows:
                conn.execute(update(meeting).where(meeting.c.id == m["id"]).values(reminded_at=now.isoformat()))
                start = datetime.fromisoformat(m["scheduled_start"])
                minutes = max(0, round((start - now).total_seconds() / 60))
                notices.append(Notice(frozenset({m["host_id"]}) | self._invitee_user_ids(conn, m), {
                    "t": "starting_soon", "minutes": minutes, "meeting": self._summary(conn, m)}))
            return notices

    def usage(self, code: str, principal: Principal, bytes_in: int, bytes_out: int) -> None:
        """Cumulative byte counters from the browser's getStats(); used to report what a meeting cost."""
        for value in (bytes_in, bytes_out):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10**13:
                raise Invalid("Byte counters must be integers.")
        with self.engine.begin() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            self._require(P.USAGE, self._subject(conn, m, principal, me), self._resource(conn, m))
            conn.execute(update(mp).where(mp.c.id == me["id"]).values(
                bytes_in=max(me["bytes_in"] or 0, bytes_in), bytes_out=max(me["bytes_out"] or 0, bytes_out)))

    def ics(self, code: str, principal: Principal, base_url: str) -> str:
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            self._require(P.VIEW, self._subject(conn, m, principal, me), self._resource(conn, m))
            host = conn.execute(select(user.c.name, user.c.display_name, user.c.email).where(
                user.c.id == m["host_id"])).mappings().first() or {}
            start = datetime.fromisoformat(m["scheduled_start"] or m["created_at"]).astimezone(timezone.utc)
            end = start + timedelta(minutes=m["duration_minutes"] or 60)
            fmt = "%Y%m%dT%H%M%SZ"
            url = f"{base_url.rstrip('/')}/meet/{m['code']}"
            lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hoja//Meetings//EN", "CALSCALE:GREGORIAN",
                     "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{m['id']}@hoja", f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}",
                     f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}",
                     f"SUMMARY:{_ics_text(m['title'])}",
                     f"DESCRIPTION:{_ics_text((m['description'] or '') + chr(10) + 'Join: ' + url)}",
                     f"LOCATION:{_ics_text(url)}", f"URL:{url}",
                     f"STATUS:{'CANCELLED' if m['status'] == 'cancelled' else 'CONFIRMED'}"]
            if host.get("email"):
                lines.append(f"ORGANIZER;CN={_ics_text(host.get('display_name') or host.get('name') or 'Host')}:"
                             f"mailto:{host['email']}")
            for inv in self._invitees(conn, m):
                if inv["email"]:
                    lines.append(f"ATTENDEE;CN={_ics_text(inv['name'] or inv['email'])}:mailto:{inv['email']}")
            lines += ["END:VEVENT", "END:VCALENDAR"]
            return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"

    # ------------------------------------------------------------------ SFU bookkeeping
    def media_context(self, code: str, principal: Principal, action: str = P.SUBSCRIBE) -> dict:
        with self.engine.connect() as conn:
            m = self._meeting(conn, code)
            me = self._me(conn, m, principal)
            subject = self._subject(conn, m, principal, me)
            self._require(action, subject, self._resource(conn, m))
            return {"meeting_id": m["id"], "participant": dict(me), "subject": subject,
                    "resource": self._resource(conn, m)}

    def authorize_media(self, code: str, principal: Principal, action: str) -> None:
        self.media_context(code, principal, action)

    def set_session(self, participant_id: str, session_id: str, role: str = "pub") -> None:
        """role 'pub' = the peer connection that sends my tracks, 'sub' = the one that receives others'."""
        column = "sfu_session_id" if role == "pub" else "sfu_sub_session_id"
        with self.engine.begin() as conn:
            conn.execute(update(mp).where(mp.c.id == participant_id).values(**{column: session_id}))

    def remote_track_ok(self, meeting_id: str, session_id: str, track_name: str) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(select(mp.c.tracks).where(and_(
                mp.c.meeting_id == meeting_id, mp.c.state == "admitted", mp.c.sfu_session_id == session_id))).first()
        return bool(row) and any(t.get("name") == track_name for t in json.loads(row[0] or "[]"))

    def change_tracks(self, participant_id: str, add: list[dict] | None = None,
                      remove: list[str] | None = None) -> list[Event]:
        with self.engine.begin() as conn:
            p = self._fresh(conn, participant_id)
            if p is None or p["state"] != "admitted":
                return []
            tracks = {t["name"]: t for t in json.loads(p["tracks"] or "[]")}
            for t in add or []:
                tracks[t["name"]] = t
            for name in remove or []:
                tracks.pop(name, None)
            conn.execute(update(mp).where(mp.c.id == participant_id).values(tracks=json.dumps(list(tracks.values()))))
            m = conn.execute(select(meeting).where(meeting.c.id == p["meeting_id"])).mappings().first()
            return [Event(self._audience(conn, m)["room"],
                          {"t": "participant", "participant": self.pub(self._fresh(conn, participant_id))})]
