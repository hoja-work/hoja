#!/usr/bin/env python3
"""Keep repository documentation durable, reviewed, and free of work artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

DOCUMENT_EXTENSIONS = {".md", ".mdx", ".rst"}

# Standard community and release documents are allowed at the repository root.
ROOT_DOCUMENTS = {
    "AGENTS.md",
    "AUTHORS.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "LICENSE.md",
    "MAINTAINERS.md",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
}

# docs/ is a closed collection. Adding a document requires the same review that
# establishes it as durable product, API, architecture, or operations guidance.
DURABLE_DOCS = {
    "docs/DEPLOYMENT.md",
    "docs/DESIGN.md",
    "docs/cycle-metrics.md",
    "docs/dependency-security.md",
    "docs/email-adapter.md",
    "docs/email-delivery.md",
    "docs/hoja-mcp.md",
    "docs/extensions.md",
    "docs/webhooks.md",
    "docs/meetings.md",
    "docs/decisions.md",
    "docs/views.md",
    "docs/notifications/android-and-ux.md",
}

# No directory gets a blanket Markdown exemption. New component docs and
# functional skill content must be registered explicitly in the same review.
COMPONENT_READMES = {
    "connectors/google_workspace/README.md",
    "connectors/notion/README.md",
    "connectors/slack/README.md",
    "connectors/calendly/README.md",
    "connectors/sentry/README.md",
    "connectors/zoom/README.md",
    "connectors/grafana/README.md",
    "hoja_server/README.md",
    "vendor/README.md",
}
FUNCTIONAL_CONTENT = {
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".agents/skills/migrate-radix-to-base/SKILL.md",
    ".agents/skills/migrate-radix-to-base/class-mapping.md",
    ".agents/skills/migrate-radix-to-base/consumer-props.md",
    ".agents/skills/migrate-radix-to-base/disclosure.md",
    ".agents/skills/migrate-radix-to-base/display-misc.md",
    ".agents/skills/migrate-radix-to-base/form-controls.md",
    ".agents/skills/migrate-radix-to-base/menus.md",
    ".agents/skills/migrate-radix-to-base/overlays.md",
    ".agents/skills/migrate-radix-to-base/universal-patterns.md",
    ".agents/skills/migrate-radix-to-base/wrapper-shapes.md",
    ".agents/skills/shadcn/SKILL.md",
    ".agents/skills/shadcn/cli.md",
    ".agents/skills/shadcn/customization.md",
    ".agents/skills/shadcn/mcp.md",
    ".agents/skills/shadcn/registry.md",
    ".agents/skills/shadcn/rules/base-vs-radix.md",
    ".agents/skills/shadcn/rules/chat.md",
    ".agents/skills/shadcn/rules/composition.md",
    ".agents/skills/shadcn/rules/forms.md",
    ".agents/skills/shadcn/rules/icons.md",
    ".agents/skills/shadcn/rules/styling.md",
    "hoja/skill_content/commands.md",
    "hoja/skill_content/examples.md",
    "hoja/skill_content/filters.md",
    "hoja/skill_content/output-formats.md",
    "hoja/skill_content/overview.md",
    "hoja/skill_content/workflows.md",
    "skills/hoja/SKILL.md",
    "skills/hoja/references/commands.md",
    "skills/hoja/references/examples.md",
    "skills/hoja/references/filters.md",
    "skills/hoja/references/output-formats.md",
    "skills/hoja/references/overview.md",
    "skills/hoja/references/workflows.md",
}

SLOP_DIRECTORIES = {
    "audit",
    "audits",
    "brainstorm",
    "brainstorms",
    "context",
    "contexts",
    "note",
    "notes",
    "plan",
    "plans",
    "proposal",
    "proposals",
    "report",
    "reports",
    "scratch",
}
SLOP_STEM_WORDS = SLOP_DIRECTORIES | {"roadmap", "roadmaps", "todo", "todos"}
SLOP_TITLE_WORDS = re.compile(
    r"\b(?:audit|brainstorm|context|notes?|plans?|proposals?|reports?|roadmaps?)\b",
    re.IGNORECASE,
)
PROPOSAL_STATUS = re.compile(
    r"^\s*(?:\*\*)?status(?:\*\*)?\s*:\s*"
    r"(?:\*\*)?\s*(?:brainstorm|draft|plan|planned|planning|proposal|proposed)\b",
    re.IGNORECASE | re.MULTILINE,
)


def is_document(path: str) -> bool:
    name = PurePosixPath(path).name
    return name == "LICENSE" or PurePosixPath(path).suffix.lower() in DOCUMENT_EXTENSIONS


def is_functional_content(path: str) -> bool:
    return path in FUNCTIONAL_CONTENT


def path_error(path: str) -> str | None:
    """Return a policy violation for one repository-relative path."""
    if not is_document(path) or is_functional_content(path):
        return None

    parsed = PurePosixPath(path)
    directories = {part.casefold() for part in parsed.parts[:-1]}
    slop_directory = sorted(directories & SLOP_DIRECTORIES)
    if slop_directory:
        return f"{path}: work-artifact directory '{slop_directory[0]}' is not documentation storage"

    stem_words = {word for word in re.split(r"[^a-z0-9]+", parsed.stem.casefold()) if word}
    slop_stem = sorted(stem_words & SLOP_STEM_WORDS)
    if slop_stem:
        return f"{path}: plan, context, note, proposal, audit, and report files do not belong in Git"

    if path in ROOT_DOCUMENTS or path in COMPONENT_READMES:
        return None
    if path in DURABLE_DOCS:
        return None
    if len(parsed.parts) == 1:
        return f"{path}: unapproved root document; durable root files are listed in scripts/check_docs.py"
    if parsed.parts[0] == "docs":
        return f"{path}: docs/ is allowlisted; add only durable product guidance after reviewer approval"
    return f"{path}: unregistered standalone document; every Markdown path requires explicit review"


def content_error(path: str, text: str) -> str | None:
    """Catch slop whose filename looks innocuous."""
    first_heading = next(
        (line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")),
        "",
    )
    if SLOP_TITLE_WORDS.search(first_heading):
        return f"{path}: title identifies a work artifact rather than durable documentation"
    if PROPOSAL_STATUS.search(text):
        return f"{path}: draft/proposal status belongs in an issue or pull request, not durable docs"
    return None


def validate(paths: Iterable[str], read_text: Callable[[str], str]) -> list[str]:
    errors: list[str] = []
    for path in sorted(set(paths)):
        violation = path_error(path)
        if violation:
            errors.append(violation)
            continue
        if not is_document(path) or is_functional_content(path):
            continue
        try:
            violation = content_error(path, read_text(path))
        except (OSError, UnicodeError) as error:
            violation = f"{path}: could not read document ({error})"
        if violation:
            errors.append(violation)
    return errors


def repository_paths(root: Path, *, staged: bool = False) -> list[str]:
    command = ["git", "ls-files", "-z", "--cached"]
    if not staged:
        command.extend(["--others", "--exclude-standard"])
    result = subprocess.run(
        command,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # A locally deleted file remains in the index until it is staged. It is not
    # a candidate anymore; CI always evaluates the committed checkout.
    return [
        path
        for path in result.stdout.split("\0")
        if path and is_document(path) and (staged or (root / path).is_file())
    ]


def staged_text(root: Path, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f":{path}"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help="check the complete staged index instead of the working tree",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = repository_paths(root, staged=args.staged)
    if args.staged:
        errors = validate(paths, lambda path: staged_text(root, path))
    else:
        errors = validate(paths, lambda path: (root / path).read_text(encoding="utf-8"))
    if errors:
        print("Documentation hygiene failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "\nKeep durable product documentation only. Use issues and pull requests "
            "for plans, decisions, context, audits, and temporary reports.",
            file=sys.stderr,
        )
        return 1
    source = "staged " if args.staged else ""
    print(f"Documentation hygiene passed ({len(paths)} {source}candidate files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
