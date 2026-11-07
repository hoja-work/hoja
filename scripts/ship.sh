#!/usr/bin/env bash
# Build and publish a Hoja release from an already-versioned, clean checkout.
# This script does not create repositories, commits, or deployment-specific
# configuration. Set HOJA_RELEASE_REPOSITORY only to override the current
# GitHub remote.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v$VERSION"
NOTES_FILE="$(mktemp "${TMPDIR:-/tmp}/hoja-release-notes.XXXXXX.md")"
trap 'rm -f "$NOTES_FILE"' EXIT

if [ -n "$(git status --porcelain)" ]; then
    echo "ABORT: release checkout is not clean" >&2
    exit 2
fi

echo "==> Auditing source for credentials"
P1="lin"; P1="${P1}_api_[A-Za-z0-9]{20,}"
P2="pypi"; P2="${P2}-Ag[A-Za-z0-9_-]{20,}"
LEAKS="$(grep -rIn --exclude-dir=.venv --exclude-dir=.git \
    --exclude-dir=schema --exclude-dir=dist --exclude=ship.sh \
    -E "${P1}|${P2}" . 2>/dev/null |
    grep -vE 'lin_api_\.\.\.|lin_api_…|lin_api_\\.\.\.|lin_api_XXX' || true)"
if [ -n "$LEAKS" ]; then
    printf 'ABORT: leaked credentials detected:\n%s\n' "$LEAKS" >&2
    exit 9
fi

echo "==> Running release gate"
bash scripts/pre-commit.sh

echo "==> Building Hoja $VERSION"
rm -rf dist build
uv build
# The PyPI distribution is `hoja-cli` (the `hoja` name on PyPI is unrelated
# software); artifact filenames normalize the hyphen to an underscore.
DIST_NAME="hoja_cli"
WHEEL="dist/${DIST_NAME}-${VERSION}-py3-none-any.whl"
SDIST="dist/${DIST_NAME}-${VERSION}.tar.gz"
test -f "$WHEEL"
test -f "$SDIST"

awk -v version="$VERSION" '
    $0 ~ "^## \\[" version "\\]" { found=1; next }
    found && /^## \[/ { exit }
    found { print }
' CHANGELOG.md > "$NOTES_FILE"
test -s "$NOTES_FILE"

echo "==> Creating GitHub release $TAG"
REPOSITORY_ARGS=()
if [ -n "${HOJA_RELEASE_REPOSITORY:-}" ]; then
    REPOSITORY_ARGS=(--repo "$HOJA_RELEASE_REPOSITORY")
fi
gh release create "$TAG" "${REPOSITORY_ARGS[@]}" \
    --title "Hoja $VERSION" \
    --notes-file "$NOTES_FILE" \
    "$WHEEL" "$SDIST"

# Accept either the uv-native variable or a plain PyPI API token name so the
# token can be injected from the vault without rewriting it.
if [ -z "${UV_PUBLISH_TOKEN:-}" ] && [ -n "${PYPI_API_TOKEN:-}" ]; then
    export UV_PUBLISH_TOKEN="$PYPI_API_TOKEN"
fi
if [ -z "${UV_PUBLISH_TOKEN:-}" ]; then
    echo "ABORT: UV_PUBLISH_TOKEN (or PYPI_API_TOKEN) is not set" >&2
    exit 3
fi
uv publish "$WHEEL" "$SDIST"

echo "Hoja $VERSION published. Verify with:"
echo "  uv tool install --refresh hoja-cli"
echo "  hoja --version"
