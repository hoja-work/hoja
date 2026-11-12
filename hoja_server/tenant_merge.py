"""Move one dedicated tenant database into the shared database (ADR-003, HOJA-240).

The source holds exactly one organization. Its rows are copied into the target
in foreign-key order inside one transaction, with:

- identities matched by email, so a person in several workspaces stays one identity;
- credentials re-sealed from the tenant's HOJA_SECRET_KEY to the shared key;
- change events renumbered above every cursor a client of either side may hold;
- triggers suppressed while copying (no change events or notifications for history)
  and foreign keys checked explicitly afterwards;
- a stale copy of the same organization in the target replaced only when nothing in
  it is newer than, or missing from, the source;
- row counts and per-table checksums compared before commit.

Dry run is the default: everything runs and is verified, then rolled back.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import Integer, MetaData, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine

from hoja_server import secrets_box, tenant_context
from hoja_server.db import CURRENT_SCHEMA_REVISION, applied_schema_revision, new_id

SKIPPED = frozenset({
    "alembic_version", "oauth_state_nonce", "rate_limit_event", "schema_revision", "signup_eligibility",
    "signup_flow", "signup_identity", "signup_invite", "signup_session", "tenant_membership_hint",
    "turnstile_use",
})
HANDLED = frozenset({"organization", "global_identity", "identity_credential", "oauth_client"})
SECRETS = {"connector_account": "encrypted_refresh_token", "extension": "secret_sealed",
           "model_provider": "encrypted_api_key", "webhook": "secret_sealed"}
IDENTITY_COLUMNS = {"organization_membership": "identity_id", "membership_invitation": "accepted_identity_id"}
SEQ_COLUMNS = {"change_event": "seq", "webhook_cursor": "seq", "webhook_delivery": "seq"}
CURSOR_HEADROOM = 1000
# Authentication state in a stale copy is not carried over: those sessions and grants end.
NOT_CARRIED = frozenset({"browser_session", "oauth_access_token", "oauth_authorization", "oauth_pending",
                         "oauth_refresh_token"})


class MergeError(RuntimeError):
    pass


@dataclass
class Report:
    org_id: str = ""
    url_key: str = ""
    applied: bool = False
    replaced_stale: bool = False
    tables: dict[str, int] = field(default_factory=dict)
    identities_matched: int = 0
    identities_created: int = 0
    credentials_kept: list[str] = field(default_factory=list)
    resealed: int = 0
    skipped_rows: dict[str, int] = field(default_factory=dict)
    first_seq: int = 0
    carried: dict[str, int] = field(default_factory=dict)
    opened_with_shared_key: int = 0
    credentials_updated: list[str] = field(default_factory=list)
    renumbered: dict[str, str] = field(default_factory=dict)
    dropped_stale: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [f"organization {self.url_key} ({self.org_id})",
               f"mode {'applied' if self.applied else 'dry run, rolled back'}",
               f"replaced stale copy {self.replaced_stale}",
               f"identities matched={self.identities_matched} created={self.identities_created}",
               f"credentials resealed={self.resealed} (opened with the shared key: {self.opened_with_shared_key})",
               f"change events renumbered from {self.first_seq}"]
        out += [f"kept the shared, newer password for identity {i}" for i in self.credentials_kept]
        out += [f"took the tenant's newer password for identity {i}" for i in self.credentials_updated]
        out += [f"skipped {name}={n}" for name, n in sorted(self.skipped_rows.items()) if n]
        out += [f"rows {name}={n}" for name, n in sorted(self.tables.items()) if n]
        out += [f"carried from the shared copy {name}={n}" for name, n in sorted(self.carried.items()) if n]
        out += [f"not carried {name}={n}" for name, n in sorted(self.dropped_stale.items()) if n]
        out += [f"renumbered {old} -> {new}" for old, new in sorted(self.renumbered.items())]
        return out


def _tables(meta: MetaData):
    return [t for t in meta.sorted_tables if t.name not in SKIPPED]


def _org_parent(table):
    """For a child table: (column, parent table) of a foreign key to an organization-scoped table."""
    for column in table.columns:
        for fk in column.foreign_keys:
            parent = fk.column.table
            if "organization_id" in parent.c and parent.name != "organization":
                return column, parent, fk.column
    return None


def _scope(table, org_id: str):
    """The predicate selecting ``table``'s rows that belong to ``org_id``, or None for global tables."""
    if table.name == "organization":
        return table.c.id == org_id
    if "organization_id" in table.c:
        return table.c.organization_id == org_id
    link = _org_parent(table)
    if link is None:
        return None
    column, parent, target = link
    return column.in_(select(target).where(parent.c.organization_id == org_id))


def _serial(table) -> str | None:
    pk = list(table.primary_key.columns)
    if len(pk) == 1 and isinstance(pk[0].type, Integer):
        return pk[0].name
    return None


