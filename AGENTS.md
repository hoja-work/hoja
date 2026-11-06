# AGENTS.md — Hoja contributor operations

Hoja is a standalone, generic, self-hosted, offline-first, agent-native
work-management product with a Linear-compatible GraphQL interface. Keep product
defaults independent of any organization, repository owner, or deployment.

## Non-negotiables

- Use `hoja`, `hoja-mcp`, `hoja-serve`, `HOJA_*`, and Hoja config/data paths for
  new work. Keep `HOJA_TOKEN` and `HOJA_API_URL` canonical; honor the deprecated
  `LINEAR_TOKEN` and `LINEAR_API_URL` aliases and the Linear GraphQL wire names.
- Preserve existing user changes. Inspect `git status --short` before editing and
  use a separate worktree when the checkout already contains unrelated work.
- Never commit credentials, `.env` files, logs, private keys, or
  `schema/linear-schema.json`. Install `scripts/pre-commit.sh` before committing;
  it also runs the anti-slop policy against the complete staged index.
- Do not add runtime dependencies without justification and security review.
- Do not weaken authorization, tenant isolation, TLS verification, or error
  handling to make a change easier.

## Documentation contract

Git stores **durable documentation for what exists now**, not the process used to
produce it.

Allowed repository prose:

- Root community and release files such as `README.md`, `AGENTS.md`, `LICENSE`,
  `CHANGELOG.md`, `CONTRIBUTING.md`, and `SECURITY.md`.
- Reviewed product, architecture, API, and operations guides in `docs/`.
- Registered `README.md` files that document a real component or package next to
  its code.
- Markdown under `hoja/skill_content/`, `skills/`, and `.agents/skills/` when it
  is functional packaged instruction content.

Do not commit implementation plans, proposals, roadmaps, meeting notes, context
snapshots, copied conversations, branch summaries, status reports, generated
audits, or verification transcripts. Keep those in the relevant issue or pull
request, or in local storage outside the repository. Do not rename a work artifact
to make it look like durable documentation.

Every standalone Markdown path is registered in `scripts/check_docs.py`; there
are no directory-wide exemptions for random files. A new durable guide or
component README must describe current behavior and be registered in the same
reviewed change. Packaged skill references must also be registered explicitly.
Update existing documentation when behavior changes; do not add daily summaries,
host-specific details, or point-in-time test results.

Run the local equivalent of the required GitHub check:

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s tests -p 'test_check_docs.py' -v
```

## Repository map

```text
hoja/                    Python CLI, API client, models, GraphQL operations
  mcp/                   Optional stdio MCP server
  skill_content/         Canonical packaged agent reference content
hoja_server/             ASGI app, GraphQL schema/resolvers, storage, writers
frontend/                React/TypeScript browser application
connectors/              Optional connector packages and their local READMEs
docs/                    Reviewed durable product and operations guides
schema/                  GraphQL schema summary (large introspection is ignored)
scripts/                 Development, verification, release, and hook scripts
tests/                   Python unit/integration tests
skills/                  Installable agent skill bundle
.agents/skills/          Repository-local packaged agent skills
.github/workflows/       GitHub Actions checks and release automation
vendor/                  Vendored package documentation and content
web/                     Static web assets
```

Important entry points:

- `hoja/cli.py`: root Typer application and command registration.
- `hoja/client.py`: async HTTPX GraphQL client.
- `hoja/models/`: Pydantic response models.
- `hoja_server/app.py`: HTTP routes and request context.
- `hoja_server/schema.graphql`: GraphQL API contract.
- `hoja_server/resolvers.py`: API behavior and authorization boundaries.
- `hoja_server/store.py`, `writer.py`, `db.py`: reads, transactional writes, and
  ordered database migrations.
- `frontend/src/main.tsx`: application composition and primary routes.

## Development setup

Python 3.10 or newer is required.

```bash
bash scripts/install-hooks.sh
uv sync --extra dev --extra server
cd frontend && npm ci
```

Run the application locally with the Docker instructions in `README.md`. Keep
test databases disposable and credentials outside source control.

## Change workflow

1. Read the implementation and its consumers before editing. Establish relevant
   behavior and security invariants.
2. Make the smallest coherent change. Preserve existing routes, API names, and
   user-visible behavior unless the task explicitly changes them.
3. Add focused regression coverage for behavior changes. Use integration or
   browser checks when the behavior crosses process or rendering boundaries.
4. Update only durable docs affected by the behavior. Never create a completion
   report to explain the change; the diff, tests, commit, and pull request carry
   that information.
5. Run the documentation check and relevant tests below.
6. Review `git diff --check`, `git diff --stat`, and the exact final diff. Confirm
   unrelated checkout changes are absent.

### CLI or GraphQL client changes

- Register command groups in `hoja/cli.py`; put command behavior in
  `hoja/commands/` and GraphQL fields in `hoja/graphql/`.
- Validate new response shapes with Pydantic models. Do not leak `dict[str, Any]`
  out of `hoja.client` into command handlers.
- Keep fragments small and reusable. Surface actionable errors rather than
  silently ignoring failure.

### Backend changes

- Derive organization and user authority from authenticated request context, not
  client-supplied IDs.
- Preserve resolver authorization for every new operation.
- Use the existing Store/Writer boundaries and ordered, contiguous migrations.
  Account for both SQLite and PostgreSQL semantics where persistence changes.
- Never log tokens or return stored secrets through API responses.

### Frontend changes

- Preserve accessible names, keyboard behavior, focus, responsive layout, and
  reduced-motion behavior. Check long content and narrow/short viewports.
- Reuse `frontend/src/markdown.tsx` for user-authored Markdown. Do not introduce
  `dangerouslySetInnerHTML` or a second renderer for those bodies.
- Run style lint and the production build. Add browser geometry or behavior
  assertions when layout or interaction changes; CSS fixtures alone do not prove
  an authenticated application journey.

### Agent skill changes

- Treat `hoja/skill_content/` as the canonical packaged content.
- Preserve `skills/hoja/references/` symlinks rather than copying files.
- Check both the installed skill behavior and the Python package entry points.

## Verification commands

Use the commands relevant to the changed surface:

```bash
# Documentation and its policy tests
python3 scripts/check_docs.py
python3 -m unittest discover -s tests -p 'test_check_docs.py' -v

# Python tests
uv run pytest
uv run ruff check hoja/ hoja_server/
uv run mypy hoja/

# Frontend
cd frontend
npm run lint:ui
npm run lint:styles
npm run build

# Disposable local backend integration
bash scripts/e2e-local-backend.sh
```

`scripts/e2e-test.sh` is a separate live API matrix. Run it only with explicitly
configured test credentials and test data. Report unrun or unavailable checks
honestly; never convert a failing exploratory probe into a passing result by
weakening its assertions.

## GitHub and releases

- `main` requires a pull request, up-to-date branches, resolved conversations, a
  linear history, and the `Anti-slop documentation` status check. Force pushes
  and deletions are disabled, and the protection applies to administrators.
- Do not bypass branch protection or use force pushes to `main`.
- Follow the existing security and dependency workflows for relevant changes.
- Do not bump versions for ordinary edits. For a release, update `VERSION`,
  `pyproject.toml`, and `CHANGELOG.md` together, run verification, tag `vX.Y.Z`,
  and publish using the existing release workflow.
