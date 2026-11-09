"""hoja-serve merge-tenant: a dedicated tenant database moved into the shared one."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select, update
from starlette.testclient import TestClient

from hoja_server import secrets_box
from hoja_server.app import create_app
from hoja_server.db import (
    change_event,
    global_identity,
    issue,
    make_engine,
    migrate,
    model_provider,
    new_id,
    now_iso,
    organization,
    organization_membership,
    seed_tenant,
)
from hoja_server.tenant_merge import MergeError, merge
from tests import rls_mode

pytestmark = pytest.mark.skipif(not os.environ.get("HOJA_TEST_POSTGRES_URL"), reason="needs PostgreSQL")
TENANT_KEY = "tenant-secret-key"
SHARED_KEY = "shared-secret-key"


def _db():
    url, _schema = rls_mode.new_schema_url()
    engine = make_engine(url)
    migrate(engine)
    return engine


@pytest.fixture
def pair(monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", SHARED_KEY)
    source, target = _db(), _db()
    tenant = seed_tenant(source, org_name="Clover", org_url_key="clover", user_email="luis@clover.test",
                         token="clover-tok", demo_issues=True)
    with source.begin() as conn:
        conn.execute(model_provider.insert().values(
            id=new_id(), organization_id=tenant["org_id"], name="p", provider="openai", model_id="m",
            encrypted_api_key=secrets_box.encrypt_secret("sk-tenant", tenant["org_id"], secret=TENANT_KEY),
            enabled=True, created_at=now_iso(), updated_at=now_iso()))
    shared = seed_tenant(target, org_name="Alpha", org_url_key="alpha", user_email="luis@clover.test",
                         token="alpha-tok", demo_issues=True)
    return source, target, tenant, shared


def _count(engine, table, where=None):
    with engine.connect() as conn:
        stmt = select(func.count()).select_from(table)
        return conn.execute(stmt.where(where) if where is not None else stmt).scalar_one()


def test_a_dry_run_verifies_and_changes_nothing(pair):
    source, target, tenant, _shared = pair
    report = merge(source, target, source_secret=TENANT_KEY)
    assert not report.applied and report.tables["issue"] == _count(source, issue)
    assert _count(target, organization) == 1


def test_apply_moves_the_workspace_and_its_secrets(pair):
    source, target, tenant, shared = pair
    report = merge(source, target, source_secret=TENANT_KEY, vanity_host="clover.hoja.work", apply=True)
    org = tenant["org_id"]
    assert report.applied and report.resealed == 1
    assert _count(target, issue, issue.c.organization_id == org) == _count(source, issue)
    with target.connect() as conn:
        assert conn.execute(select(organization.c.vanity_host).where(organization.c.id == org)).scalar() \
            == "clover.hoja.work"
        sealed = conn.execute(select(model_provider.c.encrypted_api_key)
                              .where(model_provider.c.organization_id == org)).scalar_one()
        identities = conn.execute(select(func.count()).select_from(global_identity)
                                  .where(global_identity.c.email == "luis@clover.test")).scalar_one()
        members = conn.execute(select(organization_membership.c.organization_id)
                               .join(global_identity, global_identity.c.id == organization_membership.c.identity_id)
                               .where(global_identity.c.email == "luis@clover.test")).scalars().all()
        low = conn.execute(select(func.min(change_event.c.seq)).where(change_event.c.organization_id == org)).scalar()
    assert secrets_box.decrypt_secret(sealed, org) == "sk-tenant"
    assert identities == 1 and set(members) == {org, shared["org_id"]}
    with source.connect() as conn:
        source_head = conn.execute(select(func.max(change_event.c.seq))).scalar()
    assert low > source_head
    client = TestClient(create_app(None, database_url=target.url.render_as_string(hide_password=False),
                                   open_mode=False))
    res = client.post("/graphql", json={"query": "{ workspace { id } issues(first: 5) { nodes { id } } }"},
                      headers={"Authorization": "Bearer clover-tok"})
    assert res.json()["data"]["workspace"]["id"] == org
    assert res.json()["data"]["issues"]["nodes"]


def test_a_stale_copy_is_replaced_only_when_nothing_in_it_is_newer(pair):
    source, target, tenant, _shared = pair
    merge(source, target, source_secret=TENANT_KEY, apply=True)
    with source.begin() as conn:
        conn.execute(update(issue).where(issue.c.organization_id == tenant["org_id"])
                     .values(title="newer in tenant", updated_at="2099-01-01T00:00:00+00:00"))
    with pytest.raises(MergeError, match="older copy"):
        merge(source, target, source_secret=TENANT_KEY)
    report = merge(source, target, source_secret=TENANT_KEY, replace_stale=True, apply=True)
    assert report.replaced_stale
    with target.connect() as conn:
        titles = set(conn.execute(select(issue.c.title).where(issue.c.organization_id == tenant["org_id"])).scalars())
    assert titles == {"newer in tenant"}
    with target.begin() as conn:
        conn.execute(update(issue).where(issue.c.organization_id == tenant["org_id"])
                     .values(updated_at="2199-01-01T00:00:00+00:00"))
    with pytest.raises(MergeError, match="newer in the shared copy"):
        merge(source, target, source_secret=TENANT_KEY, replace_stale=True)


def test_a_url_key_owned_by_another_organization_refuses(pair):
    source, target, _tenant, shared = pair
    with target.begin() as conn:
        conn.execute(update(organization).where(organization.c.id == shared["org_id"]).values(url_key="clover"))
    with pytest.raises(MergeError, match="url_key"):
        merge(source, target, source_secret=TENANT_KEY)


def test_a_wrong_tenant_key_refuses_before_anything_commits(pair):
    source, target, _tenant, _shared = pair
    with pytest.raises(secrets_box.SecretBoxError):
        merge(source, target, source_secret="not-the-key", apply=True)
    assert _count(target, organization) == 1
