"""Realtime change feed shared by every WebSocket client of one server process.

Sources (the ``change_event`` table stays the source of truth for catch-up):

- PostgreSQL: the change triggers ``pg_notify('cliniar_change', ...)`` at commit, so
  every server process/node LISTENing on the channel wakes on any node's write. A
  slow per-org catch-up poll is the safety net (and the primary path while the
  LISTEN connection is down). Change triggers run deferred at commit under a
  per-org advisory lock, so per-org seqs are commit-ordered and "everything after
  seq N for this org" is gap-free.
- SQLite: a 100 ms global poll, kicked immediately after a local mutation.

Each org has a ring of published batches. A batch carries its events and the
issue rows they touched (fetched once, unscoped); frames are encoded once per
(visibility set, protocol) and shared by every socket in that group, so fan-out
costs one ``send`` per client and no per-client database work.

Wire protocol (``/ws``):

- v1 (default, legacy): each frame is a JSON array of ChangeEvent objects
  ``{seq, entity, entityId, action, teamId}`` sorted by seq (unchanged format).
- v2 (``/ws?v=2``): each frame is an object::

      {"v": 2, "prev": P, "seq": S, "events": [ChangeEvent...],
       "columns": [...], "issues": [[row]...], "removed": [id...], "full": bool}

  Events are coalesced per (entity, entityId), keeping the newest (its action
  is the latest action). Contract: every change visible to this viewer with ``P < seq <= S`` is in
  ``events``. When ``full`` is true, ``issues``/``removed`` cover every issue
  event (rows use /sync's columnar format and are visibility-filtered), so a
  store at seq ``s`` with ``P <= s < S`` can apply them and move to ``S`` without
  a /sync/delta round trip; if ``s < P`` it must pull a delta. The first frame
  after connect always arrives (possibly with no events) and carries the head
  seq. ``{"v": 2, "reset": true, "seq": S}`` means "too far behind, pull".
- Visibility: events of teams the viewer cannot see are never sent; per-user
  events (notifications) carry ``recipientId`` and reach only that recipient.
- Cursor: ``after=SEQ`` replays visible events after SEQ (bounded); omitting it
  starts at the live head with no replay.
- Close codes: 4400 bad cursor, 4401 not (or no longer) authenticated, 4408 the
  client could not keep up (reconnect and catch up).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field

from starlette.websockets import WebSocketDisconnect

from cliniar_server import sync

log = logging.getLogger("cliniar_server.realtime")

NOTIFY_CHANNEL = "cliniar_change"
POLL_INTERVAL = 0.1            # SQLite / PostgreSQL-without-LISTEN
PG_SAFETY_POLL_INTERVAL = 2.0  # PostgreSQL while LISTEN is healthy
NOTIFY_COALESCE = 0.002        # gather a burst of notifications into one batch
RING_BATCHES = 4096
ROW_PUSH_LIMIT = 200           # issue rows pushed inline per batch; beyond, clients pull
MAX_REPLAY = 500
VISIBILITY_TTL = 30.0
REVALIDATE_SECONDS = 5.0
SEND_TIMEOUT = 10.0
FEED_IDLE_SECONDS = 600.0
# Entities that can change who sees which team.
VISIBILITY_ENTITIES = {"team", "teamMember", "user", "member"}
_dumps = json.JSONEncoder(separators=(",", ":")).encode


def coalesce(events: list[dict]) -> list[dict]:
    """Keep the newest event per (entity, entityId), in seq order."""
    if len(events) < 2:
        return events
    latest: dict[tuple, dict] = {}
    for e in events:
        latest[(e["entity"], e["entityId"])] = e
    if len(latest) == len(events):
        return events
    return sorted(latest.values(), key=lambda e: e["seq"])


def _visible_events(events: list[dict], vis: frozenset | None, uid: str) -> list[dict]:
    """Team-visible events; per-user events (recipientId) only for their recipient."""
    return [e for e in events
            if (vis is None or e["teamId"] is None or e["teamId"] in vis)
            and e.get("recipientId", uid) == uid]


def issue_payload(events: list[dict], rows: dict | None, vis: frozenset | None) -> tuple[list, list]:
    """(visible rows, removed ids) for the issue events, given unscoped rows by id."""
    issues, removed = [], []
    for iid in dict.fromkeys(e["entityId"] for e in events if e["entity"] == "issue"):
        row = rows.get(iid) if rows is not None else None
        if row is not None and sync.row_visible(row, vis):
            issues.append(row)
        else:
            removed.append(iid)  # deleted, archived, or moved to a team this viewer cannot see
    return issues, removed


class Batch:
    __slots__ = ("entities", "events", "first_seq", "frames", "idx", "issue_ids", "recipients", "rows", "seq")

    def __init__(self, idx: int, events: list[dict], rows: dict | None) -> None:
        self.idx = idx
        self.events = events
        self.first_seq = events[0]["seq"]
        self.seq = events[-1]["seq"]
        self.rows = rows  # issue id -> unscoped row or None (gone); None when not fetched
        self.entities = {e["entity"] for e in events}
        self.issue_ids = {e["entityId"] for e in events if e["entity"] == "issue"}
        self.recipients = {e["recipientId"] for e in events if "recipientId" in e}
        self.frames: dict = {}

    def body(self, vis: frozenset | None, proto: int, uid: str) -> str | None:
        """Encoded frame for a visibility group (v2 bodies lack the per-socket prev).

        Viewers share a frame unless the batch holds a per-user event for them.
        """
        who = uid if uid in self.recipients else None
        key = (vis, proto, who)
        if key in self.frames:
            return self.frames[key]
        evs = _visible_events(self.events, vis, uid if who else "")
        if not evs:
            out = None
        elif proto == 1:
            out = _dumps(evs)  # legacy frames keep every event, exactly as before
        else:
            evs = coalesce(evs)
            issues, removed = issue_payload(evs, self.rows, vis)
            out = _dumps({"seq": self.seq, "events": evs, "columns": sync.ISSUE_COLUMNS,
                          "issues": issues, "removed": removed, "full": self.rows is not None})
        self.frames[key] = out
        return out


def v2_frame(prev: int, body: str) -> str:
    return '{"v":2,"prev":' + str(int(prev)) + "," + body[1:]


@dataclass(eq=False)
class Conn:
    uid: str
    org: str
    cred: tuple | None
    websocket: object
    closing: int | None = None
    ended: bool = False
    task: asyncio.Task | None = None


@dataclass(eq=False)
class Feed:
    org: str
    head: int                      # newest published seq for this org
    covered_from: int              # every event with seq > covered_from is in the ring
    ring: deque = field(default_factory=deque)
    next_idx: int = 1
    waiter: asyncio.Future | None = None
    conns: set = field(default_factory=set)
    vis: dict = field(default_factory=dict)  # uid -> (monotonic time, frozenset | None)
    vis_gen: int = 0
    vis_task: asyncio.Future | None = None
    meta_seq: int = 0
    last_active: float = field(default_factory=time.monotonic)

    def wake(self) -> None:
        old, self.waiter = self.waiter, asyncio.get_running_loop().create_future()
        if old is not None and not old.done():
            old.set_result(None)

    def publish(self, events: list[dict], rows: dict | None) -> Batch:
        batch = Batch(self.next_idx, events, rows)
        self.next_idx += 1
        self.ring.append(batch)
        if len(self.ring) > RING_BATCHES:
            self.covered_from = self.ring.popleft().seq
        self.head = max(self.head, batch.seq)
        self.last_active = time.monotonic()
        if batch.entities & VISIBILITY_ENTITIES:
            self.vis.clear()
            self.vis_gen += 1
        if batch.entities & sync.META_ENTITIES:
            self.meta_seq = batch.seq
        self.wake()
        return batch

    def batches_after(self, idx: int) -> list[Batch] | None:
        """Batches newer than idx, or None when some were evicted from the ring."""
        if not self.ring:
            return [] if idx >= self.next_idx - 1 else None
        start = idx + 1 - self.ring[0].idx
        if start < 0:
            return None
        return list(itertools.islice(self.ring, start, None))


class RealtimeHub:
    def __init__(self, store, *, poll_interval: float = POLL_INTERVAL) -> None:
        self.store = store
        self.dialect = store.engine.dialect.name
        self.poll_interval = poll_interval
        self.feeds: dict[str, Feed] = {}
        self.conns: set[Conn] = set()
        self.cursor = 0  # SQLite global poll cursor
        self.listening = False
        self.stats = {"batches": 0, "events": 0, "notifies": 0, "polls": 0, "frames": 0, "row_fetches": 0,
                      "snapshot_derived": 0, "snapshot_full": 0}
        self._ingest_lock = asyncio.Lock()
        self._kick = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._pending: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._last_poll_error = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self.validate = None  # set by the app: creds -> {cred: (uid, org)}

    # ------------------------------------------------------------------ lifecycle
    async def ensure_started(self) -> None:
        """Start on the running loop (restarting from scratch if the loop changed, as in tests)."""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self.feeds, self.conns, self._tasks, self._pending = {}, set(), [], []
        self._ingest_lock, self._kick, self._flush_task = asyncio.Lock(), asyncio.Event(), None
        await self.start()

    async def start(self) -> None:
        try:
            self.cursor = await asyncio.to_thread(self.store.max_change_seq)
        except Exception:
            log.warning("realtime feed starting without change_event (schema not migrated?)")
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        self._tasks.append(asyncio.create_task(self._revalidate_loop()))
        self._tasks.append(asyncio.create_task(self._evict_loop()))
        if self.dialect == "postgresql":
            self._tasks.append(asyncio.create_task(self._listen_loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._loop = None

    def kick(self) -> None:
        """A local write committed: poll now instead of at the next tick."""
        self._kick.set()

    # ------------------------------------------------------------------ feeds
    async def feed(self, org: str) -> Feed:
        await self.ensure_started()
        feed = self.feeds.get(org)
        if feed is not None:
            return feed
        latest = await asyncio.to_thread(self.store.latest_change_seq, org)
        feed = self.feeds.get(org)
        if feed is None:
            feed = self.feeds[org] = Feed(org=org, head=latest, covered_from=latest)
            feed.wake()
        return feed

    async def ingest(self, rows: list[dict]) -> None:
        """Publish raw events (with organization_id) as one batch per org, in seq order."""
        if not rows:
            return
        async with self._ingest_lock:
            by_org: dict[str, list[dict]] = {}
            for r in rows:
                feed = self.feeds.get(r["organization_id"])
                if feed is not None and r["seq"] <= feed.head:
                    continue  # already published (per-org seqs are commit-ordered)
                ev = {"seq": r["seq"], "entity": r["entity"], "entityId": r["entityId"],
                      "action": r["action"], "teamId": r["teamId"]}
                if r.get("recipientId") is not None:
                    ev["recipientId"] = r["recipientId"]
                by_org.setdefault(r["organization_id"], []).append(ev)
            if not by_org:
                return
            for evs in by_org.values():
                evs.sort(key=lambda e: e["seq"])
            wanted = {}
            for org, evs in by_org.items():
                feed = self.feeds.get(org)
                if feed is None or not feed.conns:
                    continue  # nobody watching: deltas read rows from the database instead
                ids = list(dict.fromkeys(e["entityId"] for e in evs if e["entity"] == "issue"))
                if 0 < len(ids) <= ROW_PUSH_LIMIT:
                    wanted[org] = ids
            fetched: dict[str, dict] = {}
            if wanted:
                try:
                    fetched = await asyncio.to_thread(self._fetch_rows, wanted)
                    self.stats["row_fetches"] += 1
                except Exception:
                    log.exception("realtime row fetch failed; frames fall back to invalidation")
            for org, evs in by_org.items():
                feed = self.feeds.get(org)
                if feed is None:
                    # First event seen for this org: everything before it is in the database.
                    feed = self.feeds[org] = Feed(org=org, head=evs[0]["seq"] - 1,
                                                  covered_from=evs[0]["seq"] - 1)
                    feed.wake()
                has_issue = any(e["entity"] == "issue" for e in evs)
                feed.publish(evs, fetched.get(org) if has_issue else {})
                self.stats["batches"] += 1
                self.stats["events"] += len(evs)

    def _fetch_rows(self, wanted: dict[str, list[str]]) -> dict[str, dict]:
        out = {}
        for org, ids in wanted.items():
            rows = {r[0]: r for r in sync.issues_by_id(self.store, org, None, ids)}
            out[org] = {iid: rows.get(iid) for iid in ids}
        return out

    # ------------------------------------------------------------------ sources
    async def _poll_loop(self) -> None:
        while True:
            self._kick.clear()
            try:
                if self.dialect == "postgresql":
                    await self._poll_orgs()
                else:
                    await self._poll_global()
            except asyncio.CancelledError:
                raise
            except Exception:
                now = time.monotonic()
                if now - self._last_poll_error > 60:
                    self._last_poll_error = now
                    log.exception("realtime poll failed")
            interval = PG_SAFETY_POLL_INTERVAL if self.listening else self.poll_interval
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._kick.wait(), timeout=interval)

    async def _poll_global(self) -> None:
        while True:
            rows = await asyncio.to_thread(self.store.change_events_after_global, self.cursor, 5000)
            self.stats["polls"] += 1
            if not rows:
                return
            self.cursor = max(self.cursor, rows[-1]["seq"])
            await self.ingest(rows)
            if len(rows) < 5000:
                return

    async def _poll_orgs(self) -> None:
        heads = {org: feed.head for org, feed in self.feeds.items()}
        if not heads:
            return
        rows = await asyncio.to_thread(self.store.change_events_after_orgs, heads)
        self.stats["polls"] += 1
        await self.ingest(rows)

    def _pg_dsn(self) -> str:
        url = self.store.engine.url.set(drivername="postgresql")
        return url.render_as_string(hide_password=False)

    async def _listen_loop(self) -> None:
        import psycopg

        backoff = 0.5
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self._pg_dsn(), autocommit=True) as conn:
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    self.listening = True
                    backoff = 0.5
                    # Catch up anything committed while we were not listening.
                    self._kick.set()
                    async for note in conn.notifies():
                        self._on_notify(note.payload)
            except asyncio.CancelledError:
                self.listening = False
                raise
            except Exception:
                log.warning("realtime LISTEN connection lost; polling until it recovers", exc_info=True)
            self.listening = False
            self._kick.set()
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2)

    def _on_notify(self, payload: str) -> None:
        try:
            seq, org, entity, entity_id, action, team_id, *rest = json.loads(payload)
        except (ValueError, TypeError):
            return
        self.stats["notifies"] += 1
        self._pending.append({"seq": int(seq), "organization_id": org, "entity": entity,
                              "entityId": entity_id, "action": action, "teamId": team_id,
                              "recipientId": rest[0] if rest else None})
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_notifies())

    async def _flush_notifies(self) -> None:
        await asyncio.sleep(NOTIFY_COALESCE)
        while self._pending:
            rows, self._pending = self._pending, []
            try:
                await self.ingest(rows)
            except Exception:
                log.exception("realtime ingest failed; the safety poll will retry")
                self._kick.set()

    # ------------------------------------------------------------------ visibility
    async def visibility(self, feed: Feed, uid: str) -> frozenset | None:
        """Viewer's visible teams, refreshed for every subscriber of the org in one read."""
        while True:
            now = time.monotonic()
            entry = feed.vis.get(uid)
            if entry is not None and now - entry[0] < VISIBILITY_TTL:
                return entry[1]
            if feed.vis_task is not None:
                with suppress(Exception):
                    await asyncio.shield(feed.vis_task)
                continue
            task = feed.vis_task = asyncio.get_running_loop().create_future()
            gen = feed.vis_gen
            uids = {uid} | {c.uid for c in feed.conns
                            if (e := feed.vis.get(c.uid)) is None or now - e[0] >= VISIBILITY_TTL / 2}
            try:
                result = await asyncio.to_thread(self.store.visibility_map, feed.org, uids)
            except BaseException as exc:
                feed.vis_task = None
                task.set_exception(exc)
                task.exception()
                raise
            feed.vis_task = None
            task.set_result(None)
            if gen != feed.vis_gen:
                continue  # membership changed while we read; read again
            for u, v in result.items():
                feed.vis[u] = (now, v)
            return result[uid]

    # ------------------------------------------------------------------ revalidation
    async def _revalidate_loop(self) -> None:
        while True:
            await asyncio.sleep(REVALIDATE_SECONDS)
            conns = [c for c in self.conns if c.cred is not None and c.closing is None]
            if not conns or self.validate is None:
                continue
            try:
                valid = await asyncio.to_thread(self.validate, {c.cred for c in conns})
            except Exception:
                log.exception("realtime credential revalidation failed")
                continue
            for c in conns:
                if valid.get(c.cred) != (c.uid, c.org):
                    self.close(c, 4401)

    def close(self, conn: Conn, code: int) -> None:
        conn.closing = code
        if conn.task is not None and not conn.task.done():
            conn.task.cancel()

    async def _evict_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for org, feed in list(self.feeds.items()):
                if not feed.conns and now - feed.last_active > FEED_IDLE_SECONDS:
                    del self.feeds[org]

    # ------------------------------------------------------------------ snapshot derivation
    async def wait_head(self, org: str, seq: int, timeout: float = 0.25) -> bool:
        """Wait (briefly) until this org's feed has published everything up to seq."""
        feed = self.feeds.get(org)
        if feed is None:
            return False
        deadline = time.monotonic() + timeout
        while feed.head < seq:
            left = deadline - time.monotonic()
            if left <= 0 or self.feeds.get(org) is not feed:
                return False
            self.kick()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(feed.waiter), timeout=left)
        return True

    def issue_changes(self, org: str, after: int, upto: int) -> dict | None:
        """Unscoped rows (None = gone) of issues changed in (after, upto], or None if unknown."""
        feed = self.feeds.get(org)
        if feed is None or after < feed.covered_from or feed.head < upto:
            return None
        changes: dict = {}
        for batch in feed.ring:
            if batch.seq <= after:
                continue
            if batch.first_seq > upto:
                break
            if "workflowState" in batch.entities:
                return None  # a state's type change can move many issues in or out of the window
            ids = {e["entityId"] for e in batch.events
                   if e["entity"] == "issue" and after < e["seq"] <= upto}
            if ids and batch.rows is None:
                return None
            for i in ids:
                changes[i] = batch.rows.get(i)
        return changes

    # ------------------------------------------------------------------ delta from memory
    async def delta(self, org: str, uid: str, after: int) -> dict | None:
        """/sync/delta served from the ring, or None when the database must answer."""
        await self.ensure_started()
        feed = self.feeds.get(org)
        if feed is None or after < feed.covered_from or after > feed.head:
            return None
        # Read-your-writes: a client pulling right after its own mutation must see
        # it even if the broadcast has not ingested it yet (one indexed MAX query).
        if await asyncio.to_thread(self.store.latest_change_seq, org) > feed.head:
            return None
        events: list[dict] = []
        batches: list[Batch] = []
        for batch in reversed(feed.ring):
            if batch.seq <= after:
                break
            batches.append(batch)
        batches.reverse()
        for batch in batches:
            events.extend(e for e in batch.events if e["seq"] > after)
        head = feed.head
        if len(events) > sync.MAX_DELTA_EVENTS:
            return {"v": sync.SYNC_VERSION, "reload": True, "seq": head}
        vis = await self.visibility(feed, uid)
        events = _visible_events(events, vis, uid)
        entities = {e["entity"] for e in events}
        if entities & sync.RELOAD_ENTITIES:
            return {"v": sync.SYNC_VERSION, "reload": True, "seq": head}
        rows: dict = {}
        missing: list[str] = []
        touched = list(dict.fromkeys(e["entityId"] for e in events if e["entity"] == "issue"))
        if touched:
            want = set(touched)
            for batch in reversed(batches):  # newest row wins
                if not want:
                    break
                for i in batch.issue_ids & want:
                    want.discard(i)
                    if batch.rows is None:
                        missing.append(i)
                    else:
                        rows[i] = batch.rows.get(i)
            missing.extend(want)
        issues, removed = issue_payload([e for e in events if e["entity"] == "issue"
                                         and e["entityId"] not in missing], rows, vis)
        if missing:
            fresh = await asyncio.to_thread(sync.issues_by_id, self.store, org, uid, missing)
            present = {r[0] for r in fresh}
            issues.extend(fresh)
            removed.extend(i for i in missing if i not in present)
        return {
            "v": sync.SYNC_VERSION, "reload": False, "seq": head,
            "columns": sync.ISSUE_COLUMNS, "issues": issues, "removed": removed,
            "meta": bool(entities & sync.META_ENTITIES),
            "other": sorted(entities - sync.META_ENTITIES - {"issue"}),
        }

    # ------------------------------------------------------------------ sockets
    async def serve(self, websocket, uid: str, org: str, cred: tuple | None,
                    cursor: int | None, proto: int) -> None:
        feed = await self.feed(org)
        conn = Conn(uid=uid, org=org, cred=cred, websocket=websocket)
        conn.task = asyncio.current_task()
        feed.conns.add(conn)
        self.conns.add(conn)
        reader = asyncio.create_task(self._read_until_closed(websocket, conn))
        try:
            await self._pump(feed, conn, cursor, proto)
        except asyncio.CancelledError:
            if conn.closing is None and not conn.ended:
                raise
        except asyncio.TimeoutError:
            conn.closing = 4408  # could not keep up; it reconnects and catches up from its cursor
        except (RuntimeError, OSError, ConnectionError, WebSocketDisconnect):
            pass  # client went away mid-send
        finally:
            reader.cancel()
            feed.conns.discard(conn)
            self.conns.discard(conn)
            if conn.closing is not None and not conn.ended:
                with suppress(Exception):
                    await asyncio.wait_for(websocket.close(code=conn.closing), timeout=1.0)

    async def _read_until_closed(self, websocket, conn: Conn) -> None:
        with suppress(asyncio.CancelledError):
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
            except (WebSocketDisconnect, RuntimeError):
                pass
            conn.ended = True
            if conn.task is not None and not conn.task.done():
                conn.task.cancel()

    async def _send(self, websocket, text: str) -> None:
        await asyncio.wait_for(websocket.send_text(text), timeout=SEND_TIMEOUT)
        self.stats["frames"] += 1

    async def _backlog(self, feed: Feed, conn: Conn, cursor: int, proto: int) -> tuple[str | None, int]:
        """Replay frame for events after cursor, and the max seq it covers."""
        latest = feed.head
        start = cursor
        if proto == 1 and latest - cursor > MAX_REPLAY:
            start = max(0, latest - MAX_REPLAY)  # legacy clients refetch on any event anyway
        events = await asyncio.to_thread(self.store.change_events_after, conn.org, start,
                                         MAX_REPLAY + 1, conn.uid)
        if len(events) > MAX_REPLAY:
            if proto == 1:
                events = events[-MAX_REPLAY:]
            else:
                head = max(feed.head, events[-1]["seq"])
                return _dumps({"v": 2, "reset": True, "seq": head}), head
        top = max([feed.head] + [e["seq"] for e in events])
        if proto == 1:
            return (_dumps(events) if events else None), top
        evs = coalesce(events)
        issues, removed, full = [], [], True
        ids = list(dict.fromkeys(e["entityId"] for e in evs if e["entity"] == "issue"))
        if len(ids) > ROW_PUSH_LIMIT:
            full = False
        elif ids:
            fresh = await asyncio.to_thread(sync.issues_by_id, self.store, conn.org, conn.uid, ids)
            present = {r[0] for r in fresh}
            issues, removed = fresh, [i for i in ids if i not in present]
        body = _dumps({"seq": top, "events": evs, "columns": sync.ISSUE_COLUMNS,
                       "issues": issues, "removed": removed, "full": full})
        return v2_frame(cursor, body), top

    async def _pump(self, feed: Feed, conn: Conn, cursor: int | None, proto: int) -> None:
        ws = conn.websocket
        pos = feed.next_idx - 1
        if cursor is None:
            # Start at the database head, not the feed's: events committed but not
            # yet broadcast are history this client's snapshot already covers.
            sent = max(feed.head, await asyncio.to_thread(self.store.latest_change_seq, conn.org))
            if proto == 2:
                await self._send(ws, _dumps({"v": 2, "prev": sent, "seq": sent, "events": [],
                                             "columns": sync.ISSUE_COLUMNS, "issues": [],
                                             "removed": [], "full": True}))
        else:
            frame, sent = await self._backlog(feed, conn, cursor, proto)
            if frame is not None:
                await self._send(ws, frame)
        prev = sent
        while True:
            waiter = feed.waiter
            batches = feed.batches_after(pos)
            if batches is None:
                # Fell off the ring (very slow client): catch up from the database.
                pos = feed.next_idx - 1
                frame, top = await self._backlog(feed, conn, prev, proto)
                if frame is not None:
                    await self._send(ws, frame)
                prev = sent = max(sent, top)
                continue
            if batches:
                vis = await self.visibility(feed, conn.uid)
                for batch in batches:
                    pos = batch.idx
                    if batch.seq <= sent:
                        continue
                    if batch.first_seq <= sent:
                        # Overlaps the replayed backlog: build an unshared frame without the dupes.
                        part = Batch(batch.idx, [e for e in batch.events if e["seq"] > sent], batch.rows)
                        body = part.body(vis, proto, conn.uid)
                    else:
                        body = batch.body(vis, proto, conn.uid)
                    if body is not None:
                        await self._send(ws, body if proto == 1 else v2_frame(prev, body))
                        prev = batch.seq
                    sent = max(sent, batch.seq)
                continue
            await asyncio.shield(waiter)