def _rows(conn: Connection, table, where, columns):
    stmt = select(*[table.c[c] for c in columns])
    if where is not None:
        stmt = stmt.where(where)
    order = list(table.primary_key.columns) or [table.c[columns[0]]]
    return [dict(r) for r in conn.execute(stmt.order_by(*order)).mappings()]


def _digest(rows: list[dict], drop: set[str]) -> str:
    body = sorted(json.dumps({k: v for k, v in r.items() if k not in drop}, sort_keys=True, default=str)
                  for r in rows)
    return hashlib.sha256("\n".join(body).encode()).hexdigest()


def _check_columns(source: Engine, target: Engine, meta: MetaData) -> dict[str, list[str]]:
    """Columns to copy per table: every source column, each of which must exist in the target."""
    src, dst = inspect(source), inspect(target)
    present = set(src.get_table_names())
    columns = {}
    for table in _tables(meta):
        if table.name not in present:
            continue
        names = [c["name"] for c in src.get_columns(table.name)]
        unknown = set(names) - {c["name"] for c in dst.get_columns(table.name)}
        if unknown:
            raise MergeError(f"{table.name}: source columns missing from the shared database: {sorted(unknown)}")
        columns[table.name] = names
    return columns


def _stale_conflicts(src: Connection, dst: Connection, org_id: str, columns, meta) -> list[str]:
    problems = []
    for table in _tables(meta):
        if table.name not in columns or table.name in HANDLED or _scope(table, org_id) is None:
            continue
        pk = [c.name for c in table.primary_key.columns]
        if not pk or _serial(table):
            continue
        stamp = "updated_at" if "updated_at" in table.c else "created_at" if "created_at" in table.c else None
        wanted = pk + ([stamp] if stamp else [])
        theirs = {tuple(r[k] for k in pk): r.get(stamp) for r in _rows(src, table, _scope(table, org_id), wanted)}
        for row in _rows(dst, table, _scope(table, org_id), wanted):
            key = tuple(row[k] for k in pk)
            if key not in theirs:
                problems.append(f"{table.name} {key} exists only in the shared copy")
            elif stamp and row[stamp] and theirs[key] and str(row[stamp]) > str(theirs[key]):
                problems.append(f"{table.name} {key} is newer in the shared copy")
    return problems


def _shared_only(src: Connection, dst: Connection, org_id: str, columns, meta) -> dict[str, list[dict]]:
    """Rows of the organization that exist only in the shared copy, by primary key, per table."""
    found: dict[str, list[dict]] = {}
    for table in _tables(meta):
        if table.name in HANDLED or table.name not in columns or _scope(table, org_id) is None:
            continue
        pk = [c.name for c in table.primary_key.columns]
        if not pk or _serial(table):
            continue
        theirs = {tuple(r[k] for k in pk) for r in _rows(src, table, _scope(table, org_id), pk)}
        rows = [r for r in _rows(dst, table, _scope(table, org_id), list(table.c.keys()))
                if tuple(r[k] for k in pk) not in theirs]
        if rows:
            found[table.name] = rows
    return found


def _carry(dst: Connection, meta: MetaData, carried: dict[str, list[dict]], report: "Report") -> None:
    """Re-insert shared-only rows after the tenant's copy; issues take the next numbers of their team."""
    team, issue = meta.tables["team"], meta.tables["issue"]
    for table in _tables(meta):
        rows = carried.get(table.name)
        if not rows:
            continue
        if table.name in NOT_CARRIED:
            report.dropped_stale[table.name] = len(rows)
            continue
        if table is issue:
            rows = sorted(rows, key=lambda r: (r.get("team_id") or "", r.get("number") or 0))
        for row in rows:
            if table is issue and row.get("team_id"):
                counter = dst.execute(select(team.c.issue_counter, team.c.key).where(team.c.id == row["team_id"])
                                      .with_for_update()).first()
                number = max(counter[0] or 0, dst.execute(select(func.coalesce(func.max(issue.c.number), 0))
                                                          .where(issue.c.team_id == row["team_id"])).scalar_one()) + 1
                dst.execute(team.update().where(team.c.id == row["team_id"]).values(issue_counter=number))
                old = row.get("identifier")
                row = {**row, "number": number, "identifier": f"{counter[1]}-{number}"}
                report.renumbered[old] = row["identifier"]
            dst.execute(table.insert().values(**row))
        report.carried[table.name] = len(rows)


def _open(token: str, org_id: str, source_secret: str, target_secret: str | None, report: "Report") -> str:
    """Open with the tenant's key; a tenant cloned from the shared database may hold tokens sealed with its key."""
    try:
        # A tenant that never ran `reseal-secrets` still holds master-key ciphertexts; this
        # is the one place that must open them, and it re-seals every row as v2 on the way out.
        return secrets_box.decrypt_secret(token, org_id, secret=source_secret, allow_legacy=True)
    except secrets_box.SecretBoxError:
        plain = secrets_box.decrypt_secret(token, org_id, secret=target_secret, allow_legacy=True)
        report.opened_with_shared_key += 1
        return plain


