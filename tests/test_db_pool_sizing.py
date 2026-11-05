"""Pool sizing is arithmetic against one server, so it has to be predictable.

Every instance is per tenant and every instance holds a pool. The failure this
prevents is not subtle: N tenants times (size + overflow) exceeding the
server's max_connections takes the whole fleet down, not one tenant.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from hoja_server.db import make_engine

PG = "postgresql+psycopg://u:p@127.0.0.1:1/none"


def test_the_default_pool_is_conservative(monkeypatch):
    for var in ("HOJA_DB_POOL_SIZE", "HOJA_DB_MAX_OVERFLOW", "HOJA_DB_POOL_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    eng = make_engine(PG)
    assert eng.pool.size() == 5
    assert eng.pool._max_overflow == 5
    assert eng.pool._timeout == 30


def test_the_pool_can_be_sized_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOJA_DB_POOL_SIZE", "2")
    monkeypatch.setenv("HOJA_DB_MAX_OVERFLOW", "3")
    monkeypatch.setenv("HOJA_DB_POOL_TIMEOUT", "5")
    eng = make_engine(PG)
    assert (eng.pool.size(), eng.pool._max_overflow, eng.pool._timeout) == (2, 3, 5)


def test_nonsense_is_refused_rather_than_silently_defaulted(monkeypatch):
    monkeypatch.setenv("HOJA_DB_POOL_SIZE", "lots")
    with pytest.raises(ValueError):
        make_engine(PG)
    monkeypatch.setenv("HOJA_DB_POOL_SIZE", "0")
    with pytest.raises(ValueError):
        make_engine(PG)


def test_the_fleet_fits_under_a_hundred_connections(monkeypatch):
    # The arithmetic that matters: nine instances at 5+5 stay under 100 with
    # room for psql and a migration, which the old 40+20 did not.
    monkeypatch.delenv("HOJA_DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("HOJA_DB_MAX_OVERFLOW", raising=False)
    eng = make_engine(PG)
    per_instance = eng.pool.size() + eng.pool._max_overflow
    assert per_instance * 9 < 100
