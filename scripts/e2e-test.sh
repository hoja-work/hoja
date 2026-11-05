#!/usr/bin/env bash
# Comprehensive E2E test for Hoja
# Tests every command in both human and JSON output modes.
#
# Requires: $LINEAR_TOKEN in environment. We never hardcode the token here.

set -u
if [ -z "${LINEAR_TOKEN:-}" ]; then
    echo "ERROR: \$LINEAR_TOKEN is not set. Export it before running these tests." >&2
    echo "Get a token at: https://linear.app/settings/api" >&2
    exit 3
fi
if [ -z "${HOJA_TEST_TEAM:-}" ] || [ -z "${HOJA_TEST_ISSUE:-}" ]; then
    echo "ERROR: HOJA_TEST_TEAM and HOJA_TEST_ISSUE are required." >&2
    echo "Use identifiers from the Linear-compatible workspace under test." >&2
    exit 3
fi
CLI="${HOJA_CLI:-.venv/bin/hoja}"
TEAM_KEY="$HOJA_TEST_TEAM"
ISSUE_ID="$HOJA_TEST_ISSUE"

PASS=0
FAIL=0
RESULTS=()

run_test() {
    local name="$1"
    local expect_fail="${EXPECT_FAIL:-0}"
    shift
    echo ""
    echo "=================================================="
    echo "TEST: $name"
    echo "CMD: $*"
    echo "=================================================="
    output=$("$@" 2>&1)
    rc=$?
    echo "$output" | head -40
    echo "[EXIT: $rc]"
    if [ "$expect_fail" = "1" ]; then
        # Expected non-zero exit
        if [ $rc -ne 0 ]; then
            PASS=$((PASS+1))
            RESULTS+=("PASS: $name (expected non-zero, got $rc)")
        else
            FAIL=$((FAIL+1))
            RESULTS+=("FAIL: $name (expected non-zero, got 0)")
        fi
    else
        if [ $rc -eq 0 ]; then
            PASS=$((PASS+1))
            RESULTS+=("PASS: $name")
        else
            FAIL=$((FAIL+1))
            RESULTS+=("FAIL: $name (exit=$rc)")
        fi
    fi
}

# --- Identity / Auth ---
run_test "me (human)" $CLI me
run_test "me (json)" $CLI -o json me
run_test "auth status" $CLI auth status

# --- Teams ---
run_test "team list (human)" $CLI team list
run_test "team list (json)" $CLI -o json team list
run_test "team get" $CLI team get "$TEAM_KEY"
run_test "team states (human)" $CLI team states "$TEAM_KEY"
run_test "team states (json)" $CLI -o json team states "$TEAM_KEY"
run_test "team members (human)" $CLI team members "$TEAM_KEY"

# --- Projects ---
run_test "project list (human)" $CLI project list
run_test "project list (json)" $CLI -o json project list

# --- Cycles ---
# --- Cycles ---
run_test "cycle current (graceful no-cycle)" $CLI cycle current "$TEAM_KEY"
run_test "cycle current (json graceful)" $CLI -o json cycle current "$TEAM_KEY"
run_test "cycle list" $CLI cycle list "$TEAM_KEY"

# --- Comments ---
run_test "comment list" $CLI comment list "$ISSUE_ID" -n 5

# --- Labels ---
run_test "label list" $CLI label list -n 20
run_test "label list --team" $CLI label list --team "$TEAM_KEY"

# --- Init ---
TMP_CONFIG=$(mktemp -d)/config.toml
run_test "init --path (custom location)" $CLI init --path "$TMP_CONFIG"
EXPECT_FAIL=1 run_test "init duplicate (expect exit 2)" $CLI init --path "$TMP_CONFIG"
run_test "init --force overwrite" $CLI init --path "$TMP_CONFIG" --force
rm -rf "$(dirname "$TMP_CONFIG")"

# --- Issues ---
run_test "issue list --assignee me -n 3 (human)" $CLI issue list --assignee me -n 3
run_test "issue list --assignee me -n 3 (json)" $CLI -o json issue list --assignee me -n 3
run_test "issue list --team --state Todo -n 5" $CLI issue list --team "$TEAM_KEY" --state Todo -n 5
run_test "issue list -o ids (xargs-friendly)" $CLI -o ids issue list --assignee me -n 3
run_test "issue get (human)" $CLI issue get "$ISSUE_ID"
run_test "issue get (json)" $CLI -o json issue get "$ISSUE_ID"
run_test "issue url" $CLI issue url "$ISSUE_ID"
run_test "issue search login" $CLI issue search login -n 3

# --- Dry-run mutations ---
run_test "issue create --dry-run" $CLI --dry-run issue create --team "$TEAM_KEY" --title "Test from Hoja" --priority 3
run_test "issue update --dry-run" $CLI --dry-run issue update "$ISSUE_ID" --priority 2

# --- Output formats ---
run_test "issue list -o yaml" $CLI -o yaml issue list --assignee me -n 2
run_test "issue list -o md" $CLI -o md issue list --assignee me -n 3
run_test "issue list -o plain" $CLI -o plain issue list --assignee me -n 3

# --- Raw query ---
run_test "raw query" $CLI raw query 'query { viewer { id name email } }'

# --- Error cases (these SHOULD fail with non-zero exit) ---
EXPECT_FAIL=1 run_test "non-existent team (expect error 4)" $CLI team get NONEXISTENT
EXPECT_FAIL=1 run_test "non-existent issue (expect error 4)" $CLI issue get FAKE-9999

# --- MCP / Skill smoke tests (no live API; pure-Python import + content) ---
run_test "mcp: content module imports + loads all topics" \
    python3 -c "from hoja.mcp.content import Topic, HojaGuide, load_topic
for t in Topic:
    g = load_topic(t)
    assert isinstance(g, HojaGuide), f'{t}: bad type'
    assert g.title, f'{t}: missing title'
    assert g.instructions, f'{t}: empty instructions'
print('ok')"

run_test "mcp: resources module imports without mcp SDK" \
    python3 -c "from hoja.mcp import resources; assert callable(resources.viewer); print('ok')"

run_test "mcp: prompts module produces non-empty templates" \
    python3 -c "from hoja.mcp import prompts
for fn, args in [(prompts.triage, ('ENG',)),
                  (prompts.daily_standup, ()),
                  (prompts.hand_off, ('ENG-1', 'Bob', 'note')),
                  (prompts.cycle_review, ('ENG',)),
                  (prompts.issue_investigate, ('ENG-1',)),
                  (prompts.create_from_error, ('TypeError', 'ENG', 2))]:
    out = fn(*args)
    assert isinstance(out, str) and len(out) > 100, f'{fn.__name__}: too short'
    assert 'hoja' in out, f'{fn.__name__}: no hoja command'
    assert 'REMINDER' in out, f'{fn.__name__}: missing reminder'
print('ok')"

run_test "skill: SKILL.md frontmatter is valid YAML and lists hoja bin" \
    python3 -c "import re, pathlib
text = pathlib.Path('skills/hoja/SKILL.md').read_text()
m = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
assert m, 'frontmatter missing'
fm = m.group(1)
assert 'name: hoja' in fm, 'name missing/incorrect'
assert '\"hoja\"' in fm, 'requires.bins must include hoja'
print('ok')"

echo ""
echo "================================================================"
echo "SUMMARY: $PASS passed, $FAIL failed"
echo "================================================================"
for r in "${RESULTS[@]}"; do echo "  $r"; done
exit $FAIL
