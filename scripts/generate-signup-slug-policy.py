#!/usr/bin/env python3
"""Generate the page's copy of the workspace-address rule from the server's.

``hoja_server/signup_policy.py`` is the single source of truth for a workspace
address (itself a mirror of the provisioner, ``Hoja-Cloud/scripts/hoja-tenant``).
The page has to refuse the same inputs *before* a request is made, so it cannot
retype the rule: the regex, the length limit and the reserved names are generated
from those constants into TypeScript.

    .venv/bin/python scripts/generate-signup-slug-policy.py

``tests/test_signup_page.py`` regenerates in memory and fails when the committed
file differs, so editing the Python rule without regenerating is a test failure
rather than a page that quietly disagrees with the server.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hoja_server.signup_policy import (  # noqa: E402  (after sys.path)
    RESERVED_SLUGS,
    SLUG_MAX_LENGTH,
    SLUG_RE,
)

TARGET = ROOT / "frontend" / "src" / "signup" / "slugPolicy.generated.ts"

HEADER = """\
// GENERATED FILE — do not edit by hand.
//
// Source of truth: hoja_server/signup_policy.py (SLUG_RE, SLUG_MAX_LENGTH,
// RESERVED_SLUGS), which mirrors the provisioner's rule.
// Regenerate with: .venv/bin/python scripts/generate-signup-slug-policy.py
// Parity is enforced by tests/test_signup_page.py, so a server-side change that
// is not regenerated here fails the suite.
"""


def render_module() -> str:
    """The exact TypeScript the page imports. Pure: no I/O, so a test can diff it."""
    reserved = sorted(RESERVED_SLUGS)
    lines = [HEADER.rstrip("\n"), ""]
    lines.append("//: The provisioner's DNS-label expression, verbatim.")
    lines.append(f"export const SLUG_PATTERN = {json.dumps(SLUG_RE.pattern)};")
    lines.append("")
    lines.append("//: A DNS label is at most this many characters.")
    lines.append(f"export const SLUG_MAX_LENGTH = {SLUG_MAX_LENGTH};")
    lines.append("")
    lines.append("//: Names that already mean something on the base domain.")
    lines.append("export const RESERVED_SLUGS: readonly string[] = [")
    for name in reserved:
        lines.append(f"  {json.dumps(name)},")
    lines.append("];")
    return "\n".join(lines) + "\n"


def main() -> int:
    rendered = render_module()
    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current == rendered and "--check" in sys.argv:
        print(f"{TARGET.relative_to(ROOT)} is current")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} "
          f"({len(RESERVED_SLUGS)} reserved names, pattern {SLUG_RE.pattern!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
