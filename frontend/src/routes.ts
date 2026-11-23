// Path -> view routing, extracted from main.tsx so it can be tested without a browser.
//
// Regression this file exists to prevent (HOJA-80 / HOJA-18):
// the original chain tested `p.includes('/settings')` FIRST and only later tested
// `p.includes('/team/') && p.includes('/cycles/settings')`. Every team cycle-settings
// URL is `/workspace/team/<key>/cycles/settings`, which contains `/settings`, so the
// generic workspace-settings route always won and the cycle-settings branch was dead
// code. Team-scoped routes therefore have to be matched before the workspace one.

export const KNOWN_WORKSPACE_PAGES = [
  "my-issues",
  "inbox",
  "pulse",
  "agent",
  "initiatives",
  "projects",
  "views",
  "board",
  "cycles",
  "members",
  "team",
  "settings",
  "issue",
  "task",
  "project",
  "cycle",
  "favorite-views",
  "more",
  "reviews",
  "agents",
  "initiative",
  "leaderboard",
  "member",
  "profile",
];

export function routeView(path: string): string {
  const p = path.toLowerCase();

  if (p.includes("/reviews")) return "Reviews";
  // Team-scoped routes first: they can contain segments that look like other routes.
  if (p.includes("/team/") && p.includes("/cycles/settings"))
    return "Team cycle settings";
  if (p.includes("/team/") && p.includes("/cycle/")) return "Team cycle";
  if (p.includes("/team/") && p.endsWith("/active")) return "Team issues";
  if (p.includes("/team/") && p.includes("/cycles")) return "Team cycles";
  if (p.includes("/team/") && p.includes("/views")) return "Team views";
  if (p.includes("/team/") && p.includes("/initiatives"))
    return "Team initiatives";
  if (p.includes("/team/") && p.includes("/projects")) return "Team projects";

  // Workspace settings. Only reached when no team-scoped route matched.
  if (p.includes("/settings")) return "Settings";

  if (p.includes("/issue/") || p.includes("/task/")) return "Issue detail";
  if (p.includes("/project/") && p.includes("/view/")) return "Project view";
  if (p.includes("/project/")) return "Project detail";
  // '/initiative/<id>' is the detail page; the list lives under '/initiatives'.
  if (p.includes("/initiative/")) return "Initiative detail";
  if (p.endsWith("/home")) return "Team Home";
  if (p.includes("/favorite-views")) return "Favorite views";
  if (p.includes("/cycle/") && !p.includes("/team/")) return "Cycle detail";
  if (p.includes("/board")) return "Board";
  if (p.includes("/team/")) return "Team Home";
  if (p.includes("/my-issues")) return "My issues";
  if (p.includes("/inbox")) return "Inbox";
  if (p.includes("/initiatives")) return "Initiatives";
  if (p.includes("/pulse")) return "Pulse";
  if (p.includes("/views")) return "Views";
  if (p.includes("/projects")) return "Projects";
  if (p.includes("/cycles")) return "Cycles";
  if (p.includes("/leaderboard")) return "Leaderboard";
  if (p.includes("/member/") || p.endsWith("/profile")) return "Member profile";
  if (p.includes("/members")) return "Members";
  // Before '/agent': every agent-surface URL contains '/agents', which the Agent page's
  // substring test would otherwise swallow (HOJA-602-A-16).
  //
  // The detail page is its own view, for the same reason 'Issue detail' is: the app puts the
  // list views inside the rounded workspace shell and lets a detail page be full-bleed. One
  // view name cannot be both, so the shape of the URL decides, exactly as it does for tasks.
  if (p.includes("/agents")) {
    const rest = p.split("/").filter(Boolean);
    const tail = rest.slice(rest.indexOf("agents") + 1);
    // `runners/<id>` is a detail page for the same reason `<key>` is: it is full-bleed like the
    // issue page, while `runners` itself is a list and stays in the rounded shell (HOJA-602-A-16).
    const detail =
      (tail.length === 1 && !["queue", "audit", "runners"].includes(tail[0])) ||
      (tail.length === 2 && tail[0] === "runners");
    return detail ? "Agent detail" : "Agents";
  }
  if (p.includes("/agent")) return "Agent";

  const parts = p.split("/").filter(Boolean);
  if (
    parts[0] === "workspace" &&
    parts.length === 2 &&
    !KNOWN_WORKSPACE_PAGES.includes(parts[1])
  ) {
    return "Not found";
  }
  const name = parts.pop();
  return name
    ? name.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    : "My issues";
}

// HOJA-288: task detail pages moved from /workspace/issue/<IDENT>/<slug> to
// /workspace/task/<IDENT>/<slug>. The old segment has to keep resolving forever,
// because those links are already sitting in people's email and bookmarks. This is
// applied at the routing boundary so no other code has to know the old name.
export const LEGACY_DETAIL_SEGMENT = "/workspace/issue/";
export const DETAIL_SEGMENT = "/workspace/task/";

export function redirectLegacyTaskPath(path: string): string {
  const lower = path.toLowerCase();
  const at = lower.indexOf(LEGACY_DETAIL_SEGMENT);
  if (at === -1) return path;
  // Rewrite only the first occurrence and keep identifier, slug, query and hash.
  return (
    path.slice(0, at) +
    DETAIL_SEGMENT +
    path.slice(at + LEGACY_DETAIL_SEGMENT.length)
  );
}
