"""The /signup page's contract with the server.

The page is the user-visible half of the gate that ``tests/test_signup_gate.py``
covers. What is asserted here is the seam between the two, because that is where
this kind of UI goes wrong:

1. ``GET /api/signup/config`` publishes the public configuration (mode, Turnstile
   *site* key, Google sign-in start URL) and **nothing else** — no secret, ever —
   and reports ``available: false`` when there is no site key, so the page renders
   "sign-up unavailable" instead of a widget that could never be verified.
2. The page's reason-code table covers *every* code the server can answer with,
   compared against ``signup_gate.SIGNUP_REASON_CODES`` — the server's own list,
   not a copy. A new code fails this suite until the page is updated.
3. The workspace-address rule the page uses is generated from
   ``hoja_server/signup_policy.py``, and the generated file is current; when node
   is available the generated rule is also compared *behaviourally* against the
   real ``validate_slug`` on one corpus, through
   ``scripts/test-signup-page.cjs --slug-check``.
4. The submit handler posts the Turnstile token and surfaces a refusal, tested in
   ``scripts/test-signup-page.cjs`` (run here so `pytest -q` covers it).

Nothing in this module touches the network, Google or Cloudflare.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate
from hoja_server.signup_config import (
    MODE_ENV,
    REQUIRED_ENV,
    SITE_KEY_ENV,
    SignupConfigError,
    load_signup_config,
)
from hoja_server.signup_gate import SIGNUP_REASON_CODES
from hoja_server.signup_policy import SlugRefused, validate_slug

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
SIGNUP_SRC = FRONTEND / "src" / "signup"

PUBLIC_CONFIG_KEYS = {"mode", "available", "sitekey", "signInUrl", "hosted", "widgetScriptUrl"}

SITE_KEY = "0x4AAAAAAF0T6-JuMDYB9gPf"
#: Deliberately distinctive strings, so a leak is unambiguous in the failure.
TURNSTILE_SECRET = "0xSECRETSECRETTURNSTILETOKEN"
CLIENT_SECRET = "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p"
CLIENT_ID = "hoja-client-id.apps.googleusercontent.com"
EXPECTED_HOST = "hoja.work"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _clear_signup_env(monkeypatch) -> None:
    for name in (
        MODE_ENV,
        SITE_KEY_ENV,
        "GOOGLE_REDIRECT_URI_ALLOWLIST",
        "HOJA_GOOGLE_HD",
        "HOJA_SIGNUP_IDENTITY_CAP",
        "HOJA_SIGNUP_IP_CAP",
        "HOJA_SIGNUP_IP_WINDOW_SECONDS",
        "HOJA_TENANT_REGISTRY",
        *REQUIRED_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def _configured_env(**overrides) -> dict[str, str]:
    env = {
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
        "GOOGLE_REDIRECT_URI": f"https://{EXPECTED_HOST}/auth/google/callback",
        "TURNSTILE_SECRET_KEY": TURNSTILE_SECRET,
        "TURNSTILE_EXPECTED_HOSTNAME": EXPECTED_HOST,
        SITE_KEY_ENV: SITE_KEY,
        MODE_ENV: "open",
    }
    env.update(overrides)
    return {name: value for name, value in env.items() if value is not None}


@pytest.fixture
def app_client(tmp_path, monkeypatch, request):
    """A real app + schema with exactly the environment a test asks for."""

    def build(env: dict[str, str]) -> TestClient:
        _clear_signup_env(monkeypatch)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        db_path = tmp_path / "signup-page.db"
        engine = make_engine(db_path)
        migrate(engine)
        app = create_app(str(db_path), open_mode=False)
        client = TestClient(app, base_url=f"http://{EXPECTED_HOST}")
        client.__enter__()
        request.addfinalizer(lambda: client.__exit__(None, None, None))
        return client

    return build


# --------------------------------------------------------------------------- #
# 1. the public configuration endpoint
# --------------------------------------------------------------------------- #


def test_public_config_is_exactly_the_public_fields(app_client):
    client = app_client(_configured_env())
    response = client.get("/api/signup/config")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PUBLIC_CONFIG_KEYS, "the endpoint grew a field nobody reviewed"
    assert body["mode"] == "open"
    assert body["hosted"] is False
    assert body["available"] is True
    assert body["sitekey"] == SITE_KEY
    assert body["signInUrl"] == "/auth/google/start?return_to=%2Fsignup"
    assert "render=explicit" in body["widgetScriptUrl"]
    assert response.headers["cache-control"] == "no-store"


def test_public_config_needs_no_cookie_and_starts_the_flow_on_this_host(app_client):
    client = app_client(_configured_env())
    anonymous = client.get("/api/signup/config")  # no session cookie of any kind
    assert anonymous.status_code == 200
    # The start URL is same-origin and returns to the page, which is what the
    # server's _safe_return_to accepts; nothing here is an absolute URL.
    assert anonymous.json()["signInUrl"].startswith("/auth/google/start?")
    assert "//" not in anonymous.json()["signInUrl"]


def test_public_config_leaks_no_secret(app_client):
    client = app_client(_configured_env())
    raw = client.get("/api/signup/config").text

    for secret in (TURNSTILE_SECRET, CLIENT_SECRET, CLIENT_ID, EXPECTED_HOST):
        assert secret not in raw, f"the public config leaked {secret!r}"
    assert "secret" not in raw.lower()
    assert "gocspx" not in raw.lower()
    # The site key is the one credential-shaped value that must be there.
    assert SITE_KEY in raw


def test_public_config_fails_closed_when_the_site_key_is_unset(app_client):
    """Configured credentials but no public key: the page must say "unavailable"."""
    client = app_client(_configured_env(**{SITE_KEY_ENV: None}))
    body = client.get("/api/signup/config").json()

    assert body["available"] is False, "a page with no site key would render a dead widget"
    assert body["sitekey"] is None
    assert body["mode"] == "open"
    # The Google half is still configured and is reported as such; only the
    # widget is missing, and `available` is what the page branches on.
    assert body["signInUrl"] is not None


def test_public_config_on_an_unconfigured_deployment(app_client):
    client = app_client({})
    body = client.get("/api/signup/config").json()

    assert body["mode"] == "closed"  # the fail-closed default
    assert body["hosted"] is False
    assert body["available"] is False
    assert body["sitekey"] is None
    assert body["signInUrl"] is None
    assert "render=explicit" in body["widgetScriptUrl"]


def test_a_hosted_tenant_exposes_its_delegated_google_sign_in(tmp_path, monkeypatch):
    _clear_signup_env(monkeypatch)
    monkeypatch.setenv("HOJA_SIGNUP_MODE", "closed")
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", "https://app.hoja.work")
    db_path = tmp_path / "tenant-signin.db"
    engine = make_engine(db_path)
    migrate(engine)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app, base_url="https://acme.hoja.work") as client:
        body = client.get("/api/signup/config").json()
    assert body["hosted"] is True
    assert body["mode"] == "closed"
    assert body["available"] is False
    assert body["signInUrl"] == "/auth/google/start?return_to=%2F"


def test_the_site_key_must_not_be_the_secret_key():
    """The site key ships to the browser, so this mistake is a startup error."""
    with pytest.raises(SignupConfigError) as caught:
        load_signup_config(_configured_env(**{SITE_KEY_ENV: TURNSTILE_SECRET}))
    assert SITE_KEY_ENV in str(caught.value)
    assert "TURNSTILE_SECRET_KEY" in str(caught.value)


def test_a_site_key_that_is_not_a_bare_token_is_refused():
    for bad in ("https://hoja.work/key", "0xAAAA AAAA", "a b"):
        with pytest.raises(SignupConfigError):
            load_signup_config(_configured_env(**{SITE_KEY_ENV: bad}))


def test_the_site_key_does_not_change_whether_the_gate_is_configured():
    """A public key must never be what enables the gate: the secret still is."""
    without = load_signup_config(_configured_env(**{SITE_KEY_ENV: None}))
    with_key = load_signup_config(_configured_env())
    assert without.configured is with_key.configured is True
    assert without.public_widget_available is False
    assert with_key.public_widget_available is True


# --------------------------------------------------------------------------- #
# 2. the reason-code contract
# --------------------------------------------------------------------------- #


def _reason_copy_codes() -> list[str]:
    """The keys of the page's REASON_COPY table, read from the TypeScript source."""
    source = (SIGNUP_SRC / "reasonCopy.ts").read_text(encoding="utf-8")
    start = source.index("export const REASON_COPY = {")
    end = source.index("} as const satisfies", start)
    block = source[start:end]
    # One entry per line, two-space indent: `  code_name: {`.
    codes = re.findall(r"^  ([a-z][a-z0-9_]*):\s*\{$", block, flags=re.MULTILINE)
    assert codes, "reasonCopy.ts parsed as empty; the table's shape changed"
    return codes


