"""Every credential Hoja mints must name Hoja (HOJA-207).

``gen_token`` was written as ``"lin_api_" + ...`` in the initial
``feat: rename Clinear to Cliniar`` commit (831ad3f5) and survived the rename to
Hoja, so product-minted keys still advertise Linear. Nothing parses the prefix:
lookups go through ``token_hash`` against ``api_key.token_hash``, which is why
existing ``lin_api_`` keys must keep working with no migration.

The entropy contract is part of the test: the prefix may change, the 40-character
URL-safe body may not shrink.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from hoja_server.db import (
    api_key,
    gen_token,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
    token_hash,
)
from hoja_server.store import Store

APP_ROOT = Path(__file__).resolve().parents[1]
HOJA_PREFIX = "hoja_"
BODY_CHARS = 40  # secrets.token_urlsafe(30)
LEGACY_PREFIX = "lin_api_"


def _fresh_store(tmp_path, name: str = "prefix.db"):
    engine = make_engine(tmp_path / name)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="Prefix Workspace", org_url_key="prefix-workspace",
        user_email="prefix@example.test", demo_issues=False,
    )
    return engine, seeded, Store(engine)


def test_gen_token_mints_the_hoja_prefix_and_keeps_its_entropy() -> None:
    token = gen_token()
    assert token.startswith(HOJA_PREFIX), token
    assert not token.startswith(LEGACY_PREFIX)
    assert len(token) == len(HOJA_PREFIX) + BODY_CHARS
    assert re.fullmatch(r"hoja_[A-Za-z0-9_-]{40}", token), token


def test_seeded_admin_token_uses_the_hoja_prefix(tmp_path) -> None:
    _engine, seeded, _store = _fresh_store(tmp_path)
    assert seeded["token"].startswith(HOJA_PREFIX), seeded["token"]


def test_settings_api_key_uses_the_hoja_prefix(tmp_path) -> None:
    _engine, seeded, store = _fresh_store(tmp_path)
    created = store.create_api_key(
        seeded["org_id"], seeded["user_id"], "Laptop", "read_write",
    )
    assert created is not None
    _metadata_row, secret = created
    assert secret.startswith(HOJA_PREFIX), secret
    assert len(secret) == len(HOJA_PREFIX) + BODY_CHARS


def test_existing_lin_api_key_still_authenticates(tmp_path) -> None:
    """No rotation, no revocation: a key already in the wild keeps working."""
    engine, seeded, store = _fresh_store(tmp_path)
    legacy = LEGACY_PREFIX + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zA7bC0dE"
    with engine.begin() as conn:
        conn.execute(api_key.insert().values(
            id=new_id(), token_hash=token_hash(legacy), label="Pre-207 key",
            hint=legacy[-4:], access="read_write", user_id=seeded["user_id"],
            organization_id=seeded["org_id"], created_at=now_iso(),
        ))
    resolved = store.resolve_token(legacy)
    assert resolved is not None
    assert resolved["label"] == "Pre-207 key"


def test_server_source_never_mints_the_linear_prefix() -> None:
    """The rename cannot silently regress: no ``lin_api_`` literal in the server."""
    offenders = sorted(
        f"{path.relative_to(APP_ROOT)}:{lineno}"
        for path in (APP_ROOT / "hoja_server").rglob("*.py")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if LEGACY_PREFIX in line
    )
    assert offenders == [], f"legacy Linear prefix still minted/mentioned: {offenders}"


def _run_pre_commit(staged_files: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real hook in a throwaway repo so the pattern table is what is tested."""
    repo = tmp_path / "hookrepo"
    (repo / "scripts").mkdir(parents=True)
    hook = (APP_ROOT / "scripts" / "pre-commit.sh").read_text(encoding="utf-8")
    (repo / "scripts" / "pre-commit.sh").write_text(hook, encoding="utf-8")
    (repo / "scripts" / "pre-commit.sh").chmod(0o755)
    # The hook's documentation gate is a separate policy; this test is about secrets.
    (repo / "scripts" / "check_docs.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8",
    )
    env_cmd = ["git", "init", "-q"]
    subprocess.run(env_cmd, cwd=repo, check=True)
    for rel, content in staged_files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return subprocess.run(
        ["bash", "scripts/pre-commit.sh"], cwd=repo, capture_output=True, text=True,
    )


def test_pre_commit_scanner_blocks_the_new_prefix(tmp_path) -> None:
    fake = HOJA_PREFIX + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zA7bC0dE"
    result = _run_pre_commit({"leak.toml": f'token = "{fake}"\n'}, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SECRET DETECTED" in result.stderr


def test_pre_commit_scanner_still_blocks_the_legacy_prefix(tmp_path) -> None:
    """Old keys live in dotfiles for a long time; keep catching them."""
    fake = LEGACY_PREFIX + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zA7bC0dE"
    result = _run_pre_commit({"leak.toml": f'token = "{fake}"\n'}, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SECRET DETECTED" in result.stderr


def test_pre_commit_scanner_allows_a_clean_file(tmp_path) -> None:
    result = _run_pre_commit({"notes.md": "nothing secret here\n"}, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
