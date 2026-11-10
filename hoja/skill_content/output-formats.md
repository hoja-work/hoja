# hoja — Output Formats

Every command supports `--output / -o {human|json|yaml|md|plain|ids}`. Pick the right one for the job.

## Decision matrix

| Format | Use when | Example |
|---|---|---|
| `human` (default) | Showing results to a person in the terminal | `hoja issue list` |
| `json` | Piping to `jq`, parsing programmatically, archiving | `hoja -o json issue get ENG-35` |
| `yaml` | Human + machine readability tradeoff; configs and snapshots | `hoja -o yaml issue list` |
| `md` (markdown) | Pasting into PRs, docs, chat tools | `hoja -o md issue list -n 5` |
| `plain` | TSV — for spreadsheets and shell tools that don't like JSON | `hoja -o plain issue list` |
| `ids` | One identifier per line — feeds `xargs`, `for`, etc. | `hoja -o ids issue list --label bug \| xargs -I{} hoja issue url {}` |

## `human` — Rich tables

The default. Uses Rich (https://github.com/Textualize/rich) for color, wrapping, and table layouts.

- Auto-adapts to terminal width (configurable via `display.table_max_width` in `~/.config/hoja/config.toml`).
- Colors can be disabled with `--no-color`.
- **Never parse `human` output programmatically** — it's a presentation layer that may change visually between minor versions.

## `json` — canonical structured output

The canonical machine-readable contract.

- **Null fields are pruned recursively.** `{"avatar_url": null, "active": true}` becomes `{"active": true}`. This keeps payloads small and reduces context window noise for agents.
- Fields use the upstream contract's GraphQL camelCase (e.g. `displayName`, `priorityLabel`) — hoja models pass through alias names.
- Stable across minor versions; breaking schema changes require a major version bump.

```bash
hoja -o json issue get ENG-35 | jq '.title'
hoja -o json issue list --assignee me | jq -r '.[].identifier'
```

## `yaml` — block-style YAML

Indented block-style (never inline `{...}`). Good for snapshots and configs.

```bash
hoja -o yaml issue get ENG-35 > issue-ENG-35.yaml
```

## `md` (markdown) — paste-ready

Renders lists as bullet lists, single issues as headings + key/value, tables as GitHub-flavored markdown tables.

```bash
# Standup summary
hoja -o md issue list --assignee me --state "In Progress" > standup.md
```

## `plain` — TSV

Tab-separated values. Header row + data rows. Useful with `cut`, `awk`, `sort`, `column -t`.

```bash
hoja -o plain issue list --team ENG | column -t -s $'\t' | less
```

## `ids` — pipeline-friendly

One the upstream API identifier per line. **The right tool for shell pipelines.**

```bash
# Re-label every issue with the old label
hoja -o ids issue list --label "old" -n 100 \
    | xargs -I{} hoja issue update {} --label "new"

# Print URLs for every high-priority unassigned issue
hoja -o ids issue list --priority 1,2 --no-assignee \
    | while read id; do hoja issue url "$id"; done
```

## Behavioral rule

**Agents should default to `--output json` for any task that parses, filters, or transforms output.** Save `--output human` for the final user-facing summary.

```bash
# Wrong — fragile and slow
hoja issue list --assignee me | grep "In Progress" | awk '{...}'

# Right
hoja -o json issue list --assignee me --state "In Progress" | jq '...'
```

## See also

- `commands.md` — full command reference
- `filters.md` — server-side filtering pairs with `-o ids` for fast pipelines
- `examples.md` — output-format recipes
