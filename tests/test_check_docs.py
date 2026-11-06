"""Regression tests for scripts/check_docs.py; no third-party packages required."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
check_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_docs)


class DocumentationPolicyTests(unittest.TestCase):
    def test_allows_durable_documents_and_functional_content(self) -> None:
        paths = {
            "AGENTS.md",
            "CHANGELOG.md",
            "LICENSE",
            "README.md",
            "connectors/google_workspace/README.md",
            ".agents/skills/shadcn/SKILL.md",
            ".agents/skills/shadcn/registry.md",
            "hoja/skill_content/workflows.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            *check_docs.DURABLE_DOCS,
        }
        self.assertEqual(check_docs.validate(paths, lambda _path: "# Product guide\n"), [])

    def test_rejects_unapproved_and_work_artifact_paths(self) -> None:
        paths = {
            "notes.md",
            "feature-plan.md",
            "context/current-state.md",
            "plans/feature.md",
            "docs/new-feature.md",
            "docs/architecture-proposal.md",
            "docs/generated-audit.md",
            "frontend/README.md",
            ".agents/skills/shadcn/random.md",
            "hoja/skill_content/random.md",
            "internal/context.md",
        }
        errors = check_docs.validate(paths, lambda _path: "# Guide\n")
        self.assertEqual(len(errors), len(paths))
        for path in paths:
            self.assertTrue(any(error.startswith(f"{path}:") for error in errors), path)

    def test_rejects_slop_hidden_behind_a_normal_filename(self) -> None:
        errors = check_docs.validate(
            ["docs/DEPLOYMENT.md"],
            lambda _path: "# Deployment implementation plan\n\nSteps for future work.\n",
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("work artifact", errors[0])

    def test_rejects_proposal_status(self) -> None:
        errors = check_docs.validate(
            ["docs/DESIGN.md"],
            lambda _path: "# Architecture\n\n**Status:** proposal\n",
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("draft/proposal status", errors[0])

    def test_operational_policy_can_name_forbidden_artifacts(self) -> None:
        errors = check_docs.validate(
            ["AGENTS.md"],
            lambda _path: "# Contributor operations\n\nDo not commit plans or context reports.\n",
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
