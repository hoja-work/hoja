"""Browser e2e fixtures: a real hoja-serve process on a fresh database."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("sqlalchemy")

from playwright.sync_api import sync_playwright  # noqa: E402

from hoja_server.db import make_engine, migrate, seed_tenant  # noqa: E402
from tests import rls_mode  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "e2e-realtime-token"
OTHER_TOKENS: dict[str, str] = {}
PASSWORD = "e2e-realtime-password-123"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _database_url(tmp_path: Path) -> str:
    """SQLite per test by default; a fresh Postgres database per test when configured."""
    postgres = os.environ.get("HOJA_TEST_POSTGRES_URL")
    if not postgres:
        return f"sqlite:///{tmp_path / 'e2e.db'}"
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    admin = make_engine(make_url(postgres).set(database="postgres").render_as_string(hide_password=False))
    name = f"hoja_e2e_{uuid.uuid4().hex[:12]}"
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return make_url(postgres).set(database=name).render_as_string(hide_password=False)


def _drop_postgres(url: str) -> None:
    if not url.startswith("postgresql"):
        return
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    admin = make_engine(parsed.set(database="postgres").render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def server(tmp_path):
    url = _database_url(tmp_path)
    engine = make_engine(url)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="E2E", org_url_key="e2e", user_email="e2e@example.test",
                         token=TOKEN, demo_issues=False)
    from hoja_server.store import Store

    store = Store(engine)
    store.set_password(seeded["token"], PASSWORD)
    other_user, other_token = store.invite_member(
        seeded["org_id"], "Other Actor", "other@example.test", team_ids=[seeded["team_id"]]
    )
    OTHER_TOKENS["token"] = other_token
    engine.dispose()
    app_url = rls_mode.restrict(url) if rls_mode.ENABLED and url.startswith("postgresql") else url
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "hoja_server.cli", "serve", "--database-url", app_url,
         "--port", str(port), "--host", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/ready", timeout=2) as resp:
                if resp.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("hoja-serve did not become ready")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    if app_url != url:
        rls_mode.drop_role(url, app_url)
    _drop_postgres(url)


def _graphql(base: str, token: str, query: str, variables: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{base}/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    body = json.loads(urllib.request.urlopen(request, timeout=10).read())
    assert "errors" not in body, body
    return body["data"]


@pytest.fixture
def gql(server):
    def call(query: str, variables: dict | None = None) -> dict:
        return _graphql(server, TOKEN, query, variables)

    return call


@pytest.fixture
def other_gql(server):
    """GraphQL client authenticated as the second workspace member."""
    def call(query: str, variables: dict | None = None) -> dict:
        return _graphql(server, OTHER_TOKENS["token"], query, variables)

    return call


def _sign_in(page, base: str) -> None:
    """Log in through the real form so the browser receives its session cookie."""
    page.goto(base + "/")
    page.locator("input[type=email]").first.wait_for(timeout=15000)
    page.locator("input[type=email]").first.fill("e2e@example.test")
    page.locator("input[type=password]").first.fill(PASSWORD)
    page.locator("button.primary", has_text="Continue").first.click()
    page.wait_for_load_state("networkidle")


@pytest.fixture
def browser_pair(server):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # Chromium not installed locally
            pytest.skip(f"Chromium unavailable: {exc}")
        contexts = [browser.new_context(), browser.new_context()]
        pages = [ctx.new_page() for ctx in contexts]
        for page in pages:
            _sign_in(page, server)
        yield server, pages
        browser.close()
