/* Cycle metrics, defined once and tested in scripts/test-cycle-metrics.mjs.
 *
 * Vocabulary (see docs/cycle-metrics.md): every issue currently attached to a
 * cycle is in its **scope**. Issues leave scope in exactly two ways: delivered
 * (state type "completed") or canceled (state type "canceled"). Everything
 * else is still **open**. That splits the old single percentage into the three
 * numbers a cycle actually has to report:
 *
 *   deliveryRate    = completed / (scope - canceled)   how much of the work you kept did you land
 *   dropoutRate     = canceled / scope                 how much of the commitment you dropped
 *   scopeCompletion = completed / scope                how much of the original commitment you landed
 *
 * The UI shows deliveryRate next to dropoutRate, never instead of it: a cycle
 * that cancels its leftovers must not read as 100% delivered.
 *
 * Structural input type on purpose (no import of LinIssue) so the module stays
 * a leaf the test runner can load directly.
 */

export type MetricIssue = {
  state?: {type?: string | null} | null;
  estimate?: number | null;
  stateChangedAt?: string | null;
  updatedAt?: string | null;
};

export type CycleStats = {
  /** Issues currently attached to the cycle. */
  scope: number;
  /** Delivered: state type "completed". */
  completed: number;
  /** Dropped: state type "canceled". */
  canceled: number;
  /** Started but not delivered or canceled. */
  started: number;
  /** Not started, not delivered, not canceled. */
  open: number;
  /** scope - canceled: the denominator delivery is measured against. */
  kept: number;
  /** Estimate points over kept issues. */
  points: number;
  /** Estimate points delivered. */
  deliveredPoints: number;
};

export function cycleStats(issues: readonly MetricIssue[]): CycleStats {
  let started = 0, completed = 0, canceled = 0, points = 0, deliveredPoints = 0;
  for (const i of issues) {
    const type = i.state?.type;
    if (type === 'completed') completed++;
    else if (type === 'canceled') canceled++;
    else if (type === 'started') started++;
    const est = i.estimate || 0;
    if (type !== 'canceled') {
      points += est;
      if (type === 'completed') deliveredPoints += est;
    }
  }
  const scope = issues.length;
  return {
    scope, completed, canceled, started,
    open: scope - completed - canceled,
    kept: scope - canceled,
    points, deliveredPoints,
  };
}

const ratio = (num: number, den: number): number | null => (den > 0 ? num / den : null);

/** completed / kept. Null when the cycle kept nothing (every issue was canceled). */
export const deliveryRate = (s: CycleStats): number | null => ratio(s.completed, s.kept);
/** canceled / scope. Null on an empty cycle. */
export const dropoutRate = (s: CycleStats): number | null => ratio(s.canceled, s.scope);
/** completed / scope: the number the old UI showed, still the honest "did I do what I committed to". */
export const scopeCompletion = (s: CycleStats): number | null => ratio(s.completed, s.scope);

/** Whole-percent formatters; -1 style sentinels are avoided so callers must branch on null. */
export const asPct = (r: number | null): number | null => (r === null ? null : Math.round(r * 100));

/**
 * Remaining-work steps, one per day from `start` to `until`: scope minus the
 * issues that had left the remaining pool by then. Both terminal transitions
 * count — a delivered issue leaves the pool, and so does a canceled one, at the
 * moment it was canceled. Without this the line flattens at the number of
 * canceled issues and never reaches zero, which is what made canceled work look
 * like unfinished work.
 */
export function remainingSteps(
  issues: readonly MetricIssue[] | undefined,
  scope: number,
  start: number,
  until: number,
): [number, number][] {
  if (!issues || !(until > start) || !scope) return [];
  const left = issues
    .filter(i => i.state?.type === 'completed' || i.state?.type === 'canceled')
    .map(i => Date.parse(i.stateChangedAt || i.updatedAt || ''))
    .filter(t => !Number.isNaN(t))
    .sort((a, b) => a - b);
  const steps: [number, number][] = [];
  const days = Math.min(400, Math.ceil((until - start) / 864e5));
  let k = 0;
  for (let d = 0; d <= days; d++) {
    const t = Math.min(until, start + d * 864e5);
    while (k < left.length && left[k] <= t) k++;
    steps.push([t, Math.max(0, scope - k)]);
  }
  return steps;
}
