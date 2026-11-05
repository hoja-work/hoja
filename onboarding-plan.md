# First-run onboarding wizard plan

## Goal
Replace the cold login-first experience for an uninitialized instance with a welcoming, guided setup. Existing initialized instances continue to use the current sign-in UI. This plan reflects the requested initial scope: first-run-only setup, English-only UI with a saved language preference, and optional invitations showing generated one-time API keys for the admin to share.

## User journey
1. **Welcome / language** — Friendly introduction, English as the initial language option, and a saved preference for future localization.
2. **Admin profile** — Collect admin name and email plus password and confirmation (minimum 12 characters per current auth rules). No API key should be needed from the person installing Hoja.
3. **Workspace setup** — Workspace name/key and initial team name/key. Validate names and key collisions with clear inline errors.
4. **Create + sign in** — Server atomically creates the first admin identity, workspace, team, default states and browser session. Issue an API key only if the product requires one for CLI use; show it once with copy/download guidance, and never log or persist its raw value in browser storage.
5. **Invite teammates?** — Ask whether they want to add people now. Allow skip/“Invite later”; for each invite collect name/email and expose the returned one-time API key once, with clear instructions to share it securely. No email sending is implied.
6. **Finish** — Continue directly into the authenticated workspace, with a brief getting-started cue.

## Existing contracts and constraints
- `hoja_server/db.py#seed_tenant` already creates an organization, initial admin user, team/default states, demo issues, and a generated API token, but only via CLI.
- `hoja_server/app.py#auth_setup` currently sets a password using a valid token; `/auth/login` accepts email/password or API key and establishes an HttpOnly cookie session.
- `hoja_server/resolvers.py#m_member_invite` is admin-gated and returns an API key once; `frontend/src/main.tsx#MembersPage` is the current manual invite UI. No invitation email delivery is present.
- `frontend/src/main.tsx#App` currently renders `Login` whenever there is no local token, and login places API key in localStorage. New first-run bootstrap must avoid requiring that token storage for browser authentication; HttpOnly session cookie should be the browser auth mechanism.

## Implementation sequence
1. Add a backend first-run status/bootstrap contract. It must only allow creation while the database has no organization/admin, be transactionally safe against concurrent bootstrap attempts, and return no reusable secret in logs. Preserve CLI `seed` and existing login behavior. Add tests for fresh DB, already-initialized DB, and racing/duplicate setup attempts.
2. Add an onboarding screen state machine in the frontend, with explicit step validation, back/forward behavior, busy/error states, and recovery after refresh/network errors. Do not store password or API key in localStorage. Persist only the language preference (English initially).
3. Connect bootstrap completion to the session cookie and load the authenticated workspace without API-key sign-in. Keep the current login route for initialized databases and ensure setup is unavailable after bootstrap.
4. Add optional team invitation step using the existing admin-only invitation mechanism. Make returned keys visible only once, with copy controls and clear skip/invite-later route. Do not claim email invites.
5. Add Playwright journeys for fresh first-run, step navigation/validation, bootstrap rejection on an initialized instance, successful direct sign-in, optional invite/skip, responsive views, and persistence. Screenshots are required artifacts for the key steps; `--debug-boxes` enables visible element outlines. Review screenshots visually, including checking branding assets load.
6. Update README Docker onboarding only after the browser flow and contracts are implemented and tested. Explain local first run, access, persistence, invite behavior and recovery/reset accurately.

## Verification and failure points
- Build frontend and backend tests without launching a persistent service; then run focused API integration tests on disposable SQLite.
- Run Playwright against disposable SQLite, capture required welcome/profile/workspace/invite/completion screenshots, verify no console/page errors, and visually inspect responsive layout.
- Ensure initialized databases never expose the bootstrap flow; a missing/failed status endpoint must fail closed to login or an actionable error, never allow unauthenticated creation.
- Test duplicate email/workspace key, invalid email/password, DB unavailable mid-bootstrap, repeated submission, and network loss after creation. On uncertain response, query authoritative state before retrying, avoiding duplicate admins/workspaces.
- Preserve existing seeded installations, token-based login, CLI workflows, and database volume.

## Not in initial scope
- Multilingual translations (store English preference as groundwork only).
- Sending invitations by email, password reset/email recovery, OAuth, or remote production deployment.
