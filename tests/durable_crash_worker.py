"""Subprocess worker for test_agent_durable: phase 'crash' dies mid-turn, phase 'recover' finishes it.

Run as: python durable_crash_worker.py <crash|recover> <dir>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dbos import DBOS
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import func, select

from hoja_server import agent_durable as ad
from hoja_server.db import agent_message, issue, make_engine, migrate, model_provider, new_id, now_iso, seed_tenant, team
from hoja_server.resolvers import build_schema
from hoja_server.store import Store
from hoja_server.writer import Writer

TITLE = "Durable crash test issue"
phase, root = sys.argv[1], Path(sys.argv[2])
engine = make_engine(root / "app.db")
meta_path = root / "meta.json"

if phase == "crash":
    migrate(engine)
    admin = seed_tenant(engine, org_name="Crash Org", org_url_key="crash-org", user_email="o@crash.test",
                        token="crash-token", demo_issues=False)
    with engine.begin() as conn:
        team_id = conn.execute(select(team.c.id).where(team.c.organization_id == admin["org_id"])).scalars().first()
        cols = set(model_provider.c.keys())
        row = dict(id=new_id(), organization_id=admin["org_id"], name="Stub", provider="openai", model_id="stub",
                   base_url="http://127.0.0.1:9/v1", encrypted_api_key="unused", enabled=True,
                   created_at=now_iso(), updated_at=now_iso())
        conn.execute(model_provider.insert().values(**{k: v for k, v in row.items() if k in cols}))
    meta_path.write_text(json.dumps({"org_id": admin["org_id"], "user_id": admin["user_id"], "team_id": team_id}))
meta = json.loads(meta_path.read_text())
calls_path = root / f"model-calls-{phase}.txt"


async def stream_fn(messages, info):
    with calls_path.open("a") as f:
        f.write(f"{len(messages)}\n")
    if len(messages) == 1:
        query = "mutation($t: String!, $title: String!) { issueCreate(input: {teamId: $t, title: $title}) { success } }"
        args = {"query": query, "variables": {"t": meta["team_id"], "title": TITLE}}
        yield {0: DeltaToolCall(name="graphql", json_args=json.dumps(args))}
        return
    if phase == "crash":
        os._exit(17)  # hard crash after the mutation's step was checkpointed
    yield "Created it."


ad.model_factory = lambda row: FunctionModel(stream_function=stream_fn)
ad.configure(Store(engine), Writer(engine), build_schema())
DBOS(config={"name": "crash-test", "system_database_url": f"sqlite:///{root / 'dbos.sqlite'}"})
DBOS.launch()

if phase == "crash":
    async def run() -> None:
        async for _ in ad.stream_reply(Store(engine), meta["org_id"], meta["user_id"],
                                       [{"role": "user", "content": "make an issue"}], {"access": "read_write"}):
            pass

    asyncio.run(run())
    sys.exit(3)  # unreachable when the crash happens as intended

workflow = DBOS.list_workflows()[0]
DBOS.retrieve_workflow(workflow.workflow_id).get_result()
with engine.connect() as conn:
    issues = conn.execute(select(func.count()).select_from(issue).where(issue.c.title == TITLE)).scalar_one()
    replies = conn.execute(select(agent_message.c.content).where(agent_message.c.role == "assistant")).scalars().all()
print(json.dumps({
    "status": DBOS.get_workflow_status(workflow.workflow_id).status,
    "issues": issues,
    "replies": replies,
    "recover_model_calls": calls_path.read_text().split(),
}))
DBOS.destroy()
