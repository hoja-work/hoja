"""`cliniar-serve` entry point.

Subcommands:
  serve   Start the GraphQL backend.
  seed    Create an organization, user, team, states, and token.
  token   Mint a token for an existing organization user.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.exc import IntegrityError

from cliniar_server import __version__
from cliniar_server.db import (
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

    from cliniar_server.app import create_app
    return create_app(database_url=os.environ["CLINIAR_SERVE_TARGET"],
                      open_mode=os.environ.get("CLINIAR_SERVE_OPEN") == "1")


def _cmd_serve(args):
    import os

    import uvicorn

    from cliniar_server.app import create_app
    target = _database_target(args)
    print(f"cliniar-serve v{__version__}")
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
        os.environ["CLINIAR_SERVE_TARGET"] = target
        os.environ["CLINIAR_SERVE_OPEN"] = "1" if args.open else "0"
        uvicorn.run("cliniar_server.cli:_worker_app", factory=True, workers=args.workers, **options)
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
    print("Use it with cliniar:")
    print(f"  export LINEAR_TOKEN='{info['token']}'")
    print(f"  cliniar --token '{info['token']}' me   # (after wiring base_url)")


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


def main(argv=None):
    p = argparse.ArgumentParser(prog="cliniar-serve", description="cliniar local backend")
    p.add_argument("--version", action="version", version=f"cliniar-serve {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the GraphQL server")
    s.add_argument("--tenant", default="default")
    s.add_argument("--database-url", default=None, help="explicit SQLAlchemy database URL")
    s.add_argument("--db", default=None, help="explicit DB path (overrides tenant)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--open", action="store_true", help="offline open mode: any token → first identity")
    s.add_argument("--log-level", default="warning")
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

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
