"""Deployment shape: how many organizations this instance is allowed to hold.

Hoja is a single-organization product. The database still keeps the
``organization`` table and every ``organization_id`` column, because the
change_event triggers and the whole read scope depend on that invariant; what
``HOJA_SINGLE_ORG`` removes is the *capability* to create or switch organization,
not the invariant itself.
"""

from __future__ import annotations

import os

SINGLE_ORG_ENV = "HOJA_SINGLE_ORG"


def single_org_mode() -> bool:
    """True when this instance serves exactly one organization (the product default is off)."""
    return os.environ.get(SINGLE_ORG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def require_multi_org(operation: str) -> None:
    """Refuse a mutation that only makes sense when several organizations exist."""
    if single_org_mode():
        raise ValueError(
            f"{operation} is unavailable: this Hoja instance runs in single-organization mode "
            f"({SINGLE_ORG_ENV}=1). Everything lives in the one workspace."
        )
