"""Chat is the second scope add-on on the existing Google grant.

These tests pin what makes that safe: the upgrade asks for exactly the three Chat scopes, none of
them can write, and the capability is reported only when all three are present.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from google_workspace.config import GoogleOAuthConfig
from google_workspace.oauth import (
    ADDON_LABELS,
    ADDON_SCOPES,
    CAPABILITY_SCOPES,
    CHAT_MEMBERSHIPS_SCOPE,
    CHAT_MESSAGES_SCOPE,
    CHAT_SCOPES,
    CHAT_SPACES_SCOPE,
    READ_SCOPES,
    WRITE_SCOPES,
    authorization_url,
    capabilities_for,
    pkce_pair,
)

CONFIG = GoogleOAuthConfig(
    client_id="client-123.apps.googleusercontent.com",
    client_secret="GOCSPX-test-secret",
    redirect_uri="https://hoja.example.test/api/connectors/google/callback",
)


def _scope_string(scopes: list[str]) -> str:
    _verifier, challenge = pkce_pair()
    url = authorization_url(CONFIG, scopes=scopes, state="sealed-state", code_challenge=challenge)
    return parse_qs(urlparse(url).query)["scope"][0]


def test_the_chat_addon_holds_the_three_read_only_scopes() -> None:
    assert ADDON_SCOPES["chat"] == (
        "https://www.googleapis.com/auth/chat.spaces.readonly",
        "https://www.googleapis.com/auth/chat.messages.readonly",
        "https://www.googleapis.com/auth/chat.memberships.readonly",
    )
    assert CHAT_SCOPES == (CHAT_SPACES_SCOPE, CHAT_MESSAGES_SCOPE, CHAT_MEMBERSHIPS_SCOPE)
    assert all(scope.endswith(".readonly") for scope in CHAT_SCOPES)


def test_no_chat_capable_scope_reaches_the_write_sets() -> None:
    for scope in CHAT_SCOPES:
        assert scope not in WRITE_SCOPES.values()
        assert scope not in READ_SCOPES.values()


def test_an_upgrade_asks_for_chat_scopes_only_and_adds_nothing_else() -> None:
    """One OAuth client serves Workspace, Meet and Chat. Sending the other scopes on a Chat connect
    would re-ask for Gmail, Drive and Meet access and widen consent for no reason."""
    requested = _scope_string(list(ADDON_SCOPES["chat"]))
    assert set(requested.split()) == set(CHAT_SCOPES)
    for other in READ_SCOPES.values():
        assert other not in requested.split()


def test_chat_is_reported_available_only_when_all_three_scopes_are_present() -> None:
    assert capabilities_for(" ".join(CHAT_SCOPES))["chat"] is True
    for missing in CHAT_SCOPES:
        partial = " ".join(scope for scope in CHAT_SCOPES if scope != missing)
        assert capabilities_for(partial)["chat"] is False, missing
    assert capabilities_for("")["chat"] is False


def test_a_chat_grant_does_not_claim_meet_or_workspace_access() -> None:
    capabilities = capabilities_for(" ".join(CHAT_SCOPES))
    assert capabilities["meet"] is False
    assert capabilities["gmail"] is False


def test_every_addon_has_a_human_label_for_the_consent_copy() -> None:
    """The route interpolates the label into its errors, so a registry entry without one would
    raise a KeyError in the middle of an OAuth callback."""
    assert set(ADDON_LABELS) == set(ADDON_SCOPES)
    assert ADDON_LABELS["chat"] == "Google Chat"


def test_every_capability_maps_to_a_non_empty_scope_tuple() -> None:
    for name, scopes in CAPABILITY_SCOPES.items():
        assert isinstance(scopes, tuple) and scopes, name
