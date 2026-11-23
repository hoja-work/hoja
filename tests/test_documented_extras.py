"""The documented install (`--extra dev --extra server`) must actually serve GraphQL.

The MCP endpoint is mounted by the server, so the `server` extra carries the MCP
SDK. The agent chat panel is genuinely optional: serving the API must not import
`agent_chat`, and the endpoint must name the missing extra instead of raising a
bare ImportError.
"""

from __future__ import annotations

import builtins
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import app as app_module
from hoja_server.db import make_engine, migrate, seed_tenant

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

AGENT_PACKAGES = ("openai", "fastmcp", "pydantic_ai")
VIEWER = {"query": "{ viewer { name } }"}


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")


def _setup(tmp_path):
    db_path = tmp_path / "extras.db"
    engine = make_engine(db_path)
    migrate(engine)
    admin = seed_tenant(engine, org_name="Extras Org", org_url_key="extras-org",
                        user_email="owner@extras.test", token="extras-token", demo_issues=False)
    app = app_module.create_app(None, database_url=str(db_path), open_mode=False)
    return app, admin


@pytest.fixture
def without_agent_extra(monkeypatch):
    """Make the optional agent packages unimportable for the duration of a test."""
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in AGENT_PACKAGES:
            raise ModuleNotFoundError(f"No module named {root!r}", name=root)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for module in [m for m in sys.modules
                   if m == "hoja_server.agent_chat" or m.startswith("hoja_server.agent_chat.")]:
        monkeypatch.delitem(sys.modules, module)


def test_server_extra_declares_mcp() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text())["project"]["optional-dependencies"]
    names = {re.split(r"[^A-Za-z0-9_.-]", spec, maxsplit=1)[0] for spec in extras["server"]}
    assert "mcp" in names


def test_missing_packages_reports_absent_name() -> None:
    assert app_module._missing_packages(("definitely-not-a-real-package",)) == [
        "definitely-not-a-real-package"
    ]
    assert app_module._missing_packages(("json",)) == []


def test_graphql_serves_without_the_agent_extra(tmp_path, without_agent_extra) -> None:
    app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        res = client.post("/graphql", json=VIEWER,
                          headers={"Authorization": f"Bearer {admin['token']}"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["viewer"] is not None


def test_agent_chat_names_the_missing_extra(tmp_path, monkeypatch) -> None:
    app, admin = _setup(tmp_path)
    real_missing = app_module._missing_packages

    def missing(names):
        if tuple(names) == app_module._AGENT_REQUIRED_PACKAGES:
            return ["openai"]
        return real_missing(names)

    monkeypatch.setattr(app_module, "_missing_packages", missing)
    with TestClient(app) as client:
        res = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                          headers={"Authorization": f"Bearer {admin['token']}"})
    assert res.status_code == 503
    assert "'agent' extra" in res.json()["error"]
    assert "hoja-cli[server,agent]" in res.json()["error"]


def test_startup_error_names_the_server_extra(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "extras.db"
    make_engine(db_path).dispose()
    monkeypatch.setattr(app_module, "_missing_packages", lambda names: list(names))
    with pytest.raises(RuntimeError, match=r"hoja-cli\[server\]"):
        app_module.create_app(None, database_url=str(db_path), open_mode=False)
