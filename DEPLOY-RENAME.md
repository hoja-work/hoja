# Deploying the product rename to Hoja

Runbook for the box that already runs the product:

- host `45.32.168.82`
- deployment directory `/opt/hoja/deploy`
- Compose services `hoja-app`, `hoja-db`, `hoja-traefik`

Nothing in this document has been executed from this repository. It is an
ordered checklist; every command runs on that host, by a person, in an agreed
maintenance window.

The rename changes the application image, the Compose file and the
environment-variable prefix. It does **not** change the database contents, the
database role, the data volume, or the GraphQL wire surface. The database must
not be renamed in place — see step 2.

The previous release used a different product name for the distribution, the
commands, the environment-variable prefix and the local config directory. This
document does not assume that spelling: it is read off the box once, into the
two variables below, and used from then on.

```bash
# The name the box was deployed under, in the two spellings that matter.
OLD_NAME='<pre-rename name, lower case>'      # e.g. what `ls ~/.config` shows
OLD_PREFIX='<the same name, upper case>'      # e.g. `grep -oE '^[A-Z]+_' .env`
export OLD_NAME OLD_PREFIX
```

---

## 0. Pre-flight (read-only)

Run these first and keep the output. They are the rollback baseline.

```bash
cd /opt/hoja/deploy

# Every variable currently set, names only, so the transcript stays safe.
sudo grep -E '^[A-Z0-9_]+=' .env | cut -d= -f1 | sort

# Which database, role and volume the running deployment actually uses.
docker compose config | grep -E 'POSTGRES_|HOJA_|source:|target:' | grep -v PASSWORD

# The volume that holds the data, and the containers that mount it.
docker volume ls
docker inspect hoja-db --format '{{json .Mounts}}'

# Image currently in service, so it can be tagged for rollback.
docker compose images

# Schema revision currently applied.
curl -fsS http://127.0.0.1:8787/ready

# A dump that does not depend on any step below.
set -a; . /opt/hoja/deploy/.env; set +a
docker compose exec -T hoja-db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > /opt/hoja/deploy/backup-pre-hoja-rename-$(date +%F).dump
```

Record four values, because steps 2 and 3 need them verbatim:

1. `POSTGRES_DB` as it is used today,
2. `POSTGRES_USER` as it is used today,
3. the exact Docker volume name behind the database service
   (`docker volume ls`), and
4. the row count of `issue` (the before/after data check in step 5).

---

## 1. Tag the rollback image

```bash
cd /opt/hoja/deploy
docker tag "$(docker compose images -q hoja-app)" hoja-app:pre-rename
docker images hoja-app
```

Rollback depends on this tag existing, so do not skip it.

---

## 2. Keep the database identity (the only irreversible step if done wrong)

The repository's `docker-compose.yml` now defaults to
`POSTGRES_DB: ${POSTGRES_DB:-hoja}`, `POSTGRES_USER: ${POSTGRES_USER:-hoja}`
and the volume `hoja-postgres`. Those defaults only apply when the variable is
unset. **Set them explicitly in `.env` to today's values**, so the app keeps
connecting to the database and role that already hold the data:

```dotenv
# /opt/hoja/deploy/.env — values from step 0, unchanged by the rename
POSTGRES_DB=<current database name>
POSTGRES_USER=<current role name>
POSTGRES_PASSWORD=<unchanged>
```

`HOJA_DATABASE_URL` is built from these, so a "modernised" value means the app
authenticates as a role that does not own the data. `initdb` does not re-run on
a populated volume, so the contents are never rewritten — but the connection
string must still name the real role and database.

### Keep the data volume by pinning its name (preferred: no data movement)

Compose names volumes `<project>_<volume>`. After the rename the logical volume
is `hoja-postgres`, so an unmodified deployment would create a **new, empty**
volume and boot against an empty database. Pin the existing volume in a
deployment-local override instead of creating one:

```bash
cd /opt/hoja/deploy
cat > docker-compose.override.yml <<'YAML'
services: {}
volumes:
  hoja-postgres:
    # The volume created before the rename: copy the exact name reported by
    # `docker volume ls` in step 0.
    name: <exact volume name from step 0>
YAML
docker compose config | grep -A2 '^volumes:'
```

The override is deployment-local, never committed, and can be dropped once the
data lives in a `hoja`-named volume (alternative below).

### Alternative: copy the data into a newly named volume

Only in the same maintenance window, with the app stopped, and only after the
dump in step 0 succeeded:

```bash
docker compose stop hoja-app
docker volume create <compose-project>_hoja-postgres
docker run --rm \
  -v <exact volume name from step 0>:/from \
  -v <compose-project>_hoja-postgres:/to \
  alpine sh -c 'cd /from && cp -a . /to'
```

Keep the old volume until `/ready` is green and a real issue list loads; then
drop the override file (the new name becomes the default) and remove the old
volume in a later window.

---

## 3. Rename the environment variables in `.env`

Every application key that starts with `OLD_PREFIX` becomes the same key with
`HOJA_` in its place; the value is unchanged. On this deployment the keys in
scope are:

