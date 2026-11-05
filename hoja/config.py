"""Config loading and validation.

Lookup order:
  1. $HOJA_CONFIG env var (absolute path)
  2. Existing or new $XDG_CONFIG_HOME/hoja/config.toml

Token resolution order (per account):
  1. --token CLI flag (passed in)
  2. $<ACCOUNT_TOKEN_ENV> environment variable (default: LINEAR_TOKEN)
  3. config.toml [accounts.<name>].token (discouraged)

Account resolution order:
  1. --account CLI flag
  2. Workspace-mapped account (git repo root → config.workspaces)
  3. Global default account (config.defaults.default_account)
  4. First available account (fallback)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

try:
    import tomli_w
except ImportError:  # pragma: no cover
    tomli_w = None  # type: ignore[assignment]

import contextlib

from hoja.errors import AuthError, UsageError


class AccountConfig(BaseModel):
    """A named Linear account."""

    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    token_env: str = "LINEAR_TOKEN"
    org_name: str | None = None
    # GraphQL endpoint override. Point this at a local hoja-serve backend
    # (e.g. "http://127.0.0.1:8787/graphql") to run fully offline. When unset,
    # hoja talks to the configured Linear-compatible API. $LINEAR_API_URL overrides this too.
    base_url: str | None = None
    # Team keys this account owns (e.g. ["SWA", "ENG"]). Used for intelligent
    # account auto-selection: a command targeting team SWA (via --team SWA or
    # an identifier like SWA-20) picks the account that lists "SWA" here.
    teams: list[str] = Field(default_factory=list)


class DefaultsConfig(BaseModel):
    """Default values applied to commands."""

    model_config = ConfigDict(extra="forbid")

    team: str | None = None
    output: str = "human"
    editor: str | None = None
    default_account: str | None = None


class DisplayConfig(BaseModel):
    """Display preferences."""

    model_config = ConfigDict(extra="forbid")

    color: bool = True
    table_max_width: int = 120
    truncate_descriptions: int = 80


class Config(BaseModel):
    """Top-level Hoja configuration."""

    model_config = ConfigDict(extra="forbid")

    # Multi-account support (replaces single [auth] section)
    accounts: dict[str, AccountConfig] = Field(default_factory=dict)
    # Legacy [auth] section — present for loading old configs, migrated to accounts.default
    auth: dict[str, Any] | None = None

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    aliases: dict[str, str] = Field(default_factory=dict)
    views: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Per-workspace account mappings (absolute repo path → account name)
    workspaces: dict[str, str] = Field(default_factory=dict)


def config_path() -> Path:
    """Resolve config file location."""
    if env_path := os.environ.get("HOJA_CONFIG"):
        return Path(env_path).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    canonical = base / "hoja" / "config.toml"
    if canonical.exists():
        return canonical
    return canonical


def _migrate_legacy_auth(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old [auth] section to accounts.default."""
    auth = data.pop("auth", None)
    if auth and isinstance(auth, dict):
        accounts = data.setdefault("accounts", {})
        # Only migrate if accounts.default doesn't already exist
        if "default" not in accounts:
            accounts["default"] = {}
        default = accounts["default"]
        if "token" in auth and "token" not in default:
            default["token"] = auth["token"]
        if "token_env" in auth and "token_env" not in default:
            default["token_env"] = auth["token_env"]
    return data


def load_config(path: Path | None = None) -> Config:
    """Load and validate config.toml. Returns defaults if file missing."""
    p = path or config_path()
    if not p.exists():
        return Config()
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise UsageError(
            f"Invalid TOML in config file: {e}",
            hint=f"Edit {p} to fix syntax",
        ) from e

    # Migrate legacy [auth] section if present
    data = _migrate_legacy_auth(data)

    try:
        return Config(**data)
    except Exception as e:
        raise UsageError(
            f"Invalid config: {e}",
            hint="See docs/DESIGN.md for the config schema",
        ) from e


def save_config(config: Config, path: Path | None = None) -> None:
    """Save config to disk as TOML."""
    if tomli_w is None:
        raise UsageError(
            "Cannot write config: tomli-w not installed",
            hint="pip install tomli-w",
        )
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _config_to_dict(config)
    with open(p, "wb") as f:
        tomli_w.dump(data, f)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)


