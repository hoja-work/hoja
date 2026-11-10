#!/usr/bin/env python3
"""Regenerate or verify the Google Chat v1 discovery projection used by the conformance test.

The stored fixture is a *projection* of Google's own discovery document, not a hand-written
summary of the docs:

    https://chat.googleapis.com/$discovery/rest?version=v1

The projection keeps, verbatim, every method the connector calls (path, HTTP method, path and query
parameters, required flags, accepted OAuth scopes, response schema) plus the transitive response
schemas those methods return, and the `pageSize` description for each list method (the only place
Google states the default and maximum page size). It also records the whole-document sha256 and the
document revision, so a drift check can tell "Google changed the document" from "someone edited the
fixture".

Usage:
    python3 scripts/check_chat_discovery.py --refresh   # re-fetch and rewrite the fixture
    python3 scripts/check_chat_discovery.py --check     # re-fetch and fail if Google's doc moved

`--check` needs network access and is meant for a scheduled job or a maintainer's release pass; the
pytest suite runs offline against the stored projection. Nothing here is a dependency of the
connector at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "https://chat.googleapis.com/$discovery/rest?version=v1"


def _fixture_path() -> Path:
    """Find the connectors test fixtures dir from either repository layout (app/ prefix or not)."""
    for base in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
        candidate = base / "tests" / "connectors" / "fixtures"
        if candidate.is_dir():
            return candidate / "chat_v1_discovery.json"
    raise SystemExit("tests/connectors/fixtures not found above " + __file__)


FIXTURE = _fixture_path()

# The response schemas the connector parses, plus the objects nested directly inside them. The
# card/widget schemas a message may also carry are not read by any tool here, so they are not
# recorded: this projection describes the contract the client depends on, verbatim, not the whole
# API surface.
SCHEMAS = [
    "ListSpacesResponse",
    "Space",
    "MembershipCount",
    "ListMessagesResponse",
    "Message",
    "Thread",
    "User",
    "ListMembershipsResponse",
    "Membership",
    "Group",
]

# The methods the connector calls. `spaces.get` and the write methods are deliberately absent.
METHODS = {
    "spaces.list": ("spaces", "list"),
    "spaces.messages.list": ("spaces", "messages", "list"),
    "spaces.messages.get": ("spaces", "messages", "get"),
    "spaces.members.list": ("spaces", "members", "list"),
}


def canonical_sha256(doc: dict) -> str:
    """Content hash of the document, independent of key order.

    Google serves the same discovery document with object keys in a different order on each
    request (measured: two fetches, different bytes, equal objects), so hashing the raw response
    would report drift on every check. Hashing the sorted serialization answers the only question
    that matters: did the contract change?
    """
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch(url: str = SOURCE) -> tuple[dict, str]:
    """Return (document, canonical content hash)."""
    with urllib.request.urlopen(url, timeout=30) as response:  # nosec - fixed https URL
        doc = json.loads(response.read())
    return doc, canonical_sha256(doc)


def _method(doc: dict, path: tuple[str, ...]) -> dict:
    """Resolve a discovery method by resource path, e.g. ("spaces", "messages", "list")."""
    node = doc["resources"][path[0]]
    for name in path[1:-1]:
        node = node["resources"][name]
    return node["methods"][path[-1]]


def _schemas(doc: dict, names: list[str]) -> dict[str, dict]:
    """Record the declared schemas verbatim, including every field and its type/enum/description."""
    out: dict[str, dict] = {}
    for name in names:
        schema = doc["schemas"][name]
        fields: dict[str, dict] = {}
        for field, spec in (schema.get("properties") or {}).items():
            entry = {k: spec[k] for k in ("type", "$ref", "enum", "description") if k in spec}
            if "items" in spec:
                entry["items"] = {
                    k: spec["items"][k] for k in ("type", "$ref") if k in spec["items"]
                }
            fields[field] = entry
        out[name] = {"description": schema.get("description", ""), "properties": fields}
    return out


def project(doc: dict, digest: str, retrieved: str) -> dict:
    methods: dict[str, dict] = {}
    responses: list[str] = []
    page_notes: dict[str, str] = {}
    for name, path in METHODS.items():
        method = _method(doc, path)
        params = method.get("parameters") or {}
        response = method.get("response") or {}
        ref = response.get("$ref")
        if ref:
            responses.append(ref)
        methods[name] = {
            "flatPath": method["flatPath"],
            "httpMethod": method["httpMethod"],
            # Sorted by name: Google returns parameters in a per-request order, so an unsorted
            # projection would differ on every fetch for no reason.
            "pathParams": sorted(
                (
                    {"name": key, "required": bool(spec.get("required"))}
                    for key, spec in params.items()
                    if spec.get("location") == "path"
                ),
                key=lambda item: item["name"],
            ),
            "queryParams": sorted(
                (
                    {"name": key, "required": bool(spec.get("required")), "type": spec.get("type")}
                    for key, spec in params.items()
                    if spec.get("location") == "query"
                ),
                key=lambda item: item["name"],
            ),
            "scopes": sorted(method.get("scopes") or []),
            "response": ref,
        }
        if "pageSize" in params:
            page_notes[name] = params["pageSize"]["description"]
    return {
        "source": SOURCE,
        "retrieved": retrieved,
        "revision": doc.get("revision"),
        "documentCanonicalSha256": digest,
        "methods": methods,
        "pageSizeNotes": page_notes,
        "schemas": _schemas(doc, SCHEMAS),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--refresh", action="store_true", help="re-fetch and rewrite the fixture")
    group.add_argument("--check", action="store_true", help="fail if Google's document moved")
    args = parser.parse_args(argv)

    doc, digest = fetch()
    retrieved = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fresh = project(doc, digest, retrieved)

    if args.refresh:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n")
        print(f"wrote {FIXTURE} revision={fresh['revision']} sha256={digest}")
        return 0

    stored = json.loads(FIXTURE.read_text())
    drift = []
    if stored.get("revision") != fresh["revision"]:
        drift.append(f"revision {stored.get('revision')} -> {fresh['revision']}")
    if stored.get("documentCanonicalSha256") != fresh["documentCanonicalSha256"]:
        drift.append("document content changed")
    # Compare everything that is not provenance.
    for key in ("methods", "pageSizeNotes", "schemas"):
        if stored.get(key) != fresh.get(key):
            drift.append(f"{key} changed")
    if drift:
        print("Google Chat discovery document moved: " + "; ".join(drift))
        print("Re-run with --refresh and review the diff before updating the client.")
        return 1
    print(f"OK revision={fresh['revision']} sha256={fresh['documentCanonicalSha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
