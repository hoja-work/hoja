"""The handoff as the browser drives it: tenant start, control plane, tenant finish.

These use the real ASGI app, the real schema and the real cookies. What is
asserted is the part that would be a security bug if wrong: a token minted for
one workspace cannot sign anyone in on another, and a verified address that is
not a member is refused rather than quietly given an account.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import identity_transfer
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, organization, user
from hoja_server.signup_config import SignupConfig

KEY = "tenant-handoff-key-that-is-long-enough-123"
TENANT = "acme.hoja.work"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", "https://app.hoja.work")
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", KEY)
    db_path = tmp_path / "app.db"
    engine = make_engine(db_path)
    migrate(engine)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app, base_url=f"https://{TENANT}") as c:
        yield c, engine


def _token(**over):
    kwargs = dict(key=KEY, audience=TENANT, email="luis@hoja.work", subject="sub-1",
                  name="Luis", email_verified=True)
    kwargs.update(over)
    return identity_transfer.issue(**kwargs)


def test_the_config_object_carries_the_handoff_settings(client):
    from hoja_server.signup_config import load_signup_config
    cfg = load_signup_config({"HOJA_CONTROL_PLANE_URL": "https://app.hoja.work",
                              "HOJA_IDENTITY_TRANSFER_KEY": KEY})
    assert isinstance(cfg, SignupConfig)
    assert cfg.control_plane_url == "https://app.hoja.work"
    assert cfg.identity_transfer_key == KEY


def test_a_tenant_defers_sign_in_to_the_control_plane(client):
    c, _ = client
    r = c.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == (
        "https://app.hoja.work/auth/google/start"
        "?return_to=https%3A%2F%2Facme.hoja.work%2F"
    )


def test_a_handoff_token_signs_the_first_user_in_as_admin(client):
    c, engine = client
    r = c.get(f"/auth/google/finish?t={_token()}", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "hoja_session" in r.headers.get("set-cookie", "")
    with engine.connect() as conn:
        rows = conn.execute(select(user.c.email, user.c.admin)).all()
        assert [(x[0], bool(x[1])) for x in rows] == [("luis@hoja.work", True)]
        assert conn.execute(select(organization.c.url_key)).first()[0] == "acme"


def test_a_token_for_another_workspace_cannot_sign_in_here(client):
    c, engine = client
    r = c.get(f"/auth/google/finish?t={_token(audience='other.hoja.work')}",
              follow_redirects=False)
    assert r.status_code == 400
    assert b"wrong_audience" in r.content
    with engine.connect() as conn:
        assert conn.execute(select(user.c.id)).first() is None


def test_an_uninvited_address_is_refused_even_with_a_valid_token(client):
    c, engine = client
    assert c.get(f"/auth/google/finish?t={_token()}", follow_redirects=False).status_code == 302
    r = c.get(f"/auth/google/finish?t={_token(email='stranger@elsewhere.test', subject='s2')}",
              follow_redirects=False)
    assert r.status_code == 403
    assert b"not_invited" in r.content
    with engine.connect() as conn:
        assert conn.execute(select(user.c.email)).all() == [("luis@hoja.work",)]


def test_no_transfer_key_means_no_way_in(client, tmp_path, monkeypatch):
    monkeypatch.delenv("HOJA_IDENTITY_TRANSFER_KEY", raising=False)
    db_path = tmp_path / "bare.db"
    engine = make_engine(db_path)
    migrate(engine)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app, base_url=f"https://{TENANT}") as c:
        r = c.get(f"/auth/google/finish?t=whatever", follow_redirects=False)
        assert r.status_code == 503
        assert b"transfer_unconfigured" in r.content