| Old key (`${OLD_PREFIX}_…`) | New key |
| --- | --- |
| `DATABASE_URL` | `HOJA_DATABASE_URL` |
| `APP_URL` | `HOJA_APP_URL` |
| `PORT` | `HOJA_PORT` |
| `WEB_ROOT` | `HOJA_WEB_ROOT` |
| `CF_REALTIME_APP_ID` | `HOJA_CF_REALTIME_APP_ID` |
| `CF_REALTIME_APP_SECRET` | `HOJA_CF_REALTIME_APP_SECRET` |
| `CF_TURN_KEY_ID` | `HOJA_CF_TURN_KEY_ID` |
| `CF_TURN_KEY_API_TOKEN` | `HOJA_CF_TURN_KEY_API_TOKEN` |
| `CONNECTOR_SIGNING_KEY` | `HOJA_CONNECTOR_SIGNING_KEY` |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | **unchanged** — the sign-up service reads these unprefixed (`hoja_server/signup_config.py`); renaming them silently disables Google sign-in |
| `TURNSTILE_SECRET_KEY` / `TURNSTILE_EXPECTED_HOSTNAME` | **unchanged**, same reason |
| `MEETING_MAX_PARTICIPANTS` | `HOJA_MEETING_MAX_PARTICIPANTS` |
| `GRAPHQL_CONCURRENCY` | `HOJA_GRAPHQL_CONCURRENCY` |
| `SERVER_DEBUG` | `HOJA_SERVER_DEBUG` |
| `AGENT_UPLOAD_DIR` | `HOJA_AGENT_UPLOAD_DIR` |
| `AGENT_DBOS_URL` | `HOJA_AGENT_DBOS_URL` |

`POSTGRES_*` keys keep their names (step 2). `LINEAR_TOKEN` and
`LINEAR_API_URL` keep working as deprecated aliases of `HOJA_TOKEN` and
`HOJA_API_URL`, so anything already exporting them needs no change in this
window.

```bash
cd /opt/hoja/deploy
cp .env .env.bak-pre-hoja-rename
sed -i -E "s/^${OLD_PREFIX}_/HOJA_/" .env
grep -E "^${OLD_PREFIX}_" .env || echo "no old-prefix keys left"
grep -E '^HOJA_' .env | cut -d= -f1 | sort
```

Any key the new image no longer reads is inert; leave it for one release, then
delete it.

---

## 4. Rebuild and roll forward

The image now installs the `hoja` distribution, runs as the `hoja` user, and
starts with `hoja-serve migrate`. The pre-rename console scripts no longer
exist, so a cached image must not be reused.

```bash
cd /opt/hoja/deploy
git fetch --all && git log --oneline -1        # confirm the rename commit
docker compose build --no-cache hoja-app
docker compose up -d hoja-db
docker compose run --rm hoja-app hoja-serve migrate
docker compose up -d hoja-app hoja-traefik
docker compose ps
```

The container command also runs `hoja-serve migrate`; running it explicitly
first makes a migration failure visible before traffic moves.

---

## 5. Verify

```bash
curl -fsS http://127.0.0.1:8787/health
curl -fsS http://127.0.0.1:8787/ready

# The container runs as the renamed user, and only the new commands exist.
docker compose exec -T hoja-app id
docker compose exec -T hoja-app hoja-serve --version
docker compose exec -T hoja-app ls /usr/local/bin

# The GraphQL surface answers with a real token, and the data is still there.
docker compose exec -T hoja-app sh -c \
  'curl -fsS -X POST http://127.0.0.1:8787/graphql \
     -H "Authorization: $HOJA_TOKEN" -H "content-type: application/json" \
     -d "{\"query\":\"{ viewer { name } teams { nodes { key } } }\"}"'

# Row count must match the value recorded in step 0.
docker compose exec -T hoja-db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'select count(*) from issue;'
```

Then open the app in a browser, sign in, and load one issue, one project and
one cycle.

---

## 6. Client machines (not this box)

Local client state keeps the old name and is not migrated automatically:

- `~/.config/$OLD_NAME/config.toml` → `~/.config/hoja/config.toml`
- `~/.local/share/$OLD_NAME/` → `~/.local/share/hoja/`
- `.$OLD_NAME/memory.yaml` in a repository → `.hoja/memory.yaml`
- reinstall the commands: `pipx install --force 'hoja[server]'`

```bash
mv ~/.config/"$OLD_NAME" ~/.config/hoja 2>/dev/null
```

---

## 7. Rollback

Rollback is safe because the database was never renamed.

```bash
cd /opt/hoja/deploy

# 1. Put the previous environment back.
cp .env.bak-pre-hoja-rename .env

# 2. Stop the new stack without touching the volumes.
docker compose down

# 3. Rebuild the previous revision and start it.
git checkout <previous-commit> -- Dockerfile docker-compose.yml
docker build -t hoja-app:rollback .
docker compose up -d hoja-db
docker compose up -d hoja-app hoja-traefik
```

If the build cannot be reproduced quickly, restore the dump from step 0 and
start the released tag instead:

```bash
docker compose exec -T hoja-db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  < /opt/hoja/deploy/backup-pre-hoja-rename-$(date +%F).dump
```

The `docker-compose.override.yml` from step 2 is harmless on rollback — it only
pins the volume the old deployment already used. Remove it only after the data
has been copied to the `hoja`-named volume and verified.

---

## Ordering summary

1. Pre-flight inventory + `pg_dump` (read-only, no downtime).
2. Tag `hoja-app:pre-rename`.
3. Pin `POSTGRES_DB` / `POSTGRES_USER` in `.env` to today's values, and pin the
   existing volume name in `docker-compose.override.yml`.
4. Rewrite the old env prefix to `HOJA_` in `.env`, keeping a `.env.bak`.
5. Rebuild, `hoja-serve migrate`, `up -d`.
6. Verify `/health`, `/ready`, the container user, a GraphQL viewer query, and
   the row counts.
7. Only later: copy the data into a `hoja`-named volume and drop the override.

Anything that cannot be verified on the box (a build needing registry access, a
Traefik configuration outside this repository) is a stop-and-ask, not a guess.
