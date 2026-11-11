#!/usr/bin/env python3
"""HOJA-288 gate: prose in docs says "task", not "issue".

The API keeps `issue*` names as deprecated aliases, so documentation has to be
able to *name* them. The rule is therefore positional, not lexical:

  - a fenced code block is code, not prose            -> allowed
  - a `code span` is a name the API really uses       -> allowed
  - anything else containing the word issue/issues    -> must say task

Usage: python3 scripts/check_docs_vocabulary.py [paths...]
Exit 0 = clean, 1 = prose still calls them issues.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORD = re.compile(r"\bissues?\b", re.IGNORECASE)
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`]*`")


def prose_lines(path: Path):
    """Yield (lineno, prose) with fences and inline code removed."""
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        yield number, CODE_SPAN.sub("``", line)


def main(argv: list[str]) -> int:
    # CHANGELOG.md is deliberately absent: it records what shipped in past
    # releases under the name those releases used. Rewriting it would falsify the
    # history the file exists to keep; the rename is announced at its top instead.
    roots = [Path(a) for a in argv[1:]] or [Path("README.md"), Path("docs")]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(root.rglob("*.md")))
        elif root.exists():
            files.append(root)
    problems = []
    for path in files:
        for number, prose in prose_lines(path):
            if WORD.search(prose):
                problems.append(f"{path}:{number}: {prose.strip()[:120]}")
    if problems:
        print("Documentation prose still says \"issue\" (HOJA-288); say \"task\":\n")
        for problem in problems:
            print(f"  - {problem}")
        print(f"\n{len(problems)} line(s). API names belong in `code spans`.")
        return 1
    print(f"docs vocabulary: clean ({len(files)} markdown files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
