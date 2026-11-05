# hoja — Workflow Patterns

Multi-step patterns that combine `hoja` commands. **Always chain via the shell.** Do not write Python wrappers — that's what the CLI exists to avoid.

---

## 1. Triage — claim unassigned issues

When a user says "triage Team ENG" or "clean up the inbox":

```bash
# Step 1 — list unassigned, recently created
hoja issue list --team ENG --no-assignee --state "Todo" -n 20

# Step 2 — for each issue you want to act on, get details
hoja issue get ENG-42

# Step 3 — assign + label + (optionally) move state
hoja issue assign ENG-42 me
hoja issue update ENG-42 --label "infra"
hoja issue state ENG-42 "In Progress"
```

**Behavioral rule:** during triage, never bulk-modify without showing the user the list first (with `-o human`). Confirm intent before mutations.

---

## 2. Daily standup — what am I working on?

```bash
hoja issue list --assignee me --state "In Progress" -n 20
hoja issue list --assignee me --state "In Review" -n 20
```

Render in markdown for the user:
```bash
hoja -o md issue list --assignee me --state "In Progress"
```

---

## 3. Create an issue from an error trace

When the user pastes a stack trace and says "log this":

```bash
# Step 1 — search first to avoid duplicates
hoja issue search "TypeError viewer" -n 5

# Step 2 — if no match, create
hoja issue create \
    --team ENG \
    --title "TypeError: viewer is undefined on /dashboard" \
    --description "$(cat error.log)" \
    --priority 2 \
    --label "bug"
```

**Behavioral rule:** ALWAYS search before create unless the user has explicitly said "create a new one even if duplicates exist."

---

## 4. Cycle health — what's at risk in the current sprint?

```bash
# Step 1 — what cycle are we in?
hoja cycle current ENG

# Step 2 — issues in current cycle, grouped by state
hoja -o json issue list --cycle current --team ENG | jq 'group_by(.state.name) | map({state: .[0].state.name, count: length, items: map(.identifier)})'

# Step 3 — blockers
hoja issue list --cycle current --team ENG --label "blocked"
```

---

## 5. Hand off an issue to another teammate

Three commands, in order, with one comment trail:

```bash
hoja issue assign ENG-35 "Bob"
hoja issue state ENG-35 "In Review"
hoja comment add ENG-35 "Handing off to @bob — context: see PR #1234"
```

**Behavioral rule:** always add a comment when handing off. A bare reassignment without context creates ambiguity downstream.

---

## 6. Bulk re-label (xargs pattern)

To re-label every issue carrying a deprecated label:

```bash
# Step 1 — preview the target set
hoja -o ids issue list --label "old-name" -n 100

# Step 2 — for each id, run the mutation
hoja -o ids issue list --label "old-name" -n 100 \
    | xargs -I{} hoja issue update {} --label "new-name"
```

`-o ids` emits one identifier per line, perfect for `xargs`.

---

## 7. Investigate one issue end-to-end

```bash
# Full payload
hoja -o json issue get ENG-35

# Comments
hoja comment list ENG-35

# Related issues by label
LABEL=$(hoja -o json issue get ENG-35 | jq -r '.labels[0].name')
hoja issue list --label "$LABEL" -n 10
```

---

## 8. Create issue and link to a PR

```bash
ISSUE=$(hoja -o json issue create --team ENG --title "Add retry to API client" --priority 3 | jq -r '.identifier')
echo "Created $ISSUE"
echo "Linear: $(hoja issue url $ISSUE)"
# Now reference $ISSUE in your PR title or commit message:
# hoja convention: include the identifier in the branch name (e.g. ENG-35-add-retry)
```

---

## 9. Weekly review — what shipped?

```bash
hoja -o md issue list --assignee me --state "Done" --updated-after $(date -d "7 days ago" +%Y-%m-%d) -n 50
```

---

## 10. Comment from CI output

In a CI script, post failure details to the related Linear issue:

```bash
ISSUE_ID=$(git log -1 --pretty=%B | grep -oE 'ENG-[0-9]+' | head -1)
if [ -n "$ISSUE_ID" ]; then
    pytest 2>&1 | tail -50 | hoja comment add "$ISSUE_ID"
fi
```

---

## Anti-patterns (do NOT do these)

| Don't | Why | Instead |
|---|---|---|
| Write a Python script calling the GraphQL API directly | hoja already handles auth/retries/pagination/validation | Use `hoja` in the shell |
| Loop in shell when filters would do the job | One API call vs N | Use `hoja issue list --filter ...` |
| `hoja issue list \| jq` to filter on team or state | Server-side filters are faster and produce smaller payloads | Use `--team`, `--state`, `--assignee` |
| Hardcode tokens in scripts | Leak risk | Use the `$HOJA_TOKEN` env var (deprecated `$LINEAR_TOKEN` still works), never `--token <real>` |
| Use `raw query` for things the wrapper supports | Inconsistent errors, no Pydantic validation | Use dedicated commands |
| `hoja issue delete` | Does not exist | Use state transition or archive workflow |
| Bulk mutate without `--dry-run` first when uncertain | One typo = many bad writes | Always preview with `--dry-run` |

## See also

- `commands.md` — verb-by-verb reference
- `filters.md` — `issue list` filter DSL
- `examples.md` — short copy-paste recipes
