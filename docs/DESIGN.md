# hoja — Design Document

> the upstream API CLI built in Python with Pydantic v2 + httpx + Typer
> Designed for: humans, agents, CI/CD, and shell pipelines

---

## Philosophy

1. **Type-safe everything.** Every API response is a validated Pydantic model. No `dict[str, Any]` leaks into business logic.
2. **Agent-first, human-second.** JSON output is the canonical contract. Human-friendly tables are a presentation layer on top.
3. **Composable.** Every command works in pipelines (`hoja issue list | jq | xargs ...`).
4. **Idempotent where possible.** Re-running a command shouldn't break state.
5. **Fast.** Async by default. Concurrent fetches where safe.
6. **Discoverable.** `hoja --help`, `hoja issue --help`, `hoja issue create --help` should be sufficient.
7. **Honest errors.** the upstream API errors surfaced verbatim with context. No silent failures.

---

## API Surface (from introspection)

- **151 root queries**
- **353 root mutations**
- **519 object types**
- **371 input types**
- **92 enums**

v1 covers the 8 highest-value domains. v2+ expands.

---

## Domain Priority

| Tier | Domain | Queries | Mutations | v1? |
|------|--------|---------|-----------|-----|
| Core | issue | 17 | 34 | ✅ |
| Core | team | 3 | 9 | ✅ |
| Core | user (viewer/me) | 3 | 12 | ✅ |
| Core | workflow (states) | 2 | 3 | ✅ |
| High | project | 13 | 31 | ✅ |
| High | cycle | 1 | 5 | ✅ |
| High | comment | 1 | 5 | ✅ |
| High | label | (under issue) | - | ✅ |
| Med  | initiative | 7 | 17 | v2 |
| Med  | document | 3 | 4 | v2 |
| Med  | customer | 7 | 18 | v2 |
| Med  | attachment | 3 | 15 | v2 |
| Low  | integration | 4 | 59 | v3 |
| Low  | webhook | 1 | 4 | v3 |
| Low  | roadmap | 3 | 8 | v3 |
| Low  | agent (the upstream contract's AI) | 5 | 9 | v3 |

---

## Command Hierarchy

```
hoja
├── auth                    # Token management
│   ├── login               # Interactive: prompt for token, validate, store
│   ├── status              # Show authenticated user
│   ├── logout              # Clear stored token
│   └── whoami              # Alias for `me`
│
├── me                      # Viewer (current user) info
│
├── issue                   # Issues (most-used domain)
│   ├── list                # List issues with filters
│   ├── get <id>            # Get single issue (full detail)
│   ├── create              # Create issue (interactive or flags)
│   ├── update <id>         # Update fields
│   ├── delete <id>         # Archive issue
│   ├── assign <id> <user>  # Quick reassign
│   ├── state <id> <state>  # Change workflow state
│   ├── prio <id> <0-4>     # Change priority
│   ├── label <id> +Bug -Fix # Add/remove labels
│   ├── comment <id> <body> # Add comment
│   ├── open <id>           # Open in browser
│   ├── url <id>            # Print URL
│   └── search <query>      # Full-text search
│
├── team                    # Teams
│   ├── list
│   ├── get <key>           # by key (ENG) or id
│   ├── members <key>
│   └── states <key>        # Workflow states for team
│
├── project                 # Projects
│   ├── list
│   ├── get <id|name>
│   ├── create
│   ├── update <id>
│   ├── issues <id>         # Issues in project
│   └── archive <id>
│
├── cycle                   # Cycles (sprints)
│   ├── current [team]      # Current cycle
│   ├── list [team]
│   ├── issues [cycle-id]   # Issues in cycle
│   └── next [team]         # Next upcoming cycle
│
├── comment                 # Comments
│   ├── list <issue-id>
│   ├── add <issue-id> <body>
│   ├── edit <comment-id>
│   └── delete <comment-id>
│
├── label                   # Labels
│   ├── list [team]
│   ├── create <name> [team]
│   └── delete <id>
│
├── workflow                # Workflow states
│   └── states [team]
│
├── search <query>          # Cross-entity search
│
├── config                  # CLI configuration
│   ├── get <key>
│   ├── set <key> <value>
│   ├── list
│   └── path                # Show config file path
│
├── completions             # Shell completion install
│   ├── bash
│   ├── zsh
│   └── fish
│
└── raw                     # Escape hatch: arbitrary GraphQL
    └── query <query-string>
```

---

## Output Formats

Every command supports `--output` / `-o`:

| Format | Flag | Use Case |
|--------|------|----------|
| `human` | default | Pretty tables, colors, emoji-free |
| `json` | `-o json` | Agents, scripts, pipelines |
| `plain` | `-o plain` | Tab-separated, no colors (legacy pipelines) |
| `yaml` | `-o yaml` | Human-readable configs, exports |
| `markdown` | `-o md` | Reports, docs, embedding in PRs |
| `ids` | `-o ids` | Just IDs, one per line (great for xargs) |

---

## Global Flags

```
--token TEXT            Override the HOJA_TOKEN env var
--config PATH           Use specific config file
--output, -o FORMAT     Output format (default: human)
--no-color              Disable ANSI colors
--quiet, -q             Suppress non-essential output
--verbose, -v           Show GraphQL queries and response timing
--dry-run               Print mutation that would be sent; don't execute
--timeout INT           HTTP timeout in seconds (default: 30)
--help, -h              Show help
--version               Print version and exit
```

There is no `--workspace` flag: Hoja is a single-organization product. The
`organization` table and every `organization_id` column stay (the change_event
triggers and the whole read scope depend on them); only the ability to create or
switch organization is gone. `HOJA_SINGLE_ORG=1` makes that explicit by refusing
the `workspaceCreate` and `workspaceSwitch` mutations and hiding the workspace
switcher in the UI.

## Departments

A department is a team with children (`team.parent_team_id`), at most three levels
deep. A department is **not** an access boundary: `team.visibility` is one of
`public` (any member), `restricted` (parent-team members may see and join) or
`private` (explicit members only), and a sub-team never inherits its parent's
access. Adding someone to a sub-team also adds them to every ancestor; removing
them from a parent while they still sit on a descendant is refused rather than
cascaded.

---

## Filter DSL (Issue list example)

the upstream contract's GraphQL has powerful filter inputs. We expose them ergonomically:

```bash
# Simple flags
hoja issue list --team ENG --state "In Progress" --assignee me

# Filter by relations
hoja issue list --project "Q2 Roadmap" --label Bug

# Date filters (ISO 8601 durations supported)
hoja issue list --updated "-P7D"          # Updated in last 7 days
hoja issue list --due-before "2026-06-01"

# Multiple states (OR)
hoja issue list --state "Todo,In Progress"

# Negation
hoja issue list --not-state "Done"

# Free-text within structured filter
hoja issue list --team ENG --contains "auth bug"

# Saved views (named filters in config)
hoja issue list --view "my-open-bugs"
```

---

## Config File

Location precedence:
1. `$HOJA_CONFIG` env var
2. `$XDG_CONFIG_HOME/hoja/config.toml`
3. `~/.config/hoja/config.toml`

```toml
# ~/.config/hoja/config.toml

[auth]
token_env = "HOJA_TOKEN"  # Read from env, never store plaintext
# Or for users who want it stored:
# token = "lin_api_..."

[defaults]
team = "ENG"                # Default team key
output = "human"            # Default output format
editor = "$EDITOR"          # For multi-line input

[display]
color = true
table_max_width = 120
truncate_descriptions = 80

[aliases]
mine = "issue list --assignee me --state-type !completed"
bugs = "issue list --label Bug --state-type started"
todo = "issue list --state Todo --assignee me"

[views]
"my-open-bugs" = { assignee = "me", label = "Bug", "state.type" = "!completed" }
"team-blockers" = { team = "ENG", priority = 1, "state.type" = "started" }
```

---

## User Stories

### Story 1: Quick triage (most common)
```bash
$ hoja me                              # confirm auth
$ hoja issue list --assignee me        # what's on my plate
$ hoja issue get ENG-123               # full detail
$ hoja issue state ENG-123 "In Progress"
$ hoja issue comment ENG-123 "Starting work on this"
```

### Story 2: Sprint planning
```bash
$ hoja cycle current ENG               # what's the current cycle
$ hoja cycle issues                    # issues in current cycle
$ hoja issue list --no-cycle --team ENG --state Todo | head -20
$ hoja issue update ENG-200 --cycle current
```

### Story 3: Bulk operations (agents/scripts)
```bash
# Find all stale bugs and assign to me
$ hoja issue list \
    --label Bug --state Todo --updated "-P30D" \
    -o ids | \
  xargs -I {} hoja issue update {} --assignee me

# Export to markdown for weekly report
$ hoja issue list --assignee me --updated "-P7D" \
    -o md > weekly-report.md
```

### Story 4: Project status check
```bash
$ hoja project list --state started
$ hoja project get "Q2 Roadmap"
$ hoja project issues "Q2 Roadmap" --state "!completed"
```

### Story 5: Agent integration
```bash
# Get JSON for an LLM to process
$ hoja issue get ENG-123 -o json

# Create from agent
$ hoja issue create \
    --team ENG \
    --title "Auth bug in login flow" \
    --description "$(cat error-report.md)" \
    --label Bug --priority 1 \
    -o json
```

### Story 6: Escape hatch
```bash
# Need a query hoja doesn't expose? Drop to raw GraphQL.
$ hoja raw query 'query { viewer { id name email } }'
```

---

## Error Handling

```
Exit codes:
  0   Success
  1   Generic error
  2   Usage error (bad flags)
  3   Auth error (missing/invalid token)
  4   Not found (entity doesn't exist)
  5   Validation error (Pydantic model failure)
  6   API error (the upstream API returned error)
  7   Network error (timeout, DNS, TLS)
  8   Rate limited
```

All errors go to stderr. Success output to stdout.

Error format (JSON mode):
```json
{
  "error": {
    "type": "NotFound",
    "message": "Issue ENG-9999 not found",
    "code": 4,
    "linear_error": null
  }
}
```

---

## Security

- **Token never logged.** Redacted in `--verbose` mode (shown as `lin_api_…XXXX`).
- **Token never stored in plaintext by default.** Read from env or via `--token`.
- **HTTPS only.** No fallback to HTTP.
- **TLS verification mandatory.** No `--insecure` flag.
- **Pinned dependencies.** `requirements.txt` includes hashes.
- **No telemetry.** Zero outbound calls except to the configured GraphQL
  endpoint (a `hoja-serve` instance, or any wire-compatible endpoint the
  account's `base_url` / `$HOJA_API_URL` points at).

---

## Project Structure

```
hoja/
├── pyproject.toml          # Modern Python project metadata
├── README.md               # Quick start
├── LICENSE                 # MIT
├── .gitignore
├── .python-version         # 3.10+
├── requirements.txt        # Hashed runtime deps
├── requirements-dev.txt    # Test/lint deps
│
├── hoja/                # Package root
│   ├── __init__.py         # Version, entry point
│   ├── __main__.py         # python -m hoja
│   ├── cli.py              # Typer app, global flags, root command
│   ├── config.py           # Config loading/validation
│   ├── client.py           # Async GraphQL client
│   ├── output.py           # Output formatters (human/json/md/yaml)
│   ├── errors.py           # Error hierarchy + exit codes
│   ├── auth.py             # Token resolution + viewer cache
│   ├── filters.py          # Filter DSL → the upstream API IssueFilter input
│   │
│   ├── commands/           # One file per command group
│   │   ├── __init__.py
│   │   ├── issue.py
│   │   ├── team.py
│   │   ├── project.py
│   │   ├── cycle.py
│   │   ├── comment.py
│   │   ├── label.py
│   │   ├── workflow.py
│   │   ├── search.py
│   │   ├── config_cmd.py   # 'config' is a builtin
│   │   ├── auth_cmd.py
│   │   └── raw.py
│   │
│   ├── models/             # Pydantic v2 models
│   │   ├── __init__.py
│   │   ├── base.py         # LinearModel, Connection[T], PageInfo
│   │   ├── user.py
│   │   ├── team.py
│   │   ├── issue.py
│   │   ├── project.py
│   │   ├── cycle.py
│   │   ├── comment.py
│   │   ├── workflow.py
│   │   ├── label.py
│   │   └── enums.py        # All the upstream API enums
│   │
│   └── graphql/            # Query/mutation string templates
│       ├── __init__.py
│       ├── fragments.py    # Shared GraphQL fragments
│       ├── queries.py      # Query strings
│       └── mutations.py    # Mutation strings
│
├── schema/
│   ├── graph-schema.json  # Full introspection result (gitignored if too large)
│   └── schema-summary.json # Categorized
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_client.py
│   ├── test_filters.py
│   ├── test_models.py
│   ├── test_output.py
│   └── fixtures/           # Recorded GraphQL responses
│
└── docs/
    ├── DESIGN.md           # This file
    ├── COMMANDS.md         # Generated reference
    ├── FILTERS.md          # Filter DSL reference
    └── EXAMPLES.md         # Recipe book
```

---

## v1 Acceptance Criteria

- [ ] `hoja me` returns viewer info
- [ ] `hoja team list` works
- [ ] `hoja issue list` with `--team`, `--state`, `--assignee`, `--label` filters
- [ ] `hoja issue get <id>` shows full issue detail
- [ ] `hoja issue create` with all common flags
- [ ] `hoja issue update <id>` for state/assignee/priority/labels
- [ ] `hoja cycle current` returns current cycle for a team
- [ ] `hoja project list` and `project get`
- [ ] Output formats: human, json, ids, plain
- [ ] `--dry-run` shows mutation without executing
- [ ] All commands validated against Pydantic models
- [ ] Errors return proper exit codes
- [ ] Test coverage > 80%
- [ ] Works on Python 3.10, 3.11, 3.12, 3.13
