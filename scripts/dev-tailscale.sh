#!/usr/bin/env bash
# Run Hoja's development UI on this machine's Tailscale IP.
#
# Usage:
#   scripts/dev-tailscale.sh [start]   # idempotent: reuses healthy processes
#   scripts/dev-tailscale.sh restart
#   scripts/dev-tailscale.sh stop
#   scripts/dev-tailscale.sh status
#
# Optional environment:
#   HOJA_TENANT=local                  SQLite tenant name
#   HOJA_BACKEND_PORT=8787             loopback-only backend port
#   HOJA_FRONTEND_PORT=5173            Tailscale-only Vite port
#   HOJA_TAILSCALE_IP=100.x.x.x        override automatic Tailscale detection
#   HOJA_SKIP_FIREWALL=1               do not add the UFW tailscale0 rule
#
# The script adds a UFW rule only for the frontend port on tailscale0. It does
# not expose the backend or configure Tailscale Funnel/public internet access.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/.hoja/dev"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
TENANT="${HOJA_TENANT:-local}"
BACKEND_PORT="${HOJA_BACKEND_PORT:-8787}"
FRONTEND_PORT="${HOJA_FRONTEND_PORT:-5173}"
ACTION="${1:-start}"

mkdir -p "$RUNTIME_DIR"

usage() {
  echo "Usage: $0 [start|stop|restart|status]" >&2
}

pid_from_file() {
  local file="$1" pid
  [[ -f "$file" ]] || return 1
  read -r pid < "$file" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

managed_running() {
  local name="$1" file="$2" pid args
  pid="$(pid_from_file "$file")" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  args="$(ps -p "$pid" -o args= 2>/dev/null)" || return 1
  case "$name" in
    backend) [[ "$args" == *"hoja_server.cli"* || "$args" == *"hoja-serve"* ]] ;;
    frontend) [[ "$args" == *"vite"* ]] ;;
    *) return 1 ;;
  esac
}

