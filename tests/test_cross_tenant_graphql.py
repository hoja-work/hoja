"""Every GraphQL operation, run with one organization's credential against another's ids.

Organization B seeds its own objects through the API. Organization A then calls
every query and every mutation in the executable schema, filling id arguments
and id input fields with B's ids. The test fails if any response carries a B id
that A did not send, if any database row that references B changes, or if an
operation in the schema was not exercised. On SQLite it checks the application
layer alone; under HOJA_TEST_RLS it runs as the restricted role with policies.
"""
from __future__ import annotations

import json

import pytest
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLInterfaceType,
    GraphQLUnionType,
    get_named_type,
    is_leaf_type,
    is_list_type,
    is_non_null_type,
)
from sqlalchemy import select
from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import metadata, seed_tenant
from hoja_server.resolvers import build_schema
from tests import rls_mode

NAME_TABLE = {
    "issue": "issue", "parent": "issue", "team": "team", "project": "project", "cycle": "cycle",
    "state": "workflow_state", "label": "issue_label", "initiative": "initiative", "comment": "comment",
    "view": "saved_view", "webhook": "webhook", "extension": "extension", "user": "user",
    "assignee": "user", "lead": "user", "member": "user", "creator": "user", "subscriber": "user",
    "attachment": "attachment", "provider": "model_provider", "conversation": "agent_conversation",
    "notification": "notification", "decision": "issue_decision", "organization": "organization",
}
LOG_TABLES = {"rate_limit_event", "security_denial", "denial"}
CANARY = "BRAVOCANARY"
TEXT_COLUMNS = ("title", "name", "description", "body", "content", "display_name", "external_account")


def _table_for(name: str) -> str | None:
    base = name[:-3] if name.endswith("Ids") else name[:-2] if name.endswith("Id") else name
    base = base.lower()
    for key, table in NAME_TABLE.items():
        if base.endswith(key):
            return table
    return None


def _is_id(name: str) -> bool:
    return name == "id" or name.endswith("Id") or name.endswith("Ids")


class Literal:
    def __init__(self, ids: dict[str, list[str]], fallback: list[str]):
        self.ids, self.fallback, self.sent = ids, fallback, set()

    def pick(self, name: str, override: str | None = None) -> str:
        if override and name == "id":
            value = override
        else:
            pool = self.ids.get(_table_for(name) or "", []) or self.fallback
            value = pool[0] if pool else "00000000-0000-0000-0000-000000000000"
        self.sent.add(value)
        return value

    def render(self, t, name: str, override: str | None = None, depth: int = 0) -> str | None:
        required = is_non_null_type(t)
        inner = t.of_type if required else t
        if is_list_type(inner):
            item = self.render(inner.of_type, name, override, depth)
            return "[]" if item is None else f"[{item}]"
        named = get_named_type(inner)
        if _is_id(name) and named.name in ("String", "ID"):
            return json.dumps(self.pick(name, override))
        if isinstance(named, GraphQLEnumType):
            return next(iter(named.values))
        if isinstance(named, GraphQLInputObjectType):
            if depth > 3:
                return None if not required else "{}"
            parts = []
            for field_name, field in named.fields.items():
                if is_non_null_type(field.type) or _is_id(field_name):
                    value = self.render(field.type, field_name, None, depth + 1)
                    if value is not None:
                        parts.append(f"{field_name}: {value}")
            return "{" + ", ".join(parts) + "}"
        if named.name == "Boolean":
            return "true"
        if named.name in ("Int", "Float"):
            return "1"
        if "url" in name.lower():
            return json.dumps("https://127.0.0.1/hoja-cross-tenant")
        if "email" in name.lower():
            return json.dumps("cross@tenant.test")
        if named.name in ("DateTime", "TimelessDate", "Date"):
            return json.dumps("2026-01-01T00:00:00Z")
        if named.name == "JSON" or named.name == "JSONObject":
            return "{}"
        return json.dumps("cross-tenant")


def _selection(t, depth: int) -> str:
    named = get_named_type(t)
    if is_leaf_type(named):
        return ""
    if isinstance(named, (GraphQLUnionType, GraphQLInterfaceType)):
        return "{ __typename }"
    parts = []
    for name, field in named.fields.items():
        if any(is_non_null_type(a.type) for a in field.args.values()):
            continue
        sub_type = get_named_type(field.type)
        if is_leaf_type(sub_type):
            parts.append(name)
        elif depth > 0:
            sub = _selection(field.type, depth - 1)
            if sub:
                parts.append(f"{name} {sub}")
    return "{ " + " ".join(parts or ["__typename"]) + " }"


def _operation(kind: str, name: str, field, lit: Literal, override: str | None) -> str:
    args = []
    for arg_name, arg in field.args.items():
        if is_non_null_type(arg.type) or _is_id(arg_name):
            value = lit.render(arg.type, arg_name, override)
            if value is not None:
                args.append(f"{arg_name}: {value}")
    call = f"{name}({', '.join(args)})" if args else name
    return f"{kind} {{ {call} {_selection(field.type, 3)} }}"


