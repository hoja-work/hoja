"""`hoja-serve` entry point.

Subcommands:
  serve   Start the GraphQL backend.
  seed    Create an organization, user, team, states, and token.
  token   Mint a token for an existing organization user.
"""
from __future__ import annotations

import argparse
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
        migrate(engine)
    except (RuntimeError, IntegrityError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        engine.dispose()
    print(f"Migrated database at {display_database_target(target)}")


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
    """Rewrite credentials sealed with the master key under their organization's key."""
    from sqlalchemy import select, update

    from hoja_server import secrets_box, tenant_context
    from hoja_server.db import connector_account, extension, model_provider, webhook

    engine = make_engine(_database_target(args))
    columns = ((connector_account, "encrypted_refresh_token"), (extension, "secret_sealed"),
               (model_provider, "encrypted_api_key"), (webhook, "secret_sealed"))
    total = 0
    with tenant_context.system(), engine.begin() as conn:
        for table, name in columns:
            column = table.c[name]
            rows = conn.execute(select(table.c.id, table.c.organization_id, column)).all()
            for row_id, org_id, token in rows:
                if not secrets_box.is_legacy(token):
                    continue
                plain = secrets_box.decrypt_secret(token, org_id)
                conn.execute(update(table).where(table.c.id == row_id)
                             .values({name: secrets_box.encrypt_secret(plain, org_id)}))
                total += 1
    print(f"resealed={total}")


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
    """Create a workspace in the shared database; an included model provider may arrive as JSON on stdin."""
    import json

    from hoja_server.workspace_provision import ProvisionError, provision

    provider = None
    if args.provider_stdin:
        provider = json.loads(sys.stdin.read())
    engine = make_engine(_database_target(args))
    try:
        result = provision(engine, slug=args.slug, owner_email=args.owner_email, name=args.name, provider=provider)
    except ProvisionError as exc:
        print(json.dumps({"error": exc.reason}))
        raise SystemExit(2) from exc
    finally:
        engine.dispose()
    print(json.dumps(result, sort_keys=True))


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
    rs.set_defaults(func=_cmd_reseal_secrets)

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
    cw.add_argument("--name", default=None)
    cw.add_argument("--provider-stdin", action="store_true",
                    help="read the included model provider, with its key, as JSON from stdin")
    cw.add_argument("--tenant", default="default")
    cw.add_argument("--database-url", default=None)
    cw.add_argument("--db", default=None)
    cw.set_defaults(func=_cmd_create_workspace)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
