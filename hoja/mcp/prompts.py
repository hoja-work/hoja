"""MCP prompt templates.

Each prompt returns a multi-line markdown string. The MCP client renders this
as a user message that walks the agent through running `hoja` commands in
a specific order. Every prompt:

  1. Lists the exact shell commands to execute (in order).
  2. Says what to look for in each response.
  3. Includes the standard behavioral reminder at the end.

The prompts NEVER perform mutations themselves. They tell the agent which
`hoja` commands to call via the Bash tool.
"""
from __future__ import annotations

REMINDER = (
    "REMINDER: run each command above via the Bash tool. Do not write Python "
    "wrappers around Linear's GraphQL API — `hoja` is the supported "
    "interface. Use `--output json` when piping or programmatically reading."
)


def triage(team_key: str) -> str:
    """Walk the agent through triaging unassigned issues for a team."""
    return f"""# Triage workflow for team `{team_key}`

You are about to triage the unassigned backlog for team **{team_key}**.

## Step 1 — Scope the queue

Run:

```bash
hoja issue list --team {team_key} --no-assignee --state Todo -n 20
```

Read the list. Identify the top 3–5 issues that look highest-priority based on
title + label.

## Step 2 — Inspect each candidate

For each candidate ID:

```bash
hoja issue get <ID>
```

Skim the description and recent comments. Decide:

- Does it have enough context? If not, comment asking the reporter.
- Is the priority field obviously wrong? Note the correction.
- Is the right label set?

## Step 3 — Act

For each issue you've decided to take action on:

```bash
hoja issue assign <ID> <USER_NAME>   # or 'me'
hoja issue update <ID> --priority <N> --label "<labels>"
hoja issue state <ID> "<NEXT_STATE>"   # e.g. "In Progress"
```

## Step 4 — Document

If the triage decision needs a paper trail (e.g. reassigning across teams),
post a comment:

```bash
hoja comment add <ID> --body "Triaged: assigned to <name>, priority <N> because <reason>"
```

{REMINDER}
"""


def daily_standup() -> str:
    """Render a standup-shaped report of viewer's current work."""
    return f"""# Daily standup workflow

## Step 1 — What I shipped yesterday

```bash
hoja -o md issue list --assignee me --state Done \\
    --updated-after $(date -d "1 day ago" +%Y-%m-%d) -n 20
```

## Step 2 — What I'm working on today

```bash
hoja -o md issue list --assignee me --state "In Progress" -n 20
hoja -o md issue list --assignee me --state "In Review" -n 20
```

## Step 3 — Blockers

```bash
hoja -o md issue list --assignee me --label blocked -n 10
```

## Step 4 — Compose the standup

Combine the three sections above into:

```
## Yesterday
- <ENG-XX>: title
- ...

## Today
- <ENG-YY>: title
- ...

## Blockers
- <ENG-ZZ>: title — reason
```

{REMINDER}
"""


def create_from_error(error_text: str, team_key: str, priority: int = 3) -> str:
    """Templated issue creation from an error message / stack trace."""
    # Extract a plausible title — first line of the error, capped at ~80 chars
    first_line = (error_text or "").strip().splitlines()[:1]
    title_hint = (first_line[0][:80] if first_line else "Unhandled error").strip()
    return f"""# Create issue from error workflow

The user has provided an error trace and asked you to file an issue.

## Step 1 — Search for duplicates FIRST

```bash
hoja issue search "{title_hint}" -n 5
```

If a matching issue exists, do not create a new one — comment on the existing
issue instead:

```bash
hoja comment add <EXISTING_ID> --body "<<<DELIM
{error_text}
DELIM"
```

## Step 2 — If no duplicate, create

```bash
hoja issue create \\
    --team {team_key} \\
    --title "{title_hint}" \\
    --description "<<<DELIM
{error_text}
DELIM" \\
    --priority {priority} \\
    --label "bug"
```

## Step 3 — Capture the new identifier

The create response includes the new issue's identifier (e.g. `{team_key}-NN`).
Surface it to the user along with `hoja issue url <ID>` for a clickable link.

{REMINDER}
"""


def hand_off(issue_id: str, to_user: str, note: str = "") -> str:
    """Three-command issue hand-off pattern."""
    note_line = note or "context: see related PR / discussion / commit"
    return f"""# Hand-off workflow for {issue_id} → {to_user}

A clean hand-off is three commands. Run them in order:

## Step 1 — Reassign

```bash
hoja issue assign {issue_id} "{to_user}"
```

## Step 2 — Move to a review/blocked state

```bash
hoja issue state {issue_id} "In Review"
```

(Substitute the right state name. Use `hoja team states <TEAM_KEY>` if you
need to confirm available states for this team.)

## Step 3 — Document the hand-off

```bash
hoja comment add {issue_id} --body "Handing off to {to_user} — {note_line}"
```

**Behavioral rule:** never reassign without a comment explaining why and what
the receiver needs to know. A bare reassignment creates ambiguity downstream.

{REMINDER}
"""


def cycle_review(team_key: str) -> str:
    """Render a current-cycle review for a team."""
    return f"""# Cycle review workflow for team `{team_key}`

## Step 1 — Confirm there is an active cycle

```bash
hoja cycle current {team_key}
```

If the response indicates no active cycle, stop and tell the user — there is
nothing to review.

## Step 2 — Inventory the cycle by state

```bash
hoja -o json issue list --cycle current --team {team_key} \\
    | jq 'group_by(.state.name) | map({{state: .[0].state.name, count: length, issues: map(.identifier)}})'
```

## Step 3 — Flag blockers

```bash
hoja issue list --cycle current --team {team_key} --label blocked
```

## Step 4 — Flag stale issues (no update in 5+ days)

```bash
hoja issue list --cycle current --team {team_key} \\
    --updated-before $(date -d "5 days ago" +%Y-%m-%d)
```

## Step 5 — Report

Combine the above into a structured summary:

- **Done**: N issues
- **In Progress**: N issues
- **Todo (not started)**: N issues — risk if cycle ends soon
- **Blockers**: list with reasons
- **Stale**: list with last-update dates

{REMINDER}
"""


def issue_investigate(issue_id: str) -> str:
    """Deep dive on a single issue."""
    return f"""# Investigate {issue_id} — full context dump

## Step 1 — Read the issue

```bash
hoja -o json issue get {issue_id}
```

Capture: title, description, state, priority, assignee, labels, project, cycle.

## Step 2 — Read every comment

```bash
hoja comment list {issue_id} -n 100
```

## Step 3 — Find related issues (same labels)

```bash
LABEL=$(hoja -o json issue get {issue_id} | jq -r '.labels[0].name // empty')
[ -n "$LABEL" ] && hoja issue list --label "$LABEL" -n 10
```

## Step 4 — Find related issues (same project / cycle)

```bash
PROJECT=$(hoja -o json issue get {issue_id} | jq -r '.project.id // empty')
[ -n "$PROJECT" ] && hoja issue list --project "$PROJECT" -n 10

CYCLE=$(hoja -o json issue get {issue_id} | jq -r '.cycle.id // empty')
[ -n "$CYCLE" ] && hoja -o json issue list --cycle "$CYCLE" | jq '[.[] | .identifier]'
```

## Step 5 — Suggest next action

Based on state + labels + comments, suggest one of:
- comment with a clarifying question
- assign (with `hoja issue assign`)
- move state (with `hoja issue state`)
- close as duplicate (set state to `Canceled` and link via comment)
- prioritize (with `hoja issue prio`)

Do **not** mutate the issue yet — propose the action and wait for the user's
go-ahead.

{REMINDER}
"""
