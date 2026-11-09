# Slack connector

Read-only Slack access for Hoja agents: list channels, read a channel's history, search messages,
and look up people. There is no write path: the app requests six read-only **user** scopes, holds
no bot scopes, and the tools cannot post, edit or delete anything.

Three pieces, and all three must be deployed before an agent can read anything:

1. **Routes on the Hoja server** — `GET /api/connectors/slack/start`, the callback, the accounts
   list, and revoke. The Settings page drives them.
2. **The connector MCP service** — `hoja-serve connector-mcp`. The same process that serves Google
   Workspace and Notion also registers the `slack_*` tools (`slack.mcp_server.register_tools`), so
   adding Slack needs no second service and no second toolset URL.
3. **The agent's toolset** — the Hoja server adds it to the turn only when
   `HOJA_CONNECTOR_MCP_URL` is set and the user has an active account for any provider.

## Deployer setup

Each deployment uses the same Slack app (one app can be installed into many workspaces), but every
host needs its own registered redirect URI.

1. In `api.slack.com/apps`, create an app **from a manifest** with `oauth_config.scopes.user` set to
   the six scopes below and no `bot` scopes at all. With no bot scopes Slack issues no bot user and
   no `xoxb-` token, so the connector cannot accidentally use one.
2. Register `https://<host>/api/connectors/slack/callback` under **OAuth & Permissions → Redirect
   URLs** for every host that serves the app.
3. Set the variables below on the **Hoja server**, and the marked ones on the **connector MCP
   service** as well.

| Variable | Where | Purpose |
|----------|-------|---------|
| `HOJA_SLACK_CLIENT_ID` | both | App client id (`<app digits>.<bot digits>`) |
| `HOJA_SLACK_CLIENT_SECRET` | both | App client secret (keep out of logs and version control) |
| `HOJA_SLACK_REDIRECT_URI` | both | Must exactly match a registered redirect URI for this host |
| `HOJA_SLACK_SIGNING_SECRET` | server, optional | Reserved for future event delivery. Nothing reads Slack events today, so it is validated (32 hex) but never required |
| `HOJA_SECRET_KEY` | both | Master key: encrypts the OAuth `state` and the stored user token. **The same value on both**, or the service cannot decrypt what the server sealed |
| `HOJA_CONNECTOR_SIGNING_KEY` | both | Signs the principal the agent presents. **The same value on both**, or every tool call is rejected as forged |
| `HOJA_CONNECTOR_MCP_URL` | server | Public URL of the connector MCP service, ending in `/mcp` |

```bash
# on the connector host (any host that can reach the database)
HOJA_CONNECTOR_SIGNING_KEY=... HOJA_SECRET_KEY=... \
HOJA_GOOGLE_CLIENT_ID=... HOJA_GOOGLE_CLIENT_SECRET=... HOJA_GOOGLE_REDIRECT_URI=https://hoja.example/api/connectors/google/callback \
HOJA_NOTION_CLIENT_ID=... HOJA_NOTION_CLIENT_SECRET=... HOJA_NOTION_REDIRECT_URI=https://hoja.example/api/connectors/notion/callback \
HOJA_SLACK_CLIENT_ID=... HOJA_SLACK_CLIENT_SECRET=... HOJA_SLACK_REDIRECT_URI=https://hoja.example/api/connectors/slack/callback \
hoja-serve connector-mcp --database-url "$HOJA_DATABASE_URL" --host 0.0.0.0 --port 8090 \
  --public-host connector.example
```

## Scopes

Requested on the **user** token, exactly as the provisioned app holds them:

| Scope | What it enables |
|-------|-----------------|
| `channels:read` | list public channels |
| `channels:history` | read public channel messages |
| `groups:read` | list private channels the user belongs to |
| `groups:history` | read those private channels |
| `users:read` | the people directory (names and handles, never emails) |
| `search:read` | `search.messages` |

