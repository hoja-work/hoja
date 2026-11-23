"""Shared CLI state — populated by the root callback, read by subcommands.

Typer doesn't have a great way to share state across commands without going
through Context. We use a small module-level container to keep subcommands
clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hoja.client import GraphClient
from hoja.config import Config

if TYPE_CHECKING:
    from hoja.credentials import OAuthSession
from hoja.models.enums import OutputFormat


@dataclass
class CLIState:
    """Mutable per-invocation state."""

    token: str = ""
    config: Config = field(default_factory=Config)
    output: OutputFormat = OutputFormat.HUMAN
    verbose: bool = False
    quiet: bool = False
    no_color: bool = False
    dry_run: bool = False
    timeout: float = 30.0
    account_name: str = ""  # active account name for context display
    base_url: str | None = None  # GraphQL endpoint override (local backend)
    # Where the credential came from: "--token", "$HOJA_TOKEN", "oauth", "config".
    credential_source: str = ""
    session: OAuthSession | None = None  # set when the account uses `hoja auth login`
    # Why no credential could be resolved, raised on the first API call.
    auth_error: Exception | None = None


_state = CLIState()


def get_state() -> CLIState:
    return _state


def set_state(state: CLIState) -> None:
    global _state
    _state = state


def build_client(state: CLIState | None = None) -> GraphClient:
    """Construct a GraphClient configured from the current CLI state."""
    s = state or _state
    if s.session is not None:
        # An OAuth account always carries its own endpoint.
        return GraphClient(
            base_url=s.base_url or s.session.base_url,
            timeout=s.timeout,
            verbose=s.verbose,
            credential=s.session,
        )
    if not s.token and s.auth_error is not None:
        raise s.auth_error
    if s.base_url:
        return GraphClient(s.token, base_url=s.base_url, timeout=s.timeout, verbose=s.verbose)
    return GraphClient(s.token, timeout=s.timeout, verbose=s.verbose)
