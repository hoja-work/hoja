# Google Workspace Connector: Plan

Status: proposed, awaiting approval.

## Decisions (resolved with user)

| # | Decision | Answer |
|---|----------|--------|
| 1 | Google auth model | Both per-user OAuth and org-level connections. Multiple accounts per user and per org. |
| 2 | Write approval | Every write (send mail, create/edit Drive files, create events, edit Docs) needs explicit user confirmation per action. |
| 3 | Packaging | Separate package inside this repo, deployable by anyone with their own Google OAuth client. |
| 4 | First services | Gmail, Drive, Calendar, Docs. |
| 5 | Agent integration | Exposed as an MCP server; the pydantic-ai agent consumes it as a toolset. |

## Breakdown

### What we need
- A Google Cloud OAuth client (`CLINIAR_GOOGLE_CLIENT_ID`, `CLINIAR_GOOGLE_CLIENT_SECRET`, `CLINIAR_GOOGLE_REDIRECT_URI`), supplied by the deployer.
- Encrypted storage for connected accounts and refresh tokens (existing `secrets_box.py`, AES-GCM keyed by `HOJA_SECRET_KEY`).
- Migration-backed tables for connector accounts and pending write actions.
- An MCP server process that the agent can reach.

### What we expect
- A user or org admin connects a Google account; it appears with its granted scopes.
- The agent can read Gmail, Drive, Calendar, and Docs through MCP tools for connected accounts it is allowed to use.
- A write tool never performs the write directly. It returns a pending action; the write runs only after the user approves through an endpoint.
- Revoked or expired tokens return a clear error, not a silent empty result.

### How the logic works (lowest level)
1. Connect: `/api/connectors/google/start` creates a PKCE state and redirects to Google with least-privilege scopes.
2. Callback: `/api/connectors/google/callback` verifies `state`, exchanges the code, encrypts the refresh token, and stores a `connector_account` row (org_id, owner_user_id or null for org-level, provider, external email, scopes, status).
3. Agent run: `build_agent` attaches an MCP toolset for the connector. The signed-in user's identity is carried by a server-issued token, not by agent-supplied arguments.
4. Read tool: the MCP server loads the account, decrypts the refresh token, obtains an access token, and calls the Google REST API.
5. Write tool: inserts a `connector_action` row (status `pending`, args stored) and returns the action id. Nothing is sent.
6. Approval: the user approves via `POST /api/connectors/actions/{id}/approve`. Only then does the server execute the stored action and set status `done` or `failed`.

### Architecture
- `connectors/google_workspace/` is a standalone package with its own `pyproject.toml`, a `FastMCP` server, and a `google-workspace-mcp` entry point.
- Modules: `oauth.py`, `accounts.py`, `tools/gmail.py`, `tools/drive.py`, `tools/calendar.py`, `tools/docs.py`, `server.py`.
- A small generic `Connector` protocol (name, scopes, tools, read/write tag per tool). Only Google implements it for now.
- Agent wiring in `cliniar_server/agent_chat.py`: `build_agent` receives toolsets built from connected accounts, using `pydantic_ai` MCP client classes. Verify exact class names against the pinned `pydantic-ai-slim` version before coding.
- OAuth and approval routes in a new `cliniar_server/connectors_api.py`, mounted in `app.py`.

### Permissions model
- Least-privilege Google scopes: Gmail read and send granted separately; Drive `drive.file` or readonly; Calendar readonly and events; Docs readonly and edit.
- Org-level accounts are usable by org members only when an admin enables them for that org.
- Per-user accounts are usable only by their owner.
- Each tool is tagged `read` or `write`. Writes always go through the approval queue.

### Breaking points
- Google client libraries are a new runtime dependency and need security review under AGENTS.md. Recommended: call Google REST with `httpx`, which is already a dependency. Decide before coding.
- Identity: the MCP server must not trust a user id from agent arguments. Otherwise an agent could read another user's mail.
- Prompt injection: email or document content can instruct the agent to call writes. The approval queue is the backstop; tool output is marked as untrusted data.
- Token refresh races: use a per-account lock.
- Routing: `/api/connectors/*` must not be shadowed by the frontend catch-all (same risk noted in `hoja-mcp-oauth-plan.md`).
- Existing agent tests use `FunctionModel`; they must stay green with empty `toolsets`.

## Dependency order
1. Decide Google client library vs `httpx` (security review).
2. Migration: `connector_account`, `connector_action`.
3. Connector registry and Google OAuth connect/callback, with `respx`-mocked tests.
4. Read tools per service (Gmail, Drive, Calendar, Docs), each with mocked tests.
5. Write tools plus approval queue; tests prove no write runs without approval.
6. Wire MCP toolset into `build_agent`; keep `tests/test_agent_chat.py` green.
7. Frontend: connect button, account list, pending-action approval UI. Run `npm run lint:styles` and `npm run build`.
8. Deployer docs: creating a Google Cloud OAuth client and setting env vars.

## Verification
- `uv run pytest tests/test_agent_chat.py` and new connector tests pass.
- `uv run ruff check` and `uv run mypy` clean on new code.
- No live Google calls in CI. Live testing needs real OAuth credentials supplied by the user.
- Version/CHANGELOG not bumped per edit, per AGENTS.md.
