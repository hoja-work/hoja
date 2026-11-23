"""The runner daemon as an artifact this deployment serves (HOJA-712).

``hoja-runnerd`` is not on PyPI and must not need to be: ``runner/pyproject.toml``
keeps an empty dependency list on purpose, so a customer host installs one wheel and
resolves nothing. Until this module the only way to get that wheel was a git checkout
and an editable install, which is why the enroll command the admin screen prints
assumed a ``hoja-runnerd`` that no machine had (the live-run finding in
``artifacts/runs/hoja-602/live-runner/REPORT.md``).

So the app serves the wheel it was built with, on its own origin, unauthenticated:

* ``GET /downloads/hoja_runnerd-<version>-py3-none-any.whl`` — the artifact itself.
  No session, because the machine that runs ``pipx install`` has not enrolled yet and
  therefore has no credential; the file is public by design, like the SPA bundle.
* ``GET /downloads/hoja-runnerd/latest`` — a JSON pointer at the newest wheel, so the
  panel can name a version that changes without the screen changing.

One rule shapes the routing: the served names are read from the directory, never
composed from the request. ``/downloads/{name}`` accepts a single path segment and then
checks that the segment is one of the wheel names found in ``HOJA_DOWNLOAD_ROOT``, so
``..%2f``, an absolute path or an unrelated file in that directory cannot be reached.

Where the file comes from: ``app/Dockerfile`` builds the wheel in its own stage
(``python -m build --wheel``) and copies it to ``/app/downloads``, which is this
module's default root — the same place ``COPY --from=frontend-build`` puts the SPA. A
native checkout builds it with ``scripts/build-runner-wheel.sh`` instead, or sets
``HOJA_DOWNLOAD_ROOT``. An install that never built one answers 404 and the enrollment
mutation returns ``installCommand: null`` rather than a URL that would 404.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

#: Everything the product publishes for the runner lives under this one prefix.
DOWNLOAD_PREFIX = "/downloads"

#: The stable pointer. It answers JSON, not a redirect: pip resolves a dist name from
#: the file name it is given, and ``hoja-runnerd-latest.whl`` is not a valid wheel name.
LATEST_PATH = f"{DOWNLOAD_PREFIX}/hoja-runnerd/latest"

#: ``runner/pyproject.toml`` requires this, and the wheel refuses to install below it.
#: Restated rather than parsed at runtime: ``tomllib`` is 3.11+, and this server runs
#: on 3.10 (``app/pyproject.toml``). ``app/tests/agent/test_runner_enrollment_admin.py``
#: asserts the two agree.
REQUIRES_PYTHON = "3.11"

#: The only shape ``python -m build --wheel`` produces for this project: a pure-Python
#: wheel, so the tag is fixed. Anything else in the directory is not served.
WHEEL_RE = re.compile(r"^hoja_runnerd-(?P<version>[0-9][A-Za-z0-9._]*)-py3-none-any\.whl$")


def download_root() -> Path:
    """Where the built wheel is served from.

    Defaults to ``<app>/downloads``: ``app/Dockerfile`` puts it there, next to
    ``/app/web``, and a native checkout that ran ``scripts/build-runner-wheel.sh`` has
    it there too.
    """
    configured = os.environ.get("HOJA_DOWNLOAD_ROOT", "").strip()
    if configured:
        return Path(configured)
    package_adjacent = Path(__file__).resolve().parent.parent / "downloads"
    # An image that pip-installs the app puts this module in site-packages, so the directory
    # next to the package is not /app/downloads. The web root is configured in every image
    # (HOJA_WEB_ROOT=/app/web), and the wheel is copied beside it; production builds from a
    # server-side Dockerfile, so this cannot rely on an ENV line in the repository's one.
    web_root = os.environ.get("HOJA_WEB_ROOT", "").strip()
    candidates = [package_adjacent]
    if web_root:
        candidates.append(Path(web_root).parent / "downloads")
    candidates.append(Path.cwd() / "downloads")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return package_adjacent


def _version_key(version: str) -> tuple:
    """Numeric-aware ordering, so 0.10.0 sorts above 0.9.0 without ``packaging``."""
    parts = re.split(r"[._-]", version)
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts)


def wheels(root: Path | None = None) -> list[Path]:
    """Every servable wheel under ``root``, oldest first. Never raises on a missing dir."""
    base = root if root is not None else download_root()
    try:
        entries = list(base.iterdir())
    except OSError:
        return []
    found = [(WHEEL_RE.match(entry.name), entry) for entry in entries]
    servable = [(match, entry) for match, entry in found
                if match is not None and entry.is_file()]
    servable.sort(key=lambda pair: (_version_key(pair[0].group("version")), pair[1].name))
    return [entry for _match, entry in servable]


def latest_wheel(root: Path | None = None) -> Path | None:
    """The newest wheel, or ``None`` when this deployment publishes none."""
    servable = wheels(root)
    return servable[-1] if servable else None


def wheel_version(wheel: Path) -> str:
    match = WHEEL_RE.match(wheel.name)
    return match.group("version") if match else ""


def download_path(wheel: Path) -> str:
    """The path this wheel is served at — the one the panel and the README print."""
    return f"{DOWNLOAD_PREFIX}/{wheel.name}"


def download_url(base_url: str, root: Path | None = None) -> str | None:
    """Absolute URL of the newest wheel, or ``None`` when there is nothing to serve."""
    wheel = latest_wheel(root)
    return f"{base_url.rstrip('/')}{download_path(wheel)}" if wheel else None


def install_command(url: str) -> str:
    """The one-liner, given that URL.

    ``pipx`` rather than ``python3 -m pip install``: the daemon is a command, not a
    library, so it wants its own environment and — the point — its entry point on
    ``PATH``. ``pip --user`` writes to ``~/.local/bin`` on Linux and to
    ``~/Library/Python/X.Y/bin`` on macOS, which is on no ``PATH`` by default, and the
    enroll command this same response carries names a bare ``hoja-runnerd``. pipx
    installs into ``~/.local/bin`` and says to run ``pipx ensurepath``.
    """
    return f"pipx install {url}"


#: The one-command installer (HOJA-602 machine-first): ``curl -fsSL <base>/install.sh | sh``.
INSTALL_SCRIPT_PATH = "/install.sh"


def clean_work_root(value: str | None) -> str | None:
    """A work root a person typed, or None for the standard one. Refused when it could not be a
    directory on the machine or could not be passed to a shell safely."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > 300 or any(ch in text for ch in " \n\r\t\"'`$;|&<>\\"):
        raise ValueError("A work folder is a plain path without spaces, like ~/code or /srv/hoja.")
    if not (text.startswith("/") or text == "~" or text.startswith("~/")):
        raise ValueError("Give an absolute path (/srv/hoja) or one under your home (~/code).")
    return text.rstrip("/") or "/"


