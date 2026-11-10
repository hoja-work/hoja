"""hoja_server — local, wire-compatible GraphQL backend for the hoja CLI.

Wire-compatible with the ~25 the upstream API GraphQL operations hoja actually sends.
Offline-first (SQLite per tenant), multi-tenant by token, no rate limits.
"""
from pathlib import Path

try:
    __version__ = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
except Exception:  # pragma: no cover
    __version__ = "0.0.0"
