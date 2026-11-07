"""Auth telemetry, and one sign-out that actually ends the session.

Two facts are pinned here, both of which were invisible in production before:

1. The funnel events already existed in the code (``hoja_server.security``) but no
   logger was ever configured, so every ``info`` record was discarded *before*
   formatting. ``--log-level`` cannot fix that: uvicorn configures the
   ``uvicorn.*`` loggers and leaves the root logger at WARNING. The application
   logger has to be enabled separately, and what it writes has to be redacted.
2. Signing out cleared one of two independent session families. A live
   ``hoja_signup_session`` makes ``/signup`` read the browser as
   ``google_verified``; when that person already has an active workspace the page
   redirects them straight back into it. The browser looked signed out and then
   signed itself back in — the "sign out doesn't kick me out" report.
"""

from __future__ import annotations

import io
import logging
import logging.config
import copy

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant
from hoja_server.logging_setup import REDACTED, configure_logging, redact

PASSWORD = "correct horse battery"
EMAIL = "telemetry@example.test"


def _signed_in_client(tmp_path):
    db_path = tmp_path / "telemetry.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine,
        org_name="Telemetry",
        org_url_key="telemetry",
        user_name="Telemetry User",
        user_email=EMAIL,
        demo_issues=False,
    )
    client = TestClient(create_app(str(db_path), open_mode=False))
    assert client.post(
        "/auth/setup", json={"token": seeded["token"], "password": PASSWORD}
    ).status_code == 201
    assert client.post(
        "/auth/login", json={"email": EMAIL, "password": PASSWORD}
    ).status_code == 200
    return client


def test_sign_out_ends_both_session_families(tmp_path):
    """A workspace session and a sign-up session share one browser."""
    client = _signed_in_client(tmp_path)
    workspace_cookie = client.cookies.get("hoja_session")
    assert workspace_cookie, "the login must have issued a workspace session"

    # Both families in one browser, stated explicitly so the test does not depend
    # on how the client's cookie jar scopes domains.
    response = client.post(
        "/auth/logout",
        headers={
            "cookie": f"hoja_session={workspace_cookie}; "
                      f"hoja_signup_session=signup-session-value"
        },
    )

    assert response.status_code == 200
    cookies = " | ".join(response.headers.get_list("set-cookie")).lower()
    assert "hoja_session=" in cookies
    assert "hoja_signup_session=" in cookies, "the sign-up session survived sign-out"
    assert "max-age=0" in cookies
    assert set(response.json()["cleared"]) == {"workspace", "signup"}

    # Nothing is left that can sign the browser back in.
    assert client.get("/auth/me").status_code == 401
    assert client.get("/api/signup").json()["googleVerified"] is False


@pytest.mark.parametrize(
    "text,secret",
    [
        ("GET /auth/google/finish?t=eyJhbGciOi.SECRET", "eyJhbGciOi.SECRET"),
        ("GET /auth/google/callback?code=abc123&state=xyz", "abc123"),
        ("GET /auth/google/callback?code=abc123&state=xyz", "xyz"),
        ("cookie hoja_session=rawtokenvalue", "rawtokenvalue"),
        ("cookie hoja_signup_session=rawsignup", "rawsignup"),
        ("person@example.test signed in", "person@example.test"),
    ],
)
def test_redact_removes_credentials(text, secret):
    cleaned = redact(text)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_leaves_ordinary_fields_alone():
    line = "auth.start host=app.hoja.work control_session=True tenant_target=acme.hoja.work"
    assert redact(line) == line


def test_redaction_keeps_the_funnel_state_visible():
    """The funnel step must survive redaction; the OAuth parameter must not.

    ``signup_state`` and ``state`` are different fields that share a suffix — the
    first is the funnel step, the second is an OAuth CSRF parameter. A redactor
    that matches the suffix blanks the telemetry it was added to protect.
    """
    decision = "auth.signup.decision signup_state=google_verified reason=turnstile_missing status=403"
    assert redact(decision) == decision

    callback = "GET /auth/google/callback?code=abc123&state=xyz"
    cleaned = redact(callback)
    assert "abc123" not in cleaned and "xyz" not in cleaned


def test_configured_logging_emits_info_and_redacts_the_handoff_ticket():
    """The event that was invisible emits, and the ticket never reaches the line."""
    stream = io.StringIO()
    experiment = logging.getLogger("hoja_server.security")
    previous, previous_propagate = experiment.level, experiment.propagate
    try:
        experiment.setLevel(logging.NOTSET)
        configure_logging("info", stream=stream)
        experiment.info(
            "auth.handoff source=control target_host=acme.hoja.work ticket=%s",
            "SUPERSECRETTICKET",
        )
        line = stream.getvalue()
    finally:
        for handler in list(logging.getLogger("hoja_server").handlers):
            logging.getLogger("hoja_server").removeHandler(handler)
        experiment.setLevel(previous)
        experiment.propagate = previous_propagate

    assert "auth.handoff" in line
    assert "INFO" in line
    assert "acme.hoja.work" in line, "redaction must not eat ordinary fields"
    assert "SUPERSECRETTICKET" not in line


def test_uvicorn_log_level_cannot_enable_the_application_loggers():
    """Pins the root cause: raising --log-level was never going to work.

    uvicorn's log config touches ``uvicorn``, ``uvicorn.error`` and
    ``uvicorn.access`` and leaves the root logger at WARNING, so a
    ``hoja_server.security`` record never reaches a formatter.
    """
    import uvicorn.config

    app_logger = logging.getLogger("hoja_server")
    root = logging.getLogger()
    saved = (app_logger.level, app_logger.propagate, root.level)
    try:
        app_logger.setLevel(logging.NOTSET)
        app_logger.propagate = True
        root.setLevel(logging.WARNING)
        config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            config["loggers"][name]["level"] = "INFO"
        logging.config.dictConfig(config)

        assert app_logger.getEffectiveLevel() == logging.WARNING
        assert app_logger.isEnabledFor(logging.INFO) is False
    finally:
        app_logger.setLevel(saved[0])
        app_logger.propagate = saved[1]
        root.setLevel(saved[2])
