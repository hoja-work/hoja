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
`https://<tenant>/api/connectors/calendly/callback` for every tenant, and it does not create one
app per tenant either (four client ids, four secrets to rotate, a console step per signup).

It registers a single **broker** callback on a host that is not any tenant:

```
https://api.hoja.work/api/connectors/calendly/callback
```

The flow, end to end:

1. `GET /api/connectors/calendly/start` runs on the tenant under its session and mints a sealed,
   single-use state carrying the user, the organization, the tenant origin, the PKCE verifier and
   an expiry. Calendly is given `redirect_uri=https://api.hoja.work/…/callback`.
2. Calendly returns the browser to the broker. The broker verifies the seal and expiry, checks
   that the tenant origin is really one of our workspaces (`*.hoja.work`, not a reserved
   control-plane name, present in the tenant registry), and redirects to
   `https://<tenant>/api/connectors/calendly/finish?code=…&state=…`.
   **It never stores a token and never calls Calendly**, so a compromised broker can misdirect a
   grant but cannot mint one.
3. `/finish` runs on the tenant under the same session. It re-verifies the state (single-use,
   bound to that user and workspace, host must match), exchanges the code with the **same**
   `redirect_uri` — Calendly compares it byte for byte — and stores the sealed grant.

No open redirect: a state naming `evil.com`, the apex, `api`, `app`, `www`, `admin` or an
unregistered `ghost.hoja.work` is refused with 403 and no `Location` header. The broker host is
configurable with `HOJA_CONNECTOR_BROKER_URL` (default `https://api.hoja.work`).

### Self-hosted (OSS) installs

A single-tenant install needs no broker: with `HOJA_CONNECTOR_BROKER_URL` unset, the registered
callback is the install's own host (`HOJA_CALENDLY_REDIRECT_URI`) and `/callback` completes the
exchange directly. That is the OSS default and the documented behaviour.

## What infrastructure must do for the broker (not done by this change)

`api.hoja.work` resolves today (Cloudflare) and 302s to `https://hoja.work/`; nothing serves the
broker route yet. For `/api/connectors/calendly/callback` on that host to reach Hoja:

1. **Proxy target.** Point `api.hoja.work` at the Hoja server that serves the tenants (the same
   app that exposes the connector routes). No new service, database or secret: the broker is a
   route, not a deployment. Its only inputs are the sealed state and the query string.
2. **Cloudflare.** Remove the redirect to `hoja.work` for `/api/connectors/calendly/*` and proxy
   those paths to the origin (or use a proxied DNS record for the whole host). Keep the redirect
   for every other path so the host does not become a second front door.
3. **Caddy / ingress.** Add `api.hoja.work` to the site block with the same TLS and upstream as
   the tenants, and remove any host allow-list that rejects it. The MCP binding and the tenant
   routes are unaffected.
4. **Do not** route the broker to a tenant vhost: the state is what names the tenant.

None of this is verified here; it is infrastructure work and is listed as unverified in the PR.

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
