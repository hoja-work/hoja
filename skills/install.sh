#!/usr/bin/env bash
# Install the Hoja agent skill into Swarm OS and/or Claude skill directories.
#
# Usage:
#   bash skills/install.sh [--swarm-only|--claude-only] [--copy]
#   bash skills/install.sh --uninstall
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/hoja"
MODE="symlink"
ACTION="install"
TARGETS=("swarm" "claude")

while [ "$#" -gt 0 ]; do
    case "$1" in
        --swarm-only) TARGETS=("swarm") ;;
        --claude-only) TARGETS=("claude") ;;
        --copy) MODE="copy" ;;
        --uninstall) ACTION="uninstall" ;;
        -h|--help)
            sed -n '2,/^set -e/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

skill_root() {
    case "$1" in
        swarm) printf '%s\n' "$HOME/.swarmos/skills" ;;
        claude) printf '%s\n' "$HOME/.claude/skills" ;;
    esac
}

remove_managed_link() {
    local path="$1"
    local expected="$2"
    if [ -L "$path" ]; then
        if [ "$(readlink "$path")" = "$expected" ]; then
            rm -- "$path"
        else
            echo "  refusing to remove unmanaged symlink: $path" >&2
            return 1
        fi
    elif [ -e "$path" ]; then
        echo "  refusing to remove unmanaged path: $path" >&2
        return 1
    fi
}

install_one() {
    local root canonical
    root="$(skill_root "$1")"
    canonical="$root/hoja"
    mkdir -p "$root"

    remove_managed_link "$canonical" "$SRC"
    if [ "$MODE" = "copy" ]; then
        if [ -e "$canonical" ]; then
            echo "  refusing to overwrite unmanaged path: $canonical" >&2
            return 1
        fi
        cp -R "$SRC" "$canonical"
        echo "  installed copy: $canonical"
    else
        ln -s "$SRC" "$canonical"
        echo "  installed symlink: $canonical"
    fi

}

uninstall_one() {
    local root canonical
    root="$(skill_root "$1")"
    canonical="$root/hoja"
    remove_managed_link "$canonical" "$SRC" || true
}

if [ ! -d "$SRC" ]; then
    echo "ERROR: source directory not found: $SRC" >&2
    exit 1
fi

if [ "$ACTION" = "install" ]; then
    echo "Installing Hoja skill (mode: $MODE)"
    for target in "${TARGETS[@]}"; do install_one "$target"; done
else
    echo "Uninstalling managed Hoja skill paths"
    for target in "${TARGETS[@]}"; do uninstall_one "$target"; done
fi
