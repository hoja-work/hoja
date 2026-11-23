"""Fail when the browser suite skips instead of running.

``tests/e2e`` cases skip for two reasons that look identical in a green check:
the playwright package is absent, or the package is present and no chromium
binary is. Both produce a passing job over zero executed cases, which is the
same defect HOJA-303 fixed for the database job. This script reads the JUnit
report and exits non-zero unless the expected number of cases actually ran.

Only cases from the browser package count. Counting every ``<testcase>`` let a
report full of passing *unit* cases satisfy the minimum with no skip marker, so
the gate announced "every browser test executed" over zero browser cases. A
report that carries cases from outside ``tests/e2e`` is mis-wired: it cannot
prove anything about the browser, so it fails instead of passing quietly.

Usage: assert_e2e_gate_ran.py <junit.xml> [--minimum N]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

# 16 `def test_` functions live in tests/e2e: 1 in test_content_controls.py,
# 1 in test_fuzz_routes.py, 6 in test_realtime_sync.py, 8 in
# test_view_freshness.py. The copy also has one parametrized case, so the
# report normally carries 17. Use the lower number so a parametrization edit
# does not fail the gate for the wrong reason, while a whole missing module
# still does. Raising this is how new browser coverage becomes mandatory.
DEFAULT_MINIMUM = 16


BROWSER_CLASS_ROOTS = ("tests.e2e", "tests/e2e", "tests\\e2e")


def is_browser_case(case: ET.Element) -> bool:
    """True only for a case that lives in the ``tests/e2e`` package.

    pytest writes ``classname`` as the dotted import path and ``file`` as the
    relative path; either one places the case. Anything else is not a browser
    test whatever the report claims.
    """
    for attr in ("classname", "file"):
        value = case.get(attr) or ""
        if value.startswith(BROWSER_CLASS_ROOTS):
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: assert_e2e_gate_ran.py <junit.xml> [--minimum N]")
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
    browser_cases = [case for case in cases if is_browser_case(case)]
    foreign_cases = [case for case in cases if not is_browser_case(case)]
    skipped = [
        f"{case.get('classname', '?')}::{case.get('name', '?')}"
        for case in browser_cases
        if case.find("skipped") is not None
    ]
    failures = [
        f"{case.get('classname', '?')}::{case.get('name', '?')}"
        for case in browser_cases
        if case.find("failure") is not None or case.find("error") is not None
    ]

    print(
        f"collected={len(cases)} browser={len(browser_cases)} "
        f"skipped={len(skipped)} failed={len(failures)} minimum={minimum}"
    )
    for name in skipped:
        print(f"  SKIPPED {name}")

    problems: list[str] = []
    if not cases:
        problems.append("the report contains no test cases")
    if foreign_cases:
        preview = ", ".join(
            f"{case.get('classname', '?')}::{case.get('name', '?')}"
            for case in foreign_cases[:3]
        )
        problems.append(
            f"{len(foreign_cases)} cases are not browser tests ({preview}); "
            "run the report over tests/e2e, because these prove nothing about the browser"
        )
    if len(browser_cases) < minimum:
        problems.append(f"only {len(browser_cases)} browser cases ran, expected at least {minimum}")
    if skipped:
        problems.append(f"{len(skipped)} browser tests skipped, so this gate proved nothing")
    if problems:
        for problem in problems:
            print(f"GATE FAILED: {problem}")
        return 1

    print("GATE OK: every browser test executed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
