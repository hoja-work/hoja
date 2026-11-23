"""Shared-tier sign-in: the identity's membership decides, not the profile's email.

HOJA-579. In the shared database (ADR-003) a person's Google identity and their
profile row are two records that legitimately disagree: linking an old profile to
a verified Google address (``google_login._claim_profile``) leaves the profile's
``email`` column as it was. The resolver must therefore read membership first.

The table below is the whole decision space, not just the five cases the ticket
lists. Two rows deserve a comment because they are where a careless "membership
first" rewrite breaks something the ticket never mentions:

* active membership + disabled profile must stay refused -- the module's own
  contract in ``_claim_profile`` ("linking a credential is not a way back in"),
  and ``tests/test_tenant_hosts.py`` already asserts a deactivated profile is
  denied while its membership row still says active;
* a membership-less identity whose verified address matches an *unclaimed*
  profile in this organization is an invitation, not a tenancy breach: the person
  is a member of another workspace and joins this one the same way an invitee
  does. The cross-tenant rule is that membership of A never admits to B, not that
  an address can never belong to two workspaces.

Row-level security note: ``user`` and ``organization_membership`` are tenant
tables keyed on the ``hoja.org_id`` GUC (``hoja_server/tenant_context.py``). Every
probe here runs with ``tenant_context.system()`` or ``scoped(org)`` bound; an
unbound read sees zero rows under RLS and looks like a bug when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select

from hoja_server import google_login, tenant_context
from hoja_server.db import (
    global_identity,
    make_engine,
    organization,
    organization_membership,
    user,
)
from tests import rls_mode

LOGIN = google_login.ACTION_LOGIN
DENIED = google_login.ACTION_DENIED
STAMP = "2026-11-12T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(tmp_path):
    """SQLite by default; under HOJA_TEST_RLS a Postgres schema whose app role is
    restricted, so these isolation assertions also run against real policies."""
    owner, _kwargs = rls_mode.workspace(tmp_path, "shared")
    return owner


def _org(conn, org_id: str, url_key: str) -> str:
    conn.execute(
        organization.insert().values(
            id=org_id, name=url_key.title(), url_key=url_key, created_at=STAMP, updated_at=STAMP
        )
    )
    return org_id


def _profile(conn, org_id: str, profile_id: str, email: str, *, active: bool = True) -> str:
    conn.execute(
        user.insert().values(
            id=profile_id,
            organization_id=org_id,
            name=email.split("@")[0],
            display_name=email.split("@")[0],
            email=email,
            active=active,
            admin=False,
            created_at=STAMP,
            updated_at=STAMP,
        )
    )
    return profile_id


def _identity(conn, identity_id: str, email: str) -> str:
    conn.execute(global_identity.insert().values(id=identity_id, email=email, created_at=STAMP))
    return identity_id


def _membership(
    conn, membership_id: str, identity_id: str, org_id: str, profile_id: str, *, active: bool = True
) -> None:
    conn.execute(
        organization_membership.insert().values(
            id=membership_id,
            identity_id=identity_id,
            organization_id=org_id,
            profile_user_id=profile_id,
            active=active,
            created_at=STAMP,
        )
    )


def _counts(conn) -> dict:
    return {
        name: conn.execute(select(func.count()).select_from(table)).scalar_one()
        for name, table in (
            ("user", user),
            ("global_identity", global_identity),
            ("organization_membership", organization_membership),
        )
    }


@dataclass
class Scenario:
    """One organization plus the three records the resolver reads."""

    org_id: str
    profile_id: str | None
    identity_id: str | None


def _scenario(
    conn, *, org="org-1", profile_email=None, profile_active=True, email=None, membership=None
) -> Scenario:
    """Build one state. ``membership`` is None, "active" or "inactive"."""
    org_id = _org(conn, org, org)
    profile_id = None
    identity_id = None
    if profile_email is not None:
        profile_id = _profile(conn, org_id, f"{org}-profile", profile_email, active=profile_active)
    if email is not None:
        identity_id = _identity(conn, f"{org}-identity", email)
    if membership is not None:
        assert identity_id and profile_id
        _membership(
            conn,
            f"{org}-membership",
            identity_id,
            org_id,
            profile_id,
            active=membership == "active",
        )
    return Scenario(org_id=org_id, profile_id=profile_id, identity_id=identity_id)


def _resolve(conn, address: str, org_id: str) -> dict:
    return google_login.resolve_member(
        conn, email=address, subject="google-sub", organization_id=org_id
    )


# --------------------------------------------------------------------------- #
# The regression: today's lockout, reproduced
# --------------------------------------------------------------------------- #


def test_an_active_member_signs_in_when_the_profile_email_differs_from_google(engine):
    """The production case: the clover4 profile said luisrincon@clover4.app, Google
    said luisalejandroclover@gmail.com, the membership was active, and sign-in
    returned not_invited."""
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(
            conn,
            profile_email="luisrincon@clover4.app",
            email="luisalejandroclover@gmail.com",
            membership="active",
        )
        before = _counts(conn)
        got = _resolve(conn, "luisalejandroclover@gmail.com", s.org_id)
        after = _counts(conn)

    assert (got["action"], got.get("reason")) == (LOGIN, None)
    assert got["user_id"] == s.profile_id
    assert got["identity_id"] == s.identity_id
    assert got["organization_id"] == s.org_id
    assert not got.get("linked"), "an existing membership is not a link event"
    assert after == before, "signing in must not create rows"


def test_the_fix_does_not_depend_on_the_interim_email_change(engine):
    """A second profile whose email intentionally differs, in its own workspace, so
    the result cannot rest on the owner's profile having been edited tonight."""
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(
            conn,
            org="second",
            profile_email="ops-desk@second.example",
            email="operator@elsewhere.example",
            membership="active",
        )
        got = _resolve(conn, "operator@elsewhere.example", s.org_id)

    assert got["action"] == LOGIN
    assert got["user_id"] == s.profile_id


