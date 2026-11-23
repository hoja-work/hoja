"""The gate that proves a test job ran must itself be provable.

``scripts/assert_pg_gate_ran.py`` is what stops a job from reporting green
without executing the tests it exists for. Until this module it had no test of
its own: the only reference to it in the repository was the workflow line that
invokes it. A gate nobody has seen fail is not evidence, so every case below
builds a JUnit report that must fail and checks that it does.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assert_pg_gate_ran.py"


def _load():
    spec = importlib.util.spec_from_file_location("assert_pg_gate_ran", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load()


def _report(path: Path, total: int, skipped: int = 0, failed: int = 0) -> Path:
    """Write a JUnit report with the requested mix of outcomes."""
    cases = []
    for index in range(total):
        body = ""
        if index < skipped:
            body = '<skipped message="gated"/>'
        elif index < skipped + failed:
            body = '<failure message="boom"/>'
        cases.append(
            f'<testcase classname="tests.test_x" name="case_{index}">{body}</testcase>'
        )
    path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_healthy_database_report_passes(tmp_path):
    report = _report(tmp_path / "pg.xml", total=15)
    assert gate.main(["gate", str(report)]) == 0


def test_empty_report_fails(tmp_path):
    report = _report(tmp_path / "empty.xml", total=0)
    assert gate.main(["gate", str(report)]) == 1


def test_all_skipped_report_fails(tmp_path):
    """pytest exits 0 for this run; the gate must not."""
    report = _report(tmp_path / "skipped.xml", total=15, skipped=15)
    assert gate.main(["gate", str(report)]) == 1


def test_single_skip_fails_by_default(tmp_path):
    """The postgres job's contract: one skipped case proves nothing."""
    report = _report(tmp_path / "one.xml", total=15, skipped=1)
    assert gate.main(["gate", str(report)]) == 1


def test_short_report_fails(tmp_path):
    report = _report(tmp_path / "short.xml", total=14)
    assert gate.main(["gate", str(report)]) == 1


def test_missing_report_fails(tmp_path):
    assert gate.main(["gate", str(tmp_path / "absent.xml")]) == 1


def test_allow_skips_tolerates_partial_skips(tmp_path):
    """The broad suite legitimately skips the Postgres-gated cases."""
    report = _report(tmp_path / "partial.xml", total=1200, skipped=40)
    assert gate.main(["gate", str(report), "--allow-skips", "--minimum", "1150"]) == 0


def test_allow_skips_still_fails_when_nothing_executed(tmp_path):
    """--allow-skips must not become a way to bless a suite that ran nothing."""
    report = _report(tmp_path / "all_skipped.xml", total=1200, skipped=1200)
    assert gate.main(["gate", str(report), "--allow-skips", "--minimum", "1150"]) == 1


def test_failures_still_count_as_executed(tmp_path):
    """A failing case executed: that is a verdict, not an absence.

    The gate detects absence of execution; pytest already reports the failure,
    so a floor breach must not be reported on top of it.
    """
    report = _report(tmp_path / "failed.xml", total=1200, skipped=0, failed=5)
    assert gate.main(["gate", str(report), "--allow-skips", "--minimum", "1150"]) == 0


def _job_blocks(workflow: str) -> dict[str, str]:
    """Split a workflow into {job_name: body}, bounded at the next job.

    A plain ``split`` on one job's header leaks the following job into the same
    slice, which makes the wiring assertions below pass for a job that has no
    step at all. The regex keeps each body to its own job.
    """
    parts = re.split(r"\n  ([A-Za-z0-9_-]+):\n", workflow)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


@pytest.mark.parametrize("job", ["pytest", "pytest-postgres"])
def test_workflow_wires_the_gate_for_every_job(job: str):
    """A gate that nothing calls is documentation, not a gate."""
    workflow = (SCRIPT.parents[1] / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    blocks = _job_blocks(workflow)
    assert job in blocks, f"{job} job not found in tests.yml"
    body = blocks[job]
    assert "assert_pg_gate_ran.py" in body, f"{job} does not assert it executed"
    assert "--junit-xml=" in body, f"{job} produces no JUnit report to assert on"
