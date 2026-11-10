#!/usr/bin/env python
"""HOJA-288 schema compatibility gate.

The rename must be ADDITIVE. This proves it with introspection instead of trust.
  --snapshot PATH   write a normalized introspection snapshot (the "before" half)
  --compare A B     fail if B removed or retyped anything present in A
Env: PYTHONPATH must reach the repo; run inside the repo root.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def normalize(schema) -> dict:
    from graphql import introspection_from_schema
    intr = introspection_from_schema(schema)
    out: dict = {"types": {}, "query": {}, "mutation": {}}
    for t in intr["__schema"]["types"]:
        if t["name"].startswith("__"):
            continue
        entry: dict = {"kind": t["kind"]}
        fields = {}
        for f in (t.get("fields") or []):
            fields[f["name"]] = {
                "type": render_type(f["type"]),
                "args": {a["name"]: render_type(a["type"]) for a in (f.get("args") or [])},
                "deprecated": f.get("isDeprecated", False),
                "reason": f.get("deprecationReason"),
            }
        if fields:
            entry["fields"] = fields
        if t.get("inputFields"):
            entry["inputFields"] = {i["name"]: render_type(i["type"]) for i in t["inputFields"]}
        if t.get("enumValues"):
            entry["enumValues"] = [e["name"] for e in t["enumValues"]]
        out["types"][t["name"]] = entry
    q = intr["__schema"].get("queryType")
    m = intr["__schema"].get("mutationType")
    out["query"] = q["name"] if q else None
    out["mutation"] = m["name"] if m else None
    return out

def render_type(t) -> str:
    if t.get("kind") == "NON_NULL":
        return render_type(t["ofType"]) + "!"
    if t.get("kind") == "LIST":
        return "[" + render_type(t["ofType"]) + "]"
    return t["name"]

def diff(before: dict, after: dict) -> list[str]:
    problems: list[str] = []
    for tname, bt in before["types"].items():
        at = after["types"].get(tname)
        if at is None:
            problems.append(f"REMOVED type {tname}")
            continue
        for fname, bf in (bt.get("fields") or {}).items():
            af = (at.get("fields") or {}).get(fname)
            if af is None:
                problems.append(f"REMOVED field {tname}.{fname}")
            elif af["type"] != bf["type"]:
                problems.append(f"RETYPED {tname}.{fname}: {bf['type']} -> {af['type']}")
            else:
                for aname, atyp in bf["args"].items():
                    if aname not in af["args"]:
                        problems.append(f"REMOVED arg {tname}.{fname}({aname})")
                    elif af["args"][aname] != atyp:
                        problems.append(f"RETYPED arg {tname}.{fname}({aname})")
        if "inputFields" in bt:
            for iname, ityp in bt["inputFields"].items():
                a_ = (at.get("inputFields") or {}).get(iname)
                if a_ is None:
                    problems.append(f"REMOVED input field {tname}.{iname}")
                elif a_ != ityp:
                    problems.append(f"RETYPED input field {tname}.{iname}")
        if "enumValues" in bt:
            for ev in bt["enumValues"]:
                if ev not in (at.get("enumValues") or []):
                    problems.append(f"REMOVED enum value {tname}.{ev}")
    return problems

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot")
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args()

    from hoja_server.resolvers import build_schema
    snap = normalize(build_schema())

    if a.snapshot:
        p = Path(a.snapshot); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
        nfields = sum(len(t.get("fields") or {}) for t in snap["types"].values())
        print(f"snapshot -> {p}  types={len(snap['types'])} fields={nfields}")
        return 0

    if a.compare:
        before = json.loads(Path(a.compare[0]).read_text())
        problems = diff(before, snap)
        if problems:
            print(f"COMPAT GATE FAILED — {len(problems)} breaking change(s):")
            for p_ in problems:
                print("  -", p_)
            return 1
        added = sorted(set(snap["types"]) - set(before["types"]))
        print(F"COMPAT GATE PASSED — additive only. new types: {len(added)}")
        if added:
            print("  " + ", ".join(added[:20]) + (" ..." if len(added) > 20 else ""))
        return 0

    ap.error("pass --snapshot or --compare")
    return 2

if __name__ == "__main__":
    sys.exit(main())
