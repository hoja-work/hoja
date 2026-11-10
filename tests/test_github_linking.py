"""Tests for identifier detection in GitHub branches, pull requests and commits."""

from __future__ import annotations

import pytest

from hoja_server.github_linking import (
    MAX_SCAN_CHARS,
    LinkSource,
    find_identifiers,
    identifiers_in_branch,
    identifiers_in_commit,
    identifiers_in_pull_request,
)

KEYS = frozenset({"HOJA", "ENG"})


def ids(refs):
    return [r.identifier for r in refs]


# --- branch names -------------------------------------------------------------------------


def test_branch_generated_by_hoja_is_detected():
    # writer.issue_create builds "<uid8>/<identifier lowercased>-<slug>".
    assert ids(identifiers_in_branch("4c114d5b/hoja-123-fix-the-thing", KEYS)) == ["HOJA-123"]


def test_branch_is_normalised_to_uppercase():
    refs = identifiers_in_branch("b89ca6d/eng-7-add-labels", KEYS)
    assert ids(refs) == ["ENG-7"]
    assert refs[0].source is LinkSource.BRANCH


def test_branch_without_identifier_yields_nothing():
    assert ids(identifiers_in_branch("dependabot/npm_and_yarn/lodash-4.17.21", KEYS)) == []


# --- pull requests ------------------------------------------------------------------------


def test_pr_title_is_detected():
    refs = identifiers_in_pull_request("Fix drawer floor HOJA-110", None, None, KEYS)
    assert ids(refs) == ["HOJA-110"]
    assert refs[0].source is LinkSource.PR_TITLE


def test_pr_body_multiple_identifiers_keep_appearance_order():
    body = "Closes HOJA-9. Also touches ENG-2 and HOJA-9 again."
    assert ids(identifiers_in_pull_request(None, body, None, KEYS)) == ["HOJA-9", "ENG-2"]


def test_pr_head_branch_is_used_when_text_has_none():
    refs = identifiers_in_pull_request("Tidy up", "no reference here", "4c114d5b/hoja-42-x", KEYS)
    assert ids(refs) == ["HOJA-42"]
    assert refs[0].source is LinkSource.BRANCH


def test_pr_title_wins_ordering_over_branch_and_body_marks_closing():
    refs = identifiers_in_pull_request(
        "HOJA-5 work",
        "this fixes HOJA-5",
        "4c114d5b/hoja-5-x",
        KEYS,
    )
    assert ids(refs) == ["HOJA-5"]
    # The title mentioned it first, but the body closes it, and closing is the meaningful claim.
    assert refs[0].source is LinkSource.PR_TITLE
    assert refs[0].closing is True


# --- closing keywords ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword", ["close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"]
)
def test_each_closing_keyword_marks_the_reference(keyword):
    refs = identifiers_in_commit(f"{keyword} HOJA-1", KEYS)
    assert len(refs) == 1
    assert refs[0].closing is True


def test_closing_keyword_is_case_insensitive_and_allows_a_colon():
    assert identifiers_in_commit("FIXES: hoja-1", KEYS)[0].closing is True


def test_plain_mention_is_not_closing():
    refs = identifiers_in_commit("see HOJA-1 for background", KEYS)
    assert refs[0].closing is False


def test_keyword_without_identifier_is_not_a_reference():
    assert ids(identifiers_in_commit("this closes the loop entirely", KEYS)) == []


# --- negative cases that must not link ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ISO-9001",          # real-world shaped, but not one of our teams
        "HTTP-404",
        "RFC-2119",
    ],
)
def test_unknown_team_key_is_not_an_identifier(text):
    assert find_identifiers(text, KEYS, LinkSource.PR_BODY) == []


@pytest.mark.parametrize(
    "text",
    [
        "XHOJA-123",     # glued on the left
        "HOJA-1234x",    # glued on the right
        "HOJA_123",      # underscore is a word character
        "HOJA-",         # no number
        "HOJA-abc",      # digits required
        "HOJA123",       # missing hyphen
    ],
)
def test_malformed_identifiers_do_not_match(text):
    assert find_identifiers(text, KEYS, LinkSource.PR_BODY) == []


def test_empty_and_missing_input_yield_nothing():
    assert find_identifiers(None, KEYS, LinkSource.PR_BODY) == []
    assert find_identifiers("", KEYS, LinkSource.PR_BODY) == []
    assert identifiers_in_branch(None, KEYS) == []
    assert identifiers_in_commit("", KEYS) == []


# --- ambiguous and overlapping configuration ----------------------------------------------


def test_prefix_overlapping_team_keys_resolve_to_the_longer_key():
    # With both HO and HOJA known, "HOJA-5" must be HOJA-5, never HO-... .
    refs = find_identifiers("HOJA-5", frozenset({"HO", "HOJA"}), LinkSource.PR_BODY)
    assert ids(refs) == ["HOJA-5"]


def test_shorter_key_still_matches_its_own_identifier():
    refs = find_identifiers("HO-5", frozenset({"HO", "HOJA"}), LinkSource.PR_BODY)
    assert ids(refs) == ["HO-5"]


def test_team_keys_must_not_be_empty():
    with pytest.raises(ValueError):
        find_identifiers("HOJA-1", frozenset(), LinkSource.PR_BODY)


def test_team_keys_type_is_enforced():
    with pytest.raises(TypeError):
        find_identifiers("HOJA-1", {"HOJA"}, LinkSource.PR_BODY)  # set, not frozenset


# --- realistic text -----------------------------------------------------------------------


def test_trailing_punctuation_and_urls_still_match():
    text = "See https://hoja.clover4.app/hoja/issue/HOJA-287, then HOJA-288."
    assert ids(find_identifiers(text, KEYS, LinkSource.PR_BODY)) == ["HOJA-287", "HOJA-288"]


def test_merge_commit_subject_carries_the_source_branch_identifier():
    subject = "Merge pull request #61 from hoja-work/hoja-123-github-linking"
    assert ids(identifiers_in_commit(subject, KEYS)) == ["HOJA-123"]


def test_multiline_commit_message_scans_every_line():
    message = "chore: tidy\n\nHOJA-1 and HOJA-2 are affected.\nfixes HOJA-2\n"
    refs = identifiers_in_commit(message, KEYS)
    assert ids(refs) == ["HOJA-1", "HOJA-2"]
    assert {r.identifier: r.closing for r in refs} == {"HOJA-1": False, "HOJA-2": True}


def test_duplicate_identifier_is_returned_once():
    refs = find_identifiers("HOJA-1 HOJA-1 HOJA-1", KEYS, LinkSource.PR_BODY)
    assert ids(refs) == ["HOJA-1"]


def test_scan_is_bounded(monkeypatch):
    # A huge body must not be scanned in full; the parser reports what is inside the bound.
    huge = "x" * MAX_SCAN_CHARS + " HOJA-999"
    assert ids(find_identifiers(huge, KEYS, LinkSource.PR_BODY)) == []
    near = "x" * (MAX_SCAN_CHARS - 20) + " HOJA-999"
    assert ids(find_identifiers(near, KEYS, LinkSource.PR_BODY)) == ["HOJA-999"]
