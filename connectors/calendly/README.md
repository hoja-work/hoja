# Calendly connector (read-only)

Five tools, all read-only: the connected user, their event types, their scheduled events, one
scheduled event, and that event's invitees. Nothing here books, reschedules, cancels or invites.

The `calendly-connector` package is the provider half: OAuth, a typed REST client and the MCP
tools. Hoja's server supplies the routes, the sealed state, the tenant's account rows and the
token store.

## The three provider facts this connector is built around

1. **PKCE is mandatory at the token endpoint.** `POST /oauth/token` without `code_verifier`
   returns `400 invalid_request: Missing required parameter: code_verifier`, before it looks at
   the client credentials; with a verifier it then requires `redirect_uri`. The developer console
   only recommends S256. The verifier lives in the sealed OAuth state.
2. **Refresh tokens are single-use and rotate.** Every refresh returns a new refresh token and
   kills the old one. Hoja caches the access token with its expiry (7200 s) and serialises
   refreshes per account; see `hoja_server/calendly_tokens.py`.
3. **One redirect URI per app.** The Calendly console accepts exactly one, and comma-separated
   values are rejected. See below.

## Deploy contract

Environment on the Hoja server (all required unless noted):

| Variable | Meaning |
|---|---|
| `HOJA_CALENDLY_CLIENT_ID` | Client ID of the Calendly OAuth app |
| `HOJA_CALENDLY_CLIENT_SECRET` | Client secret (Web/confidential client; HTTP Basic at the token endpoint) |
| `HOJA_CALENDLY_REDIRECT_URI` | The single URI registered in the Calendly app; must match byte for byte |
| `HOJA_CALENDLY_WEBHOOK_SIGNING_KEY` | Optional. Accepted and validated; unused until a webhook subscription exists |
| `HOJA_SECRET_KEY` | Required by Hoja generally; seals the stored grant |
| `HOJA_CONNECTOR_SIGNING_KEY` | Required to hand the tools to an agent |

Endpoints are taken from Calendly's RFC 8414 metadata
(`https://calendly.com/.well-known/oauth-authorization-server`):

* authorize `https://calendly.com/oauth/authorize`
* token `https://calendly.com/oauth/token`
* revoke `https://calendly.com/oauth/revoke`
* API `https://api.calendly.com`

`auth.calendly.com` answers `301` to `calendly.com` for the same paths; the advertised host is
used so a future change on the redirecting host cannot break the flow.

## The redirect URI, honestly

Calendly allows **one** redirect URI per app. A multi-tenant deployment therefore cannot register
`https://<tenant>/api/connectors/calendly/callback` for every tenant. The connector is built for
that reality:

1. The user clicks Connect on their tenant. `GET /api/connectors/calendly/start` mints a sealed,
   single-use state carrying the user, the organization, the PKCE verifier and the tenant origin
   to return to.
2. The browser is sent to the **registered** host,
   `GET /api/connectors/calendly/handoff`, which sets a short-lived HttpOnly cookie bound to the
   state nonce and redirects to Calendly. This route needs no session, because a tenant other
   than the registered host has no cookie there.
3. Calendly returns to the registered callback. The callback requires the cookie to match the
   state nonce, then exchanges the code, stores the grant for the organization in the state, and
   returns the browser to the tenant it came from.

Step 2 is what replaces the session check that a same-host callback (Slack, Google) relies on.
Without it, an attacker could mint a state for their own workspace and trick a victim into
binding their calendar to it. With it, the callback only completes in the browser that started
the flow.

Alternatives, and why they are not the default: one Calendly app per tenant (four client ids and
secrets to rotate, and a manual console step per tenant), or a parent-domain session cookie
(changing authentication for every feature to serve one connector).

Open items a deployment must decide: a Production Calendly app before real customer data (the
provisioned one is Sandbox), and how the single callback host is chosen.

## Scopes

Requested: `users:read`, `event_types:read`, `scheduled_events:read`. No write scopes, and
`availability:read` is deliberately not requested even though the provisioned app holds it,
because no tool in this connector reads availability.

## Register the app

1. `developer.calendly.com/console/apps` → create an app (`Web` client, Sandbox for development).
2. Add the one redirect URI above.
3. Tick the three scopes.
4. Copy the client id and secret once; they are shown once.

## Run it

```bash
HOJA_CALENDLY_CLIENT_ID=... HOJA_CALENDLY_CLIENT_SECRET=... \
HOJA_CALENDLY_REDIRECT_URI=https://hoja.example/api/connectors/calendly/callback \
HOJA_SECRET_KEY=... HOJA_CONNECTOR_SIGNING_KEY=... hoja-serve
```

`GET /api/connectors/config` reports `calendly` (can connect) and `calendlyAgent` (can also be
handed to an agent) so the setup wizard never offers a step that cannot complete. Neither flag
exposes a credential.

## Tests

`tests/connectors/test_calendly_*.py` drive every HTTP call through `httpx.MockTransport`, so the
suite runs offline. The refresh tests run two concurrent refreshes against a rotating endpoint to
prove only one rotation is spent.
