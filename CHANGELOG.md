# Changelog

All notable changes to Hoja will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Views now work like Linear's (see `docs/views.md`). Views run on the server, so the web
  client, the CLI, the CSV export and Linear-shaped clients all get the same rows.
  - Filters: labels, team/project/initiative scope, `"me"` and `"myTeams"`, relative dates
    stored as ISO-8601 durations (`-P7D`, `today`, `overdue`), started/completed/canceled
    dates, parent/sub-task relations, estimate, subscribers, and project status, lead,
    members, health, dates and initiative. A predicate Hoja cannot run is refused by path,
    never ignored.
  - Execution: every ordering runs in SQL, `reverse` flips the sort rather than the page,
    keyset cursors page past the first 250 rows, `totalCount` is returned, and
    `View.issues` takes a query-time `filter`, `display` and `sort`. `viewPreview` runs a
    view before it is saved.
  - Display preferences in layers (revision 89, `view_preference`): each view or built-in
    page has a default, and each person can set their own. Use `viewPreferences`,
    `viewPreferencesSet` (`scope: "default"` is "Set default for everyone") and
    `viewPreferencesReset`.
  - View subscriptions (revision 89, `view_subscription`). `viewSubscribe` sends
    `viewIssueAdded` and `viewIssueCompleted` to the inbox.
  - Pinned team and project views appear as tabs (revision 90, `saved_view.pinned` and
    `sort_order`, set with `viewPin`).
  - New mutations: `viewDuplicate`, owner hand-over and "Move to" through `viewUpdate`.
    Views have a slug link (`View.url`), and `GET /api/views/<id>/export.csv` exports
    a view.
  - Linear-compatible aliases: `customView`, `customViews`, `customViewCreate`,
    `customViewUpdate`, `customViewDelete` and `customViewHasSubscribers`, with
    `filterData` and `projectFilterData` translated both ways.
  - Web: one filter bar (chips, `F`, Advanced filter, filters in the URL) and one Display
    panel (`Shift V`) on every task list. Adds a full-page view editor, saved-view pages
    (rename, star, the "…" menu, details panel, Clear and Save ▾ for unsaved edits), a
    views list grouped by Personal, Workspace, team and project, favorites in the
    sidebar, views in the command palette, and `O` `V`, `Alt V`, `Alt S`, `Cmd/Ctrl B`
    and `T` shortcuts.
  - CLI: `hoja issue list --view <name|slug|id>` runs a saved view on the server. Other
    list flags narrow it, `--cycle current|next|<name>` included.
