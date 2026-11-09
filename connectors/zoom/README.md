# Zoom connector (read-only)

Six tools, all read-only: the connected user, their meetings, one meeting, their cloud recordings,
one meeting's recording files, and a meeting's transcript as text. Nothing here schedules, edits,
deletes or shares a meeting.

The `zoom-connector` package is the provider half: OAuth, a typed REST client and the MCP tools.
Hoja's server supplies the routes, the sealed state, the tenant's account rows and the token store.

## The provider facts this connector is built around

1. **A transcript is metadata plus a second download.** `GET /meetings/{id}/transcript` answers
   `can_download`, `download_url` and — when it cannot be read — a `download_restriction_reason`
   (`NO_TRANSCRIPT_DATA`, `NOT_READY`, `DELETED_OR_TRASHED`, `UNSUPPORTED`). The VTT body is a
   separate GET of `download_url` with the OAuth token as a Bearer header. A 404 with Zoom code
   `3322` means the meeting has no transcript at all. So the transcript scope alone is not enough to
   read a transcript; the recording-file scope is what lists it.
2. **`GET /users/me/recordings` defaults to a single day.** Omit `from`/`to` and Zoom returns today
   and tomorrow, not everything. The range is always explicit here, defaults to the last 30 days,
   and refuses a window wider than a month (Zoom's documented cap).
3. **Refresh tokens rotate.** Every refresh returns a new `refresh_token`, and the stored grant
   must persist it. Zoom's documentation says the previous value dies immediately; **measured
   2026-11-09, a replay of the just-spent token was still accepted**, so the store never treats a
   successful replay as proof that the grant is healthy — it persists the new pair and treats a
   rejection as "reconnect". A *revoked* refresh token is rejected (`invalid_grant`,
   "Invalid Token!"). Access tokens last about an hour (`expires_in` measured `3599`) and a refresh
   token about 90 days. See `hoja_server/zoom_tokens.py`.

A fourth, measured correction that costs a debugging session if missed: **a missing scope is not a
403.** Zoom answers `400` with `{"code": 4711, "message": "Invalid access token, does not contain
scopes:[...]"}`. The client maps that to the scope hint, so a user who unticked a permission is
told to reconnect rather than shown a bare HTTP 400.

Also measured: revocation is `POST https://zoom.us/oauth/revoke` with HTTP Basic and `token=` in a
form body, answering `{"status": "success"}`. It invalidates the token it is given — revoking the
access token made the next call answer 401, and revoking the refresh token made the next refresh
fail `invalid_grant` — so Hoja revokes the **refresh** token, which is what makes the grant
unrenewable. The token response carries `api_url`; the provisioning account reports
`https://api-us.zoom.us` and both that cluster and the global `https://api.zoom.us` answer 200.

## Deploy contract

Environment on the Hoja server (all required unless noted):

| Variable | Meaning |
|---|---|
| `HOJA_ZOOM_CLIENT_ID` | Client ID of the Zoom General App |
| `HOJA_ZOOM_CLIENT_SECRET` | Client secret (confidential client; HTTP Basic at the token endpoint) |
| `HOJA_ZOOM_REDIRECT_URI` | The canonical registered callback, matched byte for byte |
| `HOJA_ZOOM_REDIRECT_URIS` | Optional. Space- or comma-separated extra tenant callbacks that are also registered in the Zoom app |
| `HOJA_ZOOM_SECRET_TOKEN` | Optional. Zoom's event Secret Token. Accepted and held; unused, because no event subscriptions are configured |
| `HOJA_ZOOM_API_BASE` | Optional. Defaults to `https://api.zoom.us/v2`. Set it when the account lives in a region-pinned cluster (the token response's `api_url`) |
| `HOJA_SECRET_KEY` | Required by Hoja generally; seals the stored grant |
| `HOJA_CONNECTOR_SIGNING_KEY` | Required to hand the tools to an agent |

Endpoints are taken from Zoom's OAuth documentation and OpenAPI documents:

* authorize `https://zoom.us/oauth/authorize`
* token `https://zoom.us/oauth/token`
* revoke `https://zoom.us/oauth/revoke`
* API `https://api.zoom.us/v2`

No PKCE: `code_challenge` is supported by Zoom but is not enabled on the provisioned app, and a
challenge the app did not expect is rejected at the token endpoint. This is a confidential client
with an HTTP Basic secret, which is the documented shape for a General App.

## Per-tenant callbacks, no broker

Calendly and Sentry accept exactly one redirect URI, so those connectors carry a forwarding broker.
Zoom does not: the console holds an **allow list** of redirect URIs, and the provisioned app holds
the four tenant callbacks:

```
https://hoja.hoja.work/api/connectors/zoom/callback
https://clover.hoja.work/api/connectors/zoom/callback
https://dev.hoja.work/api/connectors/zoom/callback
https://pmoell.hoja.work/api/connectors/zoom/callback
```

The flow:

1. `GET /api/connectors/zoom/start` runs on the tenant under its session. It derives
   `https://<tenant>/api/connectors/zoom/callback` from the request origin and **refuses the flow
   before Zoom** if that exact string is not in the configured allow list, because Zoom would
   otherwise refuse it with a message the user cannot act on.
2. It mints a sealed, single-use state carrying the user, the organization, the tenant origin, an
   expiry and a nonce. There is no PKCE verifier to carry.
3. Zoom returns the browser to that tenant's own callback. `/callback` re-verifies the seal, the
   expiry, the nonce, that the user and workspace are the ones that started the flow, and that the
   origin in the state is this host — then exchanges the code with the **same** `redirect_uri`.

A self-hosted install registers its own single callback and needs nothing else.

## Scopes

Requested: `user:read:user`, `meeting:read:list_meetings`, `meeting:read:meeting`,
`cloud_recording:read:list_user_recordings`, `cloud_recording:read:list_recording_files`,
`cloud_recording:read:meeting_transcript`. No write, admin or master scopes.

`cloud_recording:read:recording` does not exist — a substring search of Zoom's granular-scope table
matches 24 longer names. The two recording scopes above are the real tokens.

## Development app vs published app

The provisioned app is **unpublished, in Development**. That has a consequence an operator must
know before wiring a customer: an unpublished Zoom app can only be authorised by users **inside the
owning Zoom account**. External customers need the app submitted to the Zoom Marketplace
(publication, which includes a security and functional review) or a per-tenant app in their own
account. Nothing in this connector assumes publication; it degrades to a consent screen Zoom itself
refuses.

Two smaller limits carried from the provisioning run: a private/beta authorization URL is valid for
four weeks, and any later scope change forces every installed user to re-consent.

## Region

`api.zoom.us` is Zoom's global cluster. An account registered in another region is served from a
different cluster, which the token response reports in `api_url`. Set `HOJA_ZOOM_API_BASE` from that
value for such an account. The provisioning run's account is global, so a non-global cluster is
unverified here.

## Register the app

1. `marketplace.zoom.us` → Develop → Build App → **General App** (user-managed OAuth).
2. Add the redirect URIs above (Redirect URL for OAuth, plus the others under *Add allow list*).
3. Tick the six scopes.
4. Copy the Client ID and Client Secret into the server environment; they are shown in the console.

## Run it

```bash
HOJA_ZOOM_CLIENT_ID=... HOJA_ZOOM_CLIENT_SECRET=... \
HOJA_ZOOM_REDIRECT_URI=https://hoja.example/api/connectors/zoom/callback \
HOJA_SECRET_KEY=... HOJA_CONNECTOR_SIGNING_KEY=... hoja-serve
```

`GET /api/connectors/config` reports `zoom` (can connect) and `zoomAgent` (can also be handed to an
agent) so the setup wizard never offers a step that cannot complete. Neither flag exposes a
credential.

## Tests

`tests/connectors/test_zoom_*.py` drive every HTTP call through `httpx.MockTransport`, so the suite
runs offline. The token tests run two concurrent refreshes against a rotating endpoint to prove only
one rotation is spent, and prove that a refresh rejected because another worker rotated first adopts
the new pair instead of declaring the account dead.
