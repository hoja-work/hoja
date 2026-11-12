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


def jobs() -> dict[str, str]:
    """Job name -> its YAML body, bounded at the next job header."""
    text = WORKFLOW.read_text(encoding="utf-8")
    parts = re.split(r"\n  ([A-Za-z0-9_-]+):\n", text)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def pytest_job_body() -> str:
    """The ``pytest`` job only, bounded at the next job header."""
    found = jobs()
    assert "pytest" in found, "tests.yml has no pytest job"
    return found["pytest"]


def folded_runs(body: str) -> list[str]:
    """Every ``run: >-`` block in a job body, folded onto one line each."""
    runs: list[str] = []
    current: list[str] | None = None
    key_indent = 0
    for line in body.splitlines():
        header = re.match(r"^(\s*)run: >-\s*$", line)
        if header:
            current, key_indent = [], len(header.group(1))
            continue
        if current is None or not line.strip():
            continue
        if len(line) - len(line.lstrip()) > key_indent:
            current.append(line.strip())
        else:
            runs.append(" ".join(current))
            current = None
    if current is not None:
        runs.append(" ".join(current))
    return runs


def suite_command() -> str:
    """The invocation in the ``pytest`` job that actually runs the suite."""
    matches = [r for r in folded_runs(pytest_job_body()) if re.search(r"\bpytest\b", r)]
    assert len(matches) == 1, (
        f"the pytest job has {len(matches)} commands that run pytest, so the "
        "floor below cannot be said to describe any particular one of them"
    )
    return matches[0]


def ignores(command: str) -> frozenset[str]:
    return frozenset(re.findall(r"--ignore=(\S+)", command))


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


def test_the_floor_describes_the_suite_that_runs() -> None:
    """The recorded measurement and the job must cover the same tests.

    Composing two drafts is how this drifts in practice: one adds the floor and
    records the command it measured, another teaches the job to exclude a
    directory. The numbers still pass every arithmetic check while describing a
    suite that no longer exists, which is the same class of defect as a floor
    that was never measured at all.
    """
    record = baseline()
    measured = ignores(record["command"])
    running = ignores(suite_command())
    assert measured == running, (
        f"scripts/ci_suite_baseline.json measured "
        f"{sorted(measured) or 'no exclusions'} but the pytest job now runs "
        f"with {sorted(running) or 'no exclusions'}; the floor no longer "
        "describes the suite it guards, so re-measure and record the new domain"
    )


def test_an_ignored_suite_is_run_by_another_job() -> None:
    """Excluding tests is only safe when something else runs them."""
    for path in sorted(ignores(suite_command())):
        owners = [
            name
            for name, body in jobs().items()
            if name != "pytest" and path in body and re.search(r"\bpytest\b", body)
        ]
        assert owners, (
            f"the pytest job ignores {path} and no other job runs it, so those "
            "cases execute nowhere while every check stays green"
        )


def test_the_gate_reads_the_report_the_suite_writes() -> None:
    """A renamed report makes the gate read a stale file or nothing at all."""
    written = re.search(r"--junit-xml=(\S+)", suite_command())
    read = re.search(r"assert_pg_gate_ran\.py (\S+)", pytest_job_body())
    assert written and read, (
        "the pytest job no longer both writes and reads a JUnit report, so the "
        "executed-case count cannot be checked at all"
    )
    assert written.group(1) == read.group(1), (
        f"the suite writes {written.group(1)} but the gate reads "
        f"{read.group(1)}; the gate would report on a file this job did not write"
    )
