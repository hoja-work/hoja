"""Which verified address may redeem an invitation sent to another address.

One function decides it, so the rule can change in one reviewed place. The rule
today is the strict one: an invitation names a mailbox, and only the Google
account that proves that same mailbox may use it. Signing in as
``jane.doe@acme.com`` with a code sent to ``jane@acme.com`` is refused with
``invite_address_mismatch``, and the refusal page says (masked) which address
the invitation went to, so the person can pick the right Google account.
"""

from __future__ import annotations

from hoja_server import addresses

#: The reason a sign-in carries when it holds a valid invitation for this
#: workspace but proved a different address than the one invited.
MISMATCH = "invite_address_mismatch"


def invite_redemption_allowed(invited_email: str | None, verified_email: str | None,
                              org_policy: dict | None = None) -> bool:
    """True when ``verified_email`` may accept an invitation sent to ``invited_email``.

    ``org_policy`` is the workspace's security policy; it is accepted so a
    per-workspace rule (for example "any address at our domain") can be added
    here without touching the callers. It does not widen anything today.
    """
    del org_policy  # reserved: no workspace may relax the exact-address rule yet
    return addresses.same(invited_email, verified_email)


def mask(address: str | None) -> str:
    """``jane@acme.com`` -> ``j***@acme.com``: enough to recognise, not to harvest."""
    value = addresses.canonical(address)
    local, _, domain = value.partition("@")
    if not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"
