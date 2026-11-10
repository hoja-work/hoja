# hoja — Commands Reference

Every `hoja` command follows `hoja <noun> <verb> [args] [flags]`. This is `gh`-style and consistent across all groups.

## Identity & Auth

### `hoja me`
Show the currently-authenticated viewer (you).
```bash
hoja me                          # human-friendly card
hoja -o json me                  # JSON for piping
```

### `hoja auth status`
Print which token source resolved (env var, config file, --token flag).
```bash
hoja auth status
```

### `hoja auth accounts`
List all configured accounts with default/workspace/current markers and org names.
```bash
hoja auth accounts               # human-readable list
hoja -o json auth accounts      # structured for agent reasoning
```

### `hoja auth add`
Add a named account. Optionally verifies the token against the upstream API to populate `org_name`. Use `--teams` to declare which team keys the account owns (enables automatic account selection) and `--default` to make it the global default.
```bash
hoja auth add work --token $LINEAR_WORK_TOKEN --teams "ENG,OPS" --default
hoja auth add personal --token $LINEAR_PERSONAL_TOKEN --token-env LINEAR_PERSONAL_TOKEN
```

### `hoja auth switch`
Set the global default account.
```bash
hoja auth switch work
```

### `hoja auth teams`
Set which team keys an account owns. When a command targets one of these teams
(via `--team SWA` or an identifier like `SWA-20`), that account — and its token —
is selected automatically, with no `--account` flag needed.
```bash
hoja auth teams work "ENG,OPS"     # set
hoja auth teams work ""            # clear
```

### `hoja auth remove`
Remove a named account.
```bash
hoja auth remove work
```

### `hoja auth workspace`
Show the current git repo workspace and which account is mapped to it.
```bash
hoja auth workspace
```

### `hoja init`
Generate a starter config at `~/.config/hoja/config.toml`.
```bash
hoja init                        # creates if missing
hoja init --force                # overwrite existing
hoja init --path /tmp/foo.toml   # custom location
```

## Teams

A the upstream API team has a `key` (e.g. `ENG`, `ENG`) used as the prefix in issue IDs.

```bash
hoja team list                          # all teams
hoja team get ENG                       # detail card
hoja team states ENG                    # workflow states (Todo, In Progress, Done, etc.)
hoja team members ENG                   # team membership
```

**Behavior:** before transitioning an issue's state with `hoja issue state`, run `hoja team states <KEY>` to learn the exact state name. State names are team-scoped and case-insensitive in lookup but stored as titled strings.

## Issues — list & read

```bash
# List filters (combine freely)
hoja issue list --assignee me
hoja issue list --team ENG --state "In Progress"
hoja issue list --team ENG --label bug --priority 1,2
hoja issue list --project "Q2 Roadmap"
hoja issue list --cycle current --team ENG
hoja issue list --contains "auth"            # title/description full-text
hoja issue list --created-after 2026-05-01
hoja issue list -n 20                         # limit

# Read one issue (returns title, description, state, assignee, labels, comments)
hoja issue get ENG-35
hoja -o json issue get ENG-35                 # full structured payload

# Get the URL only (useful for sharing in chat or commit messages)
hoja issue url ENG-35

# Full-text search across the workspace
hoja issue search "rate limit"
```

## Issues — create & mutate

```bash
# Create
hoja issue create --team ENG --title "Fix login flow" \
    --description "Token refresh fails on Safari" \
    --priority 2 --assignee "Alice" --label "bug,p2"

# Update fields
hoja issue update ENG-35 --title "Better title" --priority 1

# State transition (run `team states ENG` first to see valid names)
hoja issue state ENG-35 "In Review"

# Assign
hoja issue assign ENG-35 "Bob"
hoja issue assign ENG-35 me                   # special-case for viewer

# Priority shorthand (0=none 1=urgent 2=high 3=med 4=low)
hoja issue prio ENG-35 1
```

**Behavior:** Use `--dry-run` to preview the GraphQL payload without executing:
```bash
hoja --dry-run issue create --team ENG --title "Test" --priority 3
```

## Projects

```bash
hoja project list
hoja project get <ID_OR_SLUG>
```

`<ID_OR_SLUG>` is the project's UUID or its slug (the URL-safe short identifier visible in the upstream API web UI).

## Cycles (sprints)

```bash
hoja cycle current ENG            # active cycle for team ENG
hoja cycle list ENG               # all cycles
```

**Graceful empty:** if the team has no active cycle, `hoja cycle current` exits 0 with a friendly message in human mode and `{"active_cycle": null}` in JSON mode. **Do not treat this as an error.**

## Comments

```bash
hoja comment list ENG-35                  # all comments on an issue
hoja comment list ENG-35 -n 10            # limit

hoja comment add ENG-35 "Looks good"

# Or pipe from stdin
echo "Build broke at step 4" | hoja comment add ENG-35

# Or from a command's output
git log -1 --pretty=%B | hoja comment add ENG-35

hoja comment edit <COMMENT_ID> "Updated"
hoja comment delete <COMMENT_ID>
```

## Labels

```bash
hoja label list                           # workspace-wide
hoja label list --team ENG                # team-scoped

hoja label create --team ENG --name "infra" --color "#3b82f6"
hoja label delete <LABEL_ID>
```

Labels referenced by `hoja issue create --label "bug,p2"` are resolved by **name** per team. Run `hoja label list --team <KEY>` first to confirm spelling.

## Raw GraphQL escape hatch

For queries not yet wrapped by a dedicated command:

```bash
hoja raw query 'query { viewer { id name email teams { nodes { key } } } }'
hoja -o json raw query 'query { ... }'
```

**Behavior:** prefer the dedicated commands. `raw query` is for advanced or one-off needs only — it does not validate against Pydantic models and may surface inconsistent error shapes.

## Self-update

### `hoja update`
Check PyPI for a newer version and upgrade hoja (supports pip and pipx installations).

```bash
hoja update                     # check + interactive confirm
hoja update --dry-run           # show what would happen
hoja update --yes               # skip confirmation
hoja -o json update --dry-run  # structured output for agents
```

## Global flags (apply before any subcommand)

```bash
hoja --token <T> ...              # override env var
hoja --account <NAME> ...          # use a specific account (one-off override)
hoja -o json ...                   # output format
hoja -v ...                        # verbose to stderr
hoja --dry-run ...                 # preview mutations
hoja --timeout 60 ...              # HTTP timeout
hoja --no-color ...                # plain output
hoja --version                     # print version and exit
```

## See also

- `workflows.md` — multi-step patterns
- `filters.md` — `issue list` filter DSL deep dive
- `output-formats.md` — choosing `--output`
- `examples.md` — recipes
