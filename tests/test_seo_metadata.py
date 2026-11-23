from __future__ import annotations

from starlette.testclient import TestClient

from hoja_server import app as app_mod
from hoja_server.db import make_engine, migrate


def test_web_index_and_signup_serve_og_metadata_and_public_assets(tmp_path, monkeypatch):
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text(
        "<!doctype html><html><head>"
        "<!-- SEO:START --><title>Hoja</title><!-- SEO:END -->"
        "</head><body><div id=\"root\"></div></body></html>",
        encoding="utf-8",
    )
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    for name in ("og-image.png", "og-signup.png", "apple-touch-icon.png", "favicon-32x32.png", "icon-192.png"):
        (web_root / name).write_bytes(png_bytes)
    (web_root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (web_root / "site.webmanifest").write_text('{"name":"Hoja"}', encoding="utf-8")
    monkeypatch.setattr(app_mod, "_WEB_ROOT", web_root)

    db_file = tmp_path / "test.db"
    migrate(make_engine(str(db_file)))
    app = app_mod.create_app(db_path=str(db_file))
    with TestClient(app, base_url="https://app.hoja.work") as client:
        root_res = client.get("/")
        assert root_res.status_code == 200
        assert 'property="og:image" content="https://app.hoja.work/og-image.png"' in root_res.text
        assert 'name="twitter:card" content="summary_large_image"' in root_res.text

        signup_res = client.get("/signup")
        assert signup_res.status_code == 200
        assert 'property="og:image" content="https://app.hoja.work/og-signup.png"' in signup_res.text
        assert "<title>Request a workspace — Hoja Cloud</title>" in signup_res.text

        img_res = client.get("/og-image.png")
        assert img_res.status_code == 200
        assert img_res.headers["content-type"] == "image/png"
        assert img_res.content == png_bytes

        apple_res = client.get("/apple-touch-icon.png")
        assert apple_res.status_code == 200
        assert apple_res.headers["content-type"] == "image/png"

        robots_res = client.get("/robots.txt")
        assert robots_res.status_code == 200
        assert "Allow: /" in robots_res.text
