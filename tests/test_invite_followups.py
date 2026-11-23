"""The invitation stories that were still open after the first fix.

Each test names the story it closes. Nothing here reads the code under test's
own opinion of itself: every assertion is a row read back, or an exception.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from hoja_server import addresses, google_login, mail_events
from hoja_server.db import (
    global_identity,
    make_engine,
    membership_invitation,
    migrate,
    organization,
    organization_membership,
    user,
)
from hoja_server.store import InviteRefused, Store

NOW = "2026-11-08T12:00:00+00:00"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "tenant.db")
    migrate(eng)
    return eng


def _workspace(engine):
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
    return Store(engine)


# HOJA-30 ------------------------------------------------------------------ #


def test_one_normaliser_decides_what_the_same_address_is():
    assert addresses.canonical("  Kevin@Hoja.Work ") == "kevin@hoja.work"
    assert addresses.same("KEVIN@hoja.work", "kevin@Hoja.Work") is True
    assert addresses.same("kevin@hoja.work", "kevin@hoja.works") is False
    assert addresses.same("", "") is False


def test_google_login_and_the_invitation_agree_on_case(engine):
    store = _workspace(engine)
    store.invite_member("org-hoja", "Kevin", "Kevin@Hoja.Work")
    with engine.connect() as conn:
        kevin = conn.execute(select(user.c.email).where(
            func.lower(user.c.email) == "kevin@hoja.work")).scalar_one()
    assert kevin == "kevin@hoja.work"
    with engine.begin() as conn:
        resolved = google_login.resolve_verified_email(
            conn, email="KEVIN@HOJA.WORK", subject="sub-kevin")
    assert resolved["action"] == google_login.ACTION_LOGIN          # HOJA-25
    assert resolved["organization_id"] == "org-hoja"


# HOJA-28 ------------------------------------------------------------------ #


def test_inviting_an_existing_member_says_so(engine):
    store = _workspace(engine)
    with pytest.raises(InviteRefused) as refused:
        store.invite_member("org-hoja", "Luis", "luis@hoja.work")
    assert refused.value.reason == "already_member"


def test_an_invalid_address_is_its_own_refusal(engine):
    store = _workspace(engine)
    with pytest.raises(InviteRefused) as refused:
        store.invite_member("org-hoja", "Nobody", "not-an-address")
    assert refused.value.reason == "invalid_input"


# HOJA-31 ------------------------------------------------------------------ #


def test_re_inviting_a_removed_member_restores_the_same_profile(engine):
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    with engine.begin() as conn:
        conn.execute(update(user).where(user.c.id == created["id"]).values(active=False))

    restored, _new_key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")

    assert restored["id"] == created["id"]
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(user).where(
            func.lower(user.c.email) == "kevin@hoja.work")).scalar_one()
        active = conn.execute(select(user.c.active).where(
            user.c.id == created["id"])).scalar_one()
    assert count == 1
    assert bool(active) is True


# HOJA-29 ------------------------------------------------------------------ #


def test_a_valid_code_can_be_looked_at_without_spending_it(engine):
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    code = store.create_membership_invitation("org-hoja", created["id"], "u-luis")

    peeked = store.peek_membership_invitation(code)
    assert peeked == {"name": "Hoja", "url_key": "hoja"}
    # Peeking is not accepting: the code is still usable afterwards.
    assert store.peek_membership_invitation(code) is not None


def test_an_expired_code_cannot_be_looked_at(engine):
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    code = store.create_membership_invitation("org-hoja", created["id"], "u-luis")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with engine.begin() as conn:
        conn.execute(update(membership_invitation).values(expires_at=past))

    assert store.peek_membership_invitation(code) is None


# HOJA-27 ------------------------------------------------------------------ #


def test_an_unconfigured_mailer_is_not_reported_as_sent(engine, monkeypatch):
    monkeypatch.delenv("HOJA_MAIL_TRANSPORT", raising=False)
    for name in ("HOJA_SMTP_HOST", "HOJA_MAIL_FROM", "HOJA_APP_URL"):
        monkeypatch.delenv(name, raising=False)

    delivery = mail_events.send_invite(
        engine, organization_id="org-hoja", organization_name="Hoja",
        inviter_name="Luis", invitee_name="Kevin",
        invitee_email="kevin@hoja.work", code="c" * 43)

    assert delivery == "not_configured"


def test_a_restored_member_can_accept_the_invitation_they_were_sent(engine):
    """Reactivation must not leave a stale credential that blocks acceptance."""
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    first_code = store.create_membership_invitation("org-hoja", created["id"], "u-luis")
    assert store.accept_new_member_invitation(first_code, "kevin@hoja.work", "a-long-password")

    with engine.begin() as conn:
        conn.execute(update(user).where(user.c.id == created["id"]).values(active=False))

    restored, _fresh_key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    second_code = store.create_membership_invitation("org-hoja", restored["id"], "u-luis")

    accepted = store.accept_new_member_invitation(second_code, "kevin@hoja.work", "another-long-password")
    assert accepted is not None
    assert accepted[1]["user_id"] == created["id"]


def test_reactivation_takes_the_role_the_inviter_asked_for(engine):
    """A deactivated admin must not come back as an admin by accident."""
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Second", "second@hoja.work", True)
    with engine.begin() as conn:
        conn.execute(update(user).where(user.c.id == created["id"]).values(active=False))

    restored, _fresh = store.invite_member("org-hoja", "Second", "second@hoja.work", False)

    assert restored["id"] == created["id"]
    assert bool(restored["admin"]) is False


def test_reactivation_clears_the_old_password(engine):
    store = _workspace(engine)
    created, _key = store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")
    code = store.create_membership_invitation("org-hoja", created["id"], "u-luis")
    store.accept_new_member_invitation(code, "kevin@hoja.work", "a-long-password")
    with engine.begin() as conn:
        conn.execute(update(user).where(user.c.id == created["id"]).values(active=False))

    store.invite_member("org-hoja", "Kevin", "kevin@hoja.work")

    with engine.connect() as conn:
        credentials = conn.execute(select(func.count()).select_from(
            __import__("hoja_server.db", fromlist=["identity_credential"]).identity_credential
        )).scalar_one()
    assert credentials == 0
