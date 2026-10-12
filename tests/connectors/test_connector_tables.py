"""Connector tables: migration revision 23 creates them and rows round-trip with the expected constraints."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from cliniar_server.db import (
    CURRENT_SCHEMA_REVISION,
    applied_schema_revision,
    connector_account,
    connector_action,
    make_engine,
    migrate,
)


def _engine(tmp_path):
    engine = make_engine(tmp_path / "connectors.db")
    migrate(engine)
    return engine


def test_migration_creates_connector_tables_at_current_revision(tmp_path) -> None:
    engine = _engine(tmp_path)
    names = set(inspect(engine).get_table_names())
    assert {"connector_account", "connector_action"} <= names
    assert applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION == 25


def test_account_row_round_trips_without_plaintext_token(tmp_path) -> None:
    engine = _engine(tmp_path)
    row = {
        "id": "acct_1",
        "organization_id": "org_1",
        "owner_user_id": "user_1",
        "provider": "google_workspace",
        "external_account": "ana@example.test",
        "scopes": "gmail.readonly drive.readonly",
        "encrypted_refresh_token": "ciphertext-only",
        "status": "active",
        "created_at": "2026-10-11T00:00:00Z",
        "updated_at": "2026-10-11T00:00:00Z",
    }
    with engine.begin() as conn:
        conn.execute(connector_account.insert().values(**row))
    with engine.connect() as conn:
        stored = (
            conn.execute(select(connector_account).where(connector_account.c.id == "acct_1"))
            .mappings()
            .one()
        )
    assert stored["encrypted_refresh_token"] == "ciphertext-only"
    assert stored["revoked_at"] is None


def test_action_requires_existing_account(tmp_path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            connector_action.insert().values(
                id="act_1",
                organization_id="org_1",
                account_id="missing",
                requested_by_user_id="user_1",
                tool="gmail.send",
                args_json="{}",
                status="pending",
                created_at="2026-10-11T00:00:00Z",
            )
        )


def test_action_for_existing_account_is_stored_pending(tmp_path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            connector_account.insert().values(
                id="acct_2",
                organization_id="org_1",
                provider="google_workspace",
                external_account="ana@example.test",
                scopes="s",
                encrypted_refresh_token="c",
                status="active",
                created_at="t",
                updated_at="t",
            )
        )
        conn.execute(
            connector_action.insert().values(
                id="act_2",
                organization_id="org_1",
                account_id="acct_2",
                requested_by_user_id="user_1",
                tool="gmail.send",
                args_json="{}",
                status="pending",
                created_at="t",
            )
        )
    with engine.connect() as conn:
        status = conn.execute(select(connector_action.c.status)).scalar_one()
    assert status == "pending"
