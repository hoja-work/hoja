"""Fail when a test job passed without executing the tests it exists for.

A skipped test looks exactly like a passing one in a green check, and pytest
exits 0 for a run made entirely of skips. Two jobs therefore report their JUnit
XML to this script instead of trusting the exit code:

* ``pytest-postgres`` runs the modules that are gated on
  ``HOJA_TEST_POSTGRES_URL``. A missing URL skips them, so by default any
  skipped case fails the gate.
* ``pytest`` runs the whole SQLite suite. Some cases are *legitimately* skipped
  there (the Postgres-gated ones, which the other job owns), so it passes
  ``--allow-skips`` and gets a floor on the number of cases that actually
  executed. Without a floor, a suite that collects every test and skips all of
  them is a green check that proves nothing.

Usage: assert_pg_gate_ran.py <junit.xml> [--minimum N] [--allow-skips]

``--allow-skips`` disables the skip rule but still requires ``--minimum``
*executed* cases, so it cannot be used to bless a suite that ran nothing.
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
        print("usage: assert_pg_gate_ran.py <junit.xml> [--minimum N] [--allow-skips]")
        return 2
    path = argv[1]
    minimum = DEFAULT_MINIMUM
    allow_skips = "--allow-skips" in argv
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
    # A failure still executed; a skip did not. The floor for the broad suite
    # counts only the cases that actually ran.
    executed = len(cases) - len(skipped)

    print(
        f"collected={len(cases)} executed={executed} skipped={len(skipped)} "
        f"failed={len(failures)} minimum={minimum}"
    )
    for name in skipped:
        print(f"  SKIPPED {name}")

    problems: list[str] = []
    if not cases:
        problems.append("the report contains no test cases")
    if allow_skips:
        if executed < minimum:
            problems.append(f"only {executed} cases executed, expected at least {minimum}")
    elif len(cases) < minimum:
        problems.append(f"only {len(cases)} cases ran, expected at least {minimum}")
    if skipped and not allow_skips:
        problems.append(f"{len(skipped)} database tests skipped, so this gate proved nothing")
    if problems:
        for problem in problems:
            print(f"GATE FAILED: {problem}")
        return 1

    print("GATE OK: the tests this gate exists for executed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
