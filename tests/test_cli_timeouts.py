"""Guardrail tests for monotonic keep-alive and graceful-shutdown timeouts (HOJA-756)."""

from __future__ import annotations

from unittest.mock import patch

from hoja_server import cli


def test_serve_passes_monotonic_keepalive_and_graceful_shutdown(tmp_path):
    """hoja-serve serve must default to keep-alive=65s and graceful-shutdown=30s."""
    captured: dict = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    with (
        patch("uvicorn.run", side_effect=fake_run),
        patch("hoja_server.app.create_app", return_value=object()),
        patch("hoja_server.cli._warn_if_unbound"),
    ):
        cli.main(["serve", "--db", str(tmp_path / "t.db")])

    assert captured["timeout_keep_alive"] == 65
    assert captured["timeout_graceful_shutdown"] == 30
