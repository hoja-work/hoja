#!/usr/bin/env node
/**
 * The Reviews inbox, as a table.
 *
 * src/reviews/inbox.ts merges sources into one list. These rows pin the connection rules:
 * a task in review and its pull request are one row, a task with no pull request is an
 * attention item, and a source that is not connected contributes nothing.
 *
 *   node --experimental-strip-types scripts/test-reviews-inbox.mjs
 */
import { registerHooks } from "node:module";

// app code imports sibling modules without an extension (bundler resolution); Node's ESM
// loader does not, so fall back to `.ts` for relative specifiers that do not resolve.
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
        return next(`${specifier}.ts`, context);
      }
      throw error;
    }
  },
});

const {
  buildInbox,
  filterItems,
  groupItems,
  mergeItems,
  pullRequestSource,
  taskInReviewSource,
  REVIEW_SOURCES,
  activityOf,
  boardOf,
  connectionNodesOf,
} = await import("../src/reviews/inbox.ts");

let failures = 0;
const check = (name, ok, detail = "") => {
  if (ok) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
};

const ready = {
  clean: {
    mergeState: "clean",
    checksState: "passing",
    reviewDecision: "approved",
    mergeable: true,
  },
};

const pr = (over) => ({
  id: over.id ?? `id-${over.externalId}`,
  kind: "pull_request",
  externalId: "#1",
  url: "https://example.test/pr/1",
  title: "PR",
  state: "open",
  updatedAt: "2026-11-10T00:00:00Z",
  mergeable: null,
  mergeState: null,
  checksState: null,
  reviewDecision: null,
  issue: null,
  ...over,
});

const task = (over) => ({
  identifier: "HOJA-1",
  title: "Task",
  url: "https://example.test/t/1",
  updatedAt: "2026-11-10T00:00:00Z",
  stateChangedAt: "2026-11-09T00:00:00Z",
  githubLinks: [],
  ...over,
});

console.log("pull-request source");
{
  const items = pullRequestSource.collect([
    pr({ externalId: "#10", url: "u10", ...ready.clean, title: "Ready one" }),
    pr({ externalId: "#11", url: "u11", state: "draft", title: "Draft one" }),
    pr({
      externalId: "#12",
      url: "u12",
      mergeState: "dirty",
      checksState: "passing",
      reviewDecision: "approved",
      title: "Dirty",
    }),
    pr({ externalId: "#13", url: "u13", state: "merged", title: "Merged" }),
    pr({ externalId: "#14", url: "u14", state: "closed", title: "Closed" }),
  ]);
  const byRef = Object.fromEntries(items.map((i) => [i.ref, i]));
  check(
    "ready pull request lands in Ready to merge",
    byRef["#10"]?.bucket === "Ready to merge",
  );
  check("draft pull request lands in Draft", byRef["#11"]?.bucket === "Draft");
  check(
    "dirty branch stays Open and says why",
    byRef["#12"]?.bucket === "Open" &&
      byRef["#12"]?.reason === "Branch not clean",
  );
  check(
    "merged and closed pull requests are not in the inbox",
    !byRef["#13"] && !byRef["#14"],
  );
  check(
    "a pull request is attributed to the pull-request source",
    byRef["#10"]?.sources.join() === "pull-request",
  );
}

console.log("task-in-review source");
{
  const noPr = taskInReviewSource.collect([
    task({ identifier: "HOJA-7", title: "Orphan" }),
  ]);
  check(
    "task in review with no pull request is an attention item",
    noPr.length === 1 && noPr[0].bucket === "Needs attention",
  );
  check(
    "attention reason names the missing link",
    noPr[0]?.reason === "In review with no pull request linked",
  );

  const merged = taskInReviewSource.collect([
    task({
      identifier: "HOJA-8",
      githubLinks: [pr({ externalId: "#30", url: "u30", state: "merged" })],
    }),
  ]);
  check(
    "task whose only pull request merged is an attention item",
    merged[0]?.bucket === "Needs attention",
  );
  check(
    "merged-pr attention reason is specific",
    merged[0]?.reason ===
      "Pull request is closed, but the task is still in review",
  );

  const open = taskInReviewSource.collect([
    task({
      identifier: "HOJA-9",
      githubLinks: [pr({ externalId: "#31", url: "u31", ...ready.clean })],
    }),
  ]);
  check(
    "task with an open pull request contributes the same id as the pull request",
    open[0]?.id === "pr:u31",
  );
  check(
    "the task is carried as a link on that item",
    open[0]?.links[0]?.label === "HOJA-9",
  );
}

