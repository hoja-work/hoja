"""github_link_add: the natural key is the dedupe guarantee, and tenancy is enforced.

The webhook path already relies on UNIQUE(organization_id, kind, external_id). A
person typing a reference goes through the same key, so this test pins both that
idempotency and the tenant boundary.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from hoja_server.db import github_link, issue, make_engine, migrate, seed_tenant
from hoja_server.store import Store
from tests import rls_mode

pytestmark = pytest.mark.skipif(not os.environ.get("HOJA_TEST_POSTGRES_URL"), reason="needs PostgreSQL")


def _db():
    url, _schema = rls_mode.new_schema_url()
    engine = make_engine(url)
    migrate(engine)
    return engine


def _first_issue(engine, org_id):
    with engine.connect() as conn:
        return conn.execute(
            select(issue.c.id).where(issue.c.organization_id == org_id).limit(1)
        ).first()[0]


def _link_count(engine, org_id):
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(github_link)
            .where(github_link.c.organization_id == org_id)
        ).scalar_one()


def test_the_same_reference_twice_is_one_row():
    engine = _db()
    tenant = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                         user_email="u@alpha.test", token="tok-a", demo_issues=True)
    store = Store(engine)
    issue_id = _first_issue(engine, tenant["org_id"])
    args = dict(kind="issue", external_id="o/r#42",
                url="https://github.com/o/r/issues/42", title="o/r #42")

    first = store.github_link_add(tenant["org_id"], issue_id, **args)
    second = store.github_link_add(tenant["org_id"], issue_id, **args)

    assert first is not None and second is not None
    assert first["id"] == second["id"]
    assert _link_count(engine, tenant["org_id"]) == 1


def test_an_issue_and_a_pull_request_with_one_number_are_two_rows():
    engine = _db()
    tenant = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                         user_email="u@alpha.test", token="tok-a", demo_issues=True)
    store = Store(engine)
    issue_id = _first_issue(engine, tenant["org_id"])

    store.github_link_add(tenant["org_id"], issue_id, kind="issue", external_id="o/r#42",
                          url="https://github.com/o/r/issues/42", title="o/r #42")
    store.github_link_add(tenant["org_id"], issue_id, kind="pr", external_id="o/r#42",
                          url="https://github.com/o/r/pull/42", title="o/r #42")

    assert _link_count(engine, tenant["org_id"]) == 2


def test_a_foreign_issue_is_refused_and_not_visible():
    engine = _db()
    alpha = seed_tenant(engine, org_name="Alpha", org_url_key="alpha",
                        user_email="u@alpha.test", token="tok-a", demo_issues=True)
    beta = seed_tenant(engine, org_name="Beta", org_url_key="beta",
                       user_email="u@beta.test", token="tok-b", demo_issues=True)
    store = Store(engine)
    alpha_issue = _first_issue(engine, alpha["org_id"])

    store.github_link_add(alpha["org_id"], alpha_issue, kind="issue", external_id="o/r#1",
                          url="https://github.com/o/r/issues/1", title="o/r #1")

    # Beta cannot attach to Alpha's task, and cannot read Alpha's link.
    assert store.github_link_add(beta["org_id"], alpha_issue, kind="commit",
                                 external_id="o/r@deadbeef",
                                 url="https://github.com/o/r/commit/deadbeef",
                                 title="o/r@deadbeef") is None
    assert store.issue_github_links_for(beta["org_id"], alpha_issue) == []
    assert _link_count(engine, beta["org_id"]) == 0
