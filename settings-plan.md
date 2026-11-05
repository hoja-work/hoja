# Complete Settings UI — Implementation Plan

## Decomposition and choreography

### Inputs and preconditions
- Authenticated viewer (`id`, `name`, `email`, `admin`) and active workspace; existing GraphQL authentication resolves user and organization.
- Existing API keys are stored in `api_key` with `token_hash`, `label`, `user_id`, `organization_id`, `created_at`, and nullable `revoked_at` (`hoja_server/db.py`). No key list/create/revoke contract exists yet; key plaintext is returned only from creation flows such as bootstrap/invite/CLI.
- Settings navigation currently routes to `WorkspaceSettings`; workspace update and team creation already have admin checks on backend.
- No service-connection persistence or integration backend exists.

### Expected outcomes
- Both regular users and admins can access settings, see account details and manage only their own API keys: list metadata, create labeled keys, reveal/copy secret once, and revoke keys.
- Server enforces owner + active-workspace boundaries for every key operation. Hash-only persistence; never return stored secret, and never put raw secret in logs/local storage.
- Admin-only workspace settings/team controls remain visible only to admins and enforced by existing backend checks. Ordinary users get useful account/personal-key settings without broken admin forms.
- Connections section states clearly that integrations are not currently available; no pretend connected state or fake secret collection.
- Existing CLI/API-key authentication remains valid; revoked keys fail authentication.

### Logic and invariants
1. Frontend requests current user’s key metadata using authenticated GraphQL context.
2. Create mutation validates label, generates high-entropy token, persists only hash scoped to authenticated user and workspace, and returns raw token once.
3. Frontend displays raw value in a one-time panel with copy affordance; after dismissal/reload only metadata remains.
4. Revoke mutation updates only matching key ID + current user + workspace and active state; repeated/foreign revocations fail safely.
5. Backend remains authorization source of truth; hiding admin controls is UX only.
6. Connections panel is explanatory only until a real service contract is chosen.

### Ordered steps and dependencies
1. Preserve existing worktree state; inspect resolver context, API-key helpers/token generation, GraphQL payload patterns, and tests. Confirm current git diff before touching files.
2. Add store operations for listing a user's key metadata, generating/persisting a labeled personal key, and revoking an owned active key. Follow existing token hashing and ID/time conventions.
3. Add GraphQL schema fields and resolvers with authenticated context-derived identity/workspace; never trust client-supplied owner IDs. Keep the existing `apiKey` one-time raw-token field pattern only for creation response.
4. Rework Settings UI into account, personal API keys, connections, and (admin-only) workspace/team sections. Add accessible create/revoke confirmation, one-time secret reveal/copy, loading/error states, and role-aware display.
5. Add backend tests for ownership, organization isolation, revoked-key rejection, metadata secrecy, and admin/member behavior. Add local E2E coverage if the ignored harness can safely exercise key flows without exposing captured secrets.
6. Run focused backend tests, full backend suite, frontend build, E2E/screenshot workflow where practical, and `git diff --check`; inspect final diff and do not commit.

### Breaking points / ripple checks
- Resolver context may represent a browser session or API-key bearer; both must resolve the same current profile/workspace.
- Workspace switching must refresh key list and cannot reveal keys from the previous workspace.
- Existing bootstrap/CLI/member-invite keys must remain untouched and continue authenticating.
- Revoke must prevent future token resolution while not terminating unrelated browser sessions unless current architecture explicitly couples them.
- Secret must not appear in console, browser storage, logs, failure screenshots, or key-list query results.
- Existing admin workspace/team settings and member navigation must not regress; backend authorization remains authoritative.
- A failed create response could persist a key whose plaintext was lost; show clear retry guidance and avoid blindly retrying duplicate creation.

## Decisions resolved
- Connections scope: API-key management plus an honest unsupported Connections section; no pretend connection records.
- API-key permissions: each user manages only their own keys; admins retain workspace controls, not other users' personal keys.
- Secret policy: reveal only once at creation; persisted token hashes are not recoverable.
