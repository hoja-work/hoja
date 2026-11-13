#!/usr/bin/env python3
"""The runner screens against their two server-side authorities (HOJA-602-A-16).

Nothing else in this repository checks that a frontend surface names the same things the server
does. This file is that check, and it deliberately reads the *sources* rather than a copy of them:

  1. `runner/hoja_runnerd/connect.py` — imported, and `Exposure.public_row()` is called, so the
     expected exposure fields are whatever that method actually returns.
  2. `kitty-specs/agents-as-members-01M78WJN/spec.md` — the glossary line for "Runner", parsed,
     because that line is the only written list of a runner's columns.

Then it compares them with the two mapping objects and the two types in
`app/frontend/src/agents/types.ts`, and finally with the fixture and the screen, to prove that the
two secrets (the tailcat address and the lease token hash) cannot reach a rendered row.

Exit code 1 names the offending field. `npm run test:runner-contract`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TYPES = REPO / "frontend/src/agents/types.ts"
SEED = REPO / "frontend/src/agents/seed.ts"
PANE = REPO / "frontend/src/agents/RunnersPane.tsx"
SPEC = REPO.parent / "kitty-specs/agents-as-members-01M78WJN/spec.md"
CONNECT = REPO.parent / "runner/hoja_runnerd/connect.py"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'PASS' if condition else 'FAIL'} {message}")
    if not condition:
        failures.append(message)


def load_connect():
    spec = importlib.util.spec_from_file_location("hoja_connect_contract", CONNECT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolves annotations through sys.modules[cls.__module__]; without this the
    # @dataclass decorators in connect.py fail with a NoneType.__dict__ error.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mapping_block(name: str) -> dict[str, str]:
    """The `export const NAME = {server: 'local', ...} as const;` object in types.ts."""
    text = TYPES.read_text()
    start = text.index(f"export const {name} = {{")
    end = text.index("} as const;", start)
    body = text[start:end]
    # Keys are quoted only when they contain a space ("lease token hash").
    pairs = re.findall(r"^\s*'?([A-Za-z_][A-Za-z0-9_ ]*)'?:\s*'([^']+)'", body, re.M)
    return {server: local for server, local in pairs}


def type_fields(name: str) -> set[str]:
    text = TYPES.read_text()
    start = text.index(f"export type {name} = {{")
    end = text.index("\n};", start)
    body = text[start:end]
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??:", body, re.M))


def main() -> int:
    connect = load_connect()

    # ---- 1. exposures: connect.py is the authority ----
    exposure = connect.Exposure(
        exposure_id="exp-x",
        workspace_id="ws-x",
        ports=(8080,),
        pinned_peer="nodekey:" + "0" * 64,
        opened_at=0.0,
        expires_at=1.0,
    )
    public_keys = set(exposure.public_row())
    declared = mapping_block("EXPOSURE_SERVER_FIELDS")
    check(public_keys == set(declared),
          "every Exposure.public_row() field is mapped, and nothing else "
          f"(public={sorted(public_keys)} mapped={sorted(declared)})")
    local_exposure = set(declared.values())
    fields = type_fields("ConnectExposure")
    check(local_exposure <= fields,
          f"ConnectExposure declares every mapped field ({sorted(local_exposure - fields)} missing)")
    check(not ({"address", "psk", "presharedkey", "join_token", "lease_token"} & fields),
          "ConnectExposure carries no address, key or token field")

    # ---- 2. runners: the spec glossary is the authority ----
    line = next((ln for ln in SPEC.read_text().splitlines() if ln.startswith("- **Runner**")), "")
    check(bool(line), "spec.md still has the Runner glossary line")
    glossary = {part.strip().rstrip(".") for part in line.split("—", 1)[-1].split(",")}
    declared_runner = mapping_block("RUNNER_SERVER_FIELDS")
    check(set(declared_runner) == glossary,
          f"the runner mapping covers the glossary exactly (glossary={sorted(glossary)} "
          f"mapped={sorted(declared_runner)})")
    local_runner = set(declared_runner.values())
    runner_fields = type_fields("Runner")
    check(local_runner <= runner_fields,
          f"Runner declares every mapped field ({sorted(local_runner - runner_fields)} missing)")
    check("leaseTokenHash" not in runner_fields,
          "Runner does not expose the lease token hash column")

    # ---- 3. the two secrets cannot reach a rendered row ----
    seed_text = SEED.read_text()
    addresses = connect.TAILCAT_ADDRESS_RE.findall(seed_text)
    check(len(addresses) >= 1, "the fixture carries a tailcat-shaped canary so the leak test has a target")
    check(all("address" in ln for ln in seed_text.splitlines()
              if connect.TAILCAT_ADDRESS_RE.search(ln)),
          "every tailcat-shaped string in the fixture is on an `address` line (the lease payload)")
    # Prose may say the word (that is the explanation); a *field access* is what would render it.
    check(re.search(r"\.address\b|\baddress\s*[:=]", PANE.read_text()) is None,
          "the runner screen never reads an address field, so it has nothing to render")
    check("leaseTokenHash" not in PANE.read_text(),
          "the runner screen never mentions the lease token hash")
    check("publicExposure" in (REPO / "frontend/src/agents/adapter.ts").read_text(),
          "the adapter projects exposures through publicExposure before serving them")

    print()
    if failures:
        print(f"{len(failures)} failed")
        return 1
    print("runner contract: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
