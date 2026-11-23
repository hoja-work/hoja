# Mobile sign-in (HOJA-733)

The Android/iOS app is a WebView shell around a workspace host (`mobile/app/index.tsx`).
Google refuses OAuth inside embedded web views, so the app signs in **natively** and then
opens the web UI with a `hoja_session` cookie already in the shared cookie jar.

There are three ways in, all of which end with a `hoja_session` cookie on the workspace host:

| Path | Where the proof comes from |
|---|---|
| **QR handoff** (primary) | A signed-in computer shows a QR code. The phone scans it, the computer approves, and the phone redeems with its PKCE verifier. |
| **Google, native sheet** | Android Credential Manager returns a Google ID token. The control plane verifies it and mints a transfer bound to the app's challenge. |
| **Google, Chrome tab** | `openAuthSessionAsync` runs the existing control-plane Google flow with `app_challenge`. The control plane returns to the app with a transfer bound to that challenge. |

App sessions (QR or Google from the app) last **90 days, sliding**. They are listed in
Settings → Connected apps → Browser sessions with their device label, and can be revoked there.

Threat model and decisions: `artifacts/runs/hoja-733-mobile-signin/DESIGN.md` in the workspace.

---

## 1. QR handoff — endpoints on the workspace host

All bodies are JSON. Errors are `{"error": "<human sentence>", "reason": "<code>"}`.

### Computer side (a signed-in browser session; same-origin JSON; API keys and OAuth tokens are refused)

`POST /auth/handoff` body `{"replaces"?: "<id>"}`
- Creates a handoff. `replaces` cancels a still-pending earlier handoff owned by the same session (QR rotation).
- `201 {"id", "secret", "code", "qrUrl", "expiresAt", "rotateAfter": 30}`
  - `code`: 8 letters from `BCDFGHJKLMNPQRSTVWXZ`, shown as `BCDF-GHJK`, for typing in when the camera is unavailable.
  - `qrUrl`: `https://<host>/m/pair#<id>.<secret>`. The secret is in the fragment, so it is never sent to a server.
- `403 reason=browser_session_required` for a bearer token or API key. `429 reason=rate_limited` (5 per minute per session).

`GET /auth/handoff/<id>`
- Only the session that created the handoff may read it.
- `200 {"id", "status", "expiresAt", "device": null | {"name", "platform", "ip", "userAgent"}}`
- `status` ∈ `pending | claimed | approved | consumed | denied | cancelled | burned | expired`
- `404` for another session's handoff.

`POST /auth/handoff/<id>/decision` body `{"decision": "approve" | "deny" | "cancel"}`
- `approve` is valid only from `claimed`. `deny` is valid from `pending` or `claimed`. `cancel` is valid from `pending` or `claimed`.
- On approve, the workspace IP allowlist is checked against the **phone's** claim IP. Admins are exempt, as they are everywhere. The QR handoff is **not** a sign-in method: it copies a session that already passed `allowedSigninMethods`, so that policy does not gate it. Membership is re-checked when the phone redeems.
- `200 {"status"}`. `409 reason=wrong_state`. `403 reason=ip_not_allowed` (the handoff becomes `denied`).

### Phone side (no credentials)

