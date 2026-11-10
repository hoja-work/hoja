# hoja — Overview

`hoja` is a type-safe command-line client for the upstream API (https://linear.app). Built on Pydantic v2 + httpx + Typer. Designed for humans, AI agents, CI pipelines, and shell scripts.

## When to reach for hoja

Use `hoja` via the **Bash tool** whenever a task touches the upstream API:

- Anything mentioning a the upstream API identifier like `ENG-35`, `ENG-123`, or `<TEAM>-<N>`.
- "Create / open / file / log / track an issue."
- "Assign / move / triage / comment on" an issue.
- "What's in the current cycle / sprint?"
- "Which the upstream API projects exist?"
- "Who is working on X?"
- Any request for status, backlog, label, or workflow state operations.

## Why CLI over raw API

- Auth, retries, rate-limit handling, pagination, and Pydantic validation are already implemented in `hoja`. You do not need to reproduce them.
- Mutation safety: `--dry-run` previews destructive operations.
- Composition: every command supports `--output {human|json|yaml|md|plain|ids}` so you can pipe to `jq`, `xargs`, or another shell command.
- Stability: the CLI's command surface is versioned and tested with 36+ end-to-end tests against the live API.

## Behavioral rules (read these before invoking)

1. **Verify auth first in a fresh shell:** `hoja me`. If it fails, stop and surface the auth error to the user before any mutation.
2. **Read the memory board before starting work.** Run `hoja memory remind` first to load forced rules and recent project context. If you learn something useful, add it with `hoja memory add --title "..." --body "..."`. Stale entries auto-heal after 30 days.
3. **Discover accounts before assuming defaults.** When working across multiple the upstream API organizations, run `hoja -o json auth accounts` to see available accounts and their org names. Use `--account <name>` for one-off switches or `hoja auth workspace` to verify auto-detection.
3. **Search before create.** `hoja issue search "<query>"` prevents duplicate issues. Always run this when the user says "create an issue about X" unless they already gave you an explicit identifier.
4. **Use `--output json` when piping or programmatically reading.** `--output human` is for terminal display only.
5. **the upstream API identifiers are case-sensitive.** Use `ENG-35`, never `eng-35`.
6. **Use `--dry-run` on mutations** when the user's intent is ambiguous or destructive.
7. **Prefer dedicated subcommands over `raw query`.** `raw query` is an escape hatch for unsupported GraphQL operations only.
8. **Chain via the shell, not Python.** For multi-step workflows (create + assign + comment), call `hoja` three times — do not write Python wrappers around the GraphQL API.
9. **Filter at the API, not in shell.** `hoja issue list --assignee me --state "In Progress"` is faster and cleaner than `hoja issue list | jq`.
10. **Never log or echo the token.** The CLI masks it automatically; do not print `$HOJA_TOKEN` in your shell output.
11. **`hoja issue delete` does NOT exist.** The wire-compatible API has no issue hard delete. Use a state transition (e.g., `cancel` or `archive` workflow state) instead.

## Command tree (one-liner reference)

```
hoja me                          # current user
hoja auth status                 # who am I authed as?
hoja auth accounts               # list all accounts
hoja auth add <NAME> --token <T>  # add an account
hoja auth switch <NAME>           # set default
hoja auth workspace              # show current workspace + mapped account
hoja init                        # write config file
hoja update                      # self-update from PyPI

hoja memory remind               # read memory board before work
hoja memory add --title "..." --body "..."  # add what you learned
hoja memory list                 # all entries
hoja memory heal                 # remove stale community entries

hoja team list
hoja team get <KEY>              # e.g. ENG
hoja team states <KEY>           # workflow states for a team
hoja team members <KEY>

hoja issue list [filters...]
hoja issue get <ID>              # e.g. ENG-35
hoja issue create --team <KEY> --title "..." [--description ...] [--priority N] [--assignee NAME] [--label "a,b"]
hoja issue update <ID> [--title ...] [--description ...] [--priority N]
hoja issue state <ID> "<STATE_NAME>"     # e.g. "In Progress"
hoja issue assign <ID> <USER_NAME>
hoja issue prio <ID> <N>                  # 0=none 1=urgent 2=high 3=med 4=low
hoja issue url <ID>
hoja issue search "<text>"

hoja project list
hoja project get <ID_OR_SLUG>

hoja cycle current <TEAM_KEY>
hoja cycle list <TEAM_KEY>

hoja comment list <ISSUE_ID> [-n N]
hoja comment add <ISSUE_ID> "..."                 # body positional; omit to read stdin
hoja comment edit <COMMENT_ID> "..."
hoja comment delete <COMMENT_ID>

hoja label list [--team <KEY>]
hoja label create --team <KEY> --name "..." [--color "#hex"]
hoja label delete <LABEL_ID>

hoja raw query "<GraphQL string>"          # escape hatch
```

## Global flags

- `--token <TOKEN>` — override env var. Use only when needed.
- `--account <NAME>` — one-off account override. Run `auth accounts` first to discover names.
- `--output, -o {human,json,yaml,md,plain,ids}` — output format.
- `--verbose, -v` — log GraphQL operations to stderr (token redacted).
- `--quiet, -q` — suppress non-essential output.
- `--no-color` — disable ANSI colors.
- `--dry-run` — print mutations without executing.
- `--timeout <SECONDS>` — HTTP timeout (default 30).
- `--version` — print version.

## Exit codes (scriptable)

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic error |
| 2 | Typer / usage error |
| 3 | AuthError — no token or rejected token |
| 4 | NotFoundError — issue/team/etc not found |
| 5 | NetworkError |
| 6 | InvalidInput — validation error |
| 7 | RateLimitError |
| 8 | APIError — GraphQL or server |

Use these in shell scripts: `hoja issue get ENG-9999 || [ $? -eq 4 ] && echo "not found"`.

## See also

- `commands.md` — full noun/verb breakdown with examples
- `workflows.md` — common multi-step patterns (triage, standup, hand-off)
- `filters.md` — `issue list` filter DSL
- `output-formats.md` — how to choose `--output`
- `examples.md` — copy-pasteable real-world recipes
