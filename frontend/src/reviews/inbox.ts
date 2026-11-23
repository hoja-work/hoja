/**
 * The Reviews inbox: one list of "things that need a person's attention", assembled from
 * sources.
 *
 * A source is a function from its own records to InboxItems. The inbox never knows what
 * GitHub or Sentry or a task is; it only merges items, filters them by source, and groups
 * them. Adding a source means writing one `collect` and registering it in `REVIEW_SOURCES`.
 *
 * Items are identified by `id`. Two sources that describe the same thing (a pull request
 * linked to an In Review task, for example) produce items with the same id, and `mergeItems`
 * folds them into one row that carries both connections. That is what keeps the inbox from
 * listing one pull request twice when the task and the PR are both in the data.
 *
 * The readiness rule for a pull request is NOT defined here. `reviewGroupOf` in
 * ../reviews-gate.ts owns it, and this module only reads its answer, so the Ready/Open/Draft
 * buckets cannot drift between the queue and the inbox.
 */
import { reviewGroupOf, type ReviewBucket } from "../reviews-gate";

export type InboxSourceId =
  "pull-request" | "task-in-review" | "github-review-request" | "sentry-issue";

export type InboxBucket =
  "Needs attention" | "Ready to merge" | "Open" | "Draft" | "Waiting on you";

/** Render order of buckets. Attention comes first because it is the only bucket that
 *  describes a broken connection (a task in review with no pull request, a merged pull request
 *  whose task is still open) rather than the state of a review. */
export const INBOX_BUCKETS: readonly InboxBucket[] = [
  "Needs attention",
  "Waiting on you",
  "Ready to merge",
  "Open",
  "Draft",
];

export type InboxTone = "ready" | "open" | "draft" | "attention" | "info";

export type InboxLinkKind =
  "task" | "pull-request" | "sentry" | "github" | "other";

export type InboxLink = {
  kind: InboxLinkKind;
  label: string;
  url?: string | null;
};

export type InboxItem = {
  /** Stable identity. Items with the same id are the same thing seen from two sources. */
  id: string;
  /** Every source that contributed to this item, in first-seen order. */
  sources: InboxSourceId[];
  title: string;
  /** Short human reference: "#123", "HOJA-42", a Sentry short id. */
  ref: string;
  url?: string | null;
  bucket: InboxBucket;
  tone: InboxTone;
  /** One line that says why the item is in its bucket. Printed in the row and in the detail. */
  reason: string;
  updatedAt?: string | null;
  links: InboxLink[];
  /** Set when the item is a pull request, so the detail pane can load the live gate. */
  pullRequestId?: string | null;
};

export type InboxSource<Input = unknown> = {
  id: InboxSourceId;
  label: string;
  /** False when the workspace has no data path for this source yet. The chip still shows, so
   *  the inbox says what it does not read instead of silently omitting it. */
  connected: boolean;
  /** Hint printed when the source is not connected. */
  connectHint?: string;
  collect: (input: Input) => InboxItem[];
};

/* ---- Pull requests ------------------------------------------------------------------- */

/** The fields of a stored pull-request link that the inbox reads. Structural, so the view's
 *  ReviewLink satisfies it without this module importing the view. */
export type PullRequestInput = {
  id: string;
  kind?: string | null;
  externalId: string;
  url?: string | null;
  title?: string | null;
  state?: string | null;
  updatedAt?: string | null;
  mergeable?: boolean | null;
  mergeState?: string | null;
  checksState?: string | null;
  reviewDecision?: string | null;
  issue?: { identifier?: string | null } | null;
};

function pullRequestBucket(bucket: ReviewBucket): InboxBucket {
  if (bucket === "Ready to merge") return "Ready to merge";
  if (bucket === "Draft") return "Draft";
  return "Open";
}

function pullRequestReason(
  pr: PullRequestInput,
  group: ReviewBucket,
  reason: string,
): string {
  if (group === "Terminal") return `${pr.state ?? "closed"} pull request`;
  if (group === "Ready to merge")
    return "Approved, checks passing, branch clean";
  if (group === "Draft") return "Draft pull request";
  // Open: say which of the readiness conditions is missing, in words.
  if (reason === "review-unknown" || reason.startsWith("review-"))
    return "Waiting for review";
  if (reason.startsWith("checks-")) return "Checks not passing";
  if (reason.startsWith("merge-state-")) return "Branch not clean";
  return "Open for review";
}

