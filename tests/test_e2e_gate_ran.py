"""The e2e gate must only pass when the *browser* suite really ran.

``scripts/assert_e2e_gate_ran.py`` is the guard that stops a green check from
standing over zero executed browser cases (HOJA-375, the same defect HOJA-303
fixed for the database job). A guard is only worth its green check if it fails
for every way the suite can fail to run, so each of those ways has a case here.
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assert_e2e_gate_ran.py"


def _report(path: Path, *, cases: int, classname: str, skipped: bool = False) -> Path:
    suite = ET.Element("testsuite", name="pytest", tests=str(cases))
    for i in range(cases):
        case = ET.SubElement(
            suite, "testcase", classname=f"{classname}.test_mod", name=f"test_case_{i}"
        )
        if skipped:
            ET.SubElement(case, "skipped", message="Chromium unavailable")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _run(report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_gate_passes_when_every_browser_case_executed(tmp_path: Path) -> None:
    result = _run(_report(tmp_path / "e2e.xml", cases=16, classname="tests.e2e.test_view_freshness"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GATE OK" in result.stdout


def test_gate_fails_when_browser_cases_skipped(tmp_path: Path) -> None:
    result = _run(
        _report(tmp_path / "e2e.xml", cases=16, classname="tests.e2e.test_view_freshness", skipped=True)
    )
    assert result.returncode == 1, result.stdout
    assert "GATE FAILED" in result.stdout


def test_gate_fails_on_a_unit_only_report(tmp_path: Path) -> None:
    """A report full of passing *unit* cases says nothing about the browser suite.

    Counting bare ``<testcase>`` nodes made this a false green: 16 passing unit
    tests satisfied the minimum and carried no skip marker, so the gate printed
    "every browser test executed" over zero browser cases.
    """
    result = _run(_report(tmp_path / "unit.xml", cases=16, classname="tests.test_units"))
    assert result.returncode == 1, result.stdout
    assert "GATE FAILED" in result.stdout


def test_gate_fails_when_the_report_is_empty(tmp_path: Path) -> None:
    suite = ET.Element("testsuite", name="pytest", tests="0")
    path = tmp_path / "empty.xml"
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    result = _run(path)
    assert result.returncode == 1, result.stdout
    assert "GATE FAILED" in result.stdout
