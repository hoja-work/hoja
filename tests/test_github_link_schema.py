"""Schema tests for the github_link table (revision 50)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from hoja_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    github_link,
    issue,
    make_engine,
    metadata,
    migrate,
    now_iso,
    schema_revision,
    seed_tenant,
)


def _row(**overrides):
    values = {
        "id": "gl_1",
        "organization_id": "org_1",
        "issue_id": "iss_1",
        "kind": "pr",
        "external_id": "4242",
        "url": "https://github.com/hoja-work/hoja/pull/67",
        "title": "detect linked issue identifiers",
        "state": "open",
        "author": "rinadelph",
        "sha": "343458f",
        "environment": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return values


def _seeded(tmp_path, name: str):
    """A migrated database with a real organization and issue to hang links on."""
    engine = make_engine(tmp_path / name)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="GitHub Links", org_url_key=f"github-links-{name}"
    )
    with engine.connect() as conn:
        issue_id = conn.execute(select(issue.c.id).limit(1)).scalar_one()
    return engine, seeded["org_id"], issue_id


def test_revision_50_creates_the_link_table(tmp_path) -> None:
    engine = make_engine(tmp_path / "link.db")
    migrate(engine)

    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    assert CURRENT_SCHEMA_REVISION >= 50
    assert inspect(engine).has_table(github_link.name)

    index_names = {i["name"] for i in inspect(engine).get_indexes(github_link.name)}
    # issue_id and organization_id are indexed for the two lookups the panel makes.
    assert any("issue_id" in name for name in index_names)
    assert any("organization_id" in name for name in index_names)


def test_revision_50_upgrades_a_revision_49_database(tmp_path) -> None:
    engine = make_engine(tmp_path / "prior.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        github_link.drop(conn)
        conn.execute(schema_revision.insert().values(revision=1, applied_at=now_iso()))

    migrate(engine)

    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION
    assert inspect(engine).has_table(github_link.name)


def test_duplicate_external_object_is_rejected(tmp_path) -> None:
    engine, org_id, issue_id = _seeded(tmp_path, "unique.db")
    with engine.begin() as conn:
        conn.execute(github_link.insert().values(**_row(organization_id=org_id, issue_id=issue_id)))

    # The same GitHub object re-delivered must not become a second row.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            github_link.insert().values(
                **_row(id="gl_2", organization_id=org_id, issue_id=issue_id)
            )
        )


def test_the_same_external_id_may_exist_for_another_kind_or_org(tmp_path) -> None:
    engine, org_id, issue_id = _seeded(tmp_path, "distinct.db")
    other = seed_tenant(
        engine, org_name="GitHub Links Two", org_url_key="github-links-two"
    )
    with engine.begin() as conn:
        conn.execute(github_link.insert().values(**_row(organization_id=org_id, issue_id=issue_id)))
        conn.execute(
            github_link.insert().values(
                **_row(id="gl_2", kind="commit", organization_id=org_id, issue_id=issue_id)
            )
        )
        # A different organization may legitimately link the same external id.
        conn.execute(
            github_link.insert().values(
                **_row(id="gl_3", organization_id=other["org_id"], issue_id=issue_id)
            )
        )

    with engine.connect() as conn:
        assert len(conn.execute(select(github_link.c.id)).all()) == 3
