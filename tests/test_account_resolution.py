"""Unit tests for intelligent account/token resolution and WorkflowState.

These tests are token-free and offline — they exercise the pure resolution
logic in hoja.config plus the WorkflowState smart-union introduced to
tolerate undocumented Linear workflow state types (e.g. "duplicate").
"""

from __future__ import annotations

import pytest

from hoja.config import (
    AccountConfig,
    Config,
    DefaultsConfig,
    resolve_account,
    resolve_token,
    team_key_from_hint,
)
from hoja.errors import AuthError
from hoja.models.workflow import WorkflowState, WorkflowStateType

# ----------------- team_key_from_hint -----------------


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("SWA", "SWA"),
        ("SWA-20", "SWA"),
        ("eng-123", "ENG"),
        (None, None),
        ("", None),
        ("123", None),
        # UUID is not a team key
        ("014d5784-ff77-4f06-aec2-ac20b32c6e1d", None),
    ],
)
def test_team_key_from_hint(hint, expected):
    assert team_key_from_hint(hint) == expected


# ----------------- resolve_account -----------------


def _cfg() -> Config:
    return Config(
        accounts={
            "work": AccountConfig(token="lin_api_work", teams=["SWA"]),
            "personal": AccountConfig(token="lin_api_personal", teams=["PER"]),
        },
        defaults=DefaultsConfig(default_account="personal"),
    )


def test_cli_account_flag_wins():
    name, acc = resolve_account("work", _cfg(), team_key="PER-1")
    assert name == "work"


def test_team_key_selects_owning_account():
    # No --account, no workspace; team key SWA → account "work".
    name, acc = resolve_account(None, _cfg(), team_key="SWA-20")
    assert name == "work"
    assert acc.token == "lin_api_work"


def test_team_key_bare_key_selects_account():
    name, _ = resolve_account(None, _cfg(), team_key="PER")
    assert name == "personal"


def test_falls_back_to_default_when_team_unknown():
    name, _ = resolve_account(None, _cfg(), team_key="ZZZ-9")
    assert name == "personal"  # default_account


def test_falls_back_to_default_when_no_team():
    name, _ = resolve_account(None, _cfg())
    assert name == "personal"


def test_unknown_cli_account_raises():
    with pytest.raises(AuthError):
        resolve_account("nope", _cfg())


def test_synthetic_default_when_empty():
    name, acc = resolve_account(None, Config())
    assert name == "default"
    assert acc.token_env == "LINEAR_TOKEN"


# ----------------- resolve_token -----------------


def test_token_cli_overrides(monkeypatch):
    monkeypatch.setenv("LINEAR_TOKEN", "from_env")
    acc = AccountConfig(token="from_config")
    assert resolve_token("from_cli", acc) == "from_cli"


def test_token_env_overrides_config(monkeypatch):
    monkeypatch.setenv("LINEAR_TOKEN", "from_env")
    acc = AccountConfig(token="from_config")
    assert resolve_token(None, acc) == "from_env"


def test_token_config_last(monkeypatch):
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    acc = AccountConfig(token="from_config")
    assert resolve_token(None, acc) == "from_config"


# ----------------- WorkflowState smart-union -----------------


def test_known_state_type_is_enum():
    s = WorkflowState(id="1", name="Done", type="completed")
    assert s.type is WorkflowStateType.COMPLETED
    assert s.type == "completed"


def test_unknown_state_type_passes_through_as_str():
    # This is the bug fix: "duplicate" is not in the documented enum but must
    # not raise — it should round-trip as a plain string.
    s = WorkflowState(id="2", name="Duplicate", type="duplicate")
    assert s.type == "duplicate"
    assert isinstance(s.type, str)


def test_workflowstatetype_coerce():
    assert WorkflowStateType.coerce("started") is WorkflowStateType.STARTED
    assert WorkflowStateType.coerce("duplicate") == "duplicate"
