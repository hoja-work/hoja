"""Sign-in for workspaces that have their own host, in the shared tier.

One app and one database serve app.hoja.work (the sign-in control plane) and every
enterprise workspace (``organization.vanity_host``). These pin the defects that
made an invited member of such a workspace unable to get in:

* the control plane's directory only knew sign-up rows and announced hints, and
  the announcements were signed with a key prod never had, so members were
  strangers at ``/signin`` and ``/signup``;
* a refused sign-in announced membership anyway;
* a valid invitation held by somebody signed in as another address read as
  "not invited";
* switching workspace on a pinned host traded its session for one the host
  refuses;
* sign-in failures were raw JSON, and transient ones read as refusals;
* a mistyped workspace address cost a full Google round-trip;
* an enterprise workspace's session on the apex stayed on the apex.

Runs under row-level security when ``HOJA_TEST_RLS`` is set (see rls_mode).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import select, update
from starlette.testclient import TestClient

from hoja_server import identity_transfer, invite_policy, membership_announce, signin_pages
from hoja_server import workspace_directory
from hoja_server.app import create_app
from hoja_server.db import (
    browser_session,
    membership_invitation,
    organization,
    seed_tenant,
    signup_session,
    token_hash,
)
from hoja_server.signup_store import SignupStore
from hoja_server.store import Store
from tests import rls_mode

CONTROL_HOST = "app.hoja.work"
CONTROL = f"https://{CONTROL_HOST}"
CORE = "core.hoja.work"
CLOVER = "clover.hoja.work"
MASTER = "m" * 48
HTML = {"accept": "text/html,application/xhtml+xml"}

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch):
    for name, value in {
        "HOJA_CONTROL_PLANE_URL": CONTROL,
        "HOJA_IDENTITY_TRANSFER_MASTER": MASTER,
        "HOJA_TENANT_BASE_DOMAIN": "hoja.work",
        "GOOGLE_CLIENT_ID": "hoja-test-client.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": f"{CONTROL}/auth/google/callback",
        "TURNSTILE_SECRET_KEY": "turnstile-secret",
        "TURNSTILE_EXPECTED_HOSTNAME": CONTROL_HOST,
        "HOJA_SIGNUP_MODE": "closed",
        "HOJA_WEBHOOK_WORKER": "0",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("HOJA_IDENTITY_TRANSFER_KEY", raising=False)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """Prod's shape: the control plane and two pinned workspaces, one app, one DB."""
    _env(monkeypatch)
    engine, kwargs = rls_mode.workspace(tmp_path, "shared")
    core = seed_tenant(engine, org_name="Core", org_url_key="core", user_email="christian@core.shop",
                       token="core-tok", demo_issues=False)
    clover = seed_tenant(engine, org_name="Clover International", org_url_key="clover",
                         user_email="luis@clover.test", token="clover-tok", demo_issues=False)
    apex = seed_tenant(engine, org_name="Apex Only", org_url_key="apexonly",
                       user_email="someone@apex.test", token="apex-tok", demo_issues=False)
    with engine.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == core["org_id"]).values(vanity_host=CORE))
        conn.execute(update(organization).where(organization.c.id == clover["org_id"]).values(vanity_host=CLOVER))
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False,
                     trusted_proxy_cidrs="127.0.0.1/32", trusted_public_host=CONTROL_HOST)
    client = TestClient(app, base_url=CONTROL, client=("127.0.0.1", 40100))
    with client:
        yield {"engine": engine, "client": client, "core": core, "clover": clover, "apex": apex,
               "store": Store(engine)}


def _invite(env, org, email, name="Invitee"):
    store = env["store"]
    store.invite_member(org["org_id"], name, email, False, [])
    profile = next(u for u in store.organization_members(org["org_id"]) if u["email"] == email)
    return store.create_membership_invitation(org["org_id"], profile["id"], org["user_id"])


def _add_to_second_workspace(env, org, email, *, invited=False):
    """Invite an address that already belongs to one workspace into another, through the real path."""
    env["store"].invite_member(org["org_id"], email, email, False, [])
    uid = next(u["id"] for u in env["store"].organization_members(org["org_id"]) if u["email"] == email)
    if invited:
        env["store"].create_membership_invitation(org["org_id"], uid, org["user_id"])
    return uid


