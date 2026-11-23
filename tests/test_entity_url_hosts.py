"""Entity links name the host their workspace is served from.

One database serves several workspaces, and a hosted workspace
(``organization.vanity_host``) answers on its own origin. A link built from the
process-wide ``HOJA_APP_URL`` therefore opens the canonical host and the wrong
workspace — production Core stored ``https://hoja.hoja.work/core/issue/…`` for
all 99 issues and 7/7 projects. These tests pin the rule from both ends: what a
new row is written with, and what revision 73 does to the rows already stored.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from sqlalchemy import and_, select, update

from hoja_server import db
from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    attachment,
    comment,
    issue,
    make_engine,
    migrate,
    new_id,
    now_iso,
    organization,
    project,
    seed_tenant,
    user,
    webhook,
)
from hoja_server.writer import Writer
from tests import legacy_schema
from tests import rls_mode

APP = "https://hoja.hoja.work"
CORE = "core.hoja.work"


@pytest.fixture
def canonical(monkeypatch):
    """HOJA_APP_URL as production has it: the canonical host, not the workspace host."""
    monkeypatch.setenv("HOJA_APP_URL", APP)
    return APP


def _seed(engine, *, key, name, vanity=None, email=None):
    seeded = seed_tenant(
        engine, org_name=name, org_url_key=key, team_key="ENG", team_name="Engineering",
        user_name=f"{name} User", user_email=email or f"{key}@example.test",
        token=f"{key}-token", demo_issues=True, org_vanity_host=vanity,
    )
    return seeded


def _urls(engine, org_id):
    """Every stored entity link of one workspace, keyed by table and id."""
    found = {}
    with engine.connect() as conn:
        for table in (issue, project, user, comment, attachment, webhook):
            rows = conn.execute(select(table.c.id, table.c.url).where(
                table.c.organization_id == org_id)).all()
            for row_id, url in rows:
                found[(table.name, row_id)] = url
    return found


# --------------------------------------------------------------- new rows


def test_a_new_link_names_the_workspace_host(tmp_path, canonical):
    engine = make_engine(tmp_path / "hosted.db")
    migrate(engine)
    seeded = _seed(engine, key="core", name="Core", vanity=CORE)
    writer = Writer(engine)

    issue_id = writer.issue_create(seeded["org_id"], seeded["user_id"],
                                   {"teamId": seeded["team_id"], "title": "Vanity link"})
    project_id = writer.project_create(seeded["org_id"], seeded["user_id"],
                                       {"name": "Vanity project"})
    with engine.connect() as conn:
        issue_row = conn.execute(select(issue.c.identifier, issue.c.url).where(
            issue.c.id == issue_id)).one()
        project_row = conn.execute(select(project.c.slug_id, project.c.url).where(
            project.c.id == project_id)).one()
        profile_url = conn.execute(select(user.c.url).where(
            user.c.id == seeded["user_id"])).scalar_one()

    # The path shape is unchanged; only the origin is the workspace's own.
    assert issue_row.url == f"https://{CORE}/core/issue/{issue_row.identifier}"
    assert project_row.url == f"https://{CORE}/core/project/{project_row.slug_id}"
    assert profile_url == f"https://{CORE}/core/profiles/{seeded['user_id']}"
    assert seeded["token"] and profile_url.startswith(f"https://{CORE}/")


def test_a_workspace_without_a_host_keeps_the_app_url(tmp_path, canonical):
    engine = make_engine(tmp_path / "canonical.db")
    migrate(engine)
    seeded = _seed(engine, key="plain", name="Plain")
    writer = Writer(engine)
    issue_id = writer.issue_create(seeded["org_id"], seeded["user_id"],
                                  {"teamId": seeded["team_id"], "title": "Canonical link"})
    with engine.connect() as conn:
        identifier, url = conn.execute(select(issue.c.identifier, issue.c.url).where(
            issue.c.id == issue_id)).one()
    assert url == f"{APP}/plain/issue/{identifier}"


def test_the_host_is_not_read_from_the_environment(tmp_path, canonical, monkeypatch):
    """A hosted workspace links to its host even when HOJA_APP_URL names another one."""
    engine = make_engine(tmp_path / "env.db")
    migrate(engine)
    seeded = _seed(engine, key="core", name="Core", vanity=CORE)
    monkeypatch.setenv("HOJA_APP_URL", "https://elsewhere.example")
    writer = Writer(engine)
    issue_id = writer.issue_create(seeded["org_id"], seeded["user_id"],
                                  {"teamId": seeded["team_id"], "title": "Host wins"})
    with engine.connect() as conn:
        url = conn.execute(select(issue.c.url).where(issue.c.id == issue_id)).scalar_one()
    assert url.startswith(f"https://{CORE}/core/issue/")


# ---------------------------------------------------------------- migration


@pytest.fixture
def stored_links(tmp_path, canonical):
    """A revision-72 database holding links the old rule wrote, for two workspaces."""
    engine = make_engine(tmp_path / "legacy-links.db")
    legacy_schema.create_schema(engine, 72)
    hosted = _seed(engine, key="core", name="Core", vanity=CORE, email="core@example.test")
    plain = _seed(engine, key="plain", name="Plain", email="plain@example.test")
    stamp = now_iso()
    with engine.begin() as conn:
        issue_id = conn.execute(select(issue.c.id).where(
            issue.c.organization_id == hosted["org_id"]).limit(1)).scalar_one()
        identifier = conn.execute(select(issue.c.identifier).where(
            issue.c.id == issue_id)).scalar_one()
        conn.execute(issue.update().where(issue.c.id == issue_id).values(
            url=f"{APP}/core/issue/{identifier}"))
        project_id = new_id()
        # project columns the writer always fills; the rest read as NULL.
        conn.execute(project.insert().values(
            id=project_id, organization_id=hosted["org_id"], name="Legacy project",
            slug_id="legacy-project", state="planned", progress=0, creator_id=hosted["user_id"],
            url=f"{APP}/core/project/legacy-project", created_at=stamp, updated_at=stamp))
        conn.execute(user.update().where(user.c.id == hosted["user_id"]).values(
            url=f"{APP}/core/profiles/{hosted['user_id']}"))
        comment_id = new_id()
        conn.execute(comment.insert().values(
            id=comment_id, organization_id=hosted["org_id"], issue_id=issue_id,
            body="legacy", user_id=hosted["user_id"],
            url=f"{APP}/core/issue/{identifier}#comment-abc12345",
            created_at=stamp, updated_at=stamp))
        # Links that are not ours to touch: an object store key, a webhook endpoint.
        attachment_id = new_id()
        conn.execute(attachment.insert().values(
            id=attachment_id, organization_id=hosted["org_id"], issue_id=issue_id,
            creator_id=hosted["user_id"], url="https://objects.example/core/issue/asset-1.png",
            title="asset-1.png", created_at=stamp))
        webhook_id = new_id()
        conn.execute(webhook.insert().values(
            id=webhook_id, organization_id=hosted["org_id"], url="https://hooks.example/hoja",
            secret_sealed="v2:not-a-real-seal", resource_types="[]", enabled=True,
            start_seq=0, creator_id=hosted["user_id"], created_at=stamp, updated_at=stamp))
        # The canonical workspace's link: the old rule was already right for it.
        plain_issue = conn.execute(select(issue.c.id, issue.c.identifier).where(
            issue.c.organization_id == plain["org_id"]).limit(1)).one()
    return {
        "engine": engine, "hosted": hosted, "plain": plain,
        "issue_id": issue_id, "identifier": identifier, "project_id": project_id,
        "comment_id": comment_id, "attachment_id": attachment_id, "webhook_id": webhook_id,
        "plain_issue_id": plain_issue.id, "plain_identifier": plain_issue.identifier,
    }


def test_the_migration_moves_stored_links_onto_the_workspace_host(stored_links, canonical):
    engine, hosted = stored_links["engine"], stored_links["hosted"]
    migrate(engine)
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION

    urls = _urls(engine, hosted["org_id"])
    ident = stored_links["identifier"]
    assert urls[("issue", stored_links["issue_id"])] == f"https://{CORE}/core/issue/{ident}"
    assert urls[("project", stored_links["project_id"])] == \
        f"https://{CORE}/core/project/legacy-project"
    assert urls[("user", hosted["user_id"])] == f"https://{CORE}/core/profiles/{hosted['user_id']}"
    # A comment's link is its issue's link plus a fragment: both halves survive.
    assert urls[("comment", stored_links["comment_id"])] == \
        f"https://{CORE}/core/issue/{ident}#comment-abc12345"


def test_the_migration_leaves_links_it_does_not_own_alone(stored_links, canonical):
    engine = stored_links["engine"]
    migrate(engine)
    hosted, plain = stored_links["hosted"], stored_links["plain"]
    urls = _urls(engine, hosted["org_id"])
    assert urls[("attachment", stored_links["attachment_id"])] == \
        "https://objects.example/core/issue/asset-1.png"
    assert urls[("webhook", stored_links["webhook_id"])] == "https://hooks.example/hoja"
    # A workspace with no host is untouched: HOJA_APP_URL was already its answer.
    assert _urls(engine, plain["org_id"])[("issue", stored_links["plain_issue_id"])] == \
        f"{APP}/plain/issue/{stored_links['plain_identifier']}"


def test_the_migration_is_idempotent(stored_links, canonical):
    engine = stored_links["engine"]
    migrate(engine)
    before = _urls(engine, stored_links["hosted"]["org_id"])
    # Re-running the revision itself (a raced owner, a replayed deploy) moves nothing.
    with engine.begin() as conn:
        assert db._apply_entity_url_hosts(conn) is None
    assert _urls(engine, stored_links["hosted"]["org_id"]) == before
    # And the public repair routine agrees it has nothing left to do.
    with engine.begin() as conn:
        assert db.repair_entity_urls(conn, stored_links["hosted"]["org_id"]) == 0


def test_a_host_pin_after_the_fact_rehosts_the_existing_links(tmp_path, canonical):
    """The setter path: a workspace given a host later moves the rows it already has."""
    engine = make_engine(tmp_path / "pin.db")
    migrate(engine)
    seeded = _seed(engine, key="core", name="Core")
    with engine.begin() as conn:
        assert db.repair_entity_urls(conn, seeded["org_id"]) == 0
        conn.execute(update(organization).where(organization.c.id == seeded["org_id"])
                     .values(vanity_host=CORE))
        assert db.repair_entity_urls(conn, seeded["org_id"]) > 0
    urls = _urls(engine, seeded["org_id"])
    issue_urls = [url for (table, _), url in urls.items() if table == "issue"]
    assert issue_urls and all(url.startswith(f"https://{CORE}/core/issue/") for url in issue_urls)


@pytest.mark.skipif(not os.environ.get("HOJA_TEST_POSTGRES_URL"), reason="needs PostgreSQL")
def test_the_repair_runs_on_postgres(canonical):
    """The deployment's own dialect: the rewrite is portable, not SQLite-shaped.

    Runs on the real server (row-level security policies are installed by this
    database's own classification) and reads the rows back through the store, so
    the rewritten link is the one an authenticated request sees.
    """
    url, _schema = rls_mode.new_schema_url()
    engine = make_engine(url)
    try:
        migrate(engine)
        seeded = _seed(engine, key="core", name="Core", vanity=CORE)
        with engine.begin() as conn:
            identifier = conn.execute(select(issue.c.identifier).where(
                issue.c.organization_id == seeded["org_id"]).limit(1)).scalar_one()
            # Exactly what the old rule stored, tenanted by RLS like any row.
            conn.execute(issue.update().where(issue.c.organization_id == seeded["org_id"])
                         .values(url=f"{APP}/core/issue/{identifier}"))
            assert db._apply_entity_url_hosts(conn) is None
        with engine.connect() as conn:
            fixed = conn.execute(select(issue.c.url).where(and_(
                issue.c.organization_id == seeded["org_id"],
                issue.c.identifier == identifier))).scalar_one()
        assert fixed == f"https://{CORE}/core/issue/{identifier}"
        # A second pass is still a no-op on this dialect.
        with engine.begin() as conn:
            assert db.repair_entity_urls(conn, seeded["org_id"]) == 0
    finally:
        engine.dispose()
