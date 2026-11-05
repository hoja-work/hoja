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
    env_with_legacy,
    resolve_account,
    resolve_base_url,
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
    assert acc.token_env == "HOJA_TOKEN"


# ----------------- resolve_token -----------------


def test_token_cli_overrides(monkeypatch):
    monkeypatch.setenv("HOJA_TOKEN", "from_env")
    acc = AccountConfig(token="from_config")
    assert resolve_token("from_cli", acc) == "from_cli"


def test_token_env_overrides_config(monkeypatch):
    monkeypatch.setenv("HOJA_TOKEN", "from_env")
    acc = AccountConfig(token="from_config")
    assert resolve_token(None, acc) == "from_env"


def test_token_config_last(monkeypatch):
    monkeypatch.delenv("HOJA_TOKEN", raising=False)
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    acc = AccountConfig(token="from_config")
    assert resolve_token(None, acc) == "from_config"


# -------- env-var aliases: HOJA_* canonical, LINEAR_* deprecated --------------
#
# The Linear-compatible wire surface keeps accepting the old names, but the
# HOJA_* variable must win whenever both are present.


def _clear_auth_env(monkeypatch) -> None:
    for name in ("HOJA_TOKEN", "LINEAR_TOKEN", "HOJA_API_URL", "LINEAR_API_URL"):
        monkeypatch.delenv(name, raising=False)


def test_hoja_token_wins_when_both_set(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("HOJA_TOKEN", "from_hoja")
    monkeypatch.setenv("LINEAR_TOKEN", "from_linear")
    assert resolve_token(None, AccountConfig()) == "from_hoja"


def test_linear_token_still_works_alone(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("LINEAR_TOKEN", "from_linear")
    assert resolve_token(None, AccountConfig()) == "from_linear"


def test_missing_token_error_names_both_vars(monkeypatch):
    _clear_auth_env(monkeypatch)
    with pytest.raises(AuthError) as excinfo:
        resolve_token(None, AccountConfig())
    message = str(excinfo.value) + str(getattr(excinfo.value, "hint", ""))
    assert "HOJA_TOKEN" in message
    assert "LINEAR_TOKEN" in message


def test_custom_token_env_does_not_inherit_linear_alias(monkeypatch):
    # An account that names its own variable must not silently fall back to
    # LINEAR_TOKEN; only the canonical HOJA_TOKEN gets that alias.
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("LINEAR_TOKEN", "from_linear")
    acc = AccountConfig(token="from_config", token_env="ACME_TOKEN")
    assert resolve_token(None, acc) == "from_config"


# ----------------- resolve_base_url + aliases -----------------


def test_hoja_api_url_wins_when_both_set(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("HOJA_API_URL", "http://hoja.example/graphql")
    monkeypatch.setenv("LINEAR_API_URL", "http://linear.example/graphql")
    assert resolve_base_url(None, AccountConfig()) == "http://hoja.example/graphql"


def test_linear_api_url_still_works_alone(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("LINEAR_API_URL", "http://linear.example/graphql")
    assert resolve_base_url(None, AccountConfig()) == "http://linear.example/graphql"


def test_api_url_precedence_flag_env_account(monkeypatch):
    _clear_auth_env(monkeypatch)
    acc = AccountConfig(base_url="http://account.example/graphql")
    monkeypatch.setenv("HOJA_API_URL", "http://env.example/graphql")
    assert resolve_base_url("http://flag.example/graphql", acc) == "http://flag.example/graphql"
    assert resolve_base_url(None, acc) == "http://env.example/graphql"
    monkeypatch.delenv("HOJA_API_URL")
    assert resolve_base_url(None, acc) == "http://account.example/graphql"


def test_env_with_legacy_reads_alias_only_when_canonical_is_unset(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("LINEAR_TOKEN", "alias")
    assert env_with_legacy("HOJA_TOKEN") == "alias"
    monkeypatch.setenv("HOJA_TOKEN", "canonical")
    assert env_with_legacy("HOJA_TOKEN") == "canonical"
    # Names without an alias mapping are read verbatim.
    assert env_with_legacy("UNMAPPED_VAR") is None


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
