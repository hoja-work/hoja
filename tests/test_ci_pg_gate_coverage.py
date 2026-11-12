"""The Postgres CI job must run every module that branches on HOJA_TEST_POSTGRES_URL.

``tests/rls_mode.py``, ``tests/test_meetings.py`` and parts of
``tests/test_server_backend.py`` change what they exercise when
``HOJA_TEST_POSTGRES_URL`` is set: without it they quietly run the SQLite path.
That is worse than a skip. A skipped test at least announces itself; a test that
silently takes the cheaper branch looks identical to a passing one, so a module
the ``pytest-postgres`` job does not name would keep its Postgres path
unexercised while ``pytest`` reports green.

This test reads the workflow as text (no YAML dependency) and requires the
database job's step to name each such module, either as a whole file or as an
explicit ``file::test`` node. It fails if a new gate is added to a module the
job does not cover, which is the only way this hole closes for good.

See HOJA-303 (the suite ran in no CI job at all before ``tests.yml`` existed)
and the ``pytest-postgres`` job comment, which promises exactly this coverage.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
TESTS = REPO / "tests"

# Helpers, not test modules: they read the variable to decide how a fixture
# behaves, and they are exercised through the modules that import them.
HELPERS = {"conftest.py", "rls_mode.py"}

# This guard names the variable in its own text; it is not a gated test module.
SELF = "tests/test_ci_pg_gate_coverage.py"


def postgres_step() -> str:
    """The text of the ``Database-backed tests`` run step in the tests workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "name: Database-backed tests"
    start = text.index(marker)
    rest = text[start:]
    # The step's command block ends at the next top-level step/level marker.
    lines: list[str] = []
    for line in rest.splitlines()[1:]:
        if line.strip().startswith("- ") or line.strip() == "name: Assert the database tests executed":
            break
        lines.append(line)
    return "\n".join(lines)


def gated_modules() -> list[str]:
    found: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in HELPERS:
            continue
        relative = path.relative_to(REPO).as_posix()
        if relative == SELF:
            continue
        if "HOJA_TEST_POSTGRES_URL" in path.read_text(encoding="utf-8"):
            found.append(relative)
    return found


def test_every_postgres_gated_module_is_run_by_the_database_job() -> None:
    step = postgres_step()
    missing = [module for module in gated_modules() if module not in step]
    assert not missing, (
        "these modules change behaviour when HOJA_TEST_POSTGRES_URL is set but the "
        f"pytest-postgres job never runs them, so their Postgres path is green by "
        f"absence: {missing}. Add each module (or a file::test node) to the "
        "'Database-backed tests' step in .github/workflows/tests.yml."
    )


def test_the_gate_finds_the_modules_it_is_supposed_to_guard() -> None:
    """Guard against the discovery above silently matching nothing."""
    modules = gated_modules()
    assert "tests/test_server_postgres.py" in modules
    assert "tests/test_tenant_context.py" in modules
