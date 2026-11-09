# GitHub connector (read-only)

Eight tools, all read-only: the connected user, the repositories the app can see, a file's contents
(or a directory listing), a cross-repository issue/PR search, a repository's issues, one issue with
its comments, a repository's pull requests, and one pull request with a files summary. Nothing here
creates an issue, comments, merges, pushes or changes a setting.

The `github-connector` package is the provider half: OAuth, a typed REST client and the MCP tools.
Hoja's server supplies the routes, the sealed state, the tenant's account rows and the token store
(`hoja_server/github_tokens.py`).

## The provider facts this connector is built around

Each of these was measured against the provisioned app on 2026-11-09, not copied from the brief.

1. **The user token is the intersection of user and app access.** GitHub: *"a user access token can
   only access resources that both the user and app can access."* Repository permissions reach only
   repositories owned by the account where the app is installed. Measured: the `rinadelph` token saw
   `hoja-work/hoja` and `hoja-work/hoja-cloud` — exactly the two private repositories installation
   `180333144` was granted — and nothing else through `/user/installations`. So an install link is
   part of the flow, not a nicety, and a 404 is reported as *"cannot see it"* rather than *"does
   not exist"*.
2. **User tokens expire: 8 hours.** Measured `expires_in: 28800`. A connector that refreshed per
   call would spend the rotation chain for nothing, so the access token and its expiry are cached
   and refreshed inside a five-minute skew.
3. **Refresh tokens rotate, strictly.** Measured: a refresh returned a *new* refresh token, and
   replaying the just-spent one answered `bad_refresh_token: The refresh token passed is incorrect
   or expired.` This is stricter than Zoom, whose spent token was still accepted seconds later. The
   consequence is designed in: a per-account lock (so two concurrent tool calls cannot both spend
   the token), persist-before-use, and an adoption path for the case where another worker in a
   different process rotated first.
4. **The refresh chain lasts six months.** Measured `refresh_token_expires_in: 15638400` (181 days).
   Hoja's schema has no column for it, so the store does not pretend to know it: an expired chain
   surfaces as `bad_refresh_token` and the error says reconnect, which is the only action that helps.
5. **Failures are HTTP 200 with an `error` body.** A bad code answers
   `{"error":"bad_verification_code"}` with status 200, so the payload is checked rather than the
   status, and `Accept: application/json` is sent (without it the body is form-encoded). An unknown
   client id answers **404 `{"error":"Not Found"}`** before any client validation — a different
   fault from `incorrect_client_credentials`, and reported as such.
6. **Revocation is an application endpoint.** `DELETE /applications/{client_id}/grant` with HTTP
   Basic takes the access token and ends the token **and its refresh chain**. Measured: the old
   token then answered 401 and the previous refresh token answered `bad_refresh_token`. There is no
   `/login/oauth/revoke`. (`DELETE /applications/{client_id}/token` revokes one token and leaves the
   grant renewable, so it is not used.)

## Deploy contract

Environment on the Hoja server (required unless noted):

| Variable | Meaning |
|---|---|
| `HOJA_GITHUB_CLIENT_ID` | The app's Client ID (`Iv23…`) |
| `HOJA_GITHUB_CLIENT_SECRET` | The app's client secret (confidential client; also HTTP Basic at the grant endpoint) |
| `HOJA_GITHUB_REDIRECT_URI` | The canonical registered callback, matched by string |
| `HOJA_GITHUB_REDIRECT_URIS` | Optional. Space- or comma-separated extra tenant callbacks that are also registered on the app |
| `HOJA_GITHUB_APP_SLUG` | Optional but recommended. The app's slug (`hoja-connector`); what makes the *Install on GitHub* link appear |
| `HOJA_GITHUB_APP_ID` | Optional. The numeric app id; recorded for operators, unused by the flow |
| `HOJA_GITHUB_API_BASE` | Optional. Defaults to `https://api.github.com`. Set it for GitHub Enterprise Server |
| `HOJA_GITHUB_WEB_BASE` | Optional. Defaults to `https://github.com` |
| `HOJA_SECRET_KEY` | Required by Hoja generally; seals the stored grant |
| `HOJA_CONNECTOR_SIGNING_KEY` | Required to hand the tools to an agent |

Endpoints, from GitHub's documentation and measured live:

* authorize `https://github.com/login/oauth/authorize` (no `scope`, `allow_signup=false`)
* token `https://github.com/login/oauth/access_token` (client secret in the body, JSON accept)
* revoke `https://api.github.com/applications/{client_id}/grant` (HTTP Basic, JSON body)
* API `https://api.github.com` (`X-GitHub-Api-Version: 2022-11-28`)

No PKCE: GitHub Apps do not support it on the user-to-server flow, so a challenge would be ignored
at best. This is a confidential client with a secret, which is the documented shape for an app.

## Register the app

1. **Settings > Developer settings > GitHub Apps > New GitHub App**, owned by the organization that
   owns the repositories to read. Measured: the name `Hoja` is reserved for the `@hoja` account; a
   name like `Hoja Connector` is accepted and the slug decides the install URL.
