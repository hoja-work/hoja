"""Slug and name policy for a new workspace — a mirror of the provisioner's rules.

The provisioner (``Hoja-Cloud/scripts/hoja-tenant``) is the authority: it refuses
a slug that is not a DNS label, a reserved name, or an existing tenant. The gate
must refuse the same inputs *earlier*, with a better message, or a stranger gets
as far as the provisioning queue before learning "www" is taken.

``RESERVED_SLUGS`` and ``SLUG_RE`` below are copied verbatim from
``Hoja-Cloud/scripts/hoja-tenant``; ``tests/test_signup_gate.py`` asserts the
parity (and the copy is checked against the script when that repo is present).
"""

from __future__ import annotations

import re

#: Names that already mean something on the base domain (site, API, mail, edge).
#: A tenant may never take one — copied from Hoja-Cloud/scripts/hoja-tenant.
RESERVED_SLUGS = frozenset({
    "www",
    "app",
    "api",
    "admin",
    "mail",
    "smtp",
    "imap",
    "pop",
    "webmail",
    "ns1",
    "ns2",
    "autodiscover",
    "autoconfig",
    "status",
    "docs",
    "blog",
    "support",
    "help",
    "billing",
    "auth",
    "login",
    "sso",
    "cdn",
    "assets",
    "static",
    "traefik",
    "dashboard",
})

#: Same expression as the provisioner: one DNS label, no leading/trailing hyphen.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

SLUG_MAX_LENGTH = 63


class SlugRefused(ValueError):
    """A slug the provisioner would refuse. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def validate_slug(slug: object) -> str:
    """Return the normalised slug, or raise :class:`SlugRefused`.

    Order matters only for the message a person sees; every check refuses.
    """
    if not isinstance(slug, str) or not slug.strip():
        raise SlugRefused("slug_required", "Pick a short name for your workspace.")
    candidate = slug.strip()
    if candidate != candidate.lower():
        raise SlugRefused("invalid_slug", "Use lowercase letters, digits and hyphens only.")
    if len(candidate) > SLUG_MAX_LENGTH:
        raise SlugRefused(
            "invalid_slug",
            f"{candidate!r} is {len(candidate)} characters; a DNS label is at most "
            f"{SLUG_MAX_LENGTH}.",
        )
    if candidate in RESERVED_SLUGS:
        raise SlugRefused(
            "reserved_slug",
            f"{candidate!r} is reserved: it already names something on this domain.",
        )
    if candidate.startswith("xn--"):
        raise SlugRefused("invalid_slug", "That looks like a punycode label; pick a plain one.")
    if "--" in candidate:
        raise SlugRefused(
            "invalid_slug",
            f"{candidate!r} has a double hyphen; pick e.g. {candidate.replace('--', '-')}.",
        )
    if not SLUG_RE.match(candidate):
        raise SlugRefused(
            "invalid_slug",
            f"{candidate!r} is not a DNS label: lowercase a-z, digits and inner hyphens only, "
            "no leading or trailing hyphen.",
        )
    return candidate
