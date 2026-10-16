/* Typed client for the meetings REST API (see docs/meetings.md). Members authenticate with the
 * session cookie or a stored API token; public-link guests present an opaque per-meeting token. */

export type Role = 'host' | 'cohost' | 'attendee';
export type PState = 'waiting' | 'admitted' | 'left' | 'denied' | 'removed';
export type Rsvp = 'needsAction' | 'accepted' | 'declined' | 'tentative';
export type Access = 'invited' | 'team' | 'org';

export type TrackInfo = {name: string; kind: 'mic' | 'cam' | 'screen'; mid?: string | null};
export type Participant = {
  id: string; name: string; kind: 'member' | 'guest'; role: Role; state: PState;
  muted: boolean; handRaised: boolean; audio: boolean; video: boolean; screen: boolean;
  sessionId: string | null; tracks: TrackInfo[];
};
export type MeetingSettings = {
  waitingRoom: boolean; joinBeforeHost: boolean; muteOnEntry: boolean; allowUnmute: boolean;
  screenShare: 'all' | 'host'; chatEnabled: boolean; locked: boolean; recording: 'off' | 'allowed' | 'recording';
  maxParticipants: number;
};
export type Meeting = {
  id: string; code: string; title: string; description: string | null; hostId: string; hostName: string | null;
  teamId: string | null; issueId: string | null; projectId: string | null; access: Access; linkEnabled: boolean;
  hasPasscode: boolean; status: 'scheduled' | 'live' | 'ended' | 'cancelled'; scheduledStart: string | null;
  durationMinutes: number | null; startedAt: string | null; endedAt: string | null; createdAt?: string; joinPath: string;
  settings: MeetingSettings; participantCount?: number; isHost?: boolean; myResponse?: Rsvp | null;
  invitedBy?: string | null; usage?: {egressBytes: number; ingressBytes: number; estimatedCostUsd: number};
};
export type Person = {id: string; name: string; email: string | null; avatarUrl: string | null};
export type Invitee = {id: string; userId: string | null; email: string | null; name: string | null; role: string; response?: Rsvp};
export type ChatMessage = {id: string; from: string; to: string | null; body: string; at: string};
export type Snapshot = {meeting: Meeting; you: Participant; participants: Participant[]; waiting: Participant[]; chat: ChatMessage[]};
export type Ring = {id: string; from: {id: string; name: string | null}; meeting: Meeting; expiresAt: string};
export type Preview = {code: string; title: string; status: string; hostName: string | null; scheduledStart: string | null; passcodeRequired: boolean; waitingRoom: boolean};

export class ApiError extends Error {
  status: number;
  rule?: string;
  constructor(message: string, status: number, rule?: string) {
    super(message);
    this.status = status;
    this.rule = rule;
  }
}

export function apiToken(): string | null {
  try {
    const t = localStorage.getItem('hoja_token');
    return t && t !== 'session' ? t : null;
  } catch { return null; }
}

const guestKey = (code: string) => `hoja_meeting_guest_${code}`;
export type GuestSeat = {token: string; name: string};
export function loadGuest(code: string): GuestSeat | null {
  try { return JSON.parse(sessionStorage.getItem(guestKey(code)) || 'null'); } catch { return null; }
}
export function saveGuest(code: string, seat: GuestSeat | null) {
  try { seat ? sessionStorage.setItem(guestKey(code), JSON.stringify(seat)) : sessionStorage.removeItem(guestKey(code)); } catch { /* private mode */ }
}

/** Guest token for the current call, set by the room once joined (members never set it). */
let activeGuest: string | null = null;
export function useGuestToken(token: string | null) { activeGuest = token; }

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = apiToken();
  if (token) headers.authorization = `Bearer ${token}`;
  if (activeGuest) headers['x-hoja-guest-token'] = activeGuest;
  // Cookie-authenticated writes must be JSON (the server's CSRF guard), even when there is nothing to send.
  const write = method !== 'GET' && method !== 'HEAD';
  if (write) headers['content-type'] = 'application/json';
  const res = await fetch(path, {method, credentials: 'include', headers, body: write ? JSON.stringify(body ?? {}) : undefined});
  let data: any = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(data?.error || data?.errors?.[0]?.message || `Request failed (${res.status})`, res.status, data?.rule);
  return data as T;
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();
const m = (code: string) => `/api/meetings/${encodeURIComponent(code)}`;

export type CreateInput = Partial<Omit<MeetingSettings, 'maxParticipants'>> & {
  title?: string; description?: string; access?: Access; teamId?: string | null; issueId?: string | null;
  projectId?: string | null; scheduledStart?: string | null; durationMinutes?: number | null;
  linkEnabled?: boolean; passcode?: boolean | string; maxParticipants?: number; invitees?: string[];
};

