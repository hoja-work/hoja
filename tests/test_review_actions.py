"""The merge gate and the refusals that protect the owner.

Every test here exists to prove one thing: a pull request that is not demonstrably safe to merge is
refused *before* the merge call is made, and an ambiguous answer from GitHub is never recorded as a
merge. The last test is the one that would have caught an optimistic UI: a 200 that does not say
`merged: true`.
"""

from __future__ import annotations

import pytest

from hoja_server.github_review_actions import (
    ACTION_MERGE,
    ReviewRefused,
    actor_label,
    gate_from,
    perform,
    refusal,
    split_repository,
)

LINK = {"url": "https://github.com/hoja-work/hoja-cloud/pull/12", "id": "ghl_1"}


def _pull(**overrides):
    payload = {
        "state": "open",
        "mergeable": True,
        "mergeable_state": "clean",
        "title": "Wire reviews writes",
        "html_url": LINK["url"],
        "head": {"sha": "abc123"},
    }
    payload.update(overrides)
    return payload


def _green():
    return [{"status": "completed", "conclusion": "success"}]


class _Writes:
    """Records what was sent and answers with a scripted GitHub."""

    def __init__(self, *, pull=None, checks=None, combined=None, merge_result=None):
        self._pull = pull if pull is not None else _pull()
        self._checks = checks if checks is not None else _green()
        self._combined = combined if combined is not None else {"state": "success"}
        self._merge_result = merge_result if merge_result is not None else {
            "merged": True, "sha": "def456"}
        self.merged_with: dict | None = None
        self.reviews: list[dict] = []
        self.comments: list[str] = []

    async def fetch_pull(self, repository, number):
        return self._pull

    async def fetch_check_runs(self, repository, sha):
        return self._checks

    async def fetch_combined_status(self, repository, sha):
        return self._combined

    async def merge_pull(self, repository, number, *, sha, method="squash"):
        self.merged_with = {"repository": repository, "number": number, "sha": sha,
                            "method": method}
        return self._merge_result

    async def create_review(self, repository, number, *, event, body=None):
        self.reviews.append({"event": event, "body": body})
        return {"id": 1, "state": "SUBMITTED"}

    async def create_comment(self, repository, number, body):
        self.comments.append(body)
        return {"id": 2}


def test_split_repository_reads_a_pull_url():
    assert split_repository(LINK["url"]) == ("hoja-work/hoja-cloud", 12)
    assert split_repository("https://github.com/o/r/issues/3") is None
    assert split_repository(None) is None


def test_failing_checks_refuse_the_merge():
    gate = gate_from(_pull(), [{"status": "completed", "conclusion": "failure"}], {})
    code, message = refusal(gate)
    assert code == "checks_failing"
    assert "failing" in message


def test_unknown_mergeability_refuses_the_merge():
    gate = gate_from(_pull(mergeable=None), _green(), {"state": "success"})
    code, _ = refusal(gate)
    assert code == "mergeability_unknown", "a null mergeable must never read as 'fine'"


def test_dirty_branch_refuses_the_merge():
    gate = gate_from(_pull(mergeable=False, mergeable_state="dirty"), _green(),
                     {"state": "success"})
    code, _ = refusal(gate)
    assert code == "branch_dirty"


def test_pending_checks_refuse_but_absent_checks_do_not():
    """Pending means "wait"; absent means "this repo has no platform CI".

    The second case is the normal one here: GitHub Actions is abandoned, so a commit with no
    check runs must still be mergeable, with the absence shown honestly in the gate line.
    """
    pending = gate_from(_pull(), [{"status": "queued", "conclusion": None}], {})
    assert refusal(pending)[0] == "checks_pending"
    absent = gate_from(_pull(), [], {})
    assert absent.checks["state"] == "none"
    assert refusal(absent) is None, "no checks is not a failing gate"


def test_unreadable_checks_refuse_the_merge_instead_of_becoming_no_checks():
    """A 403 on the check read is not "this repo runs no CI".

    `none` is deliberately mergeable here because the project runs no platform CI. If GitHub
    simply refuses to show the checks (the app lacks Checks / Commit statuses), the same empty
    list would wrongly read as "no checks", so the gate must carry that fact and refuse.
    """
    gate = gate_from(_pull(), [], {}, checks_unavailable=True)
    code, message = refusal(gate)
    assert code == "checks_unknown"
    assert "Checks" in message and "Commit statuses" in message
    assert gate.as_dict()["checksUnavailable"] is True


def test_a_moved_head_refuses_the_merge():
    gate = gate_from(_pull(head={"sha": "new999"}), _green(), {"state": "success"})
    assert refusal(gate, expected_sha="old111")[0] == "head_moved"


def test_a_green_clean_pull_request_is_mergeable():
    gate = gate_from(_pull(), _green(), {"state": "success"})
    assert refusal(gate, expected_sha="abc123") is None


async def test_merge_sends_the_gate_sha_and_never_a_stale_one():
    writes = _Writes()
    outcome = await perform(writes, link=LINK, action=ACTION_MERGE, user_name="Luis",
                            sha="abc123")
    assert writes.merged_with["sha"] == "abc123"
    assert writes.merged_with["method"] == "squash"
    assert outcome["mergeSha"] == "def456"
    assert outcome["actor"] == "Hoja, on behalf of Luis"


async def test_failing_checks_never_reach_the_merge_endpoint():
    writes = _Writes(checks=[{"status": "completed", "conclusion": "failure"}])
    with pytest.raises(ReviewRefused) as caught:
        await perform(writes, link=LINK, action=ACTION_MERGE, user_name="Luis", sha="abc123")
    assert caught.value.code == "checks_failing"
    assert writes.merged_with is None, "a refused merge must not call GitHub's merge endpoint"
    assert caught.value.gate is not None, "the refusal carries the gate for the UI to show"


async def test_ambiguous_200_is_not_recorded_as_merged():
    writes = _Writes(merge_result={"merged": False, "message": "Mergeability is unknown"})
    with pytest.raises(ReviewRefused) as caught:
        await perform(writes, link=LINK, action=ACTION_MERGE, user_name="Luis", sha="abc123")
    assert caught.value.code == "mergeability_unknown"
    assert "did not confirm" in caught.value.message


async def test_approve_reports_the_attributed_actor():
    writes = _Writes()
    outcome = await perform(writes, link=LINK, action="approve", user_name="Luis", body=None)
    assert writes.reviews == [{"event": "APPROVE", "body": None}]
    assert outcome["eventType"] == "githubReviewApproved"
    assert outcome["actor"] == actor_label("Luis")


async def test_comment_goes_to_the_comment_endpoint_not_a_review():
    writes = _Writes()
    await perform(writes, link=LINK, action="comment", user_name="Luis", body="ok")
    assert writes.comments == ["ok"]
    assert writes.reviews == []
