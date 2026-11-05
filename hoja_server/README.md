# hoja_server — self-hosted Linear-compatible backend

A self-hosted GraphQL server and web UI that speaks the exact subset of the Linear API the
`hoja` CLI uses. Run `hoja` **fully offline** against a single-file SQLite
database or host many organizations in PostgreSQL, with **no rate limits**.

> The CLI needs only one change to talk to it: an `accounts.<name>.base_url`
> pointing at this server. Everything else is byte-for-byte identical.

## Web UI

The server now serves a first-party workspace UI at `/`. It authenticates with
the same locally-issued API token as the CLI and never contacts Linear Cloud.
For a team deployment, use the included Docker Compose setup:

```bash
docker compose up --build -d
docker compose exec app hoja-serve seed --org "Acme" --org-key acme --user "Admin" --email admin@example.com
```

Open `http://localhost:8787/` and paste the token printed by `seed`.

## Install

```bash
pip install 'hoja[server]'  # includes the SQLite and psycopg 3 PostgreSQL drivers
```

## Quick start (offline, 60 seconds)

```bash
# 1. Seed a tenant DB (org, admin user, team ENG, workflow states, token)
hoja-serve seed --tenant default
#   -> prints:  TOKEN : lin_api_XXXXXXXX...

# 2. Start the server
hoja-serve serve --tenant default --port 8787
#   -> http://127.0.0.1:8787/graphql

# 3. Point hoja at it  (~/.config/hoja/config.toml)
cat >> ~/.config/hoja/config.toml <<'TOML'
[accounts.local]
base_url = "http://127.0.0.1:8787/graphql"
token = "lin_api_XXXXXXXX..."      # the token printed by `seed`
TOML

# 4. Use hoja normally
hoja --account local me
hoja --account local issue create --team ENG --title "Hello local"
hoja --account local issue list
```

`$HOJA_API_URL` overrides `base_url` for any account (the deprecated
`$LINEAR_API_URL` is still read when it is unset), e.g.:

```bash
HOJA_API_URL=http://127.0.0.1:8787/graphql hoja me
```

## Commands

| Command | Purpose |
|---------|---------|
| `hoja-serve migrate` | Apply outstanding ordered schema revisions. |
| `hoja-serve seed`  | Provision an organization/user/team/states/token. Prints the token. |
| `hoja-serve serve` | Run the GraphQL server (`--host`, `--port`, `--open`, database flags). |
| `hoja-serve token` | Mint a token for a selected organization user. |

Every command resolves its database in this order: explicit `--database-url`,
`HOJA_DATABASE_URL`, then the existing `--db` or `--tenant` SQLite path.
`seed` applies migrations for local convenience; hosted deployments should run
`migrate` as an explicit pre-deployment step. `serve` never mutates the schema.

`--open` is an isolated-SQLite convenience only: any token maps to the identity
only when exactly one organization and one user exist. It never chooses a global
first user in a shared or ambiguous database.

## What it implements

**Queries:** `viewer`, `teams`, `team` (+ `states`/`members`/`cycles`/`activeCycle`),
top-level `workflowStates`/`cycles`, `issues` (with `IssueFilter` + `orderBy`),
`issue` (+ `comments`/`labels`/`subscribers`), `projects`, `project`,
`issueLabels`, `searchIssues`, `rateLimitStatus`.

**Mutations:** `issueCreate/Update/Delete/Archive`, `commentCreate/Update/Delete`,
`issueLabelCreate/Delete`, `projectCreate/Update/Archive`, `attachmentCreate`.

**Semantics:** per-team incrementing identifiers (`ENG-1`, `ENG-2`, …), seeded
workflow states (Backlog/Todo/In Progress/In Review/Done/Canceled), derived
`priorityLabel`/`url`/`branchName`, state-transition timestamps
(`startedAt`/`completedAt`/`canceledAt`), soft-delete via `archivedAt`, and the
Relay `{ nodes, pageInfo }` connection envelope.

## Multi-tenancy

Each API token maps to exactly one organization. Every resolver is scoped by
`organization_id`, so tenants cannot see each other's data. One server endpoint
serves many tenants — add several `accounts.<name>` in hoja config, all with
the same `base_url` but different tokens.

## No rate limits

The server never returns HTTP 429 and implements `rateLimitStatus` with
effectively-unlimited values, so any client polling it is satisfied.

## Storage

Isolated mode uses one SQLite file per tenant at
`$XDG_DATA_HOME/hoja/<tenant>.db` (default `~/.local/share/hoja/`), WAL mode.
Hosted mode accepts a SQLAlchemy PostgreSQL URL, uses psycopg 3 with connection
pre-ping, and stores many organizations in one database. Every generated issue
and project URL uses `HOJA_APP_URL` (default `http://localhost:8787`), so an
owned deployment never depends on `linear.app`.

See [the deployment guide](../docs/DEPLOYMENT.md) for PostgreSQL setup,
organization seeding, explicit token selection, and operational checks.

## Architecture

```
hoja_server/
├── schema.graphql   SDL — exact Linear camelCase field names
├── db.py            SQLAlchemy Core tables, engine, migrate, seed
├── store.py         org-scoped reads + serializers + filter compilers
├── writer.py        mutations (identifier counter, derived fields)
├── resolvers.py     Ariadne bindings (Relay envelope, lazy field resolvers)
├── app.py           Starlette app + token auth + /graphql + /health + /ready
└── cli.py           `hoja-serve` entry point (serve|seed|token)
```

## Prove it

```bash
bash scripts/e2e-local-backend.sh      # 17/17 checks: boots server + drives real hoja CLI
```