export const pullRequestSource: InboxSource<PullRequestInput[]> = {
  id: "pull-request",
  label: "Pull requests",
  connected: true,
  collect(links) {
    const items: InboxItem[] = [];
    for (const pr of links) {
      const verdict = reviewGroupOf(pr);
      // Merged and closed pull requests are not in the queue. A task in review whose pull
      // request already merged is still reported, by the task source, as an attention item.
      if (verdict.group === "Terminal") continue;
      const bucket = pullRequestBucket(verdict.group);
      items.push({
        id: `pr:${pr.url || pr.externalId}`,
        sources: ["pull-request"],
        title: pr.title || pr.externalId,
        ref: pr.externalId,
        url: pr.url ?? null,
        bucket,
        tone:
          bucket === "Ready to merge"
            ? "ready"
            : bucket === "Draft"
              ? "draft"
              : "open",
        reason: pullRequestReason(pr, verdict.group, verdict.reason),
        updatedAt: pr.updatedAt ?? null,
        links: pr.issue?.identifier
          ? [{ kind: "task", label: pr.issue.identifier }]
          : [],
        pullRequestId: pr.id,
      });
    }
    return items;
  },
};

/* ---- Tasks in review ----------------------------------------------------------------- */

const PULL_REQUEST_KIND = /^pull[-_]?request$/i;

export type TaskInReviewInput = {
  identifier: string;
  title: string;
  url?: string | null;
  updatedAt?: string | null;
  stateChangedAt?: string | null;
  githubLinks?: PullRequestInput[] | null;
};

/** A task in In Review either has a pull request whose state is worth reading (then the pull
 *  request source owns the row, and this source only adds the task as a connection), or it
 *  has none, or its pull request is already merged. The last two are the connection problems
 *  this inbox exists to show, so they become attention items of their own. */
export const taskInReviewSource: InboxSource<TaskInReviewInput[]> = {
  id: "task-in-review",
  label: "Tasks in review",
  connected: true,
  collect(tasks) {
    const items: InboxItem[] = [];
    for (const task of tasks) {
      // A task's GitHub links include issues and other objects; only pull requests take part.
      const prs = (task.githubLinks ?? []).filter(
        (l) => l && l.externalId && PULL_REQUEST_KIND.test(l.kind ?? ""),
      );
      const taskLink: InboxLink = {
        kind: "task",
        label: task.identifier,
        url: task.url ?? null,
      };
      const updatedAt = task.stateChangedAt || task.updatedAt || null;

      if (prs.length === 0) {
        items.push({
          id: `task:${task.identifier}`,
          sources: ["task-in-review"],
          title: task.title,
          ref: task.identifier,
          url: task.url ?? null,
          bucket: "Needs attention",
          tone: "attention",
          reason: "In review with no pull request linked",
          updatedAt,
          links: [taskLink],
          pullRequestId: null,
        });
        continue;
      }

      const open = prs.filter((p) => reviewGroupOf(p).group !== "Terminal");
      if (open.length === 0) {
        // Every linked pull request is merged or closed while the task still says In Review.
        items.push({
          id: `task:${task.identifier}`,
          sources: ["task-in-review"],
          title: task.title,
          ref: task.identifier,
          url: task.url ?? null,
          bucket: "Needs attention",
          tone: "attention",
          reason: "Pull request is closed, but the task is still in review",
          updatedAt,
          links: [taskLink, ...prs.map(prLink)],
          pullRequestId: null,
        });
        continue;
      }

      // The task has an open pull request: its row is the pull request's row. This source
      // contributes the task as a connection, keyed to the same id the pull request uses.
      for (const pr of open) {
        items.push({
          id: `pr:${pr.url || pr.externalId}`,
          sources: ["task-in-review"],
          title: pr.title || pr.externalId,
          ref: pr.externalId,
          url: pr.url ?? null,
          bucket: pullRequestBucket(reviewGroupOf(pr).group),
          tone: "open",
          reason: `For ${task.identifier}, in review`,
          updatedAt,
          links: [taskLink],
          pullRequestId: pr.id,
        });
      }
    }
    return items;
  },
};

function prLink(pr: PullRequestInput): InboxLink {
  return { kind: "pull-request", label: pr.externalId, url: pr.url ?? null };
}

/* ---- Sources without a data path yet ---------------------------------------------------
 * Registered so the chip, the count and the connect hint exist in the open-ended model. A
 * source becomes connected by giving it a `collect` that reads real records; nothing else in
 * the inbox changes. */

