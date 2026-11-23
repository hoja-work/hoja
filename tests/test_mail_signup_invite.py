"""Email that leaves Hoja: template contracts, idempotency, and invite wiring.

The transport is mocked here on purpose -- this file proves *what would be
sent* and *that it is sent at most once*. Whether a real receiving MTA accepts
it is a separate, external check (``scripts/mail-doctor.py``).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server import db, mail_config, mail_outbox, mail_templates
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

CF_ENV = {
    "HOJA_MAIL_TRANSPORT": "cloudflare",
    "HOJA_MAIL_FROM": "notifications@hoja.work",
    "HOJA_MAIL_REPLY_TO": "support@hoja.work",
    "HOJA_MAIL_APP_URL": "https://hoja.work",
    "CLOUDFLARE_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
    "CLOUDFLARE_EMAIL_API_TOKEN": "cf-email-token-value-0123456789",
}

INVITE = """
mutation($input: MemberInviteInput!) {
  memberInvite(input: $input) { success user { id name email } }
}
"""
CREATE_INVITE_CODE = """
mutation($id: String!) {
  membershipInvitationCreate(userId: $id) { success inviteCode emailQueued }
}
"""


# ---- templates -------------------------------------------------------------

def test_invite_renders_text_and_html_with_the_same_link() -> None:
    rendered = mail_templates.render(
        "invite",
        invitee_name="Ada Lovelace",
        inviter_name="Luis Rincon",
        workspace_name="Analytical Engines",
        invite_url="https://hoja.work/#invite=tok_123",
    )
    assert rendered.subject == "Luis Rincon invited you to Analytical Engines on Hoja"
    assert "https://hoja.work/#invite=tok_123" in rendered.text
    assert 'href="https://hoja.work/#invite=tok_123"' in rendered.html
    assert "72 hours" in rendered.text and "72 hours" in rendered.html
    # The text part is a real alternative, not an empty stub.
    assert "Ada Lovelace" in rendered.text
    assert "<html" not in rendered.text


def test_template_escapes_user_input_in_html_but_not_in_text() -> None:
    hostile = '"><script>alert(1)</script>'
    rendered = mail_templates.render(
        "invite",
        invitee_name="Ada",
        inviter_name=hostile,
        workspace_name="Analytical Engines",
        invite_url="https://hoja.work/#invite=tok",
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "/relative", "https://u:p@host/x"])
def test_template_refuses_non_http_or_credentialed_links(bad: str) -> None:
    with pytest.raises(mail_templates.MailTemplateError):
        mail_templates.render(
            "welcome",
            name="Ada",
            workspace_name="Engines",
            app_url="https://hoja.work",
            workspace_url=bad,
        )


def test_smtp_refusal_exposes_the_code_but_not_the_server_text() -> None:
    import smtplib
    from unittest import mock

    from hoja_server.email_adapter import SMTPConfig, send_email

    config = SMTPConfig(host="smtp.example.test", port=2525, starttls=False)
    with mock.patch("hoja_server.email_adapter.smtplib.SMTP") as smtp:
        client = smtp.return_value.__enter__.return_value
        client.send_message.side_effect = smtplib.SMTPResponseException(
            550, b"5.7.1 Email sending is not enabled for domain hoja.work")
        with pytest.raises(Exception) as caught:
            send_email(config, sender="a@hoja.work", recipients=["b@hoja.work"],
                       subject="s", text="t")
    message = str(caught.value)
    assert "550" in message
    assert "not enabled for domain" not in message


def _doctor():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "mail-doctor.py"
    spec = importlib.util.spec_from_file_location("mail_doctor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dmarc_parser_reads_tags_past_the_first_one(monkeypatch) -> None:
    """Regression: tag keys keep a leading space after a ';', which once made a
    valid p=none record read as p=missing."""
    doctor = _doctor()
    monkeypatch.setattr(doctor, "dns", lambda name, rr_type: [
        "v=DMARC1; p=none; rua=mailto:dmarc@hoja.work; fo=1"])
    status, detail, notes = doctor.check_dmarc("hoja.work")
    assert status == doctor.WARN
    assert "p=none" in " ".join(notes)
    assert not any("no rua" in note for note in notes)


def test_dmarc_parser_rejects_an_invalid_policy(monkeypatch) -> None:
    doctor = _doctor()
    monkeypatch.setattr(doctor, "dns", lambda name, rr_type: [
        "v=DMARC1; p=maybe; rua=mailto:dmarc@hoja.work"])
    status, _detail, _notes = doctor.check_dmarc("hoja.work")
    assert status == doctor.FAIL


def test_unknown_template_is_an_error() -> None:
    with pytest.raises(mail_templates.MailTemplateError):
        mail_templates.render("password_reset", name="Ada")


# ---- configuration ---------------------------------------------------------

def test_disabled_by_default_and_sends_nothing() -> None:
    config = mail_config.load({})
    assert config.transport == "disabled"
    assert config.configured is False


def test_cloudflare_config_requires_a_token_and_a_domain_on_hoja_work() -> None:
    with pytest.raises(mail_config.MailConfigError):
        mail_config.load({**CF_ENV, "CLOUDFLARE_EMAIL_API_TOKEN": ""})
    with pytest.raises(mail_config.MailConfigError):
        mail_config.load({**CF_ENV, "HOJA_MAIL_FROM": "notifications@hoja-works.example"})
    with pytest.raises(mail_config.MailConfigError):
        mail_config.load({**CF_ENV, "HOJA_MAIL_APP_URL": "http://hoja.work"})


def test_config_description_never_contains_the_token() -> None:
    config = mail_config.load(CF_ENV)
    described = json.dumps(config.describe())
    assert CF_ENV["CLOUDFLARE_EMAIL_API_TOKEN"] not in described
    assert "cf-email-token-value" not in described
    assert repr(config).find(CF_ENV["CLOUDFLARE_EMAIL_API_TOKEN"]) == -1


def test_half_configured_smtp_is_a_startup_error() -> None:
    with pytest.raises(mail_config.MailConfigError):
        mail_config.load({
            "HOJA_MAIL_TRANSPORT": "smtp",
            "HOJA_MAIL_FROM": "notifications@hoja.work",
            "HOJA_MAIL_APP_URL": "https://hoja.work",
            "HOJA_MAIL_SMTP_HOST": "smtp.gmail.com",
            "HOJA_MAIL_SMTP_USERNAME": "only-a-username",
        })


# ---- outbox ----------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "mail.db")
    migrate(eng)
    return eng


def _enqueue(engine, key="invite:one"):
    return mail_outbox.enqueue(
        engine,
        template="invite",
        recipient="Teammate@Example.test",
        payload={
            "invitee_name": "Teammate",
            "inviter_name": "Luis",
            "workspace_name": "Hoja",
            "invite_url": "https://hoja.work/#invite=tok",
        },
        idempotency_key=key,
    )


def test_enqueue_is_idempotent_and_normalises_the_recipient(engine) -> None:
    first = _enqueue(engine)
    second = _enqueue(engine)
    assert first is not None and second is None
    with engine.connect() as conn:
        rows = list(conn.execute(select(db.mail_outbox)).mappings())
    assert len(rows) == 1
    assert rows[0]["recipient"] == "teammate@example.test"
    assert rows[0]["status"] == "queued"


def test_delivery_marks_sent_once_and_records_the_provider_id(engine, monkeypatch) -> None:
    config = mail_config.load(CF_ENV)
    calls: list[str] = []

    def fake_send(_config, *, to, rendered):
        calls.append(to)
        assert "invite=tok" in rendered.html
        return "provider-msg-1"

    monkeypatch.setattr(mail_outbox, "send_rendered", fake_send)
    _enqueue(engine)
    assert mail_outbox.deliver_pending(engine, config) == {"claimed": 1, "sent": 1, "failed": 0}
    assert mail_outbox.deliver_pending(engine, config) == {"claimed": 0, "sent": 0, "failed": 0}
    assert calls == ["teammate@example.test"]
    with engine.connect() as conn:
        row = conn.execute(select(db.mail_outbox)).mappings().first()
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "provider-msg-1"
    assert row["attempts"] == 1


def test_failed_delivery_stays_queued_without_leaking_the_token(engine, monkeypatch) -> None:
    config = mail_config.load(CF_ENV)

    def fail(_config, *, to, rendered):
        raise mail_outbox.MailDeliveryError("cloudflare rejected the message: 10102 forbidden")

    monkeypatch.setattr(mail_outbox, "send_rendered", fail)
    _enqueue(engine)
    assert mail_outbox.deliver_pending(engine, config)["failed"] == 1
    with engine.connect() as conn:
        row = conn.execute(select(db.mail_outbox)).mappings().first()
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert "10102" in row["last_error"]
    assert CF_ENV["CLOUDFLARE_EMAIL_API_TOKEN"] not in (row["last_error"] or "")


def test_mail_without_configuration_is_kept_for_a_later_drain(engine) -> None:
    _enqueue(engine)
    assert mail_outbox.deliver_pending(engine, mail_config.load({}))["claimed"] == 0
    with engine.connect() as conn:
        assert conn.execute(select(db.mail_outbox)).mappings().first()["status"] == "queued"


# ---- invite wiring end to end ---------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_MAIL_TRANSPORT", CF_ENV["HOJA_MAIL_TRANSPORT"])
    monkeypatch.setenv("HOJA_MAIL_FROM", CF_ENV["HOJA_MAIL_FROM"])
    monkeypatch.setenv("HOJA_MAIL_REPLY_TO", CF_ENV["HOJA_MAIL_REPLY_TO"])
    monkeypatch.setenv("HOJA_MAIL_APP_URL", CF_ENV["HOJA_MAIL_APP_URL"])
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ENV["CLOUDFLARE_ACCOUNT_ID"])
    monkeypatch.setenv("CLOUDFLARE_EMAIL_API_TOKEN", CF_ENV["CLOUDFLARE_EMAIL_API_TOKEN"])
    mail_config.reset_cache()

    sent: list[dict] = []

    def fake_send(_config, *, to, rendered):
        sent.append({"to": to, "subject": rendered.subject,
                     "html": rendered.html, "text": rendered.text})
        return f"prov-{len(sent)}"

    monkeypatch.setattr(mail_outbox, "send_rendered", fake_send)

    path = tmp_path / "invite.db"
    engine = make_engine(path)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Analytical Engines", org_url_key="analytical",
        team_key="ENG", user_name="Luis Rincon",
        user_email="luis@hoja.work", demo_issues=False,
    )
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        yield client, seeded["token"], engine, sent
    mail_config.reset_cache()


def _gql(client, token, query, variables=None):
    return client.post(
        "/graphql", headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()


def test_invite_member_sends_one_email_with_a_single_use_link(world) -> None:
    client, token, engine, sent = world
    created = _gql(client, token, INVITE, {"input": {
        "name": "Ada Lovelace", "email": "ada@example.test", "teamIds": ["ENG"],
    }})
    assert "errors" not in created, created
    user_id = created["data"]["memberInvite"]["user"]["id"]

    result = _gql(client, token, CREATE_INVITE_CODE, {"id": user_id})
    assert "errors" not in result, result
    payload = result["data"]["membershipInvitationCreate"]
    assert payload["success"] is True
    assert payload["emailQueued"] is True

    assert len(sent) == 1
    assert sent[0]["to"] == "ada@example.test"
    assert "Ada Lovelace" in sent[0]["text"]
    assert payload["inviteCode"] in sent[0]["text"]
    assert "Analytical Engines" in sent[0]["subject"]

    with engine.connect() as conn:
        row = conn.execute(select(db.mail_outbox)).mappings().first()
    assert row["template"] == "invite"
    assert row["status"] == "sent"
    # The stored payload carries the link, never the raw invitation token hash.
    assert "token_hash" not in row["payload"]