def _org_rows(engine, org_id: str, ids: set[str]) -> dict[str, set[str]]:
    """Every row whose id, organization or foreign keys point at the organization, serialized."""
    found: dict[str, set[str]] = {}
    with engine.connect() as conn:
        for table in metadata.sorted_tables:
            if table.name in LOG_TABLES:
                continue
            keys = [c.name for c in table.c if c.name in ("id", "organization_id") or c.foreign_keys]
            for row in conn.execute(select(table)).mappings():
                values = {str(row[k]) for k in keys if row[k] is not None}
                if org_id in values or values & ids:
                    found.setdefault(table.name, set()).add(json.dumps(dict(row), sort_keys=True, default=str))
    return found


def _ids_of(engine, org_id: str) -> tuple[dict[str, list[str]], set[str]]:
    by_table: dict[str, list[str]] = {}
    with engine.connect() as conn:
        for table in metadata.sorted_tables:
            if "id" not in table.c or table.name in LOG_TABLES:
                continue
            if "organization_id" in table.c:
                rows = conn.execute(select(table.c.id).where(table.c.organization_id == org_id)).scalars()
            elif table.name == "organization":
                rows = [org_id]
            else:
                continue
            values = [str(v) for v in rows]
            if values:
                by_table[table.name] = values
    if "user" not in by_table:
        with engine.connect() as conn:
            from hoja_server.db import user
            by_table["user"] = [str(v) for v in conn.execute(select(user.c.id).where(
                user.c.organization_id == org_id)).scalars()] if "organization_id" in user.c else []
    return by_table, {v for values in by_table.values() for v in values}


def _stamp(engine, org_id: str) -> None:
    """Mark organization B's readable text so any copy of it in A's responses is visible."""
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if "organization_id" not in table.c:
                continue
            for name in TEXT_COLUMNS:
                if name in table.c:
                    col = table.c[name]
                    conn.execute(table.update().where(table.c.organization_id == org_id, col.isnot(None))
                                 .values({name: col + " " + CANARY}))


def _post(client: TestClient, token: str, query: str) -> dict:
    res = client.post("/graphql", json={"query": query}, headers={"Authorization": f"Bearer {token}"})
    return res.json() if res.headers.get("content-type", "").startswith("application/json") else {}


@pytest.fixture
def two_orgs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "cross-tenant-master")
    engine, kwargs = rls_mode.workspace(tmp_path, "cross")
    a = seed_tenant(engine, org_name="AlphaOrg", org_url_key="alpha", user_email="o@alpha.test",
                    token="alpha-tok", demo_issues=True)
    b = seed_tenant(engine, org_name="BravoSecretOrg", org_url_key="bravo", team_key="BRV",
                    user_email="o@bravo-secret.test", token="bravo-tok", demo_issues=True)
    app = create_app(kwargs.get("db_path"), database_url=kwargs.get("database_url"), open_mode=False)
    return engine, TestClient(app), a, b


def test_no_operation_reads_or_writes_another_organization(two_orgs):
    engine, client, a, b = two_orgs
    schema = build_schema()
    ops = [("query", n, f) for n, f in schema.query_type.fields.items()]
    ops += [("mutation", n, f) for n, f in schema.mutation_type.fields.items()]

    b_ids, _ = _ids_of(engine, b["org_id"])
    for kind, name, field in ops:
        if kind == "mutation" and not any(_is_id(n) and n == "id" for n in field.args):
            _post(client, b["token"], _operation(kind, name, field, Literal(b_ids, []), None))

    _stamp(engine, b["org_id"])
    b_ids, b_all = _ids_of(engine, b["org_id"])
    assert len(b_ids) >= 5, f"seeding created too few organization B objects: {sorted(b_ids)}"
    one_per_table = [values[0] for values in b_ids.values()]
    baseline = _org_rows(engine, b["org_id"], b_all)

    executed, leaks, writes = set(), [], []
    for kind, name, field in ops:
        targets = one_per_table if "id" in field.args else [None]
        for target in targets:
            lit = Literal(b_ids, one_per_table)
            body = _post(client, a["token"], _operation(kind, name, field, lit, target))
            if body:
                executed.add(name)
            text = json.dumps(body)
            for sent in lit.sent:
                text = text.replace(sent, "")
            hit = sorted(v for v in b_all | {CANARY, "BravoSecretOrg", "o@bravo-secret.test", "BRV-"} if v in text)
            if hit:
                leaks.append((name, target, hit[:3]))
        if kind == "mutation":
            now = _org_rows(engine, b["org_id"], b_all)
            if now != baseline:
                changed = sorted(t for t in set(now) | set(baseline) if now.get(t) != baseline.get(t))
                writes.append((name, changed))
                baseline = now

    assert not leaks, f"responses to organization A carried organization B data: {leaks}"
    assert not writes, f"organization A's calls changed rows that reference organization B: {writes}"
    assert executed == {name for _, name, _ in ops}, f"not exercised: {sorted({n for _, n, _ in ops} - executed)}"