def test_several_identities_can_share_an_address_and_the_membership_one_wins(engine):
    """``global_identity.email`` is indexed, not unique. The session's identity must
    be the one the membership row names, never whichever row a lookup returns."""
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(
            conn, profile_email="old@corp.example", email="new@corp.example", membership="active"
        )
        _identity(conn, "stale-identity", "new@corp.example")  # same address, no membership
        got = _resolve(conn, "new@corp.example", s.org_id)

    assert got["action"] == LOGIN
    assert got["identity_id"] == s.identity_id


# --------------------------------------------------------------------------- #
# The whole decision table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("profile_email", "profile_active", "identity", "membership", "expect", "reason"),
    [
        # (a) the defect being fixed
        ("old@corp.example", True, "new@corp.example", "active", LOGIN, None),
        # (a) already matched: membership and profile agree
        ("new@corp.example", True, "new@corp.example", "active", LOGIN, None),
        # (c) named here, not in the ticket: disabled profile behind an active
        #     membership -- _claim_profile's contract and test_tenant_hosts' case
        ("old@corp.example", False, "new@corp.example", "active", DENIED, "account_disabled"),
        # (b) unchanged: a revoked membership is not a way back in
        ("new@corp.example", True, "new@corp.example", "inactive", DENIED, "membership_inactive"),
        # inactive membership wins over a disabled profile: the membership is the
        # revocation that matters, and the reason must not collapse into the other
        ("new@corp.example", False, "new@corp.example", "inactive", DENIED, "membership_inactive"),
        # (b) unchanged: the invite path, identity already known
        ("new@corp.example", True, "new@corp.example", None, LOGIN, None),
        # (b) unchanged: the address matches a profile another identity already holds
        ("new@corp.example", True, "new@corp.example", None, DENIED, "profile_linked_elsewhere"),
        # (c) an identity that is a member of nothing, with no profile in this org
        (None, True, "new@corp.example", None, DENIED, "not_invited"),
        # (c) no identity row yet, invited by profile email
        ("new@corp.example", True, None, None, LOGIN, None),
        # (c) no identity row, disabled profile: never a way in
        ("new@corp.example", False, None, None, DENIED, "not_invited"),
        # (c) nothing at all
        (None, True, None, None, DENIED, "not_invited"),
    ],
    ids=[
        "membership-active-email-differs",
        "membership-active-email-matches",
        "membership-active-profile-disabled",
        "membership-inactive",
        "membership-inactive-profile-disabled",
        "no-membership-invite",
        "profile-claimed-by-another-identity",
        "identity-with-no-membership",
        "no-identity-invited-profile",
        "no-identity-disabled-profile",
        "nothing",
    ],
)
def test_decision_table(
    engine, profile_email, profile_active, identity, membership, expect, reason
):
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(
            conn,
            profile_email=profile_email,
            profile_active=profile_active,
            email=identity,
            membership=membership,
        )
        # "profile-claimed-by-another-identity" needs a second identity owning the
        # profile; every other row is the plain one-organization state.
        if reason == "profile_linked_elsewhere":
            # a *different* identity holds the profile; the caller's has none here
            assert s.profile_id
            _identity(conn, "claimant-identity", "claimant@else.example")
            _membership(conn, "claimant", "claimant-identity", s.org_id, s.profile_id)
        address = identity or profile_email or "nobody@corp.example"
        got = _resolve(conn, address, s.org_id)
        # whatever happens, the resolver never answers for a different workspace
        assert got.get("organization_id", s.org_id) == s.org_id

    assert (got["action"], got.get("reason")) == (expect, reason)
    if expect == LOGIN:
        assert got["user_id"] == s.profile_id


