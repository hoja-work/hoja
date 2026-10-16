"""PostgreSQL integration tests, gated by CLINIAR_TEST_POSTGRES_URL."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import delete, func, select

from cliniar_server.db import (
    change_event,
    issue,
    issue_label_link,
    issue_subscriber,
    make_engine,
    metadata,
    migrate,
    organization,
    project_member,
    project_team,
    resolve_token_identity,
    seed_tenant,
    team,
    team_member,
)
from cliniar_server.store import Store
from cliniar_server.writer import Writer

POSTGRES_URL = os.environ.get("CLINIAR_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="CLINIAR_TEST_POSTGRES_URL is not configured",
)


def _cleanup(engine, org_ids: list[str]) -> None:
    with engine.begin() as conn:
        issue_ids = select(issue.c.id).where(issue.c.organization_id.in_(org_ids))
        team_ids = select(team.c.id).where(team.c.organization_id.in_(org_ids))
        conn.execute(delete(issue_label_link).where(issue_label_link.c.issue_id.in_(issue_ids)))
        conn.execute(delete(issue_subscriber).where(issue_subscriber.c.issue_id.in_(issue_ids)))
        conn.execute(delete(team_member).where(team_member.c.team_id.in_(team_ids)))
        conn.execute(
            delete(project_member).where(
                project_member.c.project_id.in_(
                    select(metadata.tables["project"].c.id).where(
                        metadata.tables["project"].c.organization_id.in_(org_ids)
                    )
                )
            )
        )
        conn.execute(
            delete(project_team).where(
                project_team.c.project_id.in_(
                    select(metadata.tables["project"].c.id).where(
                        metadata.tables["project"].c.organization_id.in_(org_ids)
                    )
                )
                | project_team.c.team_id.in_(team_ids)
            )
        )
        for table in reversed(metadata.sorted_tables):
            if "organization_id" in table.c:
                conn.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
        conn.execute(delete(organization).where(organization.c.id.in_(org_ids)))


def test_postgres_shared_database_isolation_tokens_and_atomic_identifiers() -> None:
    assert POSTGRES_URL is not None
    engine = make_engine(POSTGRES_URL)
    migrate(engine)
    assert engine.dialect.name == "postgresql"
    assert engine.pool._pre_ping is True

    suffix = uuid.uuid4().hex[:12]
    org_ids: list[str] = []
    try:
        alpha = seed_tenant(
            engine,
            org_name="Alpha Test",
            org_url_key=f"alpha-{suffix}",
            team_key="ENG",
            user_name="Alpha User",
            user_email=f"alpha-{suffix}@example.test",
            demo_issues=False,
        )
        org_ids.append(alpha["org_id"])
        beta = seed_tenant(
            engine,
            org_name="Beta Test",
            org_url_key=f"beta-{suffix}",
            team_key="ENG",
            user_name="Beta User",
            user_email=f"beta-{suffix}@example.test",
            demo_issues=False,
        )
        org_ids.append(beta["org_id"])

        with pytest.raises(ValueError, match="--organization is required"):
            resolve_token_identity(engine)
        with pytest.raises(ValueError, match="--user or --email is required"):
            resolve_token_identity(engine, organization_ref=f"alpha-{suffix}")
        assert resolve_token_identity(
            engine,
            organization_ref=f"beta-{suffix}",
            email=f"beta-{suffix}@example.test",
        ) == (beta["user_id"], beta["org_id"])

        writer = Writer(engine)

        def create(number: int) -> str | None:
            return writer.issue_create(
                alpha["org_id"],
                alpha["user_id"],
                {"teamId": "ENG", "title": f"Postgres issue {number}"},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(create, range(20)))
        assert all(ids)

        issues = Store(engine).issues(alpha["org_id"], alpha["user_id"], None)
        assert {row["identifier"] for row in issues} == {
            f"ENG-{number}" for number in range(1, 21)
        }
        assert Store(engine).issues(beta["org_id"], beta["user_id"], None) == []
    finally:
        try:
            if org_ids:
                _cleanup(engine, org_ids)
        finally:
            engine.dispose()


def test_postgres_issue_link_changes_publish_issue_updates() -> None:
    assert POSTGRES_URL is not None
    engine = make_engine(POSTGRES_URL)
    migrate(engine)
    suffix = uuid.uuid4().hex[:12]
    org_ids: list[str] = []
    try:
        seeded = seed_tenant(engine, org_name="Links", org_url_key=f"links-{suffix}",
                             user_email=f"links-{suffix}@example.test", demo_issues=True)
        org_ids.append(seeded["org_id"])
        with engine.begin() as conn:
            issue_id = conn.execute(select(issue.c.id).where(
                issue.c.organization_id == seeded["org_id"])).scalars().first()
            before = conn.execute(select(func.max(change_event.c.seq))).scalar()
            conn.execute(issue_subscriber.insert().values(issue_id=issue_id, user_id=seeded["user_id"]))
            conn.execute(delete(issue_subscriber).where(issue_subscriber.c.issue_id == issue_id))
            events = conn.execute(select(change_event.c.entity, change_event.c.entity_id, change_event.c.action)
                                  .where(change_event.c.seq > before)).all()
        assert [tuple(e) for e in events] == [("issue", issue_id, "update")] * 2
    finally:
        try:
            if org_ids:
                _cleanup(engine, org_ids)
        finally:
            engine.dispose()
