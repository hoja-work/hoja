"""Inviting people by email.

Three separate things decide whether an invitation is emailed, and only one of
them is code: whether the deployment has a mailer, whether the address is
present, and whether the link names the right workspace. These tests pin the two
the code owns, and record the one it does not.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select

from hoja_server import mail_config, mail_events
from hoja_server import mail_outbox as outbox
from hoja_server.db import mail_outbox, make_engine, migrate

APP_URL = "https://hoja.hoja.work"
CODE = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_ab"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "mail.db")
    migrate(eng)
    return eng


def _invite(engine):
    return mail_events.send_invite(
        engine, organization_id="org-hoja", organization_name="Hoja",
        inviter_name="Luis", invitee_name="Kevin",
        invitee_email="kevin@hoja.work", code=CODE)


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(mail_outbox)).mappings().all()


def test_the_invitation_link_names_the_workspace_that_issued_it(monkeypatch):
    monkeypatch.setenv("HOJA_MAIL_APP_URL", APP_URL)
    monkeypatch.setenv("HOJA_MAIL_TRANSPORT", "smtp")
    monkeypatch.setenv("HOJA_MAIL_FROM", "invites@hoja.work")
    monkeypatch.setenv("HOJA_MAIL_SMTP_HOST", "smtp.example.test")
    mail_config.reset_cache()

    config = mail_config.load()

    assert config.invitation_url(CODE) == f"{APP_URL}/#invite={CODE}"
    assert "app.hoja.work" not in config.invitation_url(CODE)


def test_with_no_mailer_the_invitation_is_not_claimed_as_sent(engine, monkeypatch):
    """The screen says "send this link yourself"; nothing may contradict it."""
    monkeypatch.setenv("HOJA_MAIL_APP_URL", APP_URL)
    monkeypatch.delenv("HOJA_MAIL_TRANSPORT", raising=False)
    mail_config.reset_cache()

    assert _invite(engine) == "not_configured"
    assert mail_config.load().configured is False
    # Recorded, not fixed: with no mailer nothing is queued for later either.
    assert _rows(engine) == []


def test_a_configured_mailer_is_recognised(monkeypatch):
    monkeypatch.setenv("HOJA_MAIL_APP_URL", APP_URL)
    monkeypatch.setenv("HOJA_MAIL_TRANSPORT", "smtp")
    monkeypatch.setenv("HOJA_MAIL_FROM", "invites@hoja.work")
    monkeypatch.setenv("HOJA_MAIL_SMTP_HOST", "smtp.example.test")
    mail_config.reset_cache()

    config = mail_config.load()

    assert config.configured is True
    assert config.invitation_url(CODE).startswith(APP_URL)


def test_the_outbox_is_idempotent_per_invitation(engine):
    """A retried send must not become a second email in someone's inbox."""
    payload = {"invite_url": f"{APP_URL}/#invite={CODE}"}
    kwargs = dict(template="invite", recipient="kevin@hoja.work", payload=payload)

    first = outbox.enqueue(engine, idempotency_key=f"invite:{CODE}", **kwargs)
    second = outbox.enqueue(engine, idempotency_key=f"invite:{CODE}", **kwargs)
    other = outbox.enqueue(engine, idempotency_key=f"invite:{'Z' * 43}", **kwargs)

    assert first is not None
    assert second is None
    assert other is not None
    rows = _rows(engine)
    assert len(rows) == 2
    assert all(row["status"] == "queued" for row in rows)
