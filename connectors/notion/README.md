# Notion connector

Read-only Notion access for Hoja agents: search pages and databases, read a page's text, read a
database's schema, and query its rows. There is no write path: the integration is configured with
"Read content" only and the tools cannot create or edit anything.

Three pieces, and all three must be deployed before an agent can read anything:

1. **Routes on the Hoja server** — `GET /api/connectors/notion/start`, the callback, the accounts
   list, and revoke. The Settings page drives them.
2. **The connector MCP service** — `hoja-serve connector-mcp`. The same process that serves Google
   Workspace also registers the `notion_*` tools (`notion.mcp_server.register_tools`), so adding
   Notion needs no second service and no second toolset URL.
3. **The agent's toolset** — the Hoja server adds it to the turn only when
   `HOJA_CONNECTOR_MCP_URL` is set and the user has an active account for either provider.

## Deployer setup

Each deployment uses the same Notion OAuth connection (one public connection may be installed in
any workspace), but every host needs its own registered redirect URI.

1. In the Notion developer portal create an **OAuth connection** with installation scope
   "Any workspace".
2. Set capabilities to **Read content** only. Disable update/insert content, comments, and user
   information. Notion has no OAuth `scope` parameter, so this object is where least privilege is
   enforced; the consent screen shows the same capabilities.
3. Register `https://<host>/api/connectors/notion/callback` for every host that serves the app.
4. Set the variables below on the **Hoja server**, and the marked ones on the **connector MCP
   service** as well.

| Variable | Where | Purpose |
|----------|-------|---------|
| `HOJA_NOTION_CLIENT_ID` | both | OAuth connection id |
| `HOJA_NOTION_CLIENT_SECRET` | both | OAuth connection secret (keep out of logs and version control) |
| `HOJA_NOTION_REDIRECT_URI` | both | Must exactly match the redirect URI registered in step 3 for this host |
| `HOJA_SECRET_KEY` | both | Master key: encrypts the OAuth `state` and the stored Notion token. **The same value on both**, or the service cannot decrypt what the server sealed |
| `HOJA_CONNECTOR_SIGNING_KEY` | both | Signs the principal the agent presents. **The same value on both**, or every tool call is rejected as forged |
| `HOJA_CONNECTOR_MCP_URL` | server | Public URL of the connector MCP service, ending in `/mcp` |

```bash
# on the connector host (any host that can reach the database)
HOJA_CONNECTOR_SIGNING_KEY=... HOJA_SECRET_KEY=... \
HOJA_GOOGLE_CLIENT_ID=... HOJA_GOOGLE_CLIENT_SECRET=... HOJA_GOOGLE_REDIRECT_URI=https://hoja.example/api/connectors/google/callback \
HOJA_NOTION_CLIENT_ID=... HOJA_NOTION_CLIENT_SECRET=... HOJA_NOTION_REDIRECT_URI=https://hoja.example/api/connectors/notion/callback \
hoja-serve connector-mcp --database-url "$HOJA_DATABASE_URL" --host 0.0.0.0 --port 8090 \
  --public-host connector.example
```

## Redirect URI

`HOJA_NOTION_REDIRECT_URI` is used verbatim in the authorization request and again in the token
exchange; it is not derived from the incoming `Host`. Each deployment therefore has exactly one
redirect URI. A shared control-plane callback cannot serve several tenants: the `state` is sealed
with the tenant's own `HOJA_SECRET_KEY`, so another host could not decrypt it.

Notion does **not** reject an unregistered redirect at the authorize endpoint; its install app and
token exchange do. That was observed during provisioning and is recorded in the contract, so the
redirect allow-list is a client-side boundary, not a reason to skip registering every host.

## Token model

Notion access tokens are long-lived and workspace-scoped (`owner=user`). The grant this
integration receives carries no expiry, so the connector stores the sealed access token and treats
an HTTP 401 as "the grant was revoked": the user reconnects. The API currently also returns a
nullable `refresh_token`; this connector deliberately does not rotate it, because the observed
grant has none and rotation would add a write path to a read-only connector.

Notion exposes no token-revocation endpoint. Disconnect therefore discards the stored credential
and marks the row revoked; the response says `providerRevoked: false` and asks the user to remove
Hoja in Notion (Settings > Connections) to invalidate the token on Notion's side.

Users select which pages to share during install. If they share nothing, `search` returns an empty
list rather than failing; the Settings copy tells them to reconnect and share pages.

## API contract

All requests send `Authorization: Bearer <token>`, `Notion-Version: 2022-06-28`, and
`Content-Type: application/json` where a body is used.

| Tool | Notion endpoint |
|------|-----------------|
| `notion_search` | `POST /v1/search` |
| `notion_read_page` | `GET /v1/pages/{id}` plus `GET /v1/blocks/{id}/children` |
| `notion_read_database` | `GET /v1/databases/{id}` |
| `notion_query_database` | `POST /v1/databases/{id}/query` |

`2022-06-28` is pinned by the provisioning contract. Newer Notion API versions split databases into
"data sources"; moving to one changes the database endpoints and the search filter values, so it is
a deliberate version bump, not a header tweak.

## Schema

No migration is needed: the connector reuses `connector_account` with `provider = "notion"`.
`external_account` is the workspace name (or id when the name is null), `scopes` is the literal
`read_content`, and `encrypted_refresh_token` holds the sealed access token. Reconnecting the same
workspace for the same user updates the row instead of adding a second grant.

## Status

Implemented: OAuth helpers, sealed and single-use state, connect and callback routes, accounts list
and revoke, read-only client, the four MCP tools, and the Settings section. Not implemented by
design: writes, comments, user information, Marketplace listing, refresh-token rotation.
