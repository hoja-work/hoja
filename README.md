<p align="center">
  <img src="https://raw.githubusercontent.com/hoja-work/hoja/main/.github/assets/banner.svg" alt="Hoja — keep every agent task on the same page" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/hoja-cli/"><img alt="PyPI" src="https://img.shields.io/pypi/v/hoja-cli?label=pypi"></a>
  <a href="https://pypi.org/project/hoja-cli/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/hoja-cli"></a>
  <a href="https://pypi.org/project/hoja-cli/"><img alt="Downloads" src="https://img.shields.io/pypi/dm/hoja-cli"></a>
  <a href="https://github.com/hoja-work/hoja/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/pypi/l/hoja-cli"></a>
  <a href="https://github.com/hoja-work/hoja/actions/workflows/documentation.yml"><img alt="Documentation check" src="https://github.com/hoja-work/hoja/actions/workflows/documentation.yml/badge.svg"></a>
  <a href="https://github.com/hoja-work/hoja/actions/workflows/security-audit.yml"><img alt="Dependency audit" src="https://github.com/hoja-work/hoja/actions/workflows/security-audit.yml/badge.svg"></a>
</p>

# Hoja

**One self-hosted place where people and agents plan, assign and review work.**
Hoja is a workspace for issues, projects, cycles and teams. Keep your editor,
terminal and harness; reach the same work through the browser app, the `hoja`
CLI, a Linear-compatible GraphQL API and a read-only MCP server.

