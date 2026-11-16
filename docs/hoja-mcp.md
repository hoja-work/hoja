# Hoja remote MCP (experimental)

Remote MCP uses the official MCP SDK Streamable HTTP transport at `/mcp` and
OAuth authorization-code + PKCE. It is disabled unless deployment configuration
is explicitly present. The current implementation requires
`HOJA_TRUSTED_PROXY_CIDRS` and `HOJA_TRUSTED_PUBLIC_HOST`; public origin is
configured, not inferred from arbitrary `Host` or forwarded headers. Deploy
behind a TLS-terminating proxy that strips client-supplied forwarding headers.
Only expose the configured HTTPS host. Do not set either variable on local or
untrusted deployments.

Clients register as public clients (no client secret), use exact HTTPS redirect
URIs (HTTP loopback is permitted for native apps), and must use PKCE S256.
Consent requires an existing Hoja browser session. `hoja:read` and `hoja:write`
are OAuth capability scopes, not Hoja roles: schema resolver authorization and
active organization membership remain authoritative. Access tokens are opaque
and short-lived; refresh tokens rotate. Revoke client grants by using OAuth
revocation or revoke tokens in the database if responding to an incident.

The MCP transport enables SDK DNS-rebinding protection and trusts only the
configured public hostname (with optional port); other Host values are rejected.
The integration exposes named schema operations only, with fixed minimal output
selections; callers cannot submit GraphQL selection text. Each call checks the
live opaque bearer token, scope, and active organization membership, in addition
to resolver role authorization. Pending consent, authorization codes, and token
records are persisted in the database, so multiple workers share OAuth state.
Automated coverage exercises browser-session login and consent, CSRF rejection
and acceptance, authorization-code PKCE exchange, wrong-verifier and code-reuse
rejection, concurrent single-use code redemption, refresh rotation and
replay-family revocation, MCP initialize/list/viewer calls, write-scope denial,
revoked-token rejection, and wrong-host rejection. Administrative mutations
remain subject to Hoja resolver role checks; OAuth scopes never grant admin
status.

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
