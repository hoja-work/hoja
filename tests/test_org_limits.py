import asyncio
import threading

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from hoja_server.org_limits import OrgBusy, OrgConcurrency, OrgConcurrencyMiddleware


def _app(limiter: OrgConcurrency, gate: threading.Event, entered: threading.Event):
    async def work(request):
        limiter.enter(request.scope, request.query_params["org"])
        if request.query_params.get("hold"):
            entered.set()
            await asyncio.get_running_loop().run_in_executor(None, gate.wait, 5)
        if request.query_params.get("fail"):
            raise RuntimeError("boom")
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/w", work)])
    app.add_middleware(OrgConcurrencyMiddleware, limiter=limiter)
    return app


def test_an_organization_at_its_cap_gets_429_and_others_do_not():
    limiter, gate, entered = OrgConcurrency(limit=1), threading.Event(), threading.Event()
    client = TestClient(_app(limiter, gate, entered))
    held = threading.Thread(target=lambda: client.get("/w?org=a&hold=1"))
    held.start()
    assert entered.wait(5)
    busy = client.get("/w?org=a")
    assert busy.status_code == 429
    assert busy.headers["retry-after"] == "1"
    assert client.get("/w?org=b").status_code == 200
    gate.set()
    held.join(5)
    assert limiter.active("a") == 0
    assert client.get("/w?org=a").status_code == 200


def test_a_failing_request_releases_its_slot():
    limiter = OrgConcurrency(limit=1)
    client = TestClient(_app(limiter, threading.Event(), threading.Event()), raise_server_exceptions=False)
    assert client.get("/w?org=a&fail=1").status_code == 500
    assert limiter.active("a") == 0


def test_the_same_request_claims_one_slot_per_organization():
    limiter, scope = OrgConcurrency(limit=1), {"type": "http"}
    limiter.enter(scope, "a")
    limiter.enter(scope, "a")
    assert limiter.active("a") == 1
    with pytest.raises(OrgBusy):
        limiter.enter({"type": "http"}, "a")
    limiter.release(scope)
    assert limiter.active("a") == 0


def test_websockets_and_disabled_limits_are_not_counted():
    limiter = OrgConcurrency(limit=1)
    limiter.enter({"type": "websocket"}, "a")
    assert limiter.active("a") == 0
    off = OrgConcurrency(limit=0)
    off.enter({"type": "http"}, "a")
    off.enter({"type": "http"}, "a")
    assert off.active("a") == 0


def test_the_app_wires_the_cap_into_authentication(tmp_path, monkeypatch):
    from hoja_server import org_limits
    from hoja_server.app import create_app
    from hoja_server.db import make_engine, migrate, seed_tenant

    claimed, limiters = [], []
    original_enter, original_init = org_limits.OrgConcurrency.enter, org_limits.OrgConcurrency.__init__

    def init(self, limit=None):
        original_init(self, limit)
        limiters.append(self)

    def enter(self, scope, org):
        original_enter(self, scope, org)
        claimed.append((org, self.active(org)))

    monkeypatch.setattr(org_limits.OrgConcurrency, "__init__", init)
    monkeypatch.setattr(org_limits.OrgConcurrency, "enter", enter)
    engine = make_engine(tmp_path / "l.db")
    migrate(engine)
    admin = seed_tenant(engine, org_name="L", org_url_key="l", user_email="o@l.test", token="l-tok",
                        demo_issues=False)
    client = TestClient(create_app(str(tmp_path / "l.db"), open_mode=False))
    res = client.post("/graphql", json={"query": "{ viewer { id } }"}, headers={"Authorization": "Bearer l-tok"})
    assert res.status_code == 200
    assert (admin["org_id"], 1) in claimed
    assert limiters[-1].active(admin["org_id"]) == 0
