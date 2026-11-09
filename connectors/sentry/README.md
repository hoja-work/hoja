# Sentry connector (read-only)

Five tools, all read-only: the organization the installation is bound to, its projects, its issues
(with Sentry search syntax), one issue, and that issue's latest event. Nothing here creates,
resolves, assigns, mutes or deletes anything.

The `sentry-connector` package is the provider half: installation OAuth, a typed REST client and the
MCP tools. Hoja's server supplies the routes, the sealed state, the tenant's account rows and the
token store.

## The four provider facts this connector is built around

1. **A public integration does not use `/oauth/authorize/`.** Installing it sends the browser to the
   registered Redirect URL with `code`, `installationId`, `orgSlug` and the `state` the app supplied
   on the external install URL. Measured 2026-11-09: `/oauth/authorize/` answers *"Missing or invalid
   redirect_uri parameter"* for the registered redirect, so there is no plain authorize leg here and
   no PKCE parameter on this flow.
2. **The code is redeemed per installation**, not at `/oauth/token/`:
   `POST /api/0/sentry-app-installations/{installationId}/authorizations/` with
   `{"grant_type":"authorization_code","code","client_id","client_secret"}`. The response is
   camelCase — `token`, `refreshToken`, `expiresAt`, `scopes` — and `expiresAt` is an ISO instant.
3. **Tokens last eight hours, so refresh is mandatory.** The documented refresh is a JWT bearer
   assertion signed HS256 with the client secret (`iss`/`sub` = client id, `iat`, `exp` + 60 s,
   `jti`), posted to the same installation URL with
   `{"grant_type":"urn:sentry:params:oauth:grant-type:jwt-bearer"}` in the `Authorization` header.
   The refresh-token grant exists too and is kept as a fallback.
4. **A token is scoped to one Sentry organization**, the one chosen at install time. The org slug is
   read from the grant (and confirmed with `GET /api/0/organizations/`), never passed by the model.

## Deploy contract

Environment on the Hoja server (all required unless noted):

| Variable | Meaning |
|---|---|
| `HOJA_SENTRY_CLIENT_ID` | Client ID of the public integration (public: it appears in install URLs) |
| `HOJA_SENTRY_CLIENT_SECRET` | Client secret. Also signs the refresh assertion and webhook HMAC |
| `HOJA_SENTRY_REDIRECT_URI` | The **one** Redirect URL registered on the integration; must match the route this server serves |
| `HOJA_SENTRY_INTEGRATION_SLUG` | The integration slug used to build the install URL. Default `hoja` |
| `HOJA_SECRET_KEY` | Required by Hoja generally; seals the stored grant and the OAuth state |
| `HOJA_CONNECTOR_SIGNING_KEY` | Required to hand the tools to an agent |
| `HOJA_CONNECTOR_BROKER_URL` | Optional. Set to `https://api.hoja.work` in a multi-tenant deployment (see below) |

Endpoints:

* install `https://sentry.io/sentry-apps/{slug}/external-install/` (external, fixed)
* exchange/refresh `https://sentry.io/api/0/sentry-app-installations/{id}/authorizations/`
* API `https://sentry.io/api/0`

The organization's data region is fixed at signup. An integration registered on the US deployment is
not the one EU-region organizations install; an EU integration is a second registration.

## Scopes

Requested and held: `project:read`, `event:read`, `org:read`. Nothing else is requested, and no
write scope exists in this connector. The token's own `scopes` are recorded on the account row, so a
later code path cannot claim more than the install page showed.

## The redirect URI and the broker

The public-integration form has **one** Redirect URL field (the API exposes a single `redirectUrl`),
so a multi-tenant deployment cannot register `https://<tenant>/api/connectors/sentry/callback` for
every tenant. It registers a single **broker** callback on a host that is not any tenant:

```
https://api.hoja.work/api/connectors/sentry/callback
```

1. `GET /api/connectors/sentry/start` runs on the tenant under its session and mints a sealed,
   single-use state carrying the user, the organization, the tenant origin and an expiry. The user is
   redirected to the external install URL with that state.
2. Sentry returns the browser to the broker with `code`, `installationId`, `orgSlug` and `state`.
   The broker verifies the seal and expiry, checks the tenant origin against the tenant registry,
   and redirects to `https://<tenant>/api/connectors/sentry/finish?…`.
   **It never stores a token, never sees the client secret and never calls Sentry**, so a
   compromised broker can misdirect a grant but cannot mint one.
3. `/finish` runs on the tenant under the same session, re-verifies the state (single use, bound to
   that user and workspace, host must match), exchanges the code for the installation, reads the
   organization back from the token, and stores the sealed grant.

### Self-hosted (OSS) installs

A single-tenant install needs no broker: with `HOJA_CONNECTOR_BROKER_URL` unset, the registered
Redirect URL is the install's own host and `/api/connectors/sentry/callback` completes the exchange
directly. That is the OSS default.

## Disconnect, and the pending window

`DELETE /api/0/sentry-app-installations/{id}/` removes the installation. Measured 2026-11-09: after
an uninstall performed in Sentry's own UI the installation sits in `pending_deletion` for about ten
minutes, `DELETE` answers 403, and a reinstall is refused with *"Integration Hoja already installed"*.
The route therefore reports `providerRevoked: false` with a hint in that window instead of failing:
the local grant is discarded either way, and the remote one is already unusable.

## Webhook (lifecycle events)

The public-integration form requires a Webhook URL. With zero event subscriptions Sentry still
delivers `installation.created` and `installation.deleted` there. This server serves
`POST /api/connectors/sentry/webhook`, verifies the `sentry-hook-signature` header
(`HMAC-SHA256(client secret, raw body)`), and on `installation.deleted` marks the matching account
revoked. Without that route a tenant who removes Hoja in Sentry leaves a row that looks active.

## Register the integration

1. `sentry.io/settings/developer-settings/` → **Create New Integration** → **Public Integration**.
2. Permissions: Project **Read**, Issue & Event **Read**, Organization **Read**; everything else
   **No Access**; CI unchecked.
3. Redirect URL: the single callback above. Webhook URL: `/api/connectors/sentry/webhook`.
4. Copy the client id and secret once into the server's environment (never into the repository).
5. Leave it **unpublished** until the review is actually wanted: an unpublished integration can only
   be installed by the Sentry organization that created it.

## Run it

```bash
HOJA_SENTRY_CLIENT_ID=... HOJA_SENTRY_CLIENT_SECRET=... \
HOJA_SENTRY_REDIRECT_URI=https://hoja.example/api/connectors/sentry/callback \
HOJA_SENTRY_INTEGRATION_SLUG=hoja \
HOJA_SECRET_KEY=... HOJA_CONNECTOR_SIGNING_KEY=... hoja-serve
```

`GET /api/connectors/config` reports `sentry` (can connect) and `sentryAgent` (can also be handed to
an agent) so the setup wizard never offers a step that cannot complete. Neither flag exposes a
credential.

## Tests

`tests/connectors/test_sentry_*.py` drive every HTTP call through `httpx.MockTransport` or `respx`,
so the suite runs offline. The token tests run two concurrent refreshes to prove only one assertion
is spent and that a rotation performed by another worker is adopted rather than declared dead.
