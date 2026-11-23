"""Reviews write actions: approve, request changes, comment, squash-merge.

The owner approved Hoja acting on GitHub on his behalf from Reviews (2026-11-12), with rollback as
the safety net. Four constraints came with that approval and each one is visible here:

1. **One credential.** Every call goes through the account's existing `TokenSource`
   (`hoja_server/github_tokens.py`); nothing in this module reads an env var or mints a token.
2. **A merge names its commit and its gate.** `perform` refuses unless the caller passes the sha it
   showed the user *and* that sha is still the head, and it refuses on failing checks, pending
   checks, an unknown mergeability or a dirty branch. The gate is read live, at the moment of the
   merge — the stored `GithubLink` row is a cache and is never the authority.
3. **Attribution.** Every successful action reports `changes.actor` as "Hoja, on behalf of <user>",
   so a bad merge is traceable to the person who clicked.
4. **No optimistic success.** The caller records "merged" only after GitHub answers
   `merged: true`; every other outcome raises a `ReviewRefused` carrying a code, and the row is
   left exactly as it was.

The decisions (`gate_from`, `refusal`) are pure so the refusal paths are tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from github.writes import APPROVE, COMMENT, REQUEST_CHANGES, summarize_checks

ACTION_APPROVE = "approve"
ACTION_REQUEST_CHANGES = "request_changes"
ACTION_COMMENT = "comment"
ACTION_MERGE = "merge"

EVENT_TYPES = {
    ACTION_APPROVE: "githubReviewApproved",
    ACTION_REQUEST_CHANGES: "githubReviewChangesRequested",
    ACTION_COMMENT: "githubReviewCommented",
    ACTION_MERGE: "githubReviewMerged",
}

# Refusal codes the UI can branch on. They are stable strings, not prose.
CHECK_FAILING = "checks_failing"
CHECK_PENDING = "checks_pending"
CHECK_UNKNOWN = "checks_unknown"
MERGEABILITY_UNKNOWN = "mergeability_unknown"
BRANCH_DIRTY = "branch_dirty"
BRANCH_BLOCKED = "branch_blocked"
HEAD_MOVED = "head_moved"
PULL_CLOSED = "pull_closed"
PULL_MERGED = "already_merged"
NOT_A_PULL = "not_a_pull_request"


@dataclass(frozen=True)
class ReviewGate:
    """The gate as GitHub reported it at one instant."""

    sha: str
    mergeable: bool | None
    merge_state: str | None
    checks: dict[str, Any]
    state: str
    title: str
    url: str | None
    # True when GitHub refused the check read itself (the app lacks Checks / Commit statuses).
    # That is not "no checks": an unreadable gate can never prove a merge is safe, so refusal
    # must treat it as undecided rather than as the benign empty case.
    checks_unavailable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "mergeable": self.mergeable,
            "mergeState": self.merge_state,
            "checksState": self.checks.get("state"),
            "checksTotal": self.checks.get("total"),
            "checksPassing": self.checks.get("passing"),
            "checksFailing": self.checks.get("failing"),
            "checksPending": self.checks.get("pending"),
            "checksUnavailable": self.checks_unavailable,
            "state": self.state,
            "title": self.title,
            "url": self.url,
        }


class ReviewRefused(RuntimeError):
    """A write was refused before or by GitHub. `code` is what the UI branches on."""

    def __init__(self, code: str, message: str, *, gate: ReviewGate | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate


def split_repository(url: str | None) -> tuple[str, int] | None:
    """`https://github.com/o/r/pull/12` -> ("o/r", 12). None when it is not a pull request URL."""
    if not url:
        return None
    marker = "github.com/"
    index = url.find(marker)
    if index < 0:
        return None
    parts = url[index + len(marker):].split("/")
    if len(parts) < 4 or parts[2] != "pull":
        return None
    owner, repo, number = parts[0], parts[1], parts[3].split("?")[0]
    if not owner or not repo or not number.isdigit():
        return None
    return f"{owner}/{repo}", int(number)


def gate_from(pull: dict[str, Any], check_runs: list[dict[str, Any]],
              combined: dict[str, Any] | None = None, *,
              checks_unavailable: bool = False) -> ReviewGate:
    """Build the gate from GitHub's own fields, without inventing a value GitHub left null."""
    head = pull.get("head") or {}
    mergeable = pull.get("mergeable")
    return ReviewGate(
        sha=str(head.get("sha") or ""),
        mergeable=mergeable if isinstance(mergeable, bool) else None,
        merge_state=(str(pull.get("mergeable_state")) if pull.get("mergeable_state") else None),
        checks=summarize_checks(check_runs, combined),
        state=str(pull.get("state") or ""),
        title=str(pull.get("title") or ""),
        url=pull.get("html_url") if isinstance(pull.get("html_url"), str) else None,
        checks_unavailable=checks_unavailable,
    )