`POST /auth/handoff/claim` body `{"id", "secret"} | {"code"}`, plus `"challenge"` (S256 of the app's verifier, 43 chars), `"deviceName"` (≤ 64 chars) and `"platform"` (`android|ios`)
- The first claim wins. A **second** claim of the same handoff burns it, and the computer shows "Another device tried to use this code".
- `200 {"id", "status": "claimed", "interval": 2, "expiresAt", "account": {"email", "name", "avatarUrl"}, "workspace": {"name", "urlKey", "host"}}`
- The phone **must** show the account and workspace and wait for the person to tap Continue before redeeming. This is the defence against scanning someone else's code.
- `404 reason=unknown` · `409 reason=already_claimed` · `410 reason=expired` · `429 reason=rate_limited` (20 per minute per IP)

`POST /auth/handoff/redeem` body `{"id", "verifier"}` (JSON), **or** a form-encoded POST of the same fields (WebView fallback)
- While the handoff is `claimed`: `202 {"status": "claimed"}` (poll every `interval` seconds).
- When `approved`: the handoff becomes `consumed`, a 90-day app session is created for the same membership, `Set-Cookie: hoja_session` is sent, and the response is `200 {"status": "consumed", "redirect": "/"}`. A form POST gets a `302` to `/` instead.
- `400 reason=verifier_mismatch` · `403 reason=denied|membership_gone` · `410 reason=expired|consumed|burned|cancelled`

### `GET /m/pair`
This is the landing page when the QR code is opened in a normal camera or browser. It reads the fragment in JavaScript and offers "Open in the Hoja app" (`hoja://pair?host=<host>&h=<id>.<secret>`) plus install links. No secret ever reaches a server.

## 2. Google from the app — control plane (`app.hoja.work`)

**Chrome tab:** `GET /auth/google/start?return_to=<tenant target>&app_challenge=<S256>`, the same flow as the browser one. On success the control plane redirects to
`hoja://auth/finish?host=<tenant host>&t=<transfer>`. The transfer carries `cc=<challenge>`.

**Native sheet:** `POST /auth/google/native` body `{"idToken", "host", "challenge"}`
- The ID token's `nonce` **must equal** `challenge`, and its `aud` must be the workspace's Google **web** client ID (Credential Manager's `serverClientId`).
- `200 {"finishUrl": "https://<host>/auth/google/finish", "transfer": "<t>"}`
- `400 reason=<oidc error code>` · `403 reason=unknown_workspace`

**Finish (workspace host):** `GET /auth/google/finish?t=<transfer>&v=<verifier>&app=1`
- A transfer with `cc` is redeemed only with the matching `v`.
- **Every** transfer, browser or app, is spent once. A replay gets `400 reason=replayed`.
- With `app=1` the session is a 90-day app session.
- Uninvited addresses get the existing "not a member" page (`403 reason=denied`).

## 3. Android App Links

`GET /.well-known/assetlinks.json` names `work.hoja.mobile` with the fingerprints in `HOJA_ANDROID_CERT_SHA256` (comma-separated: the Play app-signing key and the upload key). It returns 404 when that variable is unset.

## 4. App hosts

The app talks only to `*.hoja.work` and to the hosts in `mobile/src/hosts.ts`. A QR code or deep link for any other host is refused with "This code isn't from Hoja".

## 5. Operating it

| Variable | Where | What |
|---|---|---|
| `HOJA_IDENTITY_TRANSFER_MASTER` / `_KEY` | control plane / tenant | Already required for Google sign-in. The native sheet and the Chrome-tab app flow use the same keys. |
| `GOOGLE_CLIENT_ID` | control plane | The **web** client. Native ID tokens are verified against it, so it is also the app's `serverClientId`. |
| `HOJA_ANDROID_CERT_SHA256` | control plane and tenants | Signing-cert SHA-256 fingerprints for `/.well-known/assetlinks.json` (the Play app-signing key and the upload key). Unset means App Links are not verified; `hoja://` links still work. |
| `HOJA_GOOGLE_WEB_CLIENT_ID` | mobile build | The same web client id, baked into the APK (`extra.googleWebClientId`). Unset hides the native Google sheet; the Chrome-tab path still works. |
| `HOJA_DEV_ORIGIN` | mobile **development** build only | e.g. `http://localhost:8787`, for testing against a local server. Ignored in release builds, and outside `__DEV__`. |

The native Google sheet also needs an **Android** OAuth client in the same Google Cloud
project, registered for `work.hoja.mobile` with the **Play app-signing** SHA-1. Without
it, Credential Manager returns no account and the app falls back to the Chrome tab.

**Deploy order:** the server first (revision 95 migrates on start), then the web bundle,
then the APK. An older APK keeps working after the server deploy: the server changes only
add routes, plus the single-use rule on transfers, which an honest client never trips.

**Local end-to-end test** (how HOJA-733 was verified):
1. Run `hoja-serve serve --db … --port 8787` with `HOJA_IDENTITY_TRANSFER_KEY` set.
2. `adb reverse tcp:8787 tcp:8787`. The server must be reachable on `::1` as well as on `127.0.0.1`.
3. Build a debug APK with `HOJA_DEV_ORIGIN=http://localhost:8787`, and run Metro **without** `CI=1`, so it watches files.
4. Sign a browser in with a transfer minted for audience `localhost`.
