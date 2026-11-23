"""Relative dates under a pinned clock: midnight, daylight-saving changes, month ends, leap days.

Contract (SPEC.md §3, Linear's DateTimeOrDuration): a duration resolves when the filter runs;
without a time part it names a UTC calendar day. These tests pin "now" at the instants where
that is easy to get wrong, check the process time zone has no effect, and check that the
browser (frontend/src/views/filterModel.ts resolveDate) lands on the same UTC day as the
server for every case — the chips' preview and the saved view must agree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from hoja_server import views as viewdocs

APP = Path(__file__).resolve().parents[1]

INSTANTS = [
    "2026-11-23T23:59:59+00:00",  # a second before UTC midnight
    "2026-11-24T00:00:00+00:00",  # UTC midnight
    "2026-11-01T05:59:00+00:00",  # US fall-back (06:00Z), just before
    "2026-11-01T06:01:00+00:00",  # just after
    "2027-03-14T06:59:00+00:00",  # US spring-forward (07:00Z)
    "2027-03-14T07:01:00+00:00",
    "2026-10-25T00:59:00+00:00",  # EU fall-back (01:00Z)
    "2026-10-25T01:01:00+00:00",
    "2027-01-31T12:00:00+00:00",  # month end: +P1M clamps to Feb 28
    "2028-02-29T12:00:00+00:00",  # leap day: -P1Y clamps to Feb 28
]
VALUES = ["today", "yesterday", "tomorrow", "overdue", "-P1D", "P1D", "-P1W", "P1W", "-P2W",
          "-P1M", "P1M", "-P3M", "-P1Y", "P1Y", "-P10D", "P3D"]

EXPECTED = {
    ("2026-11-23T23:59:59+00:00", "today"): "2026-11-23",
    ("2026-11-24T00:00:00+00:00", "today"): "2026-11-24",
    ("2026-11-24T00:00:00+00:00", "-P1W"): "2026-11-17",
    ("2026-11-01T05:59:00+00:00", "P1D"): "2026-11-02",
    ("2026-11-01T06:01:00+00:00", "-P1W"): "2026-10-25",
    ("2027-03-14T06:59:00+00:00", "P1W"): "2027-03-21",
    ("2027-01-31T12:00:00+00:00", "P1M"): "2027-02-28",
    ("2028-02-29T12:00:00+00:00", "-P1Y"): "2027-02-28",
    ("2028-02-29T12:00:00+00:00", "P1Y"): "2029-02-28",
    ("2026-10-25T00:59:00+00:00", "yesterday"): "2026-10-24",
}


def _now(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.mark.parametrize(("instant", "value"), sorted(EXPECTED))
def test_pinned_instants(instant, value):
    assert viewdocs.resolve_date(value, _now(instant)) == EXPECTED[(instant, value)]


@pytest.mark.parametrize("tz", ["America/New_York", "Asia/Kolkata", "Pacific/Kiritimati", "UTC"])
def test_process_time_zone_has_no_effect(tz, monkeypatch):
    base = {(i, v): viewdocs.resolve_date(v, _now(i)) for i in INSTANTS for v in VALUES}
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        again = {(i, v): viewdocs.resolve_date(v, _now(i)) for i in INSTANTS for v in VALUES}
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    assert again == base


def test_instant_in_another_offset_is_the_same_moment():
    """23:30 in New York on Nov 23 is Nov 24 in UTC: 'today' follows UTC."""
    ny = datetime.fromisoformat("2026-11-23T23:30:00-05:00")
    assert viewdocs.resolve_date("today", ny) == "2026-11-24"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_browser_and_server_resolve_the_same_utc_day():
    script = """
import {resolveDate} from './src/views/filterModel.ts';
const cases = JSON.parse(process.argv[1]);
const out = cases.map(([instant, value]) => {
  const r = resolveDate(value, Date.parse(instant));
  return r ? new Date(r.start).toISOString().slice(0, 10) : null;
});
console.log(JSON.stringify(out));
"""
    cases = [[i, v] for i in INSTANTS for v in VALUES if v != "overdue"]
    frontend = APP / "frontend"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--import", "./scripts/ts-resolve.mjs",
         "--input-type=module", "-e", script, json.dumps(cases)],
        cwd=frontend, capture_output=True, text=True, env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    browser = json.loads(proc.stdout)
    server = [viewdocs.resolve_date(v, _now(i))[:10] for i, v in cases]
    mismatches = [(c, b, s) for c, b, s in zip(cases, browser, server, strict=True) if b != s]
    assert not mismatches, mismatches[:10]


def test_filter_at_a_pinned_clock_selects_by_utc_day():
    """'Created after 1 week ago' at 00:00:30Z on Nov 24 keeps a task created Nov 17 23:59Z out
    and Nov 18 00:00Z in (after the day a week ago, whole UTC days)."""
    now = _now("2026-11-24T00:00:30+00:00")
    compiled = viewdocs.compile_filter("issue", viewdocs.parse_filters("issue", {"createdAt": {"after": "-P1W"}}),
                                       now=now)
    text = json.dumps(compiled)
    assert "2026-11-18" in text
