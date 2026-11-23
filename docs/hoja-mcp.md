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

## Agent policy

An MCP caller whose identity is an agent (`user.kind = 'agent'`) is subject to
that agent's policy in addition to OAuth scope and resolver authorization. There
is one row per agent member and no row means no grant: an agent with no policy
can call no tool. `readTools` and `writeTools` are separate allowlists, and a tool
that is irreversible (deleting, archiving, changing team membership) additionally
needs a human's approval, which is a refusal to the agent rather than an in-band
prompt — a run cannot wait on an approver. The check runs in the dispatcher before
any resolver executes, so a tool argument cannot bypass it, and a refusal names
the missing grant and is recorded as `agent.tool.call`. Human callers are
unchanged.

Admins read and write the policy with `agentPolicy`, `agentPolicyUpdate`,
`agentSecretSet`, `agentSecretDelete` and `agentSecretNames`; every write appends
an `audit_event` containing the before and after (and, for a secret, its name and
never its value). A policy also covers team and project scope, repo and branch
grants (a default branch can never be granted), connector grants, named secrets,
runner egress and Hoja Connect endpoints, concurrency and run-minute/credit
ceilings with a queue-or-stop action, and who may delegate or approve. The runner
receives the secrets it may use and its egress allowlist in the lease payload for
that lease only — never in the prompt — and those values are redacted from the
activity stream, the audit detail and the logs.

## Usage and budgets

A run's usage is recorded in `usage_event`, an append-only ledger: there is no
update path and no delete path, so a correction is a new row whose `source` is
`correction` and the original figure stays visible beside it. Runners report
cumulative token counts on `/runner/events` while a run goes and on
`/runner/finish` at the end; the ledger stores the difference from what it already
holds for that run, so a run that streamed to the end is counted once and the sum
of a run's rows equals the runner's last report.

One credit is 1,000 processed tokens — fresh input, cache reads and output. Money
is never a float and never taken from the harness: it is computed from those token
counts at a versioned price card, and each row records the card version it was
priced with, so a new card prices future rows without rewriting a past one. Managed
compute is charged per run-minute with a fifteen-minute floor applied to the run
rather than to each report; a run on a bring-your-own key records its credits and
costs nothing.

A credit ceiling set on an agent policy (or on the agent member's own budget) is
compared against that month's ledger rows for the same agent: the schedule check
reports the `budget` reason and queues the delegation, and a policy set to `stop`
refuses the lease. Admins read the ledger with `usageSummary(month, groupBy)` —
grouped by `agent`, `model` or `month` — `usageEvents` for the newest rows and
`usageBudget(agentUserId, month)` for one agent's ceiling; members are refused. The
same data appears under Settings → Billing.

## Hoja Connect

Connect publishes one port of the machine a run is executing on, for as long as that
lease lives. The runner opens a `tailcat serve` per exposure with a fresh key, an
explicit port allowlist and exactly one pinned peer (the worker's node key), and
keeps the resulting address in its own memory: the address is a bearer secret, it
is never written to the database, to the activity stream, to the audit ledger or to
a log line.

The runner reports an exposure over the lease routes it already drives:
`POST /runner/connect/open` with the lease id, fence and lease token, the exposure
id, ports, host and pinned peer, and `POST /runner/connect/close` when the process
is gone. Both are fenced like every other lease call — a superseded attempt gets
`409 stale_fence` and cannot publish or close anything — and both are refused
unless the agent policy that governs the lease's task names a `connect` grant for
that `(host, port)`. A task nobody delegated has no policy, so it has no grant.

`connect_endpoint` (revision 71) stores one row per published port: organization,
runner, lease, session, agent, port, host, pinned peer, `opened_at`, `expires_at`,
`closed_at` and a `state` of `open` or `closed`, with `close_reason` naming why it
closed. An exposure cannot outlive its lease: finishing, releasing, cancelling or
reaping the lease closes its rows, with the reason that is actually true.

Admins read the rows with `connectEndpoints(first, after, runnerId, leaseId, live)`
and `connectEndpoint(id)`; members are refused rather than shown an empty list.
`connectEndpointClose(exposureId)` withdraws one — it closes the rows, appends a
`connect.exposure.close` audit event and marks the exposure for revocation, which
the runner receives on its next heartbeat (`connect_revoke`) and answers by killing
the process. The workspace-wide switch is `workspaceSecurityPolicy.connectEnabled`
(written through `workspaceSecurityUpdate`): off means no exposure can be opened,
which is a state a person set rather than an empty screen.

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
