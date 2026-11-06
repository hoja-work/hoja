"""The sign-up gate: ``anonymous -> google_verified -> human_verified -> eligible``.

This module is the only place that may decide ``eligible``, and it decides on
three independent facts:

| Layer | Fact | Evidence |
| --- | --- | --- |
| Google OIDC | a verified person | a live ``signup_session`` tied to an identity recorded only after a fully validated ``id_token`` (``email_verified == true``) |
| Turnstile | a human at the keyboard | a token verified server-side *now*, with our secret, our hostname and this client's address, spent exactly once |
| Capacity/policy | a workspace we are willing to create | the mode allows it, the invite exists, the caps are not exceeded, and the slug is free and not reserved |

What it never does: create a database, a container, a route or a DNS record, or
call a provisioner. Success is one ``signup_eligibility`` row — a request the
provisioner may pick up on its own schedule.

Refusal order is cheapest-first and stable, so a caller can branch on ``reason``:

1. ``signup_unconfigured`` — the deployment has no credentials
2. ``not_authenticated`` — no live Google-verified session
3. ``signup_closed`` / ``domain_not_allowed`` — policy that costs no I/O
4. ``slug_required`` / ``invalid_slug`` / ``reserved_slug`` — pure syntax
5. ``turnstile_missing`` / ``turnstile_failed`` / ``turnstile_replayed`` — the human layer
6. the idempotent repeat (`eligible` again, no write)
7. ``invite_required``
8. ``identity_cap_exceeded`` / ``ip_cap_exceeded``
9. ``slug_taken``
10. ``eligible``

Step 4 comes before step 5 on purpose: an invalid label is refused before a
Turnstile token is spent, because refusing it costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from collections.abc import Callable

from hoja_server import turnstile as turnstile_module
from hoja_server.signup_config import SignupConfig
from hoja_server.signup_policy import SlugRefused, validate_slug
from hoja_server.signup_store import EligibilityConflict, SignupStore


class SignupState(str, Enum):
    """Where the request stands in the state machine. Never skips a step."""

    ANONYMOUS = "anonymous"
    GOOGLE_VERIFIED = "google_verified"
    HUMAN_VERIFIED = "human_verified"
    ELIGIBLE = "eligible"


class SignupReason(str, Enum):
    """Stable machine-readable refusal codes. Callers may branch on these."""

    SIGNUP_UNCONFIGURED = "signup_unconfigured"
    NOT_AUTHENTICATED = "not_authenticated"
    SIGNUP_CLOSED = "signup_closed"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    SLUG_REQUIRED = "slug_required"
    INVALID_SLUG = "invalid_slug"
    RESERVED_SLUG = "reserved_slug"
    TURNSTILE_MISSING = "turnstile_missing"
    TURNSTILE_FAILED = "turnstile_failed"
    TURNSTILE_REPLAYED = "turnstile_replayed"
    INVITE_REQUIRED = "invite_required"
    IDENTITY_CAP_EXCEEDED = "identity_cap_exceeded"
    IP_CAP_EXCEEDED = "ip_cap_exceeded"
    SLUG_TAKEN = "slug_taken"
    ELIGIBLE = "eligible"


#: Two refusals ``POST /api/signup`` adds before the gate runs: the same-origin
#: check and the body parser. They are named here, next to the enum, so the route
#: and the page share one vocabulary instead of agreeing by copy-paste.
CROSS_SITE_REQUEST = "cross_site_request"
INVALID_REQUEST = "invalid_request"

#: Every ``reason`` the sign-up endpoint can answer with — the one list the page's
#: copy map is checked against. ``eligible`` is included because it is a member of
#: :class:`SignupReason` (it is delivered as ``state``, with ``reason: null``), so
#: the tuple stays a faithful enumeration rather than a hand-curated subset.
#:
#: ``frontend/src/signup/reasonCopy.ts`` must map each entry;
#: ``tests/test_signup_page.py`` asserts the two lists are equal, so a new code
#: cannot be added here without the page being updated (and vice versa).
SIGNUP_REASON_CODES = tuple(
    reason.value for reason in SignupReason
) + (CROSS_SITE_REQUEST, INVALID_REQUEST)


_SLUG_REASON_STATUS = {
    "slug_required": (SignupReason.SLUG_REQUIRED, 422),
    "invalid_slug": (SignupReason.INVALID_SLUG, 422),
    "reserved_slug": (SignupReason.RESERVED_SLUG, 403),
}


@dataclass(frozen=True)
class SignupDecision:
    """One evaluated request. ``reason`` is ``None`` only when eligible."""

    state: SignupState
    reason: SignupReason | None
    message: str
    status_code: int
    google_verified: bool = False
    human_verified: bool = False
    eligibility: dict | None = None
    created: bool = False

    @property
    def ok(self) -> bool:
        return self.reason is None

    def as_dict(self) -> dict:
        body = {
            "ok": self.ok,
            "state": self.state.value,
            "reason": self.reason.value if self.reason else None,
            "message": self.message,
            "googleVerified": self.google_verified,
            "humanVerified": self.human_verified,
        }
        if self.eligibility:
            body.update(
                {
                    "eligibilityId": self.eligibility.get("id"),
                    "slug": self.eligibility.get("slug"),
                    "status": self.eligibility.get("status"),
                    "alreadyEligible": not self.created,
                }
            )
        return body


def _refuse(
    state: SignupState,
    reason: SignupReason,
    message: str,
    status_code: int,
    *,
    google_verified: bool = False,
    human_verified: bool = False,
) -> SignupDecision:
    return SignupDecision(
        state=state,
        reason=reason,
        message=message,
        status_code=status_code,
        google_verified=google_verified,
        human_verified=human_verified,
    )


class SignupGate:
    """Evaluates one ``POST /api/signup`` against the three layers."""

    def __init__(
        self,
        *,
        config: SignupConfig,
        store: SignupStore,
        verifier: Callable[..., turnstile_module.TurnstileResult] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.store = store
        self.verifier = verifier or turnstile_module.verify_turnstile
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(
        self,
        *,
        identity: dict | None,
        slug: object,
        turnstile_token: str | None,
        client_ip: str | None,
    ) -> SignupDecision:
        """Evaluate one request. Pure with respect to the outside world except
        for the injected Turnstile verifier and the gate's own tables."""
        config, store, now = self.config, self.store, self.clock()

        # 1. Is the surface even configured? Fail closed, never silently allow.
        if not config.configured:
            return _refuse(
                SignupState.ANONYMOUS,
                SignupReason.SIGNUP_UNCONFIGURED,
                "Sign-up is not configured on this deployment.",
                503,
            )

        # 2. google_verified: a live session whose identity came from a fully
        #    validated id_token. No session, no gate.
        if (
            not identity
            or not identity.get("google_verified_at")
            or not identity.get("email_verified")
        ):
            return _refuse(
                SignupState.ANONYMOUS,
                SignupReason.NOT_AUTHENTICATED,
                "Sign in with Google before requesting a workspace.",
                401,
            )
        google_verified = True

        # 3. Policy that costs no I/O.
        if config.mode == "closed":
            return _refuse(
                SignupState.GOOGLE_VERIFIED,
                SignupReason.SIGNUP_CLOSED,
                "New workspaces are not open yet.",
                403,
                google_verified=google_verified,
            )
        if config.google_hd and (identity.get("hosted_domain") or "") != config.google_hd:
            return _refuse(
                SignupState.GOOGLE_VERIFIED,
                SignupReason.DOMAIN_NOT_ALLOWED,
                "This sign-up page only accepts the configured Google Workspace domain.",
                403,
                google_verified=google_verified,
            )

        # 4. Slug syntax and reserved names (cheap, before any token is spent).
        try:
            candidate = validate_slug(slug)
        except SlugRefused as refused:
            reason, status = _SLUG_REASON_STATUS[refused.reason]
            return _refuse(
                SignupState.GOOGLE_VERIFIED,
                reason,
                str(refused),
                status,
                google_verified=google_verified,
            )

        # 5. human_verified: server-side siteverify, then spend the token once.
        if not turnstile_token:
            return _refuse(
                SignupState.GOOGLE_VERIFIED,
                SignupReason.TURNSTILE_MISSING,
                "Complete the human verification challenge.",
                403,
                google_verified=google_verified,
            )
        result = self.verifier(
            secret=config.turnstile_secret_key,
            token=turnstile_token,
            remote_ip=client_ip,
            expected_hostname=config.turnstile_expected_hostname,
        )
        if not result.success:
            return _refuse(
                SignupState.GOOGLE_VERIFIED,
                SignupReason.TURNSTILE_FAILED,
                "Human verification failed. Reload the page and try again.",
                403,
                google_verified=google_verified,
            )
        if not store.consume_turnstile_token(
            token=turnstile_token,
            identity_id=identity.get("identity_id"),
            client_ip=client_ip,
            hostname=result.hostname,
            now=now,
        ):
            return _refuse(
                SignupState.HUMAN_VERIFIED,
                SignupReason.TURNSTILE_REPLAYED,
                "That verification token was already used. Complete the challenge again.",
                403,
                google_verified=google_verified,
            )
        human_verified = True

        # 5b. Idempotent repeat: this identity already requested this slug. The
        #     answer is the existing row and nothing is written, so it is given
        #     before the caps — which a repeat would otherwise trip by definition.
        already = store.eligibility_by_subject_slug(identity["subject"], candidate)
        if already is not None:
            message = {
                "active": "Your workspace is ready.",
                "provisioning": "Your workspace is being created.",
                "failed": "Your workspace request needs attention.",
            }.get(str(already.get("status") or ""),
                  "Your workspace request was already queued.")
            return SignupDecision(
                state=SignupState.ELIGIBLE,
                reason=None,
                message=message,
                status_code=200,
                google_verified=google_verified,
                human_verified=human_verified,
                eligibility=already,
                created=False,
            )

        # 6. Mode=invite: the address must have an open invitation.
        invite = None
        if config.mode == "invite":
            invite = store.active_invite_for(identity.get("email") or "", now=now)
            if invite is None:
                return _refuse(
                    SignupState.HUMAN_VERIFIED,
                    SignupReason.INVITE_REQUIRED,
                    "Hoja is invite-only right now. Ask for an invitation for this address.",
                    403,
                    google_verified=google_verified,
                    human_verified=human_verified,
                )

        # 7. Caps: per identity (one workspace per Google account) and per IP.
        if store.eligibility_count(google_sub=identity["subject"]) >= config.identity_cap:
            return _refuse(
                SignupState.HUMAN_VERIFIED,
                SignupReason.IDENTITY_CAP_EXCEEDED,
                "This Google account has already requested its workspace.",
                429,
                google_verified=google_verified,
                human_verified=human_verified,
            )
        since = now - timedelta(seconds=config.ip_window_seconds)
        if (
            client_ip
            and store.eligibility_count(client_ip=client_ip, since=since) >= config.ip_cap
        ):
            return _refuse(
                SignupState.HUMAN_VERIFIED,
                SignupReason.IP_CAP_EXCEEDED,
                "Too many workspaces requested from this network recently. Try again later.",
                429,
                google_verified=google_verified,
                human_verified=human_verified,
            )

        # 8. Is the name free? Any queued request or provisioned tenant claims it.
        if candidate in store.taken_slugs(config.tenant_registry):
            return _refuse(
                SignupState.HUMAN_VERIFIED,
                SignupReason.SLUG_TAKEN,
                f"{candidate!r} is already taken. Pick another name.",
                409,
                google_verified=google_verified,
                human_verified=human_verified,
            )

        # 9. eligible: record the request. Nothing is provisioned here.
        try:
            row, created = store.record_eligibility(
                identity=identity,
                slug=candidate,
                client_ip=client_ip or "unknown",
                turnstile_hostname=result.hostname,
                now=now,
            )
        except EligibilityConflict as conflict:
            reason = (
                SignupReason.SLUG_TAKEN
                if conflict.reason == "slug_taken"
                else SignupReason.NOT_AUTHENTICATED
            )
            return _refuse(
                SignupState.HUMAN_VERIFIED,
                reason,
                str(conflict),
                409,
                google_verified=google_verified,
                human_verified=human_verified,
            )
        if invite is not None:
            store.consume_invite(
                invite["id"],
                identity.get("identity_id") or identity["id"],
                now=now,
            )
        return SignupDecision(
            state=SignupState.ELIGIBLE,
            reason=None,
            message=(
                "Your workspace request is queued."
                if created
                else "Your workspace request was already queued."
            ),
            status_code=201 if created else 200,
            google_verified=google_verified,
            human_verified=human_verified,
            eligibility=row,
            created=created,
        )
