"""Google Meet is a scope add-on on the existing Google grant, not a second account.

These tests pin the two properties that make that true: the upgrade request carries the Meet scope
and nothing else, and the grant recorded for an account stays a union across consents.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from google_workspace.config import GoogleOAuthConfig
from google_workspace.oauth import (
    ADDON_SCOPES,
    CAPABILITY_SCOPES,
    MEET_SCOPE,
    READ_SCOPES,
    WRITE_SCOPES,
    authorization_url,
    capabilities_for,
    merge_scopes,
    pkce_pair,
)

CONFIG = GoogleOAuthConfig(
    client_id="client-123.apps.googleusercontent.com",
    client_secret="GOCSPX-test-secret",
    redirect_uri="https://hoja.example.test/api/connectors/google/callback",
)


def _query(scopes: list[str]) -> dict[str, list[str]]:
    _verifier, challenge = pkce_pair()
    url = authorization_url(CONFIG, scopes=scopes, state="sealed-state", code_challenge=challenge)
    return parse_qs(urlparse(url).query)


def test_the_meet_addon_holds_exactly_one_scope() -> None:
    assert ADDON_SCOPES == {"meet": (MEET_SCOPE,)}
    assert MEET_SCOPE == "https://www.googleapis.com/auth/meetings.space.readonly"


def test_the_meet_scope_is_not_reachable_as_a_write_scope() -> None:
    assert MEET_SCOPE not in WRITE_SCOPES.values()
    assert MEET_SCOPE not in READ_SCOPES.values()


def test_an_upgrade_asks_google_for_the_meet_scope_only() -> None:
    """The shared OAuth client is why this matters: sending the Workspace scopes on a Meet upgrade
    would re-ask for Gmail and Drive and widen consent for no reason."""
    query = _query(list(ADDON_SCOPES["meet"]))
    assert query["scope"] == [MEET_SCOPE]
    for workspace_scope in READ_SCOPES.values():
        assert workspace_scope not in query["scope"][0]


def test_an_upgrade_is_incremental_and_asks_for_a_refresh_token() -> None:
    query = _query(list(ADDON_SCOPES["meet"]))
    # `include_granted_scopes` is what makes Google extend the existing grant instead of replacing
    # it; `access_type=offline` + `prompt=consent` is what returns a refresh token for storage.
    assert query["include_granted_scopes"] == ["true"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert "client_secret" not in query


def test_pkce_challenge_is_the_sha256_of_the_verifier() -> None:
    verifier, challenge = pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_the_workspace_connect_does_not_ask_for_meet() -> None:
    query = _query(list(READ_SCOPES.values()))
    assert MEET_SCOPE not in query["scope"][0]
    assert set(query["scope"][0].split()) == set(READ_SCOPES.values())


def test_merge_scopes_is_an_ordered_union_not_a_replacement() -> None:
    """After a Meet upgrade Google reports the whole grant; after a Workspace reconnect it reports
    the same whole. Recording either list verbatim is safe, so the union only has to be a union."""
    assert merge_scopes("a b", "b c") == ["a", "b", "c"]
    assert merge_scopes("", ["x", "y"]) == ["x", "y"]
    assert merge_scopes(["x", " y "], "y z") == ["x", "y", "z"]
    assert merge_scopes() == []


def test_capabilities_are_reported_per_api_so_an_unticked_scope_is_visible() -> None:
    """Google lets a user untick any permission on the consent screen, so "the account exists" is
    not the answer Settings needs."""
    both = capabilities_for(f"{READ_SCOPES['gmail']} {MEET_SCOPE}")
    assert both == {
        "gmail": True,
        "drive": False,
        "calendar": False,
        "docs": False,
        "meet": True,
    }
    assert capabilities_for("") == dict.fromkeys(CAPABILITY_SCOPES, False)
    assert capabilities_for(" ".join(READ_SCOPES.values()))["meet"] is False