console.log("merge and connections");
{
  const { items } = buildInbox({
    "pull-request": [
      pr({
        externalId: "#40",
        url: "u40",
        ...ready.clean,
        title: "Linked",
        issue: { identifier: "HOJA-40" },
      }),
    ],
    "task-in-review": [
      task({
        identifier: "HOJA-40",
        githubLinks: [pr({ externalId: "#40", url: "u40", ...ready.clean })],
      }),
    ],
  });
  check(
    "a task and its pull request are one row",
    items.filter((i) => i.id === "pr:u40").length === 1,
  );
  const row = items.find((i) => i.id === "pr:u40");
  check(
    "the merged row lists both contributing sources",
    row?.sources.join() === "pull-request,task-in-review",
  );
  check(
    "the merged row keeps one HOJA-40 connection",
    row?.links.filter((l) => l.label === "HOJA-40").length === 1,
  );
  check(
    "a merged row keeps the Ready bucket from the pull-request source",
    row?.bucket === "Ready to merge",
  );
}

console.log("attention is never hidden by a connection");
{
  const merged = mergeItems([
    {
      id: "x",
      sources: ["pull-request"],
      title: "t",
      ref: "r",
      bucket: "Ready to merge",
      tone: "ready",
      reason: "ok",
      links: [],
    },
    {
      id: "x",
      sources: ["task-in-review"],
      title: "t",
      ref: "r",
      bucket: "Needs attention",
      tone: "attention",
      reason: "broken",
      links: [],
    },
  ]);
  check(
    "the more urgent bucket wins when sources disagree",
    merged[0].bucket === "Needs attention",
  );
  check("its reason comes with it", merged[0].reason === "broken");
}

console.log("unconnected sources");
{
  const { items, counts } = buildInbox({
    "github-review-request": [{ anything: true }],
    "sentry-issue": [{ anything: true }],
  });
  check("an unconnected source adds no rows", items.length === 0);
  check(
    "unconnected sources still have a count of zero",
    counts["sentry-issue"] === 0 && counts["github-review-request"] === 0,
  );
  check(
    "every registered source has a chip count",
    REVIEW_SOURCES.every((s) => s.id in counts),
  );
}

console.log("filter, count and grouping");
{
  const { items, counts } = buildInbox({
    "pull-request": [
      pr({
        externalId: "#50",
        url: "u50",
        ...ready.clean,
        updatedAt: "2026-11-01T00:00:00Z",
      }),
      pr({
        externalId: "#51",
        url: "u51",
        ...ready.clean,
        updatedAt: "2026-11-05T00:00:00Z",
      }),
      pr({
        externalId: "#52",
        url: "u52",
        state: "draft",
        updatedAt: "2026-11-02T00:00:00Z",
      }),
    ],
    "task-in-review": [task({ identifier: "HOJA-60", githubLinks: [] })],
  });
  check(
    "counts are per source",
    counts["pull-request"] === 3 && counts["task-in-review"] === 1,
  );
  check(
    'the "task-in-review" filter shows only task-connected rows',
    filterItems(items, "task-in-review").length === 1,
  );
  check(
    'the "all" filter shows every row',
    filterItems(items, "all").length === items.length,
  );

  const groups = groupItems(items);
  check(
    "groups follow the bucket order and drop empty buckets",
    groups.map((g) => g.bucket).join("|") ===
      "Needs attention|Ready to merge|Draft",
  );
  const ready2 = groups.find((g) => g.bucket === "Ready to merge");
  check(
    "rows inside a bucket are newest first",
    ready2?.items.map((i) => i.ref).join() === "#51,#50",
  );
}

console.log(
  failures === 0
    ? "\nreviews inbox: all checks passed"
    : `\nreviews inbox: ${failures} check(s) failed`,
);
process.exit(failures === 0 ? 0 : 1);
