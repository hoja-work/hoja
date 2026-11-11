"""The executed-test floor in CI must be a measured number, not a guess.

``pytest`` exits 0 for a run made entirely of skips, so the ``pytest`` job asks
``scripts/assert_pg_gate_ran.py`` how many cases actually executed. That floor
is the only thing standing between "1150 tests passed" and "1150 tests were
skipped", which makes it exactly the kind of number that rots: it can drift below
the real suite (the gate stops protecting anything) or above it (main goes red
for no reason).

``scripts/ci_suite_baseline.json`` records what the suite actually did when the
floor was set. These cases keep the record, the workflow argument and the
measurement in the same place. They cannot detect a suite that shrank since that
commit — nothing offline can — but they do stop the three from disagreeing
silently, and the JSON says which commit to re-measure at.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
BASELINE = REPO / "scripts" / "ci_suite_baseline.json"

# The floor may sit below the measurement (ordinary churn deletes tests) but not
# so far below it that the gate is decorative.
MIN_HEADROOM = 0.90


def baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def pytest_job_body() -> str:
    """The ``pytest`` job only, bounded at the next job header."""
    text = WORKFLOW.read_text(encoding="utf-8")
    parts = re.split(r"\n  ([A-Za-z0-9_-]+):\n", text)
    jobs = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    assert "pytest" in jobs, "tests.yml has no pytest job"
    return jobs["pytest"]


def enforced_floor() -> int:
    match = re.search(r"--minimum (\d+)", pytest_job_body())
    assert match, (
        "the pytest job does not pass --minimum, so a suite that skipped every "
        "case would still report green"
    )
    return int(match.group(1))


def test_baseline_is_internally_consistent() -> None:
    record = baseline()
    assert record["collected"] - record["skipped"] == record["executed"], (
        "the recorded baseline contradicts itself: collected minus skipped is "
        f"{record['collected'] - record['skipped']}, recorded executed is "
        f"{record['executed']}"
    )
    assert record["executed"] > 0
    assert re.fullmatch(r"[0-9a-f]{7,40}", record["commit"]), (
        "the baseline must name the commit it was measured at, otherwise nobody "
        "can tell whether it is stale"
    )


def test_workflow_floor_matches_the_recorded_baseline() -> None:
    record = baseline()
    assert enforced_floor() == record["floor"], (
        f"tests.yml enforces --minimum {enforced_floor()} but "
        f"scripts/ci_suite_baseline.json records {record['floor']}; one of them "
        "was edited without the other"
    )


def test_floor_cannot_exceed_what_was_measured() -> None:
    record = baseline()
    assert enforced_floor() <= record["executed"], (
        f"the floor ({enforced_floor()}) is above the {record['executed']} cases "
        f"measured at {record['commit']}, so the job would be red on arrival"
    )


def test_floor_keeps_bounded_headroom() -> None:
    record = baseline()
    ratio = enforced_floor() / record["executed"]
    assert ratio >= MIN_HEADROOM, (
        f"the floor is {ratio:.0%} of the measured {record['executed']} cases; "
        f"below {MIN_HEADROOM:.0%} it no longer detects a suite that mostly "
        "stopped running"
    )
