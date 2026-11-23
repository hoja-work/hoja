"""Data migration for recursive identifiers (HOJA-472).

Pure functions so the CLI and the tests exercise exactly the same code. See
scripts/migrate_recursive_identifiers.py for the command-line contract.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection, Engine

from hoja_server import identifiers, tenant_context
from hoja_server.db import issue, now_iso, team
from hoja_server.db import issue_identifier_alias as alias


def _children(conn: Connection, org_id: str, team_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(select(
        issue.c.id, issue.c.identifier, issue.c.number, issue.c.parent_id,
        issue.c.depth, issue.c.slot, issue.c.url,
    ).where(and_(
        issue.c.organization_id == org_id,
        issue.c.team_id == team_id,
    )).order_by(issue.c.created_at, issue.c.id)).mappings().all()
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["parent_id"]:
            by_parent.setdefault(row["parent_id"], []).append(dict(row))
    return by_parent


def _rewrite_url(url: str | None, old: str, new: str) -> str | None:
    if not url:
        return url
    suffix = f"/issue/{old}"
    return url[: -len(suffix)] + f"/issue/{new}" if url.endswith(suffix) else url


def forward(engine: Engine, apply: bool) -> dict[str, int]:
    with tenant_context.system():
        return _forward(engine, apply)


def _forward(engine: Engine, apply: bool) -> dict[str, int]:
    counts = {"teams": 0, "roots": 0, "renumbered": 0, "skipped": 0, "aliases": 0}
    with engine.begin() as conn:
        teams = conn.execute(select(team.c.id, team.c.organization_id).order_by(team.c.id)).all()
        for team_id, org_id in teams:
            counts["teams"] += 1
            root_rows = conn.execute(select(
                issue.c.id, issue.c.identifier, issue.c.number, issue.c.slot, issue.c.depth,
            ).where(and_(
                issue.c.organization_id == org_id,
                issue.c.team_id == team_id,
                issue.c.parent_id.is_(None),
            ))).mappings().all()
            node: dict[str, dict[str, Any]] = {}
            for row in root_rows:
                counts["roots"] += 1
                node[row["id"]] = {
                    "id": row["id"],
                    "identifier": row["identifier"],
                    "slot": row["slot"] or identifiers.ROOT_SLOT,
                    "depth": int(row["depth"] or 0),
                }
                if not apply:
                    continue
                conn.execute(issue.update().where(and_(
                    issue.c.organization_id == org_id,
                    issue.c.id == row["id"],
                )).values(slot=identifiers.ROOT_SLOT, depth=0))

            by_parent = _children(conn, org_id, team_id)
            # Breadth-first so a parent's new identifier is known before its
            # children are composed from it. Roots are the queue seed.
            queue = [row["id"] for row in root_rows]
            seen: set[str] = set()
            while queue:
                parent_id = queue.pop(0)
                if parent_id in seen:
                    raise SystemExit(f"cycle in parent chain at {parent_id}")
                seen.add(parent_id)
                parent = node[parent_id]
                kids = by_parent.get(parent_id, [])
                for index, child in enumerate(kids, 1):
                    queue.append(child["id"])
                    if child["depth"] is not None:
                        counts["skipped"] += 1
                        node[child["id"]] = {
                            "id": child["id"],
                            "identifier": child["identifier"],
                            "slot": child["slot"] or identifiers.slot_for(index, parent["depth"] + 1),
                            "depth": int(child["depth"]),
                        }
                        continue
                    depth = parent["depth"] + 1
                    if depth > identifiers.MAX_DEPTH:
                        raise SystemExit(
                            f"refusing to migrate {child['identifier']}: depth {depth} "
                            f"exceeds MAX_DEPTH {identifiers.MAX_DEPTH}")
                    new_ident = identifiers.child_identifier(
                        parent["identifier"], parent["slot"], index)
                    new_slot = identifiers.slot_for(index, depth)
                    new_number = identifiers.child_number(
                        (conn.execute(select(team.c.child_counter).where(
                            team.c.id == team_id)).scalar() or 0) + 1)
                    counts["renumbered"] += 1
                    node[child["id"]] = {
                        "id": child["id"], "identifier": new_ident,
                        "slot": new_slot, "depth": depth,
                    }
                    if not apply:
                        continue
                    conn.execute(team.update().where(team.c.id == team_id).values(
                        child_counter=func.coalesce(team.c.child_counter, 0) + 1))
                    conn.execute(alias.insert().values(
                        organization_id=org_id,
                        old_identifier=child["identifier"].upper(),
                        issue_id=child["id"],
                        old_number=child["number"],
                        new_identifier=new_ident,
                        created_at=now_iso(),
                    ))
                    counts["aliases"] += 1
                    conn.execute(issue.update().where(and_(
                        issue.c.organization_id == org_id,
                        issue.c.id == child["id"],
                    )).values(
                        identifier=new_ident, number=new_number,
                        slot=new_slot, depth=depth,
                        url=_rewrite_url(child["url"], child["identifier"], new_ident),
                    ))
    return counts


def backward(engine: Engine, apply: bool) -> dict[str, int]:
    with tenant_context.system():
        return _backward(engine, apply)


def _backward(engine: Engine, apply: bool) -> dict[str, int]:
    counts = {"restored": 0, "orphans": 0}
    with engine.begin() as conn:
        aliases = conn.execute(select(alias).order_by(alias.c.created_at)).mappings().all()
        migrated_ids = {row["issue_id"] for row in aliases}
        # A child created after the migration has no alias row, so the flat
        # scheme cannot name it. Refuse rather than invent an identifier.
        post_query = select(func.count()).select_from(issue).where(issue.c.depth > 0)
        if migrated_ids:
            post_query = post_query.where(~issue.c.id.in_(migrated_ids))
        post = conn.execute(post_query).scalar()
        counts["orphans"] += int(post or 0)
        if post:
            raise SystemExit(
                f"refusing to finish --back: {post} issues were created after the "
                "migration and have no retired flat identifier")
        for row in aliases:
            current = conn.execute(select(
                issue.c.identifier, issue.c.depth,
            ).where(issue.c.id == row["issue_id"])).mappings().first()
            if current and current["identifier"] != row["new_identifier"]:
                counts["orphans"] += 1
                continue
            counts["restored"] += 1
            if not apply:
                continue
            conn.execute(issue.update().where(issue.c.id == row["issue_id"]).values(
                identifier=row["old_identifier"],
                number=row["old_number"],
                slot=None,
                depth=None,
                url=_rewrite_url(current_url(conn, row["issue_id"]),
                                 row["new_identifier"], row["old_identifier"]),
            ))
            conn.execute(alias.delete().where(and_(
                alias.c.organization_id == row["organization_id"],
                alias.c.old_identifier == row["old_identifier"],
            )))
        if apply:
            conn.execute(team.update().values(child_counter=0))
    return counts


def current_url(conn: Connection, issue_id: str) -> str | None:
    value = conn.execute(select(issue.c.url).where(issue.c.id == issue_id)).scalar()
    return str(value) if value is not None else None


def verify(engine: Engine) -> list[str]:
    with tenant_context.system():
        return _verify(engine)


def _verify(engine: Engine) -> list[str]:
    """Prove the invariants the migration promises; return human-readable failures."""
    problems: list[str] = []
    with engine.connect() as conn:
        for team_id, org_id, key in conn.execute(
                select(team.c.id, team.c.organization_id, team.c.key)).all():
            rows = conn.execute(select(
                issue.c.id, issue.c.identifier, issue.c.number, issue.c.parent_id,
                issue.c.slot, issue.c.depth,
            ).where(and_(
                issue.c.organization_id == org_id,
                issue.c.team_id == team_id,
            )).order_by(issue.c.created_at, issue.c.id)).mappings().all()
            identifiers_seen: set[str] = set()
            numbers_seen: set[int] = set()
            by_id = {row["id"]: row for row in rows}
            children: dict[str, list[dict[str, Any]]] = {}
            roots: list[dict[str, Any]] = []
            for row in rows:
                if row["identifier"] in identifiers_seen:
                    problems.append(f"{key}: duplicate identifier {row['identifier']}")
                identifiers_seen.add(row["identifier"])
                if row["number"] is not None:
                    if row["number"] in numbers_seen:
                        problems.append(
                            f"{key}: duplicate UNIQUE(team_id, number) value {row['number']}")
                    numbers_seen.add(row["number"])
                if row["parent_id"]:
                    children.setdefault(row["parent_id"], []).append(dict(row))
                else:
                    roots.append(dict(row))
            for root in roots:
                if identifiers.is_child_number(root["number"]):
                    problems.append(
                        f"{root['identifier']}: top-level number is in the child range")
                if root["depth"] not in (None, 0):
                    problems.append(f"{root['identifier']}: root depth {root['depth']} is not 0")
            queue = list(roots)
            seen: set[str] = set()
            while queue:
                parent = queue.pop(0)
                if parent["id"] in seen:
                    problems.append(f"cycle at {parent['identifier']}")
                    continue
                seen.add(parent["id"])
                parent_slot = parent["slot"] or identifiers.ROOT_SLOT
                parent_depth = int(parent["depth"] or 0)
                for index, child in enumerate(children.get(parent["id"], []), 1):
                    queue.append(child)
                    expected = identifiers.child_identifier(
                        parent["identifier"], parent_slot, index)
                    if child["identifier"] != expected:
                        problems.append(
                            f"{child['identifier']}: expected {expected} under {parent['identifier']}")
                    if int(child["depth"] or 0) != parent_depth + 1:
                        problems.append(
                            f"{child['identifier']}: depth {child['depth']} should be {parent_depth + 1}")
                    if not identifiers.is_child_number(child["number"]):
                        problems.append(
                            f"{child['identifier']}: number {child['number']} is not in the child range")
    # Alias rows are org-wide while the structural pass above is per team, so
    # this runs once per organization with an org-wide identifier map.
    with engine.connect() as conn:
        for (org_id,) in conn.execute(select(team.c.organization_id).distinct()).all():
            by_id = {
                row["id"]: row["identifier"]
                for row in conn.execute(select(
                    issue.c.id, issue.c.identifier).where(
                    issue.c.organization_id == org_id)).mappings().all()
            }
            for old, issue_id, new in conn.execute(select(
                    alias.c.old_identifier, alias.c.issue_id, alias.c.new_identifier).where(
                    alias.c.organization_id == org_id)).all():
                if issue_id not in by_id:
                    problems.append(f"alias {old} points at a missing issue")
                elif by_id[issue_id] != new:
                    problems.append(
                        f"alias {old} records {new} but the issue is {by_id[issue_id]}")
                if old in set(by_id.values()):
                    problems.append(f"alias {old} is also a live identifier")
    return problems


def verify_flat(engine: Engine) -> list[str]:
    """Prove the restored (flat) state after --back: aliases gone, numbers unique."""
    problems: list[str] = []
    with tenant_context.system(), engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(alias)).scalar():
            problems.append("alias ledger is not empty after --back")
        for team_id, org_id, key in conn.execute(
                select(team.c.id, team.c.organization_id, team.c.key)).all():
            rows = conn.execute(select(
                issue.c.identifier, issue.c.number, issue.c.depth, issue.c.parent_id,
            ).where(and_(
                issue.c.organization_id == org_id,
                issue.c.team_id == team_id,
            ))).mappings().all()
            seen: set[int] = set()
            for row in rows:
                if row["depth"] not in (None, 0):
                    problems.append(f"{row['identifier']}: depth still set after --back")
                if row["number"] is not None:
                    if row["number"] in seen:
                        problems.append(f"{key}: duplicate number {row['number']}")
                    seen.add(row["number"])
    return problems
