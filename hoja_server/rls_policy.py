"""Whether row-level security actually binds *this* deployment, and how to bind it.

Three places answer that question, and before this module they disagreed:

* ``infra/scripts/hoja-rls`` — the operator tool. It generates the policies from
  the live catalog (ENABLE, never FORCE; see
  ``infra/docs/adr-002-shared-tables-rls.md``), ``apply`` regenerates them and
  ``check`` reads the catalog back afterwards.
* ``tenant_context.probe_report`` — what ``/ready`` runs inside the app, on every
  readiness probe, with the app's own credentials.
* ``hoja-serve migrate`` — the one-shot container that holds the owner credential
  and therefore the only process in a deployed stack that *can* install a policy
  (``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` requires ownership).

The probe used to look at ``organization.relrowsecurity`` alone. The policies it
could not see are the ones that matter: a table owner skips its own policies when
they are not forced, a role with ``BYPASSRLS`` (or a superuser) skips every
policy, and any table that no ``hoja-rls apply`` has visited yet has no policy at
all — which is what every revision that creates a table produces. A deployment
that skipped or predated the operator command served traffic with those policies
inert while ``/ready`` answered ``{"status": "ready"}``.

``gaps`` is therefore deliberately fail-closed in the same direction as the
operator tool: every table in the schema must be either an operator-declared
global table or covered by an enabled ``hoja_tenant`` policy. A table that is
neither is reported by name, not ignored, which is ``hoja-rls``'s "refuse to
apply while a table is unclassified" rule expressed from the app's side.

``heal`` is the other half: it derives the same classification from the live
catalog and installs the policies for every tenant table it is allowed to alter,
so a table added by a later revision cannot silently skip the gate. It runs from
``db.migrate``, which in a deployed stack is the owner-role one-shot container,
before the app that depends on it starts.

``GLOBAL_TABLES`` mirrors ``infra/rls/global-tables.txt``; ``test_rls_policy``
fails when the two lists drift, and when this module's classification disagrees
with the operator tool's on a real database.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
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

# How a table reaches its tenant. The first three are bound by a policy;
# "global" is deliberately not tenant data; "unresolved" is tenant data whose
# policy nobody can derive, which is why hoja-rls refuses to apply over it.
ORGANIZATION = "organization"
DIRECT = "direct"
CHILD = "child"
GLOBAL = "global"
UNRESOLVED = "unresolved"
BOUND_KINDS = frozenset({ORGANIZATION, DIRECT, CHILD})

# Per-table and aggregate reason tokens, in the order /ready reports them. The
# first two are about the role, the last two about the tables.
RLS_BYPASS_ROLE = "rls_bypass_role"
RLS_OWNER_ROLE = "rls_owner_role"
RLS_DISABLED = "rls_disabled"
POLICY_MISSING = "policy_missing"
RLS_UNSUPPORTED = "rls_unsupported"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# The tenant context the app sets at the start of every transaction
# (tenant_context._SET). A policy admits a row when the row's tenant is the
# request's tenant, or when the request is on an enumerated system path.
_CUR = "current_setting('hoja.org_id', true)"
_SYSTEM_ON = "current_setting('hoja.system', true) = 'on'"

_CLASSIFY = """
WITH tabs AS (
  SELECT c.oid, c.relname::text AS relname,
         EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                 AND a.attname = 'organization_id' AND NOT a.attisdropped) AS has_org
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')),
fks AS (
  SELECT k.conrelid, a.attname::text AS col, a.attnotnull AS notnull,
         p.relname::text AS parent, pa.attname::text AS pcol
  FROM pg_constraint k
  JOIN tabs p ON p.oid = k.confrelid AND p.has_org
  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
  JOIN pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = k.confkey[1]
  WHERE k.contype = 'f' AND array_length(k.conkey, 1) = 1),
cls AS (
  SELECT t.*, CASE
      WHEN t.relname = 'organization' THEN 'organization'
      WHEN t.has_org THEN 'direct'
      WHEN t.relname = ANY(__GLOBALS__::text[]) THEN 'global'
      WHEN EXISTS (SELECT 1 FROM fks f WHERE f.conrelid = t.oid) THEN 'child'
      ELSE 'unresolved' END AS kind
  FROM tabs t)
