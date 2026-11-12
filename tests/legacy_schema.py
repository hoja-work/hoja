"""Test-only: build a database as an earlier schema revision left it.

A fresh database is created from today's metadata, so it never exercises the
ALTER/backfill half of a revision. These helpers create the tables the way the
older release declared them and record the revision ledger up to that point,
which is what a deployment being upgraded actually looks like.
"""

from __future__ import annotations

from sqlalchemy import MetaData

from hoja_server.db import metadata, now_iso, schema_revision


def create_schema(engine, revision: int) -> None:
    """Create every table as ``revision`` declared it and record the ledger to it."""
    target = MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(target)
    if revision < 62:
        # Revision 62 makes this mandatory; before it the column was nullable.
        target.tables["api_key"].c.organization_id.nullable = True
    target.create_all(engine)
    with engine.begin() as conn:
        if revision < 62:
            # Revision 2 created this as raw SQL, so it is not in the metadata.
            conn.exec_driver_sql("CREATE UNIQUE INDEX uq_api_key_token_hash ON api_key (token_hash)")
        conn.execute(schema_revision.insert(),
                     [{"revision": number, "applied_at": now_iso()}
                      for number in range(1, revision + 1)])
