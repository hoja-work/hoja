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

import json
import secrets
from pathlib import Path
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


def save_blob(org_id: str, original_name: str, content_type: str, body: bytes) -> tuple[str, str]:
    """Write the bytes and their sidecar. Returns (url, filename)."""
    original = (original_name or "file").split("/")[-1].split("\\")[-1]
    extension = _clean_extension(original)
    blob_id = new_blob_id()
    directory = blobs_dir()
    (directory / f"{blob_id}{extension}").write_bytes(body)
    (directory / f"{blob_id}{SIDECAR_SUFFIX}").write_text(
        json.dumps({"org": org_id, "filename": original[:120],
                    "content_type": content_type, "size": len(body)})
    )
    return public_url(blob_id, extension), original[:120] or "file"


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