def test_the_page_maps_every_reason_the_server_can_return():
    page = _reason_copy_codes()
    assert len(page) == len(set(page)), "reasonCopy.ts has a duplicate key"
    server = set(SIGNUP_REASON_CODES)
    missing = sorted(server - set(page))
    extra = sorted(set(page) - server)
    assert not missing, (
        "the page has no copy for reason code(s) the server can return: "
        f"{missing}. Add them to frontend/src/signup/reasonCopy.ts.")
    assert not extra, (
        f"the page maps code(s) the server does not define: {extra}. "
        "Remove them or add them to signup_gate.SIGNUP_REASON_CODES.")


def test_the_server_reason_list_covers_the_route_level_refusals():
    assert "cross_site_request" in SIGNUP_REASON_CODES
    assert "invalid_request" in SIGNUP_REASON_CODES
    for required in ("not_authenticated", "signup_closed", "slug_taken", "turnstile_failed"):
        assert required in SIGNUP_REASON_CODES


def test_an_unknown_code_is_not_silently_mapped_by_the_page():
    """The table must not swallow codes it does not know (a generic catch-all)."""
    source = (SIGNUP_SRC / "reasonCopy.ts").read_text(encoding="utf-8")
    assert "describeReason" in source
    assert "hasOwnProperty.call(REASON_COPY, reason)" in source, (
        "describeReason must test membership, so an unknown code falls back to "
        "the server's message instead of a blank or a generic 'error occurred'")
    assert "an error occurred" not in source.lower()


