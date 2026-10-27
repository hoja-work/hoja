# Meetings (video calls on Cloudflare Realtime)

Zoom / Google Meet-style meetings inside Hoja: schedule or start a call, invite teammates (they get a
notification and a calendar entry), call someone and have it ring, let guests in by public link, and
moderate the room. Media runs browser ↔ Cloudflare's SFU; this server only authorizes and signals, so
the Python process never touches audio or video.

## What users get

* **Meetings page** (`/workspace/meetings`, sidebar badge for pending invitations): month/week
  **calendar**, Upcoming / Past, *New meeting*, *Schedule*, *Join with code*, *Call someone*,
  pending **invitations** with Accept / Maybe / Decline, and a drawer per meeting (join link, invitees and
  their RSVPs, invite more, call an invitee, edit, cancel, Add to Google Calendar, download `.ics`,
  public-link toggle, and — for the host — the estimated cost of the call).
* **Notifications** (personal inbox socket, works in any tab of the app): toast when you are invited,
  when a meeting you are in changes or is cancelled, when someone RSVPs, "starting in N min" five
  minutes before the start, and an **incoming-call card with a ringtone** (45 s, Accept / Decline).
  Browser notifications are used when the tab is in the background and permission was granted.
* **Call room** (`/meet/<code>`, no app shell, works for guests): lobby with camera preview and device
  pickers, waiting room, gallery / speaker / screen-share layouts, active-speaker highlight, pin,
  mute / camera / share / raise hand / reactions, chat (public and private), people panel with
  admit / deny / mute / co-host / make host / remove, lock, mute everyone, live bandwidth readout.

## Configuration

| Variable | Purpose |
|---|---|
| `CLINIAR_CF_REALTIME_APP_ID`, `CLINIAR_CF_REALTIME_APP_SECRET` | SFU application. Unset ⇒ meetings still work, media endpoints return 503. |
| `CLINIAR_CF_TURN_KEY_ID`, `CLINIAR_CF_TURN_KEY_API_TOKEN` | TURN key for short-lived relay credentials. Unset ⇒ free STUN only. |
| `CLINIAR_APP_URL` | Public base URL used to build join links. Falls back to the request host. |
| `CLINIAR_MEETING_MAX_PARTICIPANTS` | Hard cap on a meeting's `maxParticipants` (default 100; per-meeting default 25). |

Schema revisions 32–34 add `meeting`, `meeting_invitee`, `meeting_participant`, `meeting_chat`,
`meeting_ring` (+ RSVP, reminder, media-flag, usage and per-device seat columns). Run `cliniar-serve migrate` first.

## Second devices (phone as mic, screen from the laptop)

A person normally has one seat per meeting. Joining from another device returns **409 `already_joined`**
(`{device, since, canCompanion}`) and the lobby asks:

* **Use this device instead** (`mode: "switch"`): the seat moves; the old device is told (`taken_over`, close
  code 4413) and shows "You moved this meeting to another device".
* **Join also with this device** (`mode: "companion"`): a second seat, labelled e.g. "Hana Host (Phone)", admitted
  straight away (the waiting room does not apply to your own second device), with its own mic/camera/screen.
  It never has moderation powers. The server returns a `seatToken`; the device sends it as `X-Hoja-Seat`
  (REST) or the `seat` WebSocket subprotocol. Each device never pulls the other's microphone, so there is no echo.

Devices are told apart by a per-browser `deviceId` (`localStorage`), so a refresh on the same device is not a conflict.

## After the meeting

An ended meeting is never joinable. `GET /api/meetings/{code}` adds `durationSeconds` and a `recap`
(when it ran, who joined, from which device, for how long); the public preview reports `status: "ended"` with the
duration (no names). The link, the calendar chip ("ended · lasted …"), the Past list and the drawer all show
"Meeting ended · lasted N min · Who joined".

## Authorization (ABAC)

`cliniar_server/abac.py` is a small deny-overrides / default-deny engine; the rules live in
`cliniar_server/meeting_policy.py`. Every REST call, socket message and SFU control call is a
`decide(action, subject, resource)`. Refusals return `{"error", "rule"}` naming the rule.

* **Subject** – member (API key or session; workspace admin, host, invitee, team visibility from the
  existing team-privacy rule, key access level) or guest (public-link visitor bound to one participant row
  by an opaque token). A member of *another* workspace joins as a guest.
* **Resource** – status, `access` (`invited` | `team` | `org`), locked, `linkEnabled`, waiting room,
  join-before-host, passcode, capacity, host present, screen-share policy, chat, recording.
* **Defaults are closed**: invite-only, waiting room on, public link off, host-muted on entry,
  a public link without a waiting room auto-generates a passcode (the passcode guards the public link; workspace
  members are authorized by the access rules and are never asked for it).
* Read-only API keys can never mutate. Guests can never manage, invite, moderate or call.
* Public-link misses and disabled links are indistinguishable (404) and rate limited per IP.
  Passcode failures are limited per IP+meeting (8 / 10 min).

Roles: **host** (one, transferable) › **cohost** (moderate, cannot act on host/other cohosts) ›
**attendee** › **guest** (attendee-level, no workspace access).

## REST

