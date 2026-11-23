#!/usr/bin/env bash
# Local development stack: Hoja backend on loopback and a Vite dev server that is
# loopback-only by default and shareable on a tailnet on request.
#
# Usage:
#   bash scripts/dev-local.sh              # loopback only
#   bash scripts/dev-local.sh --tailscale  # UI on this machine's 100.x address
#   HOJA_DEV_SNAPSHOT_ORG=hoja bash scripts/dev-local.sh --tailscale --snapshot
#     Copy one workspace's UI data from local production (read-only export).
#     Snapshot uses a separate SQLite database and a new dev-only API key.
#     Task text is private production content, NOT an anonymized fixture.
#
# Optional environment:
#   HOJA_DEV_BACKEND_PORT=8787   backend port (loopback only)
#   HOJA_DEV_PORT=5173           Vite port (loopback only)
#   HOJA_DEV_RESET=1             recreate the selected dev database (refresh snapshot)
#   HOJA_DEV_SNAPSHOT_ORG=hoja   required workspace URL key with --snapshot
#   HOJA_DEV_TAILSCALE=1         bind Vite to the Tailscale 100.x address
#   HOJA_TAILSCALE_IP=100.x.y.z  override Tailscale address detection
#   HOJA_DEV_HOST=100.x.y.z      bind Vite to an explicit address
#
# Runtime files are git-ignored: .hoja/dev/ for seeded dev, or
# .hoja/dev-snapshot/<workspace>/ for snapshots. Includes SQLite, token and logs.
#
# The stack is development-only by construction:
#   * the backend uses an explicit --db under .hoja/, never HOJA_DATABASE_URL;
#   * this script refuses to run with a production database or app URL configured;
#   * the backend always binds 127.0.0.1; only the Vite UI can be shared on a
#     tailnet, and only through the explicit --tailscale / HOJA_DEV_HOST opt-in;
#   * the frontend reaches the backend through Vite's dev proxy, which exists only
#     while `npm run dev` runs — `npm run build` output has no proxy and no dev log.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/.hoja/dev"
DB="$RUNTIME_DIR/dev.db"
TOKEN_FILE="$RUNTIME_DIR/token"
PY="$ROOT/.venv/bin/python"
BACKEND_PORT="${HOJA_DEV_BACKEND_PORT:-8787}"
FRONTEND_PORT="${HOJA_DEV_PORT:-5173}"
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

port_in_use() {
  (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null
}

# Where the browser UI listens. Loopback by default; a tailnet address only when
# asked for, so an accidental run never exposes the dev UI to the network.
BIND_HOST="${HOJA_DEV_HOST:-127.0.0.1}"

SNAPSHOT=0
for arg in "$@"; do
  case "$arg" in
    --tailscale|tailscale) HOJA_DEV_TAILSCALE=1 ;;
    --snapshot) SNAPSHOT=1 ;;
    -h|--help) sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; p; }' "$0"; exit 0 ;;
    *) fail "Unknown argument: $arg (use --tailscale and/or --snapshot)." ;;
  esac
