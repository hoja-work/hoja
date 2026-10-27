# Changelog

All notable changes to Cliniar will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- My issues → Assigned has a "Completed issues" display option (None, Past week,
  Past month, All; default Past week, remembered per browser). Done and cancelled
  issues were previously always hidden there. Closed groups sort last.
- The create-issue dialog has a due date field, and the optimistic issue it adds
  to the board carries that date immediately.
- Board cards can be edited in place: status, due date, priority and assignee are
  changed from the card, on both the workspace/My-issues board and the team board,
  without opening the issue. Edits are optimistic and roll back if the server
  rejects them. Clicking the card title still opens the issue.
- Team settings now manage membership directly on each team card: members are
  listed with a remove control and a workspace member can be added from a picker.
- The new-issue dialog has a documented motion budget (`--motion-*` tokens,
  90–200 ms, transform/opacity only) with an entrance and an exit animation, a
  softened and blurred backdrop, hover/press states on the property pills and
  actions, a visible focus ring on the borderless title and description, a
  spinner while creating, a sticky footer on short viewports, and a focus trap
  that returns focus to the trigger. `prefers-reduced-motion` collapses the
  tokens to 1 ms and the page keeps its scrollbar gutter, so opening the dialog
  shifts nothing.

### Changed

- My issues no longer has a "Load more issues" button. The sync now carries a
  year of closed issues (`closedDays=365`; a cached 14-day snapshot re-bootstraps
  once). Older closed issues are fetched automatically, only the viewer's own
  (assigned for Assigned → All, created for Created), instead of paging the whole
  workspace's closed history. The old button loaded issues that the Assigned tab
  then hid.
- Long My issues lists are windowed like the team list, replacing the
  "Show N more" button.
- The team settings screen renders one card per team (properties, members, actions
  and a delete panel) instead of a single cramped row per team, and each card
  reports its issue and project counts.

### Fixed

- The new-issue dialog no longer hides keyboard focus on the title and
  description, no longer lets Tab escape into the page behind it, and its
  focus indicators meet 3:1 contrast.
- Deleting a team no longer fails because it still has projects. `teamDelete`
  only blocks on projects, cycles and issues for a plain delete; with
  `deleteIssues: true` it deletes the team's issues, its cycles and its project
  links, and leaves the projects in the workspace. The delete panel states what
  goes with the team and, when the server refuses, explains the real blocker and
  offers the retry.
- Date-only due dates render on their own calendar day in every timezone; they
  were previously parsed as UTC midnight and could show the previous day, and the
  "overdue" highlight now uses one shared rule.
- An assignee or state id that is unknown to the current sync snapshot no longer
  blanks the issue's field in the optimistic overlay.

### Testing

- Added `scripts/test-issue-modal-polish.cjs` (`npm run test:modal`), a browser
  suite that measures the dialog instead of screenshotting it: animation
  durations and animated properties, reduced-motion collapse, hover/press/focus
  deltas, focus trap and focus restore, layout shift during interaction, WCAG
  contrast in light and dark, and geometry at 1440×900, 390×844 and 320×568.

## [0.21.4] — 2026-10-06

### Added

- Workspace admins can create a workspace and begin using it under their own
  membership. Accounts with access to multiple workspaces can select the target
  workspace during sign-in, avoiding an ambiguous account-to-workspace choice.

### Fixed

- Team creation now reports a rejected backend response in the settings form
  instead of leaving the user without a clear result.

## [0.21.3] — 2026-10-06

### Added

- Workspace admins can create teams from Workspace settings; the new team and
  its workflow states appear in the workspace navigation after a successful
  response.
- Added an SMTP transport adapter for future email workflows. Invitations do
  not send email through this adapter yet.
- Added GitHub integration, webhook, and feature-parity design proposals. These
  documents describe possible future work; they do not indicate shipped
  integrations.

## [0.21.2] — 2026-10-01

### Fixed

- Team cycle settings now show a retry action after a failed read. The Enable
  action stays disabled until settings load successfully, so a network failure
  cannot be mistaken for an unsaved or disabled schedule.
- A retry starts a fresh request and ignores stale responses from an earlier
  settings read.

## [0.21.1] — 2026-10-01

### Fixed

- Cycle reconciliation now rejects a clock value older than the saved cycle
  boundary before changing the team's Current pointer or completed-cycle
  history.

