# Self-hosted backend deployment

`hoja_server` is Hoja's generic Linear-compatible GraphQL backend. It is
offline-first with an isolated SQLite file for local work and supports shared
PostgreSQL for multiple organizations. Organization names, URL keys, users,
teams, application URLs, and database targets are always deployment inputs;
the server assumes no particular owner or hosting environment.

## Install

```bash
python -m pip install 'hoja[server]'
```

The server extra includes Ariadne, Starlette/FastAPI, Uvicorn, SQLAlchemy,
SQLite support, and the psycopg 3 binary PostgreSQL driver.

## Database target resolution

`serve`, `seed`, `token`, and programmatic `create_app` use one precedence rule:

1. explicit `--database-url`;
2. `HOJA_DATABASE_URL`;
3. the existing `--db` path, or the SQLite path derived from `--tenant`.

`--database-url` accepts a SQLAlchemy URL such as
`postgresql+psycopg://USER:PASSWORD@DB_HOST:5432/DATABASE`. Do not place
credentials in source control or command logs; prefer a secret-injected
`HOJA_DATABASE_URL`. CLI status output redacts URL passwords.

SQLite retains WAL mode and foreign-key pragmas. PostgreSQL does not receive
SQLite pragmas and enables `pool_pre_ping` so stale pooled connections are
discarded before use.

## Isolated SQLite

```bash
hoja-serve seed \
  --tenant local \
  --org "Local Workspace" \
  --org-key local \
  --team-key ENG \
  --email user@example.test

hoja-serve serve --tenant local --host 127.0.0.1 --port 8787
```

Without `--db`, the canonical file is
`$XDG_DATA_HOME/hoja/<tenant>.db` or
`~/.local/share/hoja/<tenant>.db`.
`--open` is intended only for isolated local use. It resolves an identity only
when the SQLite database contains exactly one organization and one user. It is
disabled by ambiguity and never selects the first global user in PostgreSQL.

## Shared PostgreSQL

Create a dedicated database and role using your normal PostgreSQL provisioning
tooling, then inject its URL:

```bash
export HOJA_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@DB_HOST:5432/DATABASE'
export HOJA_APP_URL='https://issues.example.com'

hoja-serve migrate

hoja-serve seed \
  --org "Engineering" \
  --org-key engineering \
  --team-key ENG \
  --user "Initial Admin" \
  --email admin@example.com

hoja-serve seed \
  --org "Operations" \
  --org-key operations \
  --team-key OPS \
  --user "Operations Admin" \
  --email ops-admin@example.com

hoja-serve serve --host 0.0.0.0 --port 8787
```

Each organization must have a unique `--org-key`. Seeding rejects duplicates
with a clear error and does not contain deployment-specific defaults or modes.

In a shared database, minting a token requires an explicit organization plus
one user selector:

```bash
hoja-serve token \
  --organization engineering \
  --email admin@example.com \
  --label automation

# User IDs are also accepted:
hoja-serve token \
  --organization engineering \
  --user USER_ID \
  --label service
```

The organization selector accepts an organization ID or URL key. The user must
belong to that organization; token creation never falls back to a user from a
different organization.

## Application and API URLs

`HOJA_APP_URL` is the browser-facing base used for generated issue and
project URLs. Its sensible local default is `http://localhost:8787`. Set it to
the public application origin in hosted environments; generated URLs do
not require `linear.app`.

The GraphQL API remains at `/graphql`. Point each client account at it:

```toml
[accounts.hosted]
base_url = "https://api.example.com/graphql"
token = "TOKEN_FROM_A_SECRET_STORE"
```

`HOJA_API_URL` can override the client-side account URL; the deprecated
`LINEAR_API_URL` is still read when `HOJA_API_URL` is unset. Both are separate
from `HOJA_APP_URL`.

## Health, readiness, and operation

- `GET /health` is a process liveness endpoint and returns HTTP 200 without
  requiring a database round trip.
- `GET /ready` verifies database connectivity and the applied schema revision.
  It returns HTTP 200 with `{"status":"ready"}`, or HTTP 503 when the database
  is unavailable or its schema is behind the server.

Use `/health` for process supervision and `/ready` for load-balancer or
orchestrator readiness. Terminate TLS at a trusted reverse proxy, inject
database credentials through a secret manager, restrict database network
access, back up PostgreSQL normally, and monitor readiness failures.

Schema changes are ordered and recorded in `schema_revision`. Run
`hoja-serve migrate` as a pre-deployment or init step before starting new
application instances. The server process does not alter schema on startup, so
`/health` remains available during a database outage and `/ready` rejects
traffic until both connectivity and schema currency are confirmed.

## Verification

SQLite requires no external service:

```bash
uv run --extra server --extra dev python -m pytest -q \
  tests/test_server_backend.py tests/test_server_postgres.py
bash scripts/e2e-local-backend.sh
```

PostgreSQL integration tests are opt-in and never start Docker:

```bash
export HOJA_TEST_POSTGRES_URL='postgresql+psycopg://USER:PASSWORD@DB_HOST:5432/TEST_DATABASE'
uv run --extra server --extra dev python -m pytest -q tests/test_server_postgres.py

# Run the same 17-check CLI matrix against PostgreSQL:
HOJA_DATABASE_URL="$HOJA_TEST_POSTGRES_URL" \
HOJA_PYTHON="$PWD/.venv/bin/python" \
bash scripts/e2e-local-backend.sh
```

Use a disposable test database. The gated test creates uniquely named
organizations and removes its rows afterward.

## Upgrading a deployment that used the previous name

An existing installation keeps its database, its `POSTGRES_*` role and its data
volume; only the image, the Compose file and the `HOJA_*` environment-variable
prefix change. Follow `DEPLOY-RENAME.md` for the ordered steps (image tag,
volume pinning, `.env` rewrite, migrate, verification) and the rollback path.
Do not rename the database or its role in place.
