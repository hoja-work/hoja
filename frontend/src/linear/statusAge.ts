/* Single source of truth for "how long has this issue held its current status?".
 *
 * Every surface that shows status age (the My issues StatusPicker popover and the
 * Linear-style list rows) must use this module. `stateChangedAt` is the server's
 * dedicated timestamp: the backend moves it only when the state actually changes
 * (cliniar_server/writer.py), so editing a title or assignee does not reset it.
 *
 * Fallbacks exist only for data written before that column existed:
 * stateChangedAt -> updatedAt -> createdAt. An unparseable timestamp is "Unknown"
 * rather than a fake duration.
 */

export type StatusAgeIssue = {
  state?: {name?: string; type?: string; color?: string} | null;
  stateChangedAt?: string | null;
  updatedAt?: string | null;
  createdAt?: string | null;
};

export function formatDuration(ms: number): string {
  const safe = Math.max(0, ms);
  const m = Math.floor(safe / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return 'just now';
}

/** Milliseconds since the current status began, or null when no timestamp parses. */
export function millisInStatus(issue: StatusAgeIssue, now: number = Date.now()): number | null {
  const since = issue.stateChangedAt || issue.updatedAt || issue.createdAt;
  if (!since) return null;
  const t = new Date(since).getTime();
  if (Number.isNaN(t)) return null;
  // Clock skew should read as "just now", never as a negative duration.
  return Math.max(0, now - t);
}

export function timeInStatus(issue: StatusAgeIssue, now: number = Date.now()): string {
  const ms = millisInStatus(issue, now);
  return ms === null ? 'Unknown' : formatDuration(ms);
}

/** The sentence shown on hover/focus, e.g. "In In Progress for 3d 4h". */
export function statusAgeLabel(issue: StatusAgeIssue, now: number = Date.now()): string {
  return `In ${issue.state?.name || 'No status'} for ${timeInStatus(issue, now)}`;
}
