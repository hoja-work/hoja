"""Connect machines through the workspace's tailnet, the way Muse does (HOJA-602).

The person connects Tailscale once (an OAuth client of their tailnet); from then on the Machines
screen lists the tailnet's devices, and "Use for agents" on one installs the runner there with
no terminal: Hoja asks to be allowed (always, or just this once), then reaches the machine over
Tailscale SSH and runs the same one-line installer a person would paste.

How Hoja is on the tailnet
    The server joins each connected workspace's tailnet as its own **ephemeral** node, tagged
    ``tag:hoja``: a ``tailscaled`` in userspace networking (no TUN, no root, no state on disk),
    authenticated with the OAuth client secret as its auth key. It is started when it is needed
    (connecting, reaching a machine) and simply rejoins after a restart. The ``tailscale``
    binary is downloaded once from pkgs.tailscale.com, pinned to one version and checked against
    its published SHA-256, so the server image needs nothing extra.

What it may reach
    Tailscale lets a tagged node SSH only into tagged machines, so a machine Hoja is allowed to
    use is tagged ``tag:hoja-machine`` (through the API, when the person allows it). The tailnet
    policy decides the rest: one grant (``tag:hoja`` → ``tag:hoja-machine``, port 22) and one
    ``ssh`` rule (accept, non-root users). :data:`POLICY` is the snippet the UI shows.

What Hoja itself enforces
    It connects only to machines a person allowed (``tailnet_machine``), as the local user they
    named, and never again after "just this once" was used or access was revoked. Every connect,
    allow, revoke and install is audited.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

LOG = logging.getLogger("hoja_server.tailnet")

API = "https://api.tailscale.com/api/v2"
HOJA_TAG = "tag:hoja"
MACHINE_TAG = "tag:hoja-machine"
TAILSCALE_VERSION = "1.104.7"
#: Published at https://pkgs.tailscale.com/stable/tailscale_<version>_<arch>.tgz.sha256
TAILSCALE_SHA256 = {
    "amd64": "e56693ff74a64f2591b7e819c280635a100aae1ddfb0ef058fcd052b7cd8d16c",
    "arm64": "d6bed2720310b328b861f943d7b4016c5358a9bad60575eb51e8143b79068484",
}
INSTALL_TIMEOUT_S = 15 * 60
MODES = ("standing", "once")

#: The tailnet policy additions Hoja needs: who owns the tags, the network path, the SSH rule.
POLICY = {
    "tagOwners": {
        HOJA_TAG: ["autogroup:admin"],
        MACHINE_TAG: ["autogroup:admin", HOJA_TAG],
    },
    "grants": [{"src": [HOJA_TAG], "dst": [MACHINE_TAG], "ip": ["22"]}],
    "ssh": [{"action": "accept", "src": [HOJA_TAG], "dst": [MACHINE_TAG],
             "users": ["autogroup:nonroot"]}],
}

_USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}\$?$", re.IGNORECASE)


class TailnetError(Exception):
    """Something the person can act on; the message says what."""


# ------------------------------------------------------------------------------- the API
@dataclass
class Device:
    id: str
    name: str          # FQDN: surface.tail1234.ts.net
    hostname: str      # surface
    os: str | None
    address: str | None
    online: bool
    last_seen: str | None
    tags: tuple[str, ...]
    #: Tailscale SSH is on for the machine (the API's sshEnabled); None when not reported.
    ssh_enabled: bool | None = None
    #: The login that registered it (ada@example.com), for suggesting the local user name.
    user: str | None = None

    @property
    def ssh_support(self) -> str:
        """Whether Tailscale SSH can run on it: yes (Linux), cli (macOS, only with the
        open-source tailscaled, not the App Store or standalone app), or no."""
        system = (self.os or "").lower()
        if system == "linux":
            return "yes"
        if system in ("macos", "darwin"):
            return "cli"
        return "no"

    @property
    def tailnet_name(self) -> str | None:
        parts = self.name.split(".", 1)
        return parts[1] if len(parts) == 2 else None


class TailscaleAPI:
    """The few calls Hoja makes, with an OAuth client's credentials."""

    def __init__(self, client_id: str | None, client_secret: str | None, *,
                 access_token: str | None = None, http: httpx.Client | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self._http = http or httpx.Client(timeout=20)
        self._token: tuple[str, float] | None = (
            (access_token, float("inf")) if access_token else None)

    @classmethod
    def with_access_token(cls, token: str, *, http: httpx.Client | None = None) -> "TailscaleAPI":
        """A person's API access token (tskey-api-…): used once to set Hoja up, never stored."""
        return cls(None, None, access_token=token, http=http)

    def _bearer(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        response = self._http.post(f"{API}/oauth/token", data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "client_credentials"})
        if response.status_code in (400, 401, 403):
            raise TailnetError("Tailscale refused this OAuth client. Check the client ID and "
                               "secret, and that the client is not revoked.")
        response.raise_for_status()
        body = response.json()
        self._token = (body["access_token"], time.time() + float(body.get("expires_in") or 3600))
        return self._token[0]

    def _call(self, method: str, path: str, **kwargs) -> Any:
        response = self._http.request(method, f"{API}{path}", headers={
            "Authorization": f"Bearer {self._bearer()}"}, **kwargs)
        if response.status_code == 401:
            raise TailnetError("Tailscale refused the token: it may have expired. Create a new "
                               "one and paste it again.")
        if response.status_code == 403 and self.client_id is None:
            raise TailnetError(f"Your Tailscale account may not do that ({_message(response)}). "
                               f"An Owner or Admin of the tailnet has to connect it.")
        if response.status_code == 403:
            raise TailnetError(f"The OAuth client may not do that ({_message(response)}). Give it "
                               f"the devices:core and auth_keys scopes, with the tags "
                               f"{HOJA_TAG} and {MACHINE_TAG}.")
        if response.status_code >= 400:
            raise TailnetError(f"Tailscale said: {_message(response)}")
        if kwargs.get("headers", {}).get("Accept") == "application/hujson":
            return response
        return response.json() if response.content else None

    def devices(self) -> list[Device]:
        body = self._call("GET", "/tailnet/-/devices", params={"fields": "all"})
        return [_device(d) for d in (body or {}).get("devices") or []]

    def set_tags(self, device_id: str, tags: list[str]) -> None:
        self._call("POST", f"/device/{device_id}/tags", json={"tags": sorted(set(tags))})

    def get_policy(self) -> tuple[str, str]:
        """The policy file as written (HuJSON, comments kept) and its ETag."""
        response = self._call("GET", "/tailnet/-/acl", headers={"Accept": "application/hujson"})
        return response.text, response.headers.get("ETag", "")

    def validate_policy(self, text: str) -> None:
        body = self._call("POST", "/tailnet/-/acl/validate", content=text.encode(),
                          headers={"Content-Type": "application/hujson"})
        problem = (body or {}).get("message") or (body or {}).get("errors")
        if problem:
            raise TailnetError(f"Tailscale would not accept the policy change: {problem}")

    def set_policy(self, text: str, etag: str) -> None:
        headers = {"Content-Type": "application/hujson", "Accept": "application/hujson"}
        if etag:
            headers["If-Match"] = etag
        response = self._http.post(f"{API}/tailnet/-/acl", content=text.encode(), headers={
            **headers, "Authorization": f"Bearer {self._bearer()}"})
        if response.status_code == 412:
            raise TailnetError("Someone changed the tailnet policy just now. Try again.")
        if response.status_code >= 400:
            raise TailnetError(f"Tailscale would not save the policy: {_message(response)}")

    def create_oauth_client(self, description: str, scopes: list[str],
                            tags: list[str]) -> tuple[str, str]:
        """(client id, secret) of a new OAuth client (a trust credential)."""
        body = self._call("POST", "/tailnet/-/keys", json={
            "keyType": "client", "description": description, "scopes": scopes, "tags": tags})
        return str(body["id"]), str(body["key"])


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or response.text)[:300]
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"


