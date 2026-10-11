# Google Workspace connector

Read-only Gmail, Drive, Calendar, and Docs access for Cliniar agents. Write actions are not implemented yet; they will go through an approval queue.

## Deployer setup

Each deployment uses its own Google Cloud OAuth client.

1. In Google Cloud Console, create an OAuth client of type **Web application**.
2. Enable the Gmail, Drive, Calendar, and Docs APIs for the project.
3. Add the redirect URI your server exposes, for example `https://your-host/api/connectors/google/callback`.
4. Set these environment variables on the server:

| Variable | Purpose |
|----------|---------|
| `CLINIAR_GOOGLE_CLIENT_ID` | OAuth client id |
| `CLINIAR_GOOGLE_CLIENT_SECRET` | OAuth client secret (keep out of logs and version control) |
| `CLINIAR_GOOGLE_REDIRECT_URI` | Must exactly match the redirect URI registered in step 3 |

Refresh tokens are encrypted at rest with `HOJA_SECRET_KEY`, the same key used for model provider credentials.

## Scopes

Only read scopes are requested: `gmail.readonly`, `drive.readonly`, `calendar.readonly`, and `documents.readonly`.

## Schema

Migration revision 23 adds `connector_account` (connected accounts, encrypted refresh token, status) and `connector_action` (pending and completed write actions).

## Status

Implemented: OAuth helpers, configuration, read-only API client, and tables. Not yet implemented: connect and callback routes, MCP server, agent toolset wiring, approval queue, and UI.
