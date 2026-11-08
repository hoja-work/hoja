"""The whole Google hop, end to end, on a tenant host.

Previous tests proved the pieces (the validator keeps the code, the store
accepts it). This one proves the wiring: the code leaves the workspace in a
cookie and comes back in the URL that the app actually reads.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from urllib.parse import quote

from sqlalchemy import select

from hoja_server import identity_transfer
from hoja_server.app import create_app
from hoja_server.db import (
    global_identity,
    make_engine,
    migrate,
    organization,
    organization_membership,
    user,
)
from hoja_server.store import Store

CONTROL = "https://app.hoja.work"
HOST = "hoja.hoja.work"
KEY = "tenant-transfer-key-long-enough-0123456789"
CODE = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_ab"
NOW = "2026-11-08T12:00:00+00:00"


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_CONTROL_PLANE_URL", CONTROL)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", KEY)
    path = tmp_path / "tenant.db"
    engine = make_engine(path)
    migrate(engine)
    with engine.begin() as conn:
        conn.execute(organization.insert().values(
            id="org-hoja", name="Hoja", url_key="hoja", created_at=NOW, updated_at=NOW))
        conn.execute(user.insert().values(
            id="u-luis", organization_id="org-hoja", name="Luis", display_name="Luis",
            email="luis@hoja.work", active=True, admin=True, timezone="UTC",
            created_at=NOW, updated_at=NOW))
        conn.execute(global_identity.insert().values(
            id="i-luis", email="luis@hoja.work", created_at=NOW))
        conn.execute(organization_membership.insert().values(
            id="m-luis", identity_id="i-luis", organization_id="org-hoja",
            profile_user_id="u-luis", active=True, created_at=NOW))
    store = Store(engine)
    member, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    live_code = store.create_membership_invitation("org-hoja", member["id"], "u-luis")

    from starlette.testclient import TestClient

    with TestClient(create_app(str(path), open_mode=False), base_url=f"https://{HOST}") as client:
        yield client, engine, live_code


def _token(email="kevin@hoja.work", subject="sub-kevin"):
    return identity_transfer.issue(
        key=KEY, audience=HOST, email=email, subject=subject,
        name="Kevin", email_verified=True)


def test_the_code_leaves_in_a_cookie_and_returns_in_the_url(tenant):
    client, _engine, _live = tenant

    started = client.get(f"/auth/google/start?invite={CODE}", follow_redirects=False)

    assert started.status_code == 302
    assert started.headers["location"].startswith(f"{CONTROL}/signin?next=")
    assert client.cookies.get("hoja_invite_pending") == CODE
    header = started.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=lax" in header
    assert "max-age=900" in header

    finished = client.get(
        f"/auth/google/finish?t={quote(_token(), safe='')}", follow_redirects=False)

    assert finished.status_code == 302
    assert finished.headers["location"] == f"/?invite={CODE}"
    assert not client.cookies.get("hoja_invite_pending")


def test_a_round_trip_without_an_invitation_lands_on_the_plain_root(tenant):
    client, _engine, _live = tenant

    finished = client.get(
        f"/auth/google/finish?t={quote(_token(), safe='')}", follow_redirects=False)

    assert finished.status_code == 302
    assert finished.headers["location"] == "/"


def test_the_cookie_is_not_accepted_in_a_shape_we_never_issue(tenant):
    client, _engine, _live = tenant
    client.cookies.set("hoja_invite_pending", "not-a-code")

    finished = client.get(
        f"/auth/google/finish?t={quote(_token(), safe='')}", follow_redirects=False)

    assert finished.headers["location"] == "/"


def test_the_probe_is_rate_limited_but_never_hides_a_live_invitation(tenant):
    client, _engine, live_code = tenant
    first = client.post("/auth/invite/probe", json={"code": "z" * 43})
    assert first.status_code == 404
    for _ in range(25):
        client.post("/auth/invite/probe", json={"code": "z" * 43})

    blocked = client.post("/auth/invite/probe", json={"code": "z" * 43})
    assert blocked.status_code == 429

    # Over budget or not, the holder of a real invitation gets an answer.
    allowed = client.post("/auth/invite/probe", json={"code": live_code})
    assert allowed.status_code == 200
    assert allowed.json()["valid"] is True
    assert allowed.json()["workspace"]["name"] == "Hoja"
