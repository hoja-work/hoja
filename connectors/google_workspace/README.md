# Google Workspace connector

Read-only Gmail, Drive, Calendar, and Docs access for Hoja agents. Write actions go through the
approval queue (`connector_action`), not through this MCP server: the tools it serves are read-only.

Three pieces, and all three must be deployed before an agent can read anything:

1. **Routes on the Hoja server** — `GET /api/connectors/google/start`, the callback, the accounts
   list, and revoke. The Settings page drives them.
2. **The connector MCP service** — `hoja-serve connector-mcp`. This is the process the agent
   reaches at `HOJA_CONNECTOR_MCP_URL`; it verifies the server-signed principal and refreshes the
   Google access token per call.
3. **The agent's toolset** — the Hoja server adds it to the turn only when
   `HOJA_CONNECTOR_MCP_URL` is set and the user (or their organization) has an active account.

## Deployer setup

Each deployment uses its own Google Cloud OAuth client. One client can be shared by several tenant
hosts, but every host needs its own registered redirect URI (see "Redirect URI" below).

1. In Google Cloud Console, create an OAuth client of type **Web application**.
2. Enable the Gmail, Drive, Calendar, and Docs APIs for the project.
3. Register the redirect URI for every host that serves the app:
   `https://<host>/api/connectors/google/callback`.
4. Set the variables below on the **Hoja server**, and the marked ones on the **connector MCP
   service** as well.

| Variable | Where | Purpose |
|----------|-------|---------|
| `HOJA_GOOGLE_CLIENT_ID` | both | OAuth client id |
| `HOJA_GOOGLE_CLIENT_SECRET` | both | OAuth client secret (keep out of logs and version control) |
| `HOJA_GOOGLE_REDIRECT_URI` | both | Must exactly match the redirect URI registered in step 3 for this host |
| `HOJA_SECRET_KEY` | both | Master key: encrypts the OAuth `state` and the stored refresh token. **The same value on both**, or the service cannot decrypt what the server sealed |
| `HOJA_CONNECTOR_SIGNING_KEY` | both | Signs the principal the agent presents. **The same value on both**, or every tool call is rejected as forged |
| `HOJA_CONNECTOR_MCP_URL` | server | Public URL of the connector MCP service, ending in `/mcp` |

`HOJA_CONNECTOR_SIGNING_KEY` also gates readiness: `/api/connectors/config` reports
`googleWorkspace` (the OAuth client is configured) separately from `googleWorkspaceAgent` (the
server can also sign a principal). A deployment can connect an account with only the first; an
agent needs both plus `HOJA_CONNECTOR_MCP_URL`.

```bash
# on the connector host (any host that can reach the database)
HOJA_CONNECTOR_SIGNING_KEY=... HOJA_SECRET_KEY=... \
HOJA_GOOGLE_CLIENT_ID=... HOJA_GOOGLE_CLIENT_SECRET=... HOJA_GOOGLE_REDIRECT_URI=https://hoja.example/api/connectors/google/callback \
hoja-serve connector-mcp --database-url "$HOJA_DATABASE_URL" --host 0.0.0.0 --port 8090 \
  --public-host connector.example

# on the Hoja server
HOJA_CONNECTOR_MCP_URL=https://connector.example/mcp
```

Put the service behind TLS and the same access control as the app. It authenticates only the
signed principal header, so anyone who can reach it and has a valid key-signed token can read
that principal's mail — do not expose it beyond the app's own network.

The README of the deployed app must not be trusted over this file for the variable names: an
earlier version named `HOJA_SECRET_KEY` where the signature key is `HOJA_CONNECTOR_SIGNING_KEY`
and omitted `HOJA_GOOGLE_REDIRECT_URI` from the install steps.

## Redirect URI

`HOJA_GOOGLE_REDIRECT_URI` is used verbatim in the authorization request; it is not derived from
the incoming `Host`. Each deployment therefore has exactly one redirect URI, and a shared
control-plane callback cannot serve several tenants: the `state` is sealed with the tenant's own
`HOJA_SECRET_KEY`, so another host could not decrypt it.

Google allows roughly 100 redirect URIs per client, which caps one client at about 100 tenants
(fewer once custom domains are added). Beyond that, use one client per tenant or a broker.

## Scopes

Only read scopes are requested: `gmail.readonly`, `drive.readonly`, `calendar.readonly`, and
`documents.readonly`. `WRITE_SCOPES` exists in `oauth.py` but `start` never requests it; adding a
scope to the consent screen widens consent for every user, so it is a deliberate change, not a
deploy step.

Google does **not** reject an unlisted scope at the authorization endpoint, so least privilege
rests on `start` sending only `READ_SCOPES`, not on the consent-screen registry.

## Schema

Migration revision 23 adds `connector_account` (connected accounts, encrypted refresh token,
status) and `connector_action` (pending and completed write actions). Revision 25 adds
`oauth_state_nonce`, which makes each OAuth `state` single-use.

## Status

Implemented: OAuth helpers with PKCE (S256), configuration, read-only API client, connect and
callback routes, accounts list and revoke, the approval queue for writes, the connector MCP
service, agent toolset wiring, and the Settings page.

Not implemented: the write tools themselves are behind the queue and disabled unless
`WRITE_SCOPES` are granted; there is no per-account MCP tool for Google Chat, Meet, or Tasks.
