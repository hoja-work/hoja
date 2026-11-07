# Hoja

**A self-hosted workspace for issues, projects, cycles, and team work.** Hoja includes a browser UI and a Python CLI. It can run against its own SQLite or PostgreSQL-backed service; the GraphQL API also implements a Linear-compatible surface for clients that use it.

The Python package is published on PyPI as **`hoja-cli`** and installs the
commands **`hoja`**, **`hoja-serve`**, and the optional **`hoja-mcp`**. The
distribution carries the `-cli` suffix because the `hoja` project on PyPI is
unrelated software; install `hoja-cli`, then run `hoja`.

## What you can do

- Sign in to a workspace, switch between memberships, and create workspaces, teams, and members.
- Create, search, filter, assign, and update issues; browse team boards and cycles; open issue details and comment.
- Organize projects, review project issues and updates, and manage initiatives.
- Use Inbox for future issue notifications, with unread/read and archive controls. Existing historical events are not backfilled.
- Save issue and project views in the browser. These views are local to that browser, not shared workspace records.
- Use the CLI for shell workflows and automation. Responses are validated models; `--output json` and `--output ids` are suitable for scripts.

Hoja does not require Linear Cloud. The API uses Linear-compatible GraphQL names and authenticates with `HOJA_TOKEN` / `HOJA_API_URL`, so compatible clients can target a Hoja server. The former `LINEAR_TOKEN` and `LINEAR_API_URL` names are still read as deprecated aliases (the `HOJA_*` variable wins when both are set). Compatibility does not mean every Linear feature is implemented. Unsupported screens or controls are presented as unavailable rather than simulated as working.

## Run the workspace locally with Docker

This is the recommended way to try Hoja locally. Docker Compose builds the web
app and starts it with PostgreSQL. You will need Docker Desktop (or Docker
Engine with the Compose plugin) installed and running. The first build needs
internet access to download the base images and frontend/Python dependencies.

1. In the repository root, create a `.env` file with a password for the local
   PostgreSQL database:

   ```dotenv
   POSTGRES_PASSWORD=replace-with-a-long-random-password
   ```

   Keep this file private; do not commit it. Compose currently has a
   development fallback password, so setting your own avoids relying on that
   default. If port `8787` is already in use, add `HOJA_PORT=8788` to this
   same file and use port 8788 in the browser URL below.

2. Build and start the services:

   ```bash
   docker compose up --build -d
   ```

   The first build can take a few minutes. Compose waits for PostgreSQL to
   become healthy; the app applies database migrations when it starts.

