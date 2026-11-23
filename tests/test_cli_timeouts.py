"""Guardrail tests for monotonic keep-alive and graceful-shutdown timeouts (HOJA-756)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import yaml

from hoja_server import cli


def _parse_seconds(raw: str | int) -> int:
    if isinstance(raw, int):
        return raw
    m = re.fullmatch(r"(\d+)s", str(raw).strip())
    assert m, f"expected '<N>s' duration, got {raw!r}"
    return int(m.group(1))


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


def test_traefik_and_uvicorn_timeouts_are_strictly_monotonic():
    """Enforce Cloudflare(900s) < Traefik entryPoint and Traefik backend < Uvicorn(65s)."""
    traefik_path = Path(__file__).resolve().parents[2] / "infra" / "prod" / "traefik.yml"
    cfg = yaml.safe_load(traefik_path.read_text())

    entry_idle = _parse_seconds(
        cfg["entryPoints"]["websecure"]["transport"]["respondingTimeouts"]["idleTimeout"]
    )
    backend_idle = _parse_seconds(
        cfg["serversTransport"]["forwardingTimeouts"]["idleConnTimeout"]
    )
    buf_size = cfg["accessLog"]["bufferingSize"]

    assert entry_idle > 900, f"Traefik entryPoint idleTimeout ({entry_idle}s) must exceed Cloudflare 900s"
    assert backend_idle < 65, f"Traefik backend idleConnTimeout ({backend_idle}s) must be below Uvicorn 65s"
    assert buf_size == 0, f"Traefik accessLog bufferingSize ({buf_size}) must be 0 so 5xx flush immediately"
