"""Issue attachment bytes: where a blob lives and what type it is.

Three callers have to agree on this, so it lives in one module instead of inside the
app factory: `POST /api/uploads` writes a blob, `GET /api/uploads/<id>` reads it back,
and the attachment writer records the resolved media type on the attachment row so a
client can pick a renderer without guessing from the URL.

Storage is the local filesystem next to the database: `uploads/blobs/` holds `<id><ext>`
plus a `<id>.meta.json` sidecar with owner/type/filename. Keeping blobs in their own
directory means a `.json` upload cannot collide with its own sidecar, and the original
`uploads/<id><ext>` layout (blob) + `uploads/<id>.json` (sidecar) is still read, so
files stored before this split keep working. There is no object store, so a blob and
its row only survive together on one host.
"""

from __future__ import annotations

import contextlib
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

#: Bytes allowed per upload. Enforced before anything touches the disk.
MAX_BYTES = 10 * 1024 * 1024

#: Prefix-allowed families: any concrete type under these is accepted.
TYPE_PREFIXES = ("image/", "video/", "audio/")

#: Exact types a browser sends for documents people actually attach.
TYPE_EXACT = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/rtf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}

#: When a browser or CLI sends no usable type (empty or octet-stream), the extension
#: is the only evidence left. Deliberately narrow: nothing that a browser would
#: execute as a document (no .html, .htm, .xhtml, .js, .mjs, .svg).
TYPE_BY_EXTENSION = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".rtf": "application/rtf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}

#: Types that may be rendered inline by the browser. Anything else — SVG in
#: particular, whose `<script>` runs on the app origin — is served as a download.
INLINE_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/tab-separated-values",
}
INLINE_PREFIXES = ("image/", "video/", "audio/")
NEVER_INLINE = {"image/svg+xml", "image/svg"}

#: Headers every served blob carries. `sandbox` neutralises a stored SVG even if one
#: slips past the disposition rule; `nosniff` stops a browser second-guessing the type.
BLOB_HEADERS = {
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "same-origin",
}


def uploads_dir() -> Path:
    from hoja_server.db import default_db_dir

    d = default_db_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def blobs_dir() -> Path:
    d = uploads_dir() / "blobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


SIDECAR_SUFFIX = ".meta.json"


def _clean_extension(name: str) -> str:
    ext = "".join(c for c in Path(name).suffix.lower() if c.isalnum() or c == ".")[:12]
    return ext if ext not in ("", ".") else ""


def new_blob_id() -> str:
    return secrets.token_hex(16)


def resolve_content_type(filename: str, declared: str) -> str | None:
    """The stored type for an upload, or None when the file is not allowed.

    A declared type wins when it is concrete and allowed. `text/*` sniffed by a
    browser for a `.csv` is trusted as-is; `application/octet-stream` (curl, older
    clients, and files the OS does not know) falls back to the extension.
    """
    declared = (declared or "").split(";")[0].strip().lower()
    if declared and declared != "application/octet-stream":
        if declared in TYPE_EXACT or any(declared.startswith(p) for p in TYPE_PREFIXES):
            return declared
        return None
    by_ext = TYPE_BY_EXTENSION.get(_clean_extension(filename))
    return by_ext


def public_url(blob_id: str, extension: str) -> str:
    """The URL an attachment row stores.

    The extension is part of the URL on purpose: a Markdown image, a copied link and
    a browser download all infer the type from it, and clients that only know the URL
    (an external link, a cached row) can still tell a screenshot from a PDF.
    """
    return f"/api/uploads/{blob_id}{extension}"


def blob_id_from_segment(segment: str) -> str | None:
    """Recover the id from `<id>` (legacy URLs) or `<id><ext>` (current URLs)."""
    base = segment.split(".", 1)[0]
    if not base or len(base) > 64:
        return None
    if not base.replace("_", "").replace("-", "").isalnum():
        return None
    return base


def meta_path(blob_id: str) -> Path:
    return blobs_dir() / f"{blob_id}{SIDECAR_SUFFIX}"


def read_meta(blob_id: str) -> dict | None:
    for path in (meta_path(blob_id), uploads_dir() / f"{blob_id}.json"):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return None


WEBP_COMPRESSIBLE_TYPES = frozenset({"image/png", "image/jpeg"})


def _parse_iso_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_expired(meta: dict | None, *, now: datetime | None = None) -> bool:
    """True when a blob's sidecar carries an ``expires_at`` timestamp in the past."""
    if not isinstance(meta, dict):
        return False
    expires = _parse_iso_utc(meta.get("expires_at"))
    if expires is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return expires <= current


def _maybe_compress_webp(
    body: bytes, content_type: str, *, quality: int = 82
) -> tuple[bytes, str, str] | None:
    """Compress PNG/JPEG bytes to WebP when Pillow is available; fall back cleanly."""
    if content_type not in WEBP_COMPRESSIBLE_TYPES:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(body)) as img:
            if img.mode not in ("RGB", "RGBA"):
                bands = img.getbands() if hasattr(img, "getbands") else ()
                img = img.convert("RGBA" if "A" in bands else "RGB")
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=quality)
            webp_bytes = buf.getvalue()
        if webp_bytes:
            return webp_bytes, "image/webp", ".webp"
    except Exception:
        return None
    return None


