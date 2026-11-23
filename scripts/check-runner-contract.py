#!/usr/bin/env python3
"""The runner screens against their server-side authorities (HOJA-602-A-16).

Nothing else in this repository checks that a frontend surface names the same things the server
does. This file is that check, and it deliberately reads the *sources* rather than a copy of them:

  1. `app/hoja_server/connect_endpoints.py` — the `ConnectEndpoint` SDL, parsed, because since
     WP09 (revision 71) that type *is* the Connect row the screen draws.
  2. `kitty-specs/agents-as-members-01M78WJN/spec.md` — the glossary line for "Runner", parsed,
     because that line is the only written list of a runner's columns.
  3. `runner/hoja_runnerd/connect.py` — imported, for `TAILCAT_ADDRESS_RE`, so the leak check uses
     the runner's own pattern rather than a second copy of it.

Then it compares them with the mapping objects and the types in `app/frontend/src/agents/types.ts`,
and finally with the screen and the whole `src/agents` tree, to prove that the tailcat address —
which is not in the database, not in the SDL and not in the type — cannot reach a rendered row.

Exit code 1 names the offending field. `npm run test:runner-contract`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TYPES = REPO / "frontend/src/agents/types.ts"
AGENTS_SRC = REPO / "frontend/src/agents"
SDL_SOURCE = REPO / "hoja_server/connect_endpoints.py"
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


def sdl_fields(type_name: str) -> set[str]:
    """The field names of one type in the GraphQL SDL, read from the module that declares it."""
    text = SDL_SOURCE.read_text()
    start = text.index(f"type {type_name} {{")
    end = text.index("}", start)
    body = text[start:end]
    names = set()
    for line in body.splitlines()[1:]:
        line = line.strip()
        # Skip descriptions (they are strings in the SDL, and they name fields in prose).
        if not line or line.startswith('"'):
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            names.add(match.group(1))
    return names


def type_fields(name: str) -> set[str]:
    text = TYPES.read_text()
    start = text.index(f"export type {name} = {{")
    end = text.index("\n};", start)
    body = text[start:end]
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??:", body, re.M))


def main() -> int:
    connect = load_connect()

    # ---- 1. exposures: the ConnectEndpoint SDL is the authority ----
    sdl_keys = sdl_fields("ConnectEndpoint")
    check(bool(sdl_keys), "the SDL still declares the ConnectEndpoint type")
    declared = mapping_block("EXPOSURE_SERVER_FIELDS")
    check(sdl_keys == set(declared),
          "every ConnectEndpoint field is mapped, and nothing else "
          f"(sdl={sorted(sdl_keys)} mapped={sorted(declared)})")
    local_exposure = set(declared.values())
    row_fields = type_fields("ConnectEndpointRow")
    check(local_exposure <= row_fields,
          f"ConnectEndpointRow declares every mapped field ({sorted(local_exposure - row_fields)} missing)")
    # The screen's own row is grouped per exposure, and it carries no secret either.
    fields = type_fields("ConnectExposure")
    check(not ({"address", "psk", "presharedkey", "join_token", "lease_token"} & fields),
          "ConnectExposure carries no address, key or token field")
    check("address" not in sdl_keys,
          "the SDL has no address field, because the address never reaches the database")

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

    # ---- 3. the address cannot reach a rendered row ----
    # The strongest form of the WP09 rule: since slice 6 there is no fixture left, so no
    # tailcat-shaped string exists anywhere in the agent surface — not in a type, not in a seed, not
    # in a screen. A future fixture that reintroduces one fails here, by name.
    offenders = []
    for path in sorted(AGENTS_SRC.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if connect.TAILCAT_ADDRESS_RE.search(path.read_text()):
            offenders.append(path.name)
    check(not offenders,
          f"no tailcat-shaped string exists in frontend/src/agents ({sorted(offenders)})")
    # Prose may say the word (that is the explanation); a *field access* is what would render it.
    check(re.search(r"\.address\b|\baddress\s*[:=]", PANE.read_text()) is None,
          "the runner screen never reads an address field, so it has nothing to render")
    check("leaseTokenHash" not in PANE.read_text(),
          "the runner screen never mentions the lease token hash")
    check("connectEndpoints" in (REPO / "frontend/src/agents/adapter.ts").read_text(),
          "the adapter reads exposures from the server (`connectEndpoints`), not a fixture")

    print()
    if failures:
        print(f"{len(failures)} failed")
        return 1
    print("runner contract: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
