#!/usr/bin/env python
"""Mark the issue* root fields @deprecated in schema.graphql (HOJA-288). Idempotent."""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDL = ROOT / "hoja_server/schema.graphql"
REASON = "Use the task* field instead; this name is kept as a wire-compatible alias."
TARGETS = {
    "issue", "issues", "searchIssues", "issueCreate", "issueUpdate", "issueDelete",
    "issueArchive", "issueSubscribe", "issueUnsubscribe", "myIssueActivity", "issueLabels",
    "issueLabelCreate", "issueLabelUpdate", "issueLabelDelete", "issueLabelSettings",
}
FIELD_HEAD = re.compile(r"^(\s+)(\w+)(\(|:)")


def main() -> int:
    lines = SDL.read_text().splitlines()
    out: list[str] = []
    block: str | None = None
    changed = 0
    for line in lines:
        head = line.split("{", 1)[0].strip()
        if head.startswith("type ") and head[5:].strip() in ("Query", "Mutation"):
            block = head[5:].strip()
        elif line.strip() == "}" and block:
            block = None
        m = FIELD_HEAD.match(line)
        if block and m and m.group(2) in TARGETS and "@deprecated" not in line:
            line = line.rstrip() + f' @deprecated(reason: "{REASON}")'
            changed += 1
        out.append(line)
    SDL.write_text("\n".join(out) + "\n")
    print(f"deprecated {changed} field(s) in {SDL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
