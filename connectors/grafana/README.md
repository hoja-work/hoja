# Grafana connector (read-only)

Six tools, all read-only: the token's identity, a dashboard search, one dashboard's panel summary,
the data-source list, alert rules with their current state, annotations in a bounded window, and one
bounded query. Nothing here creates, edits or deletes anything.

The `grafana-connector` package is the provider half: URL validation and address pinning, a typed
read-only REST client and the MCP tools. Hoja's server supplies the two routes, the sealed token and
the account row.

## The provider facts this connector is built around

1. **There is no third-party OAuth path for a Grafana instance.** Grafana's own OpenAPI document
   (`public/api-merged.json`) declares only `basic` and `api_key` under `securityDefinitions`, and
   Grafana Cloud Access Policies explicitly do not authorize the instance HTTP API. The credential
   is a **service-account token** (`glsa_` + 41 URL-safe characters) sent as
   `Authorization: Bearer …`. Hoja therefore asks the customer for the instance origin and the token
   — never a password, and never an admin credential.
2. **`Viewer` is the floor, not a convenience.** It is the minimum org role that can read
   dashboards, folders, data sources, alert rules, annotations and `POST /api/ds/query`. On the free
   and OSS tiers there is no per-dashboard scoping (that needs Enterprise); a Viewer token reads the
   whole org.
3. **401 and 403 mean different things.** A 401 is a revoked or expired token and maps to
   "reconnect". A 403 carries `Permissions needed: dashboards:create, …` and `accessErrorId`; the
   connector surfaces both instead of a generic failure. It never retries either.
4. **Datasource secrets are masked.** `GET /api/datasources` returns
   `secureJsonFields: {"basicAuthPassword": true}` — a boolean that a secret exists, never its
   value. The client drops those fields and never requests `secureJsonData`.
5. **`POST /api/ds/query` executes with the data source's own credentials.** This is why a query is
   range-capped, point-capped and summarised before it can reach a model. It is a read, but a
   powerful one; the tool is the only path to it.
6. **Rate limits are per instance and advertised.** Every response carries
   `x-rate-limit-limit` / `x-rate-limit-duration` (measured 100/s on the test stack). Reads are
   batched and 429 is surfaced rather than hammered.
7. **Version floor for provisioning rules.** `/api/v1/provisioning/alert-rules` needs Grafana 9.4+.
   State comes from `/api/prometheus/grafana/api/v1/rules`; if that endpoint is unavailable the
   connector falls back to provisioning rules with an explicit `unknown` state rather than claiming
   a rule is healthy.

## Deploy contract

Environment on the Hoja server:

| Variable | Meaning |
|---|---|
| `HOJA_SECRET_KEY` | Required. Seals the stored service-account token. |
| `HOJA_CONNECTOR_SIGNING_KEY` | Required to hand the tools to an agent. Without it the account can be connected and listed but no toolset is issued. |

There is **no** `HOJA_GRAFANA_CLIENT_ID`, `…_SECRET` or redirect URI: the connector has no app-side
credential and no callback. `GET /api/connectors/config` reports `grafana` (can connect) and
`grafanaAgent` (can also be handed to an agent). Neither flag exposes a credential.

## Connect

1. In Grafana: **Administration → Users and access → Service accounts → Add service account.**
   Name it e.g. `hoja-connector` and give it the **Viewer** role. No fixed roles are needed.
2. **Add token**, choose an expiry within your policy, and copy the `glsa_…` value once.
3. In Hoja: **Settings → Connections → Grafana**, enter the instance origin
   (`https://your-stack.grafana.net`) and the token, then **Test connection**.
   Hoja performs `GET /api/user`; a 200 proves both values before anything is stored.
4. **Connect Grafana** seals the token with the same storage as every other connector and stores the
   origin on the account row.

Reconnecting the same instance replaces its row and token. **Disconnect** deletes the sealed copy;
because a Viewer token cannot delete the service-account token it is using, the UI tells you to
delete that token in Grafana as well.

## SSRF policy for the customer-supplied URL

`grafana/ssrf.py` fails closed:

1. scheme must be `https`; no userinfo, query, fragment or path — an origin only;
2. unicode dots and a trailing dot are normalised before resolution;
3. every resolved address must be globally routable; loopback, private, link-local, multicast,
   reserved and unspecified are refused, and an IPv4-mapped IPv6 address is unwrapped first;
4. the validated address is **pinned**: the request connects to that IP with the customer's `Host`
   header and TLS SNI, and the origin is re-resolved at request time, so a second DNS answer cannot
   be used (rebinding);
5. redirects are never followed — a 3xx is an error.

The 33-case adversarial corpus from the provisioning run is a test, alongside a rebinding test and a
redirect-refusal test.

Residual risk, stated plainly: a *public* hostname can still be a reverse proxy in front of an
internal Grafana. That is the customer's own instance by definition; what the guard prevents is Hoja
fetching an arbitrary internal address.

## Bounds

| Limit | Value |
|---|---|
| Dashboard search | 100 rows |
| Dashboard panels | 200 summaries; titles/descriptions clipped to 2000 chars |
| Annotations window | 30 days; 200 rows |
| Annotation text | 2000 chars |
| Query window | 24 hours |
| Query points | 1000 requested; 50 rows and 50 series summarised |
| Query expression | 2000 chars |

Query text is untrusted content, as are dashboard titles, panel descriptions, annotation text and
alert labels. Every tool description says so; the model is told to treat them as data.

## Tests

`tests/connectors/test_grafana_*.py` cover the SSRF corpus, rebinding, redirect refusal, the client
against `httpx.MockTransport`, the routes against a stubbed resolver, and the six tools over the
shared MCP server. The suite is offline.

## Unverified

* Self-managed Grafana versions below 9.4 (provisioning endpoint) and version-specific response
  shapes beyond the measured 13.4 Cloud stack.
* A Grafana instance behind a path prefix is refused by design, not supported.
* Non-`glsa_` legacy API keys are accepted by shape but were not measured live.
* Org-wide read is the only scoping; per-dashboard scoping needs Grafana Enterprise.
