# Hoja remote MCP (experimental)

Remote MCP uses the official MCP SDK Streamable HTTP transport at `/mcp` and
OAuth authorization-code + PKCE. It is disabled unless deployment configuration
is explicitly present. The current implementation requires
`HOJA_TRUSTED_PROXY_CIDRS` and `HOJA_TRUSTED_PUBLIC_HOST`; public origin is
configured, not inferred from arbitrary `Host` or forwarded headers. Deploy
behind a TLS-terminating proxy that strips client-supplied forwarding headers.
Only expose the configured HTTPS host. Do not set either variable on local or
untrusted deployments.

## Which URL to connect

Every workspace has one origin, and its MCP endpoint is `<origin>/mcp` (its
GraphQL endpoint is `<origin>/graphql`):

- A workspace with its own host (`organization.vanity_host`, for example
  `acme.hoja.work`) is served there and only there: connect to
  `https://acme.hoja.work/mcp`. On that host only that workspace's credentials
  are accepted, and its credentials are refused on the canonical host.
- Every other workspace is served on the canonical host
  (`HOJA_TRUSTED_PUBLIC_HOST`, for example `https://app.hoja.work/mcp`).

Settings → Connected apps shows the exact URLs under **Connect**, with a
`claude mcp add --transport http hoja <url>` line, and the GraphQL fields
`workspace { origin }` and `myWorkspaces { origin }` return the origin.

A valid credential sent to another workspace's host is answered with HTTP 403
and `{"error": "wrong_workspace_host", "url": "<its own origin>/mcp"}` (or
`/graphql`), never a 401, so a client does not start sign-in again for
nothing. A host under the apex that is not a workspace answers 421 with a JSON
body naming the canonical host.

## Authorization

Clients register dynamically as public clients (no client secret), use exact
HTTPS redirect URIs (HTTP loopback is permitted for native apps), and must use
PKCE S256. Registration is limited per client address; a registration that
never reaches an authorization is deleted after seven days. Without a
`resource` parameter, a token is issued for this server's `<origin>/mcp`.

Approval needs a Hoja browser session on the same host: a bearer token or API
key cannot approve. A signed-out browser is sent to sign in and back to the
approval page. The page names the app, where it returns, the workspace and the
signed-in account, and lets the person choose **Read and write** or **Read
only**. A grant never exceeds the person's own access. On the canonical host a
person who belongs to several workspaces picks one there; on a workspace's own
host the workspace is fixed. An approval request is valid for 15 minutes.

`hoja:read` and `hoja:write` are OAuth capability scopes, not Hoja roles:
schema resolver authorization and active organization membership remain
authoritative. Protected-resource metadata lists both scopes. A token without
`hoja:write` that calls a write tool gets HTTP 403 with
`WWW-Authenticate: Bearer error="insufficient_scope", scope="hoja:read hoja:write"`
before the tool runs, and MCP clients that implement scope step-up ask the
person for write access. A read-only API key gets a tool error saying to use a
read-write key instead.

Access tokens are opaque and short-lived; refresh tokens rotate. If a client
retries with the refresh token it just rotated, within 60 seconds it receives
one fresh pair; any later reuse of a rotated token revokes the whole grant.
Revoke a grant with OAuth revocation at `<origin>/mcp/revoke`.

The MCP transport enables SDK DNS-rebinding protection and trusts only the
configured public hostname and the workspace hosts (with optional port); other
Host values are rejected. The integration exposes named schema operations only,
with fixed minimal output selections; callers cannot submit GraphQL selection
text. Each call checks the live opaque bearer token, scope, and active
organization membership, in addition to resolver role authorization. Pending
approvals, authorization codes, and token records are persisted in the
database, so multiple workers share OAuth state. Administrative mutations
remain subject to Hoja resolver role checks; OAuth scopes never grant admin
status.

## Hoja's own apps: OAuth sign-in

The authorization server at `<origin>/mcp` also signs in Hoja's own apps (the
CLI, and plugins that reuse its token), so nobody pastes an API key:

- `hoja-cli` is a pre-registered public client (never registered through
  DCR). It uses PKCE S256 and may redirect to any
  `http://127.0.0.1:<port>/callback` or `http://[::1]:<port>/callback`.
  Its tokens are valid for both `<origin>/graphql` and `<origin>/mcp`;
  third-party clients stay bound to `<origin>/mcp`.
- Without a browser, the CLI uses the device flow (RFC 8628):
  `POST <origin>/mcp/device` returns a `user_code` such as `BCDF-GHJK`, and
  the person approves it at `<origin>/device` after signing in. Polling
  `/mcp/token` with the device grant answers `authorization_pending`,
  `slow_down`, `access_denied` or `expired_token` until then.
- Token responses add `hoja_workspace` (`id`, `name`, `url_key`, `origin`) and
  `hoja_user` (`id`, `email`, `name`). Every authorization redirect carries
  `iss`, and the metadata advertises `device_authorization_endpoint` and
  `authorization_response_iss_parameter_supported`.
- `/graphql` and the realtime socket accept these access tokens as Bearer
  credentials; `hoja:write` means read-write, otherwise read-only. An
  unknown, expired or revoked token gets HTTP 401 with
  `WWW-Authenticate: Bearer error="invalid_token"`.

Each approval is an authorized app, listed under Settings → Connected apps
with its device name, access, and when and from where it was last used.
Revoking it (or revoking any of its tokens at `<origin>/mcp/revoke`) ends
every token of that approval. The same page lists browser sessions and API
keys. Admins see every member's apps and can restrict approvals to Hoja's own
apps (`WorkspaceSecurityPolicy.oauthClients = "first_party"`), in which case
other apps are refused on the approval page.

## Agent principals

An MCP caller whose identity is an agent (`user.kind = 'agent'`, with an
`agent_member` row) is refused every tool before any resolver runs: per-agent tool
policies are a Hoja Cloud feature, and with no policy there is no grant. Human
callers, and API keys that only attribute their writes to an agent, are unchanged.

## Task hierarchies

For multi-step work, create or reuse one parent task and create actionable sub-tasks with
`hoja_taskCreate(parentId: "ENG-12", ...)`. Keep genuinely standalone tasks flat by
omitting `parentId`. Both MCP and GraphQL accept a task identifier (case-insensitive) or
internal id, and the parent must be an unarchived task in the same team. Use
`hoja_taskUpdate` with a new `parentId` to reparent, or with `parentId: null` to detach;
omitting the field leaves the hierarchy unchanged. Self-parenting and ancestor cycles are
rejected; PostgreSQL reparenting is serialized per team to preserve that rule under concurrent
updates. Task results include `parent` and `children` (cursor-paginated in GraphQL), so agents
can verify the resulting structure without manufacturing separate root tasks.

The `hoja_issue*` tools remain available as deprecated aliases of their `hoja_task*`
equivalents and behave identically.

The feature remains experimental. Before production enablement, review the
deployment's TLS-terminating proxy and forwarded-header stripping policy, test
PostgreSQL migrations/concurrency, verify each operation's fixed result shape
and role policy, and perform an external MCP-client interoperability review.
Do not infer production readiness solely from SQLite HTTP tests.
