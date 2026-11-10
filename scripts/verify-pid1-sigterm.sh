#!/usr/bin/env bash
# Prove that a container command lets SIGTERM reach hoja-serve.
#
# The runtime sends SIGTERM to PID 1 and, after its grace period (docker: 10s),
# SIGKILL. A shell running `cmd1 && cmd2` as PID 1 does not forward that signal,
# so every stop waits the full grace period and ends in SIGKILL. This script
# reproduces that: it runs the image's real CMD inside a fresh PID namespace (so
# the command genuinely is PID 1), stops it the way an orchestrator would, and
# reports the wall time and exit status.
#
# Every Dockerfile in the repo that runs `hoja-serve serve` is checked, so a
# second image cannot silently keep the bug.
#
# Usage: scripts/verify-pid1-sigterm.sh [Dockerfile ...]
# Env:   HOJA_PROBE_PORT (default 8787)  HOJA_PROBE_TIMEOUT (default 10)

set -uo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

if [ "${1:-}" = "--one" ]; then
    shift
    dockerfile=$1
    port=${HOJA_PROBE_PORT:-8787}
    grace=${HOJA_PROBE_TIMEOUT:-10}
    work=$(mktemp -d)
    db="$work/probe.db"
    log="$work/container.log"
    cleanup() {
        # Kill the namespace init too: killing only the unshare parent leaves the
        # container running, which is the very failure this script demonstrates.
        [ -n "${pid1:-}" ] && kill -KILL "$pid1" 2>/dev/null
        [ -n "${init:-}" ] && kill -KILL "$init" 2>/dev/null
        rm -rf "$work"
    }
    trap cleanup EXIT

    cmd_json=$(grep -m1 '^CMD ' "$dockerfile" | sed 's/^CMD[[:space:]]*//')
    if [ -z "$cmd_json" ]; then
        echo "FAIL: no CMD line in $dockerfile" >&2
        exit 1
    fi
    mapfile -d '' argv < <(python3 -c 'import json,sys; [sys.stdout.write(a+"\0") for a in json.loads(sys.argv[1])]' "$cmd_json")
    if [ "${#argv[@]}" -eq 0 ]; then
        echo "FAIL: CMD in $dockerfile is not a JSON array" >&2
        exit 1
    fi
    # Keep the CMD verbatim apart from the probe port, so this measures the
    # image's real command and not a rewritten one.
    for i in "${!argv[@]}"; do argv[$i]=${argv[$i]//--port 8787/--port $port}; done

    export HOJA_DATABASE_URL="sqlite:///$db"
    [ -d "$root/web" ] && export HOJA_WEB_ROOT="$root/web"
    export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"

    # A leftover server would answer /health for us and make this measure nothing.
    if curl -fsS -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
        echo "FAIL: something already answers on port $port; set HOJA_PROBE_PORT" >&2
        exit 1
    fi

    echo "CMD: ${argv[*]}"
    unshare -Urpf --mount-proc "${argv[@]}" >"$log" 2>&1 &
    init=$!

    ready=0
    for _ in $(seq 1 100); do
        if curl -fsS -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then ready=1; break; fi
        kill -0 "$init" 2>/dev/null || break
        sleep 0.2
    done
    if [ "$ready" -ne 1 ]; then
        echo "FAIL: the server never became ready; log follows" >&2
        sed 's/^/    /' "$log" >&2
        exit 1
    fi

    # unshare forks: the *child* is PID 1 inside the namespace, and that is the
    # process the runtime would signal. Its outer PID is the child of unshare.
    pid1=""
    for _ in $(seq 1 50); do
        pid1=$(pgrep -P "$init" | head -1)
        [ -n "$pid1" ] && break
        sleep 0.1
    done
    if [ -z "$pid1" ]; then
        echo "FAIL: could not find the namespace init" >&2
        exit 1
    fi
    echo "PID 1:  $(tr '\0' ' ' < "/proc/$pid1/cmdline" | sed 's/ *$//')"

    # serve does not migrate, so a schema present once /health answers proves the
    # migrate step ran first.
    if python3 -c 'import sqlite3,sys; n={r[0] for r in sqlite3.connect(sys.argv[1]).execute("select name from sqlite_master where type=\"table\"")}; sys.exit(0 if "schema_revision" in n else 1)' "$db"; then
        echo "migrate: ran before serve"
    else
        echo "FAIL: the schema is empty, migrate did not run first" >&2
        exit 1
    fi

    start=$(date +%s%N)
    kill -TERM "$pid1" 2>/dev/null
    for _ in $(seq 1 $((grace * 10))); do
        kill -0 "$pid1" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$pid1" 2>/dev/null; then
        kill -KILL "$pid1" 2>/dev/null
        wait "$init" 2>/dev/null
        echo "FAIL: still running after ${grace}s, SIGKILL sent (exit 137)" >&2
        echo "      PID 1 is a shell that does not forward SIGTERM; hoja-serve never saw it." >&2
        exit 1
    fi
    wait "$init" 2>/dev/null
    code=$?
    elapsed=$(awk -v a="$start" -v b="$(date +%s%N)" 'BEGIN { printf "%.2f", (b-a)/1000000000 }')

    if [ "$code" -eq 137 ]; then
        echo "FAIL: exit 137 (SIGKILL) after ${elapsed}s" >&2
        exit 1
    fi
    if awk -v t="$elapsed" 'BEGIN { exit !(t < 2.0) }'; then
        echo "SIGTERM: exited in ${elapsed}s with code ${code} (< 2s)"
        exit 0
    fi
    echo "FAIL: took ${elapsed}s to exit (>= 2s, code ${code})" >&2
    sed 's/^/    /' "$log" | tail -20 >&2
    exit 1
fi

repo=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || echo "$root/..")
if [ "$#" -gt 0 ]; then
    targets=("$@")
else
    mapfile -t targets < <(find "$repo" -maxdepth 3 -name 'Dockerfile*' -type f \
        -not -path '*/node_modules/*' 2>/dev/null \
        | sort | while read -r f; do grep -q 'hoja-serve serve' "$f" 2>/dev/null && echo "$f"; done)
fi
if [ "${#targets[@]}" -eq 0 ]; then
    echo "FAIL: no Dockerfile running 'hoja-serve serve' found" >&2
    exit 1
fi

failures=0
for dockerfile in "${targets[@]}"; do
    echo "==> ${dockerfile#"$repo/"}"
    if ! bash "$self" --one "$dockerfile"; then failures=$((failures + 1)); fi
done
if [ "$failures" -ne 0 ]; then
    echo "FAIL: $failures image command(s) do not hand SIGTERM to hoja-serve" >&2
    exit 1
fi
echo "OK: every image command hands SIGTERM to hoja-serve and exits in < 2s"