def connect_command(base_url: str, token: str, *, work_root: str | None = None) -> str:
    """The single line that installs, enrolls and starts a runner on a machine.

    The token travels as an environment variable of the ``sh`` that runs the script, never as an
    argument, so ``ps`` on a shared host does not show it; it is single-use and short-lived either
    way. The machine names itself after its hostname, the way a device joins a tailnet.
    """
    # ``~`` stays unquoted so the machine's shell expands it to that machine's home.
    where = ""
    if work_root:
        where = f" HOJA_WORK_ROOT={work_root}"
    return (f"curl -fsSL {base_url.rstrip('/')}{INSTALL_SCRIPT_PATH} | "
            f"HOJA_ENROLLMENT_TOKEN={token}{where} sh")


def install_script(base_url: str, wheel_url: str) -> str:
    """A POSIX sh script: a private venv, the wheel (with the Hoja Coder harness), enroll, and a
    service that keeps the runner up across logouts and reboots (systemd --user, launchd, or a
    plain background process when neither exists). Re-running it upgrades in place."""
    base = base_url.rstrip("/")
    return _INSTALL_TEMPLATE.replace("@BASE@", base).replace("@WHEEL@", wheel_url)


_INSTALL_TEMPLATE = r"""#!/bin/sh
# Hoja runner installer. Run the line Hoja prints under Agents -> Machines -> Connect a machine:
#   curl -fsSL @BASE@/install.sh | HOJA_ENROLLMENT_TOKEN=<token> sh
# Installs hoja-runnerd into ~/.hoja/runner, connects this machine to your workspace, and keeps
# it running. The runner only dials out to Hoja; nothing listens on this machine.
set -eu
BASE="@BASE@"
WHEEL="@WHEEL@"
DIR="${HOJA_RUNNER_DIR:-$HOME/.hoja/runner}"
# Where agents work: repos under it, one git worktree per task (worktrees/), scratch/ for the rest.
WORK="${HOJA_WORK_ROOT:-$HOME/hoja}"
case "$WORK" in "~"|"~/"*) WORK="$HOME${WORK#\~}" ;; esac
NAME="${HOJA_RUNNER_NAME:-$(hostname -s 2>/dev/null || hostname)}"
EXTRA="[coder]"
[ "${HOJA_RUNNER_NO_CODER:-}" = "1" ] && EXTRA=""

say() { printf '%s
' "$*"; }
fail() { printf 'hoja: %s
' "$*" >&2; exit 1; }

[ -n "${HOJA_ENROLLMENT_TOKEN:-}" ] || [ -f "$DIR/runner.json" ] ||   fail "no enrollment token. Copy the command from Hoja: Agents -> Machines -> Connect a machine."

say "Installing the Hoja runner in $DIR"
mkdir -p "$DIR"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if command -v uv >/dev/null 2>&1; then
  [ -x "$DIR/venv/bin/python" ] || uv venv -q --python "${PY:-3.12}" "$DIR/venv"
  uv pip install -q --python "$DIR/venv/bin/python" --upgrade "hoja-runnerd$EXTRA @ $WHEEL"
elif [ -n "$PY" ]; then
  [ -x "$DIR/venv/bin/python" ] || "$PY" -m venv "$DIR/venv" ||     fail "could not create a virtualenv (on Debian/Ubuntu: sudo apt install python3-venv)"
  "$DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade "hoja-runnerd$EXTRA @ $WHEEL"
else
  fail "needs Python 3.11 or newer, or uv (https://docs.astral.sh/uv/). Install one and run this again."
fi
RUNNERD="$DIR/venv/bin/hoja-runnerd"

if [ -n "${HOJA_ENROLLMENT_TOKEN:-}" ]; then
  say "Connecting $NAME to Hoja"
  HOJA_ENROLLMENT_TOKEN="$HOJA_ENROLLMENT_TOKEN" "$RUNNERD" --enroll --base-url "$BASE"     --name "$NAME" --state-dir "$DIR" >/dev/null || fail "Hoja refused the token (it works once, for an hour). Copy a fresh command and run it again."
fi

mkdir -p "$WORK"
START="$RUNNERD --base-url $BASE --state-dir $DIR --work-root $WORK"
SERVICE="${HOJA_RUNNER_SERVICE:-auto}"   # auto | none (a container, a CI box, a test)
if [ "$SERVICE" = "none" ]; then
  :
elif [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/work.hoja.runnerd.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>work.hoja.runnerd</string>
  <key>ProgramArguments</key><array>
    <string>$RUNNERD</string><string>--base-url</string><string>$BASE</string>
    <string>--state-dir</string><string>$DIR</string>
    <string>--work-root</string><string>$WORK</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/runnerd.log</string>
  <key>StandardErrorPath</key><string>$DIR/runnerd.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/work.hoja.runnerd" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  HOW="as a launch agent (work.hoja.runnerd)"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  UNIT="$HOME/.config/systemd/user/hoja-runnerd.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UNIT
[Unit]
Description=Hoja runner
After=network-online.target

[Service]
ExecStart=$START
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now hoja-runnerd >/dev/null 2>&1
  systemctl --user restart hoja-runnerd
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
  HOW="as a systemd user service (hoja-runnerd)"
fi
if [ -z "${HOW:-}" ]; then
  [ -f "$DIR/runnerd.pid" ] && kill "$(cat "$DIR/runnerd.pid")" >/dev/null 2>&1 || true
  nohup $START >> "$DIR/runnerd.log" 2>&1 &
  echo $! > "$DIR/runnerd.pid"
  HOW="in the background (log: $DIR/runnerd.log)"
fi

say ""
say "  Connected: $NAME is running $HOW."
say "  Agents work in $WORK (your repos, a worktree per task under $WORK/worktrees)."
say "  It shows up in Hoja under Agents -> Machines within a few seconds."
"""


