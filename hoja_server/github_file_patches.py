"""On-demand loading of one file's patch from a linked pull request (HOJA-513).

The bulk enrichment (scripts/enrich-github-link-files.sh) stores every file row but
drops patches over its caps, marking each drop with an explicit reason. This module is
the other half: when a user opens one of those files, the server fetches that file's
patch from GitHub on demand and caches it back into `github_link.files` under
``cached: true``, within the same per-patch cap the bulk pass uses.

Every answer is explicit. A file whose patch cannot be had comes back with a stable
reason code — never a silent null:

* ``over_cap``       the patch exists but is longer than PATCH_LIMIT characters
* ``github_omitted`` GitHub itself sends no patch (binary blob, a diff it refuses to
                     render, the empty blob, or a pure rename)
* ``not_in_pull``    the path is not among the pull request's files
* ``head_moved``     the caller's sha is not the sha the link row holds

The decisions (`classify_fetched`, `patch_fits`, `result_for`) are pure so the reason
paths are tested without a network, exactly like the review actions.
"""

from __future__ import annotations

from typing import Any

# The same per-patch cap the bulk enrichment enforces: on demand is not a way around
# the cap, it is a way to load what the cap permits, one file at a time.
PATCH_LIMIT = 20000

# git's empty blob: no content, no diff, and "binary" would be a lie.
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

# Stable reason codes the UI branches on.
OVER_CAP = "over_cap"
GITHUB_OMITTED = "github_omitted"
NOT_IN_PULL = "not_in_pull"
HEAD_MOVED = "head_moved"


def is_binary_bytes(data: bytes) -> bool:
    """Git's own heuristic: a NUL byte in the first 8000 bytes, or invalid UTF-8."""
    if b"\0" in data[:8000]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def patch_fits(patch: str | None) -> bool:
    """Would the bulk pass store this patch? On demand answers the same way."""
    return isinstance(patch, str) and len(patch) <= PATCH_LIMIT


def classify_fetched(entry: dict[str, Any], *, binary: bool = False,
                     blob_size: int = 0) -> dict[str, Any] | None:
    """The explicit marker for a fetched entry whose patch is not loadable.

    Mirrors the bulk script's `classify_omission` so the two writers can never
    disagree about what a null patch means. Returns None when the patch is present and
    fits — there is nothing to explain.
    """
    patch = entry.get("patch")
    if isinstance(patch, str) and len(patch) > PATCH_LIMIT:
        return {"oversize": True, "size": len(patch)}
    if patch is not None:
        return None
    status = str(entry.get("status") or "modified")
    changes = int(entry.get("changes") or 0)
    if status == "renamed" and changes == 0:
        return {"unchanged": True}
    if str(entry.get("sha") or "") == EMPTY_BLOB:
        return {"empty": True}
    if binary:
        return {"binary": True}
    # Text, and GitHub still sends no patch: it refuses to render the diff.
    return {"oversize": True, "size": blob_size}


def result_for(path: str, patch: str | None, marker: dict[str, Any] | None,
               *, cached: bool = False,
               reason: str | None = None) -> dict[str, Any]:
    """The GraphQL payload for one load, explicit about every absent patch."""
    # None means "not claimed", never "known to be false": a load that found a patch
    # claims no marker at all, exactly like a stored entry from before HOJA-513.
    out: dict[str, Any] = {
        "path": path, "patch": patch, "binary": None, "oversize": None,
        "oversizeSize": None, "empty": None, "unchanged": None,
        "cached": True if cached else None, "reason": reason,
    }
    if marker:
        for key in ("binary", "oversize", "empty", "unchanged"):
            if marker.get(key):
                out[key] = True
        if marker.get("oversize"):
            out["oversizeSize"] = int(marker.get("size") or 0)
    return out


async def load_file_patch(writes: Any, repository: str, number: int, path: str,
                          *, stored_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch one file's entry from the pull request and decide what can be shown.

    Returns the entry, the patch when it fits the cap, the marker explaining an absent
    patch, and a stable reason code. The stored entry (written by the bulk pass, which
    verified every omission against the blob) is the authority for *classification*;
    the fresh fetch is the authority for the *patch*. The caller owns the cache write,
    so this stays free of storage concerns.
    """
    entries = await writes.fetch_pull_files(repository, number)
    wanted = (path or "").strip()
    entry = next((e for e in entries if str(e.get("filename") or "") == wanted), None)
    if entry is None:
        return {"entry": None, "patch": None, "marker": None, "reason": NOT_IN_PULL}
    patch = entry.get("patch")
    fresh_over_cap = isinstance(patch, str) and len(patch) > PATCH_LIMIT
    marker = classify_fetched(entry)
    if fresh_over_cap:
        patch = None
    if patch is not None:
        return {"entry": entry, "patch": patch, "marker": None, "reason": None}
    # No loadable patch. When the omission is one the bulk pass already verified against
    # the blob (binary, empty, pure rename — or a text diff GitHub refuses to render),
    # its stored marker says so with evidence; prefer it over the unverified default.
    if marker is not None and marker.get("oversize") and isinstance(stored_entry, dict):
        for key in ("binary", "empty", "unchanged"):
            if stored_entry.get(key) is True:
                marker = {key: True}
                break
        else:
            if stored_entry.get("oversize") is True and not fresh_over_cap:
                marker = {"oversize": True,
                          "size": int(stored_entry.get("size") or 0) or marker.get("size")}
    reason = OVER_CAP if fresh_over_cap else GITHUB_OMITTED
    return {"entry": entry, "patch": None, "marker": marker, "reason": reason}


__all__ = [
    "EMPTY_BLOB",
    "GITHUB_OMITTED",
    "HEAD_MOVED",
    "NOT_IN_PULL",
    "OVER_CAP",
    "PATCH_LIMIT",
    "classify_fetched",
    "is_binary_bytes",
    "load_file_patch",
    "patch_fits",
    "result_for",
]
