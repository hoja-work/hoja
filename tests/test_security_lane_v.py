"""Lane V: email header injection, model URL re-validation at request time, Google OAuth state single-use."""

from __future__ import annotations

import json
import smtplib
import threading
import time
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest import mock
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx

from cliniar_server.email_adapter import EmailAdapterError, SMTPConfig, send_email

SMTP_CFG = SMTPConfig(host="smtp.example.test", port=25, username="u", password="p", starttls=False)


def _sent_message(**overrides):
    kwargs = {"sender": "a@x.test", "recipients": ["b@y.test"], "subject": "hi", "text": "body"}
    kwargs.update(overrides)
    with mock.patch("cliniar_server.email_adapter.smtplib.SMTP") as smtp:
        send_email(SMTP_CFG, **kwargs)
        return smtp.return_value.__enter__.return_value.send_message.call_args.args[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject": "hi\r\nBcc: victim@evil.test"},
        {"subject": "hi\nBcc: victim@evil.test"},
        {"subject": "hi\u2028Bcc: victim@evil.test"},
        {"sender": "a@x.test\r\nBcc: v@evil.test"},
        {"sender": '"Eve\r\nBcc: v@evil.test" <a@x.test>'},
        {"recipients": ["b@y.test\nBcc: v@evil.test"]},
        {"recipients": ['"Bob <v@evil.test>" <b@y.test>']},
        {"recipients": ['"Bob, v@evil.test" <b@y.test>']},
    ],
)
def test_header_injection_is_rejected_with_adapter_error(overrides) -> None:
    with pytest.raises(EmailAdapterError):
        _sent_message(**overrides)


def test_plain_display_name_is_kept_and_no_bcc_added() -> None:
    msg = _sent_message(recipients=['"Bob Smith" <b@y.test>'])
    assert parseaddr(msg["To"]) == ("Bob Smith", "b@y.test")
    assert msg["Bcc"] is None


def test_starttls_failure_never_sends_credentials() -> None:
    cfg = SMTPConfig(host="smtp.example.test", port=25, username="u", password="SECRET")
    with mock.patch("cliniar_server.email_adapter.smtplib.SMTP") as smtp:
        client = smtp.return_value.__enter__.return_value
        client.starttls.side_effect = smtplib.SMTPNotSupportedError(0, b"no starttls")
        with pytest.raises(EmailAdapterError):
            send_email(cfg, sender="a@x.test", recipients=["b@y.test"], subject="s", text="t")
        assert not client.login.called


# ---- model provider base URL, enforced at request time ----

SECRET = "sk-lanev-secret"


