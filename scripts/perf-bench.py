#!/usr/bin/env python3
"""Deterministic local performance scorecard for hill-climbing Hoja speed (HOJA-754).

Seeds a reproducible workspace (projects, issues, comments), runs N warm
iterations against core API & GraphQL paths, parses Server-Timing headers
(app, db, dbq, pool), verifies timing closure (app >= db), and writes a JSON
scorecard that can be diffed mechanically across commits.

Usage:
  app/.venv/bin/python app/scripts/perf-bench.py --iterations 15 --out before.json
  app/.venv/bin/python app/scripts/perf-bench.py --compare before.json after.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

TOKEN = "perf-bench-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ST_APP = re.compile(r"app;dur=([0-9.]+)")
ST_DB = re.compile(r'db;dur=([0-9.]+);desc="q=(\d+)"')
ST_POOL = re.compile(r"pool;dur=([0-9.]+)")


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, math.ceil((p / 100.0) * len(s)) - 1))
    return s[idx]


def parse_server_timing(header: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if m := ST_APP.search(header):
        out["app_ms"] = float(m.group(1))
    if m := ST_DB.search(header):
        out["db_ms"] = float(m.group(1))
        out["db_queries"] = float(m.group(2))
    if m := ST_POOL.search(header):
        out["pool_ms"] = float(m.group(1))
    return out


def seed_dataset(client: TestClient, n_issues: int = 80) -> list[str]:
    team_res = client.post(
        "/graphql",
        headers=AUTH,
        json={"query": "{ teams { nodes { id } } }"},
    ).json()
    team_id = team_res["data"]["teams"]["nodes"][0]["id"]
    identifiers: list[str] = []
    for i in range(n_issues):
        res = client.post(
            "/graphql",
            headers=AUTH,
            json={
                "query": "mutation($t:String!,$title:String!){issueCreate(input:{teamId:$t,title:$title}){issue{identifier}}}",
                "variables": {"t": team_id, "title": f"Perf bench issue #{i + 1}"},
            },
        ).json()
        identifiers.append(res["data"]["issueCreate"]["issue"]["identifier"])
    return identifiers


def run_bench(iterations: int, n_issues: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hoja-perf-bench-") as tmp:
        db_path = Path(tmp) / "bench.db"
        engine = make_engine(db_path)
        migrate(engine)
        seed_tenant(
            engine,
            org_name="PerfBench",
            org_url_key="perf-bench",
            user_email="bench@example.test",
            token=TOKEN,
            demo_issues=False,
        )
        client = TestClient(create_app(str(db_path), open_mode=False))
        identifiers = seed_dataset(client, n_issues=n_issues)
        sample_id = identifiers[0]
        burst_ids = identifiers[:20]

        def call_graphql(query: str, variables: dict[str, Any] | None = None):
            t0 = time.perf_counter()
            resp = client.post("/graphql", headers=AUTH, json={"query": query, "variables": variables or {}})
            wall_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200, resp.text
            st = parse_server_timing(resp.headers.get("server-timing", ""))
            return wall_ms, st

        def call_get(path: str):
            t0 = time.perf_counter()
            resp = client.get(path, headers=AUTH)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200, resp.text
            st = parse_server_timing(resp.headers.get("server-timing", ""))
            return wall_ms, st

        def measure(name: str, fn) -> dict[str, Any]:
            # 2 warmup runs
            for _ in range(2):
                fn()
            walls, apps, dbs, qs, pools = [], [], [], [], []
            closure_ok = True
            for _ in range(iterations):
                w, st = fn()
                walls.append(w)
                app_ms = st.get("app_ms", w)
                db_ms = st.get("db_ms", 0.0)
                q = st.get("db_queries", 0.0)
                pool_ms = st.get("pool_ms", 0.0)
                apps.append(app_ms)
                dbs.append(db_ms)
                qs.append(q)
                pools.append(pool_ms)
                if app_ms + 1.5 < db_ms:
                    closure_ok = False
            return {
                "name": name,
                "iterations": iterations,
                "wall_p50_ms": round(pct(walls, 50), 2),
                "wall_p95_ms": round(pct(walls, 95), 2),
                "app_p50_ms": round(pct(apps, 50), 2),
                "app_p95_ms": round(pct(apps, 95), 2),
                "db_p50_ms": round(pct(dbs, 50), 2),
                "db_p95_ms": round(pct(dbs, 95), 2),
                "db_queries_p50": round(pct(qs, 50), 1),
                "pool_p95_ms": round(pct(pools, 95), 2),
                "closure_ok": closure_ok,
            }

        # Simulated 20-chip markdown render: unbatched (20 HTTP calls) vs batched (1 aliased call)
        def chip_unbatched():
            total_w = 0.0
            total_app = 0.0
            total_db = 0.0
            total_q = 0.0
            total_pool = 0.0
            for ident in burst_ids:
                w, st = call_graphql(
                    "query AgentIssueChip($id:String!){issue(id:$id){identifier title state{name type color}}}",
                    {"id": ident},
                )
                total_w += w
                total_app += st.get("app_ms", 0.0)
                total_db += st.get("db_ms", 0.0)
                total_q += st.get("db_queries", 0.0)
                total_pool += st.get("pool_ms", 0.0)
            return total_w, {"app_ms": total_app, "db_ms": total_db, "db_queries": total_q, "pool_ms": total_pool}

        def chip_batched():
            parts = [
                f"i{idx}: issue(id: $id{idx}) {{ identifier title state {{ name type color }} }}"
                for idx in range(len(burst_ids))
            ]
            var_defs = ", ".join(f"$id{idx}: String!" for idx in range(len(burst_ids)))
            variables = {f"id{idx}": ident for idx, ident in enumerate(burst_ids)}
            return call_graphql(f"query AgentIssueChipBatch({var_defs}) {{ {' '.join(parts)} }}", variables)

        cases = [
            measure("GET /sync/bootstrap", lambda: call_get("/sync/bootstrap")),
            measure("GraphQL IssueList (first: 50)", lambda: call_graphql("query IssueList { issues(first: 50) { nodes { id identifier title state { name type color } } } }")),
            measure("GraphQL IssueDetail", lambda: call_graphql("query IssueDetail($id:String!){issue(id:$id){id identifier title description state{name} comments{nodes{id body}}}}", {"id": sample_id})),
            measure("GraphQL Projects", lambda: call_graphql("query Projects { projects { nodes { id name state } } }")),
            measure("GraphQL InboxUnreadCount", lambda: call_graphql("query InboxUnread { inboxUnreadCount }")),
            measure("AgentIssueChip x20 (unbatched N+1)", chip_unbatched),
            measure("AgentIssueChip x20 (batched 1-req)", chip_batched),
        ]
        return {"n_issues": n_issues, "iterations": iterations, "cases": cases}


def print_scorecard(data: dict[str, Any]) -> None:
    print(f"Hoja Local Perf Scorecard (n_issues={data['n_issues']}, iterations={data['iterations']})")
    print(f"{'Case':<36} {'wall p50':>9} {'wall p95':>9} {'db p95':>8} {'db_q':>6} {'closure':>8}")
    for c in data["cases"]:
        print(
            f"{c['name']:<36} {c['wall_p50_ms']:8.2f}m {c['wall_p95_ms']:8.2f}m "
            f"{c['db_p95_ms']:7.2f}m {c['db_queries_p50']:6.1f} {c['closure_ok']!s:>8}"
        )


def compare(before_path: Path, after_path: Path) -> None:
    b = {c["name"]: c for c in json.loads(before_path.read_text())["cases"]}
    a = {c["name"]: c for c in json.loads(after_path.read_text())["cases"]}
    print(f"Scorecard Diff: {before_path.name} -> {after_path.name}")
    print(f"{'Case':<36} {'Before p95':>10} {'After p95':>10} {'Delta':>9} {'Before Q':>9} {'After Q':>8}")
    for name, bc in b.items():
        if name not in a:
            continue
        ac = a[name]
        delta = ((ac["wall_p95_ms"] - bc["wall_p95_ms"]) / bc["wall_p95_ms"] * 100.0) if bc["wall_p95_ms"] > 0 else 0.0
        print(
            f"{name:<36} {bc['wall_p95_ms']:9.2f}m {ac['wall_p95_ms']:9.2f}m {delta:+8.1f}% "
            f"{bc['db_queries_p50']:9.1f} {ac['db_queries_p50']:8.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument("--issues", type=int, default=80)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        compare(args.compare[0], args.compare[1])
        return 0
    res = run_bench(args.iterations, args.issues)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2))
    print_scorecard(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