## [0.21.0] — 2026-10-01

### Added

- Teams can opt into automatic weekly cycles. An administrator selects the
  weekday; cycle boundaries use the team's timezone, with a Current cycle and
  two Upcoming cycles reconciled transactionally when authenticated cycle data
  is read.
- Added team cycle settings and Current/Upcoming navigation.

### Scope

- Issue auto-assignment and rollover, schedule edits, and disabling an enabled
  cadence are not available in this initial implementation.

## [0.20.2] — 2026-10-01

### Fixed

- Team Current and Upcoming cycle routes now apply the selected cycle filter
  before the generic active-issue route, so each page shows issues from the
  requested cycle rather than a broader active list.

## [0.20.1] — 2026-10-01

### Fixed

- Project lists now apply the team filter before cursor pagination; cycle
  details are looked up by organization-scoped ID and count only issues in that
  cycle.
- Team Home shortcuts now open that team, local issue-view preferences persist
  per team, and the Workspace More menu closes when the user clicks elsewhere.

## [0.20.0] — 2026-10-01

### Added

- Added team Home, Issues, Cycles, Projects, and Views destinations, with
  Current and Upcoming cycle links backed by team-scoped data.
- Added Workspace More access to Members. Actions without a backend or product
  contract are identified as unavailable instead of appearing functional.

## [0.19.1] — 2026-10-01

### Fixed

- Standardized compact typography and spacing across My issues, Inbox, Pulse,
  Initiatives, Projects, and Views; restored the mobile navigation opener on
  Inbox, Pulse, and Initiatives.

## [0.19.0] — 2026-10-01

### Added

- Added browser-local project and workspace issue views with separate scopes,
  local favorites, and team navigation. These saved views are not shared with
  other users or synchronized to the server.
- Added a Create more option to the issue composer for entering several issues
  in sequence.

### Changed

- Project issue lists now group by workflow state and use compact rows; the
  issue composer has a centered layout and clearer save/cancel behavior.

## [0.18.1] — 2026-10-01

### Changed

- Project details now keep properties in a compact overview and open editing
  only on request. The editor uses explicit Save and Discard actions, and
  project updates are composed from the project Activity view.

### Fixed

- Loaded the Inter Variable font locally and aligned project titles, rows, and
  tabs with the rest of the workspace typography.

## [0.18.0] — 2026-10-01

### Added

- Added cursor pagination to workspace projects and project-filtered issues,
  with load-more controls rather than silently stopping at the first page.
- Added project Overview, Activity, and Issues views, project update history,
  and project creation fields supported by the backend.
- Added a status board and supported filter/display controls. Counts and filters
  describe loaded data while additional pages remain.

### Fixed

- Archived projects no longer appear in project detail results; unavailable
  project routes show a missing-project state rather than stale details.

## [0.17.0] — 2026-10-01

### Added

- Added organization-scoped Initiative records, project associations,
  stable cursor listing, and create/update/archive GraphQL operations.
- Replaced Initiatives placeholder with Active/Planned/All views, a scoped
  create form, status filtering, description display, and honest empty states.

## [0.16.0] — 2026-10-01

### Added

- Added recipient-scoped Inbox notifications for future issue creation and
  changes, with stable paging, unread counts, mark-read, archive, and mark-all
  actions; historical events are not reconstructed.
- Replaced the generic Inbox issue dashboard with a responsive notification
  feed, unread-only state, issue navigation, and honest empty/error states.

## [0.15.1] — 2026-10-01

### Fixed

- Made project issue rows clickable and keyboard-accessible, opening the
  selected issue with a contextual return to its project.

## [0.15.0] — 2026-10-01

### Added

- Added authenticated project-update publication and a project-scoped Pulse
  composer with required body, health, and duplicate-retry guidance.
- Dogfooded the local project workflow with a started project, nine scoped
  screen-work issues, and a published Pulse update.

### Fixed

- Moved the cramped project editor into page flow, expanded its description
  field, added discard/unchanged-save handling, and separated Archive.

## [0.14.0] — 2026-10-01

### Added

- Added an organization-scoped, paginated project-update post query with a
  persisted revision-9 table, without synthesizing historical posts.
- Replaced the Pulse placeholder with an accessible project-update feed,
  responsive Recent/For me/Popular navigation, loading, empty/error and
  load-more states. Unavailable ranking, subscriptions, comments, reactions
  and custom views are explicitly disclosed rather than simulated.