Self-host it under the MIT license, or use [Hoja Cloud](https://app.hoja.work)
at $15 per organization each month. Neither path is priced per seat.

## Why teams use it

- **One record for people and agents.** A person triages in the browser, an agent
  updates the same issue from a shell, and everyone sees one current state.
- **Your harness stays yours.** Hoja is a shared work record, not an agent
  runtime. It does not run, host or watch your agents.
- **Self-hosted and offline-first.** SQLite for a laptop or a single box,
  PostgreSQL for a team server. No Linear Cloud account required.
- **Scriptable by default.** Every CLI response is a validated model, and
  `--output json` / `--output ids` are built for scripts, CI and agents.

> Plan in Hoja. Build in your existing harness.

## Three ways in

| Surface | What it gives you |
| --- | --- |
| **Workspace** | Inbox, My issues, Projects, Cycles, Teams and saved Views in the browser. Create, assign, discuss and review issues; group them into projects; use cycles as time-bounded reviews of what shipped, what is open and what was canceled. |
| **CLI** | `me`, `auth`, `init`, `team`, `issue`, `project`, `cycle`, `comment`, `label`, `memory`, `raw` and `update` — enough to drive real work from a terminal or a script. |
| **API** | A GraphQL endpoint with Linear-compatible names. Point your own tools at `HOJA_API_URL` with a workspace token. |
| **Agents** | An optional stdio MCP server with one guide tool, seven read-only resources and six workflow prompts. It has no mutation tools; changes go through the CLI or API. |

## Install the CLI

```bash
uv tool install hoja-cli     # or: python -m pip install hoja-cli
hoja --version
```

The PyPI distribution is **`hoja-cli`** and it installs the commands **`hoja`**,
**`hoja-serve`** and the optional **`hoja-mcp`**. The distribution carries the
`-cli` suffix because the `hoja` project on PyPI is unrelated software: install
`hoja-cli`, then run `hoja`.

Point it at a workspace with `HOJA_API_URL` and `HOJA_TOKEN`, or configure an
account once in `~/.config/hoja/config.toml`:

```toml
[accounts.local]
base_url = "http://127.0.0.1:8787/graphql"
token_env = "HOJA_TOKEN"

[defaults]
default_account = "local"
```

```bash
hoja me
hoja issue list --assignee me
hoja issue create --team ENG --title "Review onboarding flow"
hoja -o json issue list --state Todo
hoja <command> --help          # current flags for every command
```

`HOJA_TOKEN` and `HOJA_API_URL` are canonical. The former `LINEAR_TOKEN` and
`LINEAR_API_URL` names are still read as deprecated aliases, and the GraphQL type
names match Linear, so compatible clients can target Hoja without a rewrite.

## Run the workspace

### Docker Compose (recommended)

Docker Desktop, or Docker Engine with the Compose plugin, builds the web app and
starts it against PostgreSQL. The first build pulls base images and frontend and
Python dependencies, so it needs internet access.

1. Create `.env` in the repository root. `POSTGRES_PASSWORD` is required;
   Compose refuses to start without it.

   ```dotenv
   POSTGRES_PASSWORD=replace-with-a-long-random-password
   ```

   Keep this file private and do not commit it. Use letters and numbers: Compose
   interpolates the password into the PostgreSQL connection URL. If port `8787`
   is taken, add `HOJA_PORT=8788`.

2. Build and start:

   ```bash
   docker compose up --build -d
   ```

   Compose waits for PostgreSQL to pass its health check, then the app applies
   database migrations before serving.

3. Open [http://localhost:8787](http://localhost:8787). A fresh database starts
   the setup wizard: language, your admin profile and password, then your
   workspace and first team. Existing databases go straight to sign-in.
   `curl -i http://localhost:8787/health` reports liveness and
   `curl -i http://localhost:8787/ready` reports schema readiness.

4. The wizard can add teammates. Hoja creates each member and hands you a
   single-use invite link valid for 72 hours; it does not send invitation email.
   The finish step shows the admin API key once for CLI setup. Browser sign-in
   uses an HttpOnly cookie.

### SQLite, no Docker

```bash
python -m pip install 'hoja-cli[server]'
hoja-serve seed --tenant local --org "Local Workspace" \
  --org-key local --team-key ENG --email user@example.test
hoja-serve serve --tenant local --host 127.0.0.1 --port 8787
```

`hoja-serve seed` prints the token to export as `HOJA_TOKEN`.

### Operating it

- `docker compose down` stops the stack and keeps data in the `hoja-postgres`
  volume; `docker compose up -d` brings it back.
- `docker compose down -v` deletes the volume and every record in it. It cannot
  be undone without a backup.
- If the app is not ready, read `docker compose logs app db` before touching the
  volume. The container runs migrations on start.
- If `docker compose` is not a command, enable the Compose v2 plugin and check it
  with `docker compose version`. This guide does not use legacy `docker-compose`.

## Connect an agent

```bash
python -m pip install 'hoja-cli[mcp]'
which hoja-mcp
```

Register `hoja-mcp` as a stdio server in your client, passing the token through
that client's environment. Use an absolute path when the client does not inherit
your shell `PATH`:

```json
{
  "mcpServers": {
    "hoja": {
      "command": "/absolute/path/to/hoja-mcp",
      "env": { "HOJA_TOKEN": "<workspace-token>" }
    }
  }
}
```

The server reads the same account configuration as the CLI. It exposes one guide
tool, seven read-only resources and six workflow prompts, and it deliberately has
no mutation tools.

The repository also ships an agent skill:

```bash
bash skills/install.sh --claude-only     # or --swarm-only
bash skills/install.sh --copy            # copy instead of symlink
```

## Where Hoja stops

Honest boundaries keep the product predictable:

- Hoja does not run or monitor agents. Your harness does that; Hoja holds the
  work record they read and update.
- The MCP server is read-only. Mutations belong to the CLI and the API, where a
  token and its permissions are explicit.
- No prebuilt GitHub, Slack or meeting sync connectors are included. Use the CLI
  or API for your own integrations.
- Linear compatibility covers the GraphQL surface Hoja implements, not every
  Linear feature. Unsupported screens say so instead of pretending to work.
- Saved Views live in the browser, not in the shared workspace.

## Development

Python 3.10 or newer.

```bash
uv sync --extra dev --extra server
uv run pytest

cd frontend && npm ci && npm run build
```

To run the browser app against a local backend, `bash scripts/dev-local.sh` seeds a
disposable workspace in `.hoja/dev/`, serves the API on `127.0.0.1:8787` and the
Vite dev server on `127.0.0.1:5173`, and proxies `/graphql`, `/api`, `/auth`,
`/onboarding`, `/sync` and `/ws` to it. Both bind loopback, the script refuses to
start with `HOJA_DATABASE_URL` or a non-local `HOJA_APP_URL` set, and the dev proxy
rejects a non-loopback `HOJA_DEV_BACKEND`. Sign in with the API key it prints, using
the login screen's **API key** mode. `HOJA_DEV_RESET=1` reseeds the database.

Add `--tailscale` (`HOJA_DEV_TAILSCALE=1`) to bind only the Vite UI to this
machine's `100.x` Tailscale address so another device on the same tailnet can open
it; the backend stays on `127.0.0.1` and keeps proxying through Vite. If the host
firewall is active, allow the UI port on the tailnet interface once:
`sudo ufw allow in on tailscale0 to any port 5173 proto tcp`. Plain HTTP on a
`100.x` address is not a secure browser context, so clipboard copy, local view
creation and camera/microphone meetings may be unavailable there.

- `bash scripts/e2e-local-backend.sh` runs the backend integration pass against a
  disposable local database.
- `bash scripts/e2e-test.sh` runs the live CLI matrix and needs `HOJA_TOKEN`,
  `HOJA_TEST_TEAM` and `HOJA_TEST_ISSUE`.
- `python3 scripts/check_docs.py` is the documentation check CI enforces.

Contributor and agent operations are in
[AGENTS.md](https://github.com/hoja-work/hoja/blob/main/AGENTS.md).

## Documentation

- [Design](https://github.com/hoja-work/hoja/blob/main/docs/DESIGN.md) — product and architecture.
- [Deployment](https://github.com/hoja-work/hoja/blob/main/docs/DEPLOYMENT.md) — databases, migrations, readiness, tokens and release publishing.
- [Remote MCP](https://github.com/hoja-work/hoja/blob/main/docs/hoja-mcp.md) — the experimental OAuth-protected remote server; the stdio server is covered above.
- [Cycle metrics](https://github.com/hoja-work/hoja/blob/main/docs/cycle-metrics.md) and [meetings](https://github.com/hoja-work/hoja/blob/main/docs/meetings.md).
- [Changelog](https://github.com/hoja-work/hoja/blob/main/CHANGELOG.md) — release-level behavior changes.

## First run in the browser

On a fresh database, opening Hoja starts the setup wizard: your admin profile and
password, the workspace name and address, and the first team. The workspace,
admin, team, default workflow states and browser session are created together in
one step, so a failed attempt cannot leave a half-created workspace.

The remaining steps are optional and can be skipped:

- **First project** — created with the selected team; skip and add one later from
  **Projects**.
- **Invite teammates** — each invitation is a single-use link that expires after
  72 hours. A self-hosted server does not send email, so share the link yourself.
  A retry finds the person by email instead of adding a duplicate.
- **Google Workspace** — shown only when the deployment configured its Google
  OAuth client. It grants read-only Gmail, Drive, Calendar and Docs access, and
  this step connects it for the whole workspace (admins only).

Progress is kept in the browser: names and the ids the server created, never
passwords, API keys or invite codes. Reloading resumes the first unfinished step
after checking it against server state. Existing workspaces continue to show the
sign-in screen, and **Create workspace** in the workspace menu reuses the team,
project and invitation steps without asking for an account again.

The finish step summarizes only what exists and offers one action into the
workspace. The admin API key, when the server returns one, is shown once there
for CLI configuration.

## License

MIT. See [LICENSE](https://github.com/hoja-work/hoja/blob/main/LICENSE).
