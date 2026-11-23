"""`hoja-serve` entry point.

Subcommands:
  serve   Start the GraphQL backend.
  seed    Create an organization, user, team, states, and token.
  token   Mint a token for an existing organization user.
  rls-report  Print the per-table row-level-security verdict /ready enforces.
  create-workspace  Create (or repair) a workspace in the shared database.
  tenant-doctor  Read-only check that a workspace host is provisioned and reachable.
  connector-mcp  Serve the connector MCP tools (Google Workspace, Notion and Slack) on HOJA_CONNECTOR_MCP_URL.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy.exc import IntegrityError

from hoja_server import __version__
from hoja_server.db import (
    api_key,
    display_database_target,
    gen_token,
    make_engine,
    migrate,
    new_id,
    now_iso,
    resolve_database_target,
    resolve_token_identity,
    seed_tenant,
    token_hash,
)


def _database_target(args) -> str:
    return resolve_database_target(
        database_url=args.database_url,
        db_path=args.db,
        tenant=args.tenant,
    )


def _worker_app():
    """uvicorn factory for --workers > 1 (each worker process builds its own app)."""
    import os

    from hoja_server.app import create_app
    return create_app(database_url=os.environ["HOJA_SERVE_TARGET"],
                      open_mode=os.environ.get("HOJA_SERVE_OPEN") == "1")


def _cmd_serve(args):
    import os

    import uvicorn

    from hoja_server.app import create_app
    from hoja_server.logging_setup import configure_logging

    # The application's own loggers are not reachable from --log-level: uvicorn
    # configures the ``uvicorn.*`` loggers and leaves the root logger at WARNING,
    # so ``_security_log.info`` was discarded before formatting. Enable them here,
    # separately, so the auth funnel is visible without switching on uvicorn's
    # access log (which would print ?code= and ?t= from the request line).
    configure_logging(args.security_log_level)
    target = _database_target(args)
    _warn_if_unbound(target)
    print(f"hoja-serve v{__version__}")
    print(f"  tenant : {args.tenant}")
    print(f"  db     : {display_database_target(target)}")
    print(f"  url    : http://{args.host}:{args.port}/graphql")
    print(f"  open   : {args.open}")
    options = {"host": args.host, "port": args.port, "log_level": args.log_level,
               "ws_per_message_deflate": args.ws_deflate, "backlog": 4096}
    if args.workers > 1:
        # Realtime fan-out is per process; workers share change events through the
        # database (PostgreSQL LISTEN/NOTIFY, or polling the SQLite change log).
        print(f"  workers: {args.workers}")
        os.environ["HOJA_SERVE_TARGET"] = target
        os.environ["HOJA_SERVE_OPEN"] = "1" if args.open else "0"
        uvicorn.run("hoja_server.cli:_worker_app", factory=True, workers=args.workers, **options)
        return
    uvicorn.run(create_app(database_url=target, open_mode=args.open), **options)


def _cmd_migrate(args):
    target = _database_target(args)
    engine = make_engine(target)
    try:
        summary = migrate(engine)
    except (RuntimeError, IntegrityError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        engine.dispose()
    print(f"Migrated database at {display_database_target(target)}")
    if summary:
        print(f"  rls    : {summary['bound']} tenant table(s) bound by hoja_tenant in "
              f"{', '.join(summary['schemas']) or 'no schema'}, {len(summary['skipped'])} skipped "
              f"(not owned by this role), {len(summary['unresolved'])} unclassified")
        for name in summary["skipped"]:
            print(f"  skipped: {name} (owned by another role; /ready will refuse it)")
        for name in summary["unresolved"]:
            print(f"  unclassified: {name} (no organization_id and no tenant foreign key; "
                  f"classify it in rls/global-tables.txt or /ready will refuse it)",
                  file=sys.stderr)


def _warn_if_unbound(target: str) -> None:
    """Say in this container's log why /ready is about to refuse the deployment.

    A readiness probe answers 503 and whatever runs the deploy may not keep the
    body (a recreated container took the logs with it once), so the reason is also
    emitted here, once, before the server starts serving.
    """
    import os

    if not os.environ.get("HOJA_REQUIRE_RLS"):
        return
    from hoja_server import rls_policy

    log = logging.getLogger("hoja_server.rls")
    engine = None
    try:
        engine = make_engine(target)
        if engine.dialect.name != "postgresql":
            log.warning("rls: HOJA_REQUIRE_RLS is set but this deployment is not PostgreSQL; "
                        "/ready will answer 503 %s", rls_policy.RLS_UNSUPPORTED)
            return
        with engine.connect() as conn:
            report = rls_policy.report(conn)
    except Exception as exc:  # never stop the server from starting over a diagnostic
        log.warning("rls: could not read the catalog before startup: %s", type(exc).__name__)
        return
    finally:
        if engine is not None:
            engine.dispose()
    if report["bound"]:
        log.info("rls: every tenant table is bound by %s", rls_policy.RLS_POLICY)
        return
    log.error("rls: /ready will answer 503 %s: %s", report["reason"], report["detail"])
    for gap in report["gaps"][1:]:
        log.error("rls: also %s", gap)


def _cmd_rls_report(args):
    """One-shot, read-only: print the per-table verdict ``/ready`` enforces.

    Only catalog SELECTs are issued, so this is safe against a production database
    with the app's own credentials. Exit 0 when bound, 1 when not, 2 when the
    deployment has no row-level security at all (not PostgreSQL).
    """
    from hoja_server import rls_policy

    target = _database_target(args)
    engine = make_engine(target)
    try:
        if engine.dialect.name != "postgresql":
            print(f"{rls_policy.RLS_UNSUPPORTED}: row-level security only exists on "
                  f"PostgreSQL ({display_database_target(target)})", file=sys.stderr)
            raise SystemExit(2)
        with engine.connect() as conn:
            report = rls_policy.report(conn)
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        role = report["role"]
        print(f"database: {display_database_target(target)}")
        print(f"role    : {role['name']} superuser={role['superuser']} bypassrls={role['bypassrls']}")
        print("verdict : " + ("BOUND" if report["bound"] else f"NOT BOUND ({report['reason']})"))
        for gap in report["gaps"]:
            print(f"  gap   : {gap}")
        print()
        print(f"{'table':<32} {'kind':<13} {'rls':<5} {'policy':<6} {'owner':<16} reason")
        for row in report["tables"]:
            print(f"{row['table']:<32} {row['kind']:<13} {row['rls']!s:<5} "
                  f"{row['policy']!s:<6} {row['owner']:<16} {row['reason'] or ''}")
    raise SystemExit(0 if report["bound"] else 1)


def _cmd_seed(args):
    target = _database_target(args)
    engine = make_engine(target)
    migrate(engine)
    try:
        info = seed_tenant(
            engine, org_name=args.org, org_url_key=args.org_key,
            team_key=args.team_key, team_name=args.team_name,
            user_name=args.user, user_email=args.email,
            token=args.token, demo_issues=not args.no_demo,
        )
    except ValueError as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Seeded tenant '{args.tenant}' at {display_database_target(target)}")
    print(f"  org      : {args.org}")
    print(f"  team     : {info['team_key']}")
    print(f"  user     : {args.user} <{args.email}>")
    print(f"  TOKEN    : {info['token']}")
    print()
    print("Use it with hoja:")
    print(f"  export HOJA_TOKEN='{info['token']}'")
    print(f"  hoja --token '{info['token']}' me   # (after wiring base_url)")


def _cmd_token(args):
    target = _database_target(args)
    engine = make_engine(target)
    try:
        user_id, org_id = resolve_token_identity(
            engine,
            organization_ref=args.organization,
            user_ref=args.user,
            email=args.email,
        )
    except ValueError as exc:
        print(f"Token creation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        with engine.begin() as conn:
            tok = args.token or gen_token()
            conn.execute(api_key.insert().values(
                id=new_id(), token_hash=token_hash(tok), label=args.label,
                user_id=user_id, organization_id=org_id, created_at=now_iso()))
    except IntegrityError as exc:
        print("Token creation failed: API token already exists.", file=sys.stderr)
        raise SystemExit(1) from exc
    print(tok)


def _cmd_mail(args):
    """Drain the mail outbox once. Safe to run on a schedule, safe to re-run."""
    from hoja_server import mail_config, mail_outbox

    config = mail_config.current()
    if not config.configured:
        print("Email is not configured (HOJA_MAIL_TRANSPORT=disabled); nothing sent.",
              file=sys.stderr)
        raise SystemExit(2)
    engine = make_engine(_database_target(args))
    result = mail_outbox.deliver_pending(engine, config, limit=args.limit)
    print(f"claimed={result['claimed']} sent={result['sent']} failed={result['failed']}")
    raise SystemExit(0 if result["failed"] == 0 else 1)


def _cmd_reseal_secrets(args):
    """Rewrite credentials sealed with the master key under their organization's key.

    The column inventory lives in ``secrets_box.SEALED_COLUMNS`` so this command,
    the reseal revision and the tests cannot drift apart. ``--check`` reports the
    inventory without touching it and exits non-zero while anything is left, which
    is the gate a deploy can run before it starts refusing legacy tokens.
    """
    import os

    from hoja_server import secrets_box, tenant_context

    engine = make_engine(_database_target(args))
    if args.check:
        with tenant_context.system(), engine.connect() as conn:
            counts = secrets_box.count_legacy(conn)
        remaining = sum(counts.values())
        for name, count in sorted(counts.items()):
            print(f"{name}={count}")
        print(f"legacy={remaining}")
        raise SystemExit(0 if remaining == 0 else 1)
    allow_unopenable = os.environ.get("HOJA_RESEAL_SKIP_UNOPENABLE") == "1"
    failure = None
    report = None
    try:
        with tenant_context.system(), engine.begin() as conn:
            report = secrets_box.reseal_legacy(conn, dry_run=args.dry_run,
                                               allow_unopenable=allow_unopenable)
    except secrets_box.SecretBoxError as exc:  # nothing was written: the transaction rolled back
        failure = exc
    if failure is not None:
        print(f"reseal failed: {failure}", file=sys.stderr)
        raise SystemExit(1)
    for name, count in sorted(report.resealed.items()):
        print(f"{name}={count}")
    for table_name, row_id, error in report.unopenable:
        print(f"skipped {table_name} {row_id}: {error}", file=sys.stderr)
    print(f"{'would_reseal' if args.dry_run else 'resealed'}={report.total} "
          f"unopenable={len(report.unopenable)}")
    raise SystemExit(0 if not report.unopenable else 1)


def _cmd_merge_tenant(args):
    """Copy one dedicated tenant database into the shared database (dry run unless --apply)."""
    import os

    from hoja_server.tenant_merge import MergeError, merge

    source_secret = os.environ.get(args.source_secret_env, "")
    if not source_secret:
        raise SystemExit(f"set {args.source_secret_env} to the tenant's HOJA_SECRET_KEY")
    source_url = args.source_url or os.environ.get("HOJA_MERGE_SOURCE_URL")
    target_url = args.target_url or os.environ.get("HOJA_MERGE_TARGET_URL")
    if not source_url or not target_url:
        raise SystemExit("pass --source-url and --target-url, or set HOJA_MERGE_SOURCE_URL and HOJA_MERGE_TARGET_URL")
    try:
        report = merge(make_engine(source_url), make_engine(target_url), source_secret=source_secret,
                       vanity_host=args.vanity_host, apply=args.apply, replace_stale=args.replace_stale,
                       carry_over=args.carry_over)
    except MergeError as exc:
        raise SystemExit(f"merge refused: {exc}") from exc
    print("\n".join(report.lines()))


def _cmd_create_workspace(args):
    """Create a workspace in the shared database; an included model provider may arrive as JSON on stdin.

    Prints one JSON line. Re-running with the same arguments changes nothing and
    reports the stored state; ``repaired`` lists what an older provisioning run
    had left out and this run filled in. Refusals exit 2 with ``error``/``message``.
    """
    import json

    from hoja_server.workspace_provision import ProvisionError, provision

    provider = None
    if args.provider_stdin:
        provider = json.loads(sys.stdin.read())
    engine = make_engine(_database_target(args))
    try:
        result = provision(engine, slug=args.slug, owner_email=args.owner_email, name=args.name,
                           provider=provider, vanity_host=args.vanity_host)
    except ProvisionError as exc:
        print(json.dumps({"error": exc.reason, "message": exc.message}))
        print(f"create-workspace refused: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        engine.dispose()
    print(json.dumps(result, sort_keys=True))


def _cmd_tenant_doctor(args):
    """Read-only: can the owner get into the workspace on this host? Exit 1 on a blocking problem."""
    from hoja_server import tenant_doctor

    route_text = None
    if args.route_file == "-":
        route_text = sys.stdin.read()
    elif args.route_file:
        try:
            with open(args.route_file, encoding="utf-8") as handle:
                route_text = handle.read()
        except FileNotFoundError:
            route_text = ""
    engine = make_engine(_database_target(args))
    try:
        report = tenant_doctor.diagnose(engine, args.host, owner_email=args.owner_email,
                                        route_text=route_text, routes_dir=args.routes_dir)
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(report.lines()))
    raise SystemExit(0 if report.ok else 1)


def _cmd_connector_mcp(args):
    """Serve the connector MCP tools. The Hoja server signs a principal per turn and hands the
    agent this address as HOJA_CONNECTOR_MCP_URL; nothing else authenticates the caller."""
    import uvicorn

    from hoja_server.connector_mcp_service import build_connector_mcp_app

    target = _database_target(args)
    try:
        app = build_connector_mcp_app(make_engine(target), public_host=args.public_host)
    except Exception as exc:  # ConnectorConfigError today; any startup failure must be loud
        print(f"connector-mcp cannot start: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"  db  : {display_database_target(target)}")
    print(f"  url : http://{args.host}:{args.port}/mcp")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


def main(argv=None):
    p = argparse.ArgumentParser(prog="hoja-serve", description="hoja local backend")
    p.add_argument("--version", action="version", version=f"hoja-serve {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the GraphQL server")
    s.add_argument("--tenant", default="default")
    s.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    s.add_argument("--db", default=None, help="explicit DB path (overrides tenant)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--open", action="store_true", help="offline open mode: any token → first identity")
    s.add_argument("--log-level", default="warning")
    s.add_argument(
        "--security-log-level",
        default="info",
        help="level for the application's own hoja_server.* loggers (auth funnel "
             "events); independent of --log-level, which governs uvicorn and its "
             "access log",
    )
    s.add_argument("--workers", type=int, default=1,
                   help="server processes; realtime clients are spread across them")
    s.add_argument("--no-ws-deflate", dest="ws_deflate", action="store_false",
                   help="disable WebSocket compression (less CPU per client on large fan-outs)")
    s.set_defaults(func=_cmd_serve)

    mg = sub.add_parser("migrate", help="apply outstanding database schema revisions")
    mg.add_argument("--tenant", default="default")
    mg.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    mg.add_argument("--db", default=None)
    mg.set_defaults(func=_cmd_migrate)

    sd = sub.add_parser("seed", help="seed a tenant DB")
    sd.add_argument("--tenant", default="default")
    sd.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    sd.add_argument("--db", default=None)
    sd.add_argument("--org", default="Local")
    sd.add_argument("--org-key", default=None, help="unique organization URL key")
    sd.add_argument("--team-key", default="ENG")
    sd.add_argument("--team-name", default="Engineering")
    sd.add_argument("--user", default="Local User")
    sd.add_argument("--email", default="you@local")
    sd.add_argument("--token", default=None, help="use a specific token instead of random")
    sd.add_argument("--no-demo", action="store_true", help="don't create demo issues")
    sd.set_defaults(func=_cmd_seed)

    t = sub.add_parser("token", help="mint a new token for a tenant")
    t.add_argument("--tenant", default="default")
    t.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    t.add_argument("--db", default=None)
    t.add_argument("--organization", default=None, help="organization ID or URL key")
    t.add_argument("--user", default=None, help="user ID in the selected organization")
    t.add_argument("--email", default=None, help="user email in the selected organization")
    t.add_argument("--token", default=None)
    t.add_argument("--label", default="cli")
    t.set_defaults(func=_cmd_token)

    md = sub.add_parser("mail", help="deliver queued application email once")
    md.add_argument("--tenant", default="default")
    md.add_argument("--database-url", default=None)
    md.add_argument("--db", default=None)
    md.add_argument("--limit", type=int, default=25)
    md.set_defaults(func=_cmd_mail)

    rs = sub.add_parser("reseal-secrets",
                        help="re-encrypt stored credentials under per-organization keys")
    rs.add_argument("--tenant", default="default")
    rs.add_argument("--database-url", default=None)
    rs.add_argument("--db", default=None)
    rs.add_argument("--check", action="store_true",
                    help="report how many unbound ciphertexts remain; exit 1 if any do")
    rs.add_argument("--dry-run", action="store_true",
                    help="open and count what would be rewritten, without writing")
    rs.set_defaults(func=_cmd_reseal_secrets)

    rr = sub.add_parser(
        "rls-report",
        help="print the per-table row-level-security verdict /ready enforces (read-only)",
    )
    rr.add_argument("--tenant", default="default")
    rr.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    rr.add_argument("--db", default=None)
    rr.add_argument("--json", action="store_true", help="print the report as JSON")
    rr.set_defaults(func=_cmd_rls_report)

    mt = sub.add_parser("merge-tenant", help="copy a dedicated tenant database into the shared database")
    mt.add_argument("--source-url", default=None, help="tenant database, superuser (or HOJA_MERGE_SOURCE_URL)")
    mt.add_argument("--target-url", default=None, help="shared database, superuser (or HOJA_MERGE_TARGET_URL)")
    mt.add_argument("--source-secret-env", default="HOJA_SOURCE_SECRET_KEY",
                    help="environment variable holding the tenant's HOJA_SECRET_KEY")
    mt.add_argument("--vanity-host", default=None, help="keep serving the workspace on this host")
    mt.add_argument("--replace-stale", action="store_true",
                    help="replace an older copy of the same organization in the shared database")
    mt.add_argument("--carry-over", action="store_true",
                    help="replace an older copy but keep its rows the tenant lacks (issues renumbered)")
    mt.add_argument("--apply", action="store_true", help="commit; without it the merge is verified and rolled back")
    mt.set_defaults(func=_cmd_merge_tenant)

    cw = sub.add_parser("create-workspace", help="create a workspace in the shared database")
    cw.add_argument("--slug", required=True)
    cw.add_argument("--owner-email", required=True)
    cw.add_argument("--name", default=None, help="display name (ignored when the workspace exists)")
    cw.add_argument("--vanity-host", default=None,
                    help="serve the workspace on its own host, exactly <slug>.<HOJA_TENANT_BASE_DOMAIN>")
    cw.add_argument("--provider-stdin", action="store_true",
                    help="read the included model provider, with its key, as JSON from stdin")
    cw.add_argument("--tenant", default="default")
    cw.add_argument("--database-url", default=None)
    cw.add_argument("--db", default=None)
    cw.set_defaults(func=_cmd_create_workspace)

    td = sub.add_parser("tenant-doctor",
                        help="read-only check that a workspace host is provisioned and its owner can sign in")
    td.add_argument("host", help="the workspace host, e.g. acme.hoja.work")
    td.add_argument("--owner-email", default=None,
                    help="address to test admission for (default: the sign-up owner or oldest admin)")
    td.add_argument("--route-file", default=None,
                    help="the host's Traefik route file; '-' reads it from stdin")
    td.add_argument("--routes-dir", default=None,
                    help="Traefik dynamic directory (default $HOJA_TRAEFIK_DYNAMIC_DIR or "
                         "/opt/hoja/deploy/traefik/dynamic)")
    td.add_argument("--json", action="store_true", help="print the report as JSON")
    td.add_argument("--tenant", default="default")
    td.add_argument("--database-url", default=None)
    td.add_argument("--db", default=None)
    td.set_defaults(func=_cmd_tenant_doctor)


    cm = sub.add_parser(
        "connector-mcp",
        help="serve the connector MCP tools that HOJA_CONNECTOR_MCP_URL points at",
    )
    cm.add_argument("--tenant", default="default")
    cm.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    cm.add_argument("--db", default=None)
    cm.add_argument("--host", default="127.0.0.1")
    cm.add_argument("--port", type=int, default=8090)
    cm.add_argument("--log-level", default="warning")
    cm.add_argument(
        "--public-host",
        default=None,
        help="host name the proxy serves (enables DNS-rebinding protection for it)",
    )
    cm.set_defaults(func=_cmd_connector_mcp)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
