"""Whether row-level security actually binds *this* deployment, read from the
catalog and the role it connects as.

Two places answer that question, and before this module they disagreed:

* ``infra/scripts/hoja-rls`` — the operator tool. It generates the policies
  (ENABLE, never FORCE; see ``infra/docs/adr-002-shared-tables-rls.md``) and
  ``check`` reads the catalog back afterwards (``hoja-rls:252-285``).
* ``tenant_context.probe`` — what ``/ready`` runs inside the app, on every
  readiness probe, with the app's own credentials.

The probe used to look at ``organization.relrowsecurity`` alone. The policies it
could not see are the ones that matter: a table owner skips its own policies
when they are not forced, and a role with ``BYPASSRLS`` (or a superuser) skips
every policy. Either deployment — an app pointed at the owner URL that
``docker-compose.yml`` already holds for the one-shot ``migrate`` service, or a
single-URL install — served traffic with every policy inert while ``/ready``
answered ``{"status": "ready"}``.

``gaps`` is therefore deliberately fail-closed in the same direction as the
operator tool: every table in the schema must be either an operator-declared
global table or covered by an enabled ``hoja_tenant`` policy. A table that is
neither is reported, not ignored, which is ``hoja-rls``'s "refuse to apply while
a table is unclassified" rule expressed from the app's side.

``GLOBAL_TABLES`` mirrors ``infra/rls/global-tables.txt``;
``tests/test_rls_policy.py`` fails when the two lists drift.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

RLS_POLICY = "hoja_tenant"

# Same rows as infra/rls/global-tables.txt, plus alembic_version: Alembic
# bookkeeping in the shared-tenancy suites, never tenant data.
GLOBAL_TABLES = frozenset({
    "global_identity",
    "github_delivery",
    "identity_credential",
    "oauth_client",
    "oauth_state_nonce",
    "rate_limit_event",
    "schema_revision",
    "signup_flow",
    "signup_identity",
    "signup_eligibility",
    "signup_invite",
    "signup_session",
    "tenant_membership_hint",
    "turnstile_use",
    "alembic_version",
})

_CATALOG = text(
    """
    SELECT c.relname::text AS table_name,
           c.relrowsecurity AS rls,
           c.relforcerowsecurity AS "force",
           pg_get_userbyid(c.relowner)::text AS owner,
           pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_member,
           EXISTS (SELECT 1 FROM pg_policy p
                   WHERE p.polrelid = c.oid AND p.polname = :policy) AS policy
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    -- The schemas this connection actually resolves tables in, so a deployment
    -- (or a test schema) that is not on "public" is inspected where it serves.
    WHERE n.nspname = ANY(current_schemas(false)) AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)

_ROLE = text(
    "SELECT current_user::text AS role, rolsuper, rolbypassrls "
    "FROM pg_roles WHERE rolname = current_user"
)


def catalog_rows(conn: Any, policy: str = RLS_POLICY) -> list[dict[str, Any]]:
    """Every table on this connection's search path, with the facts that decide
    whether it is bound."""
    return [dict(row) for row in conn.execute(_CATALOG, {"policy": policy}).mappings()]


def role_facts(conn: Any) -> dict[str, Any]:
    """``rolsuper`` / ``rolbypassrls`` of the role this connection authenticated as."""
    return dict(conn.execute(_ROLE).mappings().one())


def gaps(rows: list[dict[str, Any]], role: dict[str, Any],
         globals_: frozenset[str] = GLOBAL_TABLES, policy: str = RLS_POLICY) -> list[tuple[str, str]]:
    """Every reason this deployment is not bound, most fundamental first.

    Returns ``(reason, detail)`` pairs; ``reason`` is the stable token ``/ready``
    reports and ``detail`` names the role or tables so an operator can act on it.
    """
    out: list[tuple[str, str]] = []
    who = role.get("role", "?")
    if role.get("rolsuper"):
        out.append(("rls_bypass_role", f"the app connects as {who}, a superuser; policies never apply"))
    elif role.get("rolbypassrls"):
        out.append(("rls_bypass_role", f"the app connects as {who} with BYPASSRLS; policies never apply"))

    # An owner skips its own policies unless they are forced, and forcing them
    # would blind the owner-run migrations (ADR-002). So ownership itself is the
    # gap: the app role must own nothing and reach no owner role.
    owned = sorted(r["table_name"] for r in rows if r["owner_member"])
    if owned:
        sample = ", ".join(owned[:3]) + (f", +{len(owned) - 3} more" if len(owned) > 3 else "")
        out.append((
            "rls_owner_role",
            f"the app role {who} owns (or is a member of the owner of) {len(owned)} table(s) "
            f"and policies are not forced: {sample}",
        ))

    missing = sorted(
        r["table_name"] for r in rows
        if r["table_name"] not in globals_ and not (r["rls"] and r["policy"])
    )
    if missing:
        sample = ", ".join(missing[:5]) + (f", +{len(missing) - 5} more" if len(missing) > 5 else "")
        out.append((
            "rls_disabled",
            f"row-level security is off, or the {policy} policy is missing, on "
            f"{len(missing)} tenant table(s): {sample}",
        ))
    return out