## [0.13.0] — 2026-10-01

### Added

- Matched the observed Linear sidebar new-issue button silhouette, dimensions,
  light colors, hover, keyboard activation, and close-on-Escape behavior.
- Replaced Hoja’s green brand palette with shared neutral light/dark tokens
  across navigation, My issues, forms, cards, and actions, preserving semantic
  status and error colors.

## [0.12.0] — 2026-09-30

### Added

- Expanded My issues filters to labels, assignee, creator, subscriber, and
  created/updated/due date ranges where the loaded issue fields allow it.
- Added grouping by project, priority, cycle, label, and team plus persistent
  assignee, priority, status, labels, and due-date column controls.

### Fixed

- Labeled filtered counts as loaded results while later pages remain available,
  and returned to sign-in when an expired browser session is rejected.

## [0.11.0] — 2026-09-30

### Added

- Added stable cursor pagination for GraphQL issues and a load-more control in
  My issues, avoiding the previous silent first-100 result limit.
- Added organization-scoped issue activity logging for new mutations and a
  paginated My issues activity feed. Historical changes before migration are
  explicitly unavailable.
- Expanded My issues filters to team, project, cycle presence and content;
  added configurable project/date columns and persistent display preferences.

## [0.10.0] — 2026-09-30

### Added

- Aligned the My issues page shell with the observed Linear title, view tabs,
  compact list groups, and light list surface.
- Added working status/priority filters, focus/status grouping, collapsible
  group counts, and date-grouped recent issue updates with clear disclosure
  that this is not a complete activity history.

### Fixed

- Kept Assigned issues scoped to the viewer and excluded completed/canceled
  issues from its active list; empty and filtered counts reflect visible data.

## [0.9.0] — 2026-09-30

### Added

- Matched the Hoja navigation rail's width, compact rows, workspace header,
  section order, and light selection treatment to the observed Linear sidebar.
- Added sidebar-first Pulse, Initiatives, and favorite Views placeholders that
  explicitly state they are not yet available, without replacing working Hoja
  destinations.

## [0.8.0] — 2026-09-22

### Added

- Added an initial first-party self-hosted workspace UI served by the local
  backend, with token login, workspace/team navigation, issue listing, search,
  and state filtering.
- Added Docker Compose packaging for the application and PostgreSQL with
  persistent storage and health checks.

### Changed

- Documented Cliniar as a standalone self-hosted product rather than only a
  CLI client for Linear Cloud.

## [0.7.0] — 2026-07-26

### Changed

- Renamed the product and canonical distribution to **Cliniar**, a generic
  self-hosted, offline-first, agent-native work-management system with a
  Linear-compatible GraphQL interface.
- Canonical executables are now `cliniar`, `cliniar-mcp`, and `cliniar-serve`;
  Python packages, release artifacts, skill metadata, documentation, and
  configuration/data directories use `cliniar`.
- Canonical application variables now use the `CLINIAR_*` prefix. The existing
  `LINEAR_TOKEN` and `LINEAR_API_URL` variables remain unchanged, as do Linear
  API field names and GraphQL wire names.
- Removed product ownership and deployment assumptions from the self-hosted
  backend. CloverOps is one tested external consumer of the compatible GraphQL
  surface, not a product owner or required deployment.

### Deprecated

- `clinear`, `clinear-mcp`, and `clinear-serve` remain as executable aliases for
  one release. They are compatibility shims; scripts and new integrations must
  move to the canonical commands now.
- Matching `CLINEAR_*` variables are read only as one-release fallbacks when
  their `CLINIAR_*` equivalents are unset.
- Existing `~/.config/clinear/` and `$XDG_DATA_HOME/clinear/` files are detected
  only when the canonical `cliniar/` location is absent. Copy configuration and
  data into the canonical location; do not maintain two writable copies.
- The skill installer can create deprecated `clinear` discovery symlinks only
  when explicitly passed `--legacy-links`, and never removes or replaces an
  arbitrary existing directory.

### Migration

1. Replace command names with `cliniar`, `cliniar-mcp`, and `cliniar-serve`.
2. Rename application variables from `CLINEAR_*` to `CLINIAR_*`; leave
   `LINEAR_TOKEN` and `LINEAR_API_URL` unchanged.
