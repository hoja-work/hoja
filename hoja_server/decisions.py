"""Decisions: the question an agent asks and the human's answer (docs/decisions.md).

Two responsibilities live here and nowhere else, mirroring ``views.py``:

* ``parse_options`` — the option-list grammar, so a malformed option is refused before a
  row is written and the writer stores exactly what the parser returned.
* ``answer_for`` — how an answer maps onto the stored ``chosen_option``/``answer`` pair,
  including the idempotent replay rule.

Nothing in this module reads or writes the database.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

STATUSES = ("open", "answered", "canceled")
OPEN = "open"
ANSWERED = "answered"
CANCELED = "canceled"

MAX_QUESTION = 500
MAX_OPTIONS = 20
MAX_LABEL = 120
MAX_OPTION_DESCRIPTION = 280
MAX_MEDIA_URL = 2048
MAX_CONTEXT = 10_000
MAX_ANSWER = 5_000
MAX_AGENT = 200
MAX_OPEN_PER_ISSUE = 20

MEDIA_TYPES = ("image", "video", "link")
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg"})
_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".m4v", ".ogv"})

_SLUG = re.compile(r"[^a-z0-9]+")


class DecisionError(ValueError):
    """A decision payload the caller can fix; ``field`` is the path that failed."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class AlreadyAnsweredError(DecisionError):
    """An answered decision received a *different* answer; the first one stands."""


def _text(value: Any, name: str, *, limit: int, required: bool) -> str | None:
    if value is None:
        if required:
            raise DecisionError(f"{name} is required.", name)
        return None
    if not isinstance(value, str):
        raise DecisionError(f"{name} must be a string.", name)
    text = value.strip()
    if required and not text:
        raise DecisionError(f"{name} must not be empty.", name)
    if len(text) > limit:
        raise DecisionError(f"{name} must be at most {limit} characters.", name)
    return text or None


def parse_question(raw: Any) -> str:
    question = _text(raw, "question", limit=MAX_QUESTION, required=True)
    assert question is not None
    return question


def parse_context(raw: Any) -> str | None:
    return _text(raw, "context", limit=MAX_CONTEXT, required=False)


def parse_agent(raw: Any) -> str | None:
    """The agent/session identity that asked; free-form, kept verbatim for the poller."""
    return _text(raw, "agent", limit=MAX_AGENT, required=False)


def _key_for(label: str, index: int) -> str:
    slug = _SLUG.sub("-", label.casefold()).strip("-")[:40]
    return f"{slug or 'option'}-{index + 1}"


def validate_media_url(raw: Any, field: str) -> str | None:
    """Validate an optional media URL: relative `/api/uploads/...` or `http(s)://...` only."""
    url = _text(raw, field, limit=MAX_MEDIA_URL, required=False)
    if url is None:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise DecisionError(f"{field} contains invalid control characters.", field)
    parsed = urlparse(url)
    relative_upload = (
        not parsed.scheme
        and not parsed.netloc
        and (parsed.path or "").startswith("/api/uploads/")
        and ".." not in (parsed.path or "").split("/")
    )
    if relative_upload:
        return url
    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https") and parsed.netloc and not parsed.username and not parsed.password:
        return url
    raise DecisionError(
        f"{field} must be a relative /api/uploads/... path or an http(s):// URL.",
        field,
    )


def infer_media_type(url: str) -> str:
    """Infer 'image', 'video', or 'link' from a media URL."""
    parsed = urlparse(url)
    ext = PurePosixPath(parsed.path or "").suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if not parsed.scheme and not parsed.netloc and (parsed.path or "").startswith("/api/uploads/"):
        from hoja_server import uploads

        ctype = uploads.content_type_for_url(url) or ""
        if ctype.startswith("image/"):
            return "image"
        if ctype.startswith("video/"):
            return "video"
    return "link"