def test_each_refusal_keeps_its_own_reason(engine):
    """Distinct causes must stay distinguishable: a collapse to one reason would
    hide the next lockout the way not_invited hid this one."""
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(
            conn,
            org="inactive",
            profile_email="a@corp.example",
            email="a@corp.example",
            membership="inactive",
        )
        reasons = {_resolve(conn, "a@corp.example", s.org_id)["reason"]}
        s = _scenario(
            conn,
            org="disabled",
            profile_email="b@corp.example",
            profile_active=False,
            email="b@corp.example",
            membership="active",
        )
        reasons.add(_resolve(conn, "b@corp.example", s.org_id)["reason"])
        s = _scenario(conn, org="none", profile_email=None, email="c@corp.example")
        reasons.add(_resolve(conn, "c@corp.example", s.org_id)["reason"])

    assert reasons == {"membership_inactive", "account_disabled", "not_invited"}


# --------------------------------------------------------------------------- #
# Refusals write nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("address", ["stranger@corp.example", "claimed@corp.example"])
def test_a_refusal_creates_no_rows(engine, address):
    """Before this fix a denial could follow an unconditional global_identity insert,
    leaving an identity row behind for someone who was never let in."""
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(conn, profile_email="claimed@corp.example", email=None)
        assert s.profile_id
        if address == "claimed@corp.example":
            _identity(conn, "claimant", "someone-else@corp.example")
            _membership(conn, "claim", "claimant", s.org_id, s.profile_id)
        before = _counts(conn)
        got = _resolve(conn, address, s.org_id)
        after = _counts(conn)

    assert got["action"] == DENIED
    assert after == before


def test_the_invite_path_writes_exactly_one_membership(engine):
    with tenant_context.system(), engine.begin() as conn:
        s = _scenario(conn, profile_email="invited@corp.example", email="invited@corp.example")
        before = _counts(conn)
        got = _resolve(conn, "invited@corp.example", s.org_id)
        after = _counts(conn)
        bound = conn.execute(
            select(organization_membership.c.identity_id).where(
                organization_membership.c.profile_user_id == s.profile_id
            )
        ).scalar_one()

    assert got["action"] == LOGIN and got["linked"] is True
    assert after["organization_membership"] == before["organization_membership"] + 1
    assert after["global_identity"] == before["global_identity"]
    assert after["user"] == before["user"]
    assert bound == s.identity_id


# --------------------------------------------------------------------------- #
# The tenancy boundary
# --------------------------------------------------------------------------- #


def test_a_member_of_one_workspace_is_not_admitted_to_another(engine):
    with tenant_context.system(), engine.begin() as conn:
        a = _scenario(
            conn,
            org="alpha",
            profile_email="old@alpha.example",
            email="person@example.com",
            membership="active",
        )
        b = _scenario(conn, org="bravo", profile_email="someone@bravo.example", email=None)
        got = _resolve(conn, "person@example.com", b.org_id)
        bravo_rows = conn.execute(
            select(func.count())
            .select_from(organization_membership)
            .where(
                organization_membership.c.organization_id == b.org_id,
                organization_membership.c.identity_id == a.identity_id,
            )
        ).scalar_one()

    assert (got["action"], got["reason"]) == (DENIED, "not_invited")
    assert "organization_id" not in got, "a refusal must not name a workspace to enter"
    assert bravo_rows == 0, "no membership may be minted for bravo"
    # The policies themselves are proven separately, on a *new* connection opened
    # inside a tenant-bound context: a read on this system-bound connection shows
    # every row and would prove nothing about isolation.