3. Move configuration to `~/.config/cliniar/config.toml` (or
   `$XDG_CONFIG_HOME/cliniar/config.toml`) and local data to
   `$XDG_DATA_HOME/cliniar/` (normally `~/.local/share/cliniar/`).
4. Reinstall the canonical `skills/cliniar` skill. Use `--legacy-links` only
   for agents that cannot yet discover the new name.

---

## [0.6.0] — 2026-07-15

### Added
- **`clinear-serve` — a local, self-hosted, Linear-API-compatible backend**
  (new optional `clinear_server` package; install with `pip install 'clinear[server]'`).
  Speaks the GraphQL subset used by clinear and compatible consumers, so
  the CLI runs fully **offline** against a single-file SQLite database with **no
  rate limits**.
  - `clinear-serve seed` provisions a tenant (org, admin user, team, seeded
    workflow states, API token) and prints the token.
  - `clinear-serve serve` runs the GraphQL server on `127.0.0.1` (FastAPI/Starlette
    + Ariadne + SQLAlchemy Core). `--open` enables zero-friction offline mode
    (any token maps to the seeded identity).
  - `clinear-serve token` mints additional tokens for a tenant.
  - **Multi-tenant by token:** each API token maps to exactly one organization;
    every resolver is org-scoped, so tenants are isolated by construction. One
    backend endpoint serves N tenants via N tokens.
  - Implements `rateLimitStatus` returning effectively-unlimited values and never
    emits HTTP 429.
  - Per-team monotonic issue identifier counter (`ENG-1`, `ENG-2`, …), derived
    `priorityLabel`/`url`/`branchName`, and workflow state-transition timestamps.
  - **PostgreSQL shared-database support:** all server commands resolve explicit
    `--database-url` then `CLINEAR_DATABASE_URL` then the existing SQLite target;
    psycopg 3 uses connection pre-ping and SQLite-only pragmas remain isolated.
    Organization URL keys are unique, token minting explicitly selects an
    organization and user/email in shared databases, API token hashes are
    globally unique, and issue counters use a portable organization-scoped
    atomic update in the insert transaction.
  - Ordered schema revisions support upgrades of existing SQLite and PostgreSQL
    deployments; `clinear-serve migrate` is the explicit hosted deployment step.
  - Mutation references are validated against the authenticated organization
    and compatible team before writes, including states, assignees, projects,
    cycles, parents, labels, project leads, and project-team relationships.
  - CloverOps compatibility includes batched bootstrap aliases, top-level
    workflow-state/cycle filters, project teams/health, issue identifier lookup,
    state transitions, comments, and persisted URL attachments.
  - `$CLINEAR_APP_URL` configures generated issue/project links for owned hosted
    deployments (local default `http://localhost:8787`), and `/ready` verifies
    database connectivity plus schema currency separately from `/health`.
- **`base_url` account setting** (`accounts.<name>.base_url`) plus the
  `$LINEAR_API_URL` environment override, letting clinear point at a local
  backend (or any Linear-compatible endpoint) with no other change. Defaults to
  the real Linear API when unset.
- `scripts/e2e-local-backend.sh` — a 17-check end-to-end suite that boots the
  local backend and drives the real clinear CLI against it (me, teams, states,
  issue create/get/state/assign/prio, comments, filters, search, labels, auth).

### Notes
- Sync (offline↔online op-log + LWW) remains a separate, unimplemented feature.

---

## [0.5.0] — 2026-06-24
### Added
- **Intelligent account auto-selection by team key.** Accounts can now declare
  the team keys they own via `teams = ["SWA", "ENG"]` (config), the new
  `clinear auth add --teams "SWA,ENG"` flag, or `clinear auth teams <name> "SWA,ENG"`.
  When a command targets a team — through `--team SWA` or an identifier like
  `SWA-20` — clinear picks the owning account (and its token) automatically,
  with no `--account` flag or env-var juggling. Resolution order is now:
  `--account` › team-key ownership › workspace mapping › default › first.
- `clinear auth teams <name> "<keys>"` command to set/clear an account's team keys.
- `teams` shown in `clinear auth accounts` output (human + JSON).

### Fixed
- **WorkflowState enum no longer crashes on undocumented state types.** Teams
  with a workflow state whose `type` is outside the six documented categories
  (e.g. `"duplicate"`) previously broke `clinear team states` and
  `clinear issue state` with a Pydantic validation error. The `type` field is
  now a smart union (`WorkflowStateType | str`): known types become the enum,
  unknown types pass through as plain strings.