def _device(raw: Mapping[str, Any]) -> Device:
    name = str(raw.get("name") or raw.get("hostname") or "")
    addresses = [a for a in raw.get("addresses") or [] if "." in str(a)] or list(
        raw.get("addresses") or [])
    return Device(
        id=str(raw.get("nodeId") or raw.get("id")), name=name,
        hostname=str(raw.get("hostname") or name.split(".")[0]),
        os=raw.get("os"), address=str(addresses[0]) if addresses else None,
        online=bool(raw.get("connectedToControl")), last_seen=raw.get("lastSeen"),
        tags=tuple(raw.get("tags") or ()),
        ssh_enabled=raw.get("sshEnabled") if isinstance(raw.get("sshEnabled"), bool) else None,
        user=raw.get("user") or None)


# ----------------------------------------------------------------------- the policy file
CLIENT_SCOPES = ["devices:core", "auth_keys"]
CLIENT_TAGS = [HOJA_TAG, MACHINE_TAG]


def _scan(text: str):
    """Yield (index, char, depth) for the HuJSON structure outside strings and comments."""
    i, depth, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            yield i, '"', depth
            i = j + 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "{[":
            yield i, c, depth
            depth += 1
        elif c in "}]":
            depth -= 1
            yield i, c, depth
        elif not c.isspace():
            yield i, c, depth
        i += 1


