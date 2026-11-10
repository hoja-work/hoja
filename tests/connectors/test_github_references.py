"""Author-written GitHub references: URL classification.

The webhook path can only see work GitHub already did. A person pasting a link
into a task is the other direction, and this is the only place that decides what
such a URL becomes in the link table.
"""

import pytest

from hoja_server.github_linking import parse_github_url


@pytest.mark.parametrize(
    "url,kind,external_id",
    [
        ("https://github.com/hoja-work/hoja/issues/42", "issue", "hoja-work/hoja#42"),
        ("https://github.com/hoja-work/hoja/pull/42", "pr", "hoja-work/hoja#42"),
        ("https://github.com/hoja-work/hoja/issues/42/", "issue", "hoja-work/hoja#42"),
        ("http://www.github.com/o/r/issues/7", "issue", "o/r#7"),
    ],
)
def test_issue_and_pull_urls_classify(url, kind, external_id):
    ref = parse_github_url(url)
    assert ref is not None
    assert (ref.kind, ref.external_id) == (kind, external_id)
    assert ref.url == url


def test_commit_url_keeps_the_full_sha_as_its_key():
    sha = "0123456789abcdef0123456789abcdef01234567"
    ref = parse_github_url(f"https://github.com/hoja-work/hoja/commit/{sha}")
    assert ref is not None
    assert ref.kind == "commit"
    assert ref.external_id == f"hoja-work/hoja@{sha}"
    # short display form, full key: a short sha is not a stable identity
    assert sha[:7] in ref.title


def test_release_url():
    ref = parse_github_url("https://github.com/hoja-work/hoja/releases/tag/v0.21.5")
    assert ref is not None
    assert ref.kind == "release"
    assert ref.external_id == "hoja-work/hoja@v0.21.5"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://github.com/hoja-work/hoja",          # repository, no object identity
        "https://github.com/hoja-work/hoja/blob/main/README.md",
        "https://gitlab.com/hoja-work/hoja/issues/1",  # not GitHub
        "https://example.com/hoja-work/hoja/issues/1",
        "not a url at all",
    ],
)
def test_non_link_urls_are_rejected(url):
    assert parse_github_url(url) is None


def test_same_issue_and_pull_share_a_number_but_not_a_kind():
    """kind is part of the unique key, so #42 as an issue and as a PR are distinct rows."""
    issue = parse_github_url("https://github.com/o/r/issues/42")
    pull = parse_github_url("https://github.com/o/r/pull/42")
    assert issue is not None and pull is not None
    assert issue.external_id == pull.external_id
    assert issue.kind != pull.kind