def save_blob_with_meta(
    org_id: str,
    original_name: str,
    content_type: str,
    body: bytes,
    *,
    ttl_days: int | None = None,
    compress_webp: bool = False,
    webp_quality: int = 82,
) -> dict[str, Any]:
    """Write blob bytes + sidecar metadata and return full stored blob details."""
    original = (original_name or "file").split("/")[-1].split("\\")[-1]
    safe_name = original[:120] or "file"
    extension = _clean_extension(original)
    stored_body, stored_type, stored_ext = body, content_type, extension

    if compress_webp:
        compressed = _maybe_compress_webp(body, content_type, quality=webp_quality)
        if compressed is not None:
            stored_body, stored_type, stored_ext = compressed

    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at: str | None = None
    if ttl_days is not None:
        if isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days < 0:
            raise ValueError("ttl_days must be a non-negative integer.")
        expires_at = (now + timedelta(days=ttl_days)).isoformat()

    blob_id = new_blob_id()
    directory = blobs_dir()
    (directory / f"{blob_id}{stored_ext}").write_bytes(stored_body)
    meta: dict[str, Any] = {
        "org": org_id,
        "filename": safe_name,
        "content_type": stored_type,
        "size": len(stored_body),
        "created_at": created_at,
    }
    if expires_at is not None:
        meta["expires_at"] = expires_at
    (directory / f"{blob_id}{SIDECAR_SUFFIX}").write_text(json.dumps(meta))
    return {
        "blob_id": blob_id,
        "url": public_url(blob_id, stored_ext),
        "filename": safe_name,
        "content_type": stored_type,
        "size": len(stored_body),
        "created_at": created_at,
        "expires_at": expires_at,
    }


def save_blob(
    org_id: str,
    original_name: str,
    content_type: str,
    body: bytes,
    *,
    ttl_days: int | None = None,
    compress_webp: bool = False,
    webp_quality: int = 82,
) -> tuple[str, str]:
    """Write the bytes and their sidecar. Returns (url, filename)."""
    info = save_blob_with_meta(
        org_id,
        original_name,
        content_type,
        body,
        ttl_days=ttl_days,
        compress_webp=compress_webp,
        webp_quality=webp_quality,
    )
    return info["url"], info["filename"]


def _iter_sidecars() -> list[tuple[str, Path, dict[str, Any]]]:
    """Collect (blob_id, sidecar_path, meta) across current and legacy upload dirs."""
    found: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[str] = set()
    bdir = blobs_dir()
    for path in bdir.glob(f"*{SIDECAR_SUFFIX}"):
        blob_id = path.name[: -len(SIDECAR_SUFFIX)]
        if not blob_id or blob_id in seen:
            continue
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            seen.add(blob_id)
            found.append((blob_id, path, meta))
    udir = uploads_dir()
    for path in udir.glob("*.json"):
        if path.name.endswith(SIDECAR_SUFFIX):
            continue
        blob_id = path.stem
        if not blob_id or blob_id in seen:
            continue
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            seen.add(blob_id)
            found.append((blob_id, path, meta))
    return found


def prune_expired_blobs(*, now: datetime | None = None) -> int:
    """Delete expired blobs and their sidecars. Returns the count of pruned blobs."""
    pruned = 0
    bdir = blobs_dir()
    udir = uploads_dir()
    for blob_id, sidecar_path, meta in _iter_sidecars():
        if not is_expired(meta, now=now):
            continue
        for candidate in list(bdir.glob(f"{blob_id}.*")) + list(udir.glob(f"{blob_id}.*")):
            if candidate == sidecar_path:
                continue
            with contextlib.suppress(OSError):
                candidate.unlink()
        with contextlib.suppress(OSError):
            sidecar_path.unlink()
        pruned += 1
    return pruned


def org_upload_bytes(org_id: str) -> int:
    """Total stored upload bytes currently on disk for an organization."""
    total = 0
    for blob_id, _sidecar_path, meta in _iter_sidecars():
        if meta.get("org") != org_id:
            continue
        size = meta.get("size")
        if isinstance(size, int) and size >= 0:
            total += size
        else:
            stored = blob_for(blob_id)
            if stored is not None:
                with contextlib.suppress(OSError):
                    total += stored.stat().st_size
    return total


def blob_for(blob_id: str) -> Path | None:
    """The stored file for an id, ignoring its (optional) extension in the URL."""
    for path in blobs_dir().glob(f"{blob_id}.*"):
        if not path.name.endswith(SIDECAR_SUFFIX):
            return path
    # Legacy layout: uploads/<id><ext>, with uploads/<id>.json as the sidecar.
    legacy = uploads_dir() / f"{blob_id}.json"
    for path in uploads_dir().glob(f"{blob_id}.*"):
        if path != legacy and not path.name.endswith(SIDECAR_SUFFIX):
            return path
    return None


def inline_disposition(content_type: str) -> bool:
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type in NEVER_INLINE:
        return False
    if content_type in INLINE_TYPES:
        return True
    return any(content_type.startswith(p) for p in INLINE_PREFIXES)


def content_type_for_url(url: str) -> str | None:
    """Media type for an attachment URL, when the URL points at one of our blobs.

    External links return None: we do not fetch them, so we cannot know.
    """
    parsed = urlparse(url or "")
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path or ""
    prefix = "/api/uploads/"
    if not path.startswith(prefix):
        return None
    blob_id = blob_id_from_segment(path[len(prefix):])
    if not blob_id:
        return None
    meta = read_meta(blob_id)
    return (meta or {}).get("content_type") or TYPE_BY_EXTENSION.get(_clean_extension(path))