def standard_json(text: str) -> Any:
    """HuJSON to data: comments and trailing commas removed."""
    out, i, n = [], 0, len(text)

    def skip(k: int) -> int:  # past whitespace and comments
        while k < n:
            if text[k].isspace():
                k += 1
            elif text.startswith("//", k):
                j = text.find("\n", k)
                k = n if j < 0 else j
            elif text.startswith("/*", k):
                j = text.find("*/", k + 2)
                k = n if j < 0 else j + 2
            else:
                break
        return k

    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i) or text.startswith("/*", i):
            i = skip(i)
            out.append(" ")
        elif c == ",":
            k = skip(i + 1)
            if k >= n or text[k] not in "}]":
                out.append(",")
            i += 1
        else:
            out.append(c)
            i += 1
    return json.loads("".join(out) or "{}")


def _top_level(text: str) -> tuple[int, dict[str, int]]:
    """(index just inside the root object's {, {key: index just inside its value's { or [})."""
    root, keys, key, want_value = -1, {}, None, False
    tokens = list(_scan(text))
    for k, (i, c, depth) in enumerate(tokens):
        if root < 0:
            if c == "{":
                root = i + 1
            continue
        if depth == 1 and c == '"' and not want_value:
            nxt = tokens[k + 1] if k + 1 < len(tokens) else None
            if nxt and nxt[1] == ":":
                end = i + 1
                while text[end] != '"':
                    end += 2 if text[end] == "\\" else 1
                key = json.loads(text[i:end + 1])
                want_value = True
            continue
        if want_value and c in "{[" and depth == 1:
            keys[key] = i + 1
            want_value = False
        elif want_value and c not in ":":
            want_value = False
    return root, keys


def added_snippet(old: str) -> str:
    """What plan_policy adds, grouped under its section, as it reads in the policy file."""
    current = standard_json(old) if old.strip() else {}
    parts = []
    owners = {t: w for t, w in POLICY["tagOwners"].items()
              if t not in (current.get("tagOwners") or {})}
    if owners:
        parts.append('"tagOwners": {\n' + "".join(
            f'\t{json.dumps(t)}: {json.dumps(w)},\n' for t, w in owners.items()) + "}")
    for section in ("grants", "ssh"):
        rules = [r for r in POLICY[section] if r not in (current.get(section) or [])]
        if rules:
            parts.append(f'"{section}": [\n' + "".join(f"\t{json.dumps(r)},\n" for r in rules)
                         + "]")
    return "\n".join(parts)