url_ready() {
  curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

wait_ready() {
  local name="$1" url="$2" pid_file="$3"
  local deadline=$((SECONDS + 45))
  while (( SECONDS < deadline )); do
    if url_ready "$url"; then
      return 0
    fi
    if [[ -n "$pid_file" ]] && ! managed_running "$name" "$pid_file"; then
      return 1
    fi
    sleep 1
  done
  return 1
}

show_log_error() {
  local label="$1" log="$2"
  echo "ERROR: $label did not become ready. Recent log output:" >&2
  if [[ -f "$log" ]]; then
    tail -n 40 "$log" >&2
  else
    echo "  (no log file at $log)" >&2
  fi
}

stop_one() {
  local name="$1" file="$2" pid pgid i
  if ! managed_running "$name" "$file"; then
    rm -f "$file"
    echo "$name: no script-managed process running"
    return 0
  fi

  pid="$(pid_from_file "$file")"
  pgid="$(ps -p "$pid" -o pgid= 2>/dev/null | tr -d ' ' || true)"
  if [[ "$pgid" =~ ^[0-9]+$ ]] && [[ "$pgid" == "$pid" ]] && [[ "$pgid" != "$$" ]]; then
    kill -TERM -- "-$pgid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi

  for i in {1..10}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    if [[ "$pgid" =~ ^[0-9]+$ ]] && [[ "$pgid" == "$pid" ]] && [[ "$pgid" != "$$" ]]; then
      kill -KILL -- "-$pgid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$file"
  echo "$name: stopped"
}

stop_all() {
  stop_one frontend "$FRONTEND_PID_FILE"
  stop_one backend "$BACKEND_PID_FILE"
}

resolve_tailscale_ip() {
  if [[ -n "${HOJA_TAILSCALE_IP:-}" ]]; then
    TAILSCALE_IP="$HOJA_TAILSCALE_IP"
  else
    if ! command -v tailscale >/dev/null 2>&1; then
      echo "ERROR: tailscale is not installed or is not on PATH." >&2
      exit 1
    fi
    TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  fi
  if [[ ! "$TAILSCALE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: could not determine a Tailscale IPv4 address. Is Tailscale connected?" >&2
    exit 1
  fi
  FRONTEND_URL="http://$TAILSCALE_IP:$FRONTEND_PORT"
  BACKEND_READY_URL="http://127.0.0.1:$BACKEND_PORT/ready"
}

ensure_backend_dependencies() {
  if command -v uv >/dev/null 2>&1; then
    (cd "$ROOT" && uv sync --extra dev --extra server --extra mcp --extra agent)
  elif [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "Creating the local Python environment at .venv/ ..."
    (cd "$ROOT" && python3 -m venv .venv)
  fi

  if ! "$ROOT/.venv/bin/python" -c 'import ariadne, mcp, openai, sqlalchemy, starlette, uvicorn' >/dev/null 2>&1; then
    echo "Installing Hoja's backend dependencies into .venv/ ..."
    (
      cd "$ROOT"
      .venv/bin/python -m pip install \
        -e './connectors/google_workspace' -e '.[server,mcp,agent]'
    )
  fi
}

ensure_frontend_dependencies() {
  if [[ ! -x "$ROOT/frontend/node_modules/.bin/vite" ]]; then
    echo "Installing frontend dependencies ..."
    npm --prefix "$ROOT/frontend" ci
  fi
}

configure_firewall() {
  if [[ "${HOJA_SKIP_FIREWALL:-0}" == "1" ]]; then
    echo "Firewall: skipped (HOJA_SKIP_FIREWALL=1)"
    return 0
  fi
  if ! command -v ufw >/dev/null 2>&1; then
    echo "ERROR: ufw is unavailable. Install it or set HOJA_SKIP_FIREWALL=1." >&2
    exit 1
  fi

  # Do not run the whole script through sudo: .venv and runtime files should
  # remain owned by the developer. UFW itself skips an identical existing rule.
  local rule=(allow in on tailscale0 to any port "$FRONTEND_PORT" proto tcp comment "Hoja dev UI via Tailscale")
  if (( EUID == 0 )); then
    ufw "${rule[@]}"
  else
    sudo ufw "${rule[@]}"
  fi
}

start_backend() {
  if url_ready "$BACKEND_READY_URL"; then
    echo "Backend: already ready on 127.0.0.1:$BACKEND_PORT"
    return 0
  fi
  if managed_running backend "$BACKEND_PID_FILE"; then
    echo "Backend: waiting for its existing process ..."
    if wait_ready backend "$BACKEND_READY_URL" "$BACKEND_PID_FILE"; then
      echo "Backend: ready"
      return 0
    fi
    show_log_error "Backend" "$BACKEND_LOG"
    exit 1
  fi
  rm -f "$BACKEND_PID_FILE"

  ensure_backend_dependencies
  echo "Applying any outstanding migrations for tenant '$TENANT' ..."
  (cd "$ROOT" && .venv/bin/python -m hoja_server.cli migrate --tenant "$TENANT")

  : >> "$BACKEND_LOG"
  (
    cd "$ROOT"
    export HOJA_APP_URL="$FRONTEND_URL"
    nohup setsid .venv/bin/python -m hoja_server.cli serve \
      --tenant "$TENANT" --host 127.0.0.1 --port "$BACKEND_PORT" \
      --log-level info >> "$BACKEND_LOG" 2>&1 < /dev/null 9>&- &
    echo $! > "$BACKEND_PID_FILE"
  )
  echo "Backend: started (PID $(pid_from_file "$BACKEND_PID_FILE")); waiting for readiness ..."
  if ! wait_ready backend "$BACKEND_READY_URL" "$BACKEND_PID_FILE"; then
    show_log_error "Backend" "$BACKEND_LOG"
    exit 1
  fi
  echo "Backend: ready at http://127.0.0.1:$BACKEND_PORT/graphql"
}

start_frontend() {
  if url_ready "$FRONTEND_URL/"; then
    echo "Frontend: already ready at $FRONTEND_URL"
    return 0
  fi
  if managed_running frontend "$FRONTEND_PID_FILE"; then
    echo "Frontend: waiting for its existing process ..."
    if wait_ready frontend "$FRONTEND_URL/" "$FRONTEND_PID_FILE"; then
      echo "Frontend: ready"
      return 0
    fi
    show_log_error "Frontend" "$FRONTEND_LOG"
    exit 1
  fi
  rm -f "$FRONTEND_PID_FILE"

  ensure_frontend_dependencies
  : >> "$FRONTEND_LOG"
  (
    cd "$ROOT"
    nohup setsid frontend/node_modules/.bin/vite frontend \
      --host "$TAILSCALE_IP" --port "$FRONTEND_PORT" --strictPort \
      >> "$FRONTEND_LOG" 2>&1 < /dev/null 9>&- &
    echo $! > "$FRONTEND_PID_FILE"
  )
  echo "Frontend: started (PID $(pid_from_file "$FRONTEND_PID_FILE")); waiting for readiness ..."
  if ! wait_ready frontend "$FRONTEND_URL/" "$FRONTEND_PID_FILE"; then
    show_log_error "Frontend" "$FRONTEND_LOG"
    exit 1
  fi
  echo "Frontend: ready"
}

status_one() {
  local name="$1" file="$2" url="$3" pid
  if managed_running "$name" "$file"; then
    pid="$(pid_from_file "$file")"
    if url_ready "$url"; then
      echo "$name: running (PID $pid), ready at $url"
    else
      echo "$name: running (PID $pid), but not ready at $url"
    fi
  elif url_ready "$url"; then
    echo "$name: ready at $url (not started by this script)"
  else
    echo "$name: stopped"
  fi
}

start_all() {
  resolve_tailscale_ip
  # Existing frontend proxy targets default to 8787. If overriding the backend
  # port, set Vite's backend target through the normal config as well.
  if [[ "$BACKEND_PORT" != "8787" ]]; then
    echo "ERROR: custom backend ports require updating frontend/vite.config.ts too." >&2
    exit 1
  fi
  configure_firewall
  start_backend
  start_frontend
  echo
  echo "Open from another Tailscale device:"
  echo "  $FRONTEND_URL"
  echo
  echo "Generated Hoja links use this UI origin through HOJA_APP_URL."
  echo "Logs: $BACKEND_LOG"
  echo "      $FRONTEND_LOG"
}

case "$ACTION" in
  start)
    # Serialize lifecycle operations so two invocations cannot start duplicates.
    if command -v flock >/dev/null 2>&1; then
      exec 9>"$RUNTIME_DIR/lifecycle.lock"
      flock 9
    fi
    start_all
    ;;
  restart)
    if command -v flock >/dev/null 2>&1; then
      exec 9>"$RUNTIME_DIR/lifecycle.lock"
      flock 9
    fi
    stop_all
    start_all
    ;;
  stop)
    if command -v flock >/dev/null 2>&1; then
      exec 9>"$RUNTIME_DIR/lifecycle.lock"
      flock 9
    fi
    stop_all
    ;;
  status)
    resolve_tailscale_ip
    status_one backend "$BACKEND_PID_FILE" "$BACKEND_READY_URL"
    status_one frontend "$FRONTEND_PID_FILE" "$FRONTEND_URL/"
    ;;
  *)
    usage
    exit 2
    ;;
esac