SELECT relname, kind, __SYS_OR__ || CASE kind
    WHEN 'organization' THEN __ORG_EXPR__
    WHEN 'direct' THEN __DIRECT_EXPR__
    WHEN 'child' THEN (
      SELECT string_agg(format(__CHILD__,
                               CASE WHEN f.notnull THEN '' ELSE format('%I.%I IS NULL OR ', relname, f.col) END,
                               f.parent, f.pcol, relname, f.col), ' AND ' ORDER BY f.col)
      FROM fks f WHERE f.conrelid = cls.oid)
    ELSE 'false' END AS expr
FROM cls ORDER BY kind, relname
"""

_STATE = """
SELECT c.relname::text AS table_name,
       c.relrowsecurity AS rls,
       c.relforcerowsecurity AS forced,
       pg_get_userbyid(c.relowner)::text AS owner,
       pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_member,
       EXISTS (SELECT 1 FROM pg_policy p
               WHERE p.polrelid = c.oid AND p.polname = :policy) AS policy
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""

_ROLE = text(
    "SELECT current_user::text AS role, rolsuper, rolbypassrls "
    "FROM pg_roles WHERE rolname = current_user"
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def execute_literal(conn: Any, statement: str) -> None:
    """Run one generated statement verbatim, on the connection's own transaction.

    Not ``exec_driver_sql``: SQLAlchemy hands psycopg an empty parameter tuple, and
    psycopg then scans the text for ``%`` placeholders, so a table or schema name
    containing a percent sign would be read as a format code. The raw cursor takes
    the statement as written. ``tenant_context.install`` uses the same route.
    """
    cursor = conn.connection.dbapi_connection.cursor()
    try:
        cursor.execute(statement)
    finally:
        cursor.close()


def _array(names: Iterable[str]) -> str:
    """A text[] literal for the allowlist. The names are a reviewed constant, but
    check them anyway: this text is concatenated into SQL."""
    names = sorted(names)
    for name in names:
        if not _IDENT.match(name):
            raise ValueError(f"{name!r} is not a plain lowercase identifier")
    return "ARRAY[" + ",".join(_literal(n) for n in names) + "]::text[]" if names else "ARRAY[]::text[]"


def classify_sql(globals_: Iterable[str] = GLOBAL_TABLES) -> str:
    """The classification query, with every identifier Postgres builds itself.

    ``:schema`` is bound (never interpolated), the allowlist is a literal array of
    validated identifiers, and ``quote_ident`` supplies the schema of the child
    subquery so a test schema is inspected where it lives.

    The substitutions are textual on purpose: the statement carries PostgreSQL
    ``format('%I')`` placeholders, which must reach the driver untouched.
    """
    # The child expression is assembled by Postgres, not by us: the schema is
    # resolved with quote_ident at execution, so the concatenation has to stay
    # SQL rather than become one string literal.
    child = ("'(%sEXISTS (SELECT 1 FROM ' || quote_ident(:schema) || '.%I p "
             f"WHERE p.%I = %I.%I AND p.organization_id = {_CUR.replace(chr(39), chr(39) * 2)}))'")
    sql = _CLASSIFY
    for token, value in (
        ("__GLOBALS__", _array(globals_)),
        ("__SYS_OR__", _literal(_SYSTEM_ON + " OR ")),
        ("__ORG_EXPR__", _literal("id = " + _CUR)),
        ("__DIRECT_EXPR__", _literal("organization_id = " + _CUR)),
        ("__CHILD__", child),
    ):
        sql = sql.replace(token, value)
    return sql


def classify(conn: Any, schema: str = "public",
             globals_: Iterable[str] = GLOBAL_TABLES) -> list[tuple[str, str, str]]:
    """``(table, kind, policy expression)`` for every table on ``schema``."""
    rows = conn.execute(text(classify_sql(globals_)), {"schema": schema}).mappings()
    return [(r["relname"], r["kind"], r["expr"] or "") for r in rows]


def schemas(conn: Any, schema: str | None = None) -> list[str]:
    """The schema(s) to inspect: the one asked for, else the connection's search path.

    Resolving from ``current_schemas(false)`` means a deployment is inspected where
    it serves — a test schema, or a search path that is not ``public`` — rather
    than where this module happens to assume it lives.
    """
    if schema:
        return [schema]
    return [row[0] for row in conn.execute(
        text("SELECT unnest(current_schemas(false))::text")).all()]


def _tables_in(conn: Any, schema: str, policy: str,
               globals_: Iterable[str]) -> list[dict[str, Any]]:
    state = {row["table_name"]: dict(row)
             for row in conn.execute(text(_STATE), {"schema": schema, "policy": policy}).mappings()}
    out: list[dict[str, Any]] = []
    for name, kind, expr in classify(conn, schema, globals_):
        row = state.get(name, {"table_name": name, "rls": False, "forced": False,
                               "owner": "?", "owner_member": False, "policy": False})
        row.update({"kind": kind, "expr": expr, "schema": schema})
        out.append(row)
    return out


def tables(conn: Any, schema: str | None = None, policy: str = RLS_POLICY,
           globals_: Iterable[str] = GLOBAL_TABLES) -> list[dict[str, Any]]:
    """Every table with the facts that decide whether it is bound, plus its kind."""
    return [row for name in schemas(conn, schema)
            for row in _tables_in(conn, name, policy, globals_)]


def role_facts(conn: Any) -> dict[str, Any]:
    """``rolsuper`` / ``rolbypassrls`` of the role this connection authenticated as."""
    return dict(conn.execute(_ROLE).mappings().one())


def table_reason(row: dict[str, Any]) -> str | None:
    """Why one table is not bound, or None when it is.

    A global table is not required to be bound; every other table is, which is
    what makes a table added by a later revision fail closed instead of passing
    the gate by being unknown.
    """
    if row["kind"] == GLOBAL:
        return None
    if not row["rls"]:
        return RLS_DISABLED
    if not row["policy"]:
        return POLICY_MISSING
    return None


def gaps(rows: list[dict[str, Any]], role: dict[str, Any],
         globals_: Iterable[str] = GLOBAL_TABLES, policy: str = RLS_POLICY) -> list[tuple[str, str]]:
    """Every reason this deployment is not bound, most fundamental first.

    Returns ``(reason, detail)`` pairs; ``reason`` is the stable token ``/ready``
    reports and ``detail`` names the role or *every* unbound table, so an operator
    can act on the body alone. Nothing here is a credential: the role name and the
    table names are the whole content, and the connection string is never read.
    """
    out: list[tuple[str, str]] = []
    who = role.get("role", "?")
    if role.get("rolsuper"):
        out.append((RLS_BYPASS_ROLE, f"the app connects as {who}, a superuser; policies never apply"))
    elif role.get("rolbypassrls"):
        out.append((RLS_BYPASS_ROLE, f"the app connects as {who} with BYPASSRLS; policies never apply"))

    # An owner skips its own policies unless they are forced, and forcing them
    # would blind the owner-run migrations (ADR-002). So ownership itself is the
    # gap: the app role must own nothing and reach no owner role.
    owned = sorted(r["table_name"] for r in rows if r["owner_member"])
    if owned:
        out.append((
            RLS_OWNER_ROLE,
            f"the app role {who} owns (or is a member of the owner of) {len(owned)} table(s) "
            f"and policies are not forced: {', '.join(owned)}",
        ))

    unbound = [(r["table_name"], table_reason(r)) for r in rows if table_reason(r)]
    if unbound:
        named = ", ".join(f"{name} ({reason})" for name, reason in unbound)
        # One aggregate token for both table-level failures, so a deploy script
        # that matches a reason keeps working; the per-table reason in ``tables``
        # says which one it is.
        out.append((
            RLS_DISABLED,
            f"{len(unbound)} tenant table(s) are not bound by a {policy} policy: {named}",
        ))
    return out


def report(conn: Any, schema: str | None = None, globals_: Iterable[str] = GLOBAL_TABLES,
           policy: str = RLS_POLICY) -> dict[str, Any]:
    """The per-table report: the whole verdict, as data.

    ``/ready`` serves it on failure and ``hoja-serve rls-report`` prints it, so a
    failing deployment is always explainable from one shape. It holds table names,
    a role name and booleans; never a DSN or a password.
    """
    rows = tables(conn, schema, policy, globals_)
    role = role_facts(conn)
    found = gaps(rows, role, globals_, policy)
    return {
        "bound": not found,
        "reason": found[0][0] if found else None,
        "detail": found[0][1] if found else None,
        "gaps": [detail for _, detail in found],
        "role": {"name": role.get("role"), "superuser": bool(role.get("rolsuper")),
                 "bypassrls": bool(role.get("rolbypassrls"))},
        "tables": [
            {
                "table": row["table_name"],
                "schema": row["schema"],
                "kind": row["kind"],
                "rls": bool(row["rls"]),
                "policy": bool(row["policy"]),
                "forced": bool(row["forced"]),
                "owner": row["owner"],
                "reason": table_reason(row),
            }
            for row in rows
        ],
    }


def heal_sql(rows: list[dict[str, Any]], schema: str = "public",
             policy: str = RLS_POLICY) -> list[str]:
    """The statements that bind every tenant table in ``rows``.

    Derived from the catalog on every call, which is what makes a table added by a
    later revision (or by hand) get a policy without anyone remembering to run the
    operator command. ENABLE, never FORCE: the owner runs the migrations and must
    keep seeing every row (ADR-002). Re-creating the policy unconditionally is
    what repairs a policy whose classification moved — ``api_key`` gained
    ``organization_id`` in revision 62 and stopped being a child table.
    """
    quote = f'"{schema}"'
    out: list[str] = []
    for row in rows:
        if row["kind"] not in BOUND_KINDS:
            continue
        name = row["table_name"]
        if not _IDENT.match(name):
            raise ValueError(f"{name!r} is not a plain lowercase identifier")
        target = f'{quote}."{name}"'
        out += [
            f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {target} NO FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS {policy} ON {target};",
            f"CREATE POLICY {policy} ON {target} FOR ALL USING ({row['expr']}) "
            f"WITH CHECK ({row['expr']});",
        ]
    return out


def heal(conn: Any, schema: str | None = None, policy: str = RLS_POLICY,
         globals_: Iterable[str] = GLOBAL_TABLES) -> dict[str, Any]:
    """Enable RLS and install the policies for every tenant table this role may alter.

    Idempotent: the statements are DDL that converges (ENABLE on an enabled table,
    ``DROP POLICY IF EXISTS`` then ``CREATE POLICY`` with the same expression), so
    running it on an already-bound database rewrites the same catalog state.

    A table this role does not own is skipped, not attempted: ``ALTER TABLE ...
    ENABLE ROW LEVEL SECURITY`` needs ownership, and a failed statement would abort
    the whole migration transaction. The caller's gate (``/ready``) then reports
    the tables it could not bind, which is the deployment-level answer rather than
    a migration crash.
    """
    rows = tables(conn, schema, policy, globals_)
    alterable = [r for r in rows if r["kind"] in BOUND_KINDS and r["owner_member"]]
    skipped = sorted(r["table_name"] for r in rows
                     if r["kind"] in BOUND_KINDS and not r["owner_member"])
    unresolved = sorted(r["table_name"] for r in rows if r["kind"] == UNRESOLVED)
    global_tables = sorted(r["table_name"] for r in rows if r["kind"] == GLOBAL)
    for table_schema in sorted({r["schema"] for r in alterable}):
        for statement in heal_sql([r for r in alterable if r["schema"] == table_schema],
                                  table_schema, policy):
            execute_literal(conn, statement)
    return {
        "bound": len(alterable),
        "skipped": skipped,
        "unresolved": unresolved,
        "global": global_tables,
        "schemas": sorted({r["schema"] for r in rows}),
    }
