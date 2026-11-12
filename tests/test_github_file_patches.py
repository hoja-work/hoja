"""The on-demand patch loader and the explicit-reason contract (HOJA-513).

Every test exists to prove one thing: a patch that cannot be shown comes back with a
stable, honest reason — never a silent null — and a patch that can be shown is cached
back exactly once. The classification mirrors the bulk enrichment script's, so the two
writers cannot disagree about what a null patch means.
"""
from __future__ import annotations

import json
import os

import pytest

from hoja_server import github_file_patches as gfp


def _entry(**overrides):
    entry = {
        "sha": "60515581a0ed2e3a114bf34addd0ee861676b845",
        "filename": "app/main.tsx",
        "status": "modified",
        "additions": 17,
        "deletions": 17,
        "changes": 34,
        "patch": "-" + "x" * 10 + "\n+" + "y" * 10,
    }
    entry.update(overrides)
    return entry


class _Writes:
    """A scripted GitHubWriteClient: answers fetch_pull_files with one pull's files."""

    def __init__(self, entries):
        self._entries = entries
        self.asked: tuple[str, int] | None = None

    async def fetch_pull_files(self, repository, number, *, max_pages=30):
        self.asked = (repository, number)
        return self._entries


def test_a_loadable_patch_needs_no_explanation():
    assert gfp.classify_fetched(_entry()) is None
    assert gfp.patch_fits("-x\n+y") is True


def test_a_patch_over_the_cap_is_oversize_with_its_size():
    marker = gfp.classify_fetched(_entry(patch="x" * (gfp.PATCH_LIMIT + 1)))
    assert marker == {"oversize": True, "size": gfp.PATCH_LIMIT + 1}
    assert gfp.patch_fits("x" * (gfp.PATCH_LIMIT + 1)) is False


def test_a_pure_rename_is_unchanged_not_binary():
    marker = gfp.classify_fetched(_entry(patch=None, status="renamed",
                                         additions=0, deletions=0, changes=0))
    assert marker == {"unchanged": True}


def test_the_empty_blob_is_empty_not_binary():
    marker = gfp.classify_fetched(_entry(patch=None, sha=gfp.EMPTY_BLOB,
                                         additions=0, deletions=0, changes=0))
    assert marker == {"empty": True}


def test_a_verified_binary_blob_says_binary():
    assert gfp.classify_fetched(_entry(patch=None), binary=True) == {"binary": True}


def test_text_that_github_refuses_to_render_is_oversize():
    marker = gfp.classify_fetched(_entry(patch=None), binary=False, blob_size=257215)
    assert marker == {"oversize": True, "size": 257215}


def test_a_nul_byte_makes_a_blob_binary():
    assert gfp.is_binary_bytes(b"\0" + b"a" * 100) is True
    assert gfp.is_binary_bytes(b"print('hello')\n" * 10) is False


async def test_load_returns_the_patch_when_it_fits():
    writes = _Writes([_entry()])
    out = await gfp.load_file_patch(writes, "o/r", 12, "app/main.tsx")
    assert out["patch"] is not None
    assert out["marker"] is None and out["reason"] is None
    assert writes.asked == ("o/r", 12)


async def test_load_reports_a_missing_path():
    writes = _Writes([_entry()])
    out = await gfp.load_file_patch(writes, "o/r", 12, "app/absent.tsx")
    assert out["patch"] is None and out["entry"] is None
    assert out["reason"] == gfp.NOT_IN_PULL


async def test_load_reports_an_over_cap_patch_without_caching_it():
    writes = _Writes([_entry(patch="x" * (gfp.PATCH_LIMIT + 1))])
    out = await gfp.load_file_patch(writes, "o/r", 12, "app/main.tsx")
    assert out["patch"] is None
    assert out["reason"] == gfp.OVER_CAP
    assert out["marker"] == {"oversize": True, "size": gfp.PATCH_LIMIT + 1}


async def test_load_prefers_the_stored_verified_marker_for_a_github_omission():
    writes = _Writes([_entry(patch=None)])
    out = await gfp.load_file_patch(writes, "o/r", 12, "app/main.tsx",
                                    stored_entry={"binary": True})
    assert out["patch"] is None
    assert out["reason"] == gfp.GITHUB_OMITTED
    assert out["marker"] == {"binary": True}


async def test_load_keeps_the_bulk_size_for_a_refused_text_diff():
    writes = _Writes([_entry(patch=None)])
    out = await gfp.load_file_patch(writes, "o/r", 12, "app/main.tsx",
                                    stored_entry={"oversize": True, "size": 257215})
    assert out["marker"] == {"oversize": True, "size": 257215}


