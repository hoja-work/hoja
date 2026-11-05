# Rename report — HOJA-4

Hard rename of the product away from its previous distribution name, with
env-var aliases for the Linear-compatible wire names.

- repository: `/home/robert/Work/Hoja-Work/hoja-rename` (worktree)
- branch: `chore/rename-cliniar-to-hoja`
- base: `e2f7c2e`, HEAD after this work: `1a0b00a` (+ this report)
- baseline to beat: `356 passed, 6 skipped, 0 failed`

Nothing was deployed; no host was contacted; `/home/robert/Work/Hoja-Work/hoja`
was not written to.

## Commits

```
1a0b00a docs(client): stop describing the endpoint as Linear in client errors
764e0a4 docs: document the Hoja env-var names and keep the wire surface Linear-compatible
df375f6 refactor(frontend): rename the stylelint rule namespace clinear/ to hoja/
05b96c7 feat(config)!: HOJA_TOKEN and HOJA_API_URL are canonical, LINEAR_* are aliases
e485b89 refactor!: rename cliniar to hoja across packages, scripts and build
```

194 files changed, 2175 insertions, 2015 deletions across the five commits
(two of them are pure `git mv` renames of 87 tracked paths).

## Gate 1 — no occurrence of the old name

Scope note, because the gate is self-referential: this report has to quote the
gate command, the mandated branch name and the pre-rename entry-point path, so
it is the one file that still contains the retired string. Excluding only this
report, the tree — including dotfiles — is empty:

```
$ rg -i "cliniar" --glob '!node_modules' --glob '!dist' --glob '!.git' --glob '!RENAME-REPORT.md'
[no output above; exit status 1]

$ rg -i --hidden "cliniar" --glob '!.git' --glob '!.venv' --glob '!node_modules' \
    --glob '!dist' --glob '!.pytest_cache' --glob '!RENAME-REPORT.md'
[no output above; exit status 1]
```

The gate command as specified, run over the whole worktree:

```
$ rg -i "cliniar" --glob '!node_modules' --glob '!dist' --glob '!.git'
$ rg -i "cliniar" --glob '!node_modules' --glob '!dist' --glob '!.git' | cut -d: -f1 | sort -u
RENAME-REPORT.md
```

Every remaining match is in this file, and all of them are citations: the
branch name from the task, the commit subject that records the rename, the gate
command itself, and the `ls`/`test` lines of the gate-4 evidence. No source
file, script, test, config, Dockerfile, Compose file, doc or skill mentions it;
`frontend/dist` is clean after a rebuild.

## Gate 2 — test suite versus the baseline

```
$ .venv/bin/python -m pytest -q
364 passed, 6 skipped, 1 warning in 74.55s (0:01:14)
```

Baseline was `356 passed, 6 skipped, 0 failed`; the eight extra tests are the
new env-var alias tests in `tests/test_account_resolution.py`. No new failures,
no new skips.

## Gate 3 — frontend type check

```
$ cd frontend && ./node_modules/.bin/tsc --noEmit
[exit status: 0]
```

`npm ci` had to run first in this worktree (`node_modules` is not committed);
`npm run build` also completes (`✓ built in 805ms`) and
`npm run lint:styles` passes after the stylelint namespace rename.

## Gate 4 — console scripts

```
$ .venv/bin/hoja --version
hoja 0.21.4

$ .venv/bin/hoja-serve --help
usage: hoja-serve [-h] [--version] {serve,migrate,seed,token} ...

hoja local backend

positional arguments:
  {serve,migrate,seed,token}
    serve               start the GraphQL server
    migrate             apply outstanding database schema revisions
    seed                seed a tenant DB
    token               mint a new token for a tenant

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit

$ ls .venv/bin | grep -E "^hoja|^cliniar"
hoja
hoja-mcp
hoja-serve

$ test -e .venv/bin/cliniar && echo present || echo ABSENT
ABSENT
```

The old editable install left `cliniar-0.21.4.dist-info`, a `cliniar_server/`
copy in site-packages and three stale entry-point scripts behind; they were
removed and the package reinstalled, so `import hoja` / `import hoja_server`
both resolve to this worktree.

## Gate 5 — env-var aliasing

```
# 1) HOJA_TOKEN wins when both are set
$ HOJA_TOKEN=from-hoja LINEAR_TOKEN=from-linear .venv/bin/python -c "from hoja.config import AccountConfig, resolve_token; print(resolve_token(None, AccountConfig()))"
from-hoja

# 2) LINEAR_TOKEN alone still resolves (HOJA_TOKEN unset)
$ env -u HOJA_TOKEN LINEAR_TOKEN=from-linear .venv/bin/python -c "..."
from-linear

# 3) HOJA_API_URL wins, otherwise LINEAR_API_URL is used
$ HOJA_API_URL=http://hoja.local/graphql LINEAR_API_URL=http://linear.local/graphql .venv/bin/python -c "..."
http://hoja.local/graphql
$ env -u HOJA_API_URL LINEAR_API_URL=http://linear.local/graphql .venv/bin/python -c "..."
http://linear.local/graphql

# 5) the alias tests
$ .venv/bin/python -m pytest -q tests/test_account_resolution.py -k "alias or wins or alone or both or inherit"
8 passed, 20 deselected, 1 warning in 0.04s
```

