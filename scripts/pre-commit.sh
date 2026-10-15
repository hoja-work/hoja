#!/usr/bin/env bash
# Cliniar pre-commit hook: hard-block secrets and forbidden file types.
#
# Refuses to commit if any staged file contains:
#   - Linear API token         (lin_api_...)
#   - PyPI API token           (pypi-...)
#   - AWS access keys          (AKIA...)
#   - GitHub PATs              (ghp_, ghs_, gho_, ghu_, ghr_)
#   - Google service account   ("type": "service_account")
#   - SSH/PGP private keys     (BEGIN ... PRIVATE KEY)
#   - Slack tokens             (xoxa-/xoxb-/xoxp-/xoxr-)
#
# Also blocks staged files:
#   - *.log
#   - *.env / .env.*
#   - schema/linear-schema.json   (2 MB, regenerable)
#   - anything under .secrets/ or vault/
#
# Bypass (use only for vetted false positives):
#   git commit --no-verify

set -u

red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
yellow() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
green()  { printf '\033[32m%s\033[0m\n' "$*" >&2; }

# Get list of staged files (added/copied/modified only)
staged=()
while IFS= read -r f; do
    staged+=("$f")
done < <(git diff --cached --name-only --diff-filter=ACM)

if [ ${#staged[@]} -eq 0 ]; then
    exit 0
fi

errors=0

# ----- 1. File-name based blocks -----
forbidden_paths_regex='(^|/)(\.env(\..*)?|.*\.log|schema/linear-schema\.json|\.secrets/.*|vault/.*)$'
for f in "${staged[@]}"; do
    if [[ "$f" =~ $forbidden_paths_regex ]]; then
        red "BLOCKED FILE TYPE: $f"
        yellow "  This file type is permanently blocked from this repo."
        yellow "  Add it to .gitignore. If you really need it, use --no-verify (audit first)."
        errors=$((errors + 1))
    fi
done

# ----- 2. Content scans (skip binary blobs) -----

# Pattern => human description
patterns=(
    'lin_api_[A-Za-z0-9]{20,}'
    'pypi-[A-Za-z0-9_-]{16,}'
    'AKIA[0-9A-Z]{16}'
    'ghp_[A-Za-z0-9]{30,}'
    'ghs_[A-Za-z0-9]{30,}'
    'gho_[A-Za-z0-9]{30,}'
    'ghu_[A-Za-z0-9]{30,}'
    'ghr_[A-Za-z0-9]{30,}'
    'xox[abpr]-[A-Za-z0-9-]{10,}'
    '"type":[[:space:]]*"service_account"'
    '-----BEGIN (RSA |OPENSSH |EC |PGP |DSA |ENCRYPTED )?PRIVATE KEY'
)
descriptions=('Linear API token' 'PyPI API token' 'AWS Access Key ID' 'GitHub personal access token' 'GitHub server-to-server token' 'GitHub OAuth token' 'GitHub user-to-server token' 'GitHub refresh token' 'Slack token' 'Google service account JSON' 'Private key block')

for f in "${staged[@]}"; do
    [ -f "$f" ] || continue
    # Skip files larger than 1 MB (likely binary / lock files)
    size=$(stat -c%s -- "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt 1048576 ]; then
        continue
    fi
    # Skip obviously-binary files
    if file -b --mime "$f" 2>/dev/null | grep -q 'charset=binary'; then
        continue
    fi
    # Skip the hook itself and its installer — they describe the patterns they block
    case "$f" in
        scripts/pre-commit.sh|scripts/install-hooks.sh|scripts/ship.sh) continue ;;
    esac
    for i in "${!patterns[@]}"; do
        if matches=$(grep -nE -- "${patterns[$i]}" "$f" 2>/dev/null); then
            red "SECRET DETECTED in $f"
            yellow "  Pattern: ${descriptions[$i]}"
            echo "$matches" | head -3 | sed 's/^/    /' >&2
            errors=$((errors + 1))
        fi
    done
done

if [ "$errors" -gt 0 ]; then
    echo "" >&2
    red "Commit refused: $errors issue(s) found."
    yellow "If you have rotated the credentials and the match is a real false positive,"
    yellow "you can bypass with: git commit --no-verify  (audit first!)"
    exit 1
fi

# Lint only staged frontend styles, including newly added files. Use the index
# content rather than the worktree so unstaged fixes cannot mask a bad commit.
styles=()
for f in "${staged[@]}"; do
    case "$f" in frontend/src/*.css) styles+=("$f") ;; esac
done
if [ "${#styles[@]}" -gt 0 ]; then
    root=$(git rev-parse --show-toplevel)
    if [ ! -x "$root/frontend/node_modules/.bin/stylelint" ]; then
        red "Stylelint is not installed. Run: cd frontend && npm ci"
        exit 1
    fi
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    for f in "${styles[@]}"; do
        mkdir -p "$tmp/$(dirname "$f")"
        git show ":$f" > "$tmp/$f" || exit 1
    done
    staged_styles=()
    for f in "${styles[@]}"; do staged_styles+=("$tmp/$f"); done
    if ! "$root/frontend/node_modules/.bin/stylelint" --config "$root/frontend/stylelint.config.mjs" "${staged_styles[@]}"; then
        red "Commit refused: staged UI CSS failed style lint. Run: cd frontend && npm run lint:styles"
        exit 1
    fi
fi

green "pre-commit OK (${#staged[@]} files scanned, no secrets / forbidden paths or style errors)"
exit 0
