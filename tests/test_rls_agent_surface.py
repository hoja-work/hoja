"""The agent surface under row-level security: conversations, messages, providers, keys.

Two organizations and one planted key with no tenant at all, read and written
through a restricted NOSUPERUSER NOBYPASSRLS role. A regressed bind shows up here
as an empty result rather than as an error, which is exactly why these tables need
to be in the RLS gate.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from hoja_server import tenant_context as tc
from hoja_server.db import (
    agent_conversation,
    agent_message,
    api_key,
    make_engine,
    model_provider,
    now_iso,
    seed_tenant,
)
from tests import rls_mode

pytestmark = pytest.mark.skipif(not rls_mode.ENABLED, reason="needs HOJA_TEST_RLS + Postgres")


@pytest.fixture
def two_orgs(tmp_path):
    owner, kwargs = rls_mode.workspace(tmp_path, "probe")
    a = seed_tenant(owner, org_name="Alpha", org_url_key="alpha", team_key="ALP",
                    user_email="a@alpha.test", token="probe-tok-a", demo_issues=True)
    b = seed_tenant(owner, org_name="Bravo", org_url_key="bravo", team_key="BRV",
                    user_email="b@bravo.test", token="probe-tok-b", demo_issues=True)
    ts = now_iso()
    conv = {}
    with owner.begin() as conn:
        for key, seeded in (("a", a), ("b", b)):
            cid = f"conv-{key}-{uuid.uuid4().hex[:6]}"
            conn.execute(agent_conversation.insert().values(
                id=cid, organization_id=seeded["org_id"], user_id=seeded["user_id"],
                title=f"secret {key}", created_at=ts, updated_at=ts))
            conn.execute(agent_message.insert().values(
                id=f"msg-{key}", conversation_id=cid, role="user",
                content=f"TOP-SECRET-{key.upper()}", created_at=ts))
            conn.execute(model_provider.insert().values(
                id=f"prov-{key}", organization_id=seeded["org_id"], name=f"p-{key}",
                provider="openai", model_id="gpt-4o-mini", base_url="https://api.example.test",
                encrypted_api_key=f"v2:{key}-ciphertext", enabled=True, created_at=ts, updated_at=ts))
            conv[key] = cid
        # a legacy key with no tenant at all
        conn.execute(api_key.insert().values(
            id="legacy-key", token_hash="deadbeef" + uuid.uuid4().hex, label="legacy",
            access="read_write", user_id=None, organization_id=None, created_at=ts))
    restricted = make_engine(kwargs["database_url"])
    yield owner, restricted, a, b, conv
    restricted.dispose()


def _count(conn, table, org):
    return conn.execute(select(func.count()).select_from(table).where(table.c.organization_id == org)).scalar()


def test_org_a_cannot_read_org_b_agent_model_or_key_rows(two_orgs):
    _owner, restricted, a, b, _conv = two_orgs
    with tc.scoped(a["org_id"]), restricted.connect() as conn:
        assert conn.execute(select(func.count()).select_from(agent_conversation)).scalar() == 1
        assert conn.execute(select(func.count()).select_from(agent_message)).scalar() == 1
        assert conn.execute(select(func.count()).select_from(model_provider)).scalar() == 1
        # direct predicate attempt on B
        for table in (agent_conversation, model_provider, api_key):
            assert _count(conn, table, b["org_id"]) == 0, table.name
        # by primary key, no WHERE help
        assert conn.execute(select(agent_message.c.content).where(
            agent_message.c.id == "msg-b")).first() is None
        assert conn.execute(select(model_provider.c.name).where(
            model_provider.c.id == "prov-b")).first() is None
        assert conn.execute(select(api_key.c.id).where(
            api_key.c.organization_id == b["org_id"])).first() is None
        # legacy NULL-tenant key is invisible to a bound tenant
        assert conn.execute(select(api_key.c.id).where(api_key.c.id == "legacy-key")).first() is None


def test_empty_and_unbound_contexts_see_nothing(two_orgs):
    _owner, restricted, _a, _b, _conv = two_orgs
    for ctx in (tc.scoped(""), tc.scoped(None)):
        with ctx, restricted.connect() as conn:
            for table in (agent_message, agent_conversation, model_provider, api_key):
                assert conn.execute(select(func.count()).select_from(table)).scalar() == 0, table.name


def test_system_context_sees_every_tenant_and_the_legacy_row(two_orgs):
    _owner, restricted, a, b, _conv = two_orgs
    with tc.system(), restricted.connect() as conn:
        assert _count(conn, model_provider, a["org_id"]) == 1
        assert _count(conn, model_provider, b["org_id"]) == 1
        assert conn.execute(select(api_key.c.id).where(api_key.c.id == "legacy-key")).first() is not None


def test_org_a_cannot_write_into_org_b(two_orgs):
    _owner, restricted, a, b, conv = two_orgs
    ts = now_iso()
    with tc.scoped(a["org_id"]):
        # cross-tenant INSERT is refused by WITH CHECK
        with pytest.raises((ProgrammingError, IntegrityError)) as exc, restricted.begin() as conn:
            conn.execute(model_provider.insert().values(
                id="prov-evil", organization_id=b["org_id"], name="evil", provider="openai",
                model_id="x", encrypted_api_key="v2:evil", enabled=True,
                created_at=ts, updated_at=ts))
        assert "row-level security" in str(exc.value).lower()
        # a child row into B's conversation is refused too
        with pytest.raises((ProgrammingError, IntegrityError)), restricted.begin() as conn:
            conn.execute(agent_message.insert().values(
                id="msg-evil", conversation_id=conv["b"], role="user",
                content="injected", created_at=ts))
        # cross-tenant UPDATE / DELETE rewrite nothing
        with restricted.begin() as conn:
            assert conn.execute(model_provider.update().where(
                model_provider.c.id == "prov-b").values(name="pwned")).rowcount == 0
            assert conn.execute(api_key.update().where(
                api_key.c.organization_id == b["org_id"]).values(revoked_at="now")).rowcount == 0
            assert conn.execute(agent_message.delete().where(
                agent_message.c.conversation_id == conv["b"])).rowcount == 0
        # a forged conversation_id inside the org is still subject to the FK/child policy
        with restricted.begin() as conn:
            assert conn.execute(agent_message.update().where(
                agent_message.c.conversation_id == conv["b"]).values(role="user")).rowcount == 0
