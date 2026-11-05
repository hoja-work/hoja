#!/usr/bin/env bash
# E2E proof: Hoja CLI driven against the local hoja_server backend.
# Boots a seeded SQLite tenant in a tmux-supervised server, then runs the
# real Hoja CLI (via `python3 -m hoja` from repo source) against it.
#
# Usage: bash scripts/e2e-local-backend.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PYTHON="${HOJA_PYTHON:-python3}"

PORT="${PORT:-8796}"
DB="/tmp/hoja_e2e_$$.db"
TOK="hoja_test_token_e2e_$$"
CFG="/tmp/hoja_e2e_$$.toml"
ENVFILE="/tmp/hoja_e2e_$$.env"
SESS="hoja_e2e_$$"
URL="http://127.0.0.1:${PORT}/graphql"
PASS=0; FAIL=0

ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if echo "$2" | grep -q "$3"; then ok "$1"; else bad "$1 (got: $(echo "$2"|head -1))"; fi; }

cleanup(){ tmux kill-session -t "$SESS" 2>/dev/null; rm -f "$DB"* "$CFG" "$ENVFILE"; }
trap cleanup EXIT

: > "$ENVFILE"
chmod 600 "$ENVFILE"
if [ -n "${HOJA_DATABASE_URL:-}" ]; then
  printf 'export HOJA_DATABASE_URL=%q\n' "$HOJA_DATABASE_URL" > "$ENVFILE"
fi

echo "== seeding tenant =="
"$PYTHON" -m hoja_server.cli seed --db "$DB" --org-key "e2e-$$" --token "$TOK" --no-demo >/dev/null

echo "== starting server in tmux =="
tmux kill-session -t "$SESS" 2>/dev/null
tmux new-session -d -s "$SESS" "cd '$ROOT' && . '$ENVFILE' && exec '$PYTHON' -m hoja_server.cli serve --db '$DB' --port $PORT --log-level warning > /tmp/${SESS}.log 2>&1"
for i in $(seq 1 20); do curl -sf "http://127.0.0.1:${PORT}/ready" >/dev/null 2>&1 && break; sleep 1; done
curl -sf "http://127.0.0.1:${PORT}/ready" >/dev/null 2>&1 || { echo "server failed readiness"; cat /tmp/${SESS}.log; exit 1; }

cat > "$CFG" <<TOML
[accounts.local]
base_url = "$URL"
token = "$TOK"
[defaults]
default_account = "local"
TOML
export HOJA_CONFIG="$CFG"
C="$PYTHON -m hoja --account local"

echo "== running CLI matrix =="
check "me"              "$($C -o json me 2>&1)"                       '"email"'
check "team list"       "$($C team list 2>&1)"                       'ENG'
check "team states"     "$($C team states ENG 2>&1)"                  'In Progress'
check "issue create 1"  "$($C issue create --team ENG --title 'First' 2>&1)"  'ENG-1'
check "issue create 2"  "$($C issue create --team ENG --title 'Second' --priority 1 2>&1)" 'ENG-2'
check "counter incr"    "$($C -o ids issue list 2>&1)"               'ENG-2'
check "issue get"       "$($C -o json issue get ENG-1 2>&1)"          '"identifier"'
check "state change"    "$($C issue state ENG-1 'In Progress' 2>&1)"  'Updated'
check "assign me"       "$($C issue assign ENG-2 me 2>&1)"            'Updated'
check "priority set"    "$($C issue prio ENG-1 2 2>&1)"               'Updated'
check "comment add"     "$($C comment add ENG-1 'hello from e2e' 2>&1)" 'Comment added'
check "filter state"    "$($C -o ids issue list --state 'In Progress' 2>&1)" 'ENG-1'
check "filter priority" "$($C -o ids issue list --priority 1 2>&1)"   'ENG-2'
check "search"          "$($C -o ids issue search Second 2>&1)"       'ENG-2'
check "label create"    "$($C label create bug -t ENG 2>&1)"          'bug'
check "label list"      "$($C label list 2>&1)"                       'bug'
check "bad token 401"   "$($C --token bogus_xxx me 2>&1)"             '401'

echo
echo "SUMMARY: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
