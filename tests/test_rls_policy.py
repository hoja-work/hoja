"""The verdict /ready runs, tested without a database.

The interesting part is the decision table, so the rows are literal here. The
last test is the drift guard between the app's allowlist and the operator's.
"""
from pathlib import Path

import pytest

from hoja_server import rls_policy

APP_ROLE = {"role": "hoja_cp_app", "rolsuper": False, "rolbypassrls": False}


def _row(name: str, rls: bool = True, policy: bool = True, owner_member: bool = False) -> dict:
    return {"table_name": name, "rls": rls, "force": False, "owner": "hoja_cp_owner",
            "owner_member": owner_member, "policy": policy}


def test_a_bound_deployment_has_no_gaps():
    assert rls_policy.gaps([_row("organization"), _row("issue")], APP_ROLE) == []


def test_a_superuser_is_a_bypass_role():
    reasons = [code for code, _ in rls_policy.gaps([], {"role": "postgres", "rolsuper": True, "rolbypassrls": False})]
    assert reasons == ["rls_bypass_role"]


def test_bypassrls_outranks_the_table_checks():
    rows = [_row("organization", rls=False)]
    assert [code for code, _ in rls_policy.gaps(rows, {"role": "x", "rolsuper": False, "rolbypassrls": True})] \
        == ["rls_bypass_role", "rls_disabled"]


def test_owning_a_table_is_a_gap_and_names_it():
    gaps = rls_policy.gaps([_row("organization", owner_member=True)], APP_ROLE)
    assert gaps[0][0] == "rls_owner_role" and "organization" in gaps[0][1]


def test_a_policy_without_rls_enabled_is_a_gap():
    gaps = rls_policy.gaps([_row("issue", rls=False, policy=True)], APP_ROLE)
    assert gaps[0][0] == "rls_disabled" and "issue" in gaps[0][1]


def test_rls_enabled_without_the_policy_is_a_gap():
    gaps = rls_policy.gaps([_row("issue", rls=True, policy=False)], APP_ROLE)
    assert gaps[0][0] == "rls_disabled"


def test_global_tables_are_not_required_to_be_bound():
    assert rls_policy.gaps([_row("signup_flow", rls=False, policy=False)], APP_ROLE) == []


def test_an_undeclared_table_fails_closed():
    """A new table is tenant data until someone says otherwise — the same rule
    hoja-rls applies when it refuses to apply over an unclassified table."""
    assert [code for code, _ in rls_policy.gaps([_row("mystery", rls=False, policy=False)], APP_ROLE)] \
        == ["rls_disabled"]


def test_the_allowlist_matches_the_operator_file():
    path = Path(__file__).resolve().parents[2] / "infra" / "rls" / "global-tables.txt"
    if not path.is_file():
        pytest.skip("infra/rls/global-tables.txt is not in this checkout")
    operator = {
        line.split()[0] for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert operator <= rls_policy.GLOBAL_TABLES, "a global table in the operator file is missing from the app"
    # The one deliberate extra: Alembic's bookkeeping table in the shared-tenancy suites.
    assert rls_policy.GLOBAL_TABLES - operator == {"alembic_version"}
