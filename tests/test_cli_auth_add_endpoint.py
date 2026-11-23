"""`hoja auth add` must never send a pasted token to an endpoint nobody named."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from hoja.commands import auth_cmd


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HOJA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOJA_API_URL", raising=False)
    monkeypatch.delenv("LINEAR_API_URL", raising=False)
    calls: list[str] = []

    async def fake_fetch(token: str, base_url: str):
        calls.append(base_url)
        return "Acme"

    monkeypatch.setattr(auth_cmd, "_fetch_org_name", fake_fetch)
    saved = {}
    monkeypatch.setattr(auth_cmd, "save_config", lambda cfg: saved.setdefault("cfg", cfg))
    monkeypatch.setattr(auth_cmd, "load_config", _empty)
    return calls, saved


def _empty():
    from hoja.config import Config
    return Config()


def _run(*args):
    return CliRunner().invoke(auth_cmd.auth_app, ["add", "work", "--token", "secret-token", *args])


def test_without_a_url_the_token_is_not_sent_anywhere(isolated):
    calls, saved = isolated
    result = _run()
    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Not verified" in result.output
    assert saved["cfg"].accounts["work"].base_url is None


def test_the_named_url_is_used_for_verification_and_stored(isolated):
    calls, saved = isolated
    result = _run("--api-url", "https://acme.hoja.work/graphql")
    assert result.exit_code == 0, result.output
    assert calls == ["https://acme.hoja.work/graphql"]
    assert saved["cfg"].accounts["work"].base_url == "https://acme.hoja.work/graphql"


def test_hoja_api_url_env_is_honoured(isolated, monkeypatch):
    calls, _saved = isolated
    monkeypatch.setenv("HOJA_API_URL", "https://env.hoja.work/graphql")
    assert _run().exit_code == 0
    assert calls == ["https://env.hoja.work/graphql"]


@pytest.mark.parametrize("url", ["acme.hoja.work/graphql", "http://acme.hoja.work/graphql"])
def test_a_url_without_https_is_refused(isolated, url):
    calls, _saved = isolated
    result = _run("--api-url", url)
    assert result.exit_code != 0
    assert calls == []


def test_plain_http_to_loopback_is_allowed(isolated):
    calls, _saved = isolated
    assert _run("--api-url", "http://127.0.0.1:8787/graphql").exit_code == 0
    assert calls == ["http://127.0.0.1:8787/graphql"]
