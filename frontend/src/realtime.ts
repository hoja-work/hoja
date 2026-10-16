import {getSync, storedSyncSeq} from './sync';

/* Realtime change feed client (protocol v2, see cliniar_server/realtime.py).
 *
 * Frames: {v:2, prev, seq, events, columns, issues, removed, full} or {v:2, reset:true, seq}.
 * Every change visible to this viewer with prev < seq <= frame.seq is in `events`
 * (coalesced per entity, newest action wins). When `full`, `issues` (sync columnar
 * rows, visibility-filtered) and `removed` cover every issue event, so a store at seq s
 * with prev <= s < frame.seq may apply them and move to frame.seq without a
 * /sync/delta pull; if s < prev it must pull. Frame handlers registered with
 * onRealtimeFrame() get the raw frame first (the store hook goes there).
 *
 * Cursor: localStorage `hoja_change_cursor` holds the last frame seq. The first
 * connect of a page starts from max(that, the IndexedDB snapshot seq the store
 * persisted), so the server never replays history the snapshot already covers;
 * with neither it starts at the live head. Reconnects resume from the last frame.
 */

export type ChangeEvent = {seq: number; entity: string; entityId: string; action: string; teamId: string | null; recipientId?: string};
export type RealtimeFrame = {
  v: 2; seq: number; prev?: number; reset?: boolean; events?: ChangeEvent[];
  columns?: string[]; issues?: unknown[][]; removed?: string[]; full?: boolean;
};

const CURSOR_KEY = 'hoja_change_cursor';
const frameHandlers = new Set<(frame: RealtimeFrame) => void>();

/** Observe every v2 frame (before per-event callbacks). Returns unsubscribe. */
export function onRealtimeFrame(cb: (frame: RealtimeFrame) => void): () => void {
  frameHandlers.add(cb);
  return () => { frameHandlers.delete(cb); };
}

function readCursor(): number {
  try { return Number(localStorage.getItem(CURSOR_KEY) || '0') || 0; } catch { return 0; }
}
function writeCursor(seq: number) {
  try { if (seq > readCursor()) localStorage.setItem(CURSOR_KEY, String(seq)); } catch { /* storage may be unavailable */ }
}

export function connectRealtime(token: string | null, onChange: (event: ChangeEvent) => void): () => void {
  let socket: WebSocket | null = null;
  let retry = 0;
  let timer: number | undefined;
  let closed = false;
  let cursor: number | null = null; // last frame seq seen by this page
  let lastClose = 0;

  const url = async () => {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    if (cursor === null) {
      const known = Math.max(readCursor(), (await storedSyncSeq()) || 0);
      if (known > 0) cursor = known;
    }
    const protocols = token && token !== 'session' ? ['bearer', token] : undefined;
    const after = cursor !== null ? `&after=${cursor}` : '';
    return {href: `${scheme}://${location.host}/ws?v=2${after}`, protocols};
  };

  const handle = (frame: RealtimeFrame) => {
    for (const cb of frameHandlers) cb(frame);
    if (frame.reset) {
      cursor = frame.seq;
      writeCursor(frame.seq);
      void getSync()?.pull();
      return;
    }
    const events = frame.events || [];
    if (!events.length) {
      // Connect/hello frame: nothing new for us, but close the gap between the
      // store's last pull and the head this socket started at (cheap, memory-served).
      if (cursor === null || frame.seq > cursor) void getSync()?.pull();
    }
    for (const event of events) onChange(event);
    cursor = Math.max(cursor ?? 0, frame.seq);
    writeCursor(frame.seq);
  };

  const open = async () => {
    const target = await url();
    if (closed) return;
    socket = target.protocols ? new WebSocket(target.href, target.protocols) : new WebSocket(target.href);
    socket.onopen = () => { retry = 0; };
    socket.onmessage = (message) => {
      lastClose = 0;
      const parsed = JSON.parse(message.data);
      if (Array.isArray(parsed)) {
        // Legacy server (v1 arrays).
        for (const event of parsed as ChangeEvent[]) {
          cursor = Math.max(cursor ?? 0, event.seq);
          writeCursor(event.seq);
          onChange(event);
        }
        return;
      }
      if (parsed && parsed.v === 2) handle(parsed as RealtimeFrame);
    };
    socket.onclose = (close) => {
      socket = null;
      if (closed) return;
      if (close.code === 4401) {
        // Credential revoked or replaced (e.g. workspace switch). Retry once; a second
        // 4401 in a row means we are signed out.
        const repeated = lastClose === 4401;
        lastClose = 4401;
        if (repeated) return;
        timer = window.setTimeout(() => { void open(); }, 1000);
        return;
      }
      lastClose = close.code;
      const delay = Math.min(30000, 500 * 2 ** retry) * (0.5 + Math.random() / 2);
      retry += 1;
      timer = window.setTimeout(() => { void open(); }, delay);
    };
  };

  void open();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    socket?.close();
  };
}