def _config_to_dict(config: Config) -> dict[str, Any]:
    """Serialize Config to a plain dict for TOML writing."""
    data: dict[str, Any] = {}
    if config.accounts:
        data["accounts"] = {
            name: acc.model_dump(exclude_none=True, exclude_defaults=True)
            for name, acc in config.accounts.items()
        }
    defaults_dict = config.defaults.model_dump(exclude_defaults=True)
    if defaults_dict:
        data["defaults"] = defaults_dict
    display_dict = config.display.model_dump(exclude_defaults=True)
    if display_dict:
        data["display"] = display_dict
    if config.aliases:
        data["aliases"] = config.aliases
    if config.views:
        data["views"] = config.views
    if config.workspaces:
        data["workspaces"] = config.workspaces
    return data


def _find_git_root(start: Path | None = None) -> Path | None:
    """Walk up from start (or cwd) looking for a .git directory."""
    start = start or Path.cwd()
    path = start.resolve()
    for parent in [path, *path.parents]:
        if (parent / ".git").is_dir():
            return parent
    return None


def resolve_workspace(path: Path | None = None) -> Path | None:
    """Resolve the current workspace directory (git repo root or None)."""
    return _find_git_root(path)


def team_key_from_hint(hint: str | None) -> str | None:
    """Extract an uppercase team key from a --team value or an issue identifier.

    Accepts a bare key ("SWA") or an identifier ("SWA-20") and returns "SWA".
    Returns None for UUIDs, empty values, or anything without a key prefix.
    """
    if not hint:
        return None
    h = hint.strip()
    # UUIDs (36 chars, 4 dashes) are not team keys.
    if len(h) == 36 and h.count("-") == 4:
        return None
    # Identifier form KEY-123 → KEY
    prefix = h.split("-", 1)[0]
    if prefix.isalpha():
        return prefix.upper()
    return None


def resolve_account(
    cli_account: str | None,
    config: Config,
    workspace_path: Path | None = None,
    team_key: str | None = None,
) -> tuple[str, AccountConfig]:
    """Resolve which account to use.

    Order:
      1. --account CLI flag
      2. Team-key-owning account (account.teams contains team_key)
      3. Workspace-mapped account
      4. Global default account
      5. First available account (fallback)
      6. Not found → synthetic default (lets resolve_token check $LINEAR_TOKEN)

    Returns (account_name, account_config).
    """
    # 1. CLI flag
    if cli_account:
        if cli_account not in config.accounts:
            raise AuthError(
                f"Account '{cli_account}' not found",
                hint="Run 'hoja auth accounts' to see available accounts.",
            )
        return cli_account, config.accounts[cli_account]

    # 2. Team-key ownership — pick the account that declares this team key.
    key = team_key_from_hint(team_key)
    if key:
        for name, acc in config.accounts.items():
            if any(t.strip().upper() == key for t in acc.teams):
                return name, acc

    # 3. Workspace-mapped
    workspace = resolve_workspace(workspace_path)
    if workspace:
        ws_key = str(workspace)
        mapped = config.workspaces.get(ws_key)
        if mapped and mapped in config.accounts:
            return mapped, config.accounts[mapped]

    # 4. Global default
    if (
        config.defaults.default_account
        and config.defaults.default_account in config.accounts
    ):
        return (
            config.defaults.default_account,
            config.accounts[config.defaults.default_account],
        )

    # 5. Fallback to any configured account
    if config.accounts:
        first_name = next(iter(config.accounts))
        return first_name, config.accounts[first_name]

    # 6. Legacy fallback: no accounts configured but $LINEAR_TOKEN may exist.
    # Return a synthetic default account so resolve_token can check the env var.
    return "default", AccountConfig(token_env="LINEAR_TOKEN")


def resolve_token(
    cli_token: str | None,
    account: AccountConfig,
) -> str:
    """Resolve the Linear API token using precedence rules.

    Order: --token > $<ACCOUNT_TOKEN_ENV> > account.token
    """
    if cli_token:
        return cli_token
    env_var = account.token_env or "LINEAR_TOKEN"
    if env_token := os.environ.get(env_var):
        return env_token
    if account.token:
        return account.token
    raise AuthError(
        "No Linear API token found",
        hint=(
            f"Set ${env_var}, pass --token, or configure token in "
            f"{config_path()}"
        ),
    )


def resolve_base_url(
    cli_base_url: str | None,
    account: AccountConfig,
) -> str | None:
    """Resolve the GraphQL endpoint override.

    Order: --api-url flag > $LINEAR_API_URL env > account.base_url > None.
    Returning None means "use the LinearClient default" (the real Linear API).
    """
    if cli_base_url:
        return cli_base_url
    if env_url := os.environ.get("LINEAR_API_URL"):
        return env_url
    return account.base_url


def redact_token(token: str) -> str:
    """Show only the last 4 chars of a token for logging."""
    if len(token) <= 8:
        return "***"
    return f"{token[:8]}…{token[-4:]}"
