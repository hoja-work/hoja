"""Regression tests for scripts/check_docs.py; no third-party packages required."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
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


class GitLayoutTests(unittest.TestCase):
    """Run the real script against a real index, the way the pre-commit hook does.

    Two layouts: Hoja Cloud keeps the policy root at ``app/`` inside the repository, Hoja OSS at
    the repository root. The hook exports ``GIT_DIR`` and runs from the policy root, which used to
    make every path repository-relative and therefore unregistered.
    """

    def _repo(self, prefix: str) -> tuple[Path, Path]:
        top = Path(tempfile.mkdtemp(prefix="check-docs-"))
        self.addCleanup(shutil.rmtree, top, True)
        root = top / prefix if prefix else top
        (root / "scripts").mkdir(parents=True)
        (root / "docs").mkdir()
        shutil.copy(SCRIPT, root / "scripts" / "check_docs.py")
        (root / "README.md").write_text("# Hoja\n")
        (root / "docs" / "sla.md").write_text("# SLA\n")
        self._git(top, "init", "-q")
        self._git(top, "add", "-A")
        return top, root

    @staticmethod
    def _git(top: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=top, check=True, capture_output=True)

    def _check(self, top: Path, root: Path, *, hook_env: bool) -> subprocess.CompletedProcess[str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        if hook_env:
            env["GIT_DIR"] = str(top / ".git")
        return subprocess.run(
            [sys.executable, str(root / "scripts" / "check_docs.py"), "--staged"],
            cwd=root, env=env, capture_output=True, text=True,
        )

    def test_clean_index_passes_in_both_layouts_with_and_without_hook_env(self) -> None:
        for prefix in ("app", ""):
            for hook_env in (False, True):
                with self.subTest(prefix=prefix or "<root>", hook_env=hook_env):
                    top, root = self._repo(prefix)
                    result = self._check(top, root, hook_env=hook_env)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_unregistered_document_is_reported_relative_to_the_policy_root(self) -> None:
        for prefix in ("app", ""):
            for hook_env in (False, True):
                with self.subTest(prefix=prefix or "<root>", hook_env=hook_env):
                    top, root = self._repo(prefix)
                    (root / "docs" / "new-thing.md").write_text("# New thing\n")
                    self._git(top, "add", "-A")
                    result = self._check(top, root, hook_env=hook_env)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("  - docs/new-thing.md:", result.stderr)
                    self.assertNotIn("docs/sla.md", result.stderr)

    def test_documents_outside_the_policy_root_are_not_its_concern(self) -> None:
        top, root = self._repo("app")
        (top / "infra").mkdir()
        (top / "infra" / "runbook.md").write_text("# Runbook\n")
        self._git(top, "add", "-A")
        result = self._check(top, root, hook_env=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staged_content_is_read_from_the_index_not_the_worktree(self) -> None:
        for prefix in ("app", ""):
            with self.subTest(prefix=prefix or "<root>"):
                top, root = self._repo(prefix)
                (root / "docs" / "sla.md").write_text("# SLA\n\nStatus: Draft\n")
                self._git(top, "add", "-A")
                (root / "docs" / "sla.md").write_text("# SLA\n")
                result = self._check(top, root, hook_env=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("docs/sla.md: draft/proposal status", result.stderr)


if __name__ == "__main__":
    unittest.main()