def plan_policy(text: str) -> tuple[str, list[str]]:
    """The policy with what Hoja needs added, and a line per addition. Comments, order and
    everything already there are kept; what is already present is not added twice."""
    current = standard_json(text) if text.strip() else {}
    if not text.strip():
        text = "{\n}\n"
    owners = current.get("tagOwners") or {}
    inserts: dict[str, list[str]] = {}
    notes: list[str] = []
    for tag, who in POLICY["tagOwners"].items():
        if tag not in owners:
            inserts.setdefault("tagOwners", []).append(f'"{tag}": {json.dumps(who)}')
            notes.append(f"tagOwners: {tag} (owned by {', '.join(who)})")
    for section in ("grants", "ssh"):
        for rule in POLICY[section]:
            if rule not in (current.get(section) or []):
                inserts.setdefault(section, []).append(json.dumps(rule))
                notes.append(f"{section}: {_describe(section, rule)}")
    if not inserts:
        return text, []
    root, keys = _top_level(text)
    edits: list[tuple[int, str]] = []
    missing = []
    for section, entries in inserts.items():
        body = "".join(f"\n\t\t// Added by Hoja (agents on your machines)\n\t\t{e},"
                       if n == 0 else f"\n\t\t{e}," for n, e in enumerate(entries))
        if section in keys:
            edits.append((keys[section], body))
        else:
            missing.append((section, body))
    if missing:
        opener = {"tagOwners": ("{", "}"), "grants": ("[", "]"), "ssh": ("[", "]")}
        block = "".join(f'\n\t"{section}": {opener[section][0]}{body}\n\t{opener[section][1]},'
                        for section, body in missing)
        edits.append((root, block))
    for at, insertion in sorted(edits, reverse=True):
        text = text[:at] + insertion + text[at:]
    standard_json(text)  # still parses
    return text, notes


def _describe(section: str, rule: Mapping[str, Any]) -> str:
    if section == "grants":
        return f"{', '.join(rule['src'])} may reach {', '.join(rule['dst'])} on port 22"
    return (f"{', '.join(rule['src'])} may SSH into {', '.join(rule['dst'])} as "
            f"{', '.join(rule['users'])}")


def suggested_user(login: str | None) -> str | None:
    local = (login or "").split("@", 1)[0].lower()
    local = re.sub(r"[^a-z0-9_.-]", "", local)
    return local if local and _USER_RE.match(local) and local != "root" else None


# ------------------------------------------------------------------------- Hoja's node
def _data_root() -> Path:
    base = os.environ.get("HOJA_TAILNET_ROOT") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "hoja", "tailnet")
    return Path(base)


def _arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


def ensure_binaries(root: Path | None = None) -> Path:
    """The pinned tailscale/tailscaled pair, downloaded and verified on first use."""
    target = (root or _data_root()) / f"bin-{TAILSCALE_VERSION}-{_arch()}"
    if (target / "tailscaled").exists() and (target / "tailscale").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.parent / ".download.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "tailscaled").exists():
            return target
        arch = _arch()
        url = f"https://pkgs.tailscale.com/stable/tailscale_{TAILSCALE_VERSION}_{arch}.tgz"
        blob = httpx.get(url, timeout=180, follow_redirects=True).raise_for_status().content
        if hashlib.sha256(blob).hexdigest() != TAILSCALE_SHA256[arch]:
            raise TailnetError("The Tailscale download did not match its published checksum.")
        staging = target.with_suffix(".partial")
        staging.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
            for member in archive.getmembers():
                leaf = member.name.rsplit("/", 1)[-1]
                if member.isfile() and leaf in ("tailscale", "tailscaled"):
                    data = archive.extractfile(member).read()
                    (staging / leaf).write_bytes(data)
                    (staging / leaf).chmod(0o755)
        staging.rename(target)
    return target