export const githubReviewRequestSource: InboxSource<unknown> = {
  id: "github-review-request",
  label: "Review requests",
  connected: false,
  connectHint: "Connect GitHub to see review requests addressed to you.",
  collect: () => [],
};

export const sentryIssueSource: InboxSource<unknown> = {
  id: "sentry-issue",
  label: "Sentry",
  connected: false,
  connectHint: "Connect Sentry to see new issues that need triage.",
  collect: () => [],
};

export const REVIEW_SOURCES: readonly InboxSource<any>[] = [
  pullRequestSource,
  taskInReviewSource,
  githubReviewRequestSource,
  sentryIssueSource,
];

/* ---- Merge, filter, group -------------------------------------------------------------- */

/** Fold items that share an id. The first item's fields win; links and sources are unioned,
 *  and the more urgent bucket is kept, so a connection never downgrades an attention item. */
export function mergeItems(items: InboxItem[]): InboxItem[] {
  const byId = new Map<string, InboxItem>();
  for (const item of items) {
    const prior = byId.get(item.id);
    if (!prior) {
      byId.set(item.id, {
        ...item,
        sources: [...item.sources],
        links: dedupeLinks(item.links),
      });
      continue;
    }
    const sources = [...prior.sources];
    for (const s of item.sources) if (!sources.includes(s)) sources.push(s);
    const bucket =
      INBOX_BUCKETS.indexOf(item.bucket) < INBOX_BUCKETS.indexOf(prior.bucket)
        ? item.bucket
        : prior.bucket;
    byId.set(item.id, {
      ...prior,
      sources,
      bucket,
      tone: bucket === "Needs attention" ? "attention" : prior.tone,
      reason: bucket === prior.bucket ? prior.reason : item.reason,
      links: dedupeLinks([...prior.links, ...item.links]),
      pullRequestId: prior.pullRequestId ?? item.pullRequestId ?? null,
    });
  }
  return [...byId.values()];
}

