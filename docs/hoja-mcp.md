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

## Issue hierarchies

For multi-step work, create or reuse one parent issue and create actionable sub-issues with
`hoja_issueCreate(parentId: "ENG-12", ...)`. Keep genuinely standalone issues flat by
omitting `parentId`. Both MCP and GraphQL accept an issue identifier (case-insensitive) or
internal id, and the parent must be an unarchived issue in the same team. Use
`hoja_issueUpdate` with a new `parentId` to reparent, or with `parentId: null` to detach;
omitting the field leaves the hierarchy unchanged. Self-parenting and ancestor cycles are
rejected; PostgreSQL reparenting is serialized per team to preserve that rule under concurrent
updates. Issue results include `parent` and `children` (cursor-paginated in GraphQL), so agents
can verify the resulting structure without manufacturing separate root issues.

The feature remains experimental. Before production enablement, review the
deployment's TLS-terminating proxy and forwarded-header stripping policy, test
PostgreSQL migrations/concurrency, verify each operation's fixed result shape
and role policy, and perform an external MCP-client interoperability review.
Do not infer production readiness solely from SQLite HTTP tests.