def test_a_member_of_one_workspace_can_be_invited_to_another(shared):
    """invite_member used to refuse any address with an identity anywhere in the shared database."""
    from hoja_server.db import global_identity, organization_membership

    _invite(shared, shared["core"], "dana@core.shop")
    _add_to_second_workspace(shared, shared["clover"], "dana@core.shop")
    with shared["engine"].connect() as conn:
        identities = conn.execute(select(global_identity.c.id).where(
            global_identity.c.email == "dana@core.shop")).scalars().all()
        orgs = conn.execute(select(organization_membership.c.organization_id).where(
            organization_membership.c.identity_id == identities[0])).scalars().all()
    assert len(identities) == 1, "one person, one identity"
    assert set(orgs) == {shared["core"]["org_id"], shared["clover"]["org_id"]}
    for host in (CORE, CLOVER):
        assert _finish(shared, host, "dana@core.shop").status_code == 302


def _control_session(env, email, subject="google-sub", verified=True):
    signup = SignupStore(env["engine"])
    identity = signup.upsert_identity(subject=subject, email=email, email_verified=verified, name="P")
    raw, _ = signup.create_session(identity_id=identity["id"], client_ip=None, user_agent=None, days=1)
    env["client"].cookies.set("hoja_signup_session", raw)
    return raw


def _finish(env, host, email, *, cookies=None, headers=None):
    token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, host), audience=host,
                                    email=email, subject="sub-" + email, email_verified=True)
    env["client"].cookies.clear()
    for name, value in (cookies or {}).items():
        env["client"].cookies.set(name, value)
    return env["client"].get(f"/auth/google/finish?t={token}", follow_redirects=False,
                             headers={"Host": host, **(headers or {})})


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


# --------------------------------------------------------------------------- #
# 1. The directory reads real memberships, with real origins
# --------------------------------------------------------------------------- #


def test_an_invited_member_with_no_hint_is_sent_to_their_workspace(shared):
    _invite(shared, shared["core"], "alvaro@core.shop")
    _control_session(shared, "alvaro@core.shop")

    entry = shared["client"].get("/signin", follow_redirects=False)

    assert entry.status_code == 302, entry.text
    assert entry.headers["location"].startswith(f"https://{CORE}/auth/google/finish?t=")


def test_the_directory_returns_only_the_verified_addresses_workspaces(shared):
    engine = shared["engine"]
    _add_to_second_workspace(shared, shared["clover"], "christian@core.shop", invited=True)
    rows = workspace_directory.workspaces_for_email(engine, "Christian@Core.shop")
    assert [(r["name"], r["host"], r["origin"], r["status"]) for r in rows] == [
        ("Core", CORE, f"https://{CORE}/", "active"),
        ("Clover International", CLOVER, f"https://{CLOVER}/", "invited"),
    ]
    # Another member's workspaces, apex-only workspaces and strangers stay invisible.
    assert [r["host"] for r in workspace_directory.workspaces_for_email(engine, "luis@clover.test")] == [CLOVER]
    assert workspace_directory.workspaces_for_email(engine, "someone@apex.test") == []
    assert workspace_directory.workspaces_for_email(engine, "nobody@core.shop") == []
    assert workspace_directory.workspaces_for_email(engine, "") == []


def test_a_deactivated_member_is_not_in_the_directory(shared):
    store = shared["store"]
    core = shared["core"]
    store.invite_member(core["org_id"], "Gone", "gone@core.shop", False, [])
    gone = next(u for u in store.organization_members(core["org_id"]) if u["email"] == "gone@core.shop")
    store.set_member_active(core["org_id"], core["user_id"], gone["id"], False)
    assert workspace_directory.workspaces_for_email(shared["engine"], "gone@core.shop") == []


def test_the_chooser_shows_names_and_hosts(shared):
    _add_to_second_workspace(shared, shared["clover"], "christian@core.shop", invited=True)
    _control_session(shared, "christian@core.shop")

    page = shared["client"].get("/signin", follow_redirects=False)

    assert page.status_code == 200
    assert "Choose a workspace" in page.text
    assert "Clover International" in page.text and CLOVER in page.text
    assert "Core" in page.text and CORE in page.text
    assert "invitation" in page.text
    assert quote(f"https://{CORE}/", safe="") in page.text


def test_an_unverified_address_gets_no_directory(shared):
    _control_session(shared, "christian@core.shop", verified=False)
    page = shared["client"].get("/signin", follow_redirects=False)
    assert page.status_code == 200 and "No workspace yet" in page.text


