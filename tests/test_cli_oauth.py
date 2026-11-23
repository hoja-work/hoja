"""`hoja auth login/logout/token/status` against an in-process fake authorization server.

The fake follows the sign-in contract: metadata at
``/.well-known/oauth-authorization-server/mcp`` with issuer ``<origin>/mcp``,
the ``hoja-cli`` public client with PKCE S256, RFC 9207 ``iss`` on the redirect,
RFC 8628 device flow, refresh-token rotation and RFC 7009 revocation. All HTTP
to the workspace is routed to it with respx; the loopback redirect is a real
request to the CLI's 127.0.0.1 listener.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx
from typer.testing import CliRunner

from hoja import credentials, oauth
from hoja.auth import reset_viewer_cache
from hoja.cli import typer_app
from hoja.client import GraphClient
from hoja.commands import auth_cmd
from hoja.config import AccountConfig, WorkspaceInfo, load_config
from hoja.errors import AuthError, UsageError

ORIGIN = "https://acme.test"
ENV_VARS = ("HOJA_TOKEN", "LINEAR_TOKEN", "HOJA_API_URL", "LINEAR_API_URL", "SSH_CONNECTION", "SSH_TTY")


class FakeAS:
    """A contract-following authorization server + GraphQL endpoint."""

    def __init__(self, origin: str = ORIGIN) -> None:
        self.origin = origin
        self.issuer = origin + "/mcp"
        self.codes: dict[str, dict[str, str]] = {}
        self.access: set[str] = set()
        self.refresh: dict[str, str] = {}  # refresh token -> scope
        self.revoked: list[tuple[str, str]] = []
        self.refresh_calls = 0
        self.device_script: list[dict[str, Any]] = []
        self.device_polls = 0
        self.expires_in = 3600
        self.workspace_origin = origin
        self.on_refresh_rejected: Any = None
        self.lock = threading.Lock()
        self.n = 0
        self.metadata_overrides: dict[str, Any] = {}

    def _next(self, prefix: str) -> str:
        with self.lock:
            self.n += 1
            return f"{prefix}-{self.n}"

    def metadata(self) -> dict[str, Any]:
        doc = {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "revocation_endpoint": self.issuer + "/revoke",
            "device_authorization_endpoint": self.issuer + "/device",
            "grant_types_supported": ["authorization_code", "refresh_token", oauth.DEVICE_GRANT],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
        }
        doc.update(self.metadata_overrides)
        return doc

    def issue(self, scope: str) -> dict[str, Any]:
        at, rt = self._next("at"), self._next("rt")
        self.access.add(at)
        self.refresh[rt] = scope
        return {
            "access_token": at,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "refresh_token": rt,
            "scope": scope,
            "hoja_workspace": {"id": "org-1", "name": "Acme", "url_key": "acme", "origin": self.workspace_origin},
            "hoja_user": {"id": "u-1", "email": "ada@acme.test", "name": "Ada"},
        }

    @staticmethod
    def _error(code: str, status: int = 400) -> httpx.Response:
        return httpx.Response(status, json={"error": code})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.method == "POST" else {}
        if path == "/.well-known/oauth-authorization-server/mcp":
            return httpx.Response(200, json=self.metadata())
        if path == "/mcp/token":
            if form.get("client_id") != "hoja-cli":
                return self._error("invalid_client", 401)
            grant = form.get("grant_type")
            if grant == "authorization_code":
                entry = self.codes.pop(form.get("code", ""), None)
                if entry is None or entry["redirect_uri"] != form.get("redirect_uri"):
                    return self._error("invalid_grant")
                digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != entry["challenge"]:
                    return self._error("invalid_grant")
                if form.get("resource") != self.origin + "/graphql":
                    return self._error("invalid_target")
                return httpx.Response(200, json=self.issue(entry["scope"]))
            if grant == oauth.DEVICE_GRANT:
                self.device_polls += 1
                step = self.device_script.pop(0)
                if "error" in step:
                    return self._error(step["error"])
                return httpx.Response(200, json=self.issue("hoja:read hoja:write"))
            if grant == "refresh_token":
                with self.lock:
                    self.refresh_calls += 1
                    scope = self.refresh.pop(form.get("refresh_token", ""), None)
                if scope is None:
                    if self.on_refresh_rejected:
                        self.on_refresh_rejected()
                    return self._error("invalid_grant")
                if form.get("resource") != self.origin + "/graphql":
                    return self._error("invalid_target")
                return httpx.Response(200, json=self.issue(scope))
            return self._error("unsupported_grant_type")
        if path == "/mcp/device":
            if form.get("resource") != self.origin + "/graphql" or not form.get("device_name"):
                return self._error("invalid_request")
            return httpx.Response(
                200,
                json={
                    "device_code": "dc-1",
                    "user_code": "WDJB-MJHT",
                    "verification_uri": self.origin + "/device",
                    "verification_uri_complete": self.origin + "/device?user_code=WDJB-MJHT",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/mcp/revoke":
            self.revoked.append((form.get("token_type_hint", ""), form.get("token", "")))
            self.access.discard(form.get("token", ""))
            self.refresh.pop(form.get("token", ""), None)
            return httpx.Response(200)
        if path == "/graphql":
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] not in self.access:
                return httpx.Response(401, json={"errors": [{"message": "unauthorized"}]})
            return httpx.Response(
                200,
                json={"data": {"viewer": {"id": "u-1", "name": "Ada", "email": "ada@acme.test", "active": True}}},
            )
        return httpx.Response(404)

    # A browser stand-in: "approves" and follows the redirect to the loopback.
    def browser(self, *, state: str | None = None, iss: str | None = None, error: str | None = None):
        def open_browser(url: str) -> bool:
            q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
            assert url.startswith(self.issuer + "/authorize?")
            assert q["client_id"] == "hoja-cli" and q["code_challenge_method"] == "S256"
            assert q["resource"] == self.origin + "/graphql"
            assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/callback")
            assert q["device_name"].startswith("hoja-cli on ")
            code = self._next("code")
            self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"], "scope": q["scope"]}
            params = {"state": state or q["state"], "iss": iss or self.issuer}
            params.update({"error": error} if error else {"code": code})
            target = q["redirect_uri"] + "?" + urlencode(params)

            def follow() -> None:
                try:
                    with urllib.request.urlopen(target, timeout=10) as resp:
                        self.callback_page = resp.read().decode()
                except urllib.error.HTTPError as exc:
                    self.callback_page = exc.read().decode()

            self.follow_thread = threading.Thread(target=follow, daemon=True)
            self.follow_thread.start()
            return True

        return open_browser


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOJA_CONFIG", str(tmp_path / "hoja" / "config.toml"))
    monkeypatch.setenv("HOJA_CREDENTIAL_STORE", "file")
    monkeypatch.setenv("DISPLAY", ":0")
    reset_viewer_cache()
    yield tmp_path / "hoja"
    reset_viewer_cache()


@pytest.fixture
def fake():
    server = FakeAS()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="acme.test").mock(side_effect=server.handler)
        yield server


def run(*args: str):
    result = CliRunner().invoke(typer_app, list(args))
    return result


def login(fake: FakeAS, monkeypatch, *args: str, **browser: Any):
    monkeypatch.setattr(auth_cmd, "_open_browser", fake.browser(**browser))
    return run("auth", "login", *args)


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("acme.hoja.work", "https://acme.hoja.work"),
        ("https://acme.hoja.work", "https://acme.hoja.work"),
        ("https://acme.hoja.work/", "https://acme.hoja.work"),
        ("https://acme.hoja.work/graphql", "https://acme.hoja.work"),
        ("https://acme.hoja.work/graphql/", "https://acme.hoja.work"),
        ("acme.hoja.work/graphql", "https://acme.hoja.work"),
        ("https://acme.hoja.work/mcp", "https://acme.hoja.work"),
        ("  HTTPS://Acme.Hoja.Work:443/  ", "https://acme.hoja.work"),
        ("https://acme.hoja.work:8443", "https://acme.hoja.work:8443"),
        ("http://127.0.0.1:8787/graphql", "http://127.0.0.1:8787"),
        ("localhost:8787", "http://localhost:8787"),
        ("http://[::1]:8787", "http://[::1]:8787"),
    ],
)
def test_normalize_origin(value, expected):
    assert oauth.normalize_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://acme.hoja.work",
        "http://10.0.0.5:8787",
        "ftp://acme.hoja.work",
        "https://user:pw@acme.hoja.work",
        "https://acme.hoja.work/settings",
        "https://acme.hoja.work/?x=1",
        "https://",
    ],
)
def test_normalize_origin_rejects(value):
    with pytest.raises(UsageError):
        oauth.normalize_origin(value)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_discover_rejects_wrong_issuer_and_foreign_endpoints(fake):
    with httpx.Client() as http:
        assert oauth.discover(ORIGIN, http).iss_parameter_supported is True
        fake.metadata_overrides = {"issuer": "https://evil.test/mcp"}
        with pytest.raises(AuthError, match="issuer"):
            oauth.discover(ORIGIN, http)
        fake.metadata_overrides = {"token_endpoint": "https://evil.test/mcp/token"}
        with pytest.raises(AuthError, match="token endpoint"):
            oauth.discover(ORIGIN, http)


# ---------------------------------------------------------------------------
# Loopback flow end to end
# ---------------------------------------------------------------------------


def test_login_loopback_end_to_end(env, fake, monkeypatch):
    result = login(fake, monkeypatch, "https://acme.test/graphql/")
    assert result.exit_code == 0, result.output
    assert "Signed in to Acme as ada@acme.test (account 'acme')" in result.stdout
    fake.follow_thread.join(10)
    assert "Signed in to Hoja" in fake.callback_page

    cfg = load_config()
    acc = cfg.accounts["acme"]
    assert acc.credential == "oauth" and acc.origin == ORIGIN
    assert acc.base_url == ORIGIN + "/graphql" and acc.mcp_url == ORIGIN + "/mcp"
    assert acc.workspace and acc.workspace.url_key == "acme"
    assert acc.user and acc.user.email == "ada@acme.test"
    assert acc.credential_store == "file" and acc.token is None
    assert cfg.defaults.default_account == "acme"

    # Secrets: only in credentials.json, mode 0600; never in config.toml.
    creds = env / "credentials.json"
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600
    stored = json.loads(creds.read_text())["accounts"]["acme"]
    assert stored["access_token"] in fake.access and stored["refresh_token"] in fake.refresh
    config_text = (env / "config.toml").read_text()
    assert stored["access_token"] not in config_text and stored["refresh_token"] not in config_text
    assert not [p for p in env.iterdir() if p.name.startswith(".credentials-")]  # atomic write left no temp

    # The stored sign-in drives ordinary commands with a Bearer token.
    status = run("-o", "json", "auth", "status")
    assert status.exit_code == 0, status.output
    info = json.loads(status.stdout)
    assert info["source"] == "oauth" and info["kind"] == "oauth"
    assert info["viewer"]["email"] == "ada@acme.test"
    assert info["scopes"] == ["hoja:read", "hoja:write"]
    assert info["api_url"] == ORIGIN + "/graphql"


def test_login_read_only_requests_read_scope(env, fake, monkeypatch):
    result = login(fake, monkeypatch, "acme.test", "--read-only", "--name", "ro")
    assert result.exit_code == 0, result.output
    stored = json.loads((env / "credentials.json").read_text())["accounts"]["ro"]
    assert stored["scope"] == "hoja:read"


@pytest.mark.parametrize("tamper", [{"iss": "https://evil.test/mcp"}])
def test_login_rejects_state_or_iss_mismatch(env, fake, monkeypatch, tamper):
    result = login(fake, monkeypatch, "acme.test", **tamper)
    assert isinstance(result.exception, AuthError)
    assert "Sign-in rejected" in result.exception.message
    assert not fake.access  # the code was never redeemed
    assert not (env / "credentials.json").exists()
    assert not load_config().accounts


def test_login_reports_denied_in_browser(env, fake, monkeypatch):
    result = login(fake, monkeypatch, "acme.test", error="access_denied")
    assert isinstance(result.exception, AuthError)
    assert "access_denied" in result.exception.message


def test_login_rejects_token_for_another_workspace(env, fake, monkeypatch):
    fake.workspace_origin = "https://other.test"
    result = login(fake, monkeypatch, "acme.test")
    assert isinstance(result.exception, AuthError)
    assert not (env / "credentials.json").exists()


def test_loopback_ignores_a_callback_with_another_state_and_keeps_waiting(fake):
    """A forged or stale callback is answered 400 and ignored; the real one still signs in."""
    real = fake.browser()
    seen: dict[str, Any] = {}

    def browser(url: str) -> bool:
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]

        def forged_then_real() -> None:
            try:
                urllib.request.urlopen(redirect + "?state=forged&code=stolen", timeout=5)
            except urllib.error.HTTPError as exc:
                seen["forged"] = exc.code
            real(url)

        threading.Thread(target=forged_then_real, daemon=True).start()
        return True

    with httpx.Client() as http:
        meta = oauth.discover(ORIGIN, http)
        tokens = oauth.loopback_login(meta, http, scope="hoja:read", open_browser=browser, echo=lambda m: None)
    assert seen["forged"] == 400
    assert tokens is not None and tokens.workspace["url_key"] == "acme"


def test_loopback_times_out(fake):
    with httpx.Client() as http:
        meta = oauth.discover(ORIGIN, http)
        started = time.monotonic()
        with pytest.raises(AuthError, match="Timed out"):
            oauth.loopback_login(meta, http, scope="hoja:read", open_browser=lambda url: True, echo=lambda m: None, timeout=0.6)
        assert time.monotonic() - started < 5


def test_loopback_ignores_other_paths_and_serves_one_callback(fake):
    seen: dict[str, Any] = {}
    real = fake.browser()

    def browser(url: str) -> bool:
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
        port_root = redirect.rsplit("/", 1)[0]

        def probe_then_approve() -> None:
            try:
                urllib.request.urlopen(port_root + "/favicon.ico", timeout=5)
            except urllib.error.HTTPError as exc:
                seen["favicon"] = exc.code
            real(url)

        threading.Thread(target=probe_then_approve, daemon=True).start()
        return True

    with httpx.Client() as http:
        meta = oauth.discover(ORIGIN, http)
        tokens = oauth.loopback_login(meta, http, scope="hoja:read", open_browser=browser, echo=lambda m: None)
    assert seen["favicon"] == 404
    assert tokens is not None and tokens.workspace["url_key"] == "acme"


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _device(fake: FakeAS, clock: Clock) -> oauth.TokenSet:
    lines: list[str] = []
    with httpx.Client() as http:
        meta = oauth.discover(ORIGIN, http)
        try:
            return oauth.device_login(meta, http, scope="hoja:read hoja:write", echo=lines.append, sleep=clock.sleep, now=clock.now)
        finally:
            fake.device_output = "\n".join(lines)


def test_device_flow_pending_then_slow_down_then_success(fake):
    fake.device_script = [{"error": "authorization_pending"}, {"error": "slow_down"}, {"error": "authorization_pending"}, {}]
    clock = Clock()
    tokens = _device(fake, clock)
    assert tokens.access_token in fake.access
    assert clock.sleeps == [5, 5, 10, 10]
    assert "WDJB-MJHT" in fake.device_output
    assert ORIGIN + "/device?user_code=WDJB-MJHT" in fake.device_output


@pytest.mark.parametrize("error,match", [("access_denied", "denied"), ("expired_token", "expired")])
def test_device_flow_terminal_errors(fake, error, match):
    fake.device_script = [{"error": "authorization_pending"}, {"error": error}]
    with pytest.raises(AuthError, match=match):
        _device(fake, Clock())


def test_device_flow_stops_at_expiry_without_polling_forever(fake):
    fake.device_script = [{"error": "authorization_pending"}] * 200
    with pytest.raises(AuthError, match="expired"):
        _device(fake, Clock())
    assert fake.device_polls == 119  # 600s / 5s, the last sleep crosses the deadline


def test_login_device_flag_and_headless_detection(env, fake, monkeypatch):
    fake.device_script = [{}]
    monkeypatch.setattr(auth_cmd, "_sleep", lambda s: None)
    monkeypatch.setattr(auth_cmd, "_open_browser", lambda url: pytest.fail("browser opened"))
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 1 10.0.0.2 22")
    result = run("auth", "login", "acme.test")
    assert result.exit_code == 0, result.output
    assert "WDJB-MJHT" in result.stderr
    assert load_config().accounts["acme"].credential == "oauth"


def test_login_falls_back_to_device_when_browser_fails(env, fake, monkeypatch):
    fake.device_script = [{}]
    monkeypatch.setattr(auth_cmd, "_sleep", lambda s: None)
    monkeypatch.setattr(auth_cmd, "_open_browser", lambda url: False)
    result = run("auth", "login", "acme.test")
    assert result.exit_code == 0, result.output
    assert "signing in with a code instead" in result.stderr


def test_login_ctrl_c_during_device_flow_stores_nothing(env, fake, monkeypatch):
    fake.device_script = [{"error": "authorization_pending"}] * 5

    def interrupt(_s: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(auth_cmd, "_sleep", interrupt)
    result = run("auth", "login", "acme.test", "--device")
    assert result.exit_code != 0
    assert not (env / "credentials.json").exists()
    assert not load_config().accounts


@pytest.mark.parametrize(
    "env_vars,platform,headless",
    [
        ({"DISPLAY": ":0"}, "linux", False),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", False),
        ({}, "linux", True),
        ({"DISPLAY": ":0", "SSH_CONNECTION": "x"}, "linux", True),
        ({}, "darwin", False),
        ({"SSH_TTY": "/dev/pts/1"}, "darwin", True),
    ],
)
def test_is_headless(env_vars, platform, headless):
    assert oauth.is_headless(env_vars, platform) is headless


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def _oauth_account() -> AccountConfig:
    return AccountConfig(
        credential="oauth",
        origin=ORIGIN,
        base_url=ORIGIN + "/graphql",
        mcp_url=ORIGIN + "/mcp",
        credential_store="file",
        workspace=WorkspaceInfo(name="Acme", origin=ORIGIN),
    )


def _seed(fake: FakeAS, *, expires_at: float) -> dict[str, Any]:
    issued = fake.issue("hoja:read hoja:write")
    record = {
        "origin": ORIGIN,
        "access_token": issued["access_token"],
        "refresh_token": issued["refresh_token"],
        "expires_at": expires_at,
        "scope": issued["scope"],
        "token_endpoint": ORIGIN + "/mcp/token",
        "revocation_endpoint": ORIGIN + "/mcp/revoke",
    }
    credentials.FileStore().save("acme", record)
    return record


def test_refresh_before_expiry_persists_rotation(env, fake):
    old = _seed(fake, expires_at=time.time() + 30)  # inside the 60s window
    session = credentials.OAuthSession("acme", _oauth_account())
    token = session.access_token()
    assert token != old["access_token"] and fake.refresh_calls == 1
    stored = credentials.FileStore().load("acme")
    assert stored["access_token"] == token
    assert stored["refresh_token"] != old["refresh_token"] and stored["refresh_token"] in fake.refresh
    assert old["refresh_token"] not in fake.refresh
    # Fresh token: no further refresh.
    assert session.access_token() == token and fake.refresh_calls == 1


def test_no_refresh_when_token_is_fresh(env, fake):
    old = _seed(fake, expires_at=time.time() + 3600)
    assert credentials.OAuthSession("acme", _oauth_account()).access_token() == old["access_token"]
    assert fake.refresh_calls == 0


async def test_graph_client_refreshes_once_on_401(env, fake):
    old = _seed(fake, expires_at=time.time() + 3600)
    fake.access.discard(old["access_token"])  # revoked server-side before expiry
    session = credentials.OAuthSession("acme", _oauth_account())
    async with GraphClient(base_url=session.base_url, credential=session) as client:
        data = await client.execute("query Viewer { viewer { id } }")
    assert data["viewer"]["id"] == "u-1"
    assert fake.refresh_calls == 1
    assert credentials.FileStore().load("acme")["access_token"] in fake.access


async def test_graph_client_second_401_signs_out(env, fake):
    _seed(fake, expires_at=time.time() + 3600)
    fake.access.clear()
    original = fake.issue

    def issue_unusable(scope: str) -> dict[str, Any]:
        body = original(scope)
        fake.access.discard(body["access_token"])
        return body

    fake.issue = issue_unusable  # type: ignore[method-assign]
    session = credentials.OAuthSession("acme", _oauth_account())
    async with GraphClient(base_url=session.base_url, credential=session) as client:
        with pytest.raises(AuthError, match="Signed out of Acme") as exc:
            await client.execute("query { viewer { id } }")
    assert exc.value.hint == f"Run `hoja auth login {ORIGIN}`"
    assert fake.refresh_calls == 1


def test_failed_refresh_says_signed_out(env, fake):
    old = _seed(fake, expires_at=time.time() - 10)
    fake.refresh.pop(old["refresh_token"])
    with pytest.raises(AuthError, match="Signed out of Acme") as exc:
        credentials.OAuthSession("acme", _oauth_account()).access_token()
    assert exc.value.hint == f"Run `hoja auth login {ORIGIN}`"


def test_refresh_failure_rereads_storage_rotated_by_another_process(env, fake):
    old = _seed(fake, expires_at=time.time() - 10)
    session = credentials.OAuthSession("acme", _oauth_account())

    # Another process (not holding our lock) rotates first: our refresh token is spent.
    winner = fake.issue("hoja:read hoja:write")
    fake.refresh.pop(old["refresh_token"])

    def other_process_wrote() -> None:
        credentials.FileStore().save(
            "acme",
            {**old, "access_token": winner["access_token"], "refresh_token": winner["refresh_token"], "expires_at": time.time() + 3600},
        )

    fake.on_refresh_rejected = other_process_wrote
    assert session.access_token() == winner["access_token"]


def test_concurrent_refresh_rotates_once(env, fake):
    _seed(fake, expires_at=time.time() - 10)
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        try:
            barrier.wait()
            results.append(credentials.OAuthSession("acme", _oauth_account()).access_token())
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert fake.refresh_calls == 1
    assert len(set(results)) == 1 and results[0] in fake.access


# ---------------------------------------------------------------------------
# Logout, token --json, precedence
# ---------------------------------------------------------------------------


def test_logout_revokes_and_deletes(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    stored = credentials.FileStore().load("acme")
    result = run("auth", "logout")
    assert result.exit_code == 0, result.output
    assert ("refresh_token", stored["refresh_token"]) in fake.revoked
    assert ("access_token", stored["access_token"]) in fake.revoked
    assert credentials.FileStore().load("acme") is None
    # The account stays so `hoja auth login` knows where to sign back in.
    assert load_config().accounts["acme"].origin == ORIGIN
    after = run("auth", "token")
    assert isinstance(after.exception, AuthError) and "Signed out" in after.exception.message


def test_logout_refuses_api_key_accounts(env, fake):
    assert run("auth", "add", "ci", "--token", "key-123", "--token-env", "CI_KEY", "--no-verify").exit_code == 0
    result = run("auth", "logout", "ci")
    assert isinstance(result.exception, UsageError)


def test_token_json_output_shape(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    result = run("auth", "token", "--json")
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) >= {"access_token", "expires_at", "api_url", "mcp_url", "workspace"}
    assert payload["access_token"] in fake.access
    assert payload["api_url"] == ORIGIN + "/graphql" and payload["mcp_url"] == ORIGIN + "/mcp"
    assert payload["workspace"] == {"id": "org-1", "name": "Acme", "url_key": "acme", "origin": ORIGIN}
    assert payload["expires_at"].endswith("Z") and payload["source"] == "oauth"

    plain = run("auth", "token")
    assert plain.stdout.strip() == payload["access_token"]


def test_token_json_refreshes_and_keeps_stdout_clean(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    rec = credentials.FileStore().load("acme")
    credentials.FileStore().save("acme", {**rec, "expires_at": time.time() + 5})
    result = run("auth", "token", "--json")
    payload = json.loads(result.stdout)
    assert payload["access_token"] != rec["access_token"] and fake.refresh_calls == 1


def test_env_token_wins_over_oauth(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    monkeypatch.setenv("HOJA_TOKEN", "env-key")
    payload = json.loads(run("auth", "token", "--json").stdout)
    assert payload["access_token"] == "env-key" and payload["source"] == "$HOJA_TOKEN"
    assert payload["api_url"] == ORIGIN + "/graphql"
    monkeypatch.setenv("HOJA_API_URL", "http://127.0.0.1:8787/graphql")
    payload = json.loads(run("auth", "token", "--json").stdout)
    assert payload["api_url"] == "http://127.0.0.1:8787/graphql"
    # Status names the env source; the fake rejects the env key, so status exits 3.
    monkeypatch.setenv("HOJA_API_URL", ORIGIN + "/graphql")
    status = run("-o", "json", "auth", "status")
    info = json.loads(status.stdout)
    assert info["source"] == "$HOJA_TOKEN" and info["kind"] == "api_key" and info["viewer"] is None
    assert status.exit_code == 3


def test_flag_token_wins_over_oauth(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    payload = json.loads(run("--token", "flag-key", "auth", "token", "--json").stdout)
    assert payload["access_token"] == "flag-key" and payload["source"] == "--token"


def test_oauth_token_never_sent_to_a_foreign_api_url(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    monkeypatch.setenv("HOJA_API_URL", "https://elsewhere.test/graphql")
    result = run("auth", "token", "--json")
    assert result.stdout == ""
    assert isinstance(result.exception, AuthError) and "HOJA_API_URL" in result.exception.message
    monkeypatch.setenv("HOJA_API_URL", ORIGIN + "/graphql/")
    assert json.loads(run("auth", "token", "--json").stdout)["source"] == "oauth"


def test_graph_client_refuses_oauth_with_default_endpoint(env):
    session = credentials.OAuthSession("acme", _oauth_account())
    with pytest.raises(ValueError):
        GraphClient(credential=session)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_file_store_permissions_and_atomic_replace(env):
    store = credentials.FileStore()
    store.save("a", {"access_token": "x"})
    store.save("b", {"access_token": "y"})
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.load("a") == {"access_token": "x"} and store.load("b") == {"access_token": "y"}
    store.delete("a")
    assert store.load("a") is None and store.load("b") is not None
    assert sorted(p.name for p in Path(store.path).parent.iterdir()) == ["credentials.json"]


def test_keyring_used_when_available(env, fake, monkeypatch):
    saved: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        priority = 1

    class Module:
        @staticmethod
        def get_keyring() -> FakeKeyring:
            return FakeKeyring()

        @staticmethod
        def set_password(service: str, user: str, value: str) -> None:
            saved[(service, user)] = value

        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            return saved.get((service, user))

        @staticmethod
        def delete_password(service: str, user: str) -> None:
            saved.pop((service, user), None)

    monkeypatch.delenv("HOJA_CREDENTIAL_STORE")
    monkeypatch.setitem(__import__("sys").modules, "keyring", Module)
    assert login(fake, monkeypatch, "acme.test").exit_code == 0
    assert load_config().accounts["acme"].credential_store == "keyring"
    assert not (env / "credentials.json").exists()
    assert len(saved) == 1
    assert json.loads(run("auth", "token", "--json").stdout)["access_token"] in fake.access
    assert run("auth", "logout").exit_code == 0
    assert not saved


def test_old_config_files_still_load(env):
    env.mkdir(parents=True, exist_ok=True)
    (env / "config.toml").write_text(
        '[accounts.work]\ntoken_env = "WORK_TOKEN"\nbase_url = "http://127.0.0.1:8787/graphql"\nteams = ["ENG"]\n'
        '[auth]\ntoken = "legacy"\n'
    )
    cfg = load_config()
    assert cfg.accounts["work"].credential is None and cfg.accounts["default"].token == "legacy"


# Fields an AccountConfig had before `hoja auth login` (origin/main before this change).
# Every older CLI validates config.toml with extra="forbid", so these are all it may contain.
_PRE_OAUTH_ACCOUNT_FIELDS = {"token", "token_env", "org_name", "base_url", "teams"}


def test_a_signed_in_config_stays_readable_by_older_clis(env, fake, monkeypatch):
    from hoja.config import tomllib

    result = login(fake, monkeypatch, "acme.test", "--name", "work")
    assert result.exit_code == 0, result.output
    on_disk = tomllib.loads((env / "config.toml").read_text())
    for name, account in on_disk.get("accounts", {}).items():
        assert set(account) <= _PRE_OAUTH_ACCOUNT_FIELDS, (name, set(account) - _PRE_OAUTH_ACCOUNT_FIELDS)
    sidecar = json.loads((env / "signins.json").read_text())
    assert sidecar["accounts"]["work"]["credential"] == "oauth"
    assert "access_token" not in json.dumps(sidecar) and "refresh_token" not in json.dumps(sidecar)
    account = load_config().accounts["work"]
    assert account.credential == "oauth" and account.origin == ORIGIN
    assert account.workspace is not None and account.workspace.url_key == "acme"


def test_an_account_removed_by_an_older_cli_leaves_no_sign_in_behind(env, fake, monkeypatch):
    assert login(fake, monkeypatch, "acme.test", "--name", "work").exit_code == 0
    (env / "config.toml").write_text("")  # an older CLI rewrote the file without the account
    assert "work" not in load_config().accounts
