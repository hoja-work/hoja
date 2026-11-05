// The one request the sign-up page makes: POST /api/signup.
//
// Kept out of the component and free of React/DOM so it can be tested directly
// (scripts/test-signup-page.cjs injects a fake `fetcher`): what it sends, and how
// it turns the gate's {ok, state, reason, message} into something a person reads.
import {describeReason} from './reasonCopy';

export const SIGNUP_ENDPOINT = '/api/signup';

/** The subset of `fetch` this module needs, so a test can supply its own. */
export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export type SignupResult =
  | {kind: 'queued'; slug: string; message: string; alreadyQueued: boolean}
  | {
      kind: 'refused';
      reason: string;
      title: string;
      message: string;
      /** The token is spent or invalid: solve the challenge again. */
      retryChallenge: boolean;
      /** The Google session is gone: sign in again. */
      retrySignIn: boolean;
    }
  | {kind: 'unreachable'; message: string};

/** Refusals that invalidate the Turnstile token (it is single use). */
const RETRY_CHALLENGE = new Set(['turnstile_missing', 'turnstile_failed', 'turnstile_replayed']);

/** Refusals that only a different (or renewed) Google session can fix. */
const RETRY_SIGN_IN = new Set(['not_authenticated', 'domain_not_allowed']);

type SignupBody = {
  ok?: unknown;
  reason?: unknown;
  message?: unknown;
  slug?: unknown;
  alreadyEligible?: unknown;
};

/**
 * Send one workspace request.
 *
 * `turnstile_token` (the server accepts `turnstileToken` too) carries the token
 * the widget handed us; the cookie session is sent with `credentials: 'include'`
 * because that is the google_verified half of the gate. Never throws: a network
 * failure is a result, so the caller always has something to render.
 */
export async function submitSignup({
  slug,
  token,
  fetcher,
}: {
  slug: string;
  token: string;
  fetcher?: FetchLike;
}): Promise<SignupResult> {
  const send = fetcher ?? ((input: string, init: RequestInit) => fetch(input, init));
  let response: Response;
  try {
    response = await send(SIGNUP_ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({slug, turnstile_token: token}),
    });
  } catch {
    return {
      kind: 'unreachable',
      message: 'We could not reach Hoja. Check your connection and try again.',
    };
  }

  let body: SignupBody | null = null;
  try {
    const parsed: unknown = await response.json();
    body = parsed && typeof parsed === 'object' ? (parsed as SignupBody) : null;
  } catch {
    body = null;
  }
  if (!body) {
    // Not the gate's JSON (a proxy page, an HTML error): say what happened,
    // never a bare "an error occurred".
    return {
      kind: 'unreachable',
      message: `Hoja answered with HTTP ${response.status} and no readable body. Try again in a moment.`,
    };
  }

  const serverMessage = typeof body.message === 'string' ? body.message : null;

  if (body.ok === true) {
    return {
      kind: 'queued',
      slug: typeof body.slug === 'string' && body.slug ? body.slug : slug,
      message: serverMessage?.trim() || 'Your workspace request is queued.',
      alreadyQueued: body.alreadyEligible === true,
    };
  }

  const reason = typeof body.reason === 'string' ? body.reason : '';
  const copy = describeReason(reason, serverMessage);
  return {
    kind: 'refused',
    reason,
    title: copy.title,
    message: copy.message,
    retryChallenge: RETRY_CHALLENGE.has(reason),
    retrySignIn: RETRY_SIGN_IN.has(reason),
  };
}