def public_base(request: Request) -> str:
    """This deployment's URL as the client reached it, behind the TLS-terminating proxy.

    ``request.base_url`` is ``http://`` behind the proxy, and an installer that pointed the
    runner at it would send every API call into a 301 (a redirected POST is not the same POST).
    Same rule as ``agent_runner_admin._base_url``: honour X-Forwarded-Proto and -Host.
    """
    headers = request.headers
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").strip()
    scheme = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = request.url.scheme or "https"
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{scheme}://{host}"


def downloads_routes(root: Path | None = None) -> list[Route]:
    """The three routes, as Starlette ``Route`` objects for ``create_app``."""

    def _wheel_for(name: str) -> Path | None:
        for wheel in wheels(root):
            if wheel.name == name:
                return wheel
        return None

    async def latest(request: Request) -> JSONResponse:
        # The pointer names the artifact and says how to install it, so the only copy of
        # the one-liner is ``install_command`` above.
        wheel = latest_wheel(root)
        if wheel is None:
            return JSONResponse(
                {"error": "This deployment publishes no hoja-runnerd wheel."},
                status_code=404,
            )
        url = f"{public_base(request)}{download_path(wheel)}"
        return JSONResponse({
            "name": "hoja-runnerd",
            "version": wheel_version(wheel),
            "filename": wheel.name,
            "url": url,
            "requiresPython": REQUIRES_PYTHON,
            "install": install_command(url),
        })

    async def wheel_file(request: Request) -> FileResponse | JSONResponse:
        wheel = _wheel_for(request.path_params["name"])
        if wheel is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(
            wheel,
            media_type="application/octet-stream",
            filename=wheel.name,
            content_disposition_type="attachment",
        )

    async def installer(request: Request) -> Response:
        wheel = latest_wheel(root)
        base = public_base(request)
        if wheel is None:
            body = ("#!/bin/sh\necho 'hoja: this deployment publishes no runner yet; "
                    "ask your Hoja admin.' >&2\nexit 1\n")
            return PlainTextResponse(body, status_code=404, media_type="text/x-shellscript")
        return PlainTextResponse(install_script(base, f"{base}{download_path(wheel)}"),
                                 media_type="text/x-shellscript",
                                 headers={"cache-control": "no-store"})

    return [
        Route(INSTALL_SCRIPT_PATH, installer, methods=["GET"]),
        # Before ``/downloads/{name}``, which would otherwise read "hoja-runnerd" as a file.
        Route(LATEST_PATH, latest, methods=["GET"]),
        Route(f"{DOWNLOAD_PREFIX}/{{name}}", wheel_file, methods=["GET", "HEAD"]),
    ]
