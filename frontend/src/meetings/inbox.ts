/* Personal meetings inbox: one socket per signed-in tab that delivers invitations, incoming calls,
 * reminders and RSVP/cancel updates. State lives in a small zustand store so the notifier, the
 * sidebar badge and the meetings page all read the same thing. */
import {create} from 'zustand';
import {apiToken, type Meeting, type Ring} from './api';
import {startRingtone, stopRingtone} from './ringtone';

export type Toast = {
  id: string; kind: 'invited' | 'starting' | 'info' | 'cancelled' | 'rsvp';
  title: string; body?: string; code?: string; meeting?: Meeting; actions?: boolean;
};

type InboxState = {
  connected: boolean;
  invitations: Meeting[];
  rings: Ring[];
  toasts: Toast[];
  /** Bumps whenever something a page may be showing changed (refetch trigger). */
  version: number;
  /** Last answer to a call I placed (accept / decline / missed). */
  lastAnswer: {id: string; answer: string; code: string; by: string | null} | null;
  dismissToast: (id: string) => void;
  removeInvitation: (code: string) => void;
  removeRing: (id: string) => void;
};

export const useInbox = create<InboxState>((set) => ({
  connected: false,
  invitations: [],
  rings: [],
  toasts: [],
  version: 0,
  lastAnswer: null,
  dismissToast: (id) => set((s) => ({toasts: s.toasts.filter((t) => t.id !== id)})),
  removeInvitation: (code) => set((s) => ({invitations: s.invitations.filter((i) => i.code !== code)})),
  removeRing: (id) => set((s) => ({rings: s.rings.filter((r) => r.id !== id)})),
}));

let counter = 0;
const push = (toast: Omit<Toast, 'id'>, ttl = 9000) => {
  const id = `t${Date.now()}-${counter++}`;
  useInbox.setState((s) => ({toasts: [...s.toasts.slice(-3), {...toast, id}]}));
  if (ttl) window.setTimeout(() => useInbox.getState().dismissToast(id), ttl);
};
const bump = () => useInbox.setState((s) => ({version: s.version + 1}));

function systemNotify(title: string, body: string, tag: string) {
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) {
      new Notification(title, {body, tag});
    }
  } catch { /* notifications are optional */ }
}

export function askNotificationPermission() {
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') void Notification.requestPermission();
  } catch { /* optional */ }
}

function syncRingtone() {
  useInbox.getState().rings.length ? startRingtone() : stopRingtone();
}

function handle(msg: any) {
  const set = useInbox.setState;
  switch (msg.t) {
    case 'hello':
      set({invitations: msg.invitations || [], rings: msg.rings || [], connected: true});
      syncRingtone();
      break;
    case 'invited': {
      const meeting: Meeting = msg.meeting;
      set((s) => ({invitations: [meeting, ...s.invitations.filter((i) => i.code !== meeting.code)]}));
      push({kind: 'invited', title: `${msg.by || 'Someone'} invited you`, body: meeting.title, code: meeting.code, meeting, actions: true}, 12000);
      systemNotify(`${msg.by || 'Someone'} invited you`, meeting.title, `inv-${meeting.code}`);
      bump();
      break;
    }
    case 'meeting_updated':
      set((s) => ({invitations: s.invitations.map((i) => (i.code === msg.meeting.code ? msg.meeting : i))}));
      push({kind: 'info', title: 'Meeting updated', body: msg.meeting.title, code: msg.meeting.code});
      bump();
      break;
    case 'meeting_cancelled':
      set((s) => ({invitations: s.invitations.filter((i) => i.code !== msg.code), rings: s.rings.filter((r) => r.meeting.code !== msg.code)}));
      push({kind: 'cancelled', title: 'Meeting cancelled', body: msg.title});
      syncRingtone();
      bump();
      break;
    case 'rsvp':
      push({kind: 'rsvp', title: `${msg.by || 'Someone'} ${msg.response === 'accepted' ? 'accepted' : msg.response === 'declined' ? 'declined' : 'might attend'}`, body: msg.title, code: msg.code});
      bump();
      break;
    case 'starting_soon':
      push({kind: 'starting', title: msg.minutes <= 0 ? 'Starting now' : `Starting in ${msg.minutes} min`, body: msg.meeting.title, code: msg.meeting.code, meeting: msg.meeting, actions: true}, 20000);
      systemNotify(msg.minutes <= 0 ? 'Meeting starting now' : `Meeting in ${msg.minutes} min`, msg.meeting.title, `soon-${msg.meeting.code}`);
      break;
    case 'ring':
      set((s) => ({rings: [...s.rings.filter((r) => r.id !== msg.ring.id), msg.ring]}));
      systemNotify(`${msg.ring.from.name || 'Someone'} is calling`, msg.ring.meeting.title, `ring-${msg.ring.id}`);
      syncRingtone();
      break;
    case 'ring_ended':
      set((s) => ({rings: s.rings.filter((r) => r.id !== msg.id)}));
      syncRingtone();
      break;
    case 'ring_answered':
      set({lastAnswer: {id: msg.id, answer: msg.answer, code: msg.code, by: msg.by}});
      if (msg.answer === 'decline') push({kind: 'info', title: `${msg.by || 'They'} declined the call`});
      else if (msg.answer === 'missed') push({kind: 'info', title: `${msg.by || 'They'} didn't answer`});
      break;
  }
}

let socket: WebSocket | null = null;
let retry = 0;
let timer: number | undefined;
let wanted = false;
let heartbeat: number | undefined;

function open() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const token = apiToken();
  const ws = token ? new WebSocket(`${scheme}://${location.host}/ws/meetings-inbox`, ['bearer', token])
    : new WebSocket(`${scheme}://${location.host}/ws/meetings-inbox`);
  socket = ws;
  ws.onopen = () => { retry = 0; };
  ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch { /* ignore malformed */ } };
  ws.onclose = (e) => {
    window.clearInterval(heartbeat);
    useInbox.setState({connected: false});
    socket = null;
    if (!wanted || e.code === 4401) return;
    timer = window.setTimeout(open, Math.min(30000, 600 * 2 ** retry++) * (0.6 + Math.random() * 0.4));
  };
  heartbeat = window.setInterval(() => { if (ws.readyState === 1) ws.send('{"t":"ping"}'); }, 25000);
}

/** Start (idempotent) the personal socket; returns a stop function. */
export function connectInbox(): () => void {
  wanted = true;
  if (!socket) open();
  return () => {
    wanted = false;
    window.clearTimeout(timer);
    window.clearInterval(heartbeat);
    socket?.close();
    socket = null;
    stopRingtone();
  };
}