function dedupeLinks(links: InboxLink[]): InboxLink[] {
  const seen = new Set<string>();
  return links.filter((l) => {
    const key = `${l.kind}:${l.label}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export type InboxResult = {
  items: InboxItem[];
  /** Items per source, counted before the source filter is applied. */
  counts: Record<InboxSourceId, number>;
};

/** Collect every connected source, merge, and count per source. `inputs` is keyed by source
 *  id so each source reads only its own records. */
export function buildInbox(
  inputs: Partial<Record<InboxSourceId, unknown>>,
): InboxResult {
  const collected: InboxItem[] = [];
  for (const source of REVIEW_SOURCES) {
    if (!source.connected) continue;
    const input = inputs[source.id];
    if (input === undefined) continue;
    collected.push(...source.collect(input));
  }
  const items = mergeItems(collected);
  const counts = Object.fromEntries(
    REVIEW_SOURCES.map((s) => [
      s.id,
      items.filter((i) => i.sources.includes(s.id)).length,
    ]),
  ) as Record<InboxSourceId, number>;
  return { items, counts };
}

export type InboxFilter = InboxSourceId | "all";

export function filterItems(
  items: InboxItem[],
  filter: InboxFilter,
): InboxItem[] {
  return filter === "all"
    ? items
    : items.filter((i) => i.sources.includes(filter));
}

export type InboxGroup = { bucket: InboxBucket; items: InboxItem[] };

/** Groups in INBOX_BUCKETS order. Empty buckets are dropped, so the view never prints a
 *  header with nothing under it. Within a bucket, most recently updated first. */
export function groupItems(items: InboxItem[]): InboxGroup[] {
  const byTime = (a: InboxItem, b: InboxItem) =>
    (Date.parse(b.updatedAt ?? "") || 0) - (Date.parse(a.updatedAt ?? "") || 0);
  return INBOX_BUCKETS.map((bucket) => ({
    bucket,
    items: items.filter((i) => i.bucket === bucket).sort(byTime),
  })).filter((g) => g.items.length > 0);
}

/* ---- Activity ---------------------------------------------------------------------------
 * A feed of what changed, newest first. It is derived from the current state of each item
 * (its last update time and its reason), not from a stored event log: there is no history
 * table behind it yet, so an item appears once, at the time it last changed. */

export type ActivityEvent = {
  id: string;
  at: string | null;
  tone: InboxTone;
  bucket: InboxBucket;
  ref: string;
  title: string;
  reason: string;
  sources: InboxSourceId[];
};

export function activityOf(items: InboxItem[]): ActivityEvent[] {
  const time = (e: ActivityEvent) => Date.parse(e.at ?? "") || 0;
  return items
    .map((item) => ({
      id: item.id,
      at: item.updatedAt ?? null,
      tone: item.tone,
      bucket: item.bucket,
      ref: item.ref,
      title: item.title,
      reason: item.reason,
      sources: item.sources,
    }))
    .sort((a, b) => time(b) - time(a));
}

/* ---- Connection map --------------------------------------------------------------------
 * The nodes around one item: its task, its pull request, the checks, the merge gate, and the
 * sources that are not connected yet. Each node says whether it is fine, needs attention,
 * is missing, or is off because its source is not connected. */

export type ConnectionNodeKind =
  | "task"
  | "pull-request"
  | "checks"
  | "merge-gate"
  | "sentry"
  | "github-review-request";

export type ConnectionNode = {
  kind: ConnectionNodeKind;
  title: string;
  detail: string;
  state: "ok" | "warn" | "missing" | "off";
};

export type ConnectionPullRequest = PullRequestInput & {
  checksTotal?: number | null;
  checksPassing?: number | null;
  checksFailing?: number | null;
};

export function connectionNodesOf(
  item: InboxItem,
  pr?: ConnectionPullRequest | null,
): ConnectionNode[] {
  const taskLink = item.links.find((l) => l.kind === "task");
  const prLink = item.links.find((l) => l.kind === "pull-request");
  const nodes: ConnectionNode[] = [];

  nodes.push(
    taskLink
      ? {
          kind: "task",
          title: taskLink.label,
          detail: "In review",
          state: "ok",
        }
      : {
          kind: "task",
          title: item.ref,
          detail: "Not linked to a task",
          state: "missing",
        },
  );

  if (pr) {
    nodes.push({
      kind: "pull-request",
      title: pr.externalId,
      detail: pr.title || "Pull request",
      state: item.bucket === "Needs attention" ? "warn" : "ok",
    });
    const total = pr.checksTotal ?? 0;
    const passing = pr.checksPassing ?? 0;
    const checksKnown =
      pr.checksState &&
      pr.checksState !== "unknown" &&
      pr.checksState !== "none" &&
      total > 0;
    nodes.push(
      checksKnown
        ? {
            kind: "checks",
            title: `${passing}/${total} checks`,
            detail: pr.checksState ?? "",
            state:
              (pr.checksFailing ?? 0) > 0
                ? "warn"
                : passing === total
                  ? "ok"
                  : "off",
          }
        : {
            kind: "checks",
            title: "No checks",
            detail: "Nothing reported",
            state: "off",
          },
    );
    nodes.push(
      item.bucket === "Ready to merge"
        ? {
            kind: "merge-gate",
            title: "Ready",
            detail: "Approved, passing, clean",
            state: "ok",
          }
        : {
            kind: "merge-gate",
            title: "Not ready",
            detail: item.reason,
            state: "warn",
          },
    );
  } else if (prLink) {
    nodes.push({
      kind: "pull-request",
      title: prLink.label,
      detail: "Closed",
      state: "warn",
    });
  } else {
    nodes.push({
      kind: "pull-request",
      title: "No pull request",
      detail: "Link one on the task",
      state: "missing",
    });
  }

  nodes.push({
    kind: "sentry",
    title: "Sentry",
    detail: "Not connected",
    state: "off",
  });
  nodes.push({
    kind: "github-review-request",
    title: "Review requests",
    detail: "Not connected",
    state: "off",
  });
  return nodes;
}

/* ---- Board -----------------------------------------------------------------------------
 * Triage columns: one per bucket, in INBOX_BUCKETS order, empty columns kept so the board
 * always shows the same shape. */

export type BoardColumn = { bucket: InboxBucket; items: InboxItem[] };

export function boardOf(items: InboxItem[]): BoardColumn[] {
  const byTime = (a: InboxItem, b: InboxItem) =>
    (Date.parse(b.updatedAt ?? "") || 0) - (Date.parse(a.updatedAt ?? "") || 0);
  return INBOX_BUCKETS.map((bucket) => ({
    bucket,
    items: items.filter((i) => i.bucket === bucket).sort(byTime),
  }));
}
