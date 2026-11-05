// Human copy for every refusal `POST /api/signup` can answer with.
//
// The key set is not invented here: it is asserted against the server's own list,
// hoja_server.signup_gate.SIGNUP_REASON_CODES, by tests/test_signup_page.py. A
// new reason code on the server therefore fails the suite until this table has a
// human sentence for it — the page cannot ship a blank or a generic "an error
// occurred" for a refusal the gate can actually produce.
//
// A code this table does not know (an older bundle against a newer server) falls
// back to the server's own `message`, verbatim, and keeps the code visible.
export type ReasonCopy = {
  /** Short heading, used as the alert's first line. */
  title: string;
  /** What happened and what the person can do about it. */
  message: string;
};

export const REASON_COPY = {
  // -- configuration and identity ------------------------------------------
  signup_unconfigured: {
    title: 'Sign-up is not configured',
    message:
      'This deployment has no sign-up credentials, so no request can be accepted yet. Nothing is wrong on your side.',
  },
  not_authenticated: {
    title: 'Sign in with Google again',
    message:
      'Your Google sign-in is missing or has expired. Start again with “Continue with Google”.',
  },
  // -- policy ---------------------------------------------------------------
  signup_closed: {
    title: 'Sign-ups are invite-only',
    message:
      'Hoja is not opening new workspaces yet. If someone invited you, open the link in that email instead of this page.',
  },
  domain_not_allowed: {
    title: 'This Google account is not accepted here',
    message:
      'This page only accepts one Google Workspace domain. Try the account your invitation named.',
  },
  // -- the address ----------------------------------------------------------
  slug_required: {
    title: 'Pick a workspace address',
    message: 'Enter the name you want your workspace to use before sending the request.',
  },
  invalid_slug: {
    title: 'That address cannot be used',
    message:
      'Use lowercase a–z, digits and single inner hyphens: no leading or trailing hyphen, no double hyphen, at most 63 characters.',
  },
  reserved_slug: {
    title: 'That address is reserved',
    message:
      'This name is reserved: it already means something on the base domain. Pick a different one.',
  },
  slug_taken: {
    title: 'That address is taken',
    message:
      'Someone already requested or runs a workspace with this name. Pick another one.',
  },
  // -- the human check ------------------------------------------------------
  turnstile_missing: {
    title: 'Finish the human check',
    message:
      'The verification challenge has not completed yet. Wait for it to finish, then send the request again.',
  },
  turnstile_failed: {
    title: 'The human check failed',
    message:
      'Cloudflare could not verify the challenge, or it was solved for another site. Reload the page and complete it again.',
  },
  turnstile_replayed: {
    title: 'That verification was already used',
    message:
      'Each verification token works once. Complete the challenge again to get a fresh one.',
  },
  // -- invitations and capacity --------------------------------------------
  invite_required: {
    title: 'An invitation is required',
    message:
      'Hoja is invite-only right now. Ask for an invitation for this Google address, then try again.',
  },
  identity_cap_exceeded: {
    title: 'This account already asked',
    message:
      'Each Google account can request one workspace. Sign in with another account to ask for a second one.',
  },
  ip_cap_exceeded: {
    title: 'Too many requests from this network',
    message:
      'Several workspaces were requested from your address recently. Try again later.',
  },
  // -- the request itself ---------------------------------------------------
  cross_site_request: {
    title: 'The request was blocked',
    message:
      'It did not come from this page on this site. Reload the page and try again.',
  },
  invalid_request: {
    title: 'The request could not be read',
    message: 'The form data was not accepted. Reload the page and try again.',
  },
  // -- not a refusal, but a member of the server's list ---------------------
  eligible: {
    title: 'Your request is queued',
    message: 'A workspace request is already recorded for this address.',
  },
} as const satisfies Record<string, ReasonCopy>;

/** Every code this table has copy for. Compared with the server's list in tests. */
export const REASON_COPY_CODES: string[] = Object.keys(REASON_COPY);

/**
 * Copy for one refusal.
 *
 * Known code: this table. Unknown code: the server's own message, which the gate
 * always sends alongside `reason`. Never blank: if there is no message either,
 * the code itself is shown so a person can quote it.
 */
export function describeReason(
  reason: string | null | undefined,
  serverMessage?: string | null,
): ReasonCopy {
  if (reason && Object.prototype.hasOwnProperty.call(REASON_COPY, reason)) {
    return REASON_COPY[reason as keyof typeof REASON_COPY];
  }
  const message = (serverMessage ?? '').trim();
  if (message) {
    return {
      title: reason ? `Sign-up was refused (${reason})` : 'Sign-up was refused',
      message,
    };
  }
  return {
    title: reason ? `Sign-up was refused (${reason})` : 'Sign-up was refused',
    message: reason
      ? `The server refused this request without a message. Quote this code when asking for help: ${reason}.`
      : 'The server refused this request without a message.',
  };
}