- Project members can be managed. `memberIds` on `projectCreate` and `projectUpdate`
  (Linear's field), and a Members picker on the project page. Anyone added follows the
  project and gets "added you to the project" in their inbox (`projectMemberAdded`).
  Removing someone keeps their subscription.
- Cycle start and end notifications go out on time for every team, including teams
  that make cycles by hand. A background sweep (`hoja_server/cycle_events.py`, turned
  off with `HOJA_CYCLE_EVENTS=0`) announces each edge from the cycle's own dates.
  Revision 80 stamps each edge once, so the cadence rollover and the sweep never
  announce it twice, and past cycles are never announced. A cycle that ends without a
  rollover reports its open work as unfinished.
- Dev snapshots copy who follows which project, initiative and cycle.
- Follow projects, initiatives and cycles. There is a Follow switch on the project page,
  on each initiative and in the inbox panel, and the cycle menu's "Subscribe to cycle
  notifications" now works. Creators, leads, owners, members, update authors and
  repliers follow automatically.
  - Followers get posted updates, replies, and status, target-date, lead or owner
    changes. Cycle subscribers hear when a cycle starts and when it ends, with what got
    done and what carried over.
  - Being made a lead or owner, and @mentions in updates or replies, reach you directly.
  - Inbox rows can be about any of these subjects: `InboxNotification.subjectType`,
    `subjectId`, `project`, `initiative`, `cycle`, `projectUpdate` and
    `initiativeUpdate`. Read, archive and snooze work per subject.
  - The inbox shows a summary panel for these subjects, and notification settings are
    grouped by subject.
- Inbox: notifications now follow task subscriptions. Creators, assignees, commenters
  and @mentioned people are subscribed automatically. Subscribers hear about comments,
  status changes, and priority or due-date changes. Assignments, mentions and decision
  requests go straight to the person they name. Each notification carries what happened
  (`data`, `comment`). New `snoozedUntil` and `inboxNotifications(snoozed:)` fields, and
  new mutations for mark unread, unarchive, snooze, archive all read, and per-task read,
  archive and snooze (`notificationsMarkReadForIssue`, `notificationsArchiveForIssue`,
  `notificationsSnoozeForIssue`). Notification preferences cover every kind.
- The web inbox shows one row per task with the task beside it. Opening a row marks it
  read. Keys: J/K to move, E to archive, U for read or unread, H to snooze, S to
  unsubscribe. Archive and snooze can be undone. There are Priority, Snoozed and
  Archived views, an unread badge in the sidebar, and @mention suggestions in comment
  boxes.
- Decisions: an agent can ask a human a question on an issue (`issueDecisionCreate`),
  the issue reports `decisionNeeded` and is flagged on a board or list, and the human
  answers in the issue detail with one click (`issueDecisionAnswer`, idempotent and
  recording `answeredBy`). Agents read the answer back over GraphQL, MCP or
  `hoja decision ask|list|get|wait|answer|cancel`, so a worker can block on a real
  answer instead of writing prose into a comment. See `docs/decisions.md`.
- `memberArchive(userId)` removes a member from a workspace: the row is soft-archived,
  API keys are revoked, browser sessions and the stored credential are deleted, and the
  org membership is deactivated. Self-removal and removing the last active admin are
  refused. `archivedMembers` lists removed people and `memberRestore` brings one back;
  re-inviting a removed address also un-archives that same profile.
- `scripts/test-settings-routes.mjs` (path -> view table) and
  `scripts/ui-probes/settings-e2e.cjs` (browser journeys).

### Fixed

- Views: a label filter no longer returns every task. A view's team or project scope now
  applies to its results. `assignee isNot X` keeps unassigned tasks. A date-only `is` on
  `createdAt` or `updatedAt` matches the whole day.
- Views: a filter chip holding an empty value ("No estimate", "No labels", "No due
  date") offers only the identity operators, and an "Overdue" date chip only "is"; both
  are saved with those operators, so a chip can no longer write a filter the server refuses.
- Entity links named the canonical host instead of the workspace's own. In a
  workspace with a `vanity_host` (Core on `core.hoja.work`), every issue, project
  and profile link was rendered from the process-wide `HOJA_APP_URL`, so it
  opened the canonical host and the wrong workspace: all 99 issues and 7/7
  projects of one workspace did. Links are now built from the workspace's
  origin (`db.tenant_base_url`), revision 73 rewrites the stored ones in place,
  and an invitation is sent on the host of the workspace that issued it.
- Completing a Google sign-in answered "Internal Server Error": the tenant's
  `_announce_member` called `membership_announce.announce` while the module was
  never imported, so every successful sign-in and every accepted invitation
  failed after the identity had already been verified. The resolve-failure
  branch of the same handler logged through an undefined `logger`. Imports are
  fixed, and `tests/test_undefined_globals.py` now fails on any module that
  references a global it never binds.
- `/workspace/team/<key>/cycles/settings` opened workspace Settings instead of the cycle
  settings page: the generic `includes('/settings')` test ran before the team-scoped
  branches. Routing moved to `app/frontend/src/routes.ts` and matches team routes first.

## [0.21.5] — 2026-11-07

### Changed

- The README leads with the product story the website tells, adds the repository
  banner and PyPI badges, and documents the published `hoja-cli` distribution.
  The same text is the project description on PyPI.

### Fixed

- The stdio MCP server's missing-extra hint told users to install `hoja[mcp]`,
  which is unrelated software on PyPI. It now names `hoja-cli[mcp]`.

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

- Matched the observed the upstream API sidebar new-issue button silhouette, dimensions,
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

- Aligned the My issues page shell with the observed the upstream API title, view tabs,
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
  section order, and light selection treatment to the observed the upstream API sidebar.
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

- Documented Hoja as a standalone self-hosted product rather than only a
  CLI client for the upstream API Cloud.

## [0.7.0] — 2026-07-26

### Changed

- Renamed the product and canonical distribution to **Hoja**, a generic
  self-hosted, offline-first, agent-native work-management system with a
  wire-compatible GraphQL interface.
- Canonical executables are now `hoja`, `hoja-mcp`, and `hoja-serve`;
  Python packages, release artifacts, skill metadata, documentation, and
  configuration/data directories use `hoja`.
- Canonical application variables now use the `HOJA_*` prefix. The existing
  `LINEAR_TOKEN` and `LINEAR_API_URL` variables remain unchanged, as do the upstream API
  API field names and GraphQL wire names.
- Removed product ownership and deployment assumptions from the self-hosted
  backend. CloverOps is one tested external consumer of the compatible GraphQL
  surface, not a product owner or required deployment.

### Deprecated

- `clinear`, `clinear-mcp`, and `clinear-serve` remain as executable aliases for
  one release. They are compatibility shims; scripts and new integrations must
  move to the canonical commands now.
- Matching `CLINEAR_*` variables are read only as one-release fallbacks when
  their `HOJA_*` equivalents are unset.
- Existing `~/.config/clinear/` and `$XDG_DATA_HOME/clinear/` files are detected
  only when the canonical `hoja/` location is absent. Copy configuration and
  data into the canonical location; do not maintain two writable copies.
- The skill installer can create deprecated `clinear` discovery symlinks only
  when explicitly passed `--legacy-links`, and never removes or replaces an
  arbitrary existing directory.

### Migration

1. Replace command names with `hoja`, `hoja-mcp`, and `hoja-serve`.
2. Rename application variables from `CLINEAR_*` to `HOJA_*`; leave
   `LINEAR_TOKEN` and `LINEAR_API_URL` unchanged.
3. Move configuration to `~/.config/hoja/config.toml` (or
   `$XDG_CONFIG_HOME/hoja/config.toml`) and local data to
   `$XDG_DATA_HOME/hoja/` (normally `~/.local/share/hoja/`).
4. Reinstall the canonical `skills/hoja` skill. Use `--legacy-links` only
   for agents that cannot yet discover the new name.

---

## [0.6.0] — 2026-07-15

### Added
- **`clinear-serve` — a local, self-hosted, wire-compatible backend**
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
  backend (or any wire-compatible endpoint) with no other change. Defaults to
  the real upstream API when unset.
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

- **Multi-account credential support** — manage multiple the upstream API accounts and switch
  between them automatically per workspace or via explicit `--account` flag.
  - New config schema: `[accounts.<name>]` sections with `token`, `token_env`,
    and auto-populated `org_name`.
  - Workspace-aware defaults: git repository roots can be mapped to specific
    accounts via `config.toml [workspaces]` or automatically detected.
  - New `clinear auth` subcommands:
    - `auth accounts` — list all accounts with default/workspace/current markers
    - `auth add <name> --token <token>` — add a named account (optionally
      verifies token against the upstream API to populate `org_name`)
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
  passing a slugId (the short identifier shown in the upstream API UI) caused an
  "Argument Validation Error" from the upstream API because the mutation's
  `projectId` field requires the full 36-char UUID. A new `_resolve_project_id`
  helper now resolves slugIds and names to canonical UUIDs before sending the
  mutation, matching the resolution pattern already used for teams, labels, and
  states.

---

## [0.3.0] — 2026-05-14

### Added

- **Agent skill bundle** at `skills/clinear/` — a Swarm/Claude-style skill
  (SKILL.md frontmatter + `references/*.md`) that teaches AI agents both the
  mechanics of the CLI and the *behavior* of working through the upstream API (when to
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
- 28/29 passing E2E tests against the live the upstream API.
