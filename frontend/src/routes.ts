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
  "view",
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
  "initiative",
];

// First path segments that are real entry points outside /workspace/.
const TOP_LEVEL_ENTRY_POINTS = [
  "workspace",
  "login",
  "signup",
  "signin",
  "setup",
  "onboarding",
  "index.html",
];

export function routeView(path: string): string {
  const p = path.toLowerCase();

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
  // A saved view's page: /workspace/view/<slug>, or the stable /<workspace>/view/<slug> link
  // its `url` carries (redirectLegacyViewPath moves that one onto /workspace/view/).
  if (/^\/[^/]+\/view\/[^/]+\/?$/.test(p)) return "View";
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
  if (p.includes("/members")) return "Members";
  if (p.includes("/agent")) return "Agent";

  const parts = p.split("/").filter(Boolean);
  if (
    parts[0] === "workspace" &&
    parts.length === 2 &&
    !KNOWN_WORKSPACE_PAGES.includes(parts[1])
  ) {
    return "Not found";
  }
  // Every app page lives under /workspace/. Any other top-level path is not a page; the
  // title-cased fallback below used to turn e.g. /totally/unknown into a fake "Unknown"
  // dashboard. The auth entry points keep their old behaviour: they are rendered before
  // this view is ever shown, and a signed-in visit to them must not read as a 404.
  if (parts.length && !TOP_LEVEL_ENTRY_POINTS.includes(parts[0])) {
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