### Changed
- Agent skill (`SKILL.md` + `skill_content/`) rewritten to match reality:
  corrected the multi-account/auth section (removed the dangerous "rewrite to
  `[auth]`" advice), fixed `comment add`/`comment edit` to positional body
  (no `--body` flag), documented `--account` and the new team auto-selection.

### Tests
- New offline unit suite `tests/test_account_resolution.py` (20 tests) covering
  `team_key_from_hint`, `resolve_account` precedence, `resolve_token`, and the
  WorkflowState smart-union.

---

## [0.4.1] — 2026-05-25

### Added

- **Project memory board** (`clinear memory`) — persistent project-scoped memory
  for agents. Every agent invocation should start with `clinear memory remind`
  to load forced behavioral rules and recent community learnings.
  - `memory remind` — print a formatted digest (forced rules + community context)
  - `memory list` — list all entries with metadata
  - `memory add --title "..." --body "..."` — add a community entry
  - `memory update <id>` — edit an existing entry
  - `memory remove <id>` — delete an entry
  - `memory heal --dry-run` — remove stale community entries (older than
    `heal_after_days`, default 30)
  - Storage: `.clinear/memory.yaml` in the git repo root (project-scoped)
  - Seeded with 5 forced rules: verify auth, search before create, use JSON
    for piping, dry-run on ambiguous mutations, read memory board first
  - Community entries auto-heal after 30 days; forced entries never expire
- Cron-ready: `clinear memory heal` can be scheduled daily to keep the board
  clean and relevant.

### Files Changed
- `clinear/memory_board.py` — storage layer (YAML read/write, heal logic)
- `clinear/commands/memory.py` — 6 subcommands
- `clinear/cli.py` — register `memory_app`
- `clinear/skill_content/overview.md` — add memory board rules

---

## [0.4.0] — 2026-05-25

### Added

- **Multi-account credential support** — manage multiple Linear accounts and switch
  between them automatically per workspace or via explicit `--account` flag.
  - New config schema: `[accounts.<name>]` sections with `token`, `token_env`,
    and auto-populated `org_name`.
  - Workspace-aware defaults: git repository roots can be mapped to specific
    accounts via `config.toml [workspaces]` or automatically detected.
  - New `clinear auth` subcommands:
    - `auth accounts` — list all accounts with default/workspace/current markers
    - `auth add <name> --token <token>` — add a named account (optionally
      verifies token against Linear API to populate `org_name`)
    - `auth switch <name>` — set global default account
    - `auth remove <name>` — remove an account
    - `auth workspace` — show current git repo workspace and mapped account
  - New global flag: `--account <name>` / `-a <name>` — one-off override for
    any subcommand.
  - Backward compatibility: existing `[auth]` sections auto-migrate to
    `accounts.default` on first load. Single-account behavior is preserved
    as fallback.
- **`clinear update`** — self-update command that checks PyPI for the latest
  version and runs the appropriate upgrade command (`pip install --upgrade` or
  `pipx upgrade`). Supports `--dry-run` and `--yes` flags. JSON output mode
  supported for agent consumption.
- New dependency: `tomli-w>=1.0` (required for writing config updates from
  `auth add/switch/remove` commands).

### Changed

- `config.py` — replaced single `AuthConfig` with `AccountConfig` and
  `AccountsConfig` dict. Added `resolve_account()` for workspace-aware
  account selection and `save_config()` for persisting config changes.
- `cli_state.py` — added `account_name` field to `CLIState` for tracking
  the active account.
- `cli.py` — added `--account` global flag and `update` command registration.
- `init.py` — updated config template to use multi-account schema.

---

## [0.3.1] — 2026-05-14

### Fixed

- `issue create --project` and `issue update --project` now accept project **slugId**
  (e.g. `24a5eb4e800e`) and project **name** in addition to the full UUID. Previously,
  passing a slugId (the short identifier shown in the Linear UI) caused an
  "Argument Validation Error" from the Linear API because the mutation's
  `projectId` field requires the full 36-char UUID. A new `_resolve_project_id`
  helper now resolves slugIds and names to canonical UUIDs before sending the
  mutation, matching the resolution pattern already used for teams, labels, and
  states.

---

## [0.3.0] — 2026-05-14

### Added

- **Agent skill bundle** at `skills/clinear/` — a Swarm/Claude-style skill
  (SKILL.md frontmatter + `references/*.md`) that teaches AI agents both the
  mechanics of the CLI and the *behavior* of working through Linear (when to
  search before create, when to use `--output json`, how to chain commands,
  safety rules, anti-patterns). Install with `bash skills/install.sh`.
- **`clinear-mcp` MCP server** — optional Model Context Protocol server
  exposing:
  - **1 tool** `clinear_guide(topic)` returning structured teaching content
    (`overview` / `commands` / `workflows` / `filters` / `output-formats`
    / `examples`).
  - **7 read-only resources**: `clinear://me`, `clinear://issue/{id}`,
    `clinear://team/{key}`, `clinear://project/{id_or_slug}`,
    `clinear://cycle/current/{team_key}`, `clinear://issues/mine`,
    `clinear://issues/team/{team_key}`.
  - **6 prompt templates**: `triage`, `daily_standup`, `create_from_error`,
    `hand_off`, `cycle_review`, `issue_investigate`.
- **By design no mutation tools** on the MCP server. Mutations happen via
  the `clinear` CLI in a shell — every tool/prompt response carries a
  reminder reinforcing this rule.
- `clinear/skill_content/` — canonical markdown source bundled inside the
  wheel via `[tool.hatch.build.targets.wheel.force-include]`. Single source
  of truth for both the skill bundle and the MCP server.
- New optional dependency extra: `pip install 'clinear[mcp]'` pulls in
  `mcp>=1.12.0`. Core install stays untouched for users who don't need MCP.
- New console script `clinear-mcp` (entry point of the MCP server).

### Changed

- Bumped version to 0.3.0.
- Updated User-Agent string emitted by `LinearClient` to `clinear/0.3.0`.

### Notes

- The MCP code is **lazy-imported**. Users who install `clinear` without the
  `[mcp]` extra are not affected; the `clinear-mcp` script prints a friendly
  install hint and exits 2 if invoked without the dep.
- ALL MCP server logging is routed to stderr to keep stdio JSON-RPC clean.

---

## [0.2.0] — 2026-05-14

### Added
- `clinear init` — scaffold the config file at `~/.config/clinear/config.toml`.
- `clinear comment` group: `list`, `add`, `edit`, `delete`.
- `clinear label` group: `list`, `create`, `delete`.
- Label resolution in `issue create` and `issue update` — accept label names
  (comma-separated) and resolve to UUIDs server-side per team.
- `VERSION` file at the repo root; `__version__` now reads from it.
- `CHANGELOG.md`.

### Changed
- `cycle current` now exits 0 with `{ "active_cycle": null }` (JSON) or
  a friendly message (human) instead of crashing with NotFoundError when
  a team has no active cycle.
- `issue search` results now render a full table with priority, state,
  assignee, and identifier columns.
- Bumped version to 0.2.0.

### Fixed
- 4 issues from the v0.1 smoke test:
  - `Issue.labels` / `Issue.subscribers` now flatten the GraphQL
    `{nodes: [...]}` connection wrapper automatically.
  - `searchIssues` no longer spreads the `Issue` fragment on
    `IssueSearchResult` (different GraphQL type).
  - YAML formatter no longer collapses nested object indentation.
  - Error exit codes propagate correctly through the typer entrypoint.

---

## [0.1.0] — 2026-05-14

Initial release.

### Added
- Core commands: `me`, `auth status/whoami`, `team list/get/states/members`,
  `issue list/get/create/update/state/assign/prio/url/search`,
  `project list/get`, `cycle current/list`, `raw query`.
- Pydantic v2 models for User, Team, Issue, Project, Cycle, WorkflowState.
- Async httpx GraphQL client with auth, retry, error handling, rate-limit
  awareness, and pagination helper.
- Six output formats: `human` (Rich tables), `json`, `yaml`, `md` (markdown),
  `plain` (TSV), `ids` (one identifier per line).
- Filter DSL for `issue list` covering team/state/assignee/project/cycle/
  label/priority/free-text/date filters.
- Token resolution from `--token` flag, `LINEAR_TOKEN` env var, or
  `config.toml`.
- Typed exit codes (0–8) for scriptable error handling.
- `--dry-run` for safe mutation previews.
- 28/29 passing E2E tests against the live Linear API.
