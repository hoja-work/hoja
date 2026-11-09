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

Only read scopes are requested: `gmail.readonly`, `drive.readonly`, `calendar.readonly`,
`documents.readonly`, and — when the account adds Meet — `meetings.space.readonly`.
`WRITE_SCOPES` exists in `oauth.py` but `start` never requests it; adding a scope to the consent
screen widens consent for every user, so it is a deliberate change, not a deploy step.

Google does **not** reject an unlisted scope at the authorization endpoint, so least privilege
rests on `start` sending only the scopes for the flow it is starting, not on the consent-screen
registry.

## Google Meet

Meet is not a second connector and not a second account. It is a **scope add-on on the Google
grant that already exists**: the same OAuth client, the same redirect URI, the same
`connector_account` row, the same Disconnect.

That shape is what Google's own flow supports. An OAuth client is a project credential, not an
API credential — the authority is the scope requested plus the APIs enabled in the project — and
`include_granted_scopes=true` makes a later authorization request *extend* the existing grant
instead of replacing it. `authorization_url` has always sent that flag; Meet is the first add-on
to rely on it.

### Deployer setup (two steps, no new secret)

1. Enable **Google Meet API** (`meet.googleapis.com`) in the Google Cloud project that owns the
   OAuth client. A deployment that skips this gets `403 SERVICE_DISABLED` on the first Meet call,
   which the connector reports as a deployment error rather than asking the user to reconnect.
2. Add `https://www.googleapis.com/auth/meetings.space.readonly` to the consent screen for that
   project. Google accepts an unlisted scope, but listing it is what makes the consent screen
   honest about what it is asking for.

No environment variable changes. `HOJA_GOOGLE_CLIENT_ID`, `HOJA_GOOGLE_CLIENT_SECRET` and
`HOJA_GOOGLE_REDIRECT_URI` are the same three values, and the connector MCP service needs no
restart beyond the one it already needs for a new build.

### How an upgrade is requested

`GET /api/connectors/google/start?addon=meet` starts an upgrade instead of a fresh connect:

- it refuses with `409` when the caller has no active Google account, because a Meet-only grant
  would leave that account unable to use Workspace;
- its authorization request carries **only** `meetings.space.readonly`, so the consent screen
  shows one new permission instead of re-asking for Gmail and Drive;
- the callback records the scope set Google reports, which with `include_granted_scopes=true` is
  the whole grant. The previous row is updated, never duplicated, so one Google account is one
  grant and one Disconnect;
- if the user unticks the Meet permission, the callback fails with `400` and the stored grant is
  left untouched — a capability is never recorded that the token cannot use.

`GET /api/connectors/accounts` reports `capabilities` per Google account
(`gmail`/`drive`/`calendar`/`docs`/`meet`), derived from the recorded grant, so Settings shows
what the token actually covers rather than "connected".

### Revocation

Disconnect calls `https://oauth2.googleapis.com/revoke` with the stored refresh token, which ends
the **whole** Google grant — Workspace and Meet together — and the response reports
`providerRevoked: true` only when Google confirmed. There is no way to revoke only the Meet scope
at this level: Google revokes a grant, not a scope. Settings says so on the Meet row.

### API contract

All calls use `https://meet.googleapis.com/v2` with the stored grant's access token and need only
`meetings.space.readonly`.

| Tool | Meet method | Notes |
|------|-------------|-------|
| `meet_list_conference_records` | `GET conferenceRecords` | `filter` (EBNF over `space.meeting_code`, `space.name`, `start_time`, `end_time`), `pageSize` ≤ 100 |
| `meet_list_participants` | `GET {record}/participants` | `pageSize` ≤ 250; `signedin`, `anonymous` and `phone` shapes are kept apart |
| `meet_list_recordings` | `GET {record}/recordings` | metadata plus the Drive file id and playback link |
| `meet_list_transcripts` | `GET {record}/transcripts` | metadata plus the Docs mirror link |
| `meet_read_transcript` | `GET {transcript}/entries` | speaker reference, text and time range |

Every tool is a read. There is no Meet method in this package that creates a space, ends a
conference, or changes a member.

Pagination is bounded twice — a caller result cap and a 10-page cap — and a repeated
`nextPageToken` stops the walk, so one tool call cannot become an unbounded read.

### Limits that shape the product

- **Artifact bytes are not available.** A recording is an MP4 in the organizer's Drive
  (`driveDestination.exportUri`) and a transcript is mirrored to Google Docs
  (`docsDestination.exportUri`). Transcript *text* comes from `transcripts.entries`, so no Drive
  or Docs scope is needed for what this connector returns. Downloading a recording file would
  need a Drive content scope, which this connector does not request.
- **Visibility is the account's visibility.** `conferenceRecords.list` returns the conferences
  that account organized or took part in; it is not an organization-wide audit.
- **A conference record expires.** Google deletes records after their `expireTime`, so this is a
  recent-history view, not an archive.
- **`meet_list_conference_records` is unfiltered by default**, which returns page after page of
  whatever Google considers the account's records. Pass a `filter` time range for a specific
  window; the result cap and page cap are what keep an unfiltered call bounded.

### Schema

No migration. Meet reuses the Workspace `connector_account` row: `scopes` records the whole grant
Google reported, `encrypted_refresh_token` the sealed refresh token. `capabilities` is derived at
read time, not stored, so it cannot drift from the token.

## Schema

Migration revision 23 adds `connector_account` (connected accounts, encrypted refresh token,
status) and `connector_action` (pending and completed write actions). Revision 25 adds
`oauth_state_nonce`, which makes each OAuth `state` single-use.

## Status

Implemented: OAuth helpers with PKCE (S256), configuration, read-only API client, connect and
callback routes, accounts list and revoke, the approval queue for writes, the connector MCP
service, agent toolset wiring, and the Settings page.

Not implemented: the write tools themselves are behind the queue and disabled unless
`WRITE_SCOPES` are granted; there is no per-account MCP tool for Google Chat or Tasks; and no Meet
tool creates a space, ends a conference, or downloads a recording's bytes.
