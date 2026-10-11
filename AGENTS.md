# AGENTS.md — Contributing to Cliniar

> Guide for humans and AI agents who modify this codebase.

Cliniar is a standalone, generic, self-hosted, offline-first, agent-native
work-management product with a Linear-compatible GraphQL interface. Do not encode a particular
organization, repository owner, or deployment as a product default. New work
uses `cliniar`, `cliniar-mcp`, `cliniar-serve`, `CLINIAR_*`, and `cliniar`
config/data paths. Preserve the wire-facing
`LINEAR_TOKEN`, `LINEAR_API_URL`, and Linear GraphQL names.

---

## TL;DR (the rules)

1. **Version and changelog follow release policy.** Update `VERSION`, `pyproject.toml`, and `CHANGELOG.md` for a release; do not bump versions for every local edit.
2. **Run relevant checks.** Start with focused pytest tests and frontend build for affected areas; run the live API E2E script only when its required credentials and test data are configured.
4. **Every commit goes through the pre-commit hook.** No secrets, no `.log`, no `.env`, no `schema/linear-schema.json`.
5. **Every release gets a git tag (`vX.Y.Z`) and a GitHub release.** No exceptions.

---

## Architecture

```
cliniar/
├── VERSION                    Source of truth for __version__
├── pyproject.toml             Build metadata (must match VERSION)
├── CHANGELOG.md               Keep-a-Changelog format
│
├── cliniar/                   Package
│   ├── __init__.py            Reads VERSION
│   ├── cli.py                 Root Typer app, global flags, error wrapper
│   ├── cli_state.py           Per-invocation state container
│   ├── config.py              TOML config + token resolution
│   ├── client.py              Async httpx GraphQL client
│   ├── auth.py                Viewer caching
│   ├── filters.py             Filter DSL → IssueFilter GraphQL input
│   ├── output.py              Six output formatters
│   ├── errors.py              Typed exit codes
│   ├── models/                Pydantic v2 models (User, Team, Issue, ...)
│   ├── graphql/               Query/mutation strings + fragments
│   ├── commands/              One file per command group
│   ├── skill_content/         Canonical markdown for the agent skill (shipped in wheel)
│   │   ├── overview.md
│   │   ├── commands.md
│   │   ├── workflows.md
│   │   ├── filters.md
│   │   ├── output-formats.md
│   │   └── examples.md
│   └── mcp/                   Optional MCP server (`pip install 'cliniar[mcp]'`)
│       ├── content.py         CliniarGuide Pydantic model + load_topic()
│       ├── resources.py       7 read-only resource handlers
│       ├── prompts.py         6 workflow prompt templates
│       └── server.py          FastMCP wire-up; entry point of `cliniar-mcp`
│
├── skills/                    Agent skill bundle (installable into ~/.swarmos and ~/.claude)
│   ├── install.sh             Symlinks skills/cliniar into both skill dirs
│   └── cliniar/
│       ├── SKILL.md           Frontmatter + body (mechanics + behavior + safety)
│       └── references/        Symlinks → ../../cliniar/skill_content/*.md
│
├── schema/
│   ├── linear-schema.json     2.3 MB introspection result (GITIGNORED)
│   └── schema-summary.json    Categorized summary (committed)
│
├── scripts/
│   ├── e2e-test.sh            Live API E2E test suite
│   ├── pre-commit.sh          Secret-blocking pre-commit hook
│   └── install-hooks.sh       Install pre-commit hook
│
└── tests/                     Pytest regression tests
```

### Data flow for any command

```
User CLI input
  └─► cliniar.cli.app (Typer)
        └─► global flags resolved → cli_state.CLIState
              └─► subcommand handler (cliniar/commands/*.py)
                    └─► cliniar.client.LinearClient.execute_as()
                          ├─► httpx POST to the configured self-hosted /graphql endpoint
                          ├─► JSON → dict
                          └─► dict → Pydantic model (validation)
                                └─► cliniar.output.render(model, fmt)
                                      └─► stdout
```

### Adding a new command

1. Pick the right file in `cliniar/commands/` (or create one).
2. Add a Typer command with rich `--help` text.
3. Write the GraphQL string in `cliniar/graphql/queries.py` or `mutations.py`. Use fragments from `fragments.py`.
4. If the response shape is new, add or extend a Pydantic model in `cliniar/models/`.
5. Register the new command group in `cliniar/cli.py` via `app.add_typer(...)`.
6. Add focused pytest coverage; update `scripts/e2e-test.sh` only for live end-to-end behavior.
7. Update release metadata only when preparing a release.

### Adding a new model

```python
# cliniar/models/your_entity.py
from cliniar.models.base import Timestamped

class YourEntity(Timestamped):
    id: str
    name: str
    # use Field(alias="camelCase") for GraphQL → snake_case mapping
```

### Adding GraphQL fragments

Keep fragments **DRY** and **small**. One fragment per entity "view":
- `YourEntityCore` → fields included in lists
- `YourEntityFull` → extra fields included on detail views