3. Open [http://localhost:8787](http://localhost:8787) (or the port you chose).
   On a fresh database, the browser wizard guides you through choosing a
   language preference, creating your admin profile/password, and naming your
   workspace and first team. Existing databases go to the sign-in screen.

   To confirm the server responds before opening the UI, run
   `curl -i http://localhost:8787/health`; a healthy server returns HTTP 200
   with a JSON status body. The `/ready` endpoint reports whether the database
   schema is ready; if it is not, inspect the app logs below.

4. When prompted, optionally add teammates. Hoja creates each member and
   provides a copyable invite link. Share it with that person; they can open it,
   enter the invited email, and set their password. Links expire after 72 hours
   and work once. Hoja does not email invitations. You can skip this and invite
   people later from **Members**. The finish step can show the admin API key for
   CLI setup. Browser sign-in uses an HttpOnly cookie.

### Stop, restart, and reset

Stop the containers while keeping your workspace data:

```bash
docker compose down
```

Start them again later with `docker compose up -d`. PostgreSQL data is stored
in the named `hoja-postgres` volume and survives ordinary container stops
and rebuilds. To permanently delete the local database and all demo data, use
`docker compose down -v`; this is destructive and the data cannot be recovered
without a backup.

### Troubleshooting

- **Docker is unavailable:** start Docker Desktop / Docker Engine, then retry.
- **`docker compose` is not a command:** install or enable the Docker Compose
  v2 plugin (included with current Docker Desktop), then verify with
  `docker compose version`. This guide uses the `docker compose` subcommand,
  not the legacy standalone `docker-compose` command.
- **Port is already allocated:** set `HOJA_PORT=8788` in `.env` and browse
  to `http://localhost:8788`.
- **The app cannot connect to PostgreSQL:** check `docker compose ps` and
  `docker compose logs db app`; Compose starts the app only after the database
  health check passes. Correct `.env` changes may require recreating services
  with `docker compose up -d`.
- **The app starts but is not ready:** check `docker compose logs app db` and
  `curl -i http://localhost:8787/ready`. The app runs migrations at startup;
  wait for the app container to finish starting, then retry. Do not remove the
  PostgreSQL volume as a first troubleshooting step.
- **The wizard reports that the instance is already set up:** use the existing
  sign-in screen. Bootstrap is only available on a fresh database. To restart a
  disposable demo from scratch, use the destructive `docker compose down -v`
  command above.

This Compose setup is intended for a local demo. The fallback database password
in `docker-compose.yml` is for development only; set a private password in
`.env` before using the configuration beyond a disposable local instance. Use
letters and numbers in that password because Compose interpolates it into the
PostgreSQL connection URL.

See [deployment](docs/DEPLOYMENT.md) for database setup, migrations, readiness
checks, and multi-organization token provisioning.

For an isolated SQLite instance instead:

```bash
python -m pip install 'hoja-cli[server]'
hoja-serve seed --tenant local --org "Local Workspace" \
  --org-key local --team-key ENG --email user@example.test
hoja-serve serve --tenant local --host 127.0.0.1 --port 8787
```

## Use the CLI

Install the CLI from PyPI or from a checkout:

```bash
python -m pip install hoja-cli
# or, isolated from your system Python:
uv tool install hoja-cli
# or, from this repository:
uv sync --extra dev --extra server
```

Configure an account in `~/.config/hoja/config.toml`:

```toml
[accounts.local]
base_url = "http://127.0.0.1:8787/graphql"
token_env = "HOJA_TOKEN"

[defaults]
default_account = "local"
```

Export the token as `HOJA_TOKEN` (or choose a different `token_env` for the
account), then try:

```bash
hoja me
hoja team list
hoja issue list --assignee me
hoja issue create --team ENG --title "Review onboarding flow"
hoja -o json issue list --state Todo
```

The CLI also supports projects, cycles, comments, labels, raw GraphQL queries, and dry-run previews for supported mutations. Run `hoja --help` or `hoja <command> --help` for the current command and option list. Clients written against the Linear-compatible surface can point at Hoja with `LINEAR_TOKEN` and `LINEAR_API_URL`; those names remain accepted as deprecated aliases of `HOJA_TOKEN` and `HOJA_API_URL`.

## Agent integrations

The optional MCP server provides one guide tool, seven read-only resources, and six workflow prompts. It does not expose mutation tools; make changes through the CLI.

```bash
python -m pip install 'hoja-cli[mcp]'
```

Register `hoja-mcp` as a stdio MCP server in your client, passing the needed token through that client's environment configuration. The server reads the same account configuration as the CLI. See [the MCP section below](#mcp-server-setup) for a complete example.

The repository also includes an installable agent skill:

```bash
bash skills/install.sh --claude-only
```

It installs the `skills/hoja` instructions into the selected agent skill directory; `--copy` copies rather than symlinks.

## MCP server setup

Install the optional MCP dependency alongside the CLI:

```bash
python -m pip install 'hoja-cli[mcp]'
```

For Claude Desktop, add a server entry to its MCP configuration. Use an absolute
executable path if the GUI does not inherit your shell `PATH`:

```json
{
  "mcpServers": {
    "hoja": {
      "command": "/absolute/path/to/hoja-mcp",
      "env": {
        "HOJA_TOKEN": "<workspace-token>"
      }
    }
  }
}
```

Replace the command path with the result of `which hoja-mcp`. If the selected
account sets a custom `token_env`, pass that variable instead. The MCP server
provides one guide tool, seven read-only resources, and six workflow prompts;
it does not provide mutation tools.

## Development

Python 3.10 or newer is required. Install development and backend dependencies, then run the tests:

```bash
uv sync --extra dev --extra server
uv run pytest
```

Build the browser app with `npm ci && npm run build` in `frontend/`. The local backend integration script is `bash scripts/e2e-local-backend.sh`; it uses a disposable local database. The live API CLI matrix in `scripts/e2e-test.sh` requires `HOJA_TOKEN`, `HOJA_TEST_TEAM`, and `HOJA_TEST_ISSUE`.

Contributor and agent operations, including the documentation contract, are in
[AGENTS.md](AGENTS.md). Run `python3 scripts/check_docs.py` before submitting
changes that add or modify documentation.

Product architecture is described in [Design](docs/DESIGN.md); deployment instructions are in [Deployment](docs/DEPLOYMENT.md). The [changelog](CHANGELOG.md) records release-level behavior changes.

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
