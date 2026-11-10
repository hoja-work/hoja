#!/usr/bin/env python
"""HOJA-288 inventory: every "issue"-bearing API symbol, parsed from the SDL.

Grepping finds strings; this finds SYMBOLS. The difference matters because "issue"
is a substring of compounds (MyIssueActivity, IssueParentFilter, sub-issue) that must
be dispositioned individually rather than blind-renamed.

Usage:  python scripts/hoja-288-inventory.py [--sdl PATH] [--out PATH]
Exit 0 always; the artifact is the output. Unclassified symbols are listed loudly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from graphql import build_schema, GraphQLObjectType, GraphQLInputObjectType, GraphQLEnumType

ROOT = Path(__file__).resolve().parents[1]
NEEDLE = re.compile(r"issue", re.I)


def classify(kind: str, name: str) -> tuple[str, str]:
    """Return (disposition, reason). Dispositions: rename | alias | leave."""
    # The label/reporter vocabulary is a product concept with its own UI ("Labels"),
    # and "issueLabel" appears in DB tables we must not touch. Alias it, never rename in place.
    # A boolean flag on teamDelete; aliasing keeps the wire contract and reads better as deleteTasks.
    if name == "deleteIssues":
        return "alias", "boolean flag: add deleteTasks, keep deleteIssues"
    if "Label" in name:
        return "alias", "label subsystem: alias only, table issue_label stays"
    # Activity feeds describe historical events; renaming the type would break stored history.
    if "Activity" in name:
        return "alias", "stored history/event feed; alias to preserve past events"
    # Pagination wrappers carry no product meaning; alias keeps client codegen stable.
    if name.endswith("Connection"):
        return "alias", "pagination wrapper; alias so existing clients keep codegen output"
    if name.endswith("Payload"):
        return "alias", "mutation payload; alias"
    if kind == "field" and name[:1].islower():
        return "alias", "root/object field: add task* equivalent, deprecate issue*"
    if kind == "type":
        return "alias", "type: add Task alias, deprecate Issue"
    if kind == "input":
        return "alias", "input type: alias, do not rename (wire contract)"
    if kind == "input_field":
        return "alias", "input field: alias on the Task input"
    return "leave", "no product meaning; verify by hand"


def walk_gql(sdl_text: str) -> list[dict]:
    schema = build_schema(sdl_text)
    out: list[dict] = []
    for tname, t in schema.type_map.items():
        if tname.startswith("__"):
            continue
        if NEEDLE.search(tname):
            out.append({"kind": "type" if isinstance(t, (GraphQLObjectType,)) else "input",
                        "name": tname, "owner": None})
        if isinstance(t, (GraphQLObjectType, GraphQLInputObjectType)):
            for fname, f in t.fields.items():
                if NEEDLE.search(fname):
                    out.append({"kind": "field" if isinstance(t, GraphQLObjectType) else "input_field",
                                "name": fname, "owner": tname})
                for aname in getattr(f, "args", {}) or {}:
                    if NEEDLE.search(aname):
                        out.append({"kind": "arg", "name": aname, "owner": f"{tname}.{fname}"})
        if isinstance(t, GraphQLEnumType):
            for vname in t.values:
                if NEEDLE.search(vname):
                    out.append({"kind": "enum_value", "name": vname, "owner": tname})
    return out


def walk_source(roots: list[Path], pattern: str) -> dict[str, int]:
    """Raw hit counts per area, to reconcile the parsed inventory against reality."""
    counts: dict[str, int] = {}
    rx = re.compile(pattern, re.I)
    for root in roots:
        n = 0
        if root.is_file():
            n = len(rx.findall(root.read_text(errors="ignore")))
        elif root.is_dir():
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if any(s in p.parts for s in (".venv", "node_modules", "dist", ".git", "__pycache__")):
                    continue
                if p.suffix not in {".py", ".ts", ".tsx", ".js", ".cjs", ".mjs", ".css", ".md", ".html", ".json", ".yml"}:
                    continue
                n += len(rx.findall(p.read_text(errors="ignore")))
        counts[str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root)] = n
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdl", default=str(ROOT / "hoja_server/schema.graphql"))
    ap.add_argument("--out", default=str(ROOT / "artifacts/hoja-288-inventory.json"))
    args = ap.parse_args()

    sdl_text = Path(args.sdl).read_text()
    symbols = walk_gql(sdl_text)
    for s in symbols:
        s["disposition"], s["reason"] = classify(s["kind"], s["name"])

    areas = ["frontend/src", "hoja", "hoja_server", "tests", "docs", "web", "connectors", "scripts"]
    raw = walk_source([ROOT / a for a in areas if (ROOT / a).exists()], r"issue")

    unclassified = [s for s in symbols if s["disposition"] == "leave"]
    payload = {
        "issue": "HOJA-288",
        "sdl": str(Path(args.sdl).relative_to(ROOT)),
        "sdl_lines": len(sdl_text.splitlines()),
        "api_symbols_total": len(symbols),
        "by_disposition": {d: sum(1 for s in symbols if s["disposition"] == d)
                           for d in ("rename", "alias", "leave")},
        "by_kind": {k: sum(1 for s in symbols if s["kind"] == k)
                    for k in sorted({s["kind"] for s in symbols})},
        "symbols": sorted(symbols, key=lambda s: (s["kind"], s["owner"] or "", s["name"])),
        "raw_issue_hits_by_area": raw,
        "unclassified_needing_review": unclassified,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"wrote {out.relative_to(ROOT)}")
    print(f"  API symbols with 'issue': {len(symbols)}  {payload['by_disposition']}")
    print(f"  kinds: {payload['by_kind']}")
    print(f"  raw text hits by area: {raw}")
    if unclassified:
        print(f"  !! {len(unclassified)} symbols need a human disposition:")
        for s in unclassified[:20]:
            print(f"     - {s['kind']} {s['owner'] or ''}.{s['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