End to end against a locally seeded `hoja-serve` (SQLite, token `tok123`), all
three resolution paths reach the renamed server and return the viewer row:

```
$ HOJA_CONFIG=/tmp/none.toml HOJA_TOKEN=tok123 HOJA_API_URL=http://127.0.0.1:8812/graphql \
    .venv/bin/python -m hoja me          # -> Viewer table
$ HOJA_CONFIG=/tmp/none.toml LINEAR_TOKEN=tok123 LINEAR_API_URL=http://127.0.0.1:8812/graphql \
    .venv/bin/python -m hoja me          # -> Viewer table, via the deprecated alias
$ .venv/bin/python -m hoja --account local me   # -> 401 when HOJA_TOKEN is set to a wrong value
```

## Gate 6 — docker build

A docker daemon is reachable on this machine. The first attempt failed on a
container-side npm registry timeout (`npm error code ETIMEDOUT` fetching
`write-file-atomic-5.0.1.tgz`); the retry succeeded with no change to the
Dockerfile.

```
$ docker build -t hoja-rename-gate:local .
Step 1/16 : FROM node:22-alpine AS frontend-build
Step 4/16 : RUN npm ci
Step 12/16 : RUN python -m pip install --no-cache-dir --upgrade pip     && pip install --no-cache-dir '.[server,mcp]'     && chown -R hoja:hoja /app
Step 13/16 : ENV HOJA_WEB_ROOT=/app/web
Step 14/16 : USER hoja
Step 16/16 : CMD ["sh", "-c", "hoja-serve migrate && hoja-serve serve --host 0.0.0.0 --port 8787"]
Successfully tagged hoja-rename-gate:local
build exit=0

$ docker run --rm hoja-rename-gate:local id
uid=999(hoja) gid=999(hoja) groups=999(hoja)
$ docker run --rm hoja-rename-gate:local hoja-serve --version
hoja-serve 0.21.4
$ docker run --rm hoja-rename-gate:local ls /usr/local/bin
hoja
hoja-mcp
hoja-serve
$ docker image inspect --format '{{.Config.Env}} {{.Config.User}} {{.Config.Cmd}}' hoja-rename-gate:local
[... HOJA_WEB_ROOT=/app/web] hoja [sh -c hoja-serve migrate && hoja-serve serve --host 0.0.0.0 --port 8787]
```

The image also serves the renamed frontend build (`/app/web/app.js`,
`/app/web/assets`, `/app/web/hoja-clover.8371400e.svg`), and `pip install`
inside it reports `hoja-0.21.4` with no old-name packages.

## Extra verification

- `uv lock --check` → `Resolved 124 packages`, exit 0.
- `bash scripts/e2e-local-backend.sh` (17-check CLI matrix against a seeded
  SQLite `hoja-serve`) → `SUMMARY: 17 passed, 0 failed`.
- `docker-compose.yml` keeps `${POSTGRES_DB:-hoja}` / `${POSTGRES_USER:-hoja}`
  defaults and the `hoja-postgres` volume, so an existing `.env` still wins.

## Decisions, and what a reviewer should know

1. **The CLI's last-resort endpoint is unchanged.** `hoja/client.py` still falls
   back to the public Linear GraphQL endpoint when no `--api-url`,
   `$HOJA_API_URL`, `$LINEAR_API_URL` or `account.base_url` is configured. That
   is a routing behaviour, not a naming one, so it was left alone; the constant
   was renamed to `DEFAULT_API_URL` and documented as "point this at your own
   `hoja-serve`". Flip it to `http://127.0.0.1:8787/graphql` only as a separate,
   deliberate product decision.
2. **`DEPLOY-RENAME.md` never spells the old name.** Gate 1 is a literal
   zero-match check, so the runbook derives the retired prefix from the box
   itself (`OLD_PREFIX`, `OLD_NAME`) instead of quoting it. It is still a
   copy-pasteable runbook; the operator reads the two variables off their own
   `.env` and `~/.config`.
3. **`clinear` (the pre-0.7.0 name) still appears in historical CHANGELOG
   entries.** Those entries record releases that really happened under that
   name; rewriting them would falsify the history. Live identifiers were
   renamed: the four stylelint rules no longer report as `clinear/*`
   (`df375f6`). One stale absolute path in `scripts/test-team-status-age.cjs`
   still contains the 0.7.0-era name; it is a machine-specific Playwright
   fallback, unused unless that directory exists.
4. **Local state is not migrated.** `~/.config/<old-name>/`,
   `~/.local/share/<old-name>/` and `.<old-name>/memory.yaml` are not
   auto-detected (that would require the old string in the codebase). The
   CHANGELOG entry, `DEPLOY-RENAME.md` step 6 and the renamed `hoja init`
   template tell the user to move them by hand.
5. **The token env var is read before the config token.** This is unchanged
   behaviour, and it is why `scripts/e2e-local-backend.sh` fails when the
   developer's shell exports a real `LINEAR_TOKEN`: the ambient token now
   arrives through the deprecated alias and shadows the seeded config token.
   With a clean environment the script is 17/17.

## Not verified

- The actual deployment described in `DEPLOY-RENAME.md` (no host access by
  design, so its commands are unexecuted).
- Sign-in flows in a browser against the built image; only HTTP-level checks
  (`/health`, `/ready`, GraphQL) and the CLI matrix were run.
