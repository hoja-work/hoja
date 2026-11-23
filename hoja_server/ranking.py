"""Fractional-index keys for manual order (a port of rocicorp/fractional-indexing, base 62).

A key is an *integer part* — a head character that encodes how many digits follow
(``a``-``z`` positive and growing, ``A``-``Z`` negative and shrinking) — followed by an
optional *fraction* with no trailing ``0``. Keys compare as plain byte strings, so the
database must order them with a binary collation (SQLite's default; ``COLLATE "C"`` on
PostgreSQL, see :func:`sortable`).

``key_between(a, b)`` returns a key strictly between ``a`` and ``b`` (``None`` means the
open end). Inserting at the same spot forever only lengthens the fraction by about one
character per six inserts; prepending or appending forever only grows the integer part,
logarithmically. Nothing is ever renumbered, so a move writes exactly one row.
"""
from __future__ import annotations

from typing import Any

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ZERO = DIGITS[0]
_SMALLEST_INTEGER = "A" + _ZERO * 26


class RankError(ValueError):
    """A key that is not a valid order key, or neighbours given in the wrong order."""


def _midpoint(a: str, b: str | None) -> str:
    """A fraction strictly between fractions ``a`` and ``b`` (``b`` None: no upper bound)."""
    if b is not None and a >= b:
        raise RankError(f"{a!r} is not below {b!r}")
    if a.endswith(_ZERO) or (b is not None and b.endswith(_ZERO)):
        raise RankError("a fraction must not end in 0")
    if b is not None:
        n = 0
        while (a[n] if n < len(a) else _ZERO) == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:])
    digit_a = DIGITS.index(a[0]) if a else 0
    digit_b = DIGITS.index(b[0]) if b is not None else len(DIGITS)
    if digit_b - digit_a > 1:
        # Half rounds up, as Math.round does in rocicorp's original and in the browser port
        # (views/ranking.ts); Python's round() would round half to even and diverge.
        return DIGITS[int(0.5 * (digit_a + digit_b) + 0.5)]
    if b is not None and len(b) > 1:
        return b[:1]
    return DIGITS[digit_a] + _midpoint(a[1:], None)


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise RankError(f"invalid order key head {head!r}")


def _integer_part(key: str) -> str:
    length = _integer_length(key[0])
    if length > len(key):
        raise RankError(f"invalid order key {key!r}")
    return key[:length]


def validate(key: str) -> str:
    """Return ``key`` if it is a well-formed order key, else raise :class:`RankError`."""
    if not isinstance(key, str) or not key:
        raise RankError("an order key is a non-empty string")
    if key == _SMALLEST_INTEGER:
        raise RankError("invalid order key")
    if any(ch not in DIGITS for ch in key[1:]):
        raise RankError(f"invalid order key {key!r}")
    integer = _integer_part(key)
    if key[len(integer):].endswith(_ZERO):
        raise RankError(f"invalid order key {key!r}")
    return key


def _increment(x: str) -> str | None:
    head, digits = x[0], list(x[1:])
    carry = True
    for i in range(len(digits) - 1, -1, -1):
        if not carry:
            break
        d = DIGITS.index(digits[i]) + 1
        if d == len(DIGITS):
            digits[i] = _ZERO
        else:
            digits[i] = DIGITS[d]
            carry = False
    if not carry:
        return head + "".join(digits)
    if head == "Z":
        return "a" + _ZERO
    if head == "z":
        return None
    nxt = chr(ord(head) + 1)
    if nxt > "a":
        digits.append(_ZERO)
    else:
        digits.pop()
    return nxt + "".join(digits)


def _decrement(x: str) -> str | None:
    head, digits = x[0], list(x[1:])
    borrow = True
    for i in range(len(digits) - 1, -1, -1):
        if not borrow:
            break
        d = DIGITS.index(digits[i]) - 1
        if d == -1:
            digits[i] = DIGITS[-1]
        else:
            digits[i] = DIGITS[d]
            borrow = False
    if not borrow:
        return head + "".join(digits)
    if head == "a":
        return "Z" + DIGITS[-1]
    if head == "A":
        return None
    nxt = chr(ord(head) - 1)
    if nxt < "Z":
        digits.append(DIGITS[-1])
    else:
        digits.pop()
    return nxt + "".join(digits)


def key_between(a: str | None, b: str | None) -> str:
    """A key strictly between ``a`` and ``b``; ``None`` is the open start or end."""
    if a is not None:
        validate(a)
    if b is not None:
        validate(b)
    if a is not None and b is not None and a >= b:
        raise RankError(f"{a!r} is not below {b!r}")
    if a is None:
        if b is None:
            return "a" + _ZERO
        int_b = _integer_part(b)
        frac_b = b[len(int_b):]
        if int_b == _SMALLEST_INTEGER:
            return int_b + _midpoint("", frac_b)
        if int_b < b:
            return int_b
        res = _decrement(int_b)
        if res is None:
            raise RankError("cannot decrement any more")
        return res
    if b is None:
        int_a = _integer_part(a)
        frac_a = a[len(int_a):]
        res = _increment(int_a)
        return int_a + _midpoint(frac_a, None) if res is None else res
    int_a = _integer_part(a)
    frac_a = a[len(int_a):]
    int_b = _integer_part(b)
    frac_b = b[len(int_b):]
    if int_a == int_b:
        return int_a + _midpoint(frac_a, frac_b)
    res = _increment(int_a)
    if res is None:
        raise RankError("cannot increment any more")
    if res < b:
        return res
    return int_a + _midpoint(frac_a, None)


def keys_between(a: str | None, b: str | None, n: int) -> list[str]:
    """``n`` ascending keys strictly between ``a`` and ``b``, spread evenly."""
    if n <= 0:
        return []
    if n == 1:
        return [key_between(a, b)]
    if b is None:
        out, cur = [], a
        for _ in range(n):
            cur = key_between(cur, None)
            out.append(cur)
        return out
    if a is None:
        out, cur = [], b
        for _ in range(n):
            cur = key_between(None, cur)
            out.append(cur)
        return out[::-1]
    mid = n // 2
    c = key_between(a, b)
    return [*keys_between(a, c, mid), c, *keys_between(c, b, n - mid - 1)]


def sortable(column: Any, dialect_name: str) -> Any:
    """``column`` ordered byte-wise: PostgreSQL's default collation would sort ``a`` < ``B``."""
    if dialect_name == "postgresql":
        return column.collate("C")
    return column