export const meetingsApi = {
  create: (input: CreateInput) => api<{meeting: Meeting; passcode: string | null; joinUrl: string}>('POST', '/api/meetings', input),
  list: (scope: 'upcoming' | 'past' = 'upcoming') => api<{meetings: Meeting[]}>('GET', `/api/meetings/list?${q({scope})}`).then(r => r.meetings),
  calendar: (from: Date, to: Date) => api<{meetings: Meeting[]}>('GET', `/api/meetings/calendar?${q({from: from.toISOString(), to: to.toISOString()})}`).then(r => r.meetings),
  invitations: () => api<{invitations: Meeting[]}>('GET', '/api/meetings/invitations').then(r => r.invitations),
  people: () => api<{people: Person[]}>('GET', '/api/meetings/people').then(r => r.people),
  get: (code: string) => api<{meeting: Meeting; joinUrl: string}>('GET', m(code)),
  preview: (code: string) => api<{meeting: Preview}>('GET', `${m(code)}/preview`).then(r => r.meeting),
  patch: (code: string, patch: Record<string, unknown>) => api<{meeting: Meeting; passcode: string | null; joinUrl: string}>('PATCH', m(code), patch),
  cancel: (code: string) => api('DELETE', m(code)),
  invitees: (code: string) => api<{invitees: Invitee[]}>('GET', `${m(code)}/invitees`).then(r => r.invitees),
  invite: (code: string, invitees: string[], role: 'attendee' | 'cohost' = 'attendee') => api<{invitees: Invitee[]}>('POST', `${m(code)}/invitees`, {invitees, role}).then(r => r.invitees),
  uninvite: (code: string, id: string) => api('DELETE', `${m(code)}/invitees/${id}`),
  rsvp: (code: string, response: Exclude<Rsvp, 'needsAction'>) => api('POST', `${m(code)}/rsvp`, {response}),
  ring: (code: string, userIds: string[]) => api<{rings: {id: string; userId: string}[]; ringSeconds: number}>('POST', `${m(code)}/ring`, {userIds}),
  respondRing: (id: string, answer: 'accept' | 'decline') => api<{code: string; joinPath: string}>('POST', `/api/meetings/rings/${id}/respond`, {answer}),
  join: (code: string, body: {name?: string; passcode?: string}) => api<{participant: Participant; guestToken: string | null; state: PState; meeting: Meeting; reason: string | null}>('POST', `${m(code)}/join`, body),
  act: (code: string, action: string, payload: Record<string, unknown> = {}) => api('POST', `${m(code)}/act`, {action, payload}),
  usage: (code: string, bytesIn: number, bytesOut: number) => api('POST', `${m(code)}/usage`, {bytesIn, bytesOut}),
  icsUrl: (code: string) => `${m(code)}/ics`,
  sfu: (code: string, role: 'pub' | 'sub') => ({
    session: () => api<{sessionId: string; iceServers: RTCIceServer[]}>('POST', `${m(code)}/sfu/${role}/session`),
    tracks: (body: unknown) => api<any>('POST', `${m(code)}/sfu/${role}/tracks`, body),
    update: (body: unknown) => api<any>('PUT', `${m(code)}/sfu/${role}/tracks/update`, body),
    renegotiate: (body: unknown) => api<any>('PUT', `${m(code)}/sfu/${role}/renegotiate`, body),
  }),
};

// ---- small shared helpers -------------------------------------------------------------------
export function fmtTime(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = {hour: 'numeric', minute: '2-digit'}): string {
  return iso ? new Intl.DateTimeFormat(undefined, opts).format(new Date(iso)) : '';
}
export function fmtWhen(m: Pick<Meeting, 'scheduledStart' | 'durationMinutes' | 'status'>): string {
  if (!m.scheduledStart) return m.status === 'live' ? 'Happening now' : 'Instant meeting';
  const start = new Date(m.scheduledStart);
  const end = m.durationMinutes ? new Date(start.getTime() + m.durationMinutes * 60000) : null;
  const day = new Intl.DateTimeFormat(undefined, {weekday: 'short', month: 'short', day: 'numeric'}).format(start);
  return `${day} · ${fmtTime(m.scheduledStart)}${end ? ` – ${fmtTime(end.toISOString())}` : ''}`;
}
export function joinUrl(code: string): string { return `${location.origin}/meet/${code}`; }
export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]?.toUpperCase()).join('') || '?';
}
export function googleCalendarUrl(m: Meeting): string {
  const start = new Date(m.scheduledStart || Date.now());
  const end = new Date(start.getTime() + (m.durationMinutes || 60) * 60000);
  const f = (d: Date) => d.toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
  return 'https://calendar.google.com/calendar/render?' + q({
    action: 'TEMPLATE', text: m.title, dates: `${f(start)}/${f(end)}`,
    details: `${m.description || ''}\n\nJoin: ${joinUrl(m.code)}`.trim(), location: joinUrl(m.code),
  });
}