2. **Homepage** `https://hoja.work`. **Callback URLs**: one
   `https://<tenant>/api/connectors/github/callback` per tenant (the app accepts up to ten).
   Wildcards exist but widen the redirect surface, so four explicit URLs are used.
3. **Repository permissions**, all read and nothing else: `metadata`, `contents`, `issues`,
   `pull_requests`. Organization, account and enterprise permissions: none.
4. **Webhook**: inactive. Nothing in this connector consumes events.
5. **Optional features > Expire user authorization tokens: ON.** This is what produces the 8-hour
   access token and the refresh token.
6. **Request user authorization (OAuth) during installation: OFF.** With it on, the tenant callback
   cannot be chosen and the flow has no `redirect_uri` to repeat at the token endpoint.
7. **Install** the app on the accounts whose repositories Hoja may read, then put the client id,
   secret and callback list in the server's environment. The connector's secret never goes in a
   repository, an issue or a log.

## Private repositories and who can authorize

Two separate limits, both measured:

* **Installation.** A private repository is readable only where the app is installed *and* the user
  can already see it. Settings > Connections therefore shows an **Install on GitHub** link
  (`https://github.com/apps/<slug>/installations/new`) next to Connect.
* **App visibility.** A **private** app can only be authorized by members of the organization that
  owns it. The provisioned app is private, so until its owner makes it public (Advanced > *Make this
  GitHub App public* — not a Marketplace listing), the `clover`, `dev` and `pmoell` tenants work only
  for `hoja-work` members. Hoja surfaces nothing it cannot honour: an outside member gets a
  `redirect_uri_mismatch`-free consent screen and GitHub's own "not authorized" answer.

Unattended reads (no user present) would need installation access tokens, which require a private
key and a locally signed JWT. That is deliberately out of scope: no private key exists for this app.

## Run it

```bash
# server
pip install -e ./connectors/github
export HOJA_GITHUB_CLIENT_ID=... HOJA_GITHUB_CLIENT_SECRET=... \
       HOJA_GITHUB_REDIRECT_URI=https://hoja.example/api/connectors/github/callback \
       HOJA_GITHUB_APP_SLUG=hoja-connector
```

The connector is installed explicitly at every pip site the deployment uses: `app/pyproject.toml`
(agent extra, uv sources and the wheel's force-include), `infra/Dockerfile.hoja`,
`.github/workflows/mobile.yml`, `app/scripts/dev-tailscale.sh`, `mobile/README.md` and
`mobile/scripts/verify-backend.sh`.

## Tests

```bash
cd app
.venv/bin/python -m pytest tests/connectors/test_github_oauth.py tests/connectors/test_github_client.py \
    tests/connectors/test_github_tokens.py tests/connectors/test_github_routes.py \
    tests/connectors/test_github_mcp_service.py
```

`tests/connectors/test_github_tokens.py` covers the three rotation hazards: one refresh per expiry,
one refresh under concurrency, and adoption of another worker's rotation.

## What was verified live (2026-11-09, real app, real account, then revoked)

Through this package's own `github.oauth` and `github.client`:

| Step | Observed |
|---|---|
| authorize URL built by the package | `github.com/login/oauth/authorize`, no `scope`, tenant callback |
| one real consent as `rinadelph` | consent page "Authorize Hoja Connector", code delivered to `hoja.hoja.work` |
| exchange | `expires_in 28800`, refresh present, `refresh_token_expires_in 15638400`, `scope ""` |
| `/user` | `rinadelph`, id `68679193`, type `User` |
| installations | 1 — `hoja-work` id `180333144`, `repository_selection: selected` |
| repositories | 2 — `hoja-work/hoja`, `hoja-work/hoja-cloud` |
| private file | `hoja-work/hoja-cloud/README.md`, 3121 bytes, decoded, `# Hoja Cloud` |
| list issues | 200 with 0 items (that repository has no issues) |
| list pull requests | 10 open, latest `#80` draft |
| get pull request | `#80`, 20 files at `file_limit=20`, `filesTruncated true` |
| search | 5 hits for `repo:hoja-work/hoja-cloud is:pr` |
| refresh | new access token, new refresh token, `expires_in 28800` |
| read after refresh | `rinadelph` |
| refresh replay of the spent token | rejected, `bad_refresh_token` |
| revoke via `/grant` | 204-equivalent success |
| old access token after revoke | 401 |
| previous refresh token after revoke | `bad_refresh_token` (the chain is dead) |

**Not verified:** a repository *with* issues (the two installed repositories have none open or
closed, so the issue shapes come from GitHub's OpenAPI schema and the mocked tests); a second
installation on a personal account; GitHub Enterprise Server; behaviour under a spent rate limit
(mapped from the response headers, not reproduced); and the public-app upgrade, which is an owner
decision.

Note on the module name: the package installs the top-level module `github`, following the
`zoom`/`sentry` convention of this repository. Do not install PyGithub (`github3`/`github`) into
the same environment.