def parse_media_type(raw: Any, field: str) -> str | None:
    text = _text(raw, field, limit=32, required=False)
    if text is None:
        return None
    lowered = text.lower()
    if lowered not in MEDIA_TYPES:
        raise DecisionError(f"{field} must be one of {list(MEDIA_TYPES)}.", field)
    return lowered


def parse_options(raw: Any) -> list[dict]:
    """Normalize the option list to ``[{key, label, description?, mediaUrl?, mediaType?}]``.

    A missing or empty list is legitimate: the answer is then free-form. Keys default to a
    slug of the label plus its position, so a caller may name only labels and still get
    stable keys back to choose from.
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise DecisionError(f"options must be valid JSON ({error.msg}).", "options") from error
    if not isinstance(raw, list):
        raise DecisionError("options must be a list.", "options")
    if len(raw) > MAX_OPTIONS:
        raise DecisionError(f"A decision offers at most {MAX_OPTIONS} options.", "options")

    options: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        path = f"options[{index}]"
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            raise DecisionError(f"{path} must be an object with a label.", path)
        label = _text(item.get("label"), f"{path}.label", limit=MAX_LABEL, required=True)
        assert label is not None
        description = _text(item.get("description"), f"{path}.description",
                            limit=MAX_OPTION_DESCRIPTION, required=False)
        key = _text(item.get("key"), f"{path}.key", limit=MAX_LABEL, required=False)
        key = key or _key_for(label, index)
        if key in seen:
            raise DecisionError(f"{path}.key '{key}' is used twice.", f"{path}.key")
        seen.add(key)
        raw_media_url = item.get("mediaUrl") if "mediaUrl" in item else item.get("media_url")
        raw_media_type = item.get("mediaType") if "mediaType" in item else item.get("media_type")
        media_url = validate_media_url(raw_media_url, f"{path}.mediaUrl")
        media_type = parse_media_type(raw_media_type, f"{path}.mediaType")
        if media_type and not media_url:
            raise DecisionError(f"{path}.mediaType requires mediaUrl.", f"{path}.mediaType")
        option: dict[str, Any] = {"key": key, "label": label}
        if description:
            option["description"] = description
        if media_url:
            option["mediaUrl"] = media_url
            option["mediaType"] = media_type or infer_media_type(media_url)
        options.append(option)
    return options


def dump_options(options: list[dict]) -> str:
    return json.dumps(options, separators=(",", ":"))


def load_options(stored: str | None) -> list[dict]:
    if not stored:
        return []
    try:
        parsed = json.loads(stored)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def resolve_option(options: list[dict], raw: Any) -> str | None:
    """Map the caller's choice (a key or a label) onto the stored option key."""
    choice = _text(raw, "option", limit=MAX_LABEL, required=False)
    if choice is None:
        return None
    for option in options:
        if choice in (option.get("key"), option.get("label")):
            return option["key"]
    raise DecisionError(
        f"option {choice!r} is not one of {[o.get('key') for o in options]}.", "option")


def answer_for(options: list[dict], *, option: Any, answer: Any) -> tuple[str | None, str]:
    """Return the ``(chosen_option, answer)`` pair to store, or refuse an incomplete answer.

    With options, the chosen key is required and the text defaults to the option's label, so
    a UI that sends only the click still records what the human chose. Without options the
    text is required and ``chosen_option`` stays ``None``.
    """
    chosen = resolve_option(options, option)
    text = _text(answer, "answer", limit=MAX_ANSWER, required=not options)
    if options:
        if chosen is None:
            raise DecisionError(
                f"option is required; choose one of {[o.get('key') for o in options]}.", "option")
        if text is None:
            text = next(o["label"] for o in options if o["key"] == chosen)
        return chosen, text
    if text is None:
        raise DecisionError("answer is required when a decision offers no options.", "answer")
    return None, text


def is_replay(existing: dict, *, chosen_option: str | None, answer: str) -> bool:
    """True when an answer repeats the one already stored (an idempotent retry)."""
    stored_option = existing.get("chosen_option")
    return stored_option == chosen_option and (existing.get("answer") or "") == answer