def test_the_signup_state_lists_workspaces_the_person_can_open(shared):
    _invite(shared, shared["core"], "alvaro@core.shop")
    _control_session(shared, "alvaro@core.shop")

    body = shared["client"].get("/api/signup").json()

    assert body["googleVerified"] is True
    assert body["workspaces"] == [{
        "name": "Core", "host": CORE, "workspaceUrl": f"https://{CORE}",
        "signInUrl": f"{CONTROL}/auth/google/start?return_to={quote(f'https://{CORE}/', safe='')}",
        "status": "invited", "role": "member",
    }]
    assert "workspace" not in body


def test_a_stranger_gets_no_workspaces_on_the_signup_state(shared):
    _control_session(shared, "stranger@example.test")
    assert shared["client"].get("/api/signup").json()["workspaces"] == []


# --------------------------------------------------------------------------- #
# 1b / 2. Announcing: right key, off the request, only after success
# --------------------------------------------------------------------------- #


@pytest.fixture
def dedicated(tmp_path, monkeypatch):
    """A dedicated tenant: no host map, only the master configured (prod's env shape)."""
    _env(monkeypatch)
    engine, kwargs = rls_mode.workspace(tmp_path, "dedicated")
    seed_tenant(engine, org_name="Acme", org_url_key="acme", user_email="o@acme.test",
                token="acme-tok", demo_issues=False)
    calls = []
    monkeypatch.setattr(membership_announce, "announce", lambda **kw: calls.append(kw) or True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    with TestClient(app, base_url="https://acme.hoja.work") as client:
        yield {"engine": engine, "client": client, "calls": calls}


def test_announce_key_is_derived_from_the_master_when_no_key_is_set(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("HOJA_IDENTITY_TRANSFER_KEY", identity_transfer.tenant_key(MASTER, "acme.hoja.work"))
    engine, kwargs = rls_mode.workspace(tmp_path, "announce")
    seed_tenant(engine, org_name="Acme", org_url_key="acme", user_email="o@acme.test",
                token="acme-tok", demo_issues=False)
    calls = []
    monkeypatch.setattr(membership_announce, "announce", lambda **kw: calls.append(kw) or True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    env = {"client": TestClient(app, base_url="https://acme.hoja.work")}
    with env["client"]:
        refused = _finish(env, "acme.hoja.work", "stranger@acme.test")
        assert refused.status_code == 403
        assert calls == [], "a refused sign-in must not announce membership"
        admitted = _finish(env, "acme.hoja.work", "o@acme.test")
        assert admitted.status_code == 302, admitted.text
    assert len(calls) == 1
    assert calls[0]["email"] == "o@acme.test" and calls[0]["slug"] == "acme"
    assert calls[0]["tenant_key"] == identity_transfer.tenant_key(MASTER, "acme.hoja.work")


def test_the_announce_key_falls_back_to_the_master_derivation(tmp_path, monkeypatch):
    """Prod sets only the master; the derived key must be what /internal/membership verifies."""
    _env(monkeypatch)
    engine, kwargs = rls_mode.workspace(tmp_path, "announce2")
    org = seed_tenant(engine, org_name="Acme", org_url_key="acme", user_email="o@acme.test",
                      token="acme-tok", demo_issues=False)
    calls = []
    monkeypatch.setattr(membership_announce, "announce", lambda **kw: calls.append(kw) or True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    store = Store(engine)
    store.invite_member(org["org_id"], "Kevin", "kevin@acme.test", False, [])
    kevin = next(u for u in store.organization_members(org["org_id"]) if u["email"] == "kevin@acme.test")
    code = store.create_membership_invitation(org["org_id"], kevin["id"], org["user_id"])
    with TestClient(app, base_url="https://acme.hoja.work") as client:
        accepted = client.post("/auth/invite/accept", json={
            "code": code, "email": "kevin@acme.test", "password": "a-long-password-123"})
        assert accepted.status_code == 200, accepted.text
    assert calls and calls[0]["tenant_key"] == identity_transfer.tenant_key(MASTER, "acme.hoja.work")


def test_the_shared_tier_does_not_announce_to_itself(shared, monkeypatch):
    calls = []
    monkeypatch.setattr(membership_announce, "announce", lambda **kw: calls.append(kw) or True)
    assert _finish(shared, CORE, "christian@core.shop").status_code == 302
    assert calls == []


# --------------------------------------------------------------------------- #
# 3. A valid invitation for another address says so
# --------------------------------------------------------------------------- #


def test_the_redemption_policy_is_exact_address_only():
    allowed = invite_policy.invite_redemption_allowed
    assert allowed("jane@acme.com", "Jane@Acme.com ")
    assert not allowed("jane@acme.com", "jane.doe@acme.com")
    assert not allowed("jane@gmail.com", "other@gmail.com")
    assert not allowed("", "")
    assert invite_policy.mask("jane@acme.com") == "j***@acme.com"


def test_an_invitation_held_by_another_address_is_named_not_refused_as_uninvited(shared):
    code = _invite(shared, shared["core"], "jane@core.shop", name="Jane")

    page = _finish(shared, CORE, "jane.doe@core.shop",
                   cookies={"hoja_invite_pending": code}, headers=HTML)

    assert page.status_code == 403
    assert "j***@core.shop" in page.text
    assert "jane.doe@core.shop" in page.text
    assert "jane@core.shop" not in page.text
    assert "Choose the invited account" in page.text
    assert "choose_account=1" in page.text
    assert "hoja_session" not in page.headers.get("set-cookie", "")
    as_json = _finish(shared, CORE, "jane.doe@core.shop", cookies={"hoja_invite_pending": code})
    assert as_json.json()["reason"] == "invite_address_mismatch"
    # The invitation is untouched: the right account can still use it.
    with shared["engine"].connect() as conn:
        assert conn.execute(select(membership_invitation.c.accepted_at)).scalar() is None


def test_an_invitation_for_another_workspace_does_not_change_the_refusal(shared):
    code = _invite(shared, shared["clover"], "jane@clover.test", name="Jane")
    refused = _finish(shared, CORE, "jane.doe@core.shop", cookies={"hoja_invite_pending": code})
    assert refused.json()["reason"] == "not_invited"


def test_the_invited_address_itself_still_signs_in(shared):
    code = _invite(shared, shared["core"], "jane@core.shop", name="Jane")
    admitted = _finish(shared, CORE, "jane@core.shop", cookies={"hoja_invite_pending": code})
    assert admitted.status_code == 302
    assert admitted.headers["location"].startswith("/?invite=")


# --------------------------------------------------------------------------- #
# 4 + apex. A workspace is opened on its own host
# --------------------------------------------------------------------------- #

SWITCH = "mutation($id:String!){workspaceSwitch(organizationId:$id){success redirectUrl organization{id}}}"


def _two_workspace_member(shared):
    """christian is admin of Core and a member of Clover, both pinned."""
    _add_to_second_workspace(shared, shared["clover"], "christian@core.shop", invited=True)
    session = _finish(shared, CORE, "christian@core.shop")
    assert session.status_code == 302, session.text
    return session.cookies.get("hoja_session") or shared["client"].cookies.get("hoja_session")


def test_switching_on_a_pinned_host_navigates_instead_of_locking_itself_out(shared):
    client = shared["client"]
    raw = _two_workspace_member(shared)
    client.cookies.clear()
    client.cookies.set("hoja_session", raw)

    switched = client.post("/graphql", json={"query": SWITCH, "variables": {"id": shared["clover"]["org_id"]}},
                           headers={"Host": CORE, "origin": f"https://{CORE}"})

    assert switched.status_code == 200, switched.text
    payload = switched.json()["data"]["workspaceSwitch"]
    assert payload["success"] is True
    assert payload["redirectUrl"] == (
        f"{CONTROL}/auth/google/start?return_to={quote(f'https://{CLOVER}/', safe='')}")
    assert "hoja_session" not in switched.headers.get("set-cookie", "")
    # The session on this host is still alive and still this workspace's.
    with shared["engine"].connect() as conn:
        assert conn.execute(select(browser_session.c.revoked_at).where(
            browser_session.c.token_hash == token_hash(raw))).scalar() is None
    viewer = client.post("/graphql", json={"query": "{ workspace { id } }"},
                         headers={"Host": CORE, "origin": f"https://{CORE}"})
    assert viewer.json()["data"]["workspace"]["id"] == shared["core"]["org_id"]


def test_creating_a_workspace_on_a_pinned_host_is_refused_before_anything_is_made(shared):
    client = shared["client"]
    raw = _two_workspace_member(shared)
    client.cookies.clear()
    client.cookies.set("hoja_session", raw)
    created = client.post("/graphql", headers={"Host": CORE, "origin": f"https://{CORE}"}, json={
        "query": "mutation($i:WorkspaceCreateInput!){workspaceCreate(input:$i){success}}",
        "variables": {"i": {"name": "Side", "urlKey": "side"}}})
    errors = created.json().get("errors") or []
    assert errors and errors[0]["extensions"]["code"] == "WORKSPACE_HOST_PINNED"
    with shared["engine"].connect() as conn:
        assert conn.execute(select(organization.c.id).where(organization.c.url_key == "side")).first() is None


def test_an_apex_session_of_a_pinned_workspace_is_sent_to_its_host(shared):
    client = shared["client"]
    raw, _details = shared["store"].create_session_for_membership(
        *_membership(shared, "christian@core.shop", shared["core"]["org_id"]))
    client.cookies.clear()
    client.cookies.set("hoja_session", raw)
    me = client.get("/auth/me")
    assert me.status_code == 409
    assert me.json()["reason"] == "wrong_workspace_host"
    assert me.json()["redirectUrl"].endswith(quote(f"https://{CORE}/", safe=""))
    # An apex workspace stays on the apex.
    apex_raw, _ = shared["store"].create_session_for_membership(
        *_membership(shared, "someone@apex.test", shared["apex"]["org_id"]))
    client.cookies.set("hoja_session", apex_raw)
    assert client.get("/auth/me").status_code == 200


def _membership(shared, email, org_id):
    row = next(r for r in shared["store"].workspaces_for_email(email) if r["organization_id"] == org_id)
    return row["identity_id"], row["profile_user_id"], org_id


def test_an_apex_password_login_for_a_pinned_workspace_redirects_without_a_session(shared):
    client = shared["client"]
    identity_id, uid, org = _membership(shared, "christian@core.shop", shared["core"]["org_id"])
    store = shared["store"]
    with store.engine.begin() as conn:
        from hoja_server.db import identity_credential
        conn.execute(identity_credential.insert().values(
            identity_id=identity_id, password_hash=Store._password_digest("a-long-password-123", b"0" * 16),
            created_at="2026-01-01", updated_at="2026-01-01"))
    client.cookies.clear()
    login = client.post("/auth/login", json={"email": "christian@core.shop", "password": "a-long-password-123"})
    assert login.status_code == 200, login.text
    assert login.json()["status"] == "redirect"
    assert login.json()["redirectUrl"].endswith(quote(f"https://{CORE}/", safe=""))
    assert "hoja_session" not in login.headers.get("set-cookie", "")


# --------------------------------------------------------------------------- #
# 5. Every failure is a page with an error id and the right way forward
# --------------------------------------------------------------------------- #


def test_an_expired_handoff_says_start_again(shared):
    token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, CORE), audience=CORE,
                                    email="christian@core.shop", subject="s", email_verified=True, now=0)
    page = shared["client"].get(f"/auth/google/finish?t={token}", headers={"Host": CORE, **HTML},
                                follow_redirects=False)
    assert page.status_code == 400
    assert "text/html" in page.headers["content-type"]
    assert "Start again" in page.text and "not authorised" not in page.text
    assert f"{CONTROL}/signin?next={quote(f'https://{CORE}/', safe='')}" in page.text.replace("&amp;", "&")
    assert page.headers["x-hoja-error-id"] in page.text


def test_a_wrong_key_is_reported_as_our_problem(shared):
    token = identity_transfer.issue(key=identity_transfer.tenant_key(MASTER, CLOVER), audience=CORE,
                                    email="christian@core.shop", subject="s", email_verified=True)
    page = shared["client"].get(f"/auth/google/finish?t={token}", headers={"Host": CORE, **HTML},
                                follow_redirects=False)
    assert page.status_code == 400
    assert "problem on our side" in page.text


@pytest.mark.parametrize("query, reason", [
    ("state=nope", "state_unknown"),
])
def test_a_bad_callback_is_a_page_for_browsers_and_json_for_clients(shared, query, reason):
    page = shared["client"].get(f"/auth/google/callback?{query}", headers=HTML)
    assert page.status_code == 400 and "text/html" in page.headers["content-type"]
    assert "Start again" in page.text
    data = shared["client"].get(f"/auth/google/callback?{query}")
    assert data.json()["reason"] == reason and data.json()["errorId"]


def test_a_cancelled_google_prompt_restarts_the_same_workspace(shared):
    client = shared["client"]
    client.cookies.clear()
    started = client.get("/auth/google/start", params={"return_to": f"https://{CORE}/"},
                         follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    page = client.get(f"/auth/google/callback?state={state}&error=access_denied", headers=HTML)
    assert page.status_code == 400
    assert "Google sign-in was cancelled" in page.text
    assert "/auth/google/start?return_to=" + quote(f"https://{CORE}/", safe="") in page.text


def _emitted_reasons() -> set[str]:
    """Every reason code a sign-in failure page can be asked to render."""
    source = lambda name: (ROOT / "hoja_server" / name).read_text(encoding="utf-8")  # noqa: E731
    reasons: set[str] = set()
    reasons |= set(re.findall(r'"reason":\s*"([a-z_]+)"', source("google_login.py")))
    reasons |= set(re.findall(r'TransferError\("([a-z_]+)"', source("identity_transfer.py")))
    reasons |= set(re.findall(r'OIDCError\("([a-z_]+)"', source("google_oidc.py")))
    consume = source("signup_store.py")
    consume = consume[consume.index("def consume_flow"):consume.index("def upsert_identity")]
    reasons |= set(re.findall(r'"(state_[a-z_]+)"', consume))
    app = source("app.py")
    for call in re.findall(r"_signin_failure\((.*?)\n\s*\)", app, re.S):
        reasons |= set(re.findall(r'reason="([a-z_]+)"', call))
    for call in re.findall(r"_failed\(([^\n]*)\)", app):
        reasons |= set(re.findall(r'"([a-z_]+)"\s*$', call.strip()))
    reasons |= set(re.findall(r'reason="([a-z_]+)"', app[app.index("def google_sign_in_callback"):
                                                        app.index("def google_sign_in_finish")]))
    reasons.add(invite_policy.MISMATCH)
    return reasons


def test_every_emitted_reason_has_specific_copy():
    reasons = _emitted_reasons()
    # The grep must actually find the families it claims to cover.
    for sample in ("not_invited", "expired", "state_expired", "provider_error", "nonce_mismatch",
                   "transfer_unconfigured", "ip_not_allowed", "invite_address_mismatch"):
        assert sample in reasons, sample
    missing = sorted(reason for reason in reasons if reason not in signin_pages.REASONS)
    assert missing == [], f"no sign-in copy for {missing}"


@pytest.mark.parametrize("reason", sorted(signin_pages.REASONS))
def test_transient_and_our_side_reasons_never_say_not_authorised(reason):
    page = signin_pages.denial_page(host=CORE, email="a@b.c", reason=reason, error_id="abc123",
                                    entry_url="/signin/signout", retry_url="/signin")
    kind = signin_pages.reason_kind(reason)
    assert "abc123" in page
    if kind == signin_pages.RETRY:
        assert "Start again" in page and "not authorised" not in page
    if kind == signin_pages.OURS:
        assert "Try again" in page and "not authorised" not in page


# --------------------------------------------------------------------------- #
# 6. A mistyped workspace address is caught before Google
# --------------------------------------------------------------------------- #


def test_an_unknown_workspace_is_named_before_google_runs(shared):
    client = shared["client"]
    for url in (f"/signin?next={quote('https://cor.hoja.work/', safe='')}",
                f"/auth/google/start?return_to={quote('https://cor.hoja.work/', safe='')}"):
        page = client.get(url, follow_redirects=False)
        assert page.status_code == 404, url
        assert "There's no workspace at cor.hoja.work" in page.text
        assert 'href="/signin"' in page.text
        assert "accounts.google.com" not in page.headers.get("location", "")


def test_a_real_workspace_still_goes_to_google(shared):
    shared["client"].cookies.clear()
    entry = shared["client"].get(f"/signin?next={quote(f'https://{CORE}/', safe='')}", follow_redirects=False)
    assert entry.status_code == 302


def test_the_dedicated_tier_cannot_tell_and_does_not_guess(dedicated):
    entry = dedicated["client"].get(
        f"{CONTROL}/signin?next={quote('https://anything.hoja.work/', safe='')}", follow_redirects=False,
        headers={"Host": CONTROL_HOST})
    assert entry.status_code == 302


def test_signup_session_table_is_untouched_by_directory_reads(shared):
    """The directory is read-only: no sign-up rows appear for a member who only signed in."""
    _control_session(shared, "christian@core.shop")
    shared["client"].get("/signin", follow_redirects=False)
    with shared["engine"].connect() as conn:
        assert len(conn.execute(select(signup_session.c.id)).all()) == 1
