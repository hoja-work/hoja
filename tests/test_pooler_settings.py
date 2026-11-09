from types import SimpleNamespace

from sqlalchemy import event

from hoja_server.db import make_engine
from hoja_server.realtime import RealtimeHub


def _connect_kwargs(eng):
    seen = {}

    @event.listens_for(eng, "do_connect")
    def capture(_dialect, _rec, _args, kwargs):
        seen.update(kwargs)
        raise ConnectionAbortedError

    try:
        eng.connect()
    except Exception:
        pass
    return seen


def test_transaction_pooler_disables_server_side_prepares(monkeypatch):
    monkeypatch.setenv("HOJA_DB_POOLER", "transaction")
    assert _connect_kwargs(make_engine("postgresql+psycopg://u:p@127.0.0.1:1/x"))["prepare_threshold"] is None
    monkeypatch.delenv("HOJA_DB_POOLER")
    assert "prepare_threshold" not in _connect_kwargs(make_engine("postgresql+psycopg://u:p@127.0.0.1:1/x"))


def test_listen_uses_the_direct_url(monkeypatch):
    hub = RealtimeHub.__new__(RealtimeHub)
    hub.store = SimpleNamespace(engine=make_engine("postgresql+psycopg://u:p@pgbouncer:6432/cliniar"))
    assert "pgbouncer:6432" in hub._pg_dsn()
    monkeypatch.setenv("HOJA_LISTEN_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/cliniar")
    assert hub._pg_dsn() == "postgresql://u:p@db:5432/cliniar"
