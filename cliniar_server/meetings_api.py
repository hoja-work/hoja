"""HTTP + WebSocket surface for meetings.

REST (all under ``/api/meetings``) is authoritative; the WebSocket (``/ws/meetings/{code}``)
pushes room events and carries the lightweight in-meeting messages (chat, hand, reactions,
moderation). Members authenticate like everywhere else (API key or ``hoja_session`` cookie);
public-link guests get an opaque token from ``join`` and present it as
``X-Hoja-Guest-Token`` (REST) or the ``guest`` WebSocket subprotocol.

Media never touches this server: the browser talks to Cloudflare's SFU directly, and the
``/sfu/*`` endpoints only authorize and forward control calls for the caller's own session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from cliniar_server import meeting_policy as P
from cliniar_server.cloudflare_realtime import CloudflareRealtime, SFUError
from cliniar_server.db import meeting, rate_limit_blocked, rate_limit_record
from cliniar_server.meetings import (
    RING_SECONDS,
    TRACK_ACTION,
    TRACK_RE,
    Conflict,
    Denied,
    Event,
    Invalid,
    MeetingError,
    MeetingService,
    NotFound,
    Notice,
    Principal,
)

log = logging.getLogger("cliniar_server.meetings")
audit = logging.getLogger("cliniar_server.meetings.audit")

MAX_BODY = 128 * 1024  # SDP offers are large; nothing legitimate exceeds this
MAX_WS_MESSAGE = 16 * 1024
LEAVE_GRACE_SECONDS = 20.0
PASSCODE_FAILURES = (8, 600.0)
GUEST_JOINS = (30, 600.0)
CODE_MISSES = (20, 600.0)
WS_RATE = (12, 5.0)  # messages per window per connection
TRACK_KEYS = {"location", "mid", "trackName", "sessionId", "simulcast", "kind"}
SIMULCAST_KEYS = {"preferredRid", "priorityOrdering", "ridNotAvailable"}
REMINDER_POLL_SECONDS = 30.0
END_CODES = {"ended": 4410, "removed": 4411, "denied": 4412, "taken_over": 4413}


class _Conn:
    __slots__ = ("code", "pid", "principal", "stamps", "ws")

    def __init__(self, ws: WebSocket, pid: str, principal: Principal, code: str) -> None:
        self.ws, self.pid, self.principal, self.code = ws, pid, principal, code
        self.stamps: list[float] = []

    def over_rate(self) -> bool:
        now = time.monotonic()
        self.stamps = [t for t in self.stamps if now - t < WS_RATE[1]]
        self.stamps.append(now)
        return len(self.stamps) > WS_RATE[0]


class MeetingHub:
    """In-process room fan-out. A meeting's sockets must land on one server process."""

    def __init__(self, service: MeetingService, *, grace: float = LEAVE_GRACE_SECONDS) -> None:
        self.service = service
        self.grace = grace
        self.rooms: dict[str, dict[str, set[_Conn]]] = {}
        self.inboxes: dict[str, set[WebSocket]] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._reminders: asyncio.Task | None = None

    # ---------------------------------------------------------------- background work
    def start(self) -> None:
        if self._reminders is None:
            self._reminders = asyncio.get_running_loop().create_task(self._remind_loop())

    async def _remind_loop(self) -> None:
        while True:
            await asyncio.sleep(REMINDER_POLL_SECONDS)
            try:
                await self.dispatch(None, await run_in_threadpool(self.service.due_reminders))
            except Exception:  # a failed poll must never kill the loop
                log.exception("meeting reminder poll failed")

    def _later(self, delay: float, make) -> None:
        async def run() -> None:
            await asyncio.sleep(delay)
            try:
                await make()
            except Exception:
                log.exception("scheduled meeting task failed")

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------------------------------------------------------------- inbox (personal) sockets
    async def notify(self, users, msg: dict) -> None:
        text = json.dumps(msg, separators=(",", ":"))
        for uid in users:
            for ws in list(self.inboxes.get(uid, ())):
                try:
                    await asyncio.wait_for(ws.send_text(text), timeout=5.0)
                except Exception:
                    self.inboxes.get(uid, set()).discard(ws)

    async def dispatch(self, meeting_id: str | None, items: list) -> None:
        """Deliver service output: Events to the room, Notices to people's personal inboxes."""
        events = [i for i in items if isinstance(i, Event)]
        if events and meeting_id:
            await self.emit(meeting_id, events)
        for item in items:
            if not isinstance(item, Notice):
                continue
            await self.notify(item.users, item.msg)
            if item.msg.get("t") == "ring":
                ring_id = item.msg["ring"]["id"]

                async def expire(ring_id=ring_id) -> None:
                    await self.dispatch(None, await run_in_threadpool(self.service.expire_ring, ring_id))

                self._later(RING_SECONDS + 1, expire)

    async def serve_inbox(self, ws: WebSocket, principal: Principal, subprotocol: str | None) -> None:
        await ws.accept(subprotocol=subprotocol)
        if principal.kind != "member":
            return await ws.close(code=4401)
        hello = {"t": "hello",
                 "invitations": await run_in_threadpool(self.service.invitations, principal),
                 "rings": await run_in_threadpool(self.service.pending_rings, principal)}
        self.inboxes.setdefault(principal.user_id, set()).add(ws)
        try:
            await ws.send_text(json.dumps(hello, separators=(",", ":")))
            while True:
                raw = await ws.receive_text()
                if len(raw) > 4096:
                    return await ws.close(code=1009)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("t") == "ping":
                    await ws.send_text('{"t":"pong"}')
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            conns = self.inboxes.get(principal.user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self.inboxes.pop(principal.user_id, None)

    # ---------------------------------------------------------------- delivery
    async def emit(self, meeting_id: str, events: list[Event]) -> None:
        room = self.rooms.get(meeting_id, {})
        for event in events:
            t = event.msg.get("t")
            for pid in event.to:
                for conn in list(room.get(pid, ())):
                    if t == "resync":
                        await self._snapshot(conn)
                        continue
                    await self._send(conn, event.msg)
                    if t in END_CODES:
                        with suppress(Exception):
                            await conn.ws.close(code=END_CODES[t])

    async def _send(self, conn: _Conn, msg: dict) -> None:
        try:
            await asyncio.wait_for(conn.ws.send_text(json.dumps(msg, separators=(",", ":"))), timeout=5.0)
        except Exception:
            self._drop(conn)

    async def _snapshot(self, conn: _Conn) -> None:
        try:
            snap = await run_in_threadpool(self.service.snapshot, conn.code, conn.principal)
        except MeetingError:
            return
        await self._send(conn, {"t": "snapshot", **snap})

    # ---------------------------------------------------------------- membership
    def _add(self, meeting_id: str, conn: _Conn) -> None:
        self.rooms.setdefault(meeting_id, {}).setdefault(conn.pid, set()).add(conn)
        pending = self._pending.pop(conn.pid, None)
        if pending:
            pending.cancel()

    def _drop(self, conn: _Conn) -> None:
        for mid, room in list(self.rooms.items()):
            conns = room.get(conn.pid)
            if conns and conn in conns:
                conns.discard(conn)
                if not conns:
                    room.pop(conn.pid, None)
                    self._schedule_leave(mid, conn.pid)
                if not room:
                    self.rooms.pop(mid, None)

    def _schedule_leave(self, meeting_id: str, pid: str) -> None:
        async def later() -> None:
            await asyncio.sleep(self.grace)
            if pid in self.rooms.get(meeting_id, {}):
                return
            events = await run_in_threadpool(self.service.leave_by_id, pid)
            await self.dispatch(meeting_id, events)

        with suppress(RuntimeError):
            self._pending[pid] = asyncio.get_running_loop().create_task(later())

    async def close_all(self) -> None:
        for task in [*self._pending.values(), *self._tasks, *([self._reminders] if self._reminders else [])]:
            task.cancel()
        self._pending.clear()
        self._tasks.clear()
        self._reminders = None

    # ---------------------------------------------------------------- socket loop
    async def serve(self, ws: WebSocket, code: str, principal: Principal, subprotocol: str | None) -> None:
        await ws.accept(subprotocol=subprotocol)
        try:
            snap = await run_in_threadpool(self.service.snapshot, code, principal)
        except NotFound:
            return await ws.close(code=4404)
        except Denied:
            return await ws.close(code=4403)
        meeting_id = snap["meeting"]["id"]
        conn = _Conn(ws, snap["you"]["id"], principal, code)
        self._add(meeting_id, conn)
        try:
            await self._send(conn, {"t": "snapshot", **snap})
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_WS_MESSAGE:
                    return await ws.close(code=1009)
                await self._handle(meeting_id, conn, raw)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._drop(conn)

    async def _handle(self, meeting_id: str, conn: _Conn, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return await self._send(conn, {"t": "ack", "ok": False, "error": "Malformed message."})
        if not isinstance(msg, dict):
            return
        kind, ref = msg.get("t"), msg.get("id")
        if kind == "ping":
            return await self._send(conn, {"t": "pong"})
        if kind == "sync":
            return await self._snapshot(conn)
        if kind in ("chat", "hand", "react"):
            action, payload = kind, msg
        elif kind == "act" and isinstance(msg.get("a"), str):
            action, payload = msg["a"], msg.get("p") if isinstance(msg.get("p"), dict) else {}
        else:
            return await self._send(conn, {"t": "ack", "id": ref, "ok": False, "error": "Unknown message."})
        if conn.over_rate():
            return await self._send(conn, {"t": "ack", "id": ref, "ok": False, "error": "Slow down."})
        try:
            out = await run_in_threadpool(self.service.perform, conn.code, conn.principal, action, payload)
        except Denied as exc:
            audit.info("denied action=%s rule=%s", action, exc.decision.rule)
            return await self._send(conn, {"t": "ack", "id": ref, "ok": False, "error": str(exc),
                                           "rule": exc.decision.rule})
        except MeetingError as exc:
            return await self._send(conn, {"t": "ack", "id": ref, "ok": False, "error": str(exc)})
        await self._send(conn, {"t": "ack", "id": ref, "ok": True, **out["result"]})
        await self.dispatch(meeting_id, out["events"])


def _err(exc: MeetingError) -> JSONResponse:
    if isinstance(exc, Conflict):
        return JSONResponse({"error": str(exc), **exc.payload}, status_code=409)
    if isinstance(exc, Denied):
        audit.info("denied rule=%s reason=%s", exc.decision.rule, exc.decision.reason)
        return JSONResponse({"error": str(exc), "rule": exc.decision.rule}, status_code=403)
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _clean_sdp(desc) -> dict:
    if not isinstance(desc, dict) or desc.get("type") not in ("offer", "answer") or not isinstance(desc.get("sdp"), str):
        raise Invalid("sessionDescription must be {type: 'offer'|'answer', sdp}.")
    if len(desc["sdp"]) > 100_000:
        raise Invalid("sessionDescription is too large.")
    return {"type": desc["type"], "sdp": desc["sdp"]}


def _clean_track(raw, *, service: MeetingService, ctx: dict, principal: Principal, code: str,
                 mode: str, role: str) -> dict:
    """Whitelist one track entry; authorize what it publishes or subscribes to."""
    if not isinstance(raw, dict):
        raise Invalid("Each track must be an object.")
    track = {k: raw[k] for k in TRACK_KEYS if k in raw}
    for key in ("mid", "trackName", "sessionId", "location", "kind"):
        if key in track and not isinstance(track[key], str):
            raise Invalid(f"track.{key} must be a string.")
    if "simulcast" in track:
        sim = track["simulcast"]
        if not isinstance(sim, dict):
            raise Invalid("track.simulcast must be an object.")
        track["simulcast"] = {k: sim[k] for k in SIMULCAST_KEYS if k in sim}
    if mode == "new":
        loc = track.get("location")
        if loc != {"pub": "local", "sub": "remote"}[role]:
            raise Invalid("The publish session only sends tracks and the subscribe session only receives them.")
        if loc == "local":
            name = track.get("trackName", "")
            match = TRACK_RE.match(name)
            if not match:
                raise Invalid("Local tracks must be named mic-*, cam-* or screen-*.")
            service.authorize_media(code, principal, TRACK_ACTION[match.group(1)])
        elif loc == "remote":
            sid, name = track.get("sessionId"), track.get("trackName")
            if not sid or not name or not service.remote_track_ok(ctx["meeting_id"], sid, name):
                raise Invalid("Unknown remote track.")
        else:
            raise Invalid("track.location must be 'local' or 'remote'.")
    elif "mid" not in track:
        raise Invalid("track.mid is required.")
    return track


def meeting_routes(engine, service: MeetingService, hub: MeetingHub, *, auth: Callable, csrf: Callable,
                   read_json: Callable, client_ip: Callable, same_origin: Callable,
                   sfu: CloudflareRealtime | None) -> list:
    app_url = os.environ.get("CLINIAR_APP_URL", "").rstrip("/")

    # ------------------------------------------------------------------ helpers
    async def who(request: Request) -> Principal | None:
        uid, org, _identity, _browser = await run_in_threadpool(auth, request)
        if uid:
            return Principal("member", uid, org, getattr(request.state, "api_key_access", None),
                             seat_token=request.headers.get("x-hoja-seat", "").strip() or None)
        token = request.headers.get("x-hoja-guest-token", "").strip()
        return Principal("guest", guest_token=token) if token else None

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": "Authentication required."}, status_code=401)

    def link(request: Request, code: str, passcode: str | None = None) -> str:
        base = app_url or str(request.base_url).rstrip("/")
        return f"{base}/meet/{code}" + (f"?pwd={passcode}" if passcode else "")

    async def body(request: Request):
        return await read_json(request, MAX_BODY)

    def throttled(bucket: str, keys: list[str], limit: tuple[int, float]) -> JSONResponse | None:
        if rate_limit_blocked(engine, bucket, keys, limit[0], limit[1]):
            return JSONResponse({"error": "Too many attempts. Try again later."}, status_code=429,
                                headers={"Retry-After": str(int(limit[1]))})
        return None

    async def write_guard(request: Request) -> JSONResponse | None:
        return csrf(request) if request.method not in ("GET", "HEAD", "OPTIONS") else None

    def meeting_id_of(code: str) -> str | None:
        with engine.connect() as conn:
            return conn.execute(select(meeting.c.id).where(meeting.c.code == code)).scalar()

    # ------------------------------------------------------------------ meetings
    async def create(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        if (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        try:
            meeting, passcode, notices = await run_in_threadpool(service.create, principal, data)
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(meeting["id"], notices)
        return JSONResponse({"meeting": meeting, "passcode": passcode,
                             "joinUrl": link(request, meeting["code"], passcode if meeting["linkEnabled"] else None)},
                            status_code=201)

    async def listing(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        scope = request.query_params.get("scope", "upcoming")
        if scope not in ("upcoming", "past"):
            return JSONResponse({"error": "scope must be 'upcoming' or 'past'."}, status_code=400)
        try:
            limit = int(request.query_params.get("limit", "50"))
            rows = await run_in_threadpool(service.list, principal, scope, limit)
        except ValueError:
            return JSONResponse({"error": "limit must be an integer."}, status_code=400)
        except MeetingError as exc:
            return _err(exc)
        return JSONResponse({"meetings": rows})

    async def detail(request: Request):
        principal = await who(request)
        if principal is None:
            return unauthorized()
        try:
            m = await run_in_threadpool(service.get, request.path_params["code"], principal)
        except MeetingError as exc:
            return _err(exc)
        return JSONResponse({"meeting": m, "joinUrl": link(request, m["code"])})

    async def preview(request: Request):
        ip = client_ip(request)
        if (limited := throttled("meeting_code_miss", [f"ip:{ip}"], CODE_MISSES)):
            return limited
        try:
            return JSONResponse({"meeting": await run_in_threadpool(service.preview, request.path_params["code"])})
        except NotFound:
            rate_limit_record(engine, "meeting_code_miss", [f"ip:{ip}"], CODE_MISSES[1])
            return JSONResponse({"error": "Meeting not found."}, status_code=404)

    async def patch(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        if (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        code = request.path_params["code"]
        try:
            m, passcode, events = await run_in_threadpool(service.update, code, principal, data)
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(m["id"], events)
        return JSONResponse({"meeting": m, "passcode": passcode,
                             "joinUrl": link(request, m["code"], passcode if m["linkEnabled"] else None)})

    async def cancel(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        if (bad := await write_guard(request)):
            return bad
        code = request.path_params["code"]
        try:
            events = await run_in_threadpool(service.cancel, code, principal)
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(await run_in_threadpool(meeting_id_of, code), events)
        return JSONResponse({"success": True})

    async def invite(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        if request.method == "GET":
            try:
                return JSONResponse({"invitees": await run_in_threadpool(
                    service.invitees, request.path_params["code"], principal)})
            except MeetingError as exc:
                return _err(exc)
        if (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        try:
            rows, notices = await run_in_threadpool(
                service.add_invitees, request.path_params["code"], principal, data.get("invitees") or [],
                data.get("role", "attendee"))
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(None, notices)
        return JSONResponse({"invitees": rows}, status_code=201)

    async def uninvite(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return unauthorized()
        if (bad := await write_guard(request)):
            return bad
        try:
            await run_in_threadpool(service.remove_invitee, request.path_params["code"], principal,
                                    request.path_params["invitee_id"])
        except MeetingError as exc:
            return _err(exc)
        return JSONResponse({"success": True})

    # ------------------------------------------------------------------ join / act
    async def join(request: Request):
        principal = await who(request) or Principal("guest")
        if principal.kind == "member" and (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        code, ip = request.path_params["code"], client_ip(request)
        keys = [f"ip:{ip}:{code}"]
        if (limited := throttled("meeting_passcode", keys, PASSCODE_FAILURES)):
            return limited
        if principal.kind == "guest" and (limited := throttled("meeting_guest_join", [f"ip:{ip}"], GUEST_JOINS)):
            return limited
        try:
            out = await run_in_threadpool(
                lambda: service.join(code, principal, display_name=data.get("name"),
                                     passcode=str(data["passcode"]) if data.get("passcode") else None,
                                     device_id=str(data["deviceId"]) if data.get("deviceId") else None,
                                     device_label=data.get("deviceLabel"), mode=data.get("mode")))
        except Denied as exc:
            if exc.decision.rule == "d-passcode":
                rate_limit_record(engine, "meeting_passcode", keys, PASSCODE_FAILURES[1])
            if principal.kind == "guest" and exc.decision.rule == "d-guest-link-off":
                return JSONResponse({"error": "Meeting not found."}, status_code=404)
            return _err(exc)
        except NotFound as exc:
            if principal.kind == "guest":
                rate_limit_record(engine, "meeting_code_miss", [f"ip:{ip}"], CODE_MISSES[1])
            return _err(exc)
        except MeetingError as exc:
            return _err(exc)
        if principal.kind == "guest":
            rate_limit_record(engine, "meeting_guest_join", [f"ip:{ip}"], GUEST_JOINS[1])
        events = out.pop("events")
        await hub.dispatch(out["meeting"]["id"], events)
        return JSONResponse(out)

    async def act(request: Request):
        principal = await who(request)
        if principal is None:
            return unauthorized()
        if principal.kind == "member" and (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        action = data.get("action")
        if not isinstance(action, str):
            return JSONResponse({"error": "action is required."}, status_code=400)
        code = request.path_params["code"]
        try:
            out = await run_in_threadpool(service.perform, code, principal, action,
                                          data.get("payload") if isinstance(data.get("payload"), dict) else {})
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(await run_in_threadpool(meeting_id_of, code), out["events"])
        return JSONResponse({"success": True, **out["result"]})

    # ------------------------------------------------------------------ SFU control
    def need_sfu() -> JSONResponse | None:
        if sfu is None:
            return JSONResponse({"error": "Realtime media is not configured on this server."}, status_code=503)
        return None

    async def media(request: Request, action: str = P.SUBSCRIBE):
        """(principal, ctx) for a caller allowed to use media in this meeting, else an error response."""
        if (off := need_sfu()):
            return off, None
        principal = await who(request)
        if principal is None:
            return unauthorized(), None
        if principal.kind == "member" and request.method != "GET" and (bad := await write_guard(request)):
            return bad, None
        try:
            ctx = await run_in_threadpool(service.media_context, request.path_params["code"], principal, action)
        except MeetingError as exc:
            return _err(exc), None
        return principal, ctx

    def role_of(request: Request) -> str:
        role = request.path_params["role"]
        if role not in ("pub", "sub"):
            raise NotFound("Unknown media role.")
        return role

    async def sfu_session(request: Request):
        """One session per peer connection: 'pub' sends my tracks, 'sub' receives everyone else's."""
        try:
            role = role_of(request)
        except NotFound as exc:
            return _err(exc)
        principal, ctx = await media(request)
        if ctx is None:
            return principal
        try:
            session, ice = await asyncio.gather(sfu.new_session(), sfu.ice_servers())
        except SFUError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        session_id = session.get("sessionId")
        if not session_id:
            return JSONResponse({"error": "Realtime service returned no session."}, status_code=502)
        await run_in_threadpool(service.set_session, ctx["participant"]["id"], session_id, role)
        return JSONResponse({"sessionId": session_id, "iceServers": ice})

    async def ice(request: Request):
        principal, ctx = await media(request)
        if ctx is None:
            return principal
        try:
            return JSONResponse({"iceServers": await sfu.ice_servers()})
        except SFUError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    async def sfu_proxy(request: Request, mode: str):
        try:
            role = role_of(request)
        except NotFound as exc:
            return _err(exc)
        principal, ctx = await media(request)
        if ctx is None:
            return principal
        session_id = ctx["participant"]["sfu_session_id" if role == "pub" else "sfu_sub_session_id"]
        if not session_id:
            return JSONResponse({"error": "Create a media session first."}, status_code=409)
        data, error = await body(request)
        if error:
            return error
        code = request.path_params["code"]
        try:
            out: dict = {}
            if "sessionDescription" in data:
                out["sessionDescription"] = _clean_sdp(data["sessionDescription"])
            if mode == "renegotiate":
                if "sessionDescription" not in out:
                    raise Invalid("sessionDescription is required.")
            else:
                raw = data.get("tracks")
                if not isinstance(raw, list) or not raw or len(raw) > 16:
                    raise Invalid("tracks must be a list of 1-16 entries.")
                out["tracks"] = [_clean_track(t, service=service, ctx=ctx, principal=principal, code=code,
                                              mode=mode, role=role) for t in raw]
                if mode == "close" and isinstance(data.get("force"), bool):
                    out["force"] = data["force"]
        except MeetingError as exc:
            return _err(exc)
        call = {"new": sfu.add_tracks, "update": sfu.update_tracks, "close": sfu.close_tracks,
                "renegotiate": sfu.renegotiate}[mode]
        try:
            result = await call(session_id, out)
        except SFUError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        events: list = []
        pid = ctx["participant"]["id"]
        if role == "pub" and mode == "new":
            asked = {o.get("trackName") for o in out["tracks"] if o.get("location") == "local"}
            add = [{"name": t["trackName"], "kind": TRACK_RE.match(t["trackName"]).group(1), "mid": t.get("mid")}
                   for t in result.get("tracks", [])
                   if not t.get("errorCode") and t.get("trackName") in asked and TRACK_RE.match(t["trackName"])]
            if add:
                events = await run_in_threadpool(service.change_tracks, pid, add, None)
        elif role == "pub" and mode == "close":
            mids = {t["mid"] for t in out["tracks"]}
            names = [t["name"] for t in json.loads(ctx["participant"]["tracks"] or "[]") if t.get("mid") in mids]
            if names:
                events = await run_in_threadpool(service.change_tracks, pid, None, names)
        await hub.dispatch(ctx["meeting_id"], events)
        return JSONResponse(result)

    async def sfu_new(request: Request):
        return await sfu_proxy(request, "new")

    async def sfu_update(request: Request):
        return await sfu_proxy(request, "update")

    async def sfu_close(request: Request):
        return await sfu_proxy(request, "close")

    async def sfu_renegotiate(request: Request):
        return await sfu_proxy(request, "renegotiate")

    # ------------------------------------------------------------------ invitations, calls, calendar
    async def member_only(request: Request):
        principal = await who(request)
        if principal is None or principal.kind != "member":
            return None, unauthorized()
        if request.method not in ("GET", "HEAD", "OPTIONS") and (bad := csrf(request)):
            return None, bad
        return principal, None

    async def calendar(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        try:
            rows = await run_in_threadpool(service.calendar, principal, request.query_params.get("from", ""),
                                           request.query_params.get("to", ""))
        except MeetingError as exc:
            return _err(exc)
        return JSONResponse({"meetings": rows})

    async def invitations(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        try:
            return JSONResponse({"invitations": await run_in_threadpool(service.invitations, principal)})
        except MeetingError as exc:
            return _err(exc)

    async def people(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        try:
            return JSONResponse({"people": await run_in_threadpool(service.people, principal)})
        except MeetingError as exc:
            return _err(exc)

    async def rsvp(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        data, error = await body(request)
        if error:
            return error
        code = request.path_params["code"]
        try:
            notices = await run_in_threadpool(service.rsvp, code, principal, data.get("response"))
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(None, notices)
        return JSONResponse({"success": True, "response": data.get("response")})

    async def ring(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        data, error = await body(request)
        if error:
            return error
        try:
            rings, notices = await run_in_threadpool(service.ring, request.path_params["code"], principal,
                                                     data.get("userIds"))
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(None, notices)
        return JSONResponse({"rings": rings, "ringSeconds": RING_SECONDS}, status_code=201)

    async def ring_respond(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        data, error = await body(request)
        if error:
            return error
        try:
            result, notices = await run_in_threadpool(service.respond_ring, request.path_params["ring_id"],
                                                      principal, data.get("answer"))
        except MeetingError as exc:
            return _err(exc)
        await hub.dispatch(None, notices)
        return JSONResponse({"success": True, **result, "joinPath": f"/meet/{result['code']}"})

    async def usage(request: Request):
        principal = await who(request)
        if principal is None:
            return unauthorized()
        if principal.kind == "member" and (bad := await write_guard(request)):
            return bad
        data, error = await body(request)
        if error:
            return error
        try:
            await run_in_threadpool(service.usage, request.path_params["code"], principal,
                                    data.get("bytesIn", 0), data.get("bytesOut", 0))
        except MeetingError as exc:
            return _err(exc)
        return JSONResponse({"success": True})

    async def ics(request: Request):
        principal, fail = await member_only(request)
        if fail:
            return fail
        base = app_url or str(request.base_url).rstrip("/")
        code = request.path_params["code"]
        try:
            text = await run_in_threadpool(service.ics, code, principal, base)
        except MeetingError as exc:
            return _err(exc)
        return Response(text, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{code}.ics"'})

    # ------------------------------------------------------------------ websocket
    async def socket(websocket: WebSocket):
        from starlette.datastructures import Headers

        headers = Headers(scope=websocket.scope)
        if not same_origin(headers.get("origin"), headers):
            await websocket.close(code=1008)
            return
        offered = websocket.scope.get("subprotocols") or []

        def after(marker: str) -> str | None:
            return offered[offered.index(marker) + 1] if marker in offered and offered.index(marker) + 1 < len(offered) else None

        principal: Principal | None = None
        subprotocol = None
        if after("guest"):
            principal, subprotocol = Principal("guest", guest_token=after("guest")), "guest"
        else:
            if after("bearer"):
                subprotocol = "bearer"
                websocket.scope["headers"] = [*websocket.scope["headers"],
                                              (b"authorization", f"Bearer {after('bearer')}".encode())]
            elif after("seat"):
                subprotocol = "seat"
            uid, org, _identity, _browser = await run_in_threadpool(auth, websocket)
            if uid:
                principal = Principal("member", uid, org, getattr(websocket.state, "api_key_access", None),
                                      seat_token=after("seat"))
        if principal is None:
            await websocket.accept(subprotocol=subprotocol)
            await websocket.close(code=4401)
            return
        await hub.serve(websocket, websocket.path_params["code"], principal, subprotocol)

    async def inbox_socket(websocket: WebSocket):
        from starlette.datastructures import Headers

        headers = Headers(scope=websocket.scope)
        if not same_origin(headers.get("origin"), headers):
            await websocket.close(code=1008)
            return
        offered = websocket.scope.get("subprotocols") or []
        subprotocol = None
        if "bearer" in offered and len(offered) > 1:
            subprotocol = "bearer"
            websocket.scope["headers"] = [*websocket.scope["headers"],
                                          (b"authorization", f"Bearer {offered[1]}".encode())]
        uid, org, _identity, _browser = await run_in_threadpool(auth, websocket)
        if not uid:
            await websocket.accept(subprotocol=subprotocol)
            await websocket.close(code=4401)
            return
        await hub.serve_inbox(websocket, Principal("member", uid, org,
                                                   getattr(websocket.state, "api_key_access", None)), subprotocol)

    base = "/api/meetings"
    return [
        Route(base, create, methods=["POST"]),
        Route(base + "/list", listing, methods=["GET"]),
        Route(base + "/calendar", calendar, methods=["GET"]),
        Route(base + "/invitations", invitations, methods=["GET"]),
        Route(base + "/people", people, methods=["GET"]),
        Route(base + "/rings/{ring_id}/respond", ring_respond, methods=["POST"]),
        Route(base + "/{code}/preview", preview, methods=["GET"]),
        Route(base + "/{code}/join", join, methods=["POST"]),
        Route(base + "/{code}/act", act, methods=["POST"]),
        Route(base + "/{code}/rsvp", rsvp, methods=["POST"]),
        Route(base + "/{code}/ring", ring, methods=["POST"]),
        Route(base + "/{code}/usage", usage, methods=["POST"]),
        Route(base + "/{code}/ics", ics, methods=["GET"]),
        Route(base + "/{code}/invitees", invite, methods=["GET", "POST"]),
        Route(base + "/{code}/invitees/{invitee_id}", uninvite, methods=["DELETE"]),
        Route(base + "/{code}/ice", ice, methods=["GET"]),
        Route(base + "/{code}/sfu/{role}/session", sfu_session, methods=["POST"]),
        Route(base + "/{code}/sfu/{role}/tracks", sfu_new, methods=["POST"]),
        Route(base + "/{code}/sfu/{role}/tracks/update", sfu_update, methods=["PUT"]),
        Route(base + "/{code}/sfu/{role}/tracks/close", sfu_close, methods=["PUT"]),
        Route(base + "/{code}/sfu/{role}/renegotiate", sfu_renegotiate, methods=["PUT"]),
        Route(base + "/{code}", detail, methods=["GET"]),
        Route(base + "/{code}", patch, methods=["PATCH"]),
        Route(base + "/{code}", cancel, methods=["DELETE"]),
        WebSocketRoute("/ws/meetings-inbox", inbox_socket),
        WebSocketRoute("/ws/meetings/{code}", socket),
    ]
