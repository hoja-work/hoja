/* Who did it, and what — for one task-activity row (HOJA-728).
 *
 * The timeline used to say "Someone updated the task" for every row it did not
 * recognise or whose actor was empty: runner events, SLA clock events and
 * archives all read like an unknown person editing the task. The server now names
 * the actor for everything a person or agent does (`hoja_server/activity.py`);
 * the only actor-less rows left are the system's own, and they are labelled as
 * Hoja, not as a person.
 */

export const SYSTEM_ACTOR = "Hoja";
/** An old row that predates actor recording; it was a person, but we never stored who. */
export const UNRECORDED_ACTOR = "An unrecorded member";

export type ActivityLike = {
  eventType: string;
  actor?: { name?: string | null } | null;
  changes?: Record<string, unknown> | null;
};

/** Events the server raises on its own. Mirrors `activity.is_system_event`. */
export function isSystemEvent(eventType: string): boolean {
  return eventType.startsWith("sla") || eventType.startsWith("runner.");
}

export function actorLabel(e: ActivityLike): string {
  if (e.actor?.name) return e.actor.name;
  return isSystemEvent(e.eventType) ? SYSTEM_ACTOR : UNRECORDED_ACTOR;
}

const RUNNER_PHRASES: Record<string, string> = {
  "lease.claimed": "picked up the task on a runner",
  "harness.selected": "chose a harness for the run",
  "harness.spawned": "started the run",
  "harness.prompted": "sent the task to the harness",
  "harness.reaped": "stopped the run",
  "connect.exposed": "opened a Connect preview",
};

const FIXED_PHRASES: Record<string, string> = {
  issueArchived: "archived the task",
  commentCreated: "commented",
  commentUpdated: "edited a comment",
  decisionCreated: "asked for a decision",
  decisionAnswered: "answered a decision",
  decisionCanceled: "canceled a decision",
  extensionPatched: "changed the task through an extension",
  slaStarted: "started the SLA clock",
  slaBreached: "marked the SLA as breached",
  slaResolved: "met the SLA",
  slaDetached: "removed the SLA",
  slaRetargeted: "moved the SLA deadline",
  githubReviewApproved: "approved the pull request",
  githubReviewChangesRequested: "requested changes on the pull request",
  githubReviewCommented: "commented on the pull request",
  githubReviewMerged: "merged the pull request",
};

/** The verb phrase for event types that are not field edits, or null for a field edit. */
export function eventPhrase(e: ActivityLike): string | null {
  if (e.eventType.startsWith("runner.")) {
    const kind = e.eventType.slice("runner.".length);
    const harness = (e.changes as { harness?: unknown } | null | undefined)
      ?.harness;
    const base = RUNNER_PHRASES[kind] || `reported runner event ${kind}`;
    return typeof harness === "string" && harness
      ? `${base} (${harness})`
      : base;
  }
  return FIXED_PHRASES[e.eventType] ?? null;
}