class TailnetNode:
    """Hoja's ephemeral userspace node on one workspace's tailnet."""

    def __init__(self, org_id: str, *, root: Path | None = None):
        self.org_id = org_id
        self.root = root or _data_root()
        self.dir = self.root / "nodes" / org_id
        self.socket = str(self.dir / "tailscaled.sock")
        if len(self.socket) > 100:  # a unix socket path is limited to ~108 bytes
            short = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"hoja-ts-{os.getuid()}"
            short.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.socket = str(short / f"{hashlib.sha256(org_id.encode()).hexdigest()[:16]}.sock")

    def _cli(self, bins: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run([str(bins / "tailscale"), f"--socket={self.socket}", *args],
                              capture_output=True, text=True, timeout=timeout)

    def status(self) -> dict[str, Any]:
        """{state, name, error}. Never starts anything."""
        bins = self.root / f"bin-{TAILSCALE_VERSION}-{_arch()}"
        if not (bins / "tailscale").exists() or not os.path.exists(self.socket):
            return {"state": "stopped", "name": None}
        try:
            done = self._cli(bins, "status", "--json", timeout=10)
            body = json.loads(done.stdout or "{}")
        except (subprocess.SubprocessError, ValueError, OSError):
            return {"state": "stopped", "name": None}
        me = body.get("Self") or {}
        state = {"Running": "running", "NeedsLogin": "needs-login", "Starting": "starting"}.get(
            body.get("BackendState"), str(body.get("BackendState") or "stopped").lower())
        return {"state": state, "name": (me.get("DNSName") or "").rstrip(".") or None}

    def up(self, client_secret: str, hostname: str) -> dict[str, Any]:
        """Start tailscaled if it is not running and join the tailnet. Idempotent."""
        bins = ensure_binaries(self.root)
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.status()["state"] == "running":
                return self.status()
            if not self._alive(bins):
                log = open(self.dir / "tailscaled.log", "ab")
                daemon = subprocess.Popen(
                    [str(bins / "tailscaled"), "--tun=userspace-networking", "--state=mem:",
                     f"--socket={self.socket}", f"--statedir={self.dir}", "--port=0"],
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
                (self.dir / "tailscaled.pid").write_text(str(daemon.pid))
                for _ in range(50):
                    if self._alive(bins):
                        break
                    time.sleep(0.2)
            key = f"{client_secret}?ephemeral=true&preauthorized=true"
            done = self._cli(bins, "up", f"--auth-key={key}", f"--advertise-tags={HOJA_TAG}",
                             f"--hostname={hostname}", "--accept-dns=false", "--timeout=60s",
                             timeout=90)
            if done.returncode != 0:
                raise TailnetError(_up_error(done.stderr or done.stdout))
        return self.status()

    def _alive(self, bins: Path) -> bool:
        if not os.path.exists(self.socket):
            return False
        try:
            return self._cli(bins, "status", "--json", timeout=5).returncode in (0, 1)
        except (subprocess.SubprocessError, OSError):
            return False

    def down(self) -> None:
        bins = self.root / f"bin-{TAILSCALE_VERSION}-{_arch()}"
        if (bins / "tailscale").exists() and os.path.exists(self.socket):
            try:
                self._cli(bins, "logout", timeout=20)  # an ephemeral node is removed at once
            except (subprocess.SubprocessError, OSError):
                pass
        pidfile = self.dir / "tailscaled.pid"
        try:
            os.kill(int(pidfile.read_text().strip()), 15)
        except (OSError, ValueError):
            pass
        pidfile.unlink(missing_ok=True)
        for _ in range(50):  # wait for the socket to go, so status() says stopped
            if not os.path.exists(self.socket):
                break
            time.sleep(0.1)

    def proxy_command(self, address: str, port: int = 22) -> str:
        bins = self.root / f"bin-{TAILSCALE_VERSION}-{_arch()}"
        return shlex.join([str(bins / "tailscale"), f"--socket={self.socket}", "nc",
                           address, str(port)])


def _up_error(text: str) -> str:
    text = (text or "").strip()
    if "401" in text or "token invalid" in text:
        return "Tailscale refused the OAuth client's secret: create a new one and connect again."
    if "tags" in text and ("not permitted" in text or "invalid" in text):
        return (f"Tailscale would not tag Hoja's node {HOJA_TAG}: add the tagOwners lines from "
                f"the policy below, and give the OAuth client the {HOJA_TAG} tag.")
    return f"Hoja could not join the tailnet: {text[-300:] or 'no reason given'}"


# ---------------------------------------------------------------------------------- SSH
def ssh_run(node: TailnetNode, address: str, user: str, command: str, *,
            on_output: Callable[[str], None], timeout: float = INSTALL_TIMEOUT_S) -> tuple[int, str]:
    """Run ``command`` as ``user`` on the machine over Tailscale SSH. Returns (exit, host key)."""
    import paramiko

    logging.getLogger("paramiko").setLevel(logging.CRITICAL)  # its thread logs what we report
    sock = paramiko.ProxyCommand(node.proxy_command(address))
    transport = paramiko.Transport(sock)
    try:
        try:
            transport.start_client(timeout=30)
        except (paramiko.SSHException, EOFError, OSError) as exc:
            raise TailnetError(
                "Hoja could not reach the machine's port 22 over the tailnet. Check that the "
                f"policy grants {HOJA_TAG} → {MACHINE_TAG} on port 22.") from exc
        host_key = transport.get_remote_server_key()
        fingerprint = f"{host_key.get_name()} {host_key.fingerprint}"
        try:
            transport.auth_none(user)
        except paramiko.BadAuthenticationType as exc:
            raise TailnetError(
                "The machine answered, but not with Tailscale SSH. Turn it on once there: "
                "sudo tailscale set --ssh") from exc
        except paramiko.AuthenticationException as exc:
            raise TailnetError(
                f"Tailscale SSH refused {user}: check the policy's ssh rule (accept, "
                f"{HOJA_TAG} → {MACHINE_TAG}, autogroup:nonroot) and that the user exists.") from exc
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        deadline, pending = time.time() + timeout, b""
        while True:
            if channel.recv_ready():
                pending += channel.recv(4096)
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    on_output(line.decode("utf-8", "replace"))
            elif channel.exit_status_ready():
                break
            elif time.time() > deadline:
                raise TailnetError("The install did not finish in 15 minutes.")
            else:
                time.sleep(0.1)
        while channel.recv_ready():
            pending += channel.recv(4096)
        for line in pending.decode("utf-8", "replace").splitlines():
            on_output(line)
        return channel.recv_exit_status(), fingerprint
    finally:
        transport.close()


# ----------------------------------------------------------------------- seams for tests
#: Replaced in tests: build the API client, the node, and the SSH call.
api_factory: Callable[[str, str], Any] = TailscaleAPI
token_api_factory: Callable[[str], Any] = TailscaleAPI.with_access_token
node_factory: Callable[[str], Any] = TailnetNode
ssh = ssh_run


def clean_user(value: str) -> str:
    text = (value or "").strip()
    if not _USER_RE.match(text) or text == "root":
        raise TailnetError("Use the machine's own (non-root) user name, like the one you log in "
                           "with.")
    return text


def node_hostname(org_slug: str | None) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (org_slug or "workspace").lower()).strip("-")[:40]
    return f"hoja-{slug or 'workspace'}"


def in_background(work: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=work, name="hoja-tailnet", daemon=True)
    thread.start()
    return thread
