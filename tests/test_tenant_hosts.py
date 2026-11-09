"""The broker host must never be mistaken for a tenant, in every copy of the slug policy."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from hoja_server.identity_transfer import tenant_target
from hoja_server.signup_policy import RESERVED_SLUGS

_HERE = Path(__file__).resolve()
# OSS keeps hoja_server/ at the repo root; Cloud keeps it under app/.
ROOT = _HERE.parents[1]


def _first_existing(*relative: str) -> Path | None:
    for base in (ROOT, ROOT.parent):
        for rel in relative:
            candidate = base / rel
            if candidate.is_file():
                return candidate
    return None


@pytest.mark.parametrize("host", ["api", "app", "www", "admin"])
def test_a_reserved_name_is_never_a_tenant_host(host: str) -> None:
    assert tenant_target(f"https://{host}.hoja.work", apex="hoja.work") is None


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.com",
        "https://hoja.work",  # the apex is not a tenant
        "http://clover.hoja.work",  # not https
        "https://clover.hoja.work.evil.com",
        "",
        None,
    ],
)
def test_only_real_tenant_origins_are_return_targets(value: str | None) -> None:
    assert tenant_target(value, apex="hoja.work") is None


def test_a_normal_tenant_origin_is_allowed() -> None:
    assert tenant_target("https://clover.hoja.work", apex="hoja.work") == "https://clover.hoja.work/"


def test_api_stays_reserved_in_the_server_policy() -> None:
    assert "api" in RESERVED_SLUGS


def test_api_stays_reserved_in_the_generated_frontend_policy() -> None:
    generated = _first_existing(
        "frontend/src/signup/slugPolicy.generated.ts",
        "app/frontend/src/signup/slugPolicy.generated.ts",
    )
    if generated is None:
        pytest.skip("no generated slug policy in this checkout")
    text = generated.read_text(encoding="utf-8")
    names = set(re.findall(r'"([a-z0-9-]+)"', text)) | set(re.findall(r"'([a-z0-9-]+)'", text))
    # The generator emits a bare-word list too; fall back to a word-boundary search.
    assert "api" in names or re.search(r'\bapi\b', text), "api missing from slugPolicy.generated.ts"


def test_reserved_slugs_match_the_provisioner_script() -> None:
    """Parity with the script the signup gate and the provisioner both rely on."""
    script = _first_existing("infra/scripts/hoja-tenant", "scripts/hoja-tenant")
    if script is None:
        pytest.skip("no hoja-tenant provisioner in this checkout")
    source = script.read_text(encoding="utf-8")
    block = re.search(r"RESERVED_SLUGS = \{([^}]*)\}", source)
    assert block is not None
    provisioner = ast.literal_eval("{" + block.group(1) + "}")
    assert set(provisioner) == set(RESERVED_SLUGS)
    assert "api" in provisioner
