"""Signing out must let the next person choose a Google account (HOJA-206).

The report says "sign in again resumes the previous identity". There are two
mechanisms, and the issue only names the first:

1. ``authorization_url`` was never given ``prompt=select_account``, so Google
   silently reuses the browser's single signed-in account.
2. ``/auth/google/start`` returns a tenant handoff straight from the control
   session when one exists (``identity is not None and tenant_target is not
   None``), and ``/signin`` builds exactly that URL. The sign-in entry therefore
   never reaches Google at all, so adding ``prompt`` alone would not show a
   chooser on the reported path.

Both are pinned here. The harness is shared with the signup-gate tests so the
flow is the real one (start -> Google -> callback), faked only at HTTP.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from tests.test_signup_gate import Harness, _build_harness

EXPECTED_CONTROL_HOST = "localhost"
TENANT_ORIGIN = "https://acme.hoja.work"


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """A control plane on the harness host, plus one tenant origin to return to."""
    built = _build_harness(
        tmp_path,
        monkeypatch,
        env={
            "HOJA_CONTROL_PLANE_URL": f"https://{EXPECTED_CONTROL_HOST}",
            # Without this the resume path answers 503 instead of a tenant
            # redirect, which would hide the bug behind an unconfigured deploy.
            "HOJA_IDENTITY_TRANSFER_MASTER": "m" * 48,
            "HOJA_TENANT_BASE_DOMAIN": "hoja.work",
        },
    )
    yield built
    built.client.__exit__(None, None, None)


def _path_and_query(absolute: str) -> str:
    """TestClient is bound to one base_url; keep the path, drop the origin."""
    parts = urlsplit(absolute)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _assert_google_chooser(location: str) -> None:
    query = parse_qs(urlsplit(location).query)
    assert query.get("prompt") == ["select_account"], location
    assert query.get("response_type") == ["code"], location


def test_a_fresh_sign_in_asks_google_for_the_account_chooser(hosted: Harness) -> None:
    start = hosted.start_flow()
    assert start.status_code == 302, start.text
    _assert_google_chooser(start.headers["location"])


def test_continue_to_sign_in_does_not_resume_the_previous_identity(hosted: Harness) -> None:
    """The reported repro: sign in once, sign out, press Continue to sign in."""
    hosted.sign_in(subject="google-sub-1", email="one@example.test")

    entry = hosted.client.get(
        "/signin", params={"next": TENANT_ORIGIN},
        headers={"host": EXPECTED_CONTROL_HOST}, follow_redirects=False,
    )
    assert entry.status_code == 302, entry.text
    handoff = entry.headers["location"]
    assert "/auth/google/start" in handoff, handoff

    started = hosted.client.get(
        _path_and_query(handoff),
        headers={"host": EXPECTED_CONTROL_HOST}, follow_redirects=False,
    )
    assert started.status_code == 302, started.text
    location = started.headers["location"]
    assert "hoja.work" not in location, (
        "the control session resumed the previous identity instead of asking Google: "
        f"{location}"
    )
    _assert_google_chooser(location)


def test_logout_clears_both_session_cookie_families(hosted: Harness) -> None:
    hosted.sign_in(subject="google-sub-1", email="one@example.test")
    hosted.client.cookies.set("hoja_session", "workspace-session")
    response = hosted.client.post(
        "/auth/logout",
        headers={"host": EXPECTED_CONTROL_HOST, "origin": f"http://{EXPECTED_CONTROL_HOST}"},
    )
    assert response.status_code == 200, response.text
    cleared = [
        header for header in response.headers.get_list("set-cookie")
        if "Max-Age=0" in header
    ]
    names = " ".join(cleared)
    assert "hoja_session" in names, cleared
    assert "hoja_signup_session" in names, cleared
