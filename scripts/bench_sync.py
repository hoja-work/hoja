#!/usr/bin/env python3
"""Benchmark and stress-test browser sync and realtime delivery.

Seeds a throwaway workspace with N issues (one public team, one private team,
an admin and a non-admin member), starts one or more cliniar server processes
on it, and measures:

  1. legacy load   — the old Dashboard query, plus paging every issue via GraphQL
  2. bootstrap     — /sync/bootstrap cold (raw + gzip bytes) and warm (ETag 304)
  3. delta         — /sync/delta after one mutation
  4. latency       — mutation start → WebSocket frame on 1 client
  5. fan-out       — C WebSocket clients (spread over client processes and server
                     nodes); per delivery: frame latency and "data" latency (frame
                     carried the row, or the client pulled /sync/delta as the current
                     browser does). Half the clients are a non-admin member: they
                     must never see private-team changes ("leaks" must be 0).
  6. writers       — fan-out while W concurrent writers mutate continuously
  7. bootstrap     — concurrent page loads while a writer invalidates the snapshot

With --nodes 2 (PostgreSQL), mutations go to node 0 and every client connects to
node 1, so delivery proves cross-node wakeup (LISTEN/NOTIFY).

Usage:
  python scripts/bench_sync.py --issues 5000 --clients 200
  python scripts/bench_sync.py --issues 50000 --clients 2000 --client-procs 8 \\
      --database-url postgresql+psycopg://bench@127.0.0.1:55433/bench --nodes 2
  python scripts/bench_sync.py --protocol 1 --pull   # legacy browser behaviour
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import gzip
import json
import logging
import os
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cliniar_server.db import (  # noqa: E402
    PRIORITY_LABELS,
    api_key,
    issue,
    issue_label,
    issue_label_link,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_states_for_team,
    seed_tenant,
    team,
    team_member,
    token_hash,
    user,
    workflow_state,
)

TOKEN = "bench-token-" + "x" * 20
MEMBER_TOKEN = "bench-member-" + "y" * 20
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


def summary(values: list[float]) -> dict:
    return {"p50_ms": round(pct(values, 50), 1), "p95_ms": round(pct(values, 95), 1),
            "p99_ms": round(pct(values, 99), 1), "max_ms": round(max(values, default=float("nan")), 1)}


# ---------------------------------------------------------------------------- seed

def seed(engine, n_issues: int, open_ratio: float, private_issues: int) -> dict:
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Bench", org_url_key=f"bench-{new_id()[:6]}",
                         user_email=f"bench-{new_id()[:6]}@bench.test", token=TOKEN, demo_issues=False)
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    org_id = seeded["org_id"]
    with engine.begin() as conn:
        team_row = conn.execute(select(team.c.id, team.c.key).where(team.c.organization_id == org_id)).first()
        # A private team only the admin belongs to, and a non-admin member who cannot see it.
        priv_id = new_id()
        ts = now_iso()
        conn.execute(team.insert().values(id=priv_id, organization_id=org_id, key="PRIV", name="Private",
                                          private=True, created_at=ts, updated_at=ts))
        seed_states_for_team(conn, org_id, priv_id)
        conn.execute(team_member.insert().values(team_id=priv_id, user_id=seeded["user_id"]))
        member_id = new_id()
        conn.execute(user.insert().values(id=member_id, organization_id=org_id, name="Member",
                                          display_name="Member", email=f"m-{member_id[:6]}@bench.test",
                                          active=True, admin=False, created_at=ts, updated_at=ts))
        conn.execute(team_member.insert().values(team_id=team_row.id, user_id=member_id))
        conn.execute(api_key.insert().values(id=new_id(), token_hash=token_hash(MEMBER_TOKEN), label="member",
                                             user_id=member_id, organization_id=org_id, created_at=ts))
        label_ids = []
        for name in ("bug", "feature", "chore", "perf", "security"):
            lid = new_id()
            label_ids.append(lid)
            conn.execute(issue_label.insert().values(id=lid, organization_id=org_id, name=name, color="#888888"))
        ids: dict[str, list[str]] = {"public": [], "private": []}
        for tid, key, count, bucket in ((team_row.id, team_row.key, n_issues, "public"),
                                        (priv_id, "PRIV", private_issues, "private")):
            states = conn.execute(select(workflow_state.c.id, workflow_state.c.type)
                                  .where(workflow_state.c.team_id == tid)).all()
            open_states = [s.id for s in states if s.type not in ("completed", "canceled")]
            closed_states = [s.id for s in states if s.type in ("completed", "canceled")]
            rows, links = [], []
            for n in range(1, count + 1):
                is_open = rng.random() < open_ratio
                age = timedelta(days=rng.uniform(0, 30 if is_open else 365))
                its = (now - age).isoformat()
                prio = rng.randint(0, 4)
                iid = new_id()
                ids[bucket].append(iid)
                rows.append(dict(
                    id=iid, organization_id=org_id, team_id=tid, number=n, identifier=f"{key}-{n}",
                    title=f"Benchmark issue {n}: fix flaky behavior in module {n % 97}",
                    description=LOREM * rng.randint(1, 4), priority=prio, priority_label=PRIORITY_LABELS[prio],
                    state_id=rng.choice(open_states if is_open else closed_states),
                    creator_id=seeded["user_id"], assignee_id=seeded["user_id"],
                    created_at=its, updated_at=its, state_changed_at=its,
                ))
                for lid in rng.sample(label_ids, rng.randint(0, 2)):
                    links.append(dict(issue_id=iid, label_id=lid))
            for start in range(0, len(rows), 500):
                conn.execute(issue.insert(), rows[start:start + 500])
            for start in range(0, len(links), 500):
                conn.execute(issue_label_link.insert(), links[start:start + 500])
            conn.execute(team.update().where(team.c.id == tid).values(issue_counter=count))
    return ids


# ---------------------------------------------------------------------------- servers

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(target: str, port: int, log: Path, deflate: bool = True, workers: int = 1) -> subprocess.Popen:
    if workers > 1:
        cmd = [sys.executable, "-m", "cliniar_server.cli", "serve", "--database-url", target,
               "--port", str(port), "--workers", str(workers)] + ([] if deflate else ["--no-ws-deflate"])
    else:
        code = (f"import sys, uvicorn; sys.path.insert(0, {str(ROOT)!r}); from cliniar_server.app import create_app; "
                f"uvicorn.run(create_app(database_url={target!r}, open_mode=False), host='127.0.0.1', port={port}, "
                f"log_level='warning', backlog=4096, ws_per_message_deflate={deflate})")
        cmd = [sys.executable, "-c", code]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log.open("w"), stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {log.read_text()[-2000:]}")
        time.sleep(0.1)
    raise RuntimeError("server did not start")


# ---------------------------------------------------------------------------- single-process phases

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
    data = res.json()["data"]["issues"]
    cursor, has_next, count = data["pageInfo"]["endCursor"], data["pageInfo"]["hasNextPage"], len(data["nodes"])
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
    await asyncio.sleep(0.3)
    times = []
    for _ in range(20):
        res, ms = await timed(client.get(f"/sync/delta?after={seq}"))
        times.append(ms)
    return {"bytes": len(res.content), "ms": round(statistics.median(times), 2), "issues": len(res.json()["issues"])}


async def bench_concurrent_bootstrap(client, concurrency: int, total: int, ids: list[str],
                                     write_every: float, ws_url: str | None = None) -> dict:
    """Concurrent page loads while a writer keeps invalidating the snapshot.

    One WebSocket client stays connected, as in production (the feed then carries
    rows, which lets the server derive new snapshots from the previous one).
    """
    watcher_stop = asyncio.Event()

    async def watcher():
        if not ws_url:
            return
        async with websockets.connect(ws_url + "?v=2", subprotocols=["bearer", TOKEN], max_size=None) as ws:
            while not watcher_stop.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), timeout=0.2)

    watch_task = asyncio.create_task(watcher())
    await asyncio.sleep(0.3)
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
    watcher_stop.set()
    await asyncio.gather(watch_task, return_exceptions=True)
    return {"concurrency": concurrency, "requests": total, "writes_during": writes, "rps": round(total / wall, 1),
            "p50_ms": round(pct(times, 50), 1), "p99_ms": round(pct(times, 99), 1)}


# ---------------------------------------------------------------------------- client worker process

async def worker_main(cfg: dict) -> None:
    """Hold cfg['clients'] sockets; record first frame and first data arrival per issue id."""
    frames: list[dict] = [{} for _ in range(cfg["clients"])]
    data: list[dict] = [{} for _ in range(cfg["clients"])]
    errors = {"connect": 0, "dropped": 0, "pull_failed": 0, "frames": 0, "bytes": 0, "pulls": 0}
    stop = asyncio.Event()
    ready = 0
    limits = httpx.Limits(max_connections=max(10, cfg["clients"]), max_keepalive_connections=cfg["clients"])
    clients = {tok: httpx.AsyncClient(base_url=cfg["http"], headers={"Authorization": f"Bearer {tok}"},
                                      timeout=60, limits=limits) for tok in {TOKEN, MEMBER_TOKEN}}

    async def one(n: int):
        nonlocal ready
        token = cfg["tokens"][n % len(cfg["tokens"])]
        url = f"{cfg['ws']}?after={cfg['seq']}" + ("&v=2" if cfg["protocol"] == 2 else "")
        seq = cfg["seq"]
        pulling = None
        pull_again = False

        async def pull():
            nonlocal seq, pull_again
            while True:
                pull_again = False
                started = seq
                try:
                    res = await clients[token].get(f"/sync/delta?after={started}")
                    body = res.json()
                    errors["pulls"] += 1
                except Exception:
                    errors["pull_failed"] += 1
                    return
                now = time.time()
                if not body.get("reload"):
                    for row in body.get("issues", []):
                        data[n].setdefault(row[0], now)
                    for iid in body.get("removed", []):
                        data[n].setdefault(iid, now)
                seq = max(seq, body.get("seq", seq))
                if not pull_again:
                    return

        for attempt in range(5):
            try:
                ws = await websockets.connect(url, subprotocols=["bearer", token], max_size=None,
                                              open_timeout=60, ping_interval=None)
                break
            except Exception:
                await asyncio.sleep(0.5 * (attempt + 1))
        else:
            errors["connect"] += 1
            ready += 1
            return
        ready += 1
        try:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                now = time.time()
                errors["frames"] += 1
                errors["bytes"] += len(raw)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    events = parsed.get("events", [])
                    if parsed.get("full"):
                        for row in parsed.get("issues", []):
                            data[n].setdefault(row[0], now)
                        for iid in parsed.get("removed", []):
                            data[n].setdefault(iid, now)
                        seq = max(seq, parsed.get("seq", seq))
                    elif cfg["pull"] and events:
                        pull_again = True
                        if pulling is None or pulling.done():
                            pulling = asyncio.create_task(pull())
                else:
                    events = parsed
                    if cfg["pull"] and events:
                        pull_again = True
                        if pulling is None or pulling.done():
                            pulling = asyncio.create_task(pull())
                for ev in events:
                    if ev.get("entity") == "issue":
                        frames[n].setdefault(ev["entityId"], now)
        except websockets.ConnectionClosed:
            errors["dropped"] += 1
        finally:
            await ws.close()

    tasks = []
    for n in range(cfg["clients"]):
        tasks.append(asyncio.create_task(one(n)))
        if n % 50 == 49:
            await asyncio.sleep(0.05)  # don't SYN-flood the accept queue
    while ready < cfg["clients"]:
        await asyncio.sleep(0.05)
    print("READY", flush=True)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sys.stdin.readline)
    await asyncio.sleep(cfg["drain"])
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    for c in clients.values():
        await c.aclose()
    out = {"frames": frames, "data": data, "tokens": [cfg["tokens"][n % len(cfg["tokens"])]
                                                      for n in range(cfg["clients"])], "errors": errors}
    sys.stdout.write("RESULT " + json.dumps(out) + "\n")
    sys.stdout.flush()


def spawn_workers(n_clients: int, procs: int, cfg: dict) -> list[subprocess.Popen]:
    workers = []
    per = [n_clients // procs + (1 if i < n_clients % procs else 0) for i in range(procs)]
    for i, count in enumerate(per):
        if not count:
            continue
        wcfg = {**cfg, "clients": count, "http": cfg["http"][i % len(cfg["http"])],
                "ws": cfg["ws"][i % len(cfg["ws"])]}
        workers.append(subprocess.Popen([sys.executable, __file__, "--_worker", json.dumps(wcfg)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True))
    for w in workers:
        line = w.stdout.readline()
        if not line.startswith("READY"):
            raise RuntimeError(f"worker failed: {line!r}")
    return workers


def collect_workers(workers) -> list[dict]:
    results = []
    for w in workers:
        w.stdin.write("go\n")
        w.stdin.flush()
    for w in workers:
        for line in w.stdout:
            if line.startswith("RESULT "):
                results.append(json.loads(line[7:]))
                break
        w.wait(timeout=60)
    return results


async def bench_fanout(args, write_client: httpx.AsyncClient, http_urls, ws_urls, ids: dict,
                       n_clients: int, n_mutations: int, gap: float, writers: int = 0) -> dict:
    seq = (await write_client.get("/sync/bootstrap")).json()["seq"]
    cfg = {"seq": seq, "protocol": args.protocol, "pull": args.pull, "drain": max(2.0, n_clients / 500),
           "tokens": [TOKEN, MEMBER_TOKEN] if args.visibility_mix else [TOKEN],
           "http": http_urls, "ws": ws_urls}
    procs = max(1, min(args.client_procs, n_clients))
    workers = await asyncio.to_thread(spawn_workers, n_clients, procs, cfg)
    try:
        return await _drive_fanout(write_client, workers, ids, n_mutations, gap, writers)
    finally:
        for w in workers:
            if w.poll() is None:
                w.kill()


async def _drive_fanout(write_client, workers, ids, n_mutations, gap, writers) -> dict:
    await asyncio.sleep(0.5)
    rng = random.Random(3)
    started: dict[str, float] = {}
    private_targets: set[str] = set()
    stop = asyncio.Event()
    background = 0

    async def background_writer(k: int):
        nonlocal background
        local = random.Random(100 + k)
        while not stop.is_set():
            iid = local.choice(ids["public"])
            await write_client.post("/graphql", json={"query": UPDATE,
                                                      "variables": {"id": iid, "i": {"priority": local.randint(0, 4)}}})
            background += 1

    bg = [asyncio.create_task(background_writer(k)) for k in range(writers)]
    t_run = time.perf_counter()
    targets = rng.sample(ids["public"], n_mutations)
    for n, iid in enumerate(targets):
        if ids["private"] and n % 5 == 4:
            piid = rng.choice(ids["private"])
            private_targets.add(piid)
            await write_client.post("/graphql", json={"query": UPDATE,
                                                      "variables": {"id": piid, "i": {"priority": n % 5}}})
        started[iid] = time.time()
        await write_client.post("/graphql", json={"query": UPDATE, "variables": {"id": iid, "i": {"priority": n % 5}}})
        await asyncio.sleep(gap)
    run_s = time.perf_counter() - t_run
    stop.set()
    await asyncio.gather(*bg, return_exceptions=True)
    results = await asyncio.to_thread(collect_workers, workers)
    frame_lat, data_lat, missing, leaks = [], [], 0, 0
    errors: dict[str, int] = {}
    total_clients = 0
    for res in results:
        for k, v in res["errors"].items():
            errors[k] = errors.get(k, 0) + v
        for frames, data, token in zip(res["frames"], res["data"], res["tokens"], strict=True):
            total_clients += 1
            for iid, t0 in started.items():
                t = frames.get(iid)
                if t is None:
                    missing += 1
                else:
                    frame_lat.append((t - t0) * 1000)
                d = data.get(iid)
                if d is not None:
                    data_lat.append((d - t0) * 1000)
            if token == MEMBER_TOKEN:
                leaks += len(private_targets & (set(frames) | set(data)))
    out = {"clients": total_clients, "mutations": n_mutations, "writers": writers,
           "background_writes": background, "write_rate_per_s": round((background + n_mutations) / run_s, 1),
           "deliveries": len(frame_lat), "missing": missing, "leaks": leaks,
           "frame": summary(frame_lat), "data": summary(data_lat), "data_deliveries": len(data_lat),
           "errors": errors}
    return out


# ---------------------------------------------------------------------------- driver

async def run(args) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="cliniar-bench-", dir=args.scratch))
    target = args.database_url or f"sqlite:///{tmp / 'bench.db'}"
    engine = make_engine(target)
    t0 = time.perf_counter()
    ids = seed(engine, args.issues, args.open_ratio, args.private_issues)
    engine.dispose()
    print(f"seeded {args.issues} issues in {time.perf_counter() - t0:.1f}s ({target})", file=sys.stderr)
    nodes = []
    ports = []
    for i in range(args.nodes):
        port = args.port_base + i if args.port_base else free_port()
        nodes.append(start_server(target, port, tmp / f"node{i}.log", deflate=args.ws_deflate,
                                  workers=args.workers))
        ports.append(port)
    http_urls = [f"http://127.0.0.1:{p}" for p in ports]
    ws_urls = [f"ws://127.0.0.1:{p}/ws" for p in ports]
    # Clients go to the last node(s); writes go to node 0 (cross-node when nodes > 1).
    client_http = http_urls[1:] or http_urls
    client_ws = ws_urls[1:] or ws_urls
    results: dict = {"issues_seeded": args.issues, "open_ratio": args.open_ratio, "nodes": args.nodes,
                     "backend": engine.dialect.name, "protocol": args.protocol, "pull": args.pull}
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    phases = set(args.phases.split(","))

    async def phase(name, coro):
        t = time.perf_counter()
        try:
            results[name] = await asyncio.wait_for(coro, timeout=args.phase_timeout)
        except asyncio.TimeoutError:
            results[name] = {"timeout": args.phase_timeout}
        print(f"[{time.perf_counter() - t:6.1f}s] {name}: {json.dumps(results[name])}", file=sys.stderr, flush=True)
    try:
        async with httpx.AsyncClient(base_url=http_urls[0], headers={"Authorization": f"Bearer {TOKEN}"},
                                     timeout=120, limits=limits) as client:
            if "legacy" in phases:
                await phase("legacy", bench_legacy(client))
            if "sync" in phases:
                await phase("bootstrap", bench_bootstrap(client))
                if "seq" in results["bootstrap"]:
                    await phase("delta", bench_delta(client, results["bootstrap"]["seq"], ids["public"][0]))
            if "latency" in phases:
                await phase("latency_1_client", bench_fanout(args, client, client_http, client_ws, ids, 1,
                                                             args.mutations, 0.05))
            if "fanout" in phases:
                await phase("latency_fanout", bench_fanout(args, client, client_http, client_ws, ids,
                                                           args.clients, args.mutations, args.gap))
            if "writers" in phases and args.writers:
                await phase("fanout_with_writers", bench_fanout(args, client, client_http, client_ws, ids,
                                                                args.clients, args.mutations, args.gap,
                                                                writers=args.writers))
            if "bootstrap" in phases:
                await phase("concurrent_bootstrap", bench_concurrent_bootstrap(
                    client, args.concurrency, args.requests, ids["public"], args.write_every, ws_urls[0]))
    finally:
        for proc in nodes:
            proc.terminate()
        for proc in nodes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return results


def report(r: dict) -> None:
    kb = lambda b: f"{b / 1024:,.1f} KB"  # noqa: E731
    print(f"\n== {r['issues_seeded']} issues, {r['open_ratio']:.0%} open, {r['backend']}, "
          f"{r['nodes']} node(s), protocol v{r['protocol']}{' +pull' if r['pull'] else ''} ==")
    if "legacy" in r:
        lg = r["legacy"]
        print(f"legacy all issues: {lg['all_issues_requests']} requests, {kb(lg['all_issues_bytes'])}, "
              f"{lg['all_issues_ms']:,.0f} ms")
    if "delta" in r and "timeout" not in r["bootstrap"]:
        bs, d = r["bootstrap"], r["delta"]
        print(f"bootstrap ({bs['issues']} issues): {kb(bs['raw_bytes'])} raw / {kb(bs['gzip_bytes'])} gzip, "
              f"{bs['cold_ms']:,.0f} ms cold, 304 p50 {bs['revalidate_304_p50_ms']} ms")
        print(f"delta after 1 change: {d['bytes']} bytes, {d['ms']} ms")
    for key in ("latency_1_client", "latency_fanout", "fanout_with_writers"):
        if key not in r or "timeout" in r[key]:
            continue
        lt = r[key]
        f, d = lt["frame"], lt["data"]
        print(f"{key}: {lt['clients']} clients x {lt['mutations']} mutations, {lt['writers']} writers "
              f"({lt['write_rate_per_s']} writes/s) → frame p50 {f['p50_ms']} p95 {f['p95_ms']} "
              f"p99 {f['p99_ms']} max {f['max_ms']} ms | data p50 {d['p50_ms']} p99 {d['p99_ms']} ms "
              f"({lt['data_deliveries']}) | missing {lt['missing']} leaks {lt['leaks']} errors {lt['errors']}")
    if "concurrent_bootstrap" in r and "timeout" not in r["concurrent_bootstrap"]:
        cb = r["concurrent_bootstrap"]
        print(f"concurrent bootstrap: {cb['requests']} req @ {cb['concurrency']}, {cb['writes_during']} writes "
              f"→ {cb['rps']} req/s, p50 {cb['p50_ms']} ms, p99 {cb['p99_ms']} ms")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        asyncio.run(worker_main(json.loads(sys.argv[2])))
        return
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--issues", type=int, default=5000)
    p.add_argument("--private-issues", type=int, default=200)
    p.add_argument("--open-ratio", type=float, default=0.3)
    p.add_argument("--clients", type=int, default=100, help="concurrent WebSocket clients for fan-out")
    p.add_argument("--client-procs", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    p.add_argument("--mutations", type=int, default=30)
    p.add_argument("--gap", type=float, default=0.1, help="seconds between measured mutations")
    p.add_argument("--writers", type=int, default=0, help="concurrent background writers (writers phase)")
    p.add_argument("--protocol", type=int, default=2, choices=(1, 2))
    p.add_argument("--pull", action="store_true",
                   help="clients pull /sync/delta after frames without rows (the current browser does)")
    p.add_argument("--no-visibility-mix", dest="visibility_mix", action="store_false",
                   help="all clients use the admin token")
    p.add_argument("--concurrency", type=int, default=20, help="concurrent bootstrap requests")
    p.add_argument("--requests", type=int, default=100, help="total bootstrap requests in the stress phase")
    p.add_argument("--write-every", type=float, default=0.1)
    p.add_argument("--nodes", type=int, default=1, help="server processes (needs PostgreSQL for > 1)")
    p.add_argument("--port-base", type=int, default=0)
    p.add_argument("--workers", type=int, default=1, help="uvicorn worker processes per node")
    p.add_argument("--no-ws-deflate", dest="ws_deflate", action="store_false",
                   help="start servers without permessage-deflate (saves per-client compression CPU)")
    p.add_argument("--phases", default="legacy,sync,latency,fanout,writers,bootstrap")
    p.add_argument("--database-url", help="PostgreSQL URL (default: temporary SQLite file)")
    p.add_argument("--scratch", default=None, help="directory for the temporary database and logs")
    p.add_argument("--phase-timeout", type=float, default=300)
    p.add_argument("--json", help="also write results to this file")
    args = p.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = asyncio.run(run(args))
    report(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
