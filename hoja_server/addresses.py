"""One canonical form for an address, used everywhere an address becomes access.

An invitation is matched to a credential by string equality. That is only safe
if every writer stores the same bytes, so the comparison lives here rather than
in each call site's own idea of ``strip().lower()``.
"""

from __future__ import annotations


def canonical(value: object) -> str:
    """The comparable form of an email address. Empty string when unusable."""
    return str(value or "").strip().lower()


def same(left: object, right: object) -> bool:
    """True when two addresses denote the same mailbox for our purposes."""
    left_value, right_value = canonical(left), canonical(right)
    return bool(left_value) and left_value == right_value