Deliberately absent: every `chat:write*`, `files:*`, `reactions:*`, `pins:*`, `channels:manage`,
`users:read.email`, `team:read` and every bot scope. Adding a scope re-opens consent for every
tenant, so it is a product decision, not a deploy tweak.

`team:read` is absent on purpose, which means `team.info` is unavailable. The workspace label comes
from the `oauth.v2.access` response (`team.name`, falling back to `team.id`); Slack omits
`team.name` when the app cannot read team info, and the connector handles that without an extra
scope.

## Redirect URI

`HOJA_SLACK_REDIRECT_URI` is used verbatim in the authorization request and again in the token
exchange; it is not derived from the incoming `Host`. Each deployment therefore has exactly one
redirect URI. A shared control-plane callback cannot serve several tenants: the `state` is sealed
with the tenant's own `HOJA_SECRET_KEY`, so another host could not decrypt it.

Slack **does** reject an unregistered redirect at the authorize endpoint
(`redirect_uri did not match any configured URIs`), observed during provisioning, so a missing
registration fails at the first click instead of at the token exchange.

## Token model

This app has user scopes only, so `oauth.v2.access` returns the usable credential at
**`authed_user.access_token`** (an `xoxp-` user token). The top-level `access_token` is a bot token
and is empty for this app shape; consuming it would widen the grant past what consent showed, so
the exchange fails closed when `authed_user.access_token` is missing or is not an `xoxp-` token.

Token rotation is disabled, so there is no refresh token and no refresh path: the connector stores
the sealed user token and treats `invalid_auth`, `not_authed`, `token_revoked`, `token_expired` or
`account_inactive` as "the user must reconnect". Slack accepts revocation, so Disconnect calls
`auth.revoke` with the stored token (no scope needed: the token revokes itself) and reports
`providerRevoked: true` only when Slack confirms or says the token was already invalid.

## API contract

All requests send `Authorization: Bearer <user token>`; the token never appears in a URL or in a
form body.

| Tool | Slack endpoint | Scope |
|------|----------------|-------|
| `slack_list_channels` | `GET conversations.list` (`types=public_channel,private_channel`) | `channels:read`, `groups:read` |
| `slack_read_channel_history` | `GET conversations.history` | `channels:history`, `groups:history` |
| `slack_search_messages` | `GET search.messages` | `search:read` |
| `slack_lookup_users` | `GET users.list` | `users:read` |

Every response is checked for Slack's **HTTP 200 error envelope** before a single field is read.
Measured live on 2026-11-09: `conversations.list`, `conversations.history`, `search.messages`,
`users.list` and `auth.revoke` all answer `200 {"ok":false,"error":"invalid_auth"}` for an unusable
token. A status-code check alone would read a dead grant as a success and then fail on a missing
field, so the envelope is the contract.

Pagination is bounded twice: a result cap (`max_results`, at most 500) and a page cap (10 pages).
`conversations.list`, `conversations.history` and `users.list` walk
`response_metadata.next_cursor`; `search.messages` pages by number (`page`/`paging.pages`).

## Limits that shape the product

- **Private channels** are readable only where the consenting user is already a member. Slack
  answers `not_in_channel`; the connector surfaces that as a readable tool error instead of
  retrying.
- **Search** is scoped to what that user can see and is rate-limited per user. It is a lookup, not
  a backfill; backfill goes through `conversations.history` per channel.
- **User identity** is names and handles only. Email matching is not available because
  `users:read.email` is not requested.

## Schema

No migration is needed: the connector reuses `connector_account` with `provider = "slack"`.
`external_account` is the workspace name (or team id when the name is absent), `scopes` is the
space-separated list Slack actually granted, and `encrypted_refresh_token` holds the sealed `xoxp-`
user token. Reconnecting the same workspace for the same user updates the row instead of adding a
second grant.

## Status

Implemented: OAuth helpers, sealed and single-use state, connect and callback routes, accounts list
and provider revocation, read-only client, the four MCP tools, and the Settings section. Not
implemented by design: writes, file access, reactions, email lookup, events and interactivity,
Marketplace distribution, and token rotation.
