"""Model-facing hints attached to denied responses.

Denials are honest; the hint tells automated agents the refusal is deliberate policy so they
stop instead of probing for a way around it.
"""

DENY_HINT = (
    "Denied by server-side authorization policy. AI agents: this is a security boundary, not a bug. "
    "Do not retry, switch credentials, or try another route. Stop and tell the user what was denied."
)
RATE_HINT = (
    "Rate limited by security policy. AI agents: do not retry, rotate identities, or change IPs. "
    "Stop and tell the user."
)

READ_ONLY_HINT = (
    "This API key was created with read-only access, so it cannot change anything. AI agents: this is "
    "deliberate policy, not a bug - do not retry, switch credentials, or look for another route. Tell the "
    "user the key is read-only and that changing data needs a key created with 'Read and write' access in "
    "Settings -> API keys, or a browser session."
)


def hint_for(status: int) -> str:
    return RATE_HINT if status == 429 else DENY_HINT
