/* ---- Reviews grouping: the one definition of "Ready to merge" ---------------
 *
 * HOJA-417. Linear's reviews list is grouped Ready to merge / Open / Draft, measured at
 * 1440x900 on https://linear.app/hoja/reviews: `Ready to merge 1 / Open 7 / Draft 16`,
 * 24 rows and no Merged or Closed bucket (artifacts/reviews-parity-*.json). This module is
 * the only place that decides which bucket a stored pull request belongs to; the ledger,
 * the group counts and the guard script all read it from here.
 *
 * A pull request is Ready to merge only when ALL of these hold:
 *   1. the merge state GitHub reported is `clean` (or `mergeable`), and
 *   2. the check state is `passing`, and
 *   3. the review decision is `approved`.
 *
 * Anything else is Open, a draft state is Draft before any of the above is consulted (a
 * draft with green checks is still a draft), and a merged/closed pull request is Terminal:
 * it is not part of the review queue at all, which is why the reference has no such group.
 *
 * A NULL NEVER PROVES READINESS. Every field above is nullable in the store, and a null
 * means at least two different things — "not enriched yet" and "GitHub reports nothing
 * here" — that the row itself cannot tell apart. Storing reviewRequests would let a proven
 * "no required reviews" satisfy (3); until then the gate stays closed rather than guess.
 * The measured data makes that concrete: of 29 stored pull-request links, `reviewDecision`
 * is null on all 29, so today no row can be Ready and the ledger says so instead of
 * inventing a bucket.
 */
export const REVIEW_GROUP_NAMES = ['Ready to merge', 'Open', 'Draft'] as const;
export type ReviewGroupName = typeof REVIEW_GROUP_NAMES[number];
export type ReviewBucket = ReviewGroupName | 'Terminal';

export type ReviewGateInput = {
  state?: string | null;
  mergeable?: boolean | null;
  mergeState?: string | null;
  checksState?: string | null;
  reviewDecision?: string | null;
};

export type ReviewGateResult = {
  group: ReviewBucket;
  /** Why, in one token — printed in the group's title attribute and asserted by the tests. */
  reason: string;
};

const READY_MERGE_STATES = ['clean', 'mergeable'];
const READY_CHECK_STATES = ['passing'];
const READY_REVIEW_DECISIONS = ['approved'];

const norm = (v?: string | null): string | null => {
  if (v === null || v === undefined) return null;
  const t = String(v).trim().toLowerCase();
  return t === '' ? null : t;
};

export function reviewGroupOf(link: ReviewGateInput): ReviewGateResult {
  const state = norm(link.state);
  if (state === 'merged') return { group: 'Terminal', reason: 'merged' };
  if (state === 'closed') return { group: 'Terminal', reason: 'closed' };
  if (state === 'draft') return { group: 'Draft', reason: 'draft' };

  const mergeState = norm(link.mergeState);
  if (mergeState === null) return { group: 'Open', reason: 'merge-state-unknown' };
  if (!READY_MERGE_STATES.includes(mergeState)) return { group: 'Open', reason: `merge-state-${mergeState}` };

  const checksState = norm(link.checksState);
  if (checksState === null) return { group: 'Open', reason: 'checks-unknown' };
  if (!READY_CHECK_STATES.includes(checksState)) return { group: 'Open', reason: `checks-${checksState}` };

  const reviewDecision = norm(link.reviewDecision);
  if (reviewDecision === null) return { group: 'Open', reason: 'review-unknown' };
  if (!READY_REVIEW_DECISIONS.includes(reviewDecision)) return { group: 'Open', reason: `review-${reviewDecision}` };

  // mergeable:false contradicts a clean merge state; trust the contradiction and stay Open.
  if (link.mergeable === false) return { group: 'Open', reason: 'not-mergeable' };

  return { group: 'Ready to merge', reason: 'clean+passing+approved' };
}

/** The groups in render order, each with the rows that belong to it, plus the terminal rows
 *  the queue does not list. Returning the terminal rows here rather than filtering them at
 *  the call site is deliberate: a caller cannot silently lose a pull request, because every
 *  link is accounted for in exactly one of `groups` and `terminal`. */
export function reviewGroupsOf<T extends ReviewGateInput>(links: T[]): {
  groups: { name: ReviewGroupName; items: T[] }[];
  terminal: T[];
} {
  const groups = REVIEW_GROUP_NAMES.map((name) => ({ name, items: links.filter((l) => reviewGroupOf(l).group === name) }));
  const terminal = links.filter((l) => reviewGroupOf(l).group === 'Terminal');
  return { groups, terminal };
}