# --------------------------------------------------------------------------- #
# 3. the slug rule: generated from the server, and behaviourally identical
# --------------------------------------------------------------------------- #


def _generator():
    path = ROOT / "scripts" / "generate-signup-slug-policy.py"
    spec = importlib.util.spec_from_file_location("generate_signup_slug_policy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generated_slug_policy_is_current():
    generator = _generator()
    committed = generator.TARGET.read_text(encoding="utf-8")
    assert committed == generator.render_module(), (
        "frontend/src/signup/slugPolicy.generated.ts is stale. The page's address "
        "rule comes from hoja_server/signup_policy.py; regenerate with "
        ".venv/bin/python scripts/generate-signup-slug-policy.py")


def test_the_generated_policy_really_contains_the_server_rule():
    text = (SIGNUP_SRC / "slugPolicy.generated.ts").read_text(encoding="utf-8")
    from hoja_server.signup_policy import RESERVED_SLUGS, SLUG_MAX_LENGTH, SLUG_RE

    assert json.dumps(SLUG_RE.pattern) in text
    assert f"SLUG_MAX_LENGTH = {SLUG_MAX_LENGTH}" in text
    for name in RESERVED_SLUGS:
        assert json.dumps(name) in text, f"reserved name {name!r} missing from the page's copy"
    # And it must be generated, not retyped by hand.
    assert "GENERATED FILE" in text
    assert "generate-signup-slug-policy.py" in text


SLUG_CORPUS: list[object] = [
    "", "   ", None, 123,
    "acme", "  acme  ", "Acme", "ACME", "a", "0", "9",
    "a" * 63, "a" * 64,
    "-acme", "acme-", "ac--me", "acme--", "--aa", "aa--",
    "xn--p1ai", "ac_me", "acme.work", "acme work",
    "www", "api", "admin", "dashboard", "traefik",
    "acme-studio", "a-b", "0-0", "ab-cd-ef", "z9", "nested-label",
]


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, so the page's TypeScript cannot run here")
    if not (FRONTEND / "node_modules" / "typescript" / "package.json").is_file():
        pytest.skip("frontend/node_modules/typescript is missing (run `cd frontend && npm ci`)")
    return node


def test_the_page_and_the_server_agree_on_every_slug():
    """One corpus, two implementations: the TypeScript result must equal Python's."""
    node = _node()
    script = ROOT / "scripts" / "test-signup-page.cjs"
    completed = subprocess.run(
        [node, str(script), "--slug-check"],
        input=json.dumps(SLUG_CORPUS),
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    page = json.loads(completed.stdout)

    expected = []
    for value in SLUG_CORPUS:
        try:
            validate_slug(value)
        except SlugRefused as refused:
            expected.append(refused.reason)
        else:
            expected.append(None)

    assert len(page) == len(SLUG_CORPUS)
    mismatches = [
        {"slug": case, "server": want, "page": got}
        for case, want, got in zip(SLUG_CORPUS, expected, page) if want != got
    ]
    assert not mismatches, f"page and server disagree: {mismatches}"


# --------------------------------------------------------------------------- #
# 4. the page's own logic: submit handler, reason copy, wiring, a11y
# --------------------------------------------------------------------------- #


def test_the_page_scripts_pass():
    """posts {slug, turnstile_token}; surfaces the refusal reason; copy for all codes."""
    node = _node()
    completed = subprocess.run(
        [node, str(ROOT / "scripts" / "test-signup-page.cjs")],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_spa_routes_signup_to_the_page():
    main = (FRONTEND / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "import {SignupPage} from './signup/SignupPage'" in main
    assert "location.pathname==='/signup'" in main, "the /signup route is not wired"
    # It must come before the auth gates, or an anonymous visitor is sent to /login.
    route_at = main.index("location.pathname==='/signup'")
    login_at = main.index("if(!token)return <Login")
    assert route_at < login_at, "/signup is behind the login gate"


def test_the_page_is_accessible_by_construction():
    page = (SIGNUP_SRC / "SignupPage.tsx").read_text(encoding="utf-8")
    # Labelled field.
    assert 'htmlFor="signup-slug"' in page
    assert 'id="signup-slug"' in page
    # Errors are announced, both the live field error and the refusal alert.
    assert 'aria-live="assertive"' in page
    assert 'role="alert"' in page
    assert 'aria-live="polite"' in page
    # The field says it is invalid, and points at its help text.
    assert "aria-invalid" in page
    assert "aria-describedby" in page
    # The submit button is disabled in flight and never hidden.
    assert "disabled={submitting}" in page
    # The three modes are distinguished.
    assert "mode === 'closed'" in page
    assert "Sign-ups are invite-only" in page
    assert "mode === 'invite'" in page
    assert "Sign-up is unavailable" in page


def test_the_page_never_retries_a_missing_token_on_the_client():
    """The token is never fabricated or re-used: the widget is reset instead."""
    page = (SIGNUP_SRC / "SignupPage.tsx").read_text(encoding="utf-8")
    assert "resetChallenge" in page
    assert "turnstile.remove" in page
    submit = (SIGNUP_SRC / "submitSignup.ts").read_text(encoding="utf-8")
    assert "turnstile_token" in submit
    assert "credentials: 'include'" in submit


def test_no_secret_can_reach_the_bundle():
    """The gate's own grep, encoded so it cannot be forgotten."""
    forbidden = re.compile(r"turnstile_secret|GOCSPX|client_secret|GOOGLE_CLIENT_SECRET", re.I)
    offenders = []
    for path in (FRONTEND / "src").rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".css", ".json"}:
            continue
        if forbidden.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"a secret name reached the bundle source: {offenders}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