```python
# cliniar/graphql/fragments.py
YOUR_ENTITY_CORE = """
fragment YourEntityCore on YourEntity {
  id
  name
  createdAt
}
"""
```

---

## Development Workflow

### Setup

```bash
git clone <repository-url> cliniar
cd cliniar
bash scripts/install-hooks.sh        # MANDATORY — installs the secret-blocking pre-commit hook
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Day-to-day

```bash
# 1. Set your token (one-time per shell)
export LINEAR_TOKEN="lin_api_..."

# 2. Edit code

# 3. Smoke test live API
.venv/bin/cliniar me

# 4. Full E2E suite (takes ~30s)
bash scripts/e2e-test.sh

# 5. Lint + type check
uv run ruff check cliniar/
uv run mypy cliniar/

# 6. Bump version (see "Versioning" below)

# 7. Commit (hook will refuse if you touch secrets)
git add -A
git commit -m "fix(issue): handle null state in IssueSearchResult"
```

---

## Testing

### E2E (configured backend — primary suite)

`scripts/e2e-test.sh` exercises every command against the configured
Linear-compatible endpoint. For standalone product work, prefer the local
backend suite below so verification does not depend on a hosted service.

```bash
export LINEAR_TOKEN="lin_api_..."  # issued by cliniar-serve seed or another configured backend
bash scripts/e2e-test.sh
```

The script reads `LINEAR_TOKEN` from your env. **It does not contain any hardcoded tokens.** If you find one, that's a bug — fix it immediately and rotate the leaked credential.

Pass condition: `SUMMARY: N passed, 0 failed`.

### Unit and regression tests

The `tests/` directory contains pytest coverage for CLI account resolution and
the backend, including optional PostgreSQL checks. Run `uv run pytest`; tests
requiring optional services or drivers may skip when those dependencies are not
installed. The live API matrix in `scripts/e2e-test.sh` is separate and requires
`LINEAR_TOKEN`, `CLINIAR_TEST_TEAM`, and `CLINIAR_TEST_ISSUE`.

### UI styling checks

Run `cd frontend && npm ci && npm run lint:styles && npm run build` for UI changes.
Stylelint checks structural CSS errors (invalid properties/units/colors, duplicate
declarations and malformed selectors), not whether rendered elements overlap.
The pre-commit hook lints **staged** `frontend/src/*.css` using the index; install
dependencies with `npm ci` before committing CSS. Run browser layout checks with
`cd frontend && npm run test:layout` after installing the local-only
Playwright tooling (`cd local-e2e && npm ci && npx playwright install chromium`).
They test project detail, settings panels and provider cards, issue breadcrumbs and
touch board cards against the actual stylesheet at desktop and mobile widths, including
long content, tall properties, navigation open/closed, and form controls. CSS-backed
fixtures are not authenticated app journeys. These checks
are not part of the fast pre-commit hook and do not require a backend.

### UI audit method: define what a component must never do

Before adding or changing a view, inventory its components (inputs, cards, menus,
navigation, actions) and write down **negative invariants** for each. A textbox
must not extend beyond its card, hide entered text, or obscure its label. A card
must not overlap the next card or clip its actions. A menu must not open outside
the visible scroll area. Tabs and buttons must not be covered by another control.
Touch interactions must not disable scrolling unless an equivalent gesture works.
Keep intentional scrolling and truncation, but never truncate a required action.

For each invariant, specify route/component, viewport **width and height**, long
or unbroken content, open/closed/focused state, and expected visible result.
Reproduce the failure in a browser before changing CSS. Trace markup → parent
layout/overflow → child dimensions/position → rendered bounding boxes. Prefer
bounding-box nonintersection, visible-area and control-reachability assertions
over screenshots or tests that only check a CSS class. Check breakpoint neighbors
(for example 700px and 701px), desktop and narrow mobile, short-height screens,
long text, scroll edges, keyboard focus, and touch where relevant. Prove a new
check fails against the old or deliberately faulty styling, then passes after
the fix. Add the narrow check to `npm run test:layout` only once it passes; do
not turn a known failing exploratory probe into a blocking check. Run style lint
and the frontend build afterward. CSS fixtures cannot prove authenticated route
behavior: report that gap and run an app journey when the state needs one.

When parallelizing, assign each agent an independent component/route and its
own worktree. Record its reproduction and invariant, reserve shared CSS/tests
for one integrator, and re-run combined checks after merging. Keep the audit
inventory honest: distinguish fixed, reproduced but pending, and uninspected.

---

## Versioning

We follow [Semantic Versioning](https://semver.org/):

| Bump | When |
|------|------|
| **Patch** `0.3.0 → 0.3.1` | Bugfixes, typo corrections, internal refactors with no behavior change. |
| **Minor** `0.3.x → 0.4.0` | New commands, new flags, new output formats. Backward-compatible. |
| **Major** `0.x → 1.0` (then `1 → 2`) | Removed/renamed commands, changed exit codes, changed default output. |

### Version bump checklist (every change)

1. Edit `VERSION` to the new number.
2. Edit `pyproject.toml` → `version = "X.Y.Z"` (must match `VERSION`).
3. Add a new section to `CHANGELOG.md`:
   ```markdown
   ## [X.Y.Z] — YYYY-MM-DD

   ### Added
   - ...

   ### Changed
   - ...

   ### Fixed
   - ...
   ```
4. Run the E2E suite. Must pass.
5. Commit with message: `release: vX.Y.Z`.
6. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z"` and push: `git push origin vX.Y.Z`.
7. Create GitHub release (see Release Process).
8. Publish to PyPI (see Release Process).

---

## Release Process

### 1. Pre-flight

```bash
# Tests pass?
bash scripts/e2e-test.sh

# Lint passes?
uv run ruff check cliniar/

# Version is bumped in BOTH places?
cat VERSION
grep '^version =' pyproject.toml

# CHANGELOG has the new section?
head -20 CHANGELOG.md
```

### 2. Tag and push

```bash
git add -A
git commit -m "release: v0.3.0"
git push origin main

git tag -a v0.3.0 -m "v0.3.0"
git push origin v0.3.0
```

### 3. Build with uv

```bash
rm -rf dist/
uv build
# → dist/cliniar-0.3.0-py3-none-any.whl
# → dist/cliniar-0.3.0.tar.gz
```

### 4. Publish to PyPI

```bash
# Token MUST come from environment — never paste into a shell that gets logged.
export UV_PUBLISH_TOKEN="pypi-..."
uv publish
```

### 5. Create GitHub release

```bash
gh release create v0.3.0 \
    --title "v0.3.0" \
    --notes-file <(awk '/^## \[0.3.0\]/,/^## \[/{if (!/^## \[/) print; if (/^## \[/ && !found) found=1; else if (/^## \[/) exit}' CHANGELOG.md) \
    dist/cliniar-0.3.0-py3-none-any.whl \
    dist/cliniar-0.3.0.tar.gz
```

### 6. Verify

```bash
# Wait ~60s for PyPI to propagate, then:
uv tool install --refresh cliniar
cliniar --version  # should print the new version
```

---

## Pre-commit Hook

`scripts/pre-commit.sh` is installed via `bash scripts/install-hooks.sh`. It runs on `git commit` and **refuses** to commit any staged file that contains:

- Linear API tokens (`lin_api_...`)
- PyPI API tokens (`pypi-...`)
- AWS access keys (`AKIA...`)
- GitHub PATs (`ghp_`, `ghs_`, `gho_`, `ghu_`, `ghr_`)
- Google service account JSON
- SSH/PGP private keys
- Slack tokens

It also blocks these file types entirely:
- `*.log`
- `.env`, `.env.*`
- `schema/linear-schema.json` (regenerable; it's 2 MB)
- Anything under `.secrets/` or `vault/`

**Bypassing:** `git commit --no-verify` (use only for vetted false positives; audit first).

---

## House Rules

1. **No new runtime dependencies without a security review.** Every dep is a supply-chain attack vector. The dependency tree is currently: `pydantic + pydantic-core (Rust binary) + httpx + httpcore + h11 + idna + certifi + sniffio + anyio + typer + click + shellingham + rich + markdown-it-py + mdurl + pygments + typing-extensions + annotated-types + annotated-doc + exceptiongroup + tomli (py<3.11)`. Don't add to it without justification.
2. **No `dict[str, Any]` leaks** out of `cliniar.client`. Everything that hits a command handler must be a Pydantic model.
3. **No silent failures.** If something can't be done, raise a `CliniarError` subclass with a helpful `hint`.
4. **No print statements** outside `cliniar/output.py` and a couple of init-related scripts. All output goes through the formatter.
5. **No `--insecure` flag, ever.** TLS verification is non-negotiable.

---

## When something breaks

1. Re-run `bash scripts/e2e-test.sh -v` (verbose flag prints full output).
2. Linear API errors will be in the error message verbatim — look for `GRAPHQL_VALIDATION_FAILED` (your query is wrong) vs. `AUTHENTICATION_ERROR` (token issue).
3. Pydantic validation errors mean the response shape changed or the model is wrong. Run with `--verbose` to see the raw response.
4. `cliniar raw query 'query { ... }'` is your friend — bypass our models, talk directly to Linear's GraphQL.
5. Re-fetch the schema from the configured backend (for example, `http://127.0.0.1:8787/graphql`) with `curl`, rather than assuming a hosted endpoint. Update fragments/models as needed.

---

## Roadmap

- v0.3 (current) — agent skill bundle + `cliniar-mcp` MCP server (1 tool + 7 resources + 6 prompts).
- v0.4 — pytest unit tests, hash-pinned `requirements.txt`, `docs/COMMANDS.md`, `cliniar completions` for shell completion install
- v0.5 — `cliniar initiative`, `cliniar document`, `cliniar customer` (next-tier domains)
- v0.6 — config-defined views (`--view my-bugs`), aliases
- v1.0 — stable command surface, full integration with the 8 priority domains
