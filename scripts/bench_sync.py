#!/usr/bin/env python3
"""Benchmark and stress-test browser sync and realtime delivery.

Seeds a throwaway workspace with N issues, starts cliniar-serve on a free
local port, and measures:

  1. legacy load   — the old Dashboard query, plus paging every issue via GraphQL
  2. bootstrap     — /sync/bootstrap cold (raw + gzip bytes) and warm (ETag 304)
  3. delta         — /sync/delta after one mutation
  4. latency       — mutation request start → WebSocket frame received (p50/p95/p99)
  5. stress        — C concurrent WS clients fan-out latency + concurrent bootstraps

Usage:
  uv run python scripts/bench_sync.py --issues 5000 --clients 200
  uv run python scripts/bench_sync.py --database-url postgresql://... --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import random
import socket
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
import websockets
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cliniar_server.app import create_app
from cliniar_server.db import (
    PRIORITY_LABELS,
    issue,
    issue_label,
    issue_label_link,
    make_engine,
    migrate,
    new_id,
    seed_tenant,
    team,
    workflow_state,
)

TOKEN = "bench-token-" + "x" * 20
LEGACY_ISSUE_FIELDS = ("{ id identifier title description createdAt updatedAt stateChangedAt dueDate priority "
                       "priorityLabel state { id name color type } team { id key name } project{id name} "
                       "assignee { id name } creator { id name } subscribers { nodes { id } } "
                       "cycle { id name number } labels { nodes { id name color } } }")
LEGACY_DASHBOARD = ("query Dashboard { viewer { id name email admin } teams { nodes { id name key description "
                    "states { nodes { id name color type } } members { nodes { id name email } } "
                    "activeCycle { id name number startsAt endsAt progress } } } "
                    f"issues(first:100, orderBy:createdAt) {{ nodes {LEGACY_ISSUE_FIELDS} "
                    "pageInfo { hasNextPage endCursor } } }")
LEGACY_PAGE = ("query MoreIssues($after:String){issues(first:100,after:$after,orderBy:createdAt)"
               f"{{nodes {LEGACY_ISSUE_FIELDS} pageInfo{{hasNextPage endCursor}}}}}}")
UPDATE = "mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}"
LOREM = ("Investigate the regression reported by customers after the last deploy. Steps to reproduce are in "
         "the linked thread; logs attached. Acceptance: no errors in the dashboard for 24h. ")


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def seed(engine, n_issues: int, open_ratio: float) -> list[str]:
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Bench", org_url_key=f"bench-{new_id()[:6]}",
                         user_email="bench@bench.test", token=TOKEN, demo_issues=False)
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        team_row = conn.execute(select(team.c.id, team.c.key, team.c.organization_id)).first()
        states = conn.execute(select(workflow_state.c.id, workflow_state.c.type)
                              .where(workflow_state.c.team_id == team_row.id)).all()
        open_states = [s.id for s in states if s.type not in ("completed", "canceled")]
        closed_states = [s.id for s in states if s.type in ("completed", "canceled")]
        label_ids = []
        for name in ("bug", "feature", "chore", "perf", "security"):
            lid = new_id()
            label_ids.append(lid)
            conn.execute(issue_label.insert().values(id=lid, organization_id=team_row.organization_id,
                                                     name=name, color="#888888"))
        rows, links, ids = [], [], []
        for n in range(1, n_issues + 1):
            is_open = rng.random() < open_ratio
            age = timedelta(days=rng.uniform(0, 30 if is_open else 365))
            ts = (now - age).isoformat()
            prio = rng.randint(0, 4)
            iid = new_id()
            ids.append(iid)
            rows.append(dict(
                id=iid, organization_id=team_row.organization_id, team_id=team_row.id, number=n,
                identifier=f"{team_row.key}-{n}", title=f"Benchmark issue {n}: fix flaky behavior in module {n % 97}",
                description=LOREM * rng.randint(1, 4), priority=prio, priority_label=PRIORITY_LABELS[prio],
                state_id=rng.choice(open_states if is_open else closed_states),
                creator_id=seeded["user_id"], assignee_id=seeded["user_id"],
                created_at=ts, updated_at=ts, state_changed_at=ts,
            ))
            for lid in rng.sample(label_ids, rng.randint(0, 2)):
                links.append(dict(issue_id=iid, label_id=lid))
        for start in range(0, len(rows), 500):
            conn.execute(issue.insert(), rows[start:start + 500])
        for start in range(0, len(links), 500):
            conn.execute(issue_label_link.insert(), links[start:start + 500])
        conn.execute(team.update().where(team.c.id == team_row.id).values(issue_counter=n_issues))
    return ids


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return server


async def timed(coro):
    t0 = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - t0) * 1000


def gz_len(content: bytes) -> int:
    return len(gzip.compress(content, 6))


async def bench_legacy(client: httpx.AsyncClient) -> dict:
    res, ms = await timed(client.post("/graphql", json={"query": LEGACY_DASHBOARD}))
    first_raw = len(res.content)
    total_bytes, total_ms, pages = first_raw, ms, 1
    cursor = res.json()["data"]["issues"]["pageInfo"]["endCursor"]
    has_next = res.json()["data"]["issues"]["pageInfo"]["hasNextPage"]
    count = len(res.json()["data"]["issues"]["nodes"])
    while has_next:
        page, ms = await timed(client.post("/graphql", json={"query": LEGACY_PAGE, "variables": {"after": cursor}}))
        data = page.json()["data"]["issues"]
        total_bytes += len(page.content)
        total_ms += ms
        pages += 1
        count += len(data["nodes"])
        cursor, has_next = data["pageInfo"]["endCursor"], data["pageInfo"]["hasNextPage"]
    return {"dashboard_first_page_bytes": first_raw, "dashboard_first_page_gzip": gz_len(res.content),
            "all_issues_requests": pages, "all_issues_count": count,
            "all_issues_bytes": total_bytes, "all_issues_ms": round(total_ms, 1)}


async def bench_bootstrap(client: httpx.AsyncClient) -> dict:
    res, cold_ms = await timed(client.get("/sync/bootstrap", headers={"Accept-Encoding": "identity"}))
    raw = res.content
    gz, gz_ms = await timed(client.get("/sync/bootstrap", headers={"Accept-Encoding": "gzip"}))
    wire_gzip = int(gz.headers.get("content-length") or gz_len(raw))
    etag = res.headers["etag"]
    warm_times = []
    for _ in range(20):
        warm, ms = await timed(client.get("/sync/bootstrap", headers={"If-None-Match": etag}))
        assert warm.status_code == 304, warm.status_code
        warm_times.append(ms)
    body = res.json()
    return {"issues": len(body["issues"]), "seq": body["seq"], "raw_bytes": len(raw), "gzip_bytes": wire_gzip,
            "cold_ms": round(cold_ms, 1), "cold_gzip_ms": round(gz_ms, 1),
            "revalidate_304_p50_ms": round(statistics.median(warm_times), 2)}


async def bench_delta(client: httpx.AsyncClient, seq: int, issue_id: str) -> dict:
    await client.post("/graphql", json={"query": UPDATE, "variables": {"id": issue_id, "i": {"priority": 2}}})
    res, ms = await timed(client.get(f"/sync/delta?after={seq}"))
    return {"bytes": len(res.content), "ms": round(ms, 2), "issues": len(res.json()["issues"])}


async def ws_client(url: str, events: dict, ready: asyncio.Event | None, stop: asyncio.Event):
    async with websockets.connect(url, subprotocols=["bearer", TOKEN], max_size=None) as ws:
        if ready:
            ready.set()
        while not stop.is_set():
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            arrived = time.perf_counter()
            parsed = json.loads(frame)
            for ev in parsed if isinstance(parsed, list) else [parsed]:
                if ev.get("entity") == "issue":
                    events.setdefault(ev["entityId"], []).append(arrived)


async def latest_seq(client: httpx.AsyncClient) -> int:
    res = await client.get("/sync/bootstrap")
    # Pre-sync servers: start from 0; the server clamps to a recent replay window.
    return res.json()["seq"] if res.status_code == 200 else 0


async def bench_latency(client, ws_url, ids, n_mutations: int, n_clients: int) -> dict:
    """Mutation start → frame received on every connected client."""
    seq = await latest_seq(client)
    url = f"{ws_url}?after={seq}"
    stop = asyncio.Event()
    per_client: list[dict] = [{} for _ in range(n_clients)]
    readies = [asyncio.Event() for _ in range(n_clients)]
    tasks = [asyncio.create_task(ws_client(url, per_client[i], readies[i], stop)) for i in range(n_clients)]
    await asyncio.wait_for(asyncio.gather(*(r.wait() for r in readies)), timeout=60)
    await asyncio.sleep(0.3)
    started: dict[str, float] = {}
    targets = random.Random(3).sample(ids, n_mutations)
    for n, iid in enumerate(targets):
        started[iid] = time.perf_counter()
        await client.post("/graphql", json={"query": UPDATE, "variables": {"id": iid, "i": {"priority": n % 5}}})
        await asyncio.sleep(0.02)
    await asyncio.sleep(1.0)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    latencies, missing = [], 0
    for events in per_client:
        for iid, t0 in started.items():
            arrivals = [t for t in events.get(iid, []) if t >= t0]
            if arrivals:
                latencies.append((arrivals[0] - t0) * 1000)
            else:
                missing += 1
    return {"clients": n_clients, "mutations": n_mutations, "deliveries": len(latencies), "missing": missing,
            "p50_ms": round(pct(latencies, 50), 1), "p95_ms": round(pct(latencies, 95), 1),
            "p99_ms": round(pct(latencies, 99), 1), "max_ms": round(max(latencies, default=float("nan")), 1)}


async def bench_concurrent_bootstrap(client, concurrency: int, total: int, ids: list[str],
                                     write_every: float) -> dict:
    """Concurrent page loads while a writer keeps invalidating the snapshot."""
    sem = asyncio.Semaphore(concurrency)
    times: list[float] = []
    stop = asyncio.Event()
    writes = 0

    async def writer():
        nonlocal writes
        while write_every > 0 and not stop.is_set():
            iid = random.choice(ids)
            await client.post("/graphql", json={"query": UPDATE, "variables": {"id": iid, "i": {"priority": writes % 5}}})
            writes += 1
            await asyncio.sleep(write_every)

    async def one():
        async with sem:
            res, ms = await timed(client.get("/sync/bootstrap", headers={"Accept-Encoding": "gzip"}))
            res.raise_for_status()
            times.append(ms)

    write_task = asyncio.create_task(writer())
    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    wall = time.perf_counter() - t0
    stop.set()
    await write_task
    return {"concurrency": concurrency, "requests": total, "writes_during": writes, "rps": round(total / wall, 1),
            "p50_ms": round(pct(times, 50), 1), "p99_ms": round(pct(times, 99), 1)}


async def run(args) -> dict:
    tmp = tempfile.mkdtemp(prefix="cliniar-bench-")
    db_path = None if args.database_url else str(Path(tmp) / "bench.db")
    engine = make_engine(args.database_url or db_path)
    t0 = time.perf_counter()
    ids = seed(engine, args.issues, args.open_ratio)
    print(f"seeded {args.issues} issues in {time.perf_counter() - t0:.1f}s ({db_path or 'postgres'})", file=sys.stderr)
    app = create_app(db_path=db_path, database_url=args.database_url, open_mode=False)
    port = free_port()
    server = start_server(app, port)
    base = f"http://127.0.0.1:{port}"
    results: dict = {"issues_seeded": args.issues, "open_ratio": args.open_ratio}
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {TOKEN}"},
                                 timeout=120, limits=limits) as client:
        results["legacy"] = await bench_legacy(client)
        has_sync = (await client.get("/sync/delta?after=0")).status_code == 200
        if has_sync:
            results["bootstrap"] = await bench_bootstrap(client)
            results["delta"] = await bench_delta(client, results["bootstrap"]["seq"], ids[0])
        ws_url = f"ws://127.0.0.1:{port}/ws"
        results["latency_1_client"] = await bench_latency(client, ws_url, ids, args.mutations, 1)
        results["latency_fanout"] = await bench_latency(client, ws_url, ids, args.mutations, args.clients)
        if has_sync:
            results["concurrent_bootstrap"] = await bench_concurrent_bootstrap(
                client, args.concurrency, args.requests, ids, args.write_every)
    server.should_exit = True
    return results


def report(r: dict) -> None:
    kb = lambda b: f"{b / 1024:,.1f} KB"  # noqa: E731
    lg, bs = r["legacy"], r.get("bootstrap")
    print(f"\n== {r['issues_seeded']} issues, {r['open_ratio']:.0%} open ==")
    print("Initial load")
    print(f"  legacy first page (100 issues): {kb(lg['dashboard_first_page_bytes'])} raw / "
          f"{kb(lg['dashboard_first_page_gzip'])} if gzipped")
    print(f"  legacy all issues: {lg['all_issues_requests']} requests, {kb(lg['all_issues_bytes'])}, "
          f"{lg['all_issues_ms']:,.0f} ms")
    if bs is None:
        print("  (server has no /sync endpoints)")
    else:
        report_sync(r)
    for key, label in (("latency_1_client", "Realtime (1 client)"), ("latency_fanout", "Realtime fan-out")):
        lt = r[key]
        print(f"{label}: {lt['clients']} clients x {lt['mutations']} mutations → p50 {lt['p50_ms']} ms, "
              f"p95 {lt['p95_ms']} ms, p99 {lt['p99_ms']} ms, max {lt['max_ms']} ms, missing {lt['missing']}")
    if "concurrent_bootstrap" in r:
        cb = r["concurrent_bootstrap"]
        print(f"Concurrent bootstrap: {cb['requests']} req @ {cb['concurrency']}, {cb['writes_during']} writes "
              f"during → {cb['rps']} req/s, p50 {cb['p50_ms']} ms, p99 {cb['p99_ms']} ms")


def report_sync(r: dict) -> None:
    kb = lambda b: f"{b / 1024:,.1f} KB"  # noqa: E731
    bs = r["bootstrap"]
    print(f"  bootstrap ({bs['issues']} open + recently closed): 1 request, {kb(bs['raw_bytes'])} raw / "
          f"{kb(bs['gzip_bytes'])} gzip, {bs['cold_ms']:,.0f} ms cold")
    print(f"  bootstrap revalidate (304): p50 {bs['revalidate_304_p50_ms']} ms, 0 body bytes")
    d = r["delta"]
    print(f"  delta after 1 change: {d['bytes']} bytes, {d['ms']} ms")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--issues", type=int, default=5000)
    p.add_argument("--open-ratio", type=float, default=0.3)
    p.add_argument("--clients", type=int, default=100, help="concurrent WebSocket clients for fan-out")
    p.add_argument("--mutations", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=20, help="concurrent bootstrap requests")
    p.add_argument("--requests", type=int, default=100, help="total bootstrap requests in the stress phase")
    p.add_argument("--write-every", type=float, default=0.1,
                   help="seconds between background writes during the bootstrap stress (0 = none)")
    p.add_argument("--database-url", help="PostgreSQL URL (default: temporary SQLite file)")
    p.add_argument("--json", help="also write results to this file")
    args = p.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = asyncio.run(run(args))
    report(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