| Route | Who |
|---|---|
| `POST /api/meetings` | member – create (`title, access, teamId, issueId, projectId, scheduledStart, durationMinutes, passcode, linkEnabled, waitingRoom, …, invitees`); invitees are notified |
| `GET /api/meetings/list?scope=upcoming\|past` | member |
| `GET /api/meetings/calendar?from=&to=` | member – meetings in a range (≤ 100 days) with `myResponse` / `isHost` |
| `GET /api/meetings/invitations` | member – unanswered invitations |
| `GET /api/meetings/people` | member – workspace directory for pickers |
| `GET /api/meetings/{code}` / `PATCH` / `DELETE` | view / host+admin (settings: also cohost) / host+admin cancels; host also gets `usage` |
| `GET /api/meetings/{code}/preview` | anyone with a link-enabled code |
| `POST /api/meetings/{code}/join` | `{name?, passcode?}` → `{state: waiting\|admitted, participant, guestToken?}` |
| `POST /api/meetings/{code}/act` | `{action, payload}` – same dispatcher as the socket |
| `POST /api/meetings/{code}/rsvp` | invitee – `{response: accepted\|declined\|tentative}`; tells the host |
| `POST /api/meetings/{code}/ring` | host/cohost/admin – `{userIds}`; invites if needed and rings for 45 s |
| `POST /api/meetings/rings/{id}/respond` | callee – `{answer: accept\|decline}` |
| `GET/POST /api/meetings/{code}/invitees`, `DELETE …/{id}` | host, admin, cohost (cohost role: host/admin) |
| `GET /api/meetings/{code}/ics` | member who can view it – calendar file |
| `POST /api/meetings/{code}/usage` | participant – cumulative `{bytesIn, bytesOut}` from `getStats()` |
| `POST …/sfu/{pub\|sub}/session`, `POST …/sfu/{role}/tracks`, `PUT …/sfu/{role}/tracks/update`, `PUT …/sfu/{role}/tracks/close`, `PUT …/sfu/{role}/renegotiate`, `GET …/ice` | admitted participants |

Actions: `admit admit_all deny remove mute mute_all lower_hand hand react chat promote transfer_host
settings lock record media end leave`.

Guests send `X-Hoja-Guest-Token` (REST) or the `guest` WebSocket subprotocol
(`new WebSocket(url, ["guest", token])`); members use their session cookie or `["bearer", apiKey]`.
Cookie-authenticated writes must be JSON (`content-type: application/json`) like every other route.

## Sockets

* `/ws/meetings/{code}` – the room. Connect after `join`. Server → client: `snapshot`, `joined`, `left`,
  `participant`, `waiting`, `waiting_done`, `chat`, `react`, `mute`, `mute_all`, `meeting`, `recording`,
  `resync`→`snapshot`, and terminal `ended` / `removed` / `denied` (close codes 4410 / 4411 / 4412).
  Client → server: `ping`, `sync`, `chat`, `hand`, `react`, `{"t":"act","id":n,"a":action,"p":{…}}`;
  each gets `{"t":"ack","id":n,"ok":bool,…}`. A dropped socket keeps the seat for 20 s.
* `/ws/meetings-inbox` – personal, members only. `hello` (pending invitations + ringing calls), then
  `invited`, `meeting_updated`, `meeting_cancelled`, `rsvp`, `starting_soon`, `ring`, `ring_ended`,
  `ring_answered`. State is in the database, so a reload or a late connect misses nothing that is
  still pending.

## Media flow and cost levers (`frontend/src/meetings/rtc.ts`)

Two peer connections with separate SFU sessions: **pub** sends my tracks, **sub** receives everyone
else's (server-driven offers never collide with mine).

1. Camera is sent as three simulcast layers (`q` 180p / `h` 540p / `f` 1080p, 200 kbps / 700 kbps /
   3 Mbps); each receiver asks for the layer that fits its tile via `preferredRid`
   (`ridForWidth`: ≥ 900 px → `f`, ≥ 360 px → `h`, else `q`, hidden tab → `q`).
2. Mic is mono Opus at 32 kbps. Muting keeps the connection; turning the camera off stops sending
   entirely (`replaceTrack(null)`), so no bytes are billed.
3. Screen share is 15 fps, `contentHint = "detail"`, ≤ 2.5 Mbps.
4. Track names are `mic-*`, `cam-*`, `screen-*` – the server enforces the prefix, which is how
   screen-share / forced-mute policy is applied. Only tracks the server saw published in the same
   meeting can be pulled.
5. Every participant reports cumulative bytes every 30 s and on leave; the host sees
   `usage.egressBytes` and an estimate at Cloudflare's $0.05/GB (the first 1,000 GB/month are free).

## Tests

* `pytest tests/test_meetings.py tests/test_meetings_social.py` – policy, lifecycle, guests, waiting
  room, moderation, SFU authorization (fake Cloudflare), invitations, RSVP, ringing, calendar, ICS,
  reminders, usage, sockets.
* `cd local-e2e && npm run e2e:meetings` – real server, real Chromium (fake camera/mic) and the
  **real Cloudflare SFU**: scheduling, invite toast, RSVP, calendar, ringing, lobby, waiting room,
  two- and three-way audio/video, guest by link, chat/hand/reactions, lock, screen share, reminder,
  end for everyone, usage, plus layout audits (controls reachable and uncovered, no overflow or
  overlap at 1440 / 901 / 899 / 621 / 619 / 390 / 360 / 780×360). Needs `CLINIAR_CF_*` in the
  environment or in `../vault/env/env.hoja-cloudflare-realtime`. Screenshots land in
  `local-e2e/artifacts/meetings-*.png`.

## Known limits

* Rooms and inbox sockets live in one process (`MeetingHub`); run one replica or route a meeting's
  sockets to one node. Persisted state (participants, rings, invitations) is in the database, so a
  restart only drops live sockets.
* Recording is a consent/state flag (`recording` → everyone is told); no media is captured yet.
* Invitations notify members in-app and by `.ics`/link; no email is sent to external addresses yet.
* Closed tracks are left in place when someone leaves and rejoins (a rejoin pulls fresh tracks); very
  long meetings with heavy churn would benefit from recycling the receive connection.
* Not built yet: breakout rooms, polls, captions/transcripts, recurring meetings, TURN-less P2P for 1:1.