done
if [[ "$SNAPSHOT" == "1" ]]; then
  SNAPSHOT_ORG="${HOJA_DEV_SNAPSHOT_ORG:-}"
  [[ "$SNAPSHOT_ORG" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    fail "Set HOJA_DEV_SNAPSHOT_ORG to a workspace URL key (for example hoja)."
  RUNTIME_DIR="$ROOT/.hoja/dev-snapshot/$SNAPSHOT_ORG"
  DB="$RUNTIME_DIR/dev.db"
  TOKEN_FILE="$RUNTIME_DIR/token"
fi

if [[ "${HOJA_DEV_TAILSCALE:-0}" == "1" && -z "${HOJA_DEV_HOST:-}" ]]; then
  TAILSCALE_IP="${HOJA_TAILSCALE_IP:-}"
  if [[ -z "$TAILSCALE_IP" ]]; then
    command -v tailscale >/dev/null 2>&1 ||
      fail "HOJA_DEV_TAILSCALE=1 needs the tailscale CLI, or set HOJA_TAILSCALE_IP."
    TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  fi
  [[ "$TAILSCALE_IP" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    fail "No Tailscale IPv4 (100.x) address found; is Tailscale connected?"
  BIND_HOST="$TAILSCALE_IP"
fi

FRONTEND_URL="http://$BIND_HOST:$FRONTEND_PORT"

# Development origins are loopback, or a Tailscale 100.64.0.0/10 address when the
# UI is shared on a tailnet. Anything else is refused before it can be adopted.
is_dev_origin() {
  local url="$1"
  [[ -z "$url" ]] && return 0
  [[ "$url" =~ ^https?://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]+)?/?$ ]] && return 0
  [[ "$url" =~ ^http://100\.[0-9]+\.[0-9]+\.[0-9]+(:[0-9]+)?/?$ ]] && return 0
  return 1
}

# --- Production safety -------------------------------------------------------
[[ "${NODE_ENV:-}" == "production" ]] &&
  fail "NODE_ENV=production is set; this script only starts a development stack."

if [[ -n "${HOJA_DATABASE_URL:-}" ]]; then
  fail "HOJA_DATABASE_URL is set (${HOJA_DATABASE_URL%%:*}…). Unset it in this shell so the dev stack can only touch $DB."
fi

is_dev_origin "${HOJA_APP_URL:-}" ||
  fail "HOJA_APP_URL=${HOJA_APP_URL} is not a development origin (127.0.0.1, localhost or 100.x). Unset it; the dev stack sets its own."

umask 077
mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
# Refuse before exporting/resetting anything if another stack is running.
port_in_use 127.0.0.1 "$BACKEND_PORT" && fail "Port $BACKEND_PORT is already in use; stop the existing dev stack first."
port_in_use "$BIND_HOST" "$FRONTEND_PORT" && fail "Port $FRONTEND_PORT is already in use; stop the existing dev stack first."

# --- Dependencies ------------------------------------------------------------
if [[ ! -x "$PY" ]] || ! "$PY" -c 'import ariadne, mcp, openai, sqlalchemy, uvicorn, hoja_server' >/dev/null 2>&1; then
  echo "Installing backend dependencies into .venv/ ..."
  [[ -x "$PY" ]] || python3 -m venv "$ROOT/.venv"
  # uv-created virtual environments intentionally omit pip. Prefer uv when
  # available; otherwise bootstrap pip into this venv (never the system Python).
  if command -v uv >/dev/null 2>&1; then
    (cd "$ROOT" && uv pip install --python "$PY" -q \
      -e './connectors/google_workspace' -e '.[server,mcp,agent]')
  else
    if ! "$PY" -m pip --version >/dev/null 2>&1; then
      "$PY" -m ensurepip --upgrade ||
        fail "The development venv has no pip. Install uv or enable Python ensurepip, then rerun."
    fi
    (cd "$ROOT" && "$PY" -m pip install -q \
      -e './connectors/google_workspace' -e '.[server,mcp,agent]')
  fi
fi

if [[ ! -x "$ROOT/frontend/node_modules/.bin/vite" ]]; then
  echo "Installing frontend dependencies ..."
  npm --prefix "$ROOT/frontend" ci
fi

# --- Database ----------------------------------------------------------------
if [[ "${HOJA_DEV_RESET:-0}" == "1" ]]; then
  rm -f "$DB" "$DB-wal" "$DB-shm" "$TOKEN_FILE"
  echo "Deleted the previous dev database."
fi

if [[ "$SNAPSHOT" == "1" && ! -f "$DB" ]]; then
  echo "Copying private UI data for workspace $SNAPSHOT_ORG (read-only production export) ..."
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" "$ROOT/scripts/dev_snapshot.py" \
    --db "$DB" --token-file "$TOKEN_FILE" || fail "Snapshot creation failed; no dev server started."
fi
if [[ ! -f "$DB" ]]; then
  DEV_TOKEN="hoja_dev_$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  printf '%s\n' "$DEV_TOKEN" > "$TOKEN_FILE"
  "$PY" -m hoja_server.cli seed --tenant dev --db "$DB" \
    --org "Hoja Dev" --org-key dev --team-key ENG --team-name Engineering \
    --user "Dev User" --email dev@hoja.local --token "$DEV_TOKEN" \
    >"$RUNTIME_DIR/seed.log" 2>&1 ||
    { cat "$RUNTIME_DIR/seed.log" >&2; fail "Seeding the dev database failed."; }
  echo "Seeded a fresh dev workspace at $DB"
fi

[[ -s "$TOKEN_FILE" ]] || fail "Dev token missing; use HOJA_DEV_RESET=1 to recreate this isolated database."
"$PY" -m hoja_server.cli migrate --tenant dev --db "$DB" >/dev/null
if [[ "$SNAPSHOT" == "1" ]]; then
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" "$ROOT/scripts/dev_snapshot.py" \
    --db "$DB" --repair-membership || fail "Local snapshot membership repair failed."
fi

# A snapshot must never inherit production mail, connector or durable-agent
# credentials from the shell. The backend gets only this explicit environment.
BACKEND_ENV=(env)
if [[ "$SNAPSHOT" == "1" ]]; then
  BACKEND_ENV=(env -i "PATH=$PATH" "HOME=$RUNTIME_DIR" "PYTHONPATH=$ROOT"
    HOJA_WEBHOOK_WORKER=0)
fi

# --- Processes ---------------------------------------------------------------
port_in_use 127.0.0.1 "$BACKEND_PORT" && fail "Port $BACKEND_PORT is already in use; set HOJA_DEV_BACKEND_PORT."
port_in_use "$BIND_HOST" "$FRONTEND_PORT" && fail "Port $FRONTEND_PORT is already in use; set HOJA_DEV_PORT."

BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  local code=$?
  trap - EXIT INT TERM
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

echo "Starting backend on $BACKEND_URL ..."
"${BACKEND_ENV[@]}" HOJA_APP_URL="$FRONTEND_URL" "$PY" -m hoja_server.cli serve \
  --tenant dev --db "$DB" --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning \
  >"$RUNTIME_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

for _ in $(seq 1 30); do
  curl -fsS --max-time 2 "$BACKEND_URL/ready" >/dev/null 2>&1 && break
  kill -0 "$BACKEND_PID" 2>/dev/null || break
  sleep 1
done
curl -fsS --max-time 2 "$BACKEND_URL/ready" >/dev/null 2>&1 ||
  { tail -n 30 "$RUNTIME_DIR/backend.log" >&2; fail "Backend did not become ready."; }

echo "Starting Vite on $FRONTEND_URL ..."
HOJA_DEV_BACKEND="$BACKEND_URL" HOJA_DEV_PORT="$FRONTEND_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --host "$BIND_HOST" --strictPort \
  >"$RUNTIME_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

proxied=""
for _ in $(seq 1 60); do
  # /onboarding/status is served by the backend; a 200 here proves the proxy works.
  if curl -fsS --max-time 2 "$FRONTEND_URL/onboarding/status" >/dev/null 2>&1; then
    proxied=1
    break
  fi
  kill -0 "$FRONTEND_PID" 2>/dev/null || break
  sleep 1
done

if [[ -n "$proxied" ]]; then
  echo
  echo "  Hoja dev is running"
  echo "    app      $FRONTEND_URL"
  echo "    backend  $BACKEND_URL (proxied through Vite; loopback only)"
  echo "    database $DB"
  echo "    API key  $(cat "$TOKEN_FILE")"
  echo
  if [[ "$BIND_HOST" != "127.0.0.1" ]]; then
    echo "  Reachable from other devices on this tailnet at $FRONTEND_URL."
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
      echo "  ufw is active; if the UI is unreachable, allow it once with:"
      echo "    sudo ufw allow in on tailscale0 to any port $FRONTEND_PORT proto tcp comment 'Hoja dev UI via Tailscale'"
    fi
    echo
  fi
  echo "  Sign in with the API key: on the login screen choose \"API key\" and paste it."
  if [[ "$SNAPSHOT" == "1" ]]; then
    echo "  PRIVATE SNAPSHOT: edits affect only this local copy, never production."
    echo "  Production login is not copied. Integrations, credentials and jobs are excluded."
    echo "  Task text remains private; only share with trusted tailnet users."
    echo "  HOJA_DEV_RESET=1 refreshes this snapshot from production on the next run."
  fi
  echo "  Ctrl-C stops both processes. HOJA_DEV_RESET=1 recreates the selected dev database."
  echo "  Logs: $RUNTIME_DIR/backend.log, $RUNTIME_DIR/frontend.log"
  echo
else
  tail -n 30 "$RUNTIME_DIR/frontend.log" >&2
  fail "Vite did not start, or its proxy could not reach the backend."
fi

wait "$FRONTEND_PID"