def _delete_org(dst: Connection, org_id: str, meta: MetaData) -> None:
    organization = meta.tables["organization"]
    for table in reversed(_tables(meta)):
        if table.name in HANDLED - {"organization"}:
            continue
        where = _scope(table, org_id)
        if where is not None and table.name != "organization":
            dst.execute(table.delete().where(where))
    dst.execute(organization.delete().where(organization.c.id == org_id))


def _orphans(dst: Connection, org_id: str, meta: MetaData) -> list[str]:
    found = []
    for table in _tables(meta):
        where = _scope(table, org_id)
        if where is None:
            continue
        for column in table.columns:
            for fk in column.foreign_keys:
                parent = fk.column
                missing = dst.execute(
                    select(func.count()).select_from(table).where(
                        where, column.isnot(None),
                        ~select(parent).where(parent == column).exists())
                ).scalar_one()
                if missing:
                    found.append(f"{table.name}.{column.name}: {missing} rows without {parent.table.name}")
    return found


def merge(source: Engine, target: Engine, *, source_secret: str, target_secret: str | None = None,
          vanity_host: str | None = None, apply: bool = False, replace_stale: bool = False,
          carry_over: bool = False) -> Report:
    if target.dialect.name != "postgresql":
        raise MergeError("The shared database must be PostgreSQL.")
    with tenant_context.system():
        src_rev, dst_rev = applied_schema_revision(source), applied_schema_revision(target)
        if dst_rev != CURRENT_SCHEMA_REVISION or src_rev > dst_rev:
            raise MergeError(f"revisions: source {src_rev}, shared {dst_rev}, server {CURRENT_SCHEMA_REVISION}")
        meta = MetaData()
        meta.reflect(target)
        missing = HANDLED.union({"change_event"}) - set(meta.tables)
        if missing:
            raise MergeError(f"shared database lacks tables: {sorted(missing)}")
        organization, global_identity = meta.tables["organization"], meta.tables["global_identity"]
        identity_credential, oauth_client = meta.tables["identity_credential"], meta.tables["oauth_client"]
        change_event = meta.tables["change_event"]
        columns = _check_columns(source, target, meta)
        report = Report()
        snapshot = ({"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
                    if source.dialect.name == "postgresql" else {})
        with source.connect().execution_options(**snapshot) as src, target.connect() as dst:
            orgs = _rows(src, organization, None, columns["organization"])
            if len(orgs) != 1:
                raise MergeError(f"source must hold exactly one organization, found {len(orgs)}")
            org = orgs[0]
            org_id = report.org_id = org["id"]
            report.url_key = org["url_key"]
            tx = dst.begin()
            try:
                dst.exec_driver_sql("SET LOCAL session_replication_role = replica")
                dst.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('hoja.tenant-merge'))")
                existing = dst.execute(select(organization).where(organization.c.id == org_id)).mappings().first()
                clash = dst.execute(select(organization.c.id).where(
                    organization.c.url_key == org["url_key"], organization.c.id != org_id)).first()
                if clash:
                    raise MergeError(f"url_key {org['url_key']!r} belongs to another organization in the shared database")
                if vanity_host and dst.execute(select(organization.c.id).where(
                        organization.c.vanity_host == vanity_host, organization.c.id != org_id)).first():
                    raise MergeError(f"host {vanity_host!r} already belongs to another organization")
                carried: dict[str, list[dict]] = {}
                if existing:
                    if carry_over:
                        carried = _shared_only(src, dst, org_id, columns, meta)
                    else:
                        problems = _stale_conflicts(src, dst, org_id, columns, meta)
                        if problems:
                            raise MergeError("the shared copy has changes the tenant lacks:\n  "
                                             + "\n  ".join(problems[:20]))
                    if not (replace_stale or carry_over):
                        raise MergeError("the shared database holds an older copy of this organization; "
                                         "pass replace_stale after review")
                    _delete_org(dst, org_id, meta)
                    report.replaced_stale = True
                dst.execute(organization.insert().values(
                    **{k: v for k, v in org.items() if k != "vanity_host"}, vanity_host=vanity_host))

                identity_map: dict[str, str] = {}
                for row in _rows(src, global_identity, None, columns["global_identity"]):
                    match = dst.execute(select(global_identity.c.id).where(
                        func.lower(global_identity.c.email) == row["email"].lower())).scalar()
                    if match:
                        identity_map[row["id"]] = match
                        report.identities_matched += 1
                        continue
                    taken = dst.execute(select(global_identity.c.id).where(global_identity.c.id == row["id"])).first()
                    identity_map[row["id"]] = new_id() if taken else row["id"]
                    dst.execute(global_identity.insert().values(**{**row, "id": identity_map[row["id"]]}))
                    report.identities_created += 1
                for row in _rows(src, identity_credential, None, columns.get("identity_credential", [])) \
                        if "identity_credential" in columns else []:
                    mapped = identity_map.get(row["identity_id"], row["identity_id"])
                    held = dst.execute(select(identity_credential.c.updated_at).where(
                        identity_credential.c.identity_id == mapped)).first()
                    if held:
                        if str(row["updated_at"]) > str(held[0]):
                            dst.execute(identity_credential.update().where(
                                identity_credential.c.identity_id == mapped).values(
                                password_hash=row["password_hash"], updated_at=row["updated_at"]))
                            report.credentials_updated.append(mapped)
                        else:
                            report.credentials_kept.append(mapped)
                        continue
                    dst.execute(identity_credential.insert().values(**{**row, "identity_id": mapped}))
                if "oauth_client" in columns:
                    for row in _rows(src, oauth_client, None, columns["oauth_client"]):
                        if not dst.execute(select(oauth_client.c.client_id).where(
                                oauth_client.c.client_id == row["client_id"])).first():
                            dst.execute(oauth_client.insert().values(**row))

                src_head = src.execute(select(func.coalesce(func.max(change_event.c.seq), 0))).scalar_one()
                dst_head = dst.execute(select(func.coalesce(func.max(change_event.c.seq), 0))).scalar_one()
                report.first_seq = max(src_head, dst_head) + CURSOR_HEADROOM
                dst.execute(text("SELECT setval(pg_get_serial_sequence('change_event', 'seq'), :v, false)"),
                            {"v": report.first_seq})
                seq_map: dict[int, int] = {}

                for table in _tables(meta):
                    if table.name in HANDLED or table.name not in columns:
                        continue
                    rows = _rows(src, table, _scope(table, org_id), columns[table.name])
                    report.tables[table.name] = len(rows)
                    if not rows:
                        continue
                    serial = _serial(table)
                    for row in rows:
                        if table.name in IDENTITY_COLUMNS and row.get(IDENTITY_COLUMNS[table.name]):
                            col = IDENTITY_COLUMNS[table.name]
                            row[col] = identity_map.get(row[col], row[col])
                        if table.name in SECRETS and row.get(SECRETS[table.name]):
                            col = SECRETS[table.name]
                            plain = _open(row[col], row["organization_id"], source_secret, target_secret, report)
                            row[col] = secrets_box.encrypt_secret(plain, row["organization_id"], secret=target_secret)
                            report.resealed += 1
                        if table.name in ("webhook_cursor", "webhook_delivery") and row.get("seq") is not None:
                            row["seq"] = seq_map.get(row["seq"], report.first_seq)
                    if table.name == "change_event":
                        for row in rows:
                            old = row.pop("seq")
                            seq_map[old] = dst.execute(change_event.insert().values(**row)
                                                       .returning(change_event.c.seq)).scalar_one()
                    elif serial:
                        dst.execute(table.insert(), [{k: v for k, v in r.items() if k != serial} for r in rows])
                    else:
                        dst.execute(table.insert(), rows)

                _carry(dst, meta, carried, report)
                orphans = _orphans(dst, org_id, meta)
                if orphans:
                    raise MergeError("foreign keys broken after copy:\n  " + "\n  ".join(orphans))
                for table in _tables(meta):
                    if table.name in HANDLED or table.name not in columns:
                        continue
                    drop = {SECRETS.get(table.name), IDENTITY_COLUMNS.get(table.name),
                            SEQ_COLUMNS.get(table.name), _serial(table)} - {None}
                    if table.name == "team" and report.renumbered:
                        drop.add("issue_counter")
                    want = _rows(src, table, _scope(table, org_id), columns[table.name])
                    got = _rows(dst, table, _scope(table, org_id), columns[table.name])
                    pk = [c.name for c in table.primary_key.columns]
                    if table.name in report.carried and pk:
                        extra = {tuple(r[k] for k in pk) for r in carried[table.name]}
                        got = [r for r in got if tuple(r[k] for k in pk) not in extra]
                    if len(want) != len(got) or _digest(want, drop) != _digest(got, drop):
                        raise MergeError(f"{table.name}: copy differs (source {len(want)} rows, shared {len(got)})")
                for name in sorted(SKIPPED & set(inspect(source).get_table_names())):
                    table = meta.tables[name]
                    report.skipped_rows[name] = src.execute(select(func.count()).select_from(table)).scalar_one()
                if apply:
                    tx.commit()
                    report.applied = True
                else:
                    tx.rollback()
            except BaseException:
                if tx.is_active:
                    tx.rollback()
                raise
    return report