def test_a_revoked_membership_does_not_admit_to_another_workspace(engine):
    with tenant_context.system(), engine.begin() as conn:
        _scenario(
            conn,
            org="alpha",
            profile_email="old@alpha.example",
            email="person@example.com",
            membership="inactive",
        )
        b = _scenario(conn, org="bravo", profile_email="someone@bravo.example", email=None)
        got = _resolve(conn, "person@example.com", b.org_id)

    assert (got["action"], got["reason"]) == (DENIED, "not_invited")


def test_an_invitation_in_another_workspace_is_not_a_tenancy_breach(engine):
    """The person is a member of alpha and was separately invited to bravo by email.
    Joining bravo is the invite path, and the session is bound to bravo only."""
    with tenant_context.system(), engine.begin() as conn:
        a = _scenario(
            conn,
            org="alpha",
            profile_email="old@alpha.example",
            email="person@example.com",
            membership="active",
        )
        b = _scenario(conn, org="bravo", profile_email="person@example.com", email=None)
        got = _resolve(conn, "person@example.com", b.org_id)
        in_bravo = conn.execute(
            select(func.count())
            .select_from(organization_membership)
            .where(
                organization_membership.c.organization_id == b.org_id,
                organization_membership.c.profile_user_id == b.profile_id,
            )
        ).scalar_one()

    assert got["action"] == LOGIN
    assert got["organization_id"] == b.org_id != a.org_id
    assert got["user_id"] == b.profile_id, "never the profile of the other workspace"
    assert in_bravo == 1


def test_a_membership_pointing_at_another_workspaces_profile_admits_nobody(engine):
    """Fail closed on inconsistent data: a membership row in bravo whose profile lives
    in alpha must not hand out alpha's profile through a bravo sign-in."""
    with tenant_context.system(), engine.begin() as conn:
        a = _scenario(
            conn, org="alpha", profile_email="owner@alpha.example", email="person@example.com"
        )
        b = _scenario(conn, org="bravo", profile_email="someone@bravo.example", email=None)
        # the inconsistent row: bravo's membership, alpha's identity and profile
        _membership(conn, "crossed", a.identity_id, b.org_id, a.profile_id)
        got = _resolve(conn, "person@example.com", b.org_id)

    assert (got["action"], got["reason"]) == (DENIED, "account_disabled")
    assert got.get("user_id") != a.profile_id


@pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS=1 and HOJA_TEST_POSTGRES_URL")
def test_under_row_level_security_a_bound_tenant_sees_only_its_own_rows(tmp_path):
    """The same boundary as the policies enforce it, not as our WHERE clauses do."""
    owner, kwargs = rls_mode.workspace(tmp_path, "signin")
    with tenant_context.system(), owner.begin() as conn:
        a = _scenario(
            conn,
            org="alpha",
            profile_email="old@alpha.example",
            email="person@example.com",
            membership="active",
        )
        b = _scenario(conn, org="bravo", profile_email="someone@bravo.example", email=None)
        # bravo really has rows of its own, so a zero below means isolation,
        # not a context that simply sees nothing.
        _profile(conn, b.org_id, "bravo-second", "second@bravo.example")

    app_engine = make_engine(kwargs["database_url"]) if kwargs.get("database_url") else owner
    with tenant_context.scoped(b.org_id), app_engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count())
                .select_from(organization_membership)
                .where(organization_membership.c.organization_id == a.org_id)
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                select(func.count()).select_from(user).where(user.c.organization_id == a.org_id)
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                select(func.count()).select_from(user).where(user.c.organization_id == b.org_id)
            ).scalar_one()
            == 2
        )
    with tenant_context.system(), app_engine.begin() as conn:
        got = _resolve(conn, "person@example.com", b.org_id)
    assert (got["action"], got["reason"]) == (DENIED, "not_invited")
