// Live, client-side workspace-address validation.
//
// The *rule* is not written here: SLUG_PATTERN, SLUG_MAX_LENGTH and
// RESERVED_SLUGS are generated from hoja_server/signup_policy.py (see
// slugPolicy.generated.ts), so the page cannot drift from the server. This file
// only mirrors the *order* of the server's checks (validate_slug) so the page
// and the gate refuse the same input with the same reason code — a full-width
// character or a trailing hyphen is caught while typing instead of after a
// request.
//
// It is deliberately pure and DOM-free: scripts/test-signup-page.cjs loads it in
// node and compares its answers with the real Python validate_slug.
import {RESERVED_SLUGS, SLUG_MAX_LENGTH, SLUG_PATTERN} from './slugPolicy.generated';

/** The three codes `validate_slug` can refuse with (SignupReason values). */
export type SlugReason = 'slug_required' | 'invalid_slug' | 'reserved_slug';

export type SlugCheck =
  | {ok: true; slug: string}
  | {ok: false; reason: SlugReason};

const SLUG_RE = new RegExp(SLUG_PATTERN);
const RESERVED = new Set<string>(RESERVED_SLUGS);

/** Same order as hoja_server.signup_policy.validate_slug, same reasons. */
export function checkSlug(raw: unknown): SlugCheck {
  if (typeof raw !== 'string' || !raw.trim()) {
    return {ok: false, reason: 'slug_required'};
  }
  const candidate = raw.trim();
  if (candidate !== candidate.toLowerCase()) {
    return {ok: false, reason: 'invalid_slug'};
  }
  if (candidate.length > SLUG_MAX_LENGTH) {
    return {ok: false, reason: 'invalid_slug'};
  }
  if (RESERVED.has(candidate)) {
    return {ok: false, reason: 'reserved_slug'};
  }
  if (candidate.startsWith('xn--')) {
    return {ok: false, reason: 'invalid_slug'};
  }
  if (candidate.includes('--')) {
    return {ok: false, reason: 'invalid_slug'};
  }
  if (!SLUG_RE.test(candidate)) {
    return {ok: false, reason: 'invalid_slug'};
  }
  return {ok: true, slug: candidate};
}
