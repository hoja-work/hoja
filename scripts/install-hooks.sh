#!/usr/bin/env bash
# Install the Hoja pre-commit hook into .git/hooks/pre-commit
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"
if [ ! -d "$ROOT/.git" ]; then
    echo "ERROR: $ROOT is not a git repository. Run 'git init' first." >&2
    exit 1
fi
cp "$ROOT/scripts/pre-commit.sh" "$HOOK"
chmod +x "$HOOK"
echo "Installed pre-commit hook at $HOOK"
echo "Test it with:  bash scripts/pre-commit.sh   (no staged files = exit 0)"