def refusal(gate: ReviewGate, *, expected_sha: str | None = None) -> tuple[str, str] | None:
    """Why this pull request may not be merged right now, or None when it may.

    Order matters: a closed pull request is refused before its checks are judged, and an unknown
    mergeability is refused before a dirty branch, because GitHub signals "still computing" as
    `mergeable: null` and a null must never be read as "fine".
    """
    if gate.state and gate.state.lower() == "merged":
        return PULL_MERGED, "This pull request is already merged."
    if gate.state and gate.state.lower() != "open":
        return PULL_CLOSED, f"This pull request is {gate.state}, not open."
    if expected_sha and gate.sha and expected_sha != gate.sha:
        return (
            HEAD_MOVED,
            f"The pull request head moved from {expected_sha[:7]} to {gate.sha[:7]} "
            "since this review was read; nothing was merged.",
        )
    if gate.checks_unavailable:
        return (
            CHECK_UNKNOWN,
            "GitHub will not let Hoja read this pull request's checks: the Hoja Connector app "
            "has no Checks or Commit statuses permission. Merging stays blocked until the app "
            "is granted Checks: Read-only and Commit statuses: Read-only.",
        )
    checks_state = str(gate.checks.get("state") or "none")
    if checks_state == "failure":
        return (
            CHECK_FAILING,
            f"{gate.checks.get('failing')} check(s) are failing; fix them before merging.",
        )
    if checks_state == "pending":
        return CHECK_PENDING, "Checks are still running; the gate is not decided yet."
    # `none` deliberately does not refuse: this project runs no platform CI, so a commit with
    # no check runs is the normal case, not an unproven one. It is still surfaced as
    # "no checks recorded" in the gate line, so the owner sees what they are merging.
    if gate.mergeable is None:
        return (
            MERGEABILITY_UNKNOWN,
            "GitHub has not computed mergeability yet (mergeable is null); try again shortly.",
        )
    merge_state = (gate.merge_state or "").lower()
    if merge_state == "dirty":
        return BRANCH_DIRTY, "The branch has conflicts with its base; resolve them first."
    if merge_state == "blocked":
        return BRANCH_BLOCKED, "GitHub reports the merge is blocked by a required rule."
    if gate.mergeable is False:
        return BRANCH_DIRTY, "GitHub reports this pull request cannot be merged as it stands."
    return None


def actor_label(user_name: str) -> str:
    """The exact attribution string the activity feed stores."""
    return f"Hoja, on behalf of {user_name}".strip()


async def perform(
    writes: Any,
    *,
    link: dict[str, Any],
    action: str,
    user_name: str,
    body: str | None = None,
    sha: str | None = None,
    method: str = "squash",
) -> dict[str, Any]:
    """Run one review action and report what to record.

    Never returns a success for an action GitHub did not confirm; a `ConnectorAPIError` propagates
    untouched so the resolver can attach GitHub's hint to the GraphQL error.
    """
    target = split_repository(link.get("url"))
    if target is None:
        raise ReviewRefused(NOT_A_PULL, "This link is not a pull request.")
    repository, number = target

    if action == ACTION_MERGE:
        pull = await writes.fetch_pull(repository, number)
        head = (pull.get("head") or {}).get("sha") or ""
        gate = gate_from(
            pull,
            await writes.fetch_check_runs(repository, head) if head else [],
            await writes.fetch_combined_status(repository, head) if head else {},
        )
        blocked = refusal(gate, expected_sha=sha)
        if blocked is not None:
            code, message = blocked
            raise ReviewRefused(code, message, gate=gate)
        result = await writes.merge_pull(repository, number, sha=gate.sha, method=method)
        if not isinstance(result, dict) or result.get("merged") is not True:
            # 200 without merged:true is ambiguous; treat it as a failure, never as success.
            reason = result.get("message") if isinstance(result, dict) else None
            raise ReviewRefused(
                MERGEABILITY_UNKNOWN,
                f"GitHub did not confirm the merge ({reason or 'no body'}); nothing is recorded "
                "as merged.",
                gate=gate,
            )
        return {
            "action": action,
            "sha": gate.sha,
            "mergeSha": str(result.get("sha") or ""),
            "gate": gate,
            "eventType": EVENT_TYPES[action],
            "actor": actor_label(user_name),
        }

    event = {
        ACTION_APPROVE: APPROVE,
        ACTION_REQUEST_CHANGES: REQUEST_CHANGES,
        ACTION_COMMENT: COMMENT,
    }[action]
    if event == COMMENT:
        payload = await writes.create_comment(repository, number, body or "")
    else:
        payload = await writes.create_review(repository, number, event=event, body=body)

    review_state = str((payload or {}).get("state") or "") if isinstance(payload, dict) else ""
    if event != COMMENT and review_state and review_state.upper() != "SUBMITTED":
        raise ReviewRefused(
            MERGEABILITY_UNKNOWN,
            f"GitHub recorded the review as {review_state}, not as submitted.",
        )
    return {
        "action": action,
        "sha": sha or "",
        "gate": None,
        "eventType": EVENT_TYPES[action],
        "actor": actor_label(user_name),
        "reviewId": str((payload or {}).get("id") or "") if isinstance(payload, dict) else "",
    }


__all__ = [
    "ACTION_APPROVE",
    "ACTION_COMMENT",
    "ACTION_MERGE",
    "ACTION_REQUEST_CHANGES",
    "EVENT_TYPES",
    "ReviewGate",
    "ReviewRefused",
    "actor_label",
    "gate_from",
    "perform",
    "refusal",
    "split_repository",
]