def test_result_is_explicit_about_every_field():
    out = gfp.result_for("a.png", None, {"binary": True}, reason=gfp.GITHUB_OMITTED)
    assert out == {"path": "a.png", "patch": None, "binary": True, "oversize": None,
                   "oversizeSize": None, "empty": None, "unchanged": None,
                   "cached": None, "reason": gfp.GITHUB_OMITTED}


def test_the_serializer_passes_markers_through_and_defaults_old_payloads():
    from hoja_server.store import Store

    marked = Store._ser_github_file({
        "path": "a.png", "additions": 0, "deletions": 0, "status": "modified",
        "patch": None, "previousPath": None, "binary": True,
    })
    assert marked["binary"] is True and marked["oversize"] is None
    assert marked["oversizeSize"] is None and marked["cached"] is None

    oversize = Store._ser_github_file({
        "path": "b.ts", "additions": 1, "deletions": 1, "status": "modified",
        "patch": None, "oversize": True, "size": 42768,
    })
    assert oversize["oversize"] is True and oversize["oversizeSize"] == 42768

    legacy = Store._ser_github_file({
        "path": "c.ts", "additions": 1, "deletions": 0, "status": "modified",
        "patch": None,
    })
    assert legacy["binary"] is None and legacy["oversize"] is None


def _db():
    from hoja_server.db import make_engine, migrate
    from tests import rls_mode

    url, _schema = rls_mode.new_schema_url()
    engine = make_engine(url)
    migrate(engine)
    return engine


@pytest.mark.skipif(not os.environ.get("HOJA_TEST_POSTGRES_URL"), reason="needs PostgreSQL")
def test_the_cache_write_is_idempotent_and_additive():
    from hoja_server.db import seed_tenant
    from hoja_server.store import Store

    engine = _db()
    tenant = seed_tenant(engine, org_name="Alpha", org_url_key="alpha513",
                         user_email="u513@alpha.test", token="tok-513", demo_issues=True)
    store = Store(engine)
    link = store.github_link_add(
        tenant["org_id"], _issue_id(store, tenant["org_id"]),
        kind="pr", external_id="o/r#7", url="https://github.com/o/r/pull/7",
        title="o/r #7")
    payload = json.dumps([{
        "path": "app/main.tsx", "additions": 17, "deletions": 17,
        "status": "modified", "patch": None, "oversize": True, "size": 42768,
    }], separators=(",", ":"), sort_keys=True)
    with engine.begin() as conn:
        from sqlalchemy import update

        from hoja_server.db import github_link
        conn.execute(update(github_link).where(
            github_link.c.id == link["id"]).values(files=payload))

    first = store.github_link_cache_file_patch(
        tenant["org_id"], link["id"], "app/main.tsx", "-x\n+y")
    assert first is not None
    entry = first["files"][0]
    assert entry["patch"] == "-x\n+y" and entry["cached"] is True
    assert entry["oversize"] is None and entry["oversizeSize"] is None
    # The stored JSON itself carries no oversize marker anymore (the GraphQL shape
    # always lists the keys, null when unclaimed).
    from sqlalchemy import select
    from hoja_server.db import github_link
    with engine.connect() as conn:
        stored = json.loads(conn.execute(select(github_link.c.files).where(
            github_link.c.id == link["id"])).scalar_one())
    assert "oversize" not in stored[0] and "size" not in stored[0]
    assert stored[0]["cached"] is True

    # A second write of the same patch changes nothing.
    second = store.github_link_cache_file_patch(
        tenant["org_id"], link["id"], "app/main.tsx", "-x\n+y")
    assert second is not None
    assert second["files"][0]["patch"] == "-x\n+y"


@pytest.mark.skipif(not os.environ.get("HOJA_TEST_POSTGRES_URL"), reason="needs PostgreSQL")
def test_a_missing_entry_or_link_writes_nothing():
    from hoja_server.db import seed_tenant
    from hoja_server.store import Store

    engine = _db()
    tenant = seed_tenant(engine, org_name="Alpha", org_url_key="alpha513b",
                         user_email="u513b@alpha.test", token="tok-513b", demo_issues=True)
    store = Store(engine)
    issue_id = _issue_id(store, tenant["org_id"])
    link = store.github_link_add(
        tenant["org_id"], issue_id, kind="pr", external_id="o/r#8",
        url="https://github.com/o/r/pull/8", title="o/r #8")
    assert store.github_link_cache_file_patch(
        tenant["org_id"], link["id"], "absent.tsx", "-x") is None
    assert store.github_link_cache_file_patch(
        tenant["org_id"], "ghl_missing", "app/main.tsx", "-x") is None


def _issue_id(store, org_id):
    from sqlalchemy import select

    from hoja_server.db import issue

    with store.engine.connect() as conn:
        return conn.execute(select(issue.c.id).where(
            issue.c.organization_id == org_id).limit(1)).first()[0]
