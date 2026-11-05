# Hoja Remote MCP with OAuth — implementation plan

## User-confirmed decisions
- Build a new hosted/remote MCP, not an extension of `hoja-mcp` stdio.
- Use OAuth 2.1 Authorization Code + PKCE.
- Allow Dynamic Client Registration with strict redirect URI validation and mandatory PKCE S256.
- Enable read and write capabilities.
- OAuth scopes: `hoja:read` and `hoja:write`; preserve existing Hoja authorization checks.
- Token lifecycle: opaque random tokens; persist only hashes; 15-minute access tokens; rotating refresh tokens; revocation; bind grants to client, user, organization, and scopes.
- External public origin is derived from reverse-proxy forwarded headers only with explicit trusted-proxy configuration.
- User explicitly requested all GraphQL mutations, including administrative/account and destructive operations.

## Repository facts and constraints
- Existing MCP is optional FastMCP stdio (`hoja/mcp/server.py`, `pyproject.toml`); it has static Linear token auth and is not a template for OAuth transport.
- Hoja ASGI app is currently Starlette (`hoja_server/app.py`), with `/graphql`, browser login/session, bearer API keys, and request-scoped `store`, `writer`, `org_id`, `user_id` context. There is no OAuth provider today.
- `hoja_server/schema.graphql` defines query and mutation surfaces. `hoja_server/resolvers.py` implements existing permission rules; `Store`/`Writer` are org-scoped. MCP must not bypass them.
- DB schema uses ordered migrations and supports SQLite/Postgres (`hoja_server/db.py`).
- Existing worktree is already modified in many unrelated files. Preserve all of it; intended implementation boundary is new OAuth/MCP modules/tests and minimal necessary app/db/ignore/dependency/docs edits.
- Latest FastAPI repository was shallow-cloned to ignored `.fastapi-reference/` at `94918c1d40afb27065c64d523219ed2ad9822f76`. Reference only; do not vendor or depend on it.

## Choreography / dependency order
1. **Baseline and implementation research** — inspect current app routes, GraphQL authorization, migration patterns, tests, and the cloned FastAPI reference. Record current worktree status. Expected result: evidence-backed map and no edits to existing user changes. If the FastAPI clone does not provide a relevant pattern, rely on maintained protocol/SDK interfaces, not guessed APIs.
2. **Define remote OAuth/MCP protocol contract** — verify available MCP SDK support for Streamable HTTP and OAuth provider integration; choose how OAuth metadata, authorization, token, registration, consent, refresh, and revocation endpoints fit the existing Starlette app. Determine strict proxy handling, origins, redirect validation (HTTPS except loopback), authorization-code PKCE S256, state, client credentials, scopes, expiry, single-use and rotation. Stop if a secure integration cannot be supported by available libraries/runtime.
3. **Define Hoja operation catalog** — map each supported query and every mutation, including inputs/outputs, pagination and existing authorization. Use explicit named MCP tools, not arbitrary GraphQL query passthrough. Require `hoja:read` for reads and `hoja:write` for writes. Preserve resolver checks; OAuth scopes do not grant Hoja admin role. For mutations requiring browser-session-only context (e.g. workspace switch), retain denial unless existing authorization can safely support them.
4. **Implement persistence and OAuth state** — add migration-backed OAuth client, authorization-code, access-token, and refresh-token records (or reviewed equivalent). Store only token hashes; use unpredictable, expiring, single-use codes/state; atomic refresh-token rotation and replay revocation; bind grants to client, subject, org, and approved scopes. Never log credentials.
5. **Implement OAuth endpoints and consent** — DCR validates client metadata/redirects and stores client; authorization requires an existing Hoja browser session and explicit consent for client, workspace and scopes; callback uses PKCE S256 and exact redirect matching; token exchange returns OAuth response; refresh rotates tokens; revocation invalidates grants. Derive canonical HTTPS origin only from configured trusted-proxy behavior, never arbitrary host headers. Do not weaken existing browser/API-token login.
6. **Implement remote MCP endpoint** — mount Streamable HTTP transport in ASGI with OAuth bearer authentication; resolve opaque token to current grant and verify expiration, revocation, client, org/user binding and scopes on every request. Register only reviewed operations. Invoke the same GraphQL schema/resolvers with the same context and normal org/user admin checks; do not expose DB internals or unrestricted GraphQL execution.
7. **Focused tests and docs** — test MCP operations, OAuth metadata/DCR, redirect rejection/near-miss, PKCE, state and code replay, consent, scope denial, expiry/revocation/refresh rotation/reuse, user/org isolation, GraphQL authorization, hostile forwarded headers, malformed inputs, database failures and secret redaction. Document client setup, trusted proxy config, scopes and limitations.
8. **Verification and diff review** — run focused OAuth/MCP tests, existing backend tests touching app/auth/schema, formatting/lint/type checks as available, and a Streamable HTTP integration test with ephemeral SQLite and synthetic identities. Reconcile `git status` and inspect exact diff to prove pre-existing changes remain intact. Report commands, outcomes and uncovered paths.

## Required invariants
- Authorization Code + PKCE S256; state and codes unpredictable, bound, expiring, one-time.
- DCR redirects exact-match allow-listed; reject unsafe schemes, fragments, credentials, wildcards, untrusted origins; loopback exception only for native clients.
- Require TLS/public HTTPS except allowed loopback redirect use; trusted proxy configuration explicit and limited.
- Scope checks (`hoja:read`, `hoja:write`) are separate from Hoja role/record authorization.
- All MCP operations remain within the grant's organization and user. Never accept client-supplied org/user as authority.
- Persist token hashes only; use random opaque bearer tokens; short expiry; refresh rotation/replay protection; revocation; no secret-bearing logs.
- No arbitrary GraphQL pass-through; explicit operation allow-list and validated parameters.
- OAuth failure is fail-closed; existing login and API-token behavior remains unchanged.

## Checks to resolve by source inspection before implementation
- Current MCP SDK/runtime supports Streamable HTTP and a custom OAuth authorization-server provider compatible with Starlette routing.
- Whether DCR client records need confidential credentials; public native MCP clients should not require a client secret.
- Existing GraphQL context/auth behavior for OAuth grants, especially `workspaceSwitch` and admin-only mutations.
- Trusted proxy scheme/host configuration and deployment docs available today.
- Migration/test patterns for concurrent SQLite/Postgres code redemption and token rotation.

## Breaking points and ripple checks
- OAuth is new; a mistaken issuer/audience/redirect/PKCE design can enable token theft or confused-deputy access.
- Starlette route precedence may shadow `/mcp` or OAuth endpoints with the frontend catch-all; existing login, GraphQL, health and static routes must retain behavior.
- Migration changes affect both SQLite and PostgreSQL and must preserve ordered contiguous revisions.
- Passing an OAuth-derived identity into GraphQL must not allow tenant switching or bypass resolver-level admin checks.
- DCR has SSRF/open-redirect risks if metadata URLs are fetched or redirects are weakly validated; do not fetch client metadata URLs.
- Long MCP requests, transport session lifecycle and async errors must not leak tokens or leave unclosed resources.
- If no maintained SDK path supports the chosen remote OAuth design, stop and report the dependency/protocol gap rather than implementing a bespoke unsafe partial OAuth server.
