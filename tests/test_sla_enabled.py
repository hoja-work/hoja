"""HOJA-734: saving an SLA policy only changes ``enabled`` when the input says so.

Before this, ``slaPolicyUpdate`` treated a missing ``enabled`` as True, so editing
a disabled policy's targets silently switched it back on.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from tests.test_slas import _gql, _setup

CREATE = (
    "mutation($i: SlaPolicyInput!) { slaPolicyCreate(input: $i) "
    "{ success slaPolicy { id enabled } } }"
)
UPDATE = (
    "mutation($id: String!, $i: SlaPolicyInput!) { slaPolicyUpdate(id: $id, input: $i) "
    "{ success slaPolicy { id enabled resolutionMinutes } } }"
)


def test_update_without_enabled_keeps_the_current_state(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    token = admin["token"]
    with TestClient(app) as client:
        made = _gql(client, token, CREATE, {"i": {"name": "Std", "resolutionMinutes": 60}})
        assert "errors" not in made, made
        policy = made["data"]["slaPolicyCreate"]["slaPolicy"]
        assert policy["enabled"] is True, "a new policy starts enabled"

        off = _gql(client, token, UPDATE, {"id": policy["id"], "i": {
            "name": "Std", "resolutionMinutes": 60, "enabled": False}})
        assert off["data"]["slaPolicyUpdate"]["slaPolicy"]["enabled"] is False

        edited = _gql(client, token, UPDATE, {"id": policy["id"], "i": {
            "name": "Std", "resolutionMinutes": 90}})
        assert "errors" not in edited, edited
        row = edited["data"]["slaPolicyUpdate"]["slaPolicy"]
        assert row["resolutionMinutes"] == 90
        assert row["enabled"] is False, "editing must not re-enable a disabled policy"

        on = _gql(client, token, UPDATE, {"id": policy["id"], "i": {
            "name": "Std", "resolutionMinutes": 90, "enabled": True}})
        assert on["data"]["slaPolicyUpdate"]["slaPolicy"]["enabled"] is True


def test_create_can_start_disabled(tmp_path) -> None:
    _engine, app, admin = _setup(tmp_path)
    with TestClient(app) as client:
        made = _gql(client, admin["token"], CREATE, {"i": {
            "name": "Later", "resolutionMinutes": 60, "enabled": False}})
        assert made["data"]["slaPolicyCreate"]["slaPolicy"]["enabled"] is False
