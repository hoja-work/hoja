#!/usr/bin/env bash
# Build the runner daemon's wheel into the directory the app serves it from (HOJA-712).
#
#     ./scripts/build-runner-wheel.sh              # -> ./downloads/hoja_runnerd-<v>-py3-none-any.whl
#     ./scripts/build-runner-wheel.sh /tmp/wheels  # somewhere else (e.g. a release tree)
#
# Why a script and not a Dockerfile stage: `app/Dockerfile` and
# `infra/Dockerfile.hoja` are built with **this directory** as the build context
# (`COPY frontend/package*.json`, and the deploy passes `HOJA_SRC=<tree>/app`), so a
# stage cannot reach the sibling `runner/` tree. The wheel therefore has to exist in
# `./downloads` before the image is built; the image's `COPY . .` carries it to
# `/app/downloads`, and `GET /downloads/<wheel>` serves it from there
# (`hoja_server/runner_release.py`).
#
# `python -m build` (not `pip wheel`) because that is what produces the artifact a
# customer installs: a wheel with the entry point, the six profile TOMLs as package
# data, and no dependency resolution — the package is stdlib-only on purpose.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app="$(dirname "$here")"
root="$(dirname "$app")"
out="${1:-$app/downloads}"

python="${PYTHON:-python3}"
if ! "$python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
	echo "error: $python is missing or older than 3.11; the wheel requires >=3.11 to build" >&2
	exit 1
fi

test -f "$root/runner/pyproject.toml" || {
	echo "error: no runner/pyproject.toml at $root/runner" >&2
	exit 1
}

mkdir -p "$out"
# An older wheel in the same directory is served by version, so removing it is not
# required for correctness — but a stale one next to a newer one makes "latest" depend
# on comparing versions, which is exactly what we want to avoid while testing.
rm -f "$out"/hoja_runnerd-*.whl

# `build` goes into a throwaway environment rather than the caller's: PEP 668 marks most
# distribution Pythons "externally managed", where `pip install build` refuses outright,
# and a source checkout's interpreter should not be modified to cut a release artifact.
venv="$(mktemp -d)"
trap 'rm -rf "$venv"' EXIT
"$python" -m venv "$venv/venv"
"$venv/venv/bin/python" -m pip install --quiet --disable-pip-version-check build
"$venv/venv/bin/python" -m build --wheel --outdir "$out" "$root/runner"

ls -1 "$out"/hoja_runnerd-*.whl