class _Stub(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []
    redirect_to: ClassVar[str | None] = None

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        if length:
            self.rfile.read(length)
        _Stub.seen.append(self.headers.get("authorization", ""))
        if _Stub.redirect_to:
            self.send_response(307)
            self.send_header("location", _Stub.redirect_to)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(
            b'data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"m",'
            b'"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_server():
    _Stub.seen = []
    _Stub.redirect_to = None
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _provider_app(tmp_path, base_url: str):
    from sqlalchemy import insert

    from cliniar_server.app import create_app
    from cliniar_server.db import make_engine, migrate, model_provider, new_id, now_iso, seed_tenant
    from cliniar_server.secrets_box import encrypt_secret

    engine = make_engine(tmp_path / "lanev.db")
    migrate(engine)
    admin = seed_tenant(engine, org_name="Lanev", org_url_key="lanev", user_email="o@lanev.test",
                        token="lanev-tok", demo_issues=False)
    now = now_iso()
    with engine.begin() as conn:
        conn.execute(insert(model_provider).values(
            id=new_id(), organization_id=admin["org_id"], name="p", provider="openai", model_id="m",
            base_url=base_url, encrypted_api_key=encrypt_secret(SECRET), enabled=True,
            created_at=now, updated_at=now))
    return create_app(str(tmp_path / "lanev.db"), open_mode=False), admin


def _chat_text(app, admin) -> str:
    from starlette.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                          headers={"Authorization": f"Bearer {admin['token']}"})
    return res.text


@pytest.fixture
def strict_urls(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.delenv("CLINIAR_ALLOW_INSECURE_MODEL_URL", raising=False)


def test_db_inserted_loopback_provider_is_not_called(tmp_path, strict_urls, stub_server) -> None:
    app, admin = _provider_app(tmp_path, f"http://127.0.0.1:{stub_server.server_port}/v1")
    text = _chat_text(app, admin)
    assert _Stub.seen == []
    assert SECRET not in text
    assert "not allowed" in text


def test_provider_redirect_is_not_followed(tmp_path, monkeypatch, stub_server) -> None:
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("CLINIAR_ALLOW_INSECURE_MODEL_URL", "1")
    internal = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=internal.serve_forever, daemon=True).start()
    try:
        _Stub.redirect_to = f"http://127.0.0.1:{internal.server_port}/internal/v1/chat/completions"
        app, admin = _provider_app(tmp_path, f"http://127.0.0.1:{stub_server.server_port}/v1")
        _chat_text(app, admin)
        assert _Stub.seen == [f"Bearer {SECRET}"]  # only the configured hop saw the request
    finally:
        internal.shutdown()


# ---- Google OAuth state: single use ----


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "test-master-key")
    monkeypatch.setenv("CLINIAR_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("CLINIAR_GOOGLE_CLIENT_SECRET", "GOCSPX-lanev")
    monkeypatch.setenv("CLINIAR_GOOGLE_REDIRECT_URI", "https://hoja.example.test/api/connectors/google/callback")


def _google_app(tmp_path):
    from cliniar_server.app import create_app
    from cliniar_server.db import make_engine, migrate, seed_tenant

    engine = make_engine(tmp_path / "g.db")
    migrate(engine)
    admin = seed_tenant(engine, org_name="G", org_url_key="g-org", user_email="o@g.test",
                        token="g-tok", demo_issues=False)
    return create_app(str(tmp_path / "g.db"), open_mode=False), admin, engine


def _state_of(client, token) -> str:
    res = client.get("/api/connectors/google/start?scope=user", headers={"Authorization": f"Bearer {token}"},
                     follow_redirects=False)
    return parse_qs(urlparse(res.headers["location"]).query)["state"][0]


@respx.mock
def test_state_cannot_be_replayed(tmp_path, google_env) -> None:
    from starlette.testclient import TestClient

    from cliniar_server.db import connector_account

    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(
        200, json={"access_token": "ya29", "refresh_token": "1//R", "expires_in": 3599, "scope": "gmail.readonly"}))
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "a@example.test"}))
    app, admin, engine = _google_app(tmp_path)
    headers = {"Authorization": f"Bearer {admin['token']}"}
    with TestClient(app) as client:
        state = quote(_state_of(client, admin["token"]), safe="")
        first = client.get(f"/api/connectors/google/callback?code=c1&state={state}", headers=headers,
                           follow_redirects=False)
        second = client.get(f"/api/connectors/google/callback?code=c1&state={state}", headers=headers,
                            follow_redirects=False)
    assert first.status_code == 302
    assert second.status_code == 400
    assert "already used" in second.json()["error"]
    with engine.connect() as conn:
        assert len(conn.execute(connector_account.select()).all()) == 1


def test_expired_state_is_rejected(tmp_path, google_env) -> None:
    from starlette.testclient import TestClient

    from cliniar_server.secrets_box import encrypt_secret

    app, admin, _engine = _google_app(tmp_path)
    expired = encrypt_secret(json.dumps({"uid": admin["user_id"], "org": admin["org_id"], "scope": "user",
                                         "verifier": "v", "exp": int(time.time()) - 1, "nonce": "n"}))
    with TestClient(app) as client:
        res = client.get(f"/api/connectors/google/callback?code=c&state={quote(expired, safe='')}",
                         headers={"Authorization": f"Bearer {admin['token']}"}, follow_redirects=False)
    assert res.status_code == 400


@respx.mock
def test_state_replay_rejected_across_app_instances(tmp_path, google_env) -> None:
    """Used nonces persist in the database, so a second worker or a restart cannot accept a replay."""
    from starlette.testclient import TestClient

    from cliniar_server.app import create_app
    from cliniar_server.db import oauth_state_nonce

    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(
        200, json={"access_token": "ya29", "refresh_token": "1//R", "expires_in": 3599, "scope": "gmail.readonly"}))
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "a@example.test"}))
    app, admin, engine = _google_app(tmp_path)
    headers = {"Authorization": f"Bearer {admin['token']}"}
    with TestClient(app) as client:
        state = quote(_state_of(client, admin["token"]), safe="")
        assert client.get(f"/api/connectors/google/callback?code=c1&state={state}", headers=headers,
                          follow_redirects=False).status_code == 302
    with TestClient(create_app(str(tmp_path / "g.db"), open_mode=False)) as other_worker:
        replay = other_worker.get(f"/api/connectors/google/callback?code=c1&state={state}", headers=headers,
                                  follow_redirects=False)
    assert replay.status_code == 400
    with engine.connect() as conn:
        stored = conn.execute(oauth_state_nonce.select()).all()
    assert len(stored) == 1 and len(stored[0].nonce_hash) == 64
