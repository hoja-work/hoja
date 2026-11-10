"""Detect Hoja issue identifiers in GitHub text and decide whether they close the issue.

A GitHub branch, pull request or commit refers to Hoja work by writing an identifier such as
``HOJA-123``. Hoja already puts one in every branch it generates
(``<uid>/<identifier>-<slug>``, see ``writer.issue_create``), so the branch name is the
highest-signal place to look; a pull request title or body and a commit message are the others.

Two properties are deliberately strict:

* **A team key must be known.** ``ISO-9001`` and ``HTTP-404`` have the same shape as a Hoja
  identifier, and guessing would link work to a team that never mentioned it. Callers pass the
  team keys they can resolve, and anything outside that set is not an identifier here.
* **Identifiers must stand alone.** ``XHOJA-123``, ``HOJA-1234x`` and ``HOJA-`` are not
  identifiers: the match may not begin or end inside a longer word. This keeps a hash or a
  version string from being read as a reference.

Closing keywords follow GitHub's own vocabulary (``close``/``fix``/``resolve`` and their
inflections) either immediately before the identifier or with a colon: ``fixes HOJA-123``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Bound the text we scan so a pathological blob cannot make matching expensive.
MAX_SCAN_CHARS = 64 * 1024

# GitHub's documented closing vocabulary, as it applies to an identifier rather than "#123".
_CLOSING_KEYWORDS = (
    "close",
    "closes",
    "closed",
    "fix",
    "fixes",
    "fixed",
    "resolve",
    "resolves",
    "resolved",
)

# Characters that may not touch an identifier on either side. A digit or letter means we are
# inside a longer token; an underscore is a word character too, so ``HOJA_123`` is not a match.
_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9_])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9_])"


class LinkSource(str, Enum):
    """Where an identifier was found."""

    BRANCH = "branch"
    PR_TITLE = "pr_title"
    PR_BODY = "pr_body"
    COMMIT_MESSAGE = "commit_message"


@dataclass(frozen=True)
class LinkReference:
    """One identifier occurrence.

    ``closing`` is True only when a closing keyword introduces it, which is what a consumer
    needs in order to decide whether a merged PR may move an issue to Done.
    """

    identifier: str
    source: LinkSource
    closing: bool


def _team_alternation(team_keys: frozenset[str]) -> str:
    """Alternation of the known keys, longest first so ``HOJA`` wins over ``HO``."""
    ordered = sorted({key.upper() for key in team_keys if key}, key=len, reverse=True)
    if not ordered:
        raise ValueError("team_keys must contain at least one non-empty key")
    return "|".join(re.escape(key) for key in ordered)


def _identifier_pattern(team_keys: frozenset[str]) -> re.Pattern[str]:
    alternation = _team_alternation(team_keys)
    # The key is followed by a hyphen and at least one digit. Digits only: a trailing letter
    # belongs to some other token, and the boundary guard rejects it anyway, but limiting the
    # class keeps the intent obvious.
    return re.compile(
        rf"{_BOUNDARY_BEFORE}(?P<key>{alternation})-(?P<number>\d+){_BOUNDARY_AFTER}",
        re.IGNORECASE,
    )


def _closing_pattern(team_keys: frozenset[str]) -> re.Pattern[str]:
    alternation = _team_alternation(team_keys)
    keywords = "|".join(_CLOSING_KEYWORDS)
    # "fixes HOJA-1", "Fixes: HOJA-1", and "fixes\nHOJA-1" are all GitHub closing forms; the
    # colon is optional and whitespace may be any run, but the keyword must be its own word.
    return re.compile(
        rf"{_BOUNDARY_BEFORE}(?P<keyword>{keywords})\b\s*:?\s*"
        rf"(?P<key>{alternation})-(?P<number>\d+){_BOUNDARY_AFTER}",
        re.IGNORECASE,
    )


def find_identifiers(
    text: str | None,
    team_keys: frozenset[str],
    source: LinkSource,
) -> list[LinkReference]:
    """Return every distinct identifier in ``text``, in first-appearance order.

    A repeated identifier is returned once. If any occurrence is a closing reference, the
    single returned entry for that identifier is closing, because the closing claim is the
    meaningful one to act on.
    """
    if not text:
        return []
    if not isinstance(team_keys, frozenset):
        raise TypeError("team_keys must be a frozenset[str]")
    bounded = text[:MAX_SCAN_CHARS]

    closing: set[str] = set()
    closing_pattern = _closing_pattern(team_keys)
    for match in closing_pattern.finditer(bounded):
        closing.add(f"{match.group('key')}-{match.group('number')}".upper())

    ordered: dict[str, LinkReference] = {}
    for match in _identifier_pattern(team_keys).finditer(bounded):
        identifier = f"{match.group('key')}-{match.group('number')}".upper()
        if identifier in ordered:
            continue
        ordered[identifier] = LinkReference(
            identifier=identifier,
            source=source,
            closing=identifier in closing,
        )
    return list(ordered.values())


def identifiers_in_branch(branch: str | None, team_keys: frozenset[str]) -> list[LinkReference]:
    """Identifiers in a branch name such as ``4c114d5b/hoja-123-fix-the-thing``.

    Branch names are lowercased when Hoja generates them, so the match is case-insensitive and
    the result is normalised to upper case.
    """
    return find_identifiers(branch, team_keys, LinkSource.BRANCH)


def identifiers_in_commit(message: str | None, team_keys: frozenset[str]) -> list[LinkReference]:
    """Identifiers in a commit message, closing keywords included.

    A merge commit's subject carries the source branch (``Merge pull request #61 from
    hoja-work/hoja-123-x``), so this also catches work that reached the default branch through a
    merge whose own message named the branch.
    """
    return find_identifiers(message, team_keys, LinkSource.COMMIT_MESSAGE)


def identifiers_in_pull_request(
    title: str | None,
    body: str | None,
    head_branch: str | None,
    team_keys: frozenset[str],
) -> list[LinkReference]:
    """Identifiers from a pull request: title, then body, then head branch.

    The head branch is included because a PR opened from a Hoja-generated branch names the issue
    even when the author never writes the identifier in the text. Earlier sources win the
    ordering, and a closing keyword anywhere marks the identifier as closing.
    """
    collected: dict[str, LinkReference] = {}
    for text, source in (
        (title, LinkSource.PR_TITLE),
        (body, LinkSource.PR_BODY),
        (head_branch, LinkSource.BRANCH),
    ):
        for reference in find_identifiers(text, team_keys, source):
            existing = collected.get(reference.identifier)
            if existing is None:
                collected[reference.identifier] = reference
            elif reference.closing and not existing.closing:
                collected[reference.identifier] = LinkReference(
                    identifier=existing.identifier,
                    source=existing.source,
                    closing=True,
                )
    return list(collected.values())
