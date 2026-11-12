"""Fail when the database-backed tests skip instead of running.

The job that runs them exists because the suite's row-level-security and
upgrade-path tests are gated on ``HOJA_TEST_POSTGRES_URL``. A missing URL makes
pytest report them as *skipped*, and a skipped test looks exactly like a passing
one in a green check. This script reads the JUnit report and exits non-zero if
any expected case skipped, so "the gate ran" is a fact rather than an inference
from the colour of a badge.

Usage: assert_pg_gate_ran.py <junit.xml> [--minimum N]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

# 37 cases live in the database-gated modules: 5 in test_server_postgres.py,
# 7 in test_tenant_context.py, 3 in agent/test_dbos_crash_recovery.py, 21 in
# test_meetings.py and 1 in test_server_backend.py (the cloverops operation
# contract). Raising this number is how new database coverage becomes mandatory
# rather than optional.
#
# test_meetings.py and test_server_backend.py are here because they too branch on
# HOJA_TEST_POSTGRES_URL: without it they quietly run the SQLite path, so leaving
# them out of the job kept 22 cases' Postgres behaviour unexercised while the
# SQLite job reported green. tests/test_ci_pg_gate_coverage.py now fails if a
# gated module is ever dropped from the job again.
DEFAULT_MINIMUM = 37


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: assert_pg_gate_ran.py <junit.xml> [--minimum N]")
        return 2
    path = argv[1]
    minimum = DEFAULT_MINIMUM
    if "--minimum" in argv:
        minimum = int(argv[argv.index("--minimum") + 1])

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"GATE FAILED: cannot read the JUnit report {path!r}: {exc}")
        return 1

    cases = list(root.iter("testcase"))
    skipped = [
        f"{case.get('classname', '?')}::{case.get('name', '?')}"
        for case in cases
        if case.find("skipped") is not None
    ]
    failures = [
        f"{case.get('classname', '?')}::{case.get('name', '?')}"
        for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    ]

    print(f"collected={len(cases)} skipped={len(skipped)} failed={len(failures)} minimum={minimum}")
    for name in skipped:
        print(f"  SKIPPED {name}")

    problems: list[str] = []
    if not cases:
        problems.append("the report contains no test cases")
    if len(cases) < minimum:
        problems.append(f"only {len(cases)} cases ran, expected at least {minimum}")
    if skipped:
        problems.append(f"{len(skipped)} database tests skipped, so this gate proved nothing")
    if problems:
        for problem in problems:
            print(f"GATE FAILED: {problem}")
        return 1

    print("GATE OK: every database test executed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
