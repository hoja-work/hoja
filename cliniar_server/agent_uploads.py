"""Text file uploads for the agent chat. Files are owner-scoped and read back only for the owner's next prompt."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

MAX_BYTES = 1_000_000
MAX_FILES_PER_MESSAGE = 5
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".ts", ".tsx", ".js", ".sql", ".yaml", ".yml", ".toml"}


def _root() -> Path:
    configured = os.environ.get("CLINIAR_AGENT_UPLOAD_DIR")
    return Path(configured) if configured else Path.home() / ".cliniar" / "agent-uploads"


def _owner_dir(org_id: str, uid: str) -> Path:
    return _root() / org_id / uid


def save_upload(org_id: str, uid: str, name: object, content: object) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if not isinstance(content, str):
        raise ValueError("content must be text")
    base = Path(name.strip()).name
    if Path(base).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(f"{base}: only text files are supported")
    size = len(content.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(f"{base}: file is larger than 1 MB")
    upload_id = uuid.uuid4().hex
    directory = _owner_dir(org_id, uid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{upload_id}.json").write_text(json.dumps({"name": base, "content": content}), encoding="utf-8")
    return {"id": upload_id, "name": base, "size": size}


def load_uploads(org_id: str, uid: str, ids: object) -> list[dict]:
    if not isinstance(ids, list) or len(ids) > MAX_FILES_PER_MESSAGE:
        raise ValueError(f"attach at most {MAX_FILES_PER_MESSAGE} files per message")
    loaded = []
    for upload_id in ids:
        if not isinstance(upload_id, str) or not upload_id.isalnum():
            raise ValueError("invalid attachment id")
        path = _owner_dir(org_id, uid) / f"{upload_id}.json"
        if not path.is_file():
            raise ValueError("attachment not found")
        loaded.append(json.loads(path.read_text(encoding="utf-8")))
    return loaded


def prompt_with_attachments(prompt: str, files: list[dict]) -> str:
    if not files:
        return prompt
    blocks = [f"--- Attached file: {f['name']} ---\n{f['content']}" for f in files]
    return "\n\n".join(blocks) + f"\n\n{prompt}"
