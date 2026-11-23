import { motion, useReducedMotion } from "framer-motion";
import "@fontsource-variable/inter/wght.css";
import "./tailwind.css";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import { Dialog as StackDialog } from "radix-ui";
import { ReactGrabDev } from "./dev/ReactGrabDev";
import {
  Popover as UIPopover,
  PopoverContent,
  PopoverTrigger,
  PopoverAnchor,
} from "./components/ui/popover";
import {
  Activity,
  ArrowLeft,
  ArrowUp,
  Bell,
  Blocks,
  BookOpen,
  Bot,
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  Command,
  ExternalLink,
  FolderKanban,
  GitBranch,
  GitMerge,
  GitPullRequest,
  Inbox,
  KeyRound,
  LayoutDashboard,
  ListFilter,
  LogOut,
  Menu,
  Moon,
  MoreHorizontal,
  Paperclip,
  Plus,
  Plug,
  Search,
  Settings,
  Shield,
  SlidersHorizontal,
  Sun,
  Tags,
  LayoutTemplate,
  Target,
  Trophy,
  UserRound,
  Users,
  X,
  Zap,
  BarChart3,
  MoreVertical,
  MessageCircle,
  Send,
  Layers3,
  List as ListIcon,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { MorphIcon } from "morphicons/react";
import { layoutIcon, listIcon } from "./icons";
import { ConnectedAppsSettings } from "./ConnectedAppsSettings";
import { ExtensionsSettings } from "./ExtensionsSettings";
import { connectRealtime, onRealtimeFrame } from "./realtime";
import {
  publishChangeEvents,
  startSync,
  type SyncHandle,
  type SyncMeta,
} from "./sync";
import "./styles.css";
import {
  confirmDialog,
  promptDialog,
  alertDialog,
  DialogHost,
} from "./dialogs";
import {
  createView,
  fieldsFromFilters,
  filtersFromFields,
  listViews,
  migrateLegacyViews,
  toggleViewFavorite,
  type ServerView,
} from "./views";
import { AgentChatPage } from "./AgentChatPage";
import { ModelProvidersSettings } from "./ModelProvidersSettings";
import { ConnectorsSettings } from "./ConnectorsSettings";
import { AgentConversationsSettings } from "./AgentConversationsSettings";
import { KnowledgeSettings } from "./KnowledgeSettings";
import { AgentsSurface } from "./agents/AgentsSurface";
import { ProjectRepository } from "./agents/ProjectRepository";
import { LeaderboardPage } from "./profiles/LeaderboardPage";
import { MemberProfilePage } from "./profiles/MemberProfilePage";
import {
  setWorkspaceAgentsEnabled,
  useAgentsSurfaceEnabled,
  useWorkspaceAgentsEnabled,
} from "./agents/flag";
import {
  bodyLayer,
  errorText,
  navigateTo,
  searchTerms,
  warmSearchIndex,
  useEntityVersion,
  useIssueSearch,
  useIssues,
  useOptimisticList,
  type Layer,
} from "./localFirst";
import { Markdown } from "./markdown";
import { ReviewsInbox } from "./reviews/ReviewsInbox";
import type { TaskInReviewInput } from "./reviews/inbox";
import type { ReviewLink } from "./reviews/types";
import {
  SplitPane,
  ScrollArea,
  TabStrip,
  Tab,
  GroupBar,
  ListRow,
  PropertyRow,
  EmptyState,
  CircularOptionButton,
  Composer,
} from "./design";
import "./graph/styles.css";
import "./settings.css";
import { SettingsSelect } from "./SettingsSelect";
import { GraphIssueDetail } from "./graph/tasks";
import { Avatar, AvatarFallback, AvatarGroup } from "./components/ui/avatar";
import { ProjectActivity } from "./ProjectActivity";
import {
  Avatar as GraphAvatar,
  blobatarSrc,
  dueIsOverdue,
  fmtDate,
  Ring,
  SubIssuePill,
  UserBlobatar,
} from "./graph/shared";
import {
  orderTree,
  railLeft,
  ROW_INSET,
  SIBLING_STEP,
} from "./graph/hierarchy";
import { StateGlyph } from "./graph/stateGlyph";
import { formatDuration, statusAgeLabel } from "./graph/statusAge";
import { useScrollWindow, visibleRange } from "./graph/taskStore";
import { useIssues as useSyncIssues, useSyncStatus } from "./sync";
import {
  cachedIssueDetail,
  fetchIssueDetail,
  guessDefaultState,
} from "./graph/tasks";
import {
  addLocalIssue,
  getSync,
  guessNextIdentifier,
  hideIssueLocally,
  localIssueId,
  onStoreIssue,
  peekIssue,
  useIssueSelect,
  useSyncMeta,
} from "./sync";
import { SignupPage } from "./signup/SignupPage";
import { SetupWizard } from "./setup/SetupWizard";
import { createCloudAdapter } from "./setup/cloudAdapter";
import { loadDraft } from "./setup/draft";
import { BillingUsageSettings } from "./settings/billing/usage";

const setupAdapter = createCloudAdapter();
import { InboxPage } from "./inbox/InboxPage";
import { FollowButton } from "./inbox/follow";
import { ProjectMembers } from "./ProjectMembers";
import { useInboxUnread, useInboxUnreadSync } from "./inbox/model";
/** Unread task notifications beside the sidebar's Inbox item, styled like the Inbox badge. */
function InboxBadge() {
  const n = useInboxUnread();
  if (!n) return null;
  return (
    <em className="" aria-label={`${n} unread`} data-inbox-badge="">
      {n > 99 ? "99+" : n}
    </em>
  );
}
import {
  GraphTeamCycles,
  GraphCycleDetail,
  GraphTeamCycle,
  GraphTeamCycleSettings,
  GraphWorkspaceCycles,
} from "./graph/cycles";

/* Dev-only: says once which backend the Vite proxy targets. `import.meta.env.DEV` is a
 * compile-time false in `npm run build`, so the import and the log leave the production bundle. */
if (import.meta.env.DEV) {
  void import("./dev/devBackend").then(({ devBackendOrigin }) => {
    const devBackend = devBackendOrigin();
    if (devBackend) console.info(`[hoja dev] API calls proxy to ${devBackend}`);
  });
}

export type Team = {
  id: string;
  name: string;
  key: string;
  description?: string;
  /** The team's sidebar lists Initiatives. */
  showInitiatives?: boolean;
  states: {
    nodes: { id: string; name: string; color: string; type: string }[];
  };
  members?: {
    nodes: {
      id: string;
      name: string;
      email?: string;
      avatarSeed?: string | null;
    }[];
  };
  activeCycle?: Cycle;
};
export type Comment = {
  id: string;
  body: string;
  user?: { id?: string; name?: string; avatarSeed?: string | null };
  createdAt?: string;
  editedAt?: string | null;
  canEdit?: boolean;
  canDelete?: boolean;
};
export type IssueActivityEvent = {
  id: string;
  eventType: string;
  createdAt: string;
  issue: Issue;
  actor?: { name?: string; avatarSeed?: string | null };
  changes?: Record<string, unknown>;
};
export type IssueLabel = { id: string; name: string; color?: string };
export type Issue = {
  id: string;
  identifier: string;
  title: string;
  description?: string;
  createdAt?: string;
  updatedAt?: string;
  stateChangedAt?: string;
  dueDate?: string;
  priority: number;
  priorityLabel?: string;
  decisionNeeded?: boolean;
  parent?: { id: string; identifier: string; title: string } | null;
  state?: { id: string; name: string; color: string; type: string };
  team?: { id: string; key: string; name: string };
  project?: { id: string; name: string };
  assignee?: { id: string; name: string; avatarSeed?: string | null };
  delegate?: { id: string; name?: string; avatarSeed?: string | null } | null;
  delegateId?: string | null;
  creator?: { id: string; name?: string; avatarSeed?: string | null };
  subscribers?: { nodes: { id: string }[] };
  cycle?: { id: string; name?: string; number?: number };
  labels?: { nodes: IssueLabel[] };
  comments?: { nodes: Comment[] };
  /** The SLA clock the task runs against (HOJA-491/704); absent when no policy
   * covers it, so an issue without one draws no chip at all. */
  sla?: IssueSla | null;
};

/** The clock a task runs against. ``state`` is derived by the server — the
 * client never decides whether a deadline passed. */
export type IssueSla = {
  state: string;
  resolutionDueAt?: string | null;
  firstResponseDueAt?: string | null;
  firstRespondedAt?: string | null;
  cycleEndsAt?: string | null;
  retargetedAt?: string | null;
  breachedAt?: string | null;
  overdueMinutes?: number;
  pausedMinutes?: number;
  startedAt?: string;
  policy?: { id: string; name: string } | null;
};
export type BoardTab = "Active" | "Backlog" | "All issues";
export type OlderField = "assignee" | "creator";
export type OlderState = {
  status: "loading" | "done" | "error";
  error?: string;
};
export type OlderMine = Partial<Record<OlderField, OlderState>>;
const NO_EVENTS: IssueActivityEvent[] = [];
const noop = () => {};
type CompletedRange = "none" | "week" | "month" | "all";
export type MyIssuesTab = "Assigned" | "Created" | "Subscribed" | "Activity";
export type Viewer = {
  id?: string;
  name?: string;
  email?: string;
  admin?: boolean;
  avatarSeed?: string | null;
};
export type Project = {
  id: string;
  name: string;
  description?: string;
  state?: string;
  progress?: number;
  startDate?: string;
  targetDate?: string;
  health?: string;
  teams?: { nodes: { id: string; name: string; key: string }[] };
  lead?: { name?: string };
};
export type Cycle = {
  id: string;
  name?: string;
  description?: string;
  number?: number;
  startsAt?: string;
  endsAt?: string;
  completedAt?: string;
  progress?: number;
  team?: { id: string; name: string; key: string };
};
export type Initiative = {
  id: string;
  name: string;
  viewerSubscribed?: boolean;
  summary?: string;
  status: string;
  priority?: number;
  targetDate?: string;
  owner?: { id: string; name?: string; avatarSeed?: string | null };
  creator?: { id: string };
  projects?: {
    nodes: { id: string; name: string; progress?: number; health?: string }[];
  };
  createdAt?: string;
};
export type PulseComment = {
  id: string;
  body: string;
  createdAt?: string;
  user?: { id?: string; name?: string; avatarSeed?: string | null };
};

export type PulsePost = {
  id: string;
  status?: string;
  body: string;
  createdAt: string;
  progress?: number;
  health?: string;
  actor?: { id: string; name?: string; avatarSeed?: string | null };
  project: {
    id: string;
    name: string;
    health?: string;
    progress?: number;
    targetDate?: string;
    lead?: { name?: string };
  };
  comments?: { nodes: PulseComment[] };
};
export const issueFields = `{ id identifier title description createdAt updatedAt stateChangedAt dueDate priority priorityLabel state { id name color type } team { id key name } githubLinks { id kind externalId url title state author } project{id name} assignee { id name avatarSeed } delegate { id name avatarSeed } delegateId creator { id name avatarSeed } subscribers { nodes { id } } cycle { id name number } parent { id identifier title } labels { nodes { id name color } } }`;
const query = `query Dashboard { viewer { id name avatarSeed email admin } teams { nodes { id name key description showInitiatives states { nodes { id name color type } } members { nodes { id name avatarSeed email } } activeCycle { id name number startsAt endsAt progress } } } issues(first:100, orderBy:createdAt) { nodes ${issueFields} pageInfo { hasNextPage endCursor } } }`;
export function storedApiToken(): string | null {
  const t = localStorage.getItem("hoja_token");
  return t && t !== "session" ? t : null;
}
// MCP OAuth consent (and the /device page) send a signed-out browser to /auth/login?next=/mcp/oauth/consent?pending=…
// Remember that one path across sign-in (password, Google, or the tenant handoff that returns to /)
// and resume it once a session exists. Only those approval pages are accepted: anything broader is an open redirect.
const CONSENT_RETURN_KEY = "hoja_consent_return";
function consentReturnPath(value: string | null): string | null {
  return value &&
    (/^\/mcp\/oauth\/consent\?pending=[A-Za-z0-9_-]{16,200}$/.test(value) ||
      /^\/device(\?user_code=[A-Z]{4}-[A-Z]{4})?$/.test(value))
    ? value
    : null;
}
try {
  const next = consentReturnPath(
    new URLSearchParams(location.search).get("next"),
  );
  if (next) sessionStorage.setItem(CONSENT_RETURN_KEY, next);
} catch {
  /* storage unavailable */
}
function takeConsentReturn(): string | null {
  try {
    const next = consentReturnPath(sessionStorage.getItem(CONSENT_RETURN_KEY));
    sessionStorage.removeItem(CONSENT_RETURN_KEY);
    return next;
  } catch {
    return null;
  }
}
export async function gql(q: string, variables?: Record<string, unknown>) {
  const apiToken = storedApiToken();
  const r = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(apiToken ? { authorization: `Bearer ${apiToken}` } : {}),
    },
    body: JSON.stringify({ query: q, variables }),
  });
  const b = await r.json();
  if (!r.ok || b.errors)
    throw new Error(b.errors?.[0]?.message || "Request failed");
  return b.data;
}

/* ---- GitHub references ----------------------------------------------------
 * One path for both the create dialog and the description editor: find GitHub
 * object URLs in text a person wrote and attach each to the task. Only issue,
 * pull request, commit and release URLs match — a repository or file link has no
 * object identity, so it is left alone. A rejected URL must never fail the
 * create or the save, so failures are swallowed per URL.
 */
const GITHUB_OBJECT_URL =
  /https?:\/\/(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/(?:issues|pull)\/\d+|https?:\/\/(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/commit\/[0-9a-fA-F]{7,40}|https?:\/\/(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/releases\/tag\/[^/?#\s]+/g;
export function githubReferencesIn(text: string | null | undefined): string[] {
  if (!text) return [];
  return [...new Set(text.match(GITHUB_OBJECT_URL) || [])];
}
export async function attachGithubReferences(
  issueId: string | null | undefined,
  text: string | null | undefined,
): Promise<string[]> {
  if (!issueId) return [];
  const attached: string[] = [];
  for (const url of githubReferencesIn(text)) {
    try {
      await gql(
        "mutation($issueId:String!,$url:String!){githubLinkAdd(issueId:$issueId,url:$url){id kind}}",
        { issueId, url },
      );
      attached.push(url);
    } catch {
      /* not a reference the server can store */
    }
  }
  return attached;
}
const sections = [
  [
    "primary",
    [
      ["My issues", CircleDot],
      ["Reviews", MessageCircle],
      ["Pulse", Zap],
      ["Inbox", Inbox],
      ["Agent", Bot],
    ],
  ],
  [
    "Workspace",
    [
      ["Initiatives", Target],
      ["Projects", FolderKanban],
      ["Views", ListFilter],
      ["Leaderboard", Trophy],
    ],
  ],
  ["Favorites", [["Favorite views", Layers3]]],
  ["Your teams", []],
  [
    "Manage",
    [
      ["Board", LayoutDashboard],
      ["Cycles", CalendarDays],
      ["Members", Users],
      ["Settings", Settings],
    ],
  ],
] as const;
import {
  KNOWN_WORKSPACE_PAGES,
  routeView,
  redirectLegacyTaskPath,
} from "./routes";
import { displayLabel } from "./labels";
// Sidebar destinations share the main content container shell from DESIGN.md.
const SHELL_VIEWS = new Set([
  "My issues",
  "Pulse",
  "Inbox",
  "Agent",
  "Agents",
  "Reviews",
  "Initiatives",
  "Projects",
  "Views",
  "Favorite views",
  "Team Home",
  "Team issues",
  "Team cycles",
  "Team cycle",
  "Team projects",
  "Team initiatives",
  "Team views",
  "Board",
  "Cycles",
  "Members",
  "Leaderboard",
  "Member profile",
  "Settings",
  "Issue detail",
  "Initiative detail",
]);
const HEADERLESS_VIEWS = new Set([
  "Issue detail",
  "Initiative detail",
  "Reviews",
  "Agent",
  "Agents",
  "Team Home",
  "Team cycles",
  "Team cycle",
  "Cycles",
  "Members",
]);
type StatusEvent = {
  id: string;
  at: string;
  actor?: string;
  from?: string | null;
  to?: string | null;
};
const statusHistoryCache = new Map<string, StatusEvent[]>();
function StatusPicker({
  issue,
  teams,
  onChange,
  disabled,
  compact,
  showLabel,
  iconClassName,
  icon,
}: {
  issue: Issue;
  teams: Team[];
  onChange: (stateId: string) => Promise<void>;
  disabled?: boolean;
  compact?: boolean;
  showLabel?: boolean;
  iconClassName?: string;
  /** Replaces the default status dot while keeping the picker and its menu. */
  icon?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hover, setHover] = useState(false);
  const [loaded, setLoaded] = useState<{
    key: string;
    events: StatusEvent[];
  } | null>(null);
  const [historyError, setHistoryError] = useState("");
  const states = teams.find((t) => t.id === issue.team?.id)?.states.nodes || [];
  const historyKey = `${issue.identifier}|${issue.stateChangedAt || ""}`;
  const history =
    loaded?.key === historyKey
      ? loaded.events
      : statusHistoryCache.get(historyKey) || null;
  useEffect(() => {
    if (!hover || open || history) return;
    let live = true;
    const intent = window.setTimeout(
      () =>
        gql(
          `query IssueStatusHistory($id:String!){issue(id:$id){activity(first:30){nodes{id eventType createdAt actor{name avatarSeed} changes}}}}`,
          { id: issue.identifier },
        )
          .then((d) => {
            if (!live) return;
            const events: StatusEvent[] = (d.issue?.activity?.nodes || [])
              .filter(
                (e: { eventType: string; changes?: { stateId?: unknown } }) =>
                  e.eventType === "issueUpdated" && Boolean(e.changes?.stateId),
              )
              .map(
                (e: {
                  id: string;
                  createdAt: string;
                  actor?: { name?: string; avatarSeed?: string | null };
                  changes: {
                    stateId: { from?: string | null; to?: string | null };
                  };
                }) => ({
                  id: e.id,
                  at: e.createdAt,
                  actor: e.actor?.name,
                  from: e.changes.stateId.from,
                  to: e.changes.stateId.to,
                }),
              )
              .sort((a: StatusEvent, b: StatusEvent) =>
                b.at.localeCompare(a.at),
              );
            statusHistoryCache.set(historyKey, events);
            setLoaded({ key: historyKey, events });
          })
          .catch((e) => {
            if (live) setHistoryError((e as Error).message);
          }),
      200,
    );
    return () => {
      live = false;
      window.clearTimeout(intent);
    };
  }, [hover, open, history, historyKey, issue.identifier]);
  const pick = async (stateId: string) => {
    setOpen(false);
    if (stateId === issue.state?.id) return;
    setBusy(true);
    setError("");
    try {
      await onChange(stateId);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const nameOf = (id?: string | null) =>
    id
      ? states.find((st) => st.id === id)?.name || "Unknown status"
      : "No status";
  return (
    <UIPopover open={hover && !open} onOpenChange={setHover}>
      <PopoverAnchor asChild>
        <span
          className="relative inline-flex items-center"
          data-status-picker=""
          role="presentation"
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          onFocus={() => setHover(true)}
          onBlur={() => setHover(false)}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
          }}
        >
          <UIPopover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                className={
                  (compact && !showLabel
                    ? "inline-flex items-center gap-1.5 p-0.5 border-0 bg-transparent text-[12px]"
                    : "inline-flex items-center gap-1.5 py-0.75 px-2 border border-line rounded-md bg-panel text-foreground text-[12px]") +
                  " disabled:opacity-[.6] disabled:cursor-wait focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit] text-[12px] group"
                }
                data-status-picker-trigger=""
                data-icon-only={compact && !showLabel ? "" : undefined}
                aria-label={`Status of ${issue.identifier}: ${issue.state?.name || "No status"}`}
                aria-haspopup="listbox"
                aria-expanded={open}
                aria-busy={busy || undefined}
                disabled={disabled}
              >
                {compact && !showLabel ? (
                  (icon ?? (
                    <StatusIcon
                      state={issue.state || {}}
                      className={iconClassName}
                    />
                  ))
                ) : (
                  <>
                    {icon ?? (
                      <StatusIcon
                        state={issue.state || {}}
                        className={iconClassName}
                      />
                    )}
                    <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                      {issue.state?.name || "No status"}
                    </span>
                  </>
                )}
              </button>
            </PopoverTrigger>
            {open && (
              <PopoverContent
                asChild
                align="start"
                sideOffset={6}
                /* Radix focuses the first child control; a status list has to open on the
                 * status the task is in, or Enter on the opened list silently moves it. */
                onOpenAutoFocus={(event) => {
                  event.preventDefault();
                  const list = event.currentTarget as HTMLElement;
                  const current = list.querySelector<HTMLElement>(
                    '[role="option"][aria-selected="true"]',
                  );
                  (
                    current ||
                    list.querySelector<HTMLElement>('[role="option"]')
                  )?.focus();
                }}
              >
                <ul
                  className="border shadow-[0_8px_24px_#0002] w-auto z-40 min-w-45 mt-0 mr-0 mb-0 ml-0 pt-1 pr-1 pb-1 pl-1 list-none border-line rounded-lg bg-panel"
                  data-status-menu=""
                  role="listbox"
                  aria-label="Choose status"
                  onKeyDown={menuArrowKeys}
                >
                  {states.map((st) => (
                    <li key={st.id}>
                      <button
                        className="flex items-center gap-2 w-full pt-1.5 pr-2 pb-1.5 pl-2 border-0 rounded-[5px] bg-transparent text-foreground text-[12px] text-left cursor-pointer hover:bg-subtle focus-visible:bg-subtle aria-selected:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
                        type="button"
                        role="option"
                        aria-selected={st.id === issue.state?.id}
                        onClick={() => void pick(st.id)}
                      >
                        <StatusIcon state={st} className={iconClassName} />
                        {st.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </PopoverContent>
            )}
          </UIPopover>
          {hover && !open && (
            <PopoverContent
              onOpenAutoFocus={(event) => event.preventDefault()}
              onCloseAutoFocus={(event) => event.preventDefault()}
              align="start"
              sideOffset={6}
              className="border shadow-[0_4px_12px_#0002] z-40 flex flex-col gap-1.5 w-60 pt-2 pr-2.5 pb-2 pl-2.5 border-line rounded-lg bg-panel text-foreground text-[11px] font-normal leading-[1.35] text-left cursor-default overflow-y-auto overscroll-contain whitespace-normal max-h-[calc(100dvh-16px)]"
              data-status-history=""
              role="tooltip"
            >
              <span className="font-semibold">{statusAgeLabel(issue)}</span>
              {historyError ? (
                <span className="text-muted">Could not load history</span>
              ) : !history ? (
                <span className="text-muted">Loading history…</span>
              ) : history.length === 0 ? (
                <span className="text-muted">No status changes recorded</span>
              ) : (
                <ol
                  className="mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0 list-none flex flex-col gap-1.25"
                  data-status-history-list=""
                >
                  {history.slice(0, 5).map((ev) => (
                    <li className="flex flex-col gap-px" key={ev.id}>
                      <span>
                        {nameOf(ev.from)} → {nameOf(ev.to)}
                      </span>
                      <small className="text-muted">
                        {ev.actor || "Someone"} ·{" "}
                        {formatDuration(Date.now() - new Date(ev.at).getTime())}{" "}
                        ago
                      </small>
                    </li>
                  ))}
                </ol>
              )}
            </PopoverContent>
          )}
          {error && (
            <span
              className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
              role="alert"
            >
              {error}
            </span>
          )}
        </span>
      </PopoverAnchor>
    </UIPopover>
  );
}
/* Roving arrow-key navigation for a listbox picker, so the assignee control is
 * usable from the keyboard the same way it is from the mouse. */
function menuArrowKeys(e: React.KeyboardEvent<HTMLElement>) {
  const opts = [
    ...e.currentTarget.querySelectorAll<HTMLElement>('[role="option"]'),
  ];
  const at = opts.indexOf(document.activeElement as HTMLElement);
  if (e.key === "ArrowDown") {
    e.preventDefault();
    opts[Math.min(opts.length - 1, at + 1)]?.focus();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    opts[Math.max(0, at - 1)]?.focus();
  } else if (e.key === "Home") {
    e.preventDefault();
    opts[0]?.focus();
  } else if (e.key === "End") {
    e.preventDefault();
    opts[opts.length - 1]?.focus();
  }
}

/* Assignee control for a list row. Same contract as StatusPicker beside it: click
 * opens a listbox, choosing a member reassigns through the existing task update,
 * Escape closes and refocuses the trigger, and the row's own click never fires. */
function AssigneePicker({
  issue,
  teams,
  onChange,
  disabled,
}: {
  issue: Issue;
  teams: Team[];
  onChange: (assigneeId: string | null) => Promise<void>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLUListElement>(null);
  const members =
    teams.find((t) => t.id === issue.team?.id)?.members?.nodes || [];
  useEffect(() => {
    if (open)
      menu.current?.querySelector<HTMLElement>('[role="option"]')?.focus();
  }, [open]);
  const pick = async (assigneeId: string | null) => {
    setOpen(false);
    if ((issue.assignee?.id || null) === assigneeId) return;
    setBusy(true);
    setError("");
    try {
      await onChange(assigneeId);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <UIPopover open={open} onOpenChange={setOpen}>
      <span
        className="relative inline-flex items-center"
        role="presentation"
        onClick={(e) => e.stopPropagation()}
      >
        <PopoverTrigger asChild>
          <button
            ref={trigger}
            type="button"
            className="inline-grid place-items-center w-5.5 h-5.5 pt-0 pr-0 pb-0 pl-0 border-0 rounded-[50%] bg-transparent cursor-pointer disabled:opacity-60 disabled:cursor-wait focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group disabled:opacity-[.6]"
            data-assignee-picker-trigger=""
            aria-label={`Assignee of ${issue.identifier}: ${issue.assignee?.name || "Unassigned"}`}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-busy={busy || undefined}
            disabled={disabled}
          >
            <span
              className={
                issue.assignee
                  ? "grid place-items-center w-5.5 h-5.5"
                  : "grid place-items-center w-5.5 h-5.5 rounded-[50%] bg-accent-soft text-accent text-[10px]"
              }
              data-my-issue-assignee=""
              aria-hidden="true"
            >
              {issue.assignee ? <UserBlobatar user={issue.assignee} /> : "·"}
            </span>
          </button>
        </PopoverTrigger>
        {open && (
          <PopoverContent asChild align="end" sideOffset={6}>
            <ul
              ref={menu}
              className="border shadow-[0_8px_24px_#0002] w-auto z-40 min-w-50 mt-0 mr-0 mb-0 ml-0 pt-1 pr-1 pb-1 pl-1 list-none border-line rounded-lg bg-panel"
              data-assignee-menu=""
              role="listbox"
              aria-label="Choose assignee"
              onKeyDown={menuArrowKeys}
            >
              <li>
                <button
                  className="flex items-center gap-2 w-full pt-1.5 pr-2 pb-1.5 pl-2 border-0 rounded-[5px] bg-transparent text-foreground text-[12px] text-left cursor-pointer hover:bg-subtle focus-visible:bg-subtle aria-selected:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
                  type="button"
                  role="option"
                  aria-selected={!issue.assignee}
                  onClick={() => void pick(null)}
                >
                  <span
                    className="grid place-items-center w-5.5 h-5.5 rounded-[50%] bg-accent-soft text-accent text-[10px]"
                    data-my-issue-assignee=""
                    aria-hidden="true"
                  >
                    ·
                  </span>
                  Unassigned
                </button>
              </li>
              {members.map((m) => (
                <li key={m.id}>
                  <button
                    className="flex items-center gap-2 w-full pt-1.5 pr-2 pb-1.5 pl-2 border-0 rounded-[5px] bg-transparent text-foreground text-[12px] text-left cursor-pointer hover:bg-subtle focus-visible:bg-subtle aria-selected:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
                    type="button"
                    role="option"
                    aria-selected={m.id === issue.assignee?.id}
                    onClick={() => void pick(m.id)}
                  >
                    <span
                      className="grid place-items-center w-5.5 h-5.5"
                      data-my-issue-assignee=""
                      aria-hidden="true"
                    >
                      <UserBlobatar user={m} />
                    </span>
                    {m.name || m.email}
                  </button>
                </li>
              ))}
            </ul>
          </PopoverContent>
        )}
        {error && (
          <span
            className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {error}
          </span>
        )}
      </span>
    </UIPopover>
  );
}

function matchesTerms(name: string, terms: string[]) {
  const n = name.toLowerCase();
  return terms.every((t) => n.includes(t));
}
const LIST_RENDER_STEP = 200;
const WINDOW_ROWS = 200;
function IssueList({
  issues,
  onOpen,
  emptyTitle,
  emptyText,
  header,
  emptyAction,
  error = "",
  teams = [],
  onStatusChange = async () => {},
}: {
  issues: Issue[];
  onOpen: (issue: Issue) => void;
  emptyTitle: string;
  emptyText: string;
  header?: string;
  emptyAction?: React.ReactNode;
  error?: string;
  teams?: Team[];
  onStatusChange?: (issueId: string, stateId: string) => Promise<void>;
}) {
  const [limit, setLimit] = useState(LIST_RENDER_STEP);
  const shown = issues.length > limit ? issues.slice(0, limit) : issues;
  return (
    <section
      className="border border-line rounded-xl shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
      data-issue-card=""
    >
      {header && (
        <div
          className="border-b border-line flex justify-between pt-3 pr-4 pb-3 pl-4 text-[12px]"
          data-card-head=""
        >
          <span>
            {header}{" "}
            <b className="ml-1.25 text-muted font-normal">{issues.length}</b>
          </span>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {issues.length ? (
        <>
          {shown.map((i) => (
            <div
              className="border-b border-line flex items-center gap-2.5 w-full pt-3.5 pr-4.5 pb-3.5 pl-4.5 bg-panel text-left min-h-14.5 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0"
              data-issue-row=""
              key={i.id}
            >
              <button
                type="button"
                className="flex items-center gap-2.5 flex-1 min-w-0 cursor-pointer focus-visible:rounded-md focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 focus-visible:-outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 border-0 p-0 bg-transparent [font:inherit] text-inherit text-left"
                data-issue-row-open=""
                onClick={() => onOpen(i)}
              >
                <span
                  className="inline-block w-2 h-2 shrink-0 rounded-[50%] bg-room-muted data-[p=1]:bg-red-500 data-[p=2]:bg-orange-500 data-[p=3]:bg-yellow-500 data-[p=4]:bg-blue-400 data-[p=1]:bg-subtle data-[p=2]:bg-subtle data-[p=3]:bg-subtle data-[p=4]:bg-subtle"
                  data-p={i.priority}
                />
                <span className="grid gap-1 flex-1 min-w-0" data-issue-copy="">
                  <b className="font-[550] block min-w-0 wrap-break-word">
                    {i.title}
                  </b>
                  <small className="text-muted text-[12px] block min-w-0 wrap-break-word">
                    {i.identifier} · {i.team?.name}
                  </small>
                </span>
              </button>
              <StatusPicker
                issue={i}
                teams={teams}
                onChange={(stateId) => onStatusChange(i.id, stateId)}
              />
            </div>
          ))}
          {issues.length > limit && (
            <div
              className="border-b border-line flex items-center gap-2.5 w-full pt-3.5 pr-4.5 pb-3.5 pl-4.5 bg-panel text-left min-h-14.5 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0"
              data-issue-row=""
            >
              <button
                type="button"
                className="flex items-center gap-2.5 flex-1 min-w-0 cursor-pointer focus-visible:rounded-md focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 focus-visible:-outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 border-0 p-0 bg-transparent [font:inherit] text-inherit text-left"
                data-issue-row-open=""
                onClick={() => setLimit((l) => l + LIST_RENDER_STEP)}
              >
                <span className="grid gap-1 flex-1 min-w-0" data-issue-copy="">
                  <b className="font-[550] block min-w-0 wrap-break-word">
                    Show {Math.min(LIST_RENDER_STEP, issues.length - limit)}{" "}
                    more
                  </b>
                  <small className="text-muted text-[12px] block min-w-0 wrap-break-word">
                    {issues.length - limit} not shown
                  </small>
                </span>
              </button>
            </div>
          )}
        </>
      ) : (
        <div
          className="grid justify-items-center gap-2 text-center pt-11.5 pr-5 pb-13 pl-5"
          data-inbox-empty=""
        >
          <div
            className="grid place-items-center w-16 h-16 rounded-2xl bg-accent-soft text-accent mt-0 mr-0 mb-0 ml-0"
            data-placeholder-icon=""
          >
            <Inbox size={23} />
          </div>
          <h2 className="mt-1.25 mr-0 mb-0 ml-0 text-[16px] tracking-[-.02em]">
            {emptyTitle}
          </h2>
          <p className="max-w-90 mt-0 mr-0 mb-1.75 ml-0 text-muted text-[12px] leading-[1.55]">
            {emptyText}
          </p>
          {emptyAction}
        </div>
      )}
    </section>
  );
}

// HOJA-288: rewrite a legacy /workspace/issue/... URL to /workspace/task/... in place
// before routing, preserving query and hash. replaceState keeps the back button sane.
function applyLegacyTaskRedirect(): string {
  const href =
    window.location.pathname + window.location.search + window.location.hash;
  const next = redirectLegacyTaskPath(href);
  if (next !== href) {
    history.replaceState({}, "", next);
  }
  return window.location.pathname;
}
function App() {
  const [setupResume, setSetupResume] = useState<{
    workspaceId: string;
    workspaceUrlKey: string;
  } | null>(null);
  const [pendingWorkspace, setPendingWorkspaceState] =
    useState<PendingWorkspace | null>(() => null);
  useEffect(() => {
    const onPop = () => {
      if (location.pathname !== "/login/workspace")
        setPendingWorkspaceState(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const setPendingWorkspace = (p: PendingWorkspace | null) => {
    setPendingWorkspaceState(p);
    history.pushState({}, "", p ? "/login/workspace" : "/login");
  };
  if (localStorage.getItem("hoja_token") === "session")
    localStorage.removeItem("hoja_token");
  const [token, setToken] = useState(storedApiToken());
  useInboxUnreadSync(Boolean(token));
  useEffect(() => {
    if (!token) return;
    const next = takeConsentReturn();
    if (next) location.assign(next);
  }, [token]);
  const [inviteCode, setInviteCode] = useState(
    () =>
      new URLSearchParams(location.search).get("invite") ||
      new URLSearchParams(location.hash.slice(1)).get("invite") ||
      "",
  );
  const [onboarding, setOnboarding] = useState<{
    checked: boolean;
    initialized: boolean;
    singleOrg: boolean;
  }>({ checked: false, initialized: true, singleOrg: false });
  const [hostedSignIn, setHostedSignIn] = useState(false);
  const [viewer, setViewer] = useState<Viewer>({});
  const [workspace, setWorkspace] = useState<{
    id: string;
    name: string;
    urlKey: string;
  }>({ id: "", name: "", urlKey: "" });
  const agentsOn = useAgentsSurfaceEnabled();
  // The workspace's Agents switch (Settings -> Workspace). Read per workspace; until it answers,
  // and if it fails, the switch reads as off.
  useEffect(() => {
    setWorkspaceAgentsEnabled(null);
    if (!workspace.id) return;
    let live = true;
    gql(`{ workspaceFeatures { agentsEnabled } }`)
      .then((d) => {
        if (live)
          setWorkspaceAgentsEnabled(
            Boolean(d.workspaceFeatures?.agentsEnabled),
          );
      })
      .catch(() => {
        if (live) setWorkspaceAgentsEnabled(false);
      });
    return () => {
      live = false;
    };
  }, [workspace.id]);
  const [workspaces, setWorkspaces] = useState<
    { organizationId: string; name: string; urlKey: string; role: string }[]
  >([]);
  const [switchError, setSwitchError] = useState("");
  const [switching, setSwitching] = useState(false);
  const [teams, setTeams] = useState<Team[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [olderMine, setOlderMine] = useState<OlderMine>({});
  const olderMineRef = useRef<OlderMine>({});
  const [activityEvents, setActivityEvents] = useState<IssueActivityEvent[]>(
    [],
  );
  const [activityCursor, setActivityCursor] = useState<string | null>(null);
  const [activityHasMore, setActivityHasMore] = useState(false);
  const [activityLoading, setActivityLoading] = useState(false);
  const [activityError, setActivityError] = useState("");
  const activityLoadRef = useRef(false);
  const activityGeneration = useRef(0);
  const issueLoadGeneration = useRef(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectCursor, setProjectCursor] = useState<string | null>(null);
  const [projectHasMore, setProjectHasMore] = useState(false);
  const [projectLoading, setProjectLoading] = useState(false);
  const [projectError, setProjectError] = useState("");
  const [projectDetailLoading, setProjectDetailLoading] = useState(false);
  const [projectDetailRefresh, setProjectDetailRefresh] = useState(0);
  const [projectDetailError, setProjectDetailError] = useState("");
  const projectLoadRef = useRef(false);
  const projectGeneration = useRef(0);
  const [labels, setLabels] = useState<
    { id: string; name: string; color?: string }[]
  >([]);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [activeCycle, setActiveCycle] = useState<Cycle | null>(null);
  const [cycleDetailError, setCycleDetailError] = useState(""),
    [cycleDetailLoading, setCycleDetailLoading] = useState(false);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [activeIssue, setActiveIssue] = useState<Issue | null>(null);
  const [issueReturnProject, setIssueReturnProject] = useState<string | null>(
    null,
  );
  const [issueReturn, setIssueReturn] = useState<{
    label: string;
    view: string;
    path: string;
  } | null>(null);
  const [issueLoading, setIssueLoading] = useState(false);
  const [issueError, setIssueError] = useState("");
  const [issueRetry, setIssueRetry] = useState(0);
  const [view, setView] = useState(() => routeView(applyLegacyTaskRedirect()));
  const [settingsSection, setSettingsSection] = useState(
    () => window.location.pathname.split("/")[3] || "preferences",
  );
  const settingsReturn = useRef("/workspace/my-issues/assigned");
  const [myIssuesTab, setMyIssuesTab] = useState<MyIssuesTab>(() =>
    window.location.pathname.split("/").pop() === "created"
      ? "Created"
      : window.location.pathname.split("/").pop() === "subscribed"
        ? "Subscribed"
        : window.location.pathname.split("/").pop() === "activity"
          ? "Activity"
          : "Assigned",
  );
  const [boardTab, setBoardTab] = useState<BoardTab>("All issues");
  const [reviewsLinks, setReviewsLinks] = useState<ReviewLink[]>([]);
  const [reviewsReload, setReviewsReload] = useState(0);
  const [reviewsError, setReviewsError] = useState("");
  const [reviewsTasks, setReviewsTasks] = useState<TaskInReviewInput[]>([]);
  const [reviewsTasksError, setReviewsTasksError] = useState("");
  /* The queue is read from the links themselves, not from the newest N tasks. Deriving it from
     issues(first:100,orderBy:updatedAt) tied the pane to how busy the workspace was: once more
     than 100 tasks had been touched, every linked pull request fell out of the window and the
     pane showed an empty queue while the links still existed. A failure is also shown now —
     `.catch(()=>{})` used to render an outage as the ordinary empty state. */
  useEffect(() => {
    if (view !== "Reviews") return;
    gql(
      `query ReviewsLinks{ pullRequests(first:250){ id kind externalId url title state author sha environment createdAt updatedAt additions deletions baseRef headRef behind ahead mergeable mergeState reviewDecision reviewers checksState checksTotal checksPassing checksFailing body mergedAt githubUpdatedAt files { path additions deletions status patch previousPath } issue { identifier } } }`,
    )
      .then((d: { pullRequests?: ReviewLink[] }) => {
        setReviewsLinks(
          (d.pullRequests || []).map((l) => ({
            ...l,
            key: l.issue?.identifier || "",
          })),
        );
        setReviewsError("");
      })
      .catch((e) => {
        setReviewsError(`Reviews could not load: ${(e as Error).message}`);
        setReviewsLinks([]);
      });
    // Tasks in review are a separate read: a failure here must not hide the pull requests.
    gql(
      `query ReviewsTasks{ issues(first:250, orderBy:updatedAt, filter:{state:{name:{eq:"In Review"}}}){ nodes { identifier title url updatedAt stateChangedAt githubLinks { id kind externalId url title state mergeable mergeState reviewDecision checksState checksTotal checksPassing checksFailing updatedAt issue { identifier } } } } }`,
    )
      .then((d: { issues?: { nodes?: TaskInReviewInput[] } }) => {
        setReviewsTasks(d.issues?.nodes ?? []);
        setReviewsTasksError("");
      })
      .catch((e) => {
        setReviewsTasks([]);
        setReviewsTasksError(
          `Tasks in review could not load: ${(e as Error).message}`,
        );
      });
  }, [view, reviewsReload]);
  const [boardTeam, setBoardTeam] = useState("");
  const [issueLayout, setIssueLayout] = useState<IssueLayout>(() =>
    localStorage.getItem("hoja_issue_layout") === "Board" ? "Board" : "List",
  );
  const changeIssueLayout = (layout: IssueLayout) => {
    localStorage.setItem("hoja_issue_layout", layout);
    setIssueLayout(layout);
  };
  const [teamCycle, setTeamCycle] = useState<Cycle | null>(null),
    [teamCycleRoute, setTeamCycleRoute] = useState(() => location.pathname),
    [teamCycleLoading, setTeamCycleLoading] = useState(false),
    [teamCycleError, setTeamCycleError] = useState("");
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "light");
  const [fontScale, setFontScale] = useState(
    localStorage.getItem("hoja_font_scale") || "100",
  );
  const [preferenceMessage, setPreferenceMessage] = useState("");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  useEffect(() => {
    document.documentElement.style.setProperty(
      "--font-scale",
      String(Number(fontScale) / 100),
    );
    localStorage.setItem("hoja_font_scale", fontScale);
  }, [fontScale]);
  const savePreference = async (key: string, value: string) => {
    setPreferenceMessage("Saving…");
    try {
      const d = await gql(
        `mutation($key:String!,$value:String!){userPreferenceSet(key:$key,value:$value){success theme fontSize}}`,
        { key, value },
      );
      if (!d.userPreferenceSet.success) throw Error("Preference was not saved");
      if (key === "theme") setTheme(value);
      else setFontScale(value);
      setPreferenceMessage("Saved to your account.");
    } catch (e) {
      setPreferenceMessage(`Could not save: ${(e as Error).message}`);
    }
  };
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Issue | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [newStateId, setNewStateId] = useState("");
  const [newTeamId, setNewTeamId] = useState("");
  const [projectOpen, setProjectOpen] = useState(false);
  const [mobile, setMobile] = useState(false);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const mobileSidebarRef = useRef<HTMLElement>(null);
  const closeMobileNav = useCallback(() => {
    setMobile(false);
    if (
      mobileToggleRef.current &&
      mobileToggleRef.current.offsetParent !== null
    )
      mobileToggleRef.current.focus();
  }, []);
  const [orgMenu, setOrgMenu] = useState(false);
  const [workspaceMenuWidth, setWorkspaceMenuWidth] = useState<number>();
  useEffect(() => {
    if (!orgMenu) return;
    const sidebar = mobileSidebarRef.current;
    if (!sidebar) return;
    const measure = () => {
      const styles = getComputedStyle(sidebar);
      setWorkspaceMenuWidth(
        sidebar.clientWidth -
          parseFloat(styles.paddingLeft) -
          parseFloat(styles.paddingRight),
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(sidebar);
    return () => observer.disconnect();
  }, [orgMenu]);
  const [commandOpen, setCommandOpen] = useState(false);
  /* The app-level "New initiative" dialog: N then I, the command palette and the list. */
  const [initiativeCreate, setInitiativeCreate] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [initiativeOptions, setInitiativeOptions] = useState<InitiativeRef[]>(
    [],
  );
  useEffect(() => {
    if (initiativeCreate)
      fetchAllInitiatives()
        .then(setInitiativeOptions)
        .catch(() => undefined);
  }, [initiativeCreate]);
  /* N then I opens it from anywhere outside a text field, as on Linear. */
  useEffect(() => {
    let armed = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        t?.closest("input,textarea,select,[contenteditable=true],[role=dialog]")
      )
        return;
      const key = e.key.toLowerCase();
      if (key === "n") armed = Date.now();
      else if (key === "i" && Date.now() - armed < 1200) {
        e.preventDefault();
        armed = 0;
        setInitiativeCreate({});
      } else armed = 0;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [commandQuery, setCommandQuery] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState<
    Record<string, boolean>
  >({});
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem("hoja_sidebar_collapsed") === "true",
  );
  useEffect(() => {
    localStorage.setItem("hoja_sidebar_collapsed", String(sidebarCollapsed));
  }, [sidebarCollapsed]);
  useEffect(() => {
    const appName = workspace.name || "Hoja";
    if (!token) {
      document.title = appName;
      return;
    }
    const pageTitle =
      view === "Issue" && activeIssue
        ? `${activeIssue.identifier} · ${activeIssue.title}`
        : view === "Project" && activeProject
          ? activeProject.name
          : view === "Cycle detail" && activeCycle
            ? activeCycle.name || `Cycle ${activeCycle.number ?? ""}`
            : view === "Settings"
              ? `Settings · ${settingsSection}`
              : displayLabel(view);
    document.title = pageTitle ? `${pageTitle} — ${appName}` : appName;
  }, [
    token,
    view,
    activeIssue,
    activeProject,
    activeCycle,
    settingsSection,
    workspace.name,
  ]);
  const [collapsedTeams, setCollapsedTeams] = useState<Record<string, boolean>>(
    {},
  );
  const [moreOpen, setMoreOpen] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([
      fetch("/onboarding/status").then((r) => r.json()),
      fetch("/auth/me", { credentials: "include" }).then(async (r) => {
        // A workspace with its own address is opened there, never on this host.
        if (r.status === 409) {
          const body = await r.json().catch(() => null);
          if (body?.redirectUrl) {
            window.location.replace(body.redirectUrl);
            return false;
          }
        }
        return r.ok;
      }),
      fetch("/api/signup/config").then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([v, session, config]) => {
        setOnboarding({
          checked: true,
          initialized: !!v.initialized,
          singleOrg: !!v.singleOrg,
        });
        setHostedSignIn(Boolean(config?.hosted && config?.signInUrl));
        if (session) setToken(localStorage.getItem("hoja_token") || "session");
      })
      .catch(() =>
        setOnboarding((v) => ({ ...v, checked: true, initialized: true })),
      );
  }, []);
  useEffect(() => {
    if (!token || !workspace.id || setupResume) return;
    const saved = loadDraft(localStorage, { edition: "cloud" });
    if (!saved || saved.step === "ready") return;
    const sameWorkspace = saved.workspaceId === workspace.id;
    const googleReturn = Boolean(saved.googleStarted && !saved.workspaceId);
    if (sameWorkspace || googleReturn)
      setSetupResume({
        workspaceId: workspace.id,
        workspaceUrlKey: workspace.urlKey,
      });
  }, [token, workspace.id, workspace.urlKey, setupResume]);
  useEffect(() => {
    if (error === "Authentication required") {
      localStorage.removeItem("hoja_token");
      setToken(null);
      setViewer({});
      setIssues([]);
      setActivityEvents([]);
    }
  }, [error]);
  const navigate = (next: string, pathOverride?: string) => {
    const slug = next.toLowerCase().replace(/ /g, "-");
    const path =
      pathOverride ||
      (next === "Board"
        ? "/workspace/team/eng/active"
        : next === "My issues"
          ? "/workspace/my-issues/assigned"
          : next === "Pulse"
            ? "/workspace/pulse/all"
            : next === "Inbox"
              ? "/workspace/inbox"
              : next === "Views"
                ? "/workspace/views/issues"
                : next === "Reviews"
                  ? "/workspace/reviews"
                  : next === "Initiatives"
                    ? "/workspace/initiatives/active"
                    : `/workspace/${slug}`);
    if (next === "Settings" && view !== "Settings")
      settingsReturn.current = location.pathname;
    history.pushState({}, "", path);
    if (next === "Settings")
      setSettingsSection(path.split("/")[3] || "preferences");
    if (next === "Team cycle") setTeamCycleRoute(path);
    setView(next);
    if (next === "My issues") {
      const part = path.split("/").pop();
      setMyIssuesTab(
        (
          {
            assigned: "Assigned",
            created: "Created",
            subscribed: "Subscribed",
            activity: "Activity",
          } as Record<string, MyIssuesTab>
        )[part || "assigned"] || "Assigned",
      );
    }
    setActiveProject(null);
    setActiveCycle(null);
  };
  const openInitiative = (id: string) =>
    navigate(
      "Initiative detail",
      `/workspace/initiative/${encodeURIComponent(id)}`,
    );
  const openMember = (userId: string) =>
    navigate(
      "Member profile",
      `/workspace/member/${encodeURIComponent(userId)}`,
    );
  const openTaskByRef = (identifier: string, title: string) => {
    const existing = issues.find(
      (i) => i.identifier.toLowerCase() === identifier.toLowerCase(),
    );
    if (existing) {
      openIssue(existing);
      return;
    }
    const slug = title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "");
    setActiveIssue(null);
    navigate(
      "Issue detail",
      `/workspace/task/${encodeURIComponent(identifier)}${slug ? `/${slug}` : ""}`,
    );
  };
  const openIssue = (issue: Issue, returnProjectId?: string) => {
    if (returnProjectId) {
      setIssueReturn({
        label: "Project",
        view: "Project detail",
        path: `/workspace/project/${returnProjectId}`,
      });
    } else if (view !== "Issue detail") {
      setIssueReturn({
        label: displayLabel(view),
        view,
        path: window.location.pathname,
      });
    }
    setIssueReturnProject(returnProjectId || null);
    setSelected(null);
    setActiveIssue(issue);
    const slug = issue.title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "");
    navigate(
      "Issue detail",
      `/workspace/task/${encodeURIComponent(issue.identifier)}/${slug}`,
    );
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen((v) => !v);
      }
      if (e.key === "Escape") {
        setCommandOpen(false);
        setFiltersOpen(false);
        setNewOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // The workspace drawer is the shared shell's phone navigation: Escape and a backdrop tap close it,
  // focus moves into the open drawer and returns to the toggle, so it matches the settings drawer.
  useEffect(() => {
    if (!mobile) return;
    const sidebar = mobileSidebarRef.current;
    const focusables = () =>
      sidebar
        ? [
            ...sidebar.querySelectorAll<HTMLElement>(
              "button:not(:disabled),a[href],input:not(:disabled)",
            ),
          ].filter((el) => el.offsetParent !== null)
        : [];
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeMobileNav();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0],
        last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mobile, closeMobileNav]);

  useEffect(() => {
    const onPop = () => {
      if (routeView(window.location.pathname) === "Team cycle")
        setTeamCycleRoute(window.location.pathname);
      if (routeView(window.location.pathname) === "Settings")
        setSettingsSection(
          window.location.pathname.split("/")[3] || "preferences",
        );
      if (
        ["Project detail", "Project view"].includes(
          routeView(window.location.pathname),
        )
      )
        setProjectDetailRefresh((n) => n + 1);
      setView(routeView(window.location.pathname));
      setIssueError("");
      setActiveIssue(null);
      setIssueReturn(null);
      const segment = window.location.pathname.split("/").pop();
      if (
        ["assigned", "created", "subscribed", "activity"].includes(
          segment || "",
        )
      )
        setMyIssuesTab(
          (
            {
              assigned: "Assigned",
              created: "Created",
              subscribed: "Subscribed",
              activity: "Activity",
            } as Record<string, MyIssuesTab>
          )[segment!],
        );
      setActiveProject(null);
      setActiveCycle(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  const [revision, setRevision] = useState(0);
  const syncRef = useRef<SyncHandle | null>(null);
  const [closedCutoff, setClosedCutoff] = useState("");
  useEffect(() => {
    if (!token || !workspace.id) return;
    let live = true;
    migrateLegacyViews(workspace.id)
      .then((count) => {
        if (live && count > 0) setRevision((r) => r + 1);
      })
      .catch(() => {
        /* a refused legacy row must not block the app */
      });
    return () => {
      live = false;
    };
  }, [token, workspace.id]);
  useEffect(() => {
    if (!token) return;
    let t: number | undefined;
    const off = connectRealtime(token, (ev) => {
      publishChangeEvents([ev]);
      window.clearTimeout(t);
      t = window.setTimeout(() => setRevision((r) => r + 1), 300);
    });
    return () => {
      window.clearTimeout(t);
      off();
    };
  }, [token]);
  useEffect(() => {
    if (!token) return;
    let live = true;
    let meta: SyncMeta | null = null;
    setDataLoading(true);
    setError("");
    const handle = startSync({
      headers: (): Record<string, string> => {
        const t = storedApiToken();
        return t ? { authorization: `Bearer ${t}` } : {};
      },
      onView: (v) => {
        if (!live) return;
        setDataLoading(false);
        if (v.reset) setError("");
        setIssues(v.issues);
        if (v.meta !== meta) {
          meta = v.meta;
          setViewer(v.meta.viewer);
          setWorkspace(v.meta.workspace as typeof workspace);
          setTeams(v.meta.teams.nodes);
          setProjects(v.meta.projects.nodes);
          setProjectCursor(v.meta.projects.pageInfo?.endCursor || null);
          setProjectHasMore(Boolean(v.meta.projects.pageInfo?.hasNextPage));
          setCycles(v.meta.cycles.nodes);
          setLabels(v.meta.issueLabels.nodes);
        }
        if (v.reset) {
          setClosedCutoff(v.closedCutoff);
          issueLoadGeneration.current++;
          olderMineRef.current = {};
          setOlderMine({});
        }
      },
      onError: (e) => {
        if (live) {
          setError(e.message);
          setDataLoading(false);
        }
      },
    });
    syncRef.current = handle;
    gql(`query MyPreferences{myPreferences{success theme fontSize}}`)
      .then((prefs) => {
        if (live && prefs.myPreferences.success) {
          setTheme(prefs.myPreferences.theme);
          setFontScale(prefs.myPreferences.fontSize);
        }
      })
      .catch((e) => {
        if (live)
          setError(`Could not load preferences: ${(e as Error).message}`);
      });
    return () => {
      live = false;
      handle.stop();
      if (syncRef.current === handle) syncRef.current = null;
    };
  }, [token]);
  useEffect(() => {
    if (!token) return;
    gql(`query MyWorkspaces{myWorkspaces{id organizationId name urlKey role}}`)
      .then((d) => setWorkspaces(d.myWorkspaces))
      .catch((e) => setSwitchError(e.message));
  }, [token, workspace.id]);
  const switchWorkspace = async (id: string) => {
    if (!id || id === workspace.id) return;
    setSwitching(true);
    setSwitchError("");
    const previous = {
      workspace,
      teams,
      issues,
      projects,
      labels,
      cycles,
      viewer,
    };
    let switched = false;
    try {
      const d = await gql(
        `mutation($id:String!){workspaceSwitch(organizationId:$id){success redirectUrl organization{id name urlKey}}}`,
        { id },
      );
      if (!d.workspaceSwitch.success) throw Error("Workspace switch denied");
      if (d.workspaceSwitch.redirectUrl) {
        // That workspace lives on its own address: this host's session stays
        // as it is, and the browser signs in there instead.
        window.location.assign(d.workspaceSwitch.redirectUrl);
        return;
      }
      switched = true;
      localStorage.removeItem("hoja_saved_views_" + workspace.id);
      setWorkspace(d.workspaceSwitch.organization);
      setTeams([]);
      setIssues([]);
      setActivityEvents([]);
      setActivityCursor(null);
      setActivityHasMore(false);
      setActivityError("");
      activityGeneration.current++;
      activityLoadRef.current = false;
      setActivityLoading(false);
      issueLoadGeneration.current++;
      olderMineRef.current = {};
      setOlderMine({});
      projectGeneration.current++;
      setProjects([]);
      setProjectCursor(null);
      setProjectHasMore(false);
      setProjectError("");
      setLabels([]);
      setCycles([]);
      setActiveProject(null);
      setSelected(null);
      setSearch("");
      setFilter("");
      setView("Inbox");
      history.pushState({}, "", `/workspace/inbox`);
      await syncRef.current?.reset();
      setWorkspace(d.workspaceSwitch.organization);
    } catch (e) {
      if (!switched) {
        setWorkspace(previous.workspace);
        setTeams(previous.teams);
        setIssues(previous.issues);
        setProjects(previous.projects);
        setLabels(previous.labels);
        setCycles(previous.cycles);
        setViewer(previous.viewer);
      }
      setSwitchError((e as Error).message);
    } finally {
      setSwitching(false);
    }
  };
  /* eslint-disable react-hooks/exhaustive-deps -- route or session driven; the listed values are written by this effect or are recreated each render */
  useEffect(() => {
    if (!token || view !== "Issue detail") return;
    const ident = decodeURIComponent(
      window.location.pathname.split("/").filter(Boolean)[2] || "",
    );
    if (!ident) {
      setIssueError("Missing task identifier");
      setIssueLoading(false);
      return;
    }
    setIssueError("");
    const known =
      peekIssue(ident) ||
      (cachedIssueDetail(ident)?.detail as Issue | undefined);
    if (known) {
      setActiveIssue((old) => (old && old.id === known.id ? old : known));
      setIssueLoading(false);
      return;
    }
    const hintId =
      activeIssue &&
      (activeIssue.identifier === ident || activeIssue.id === ident)
        ? activeIssue.id
        : undefined;
    let live = true;
    setIssueLoading(true);
    setActiveIssue((old) =>
      old && (old.identifier === ident || old.id === ident) ? old : null,
    );
    const offStore = onStoreIssue(ident, (i) => {
      if (live) {
        setActiveIssue((old) => (old && old.id === i.id ? old : i));
        setIssueLoading(false);
      }
    });
    const issueTimeout = setTimeout(() => {
      if (live && !peekIssue(ident)) {
        setIssueError(
          "Loading the task timed out. Check your connection and retry.",
        );
        setIssueLoading(false);
      }
    }, 20000);
    fetchIssueDetail(ident, hintId)
      .then((e) => {
        if (live)
          setActiveIssue((old) =>
            old && old.id === e.detail.id ? old : (e.detail as Issue),
          );
      })
      .catch((e) => {
        if (live && !peekIssue(ident)) {
          setIssueError((e as Error).message);
          setActiveIssue(null);
        }
      })
      .finally(() => {
        if (live) {
          clearTimeout(issueTimeout);
          setIssueLoading(false);
        }
      });
    return () => {
      live = false;
      offStore();
      clearTimeout(issueTimeout);
    };
  }, [token, view, window.location.pathname, issueRetry]);
  /* eslint-enable react-hooks/exhaustive-deps */
  const projectRouteId =
    view === "Project detail" || view === "Project view"
      ? window.location.pathname.split("/").filter(Boolean)[2]
      : undefined;
  const projectVersion = useEntityVersion(["project"], [projectRouteId]);
  /* eslint-disable react-hooks/exhaustive-deps -- route or session driven; the listed values are written by this effect or are recreated each render */
  useEffect(() => {
    if (!token || (view !== "Project detail" && view !== "Project view"))
      return;
    const projectId = window.location.pathname.split("/").filter(Boolean)[2];
    if (!projectId) return;
    const known = projects.find((p) => p.id === projectId);
    if (known) {
      setActiveProject(known);
      setProjectDetailError("");
      setProjectDetailLoading(false);
      return;
    }
    let live = true;
    setProjectDetailLoading(true);
    setProjectDetailError("");
    if (activeProject?.id !== projectId) setActiveProject(null);
    gql(
      `query($id:String!){project(id:$id){id name description state progress startDate targetDate health lead{name} teams{nodes{id name key}}}}`,
      { id: projectId },
    )
      .then((d) => {
        if (!live) return;
        if (!d.project) throw Error("Project not found");
        setActiveProject(d.project);
        setProjects((old) =>
          old.some((p) => p.id === d.project.id)
            ? old.map((p) => (p.id === d.project.id ? d.project : p))
            : [...old, d.project],
        );
      })
      .catch((e) => {
        if (live) {
          setProjectDetailError((e as Error).message);
          setActiveProject(null);
        }
      })
      .finally(() => {
        if (live) setProjectDetailLoading(false);
      });
    return () => {
      live = false;
    };
  }, [token, view, projectDetailRefresh, projectVersion, projects]);
  /* eslint-enable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!token || view !== "Team cycle") return;
    const path = location.pathname;
    const parts = path.split("/").filter(Boolean);
    const team = teams.find(
      (t) => t.key.toLowerCase() === parts[2]?.toLowerCase(),
    );
    if (!team) {
      setTeamCycle(null);
      setTeamCycleLoading(false);
      setTeamCycleError("Team not found in this workspace.");
      return;
    }
    const upcoming = parts[4] === "upcoming";
    if (!upcoming && team.activeCycle) {
      setTeamCycle({
        ...team.activeCycle,
        team: { id: team.id, key: team.key, name: team.name },
      });
      setTeamCycleError("");
      setTeamCycleLoading(false);
      return;
    }
    let live = true;
    setTeamCycle(null);
    setTeamCycleLoading(true);
    setTeamCycleError("");
    gql(
      `query TeamCycle($filter:CycleFilter){cycles(first:10,filter:$filter){nodes{id name number startsAt endsAt completedAt progress team{id key name}}}}`,
      {
        filter: {
          team: { id: { eq: team.id } },
          [upcoming ? "isNext" : "isActive"]: { eq: true },
        },
      },
    )
      .then((d) => {
        if (live && location.pathname === path)
          setTeamCycle(d.cycles.nodes[0] || null);
      })
      .catch((e) => {
        if (live && location.pathname === path)
          setTeamCycleError((e as Error).message);
      })
      .finally(() => {
        if (live && location.pathname === path) setTeamCycleLoading(false);
      });
    return () => {
      live = false;
    };
  }, [token, view, workspace.id, teams, teamCycleRoute]);
  useEffect(() => {
    if (!token || view !== "Cycle detail") return;
    const id = location.pathname.split("/").filter(Boolean)[2];
    if (!id) return;
    let live = true;
    const known = cycles.find((c) => c.id === id);
    setCycleDetailError("");
    if (known) {
      setActiveCycle((old) =>
        old?.id === id ? { ...known, description: old.description } : known,
      );
      setCycleDetailLoading(false);
    } else {
      setCycleDetailLoading(true);
      setActiveCycle(null);
    }
    gql(
      `query OneCycle($id:String!){cycle(id:$id){id name description number startsAt endsAt completedAt progress team{id key name}}}`,
      { id },
    )
      .then((d) => {
        if (!live) return;
        if (!d.cycle) throw Error("Cycle not found in this workspace.");
        setActiveCycle(d.cycle);
      })
      .catch((e) => {
        if (live && !known) setCycleDetailError((e as Error).message);
      })
      .finally(() => {
        if (live) setCycleDetailLoading(false);
      });
    return () => {
      live = false;
    };
  }, [token, view, workspace.id, cycles]);
  const loadMoreProjects = async () => {
    if (projectLoadRef.current || !projectHasMore || !projectCursor) return;
    projectLoadRef.current = true;
    const generation = projectGeneration.current;
    setProjectLoading(true);
    setProjectError("");
    try {
      const d = await gql(
        `query MoreProjects($after:String){projects(first:100,after:$after){nodes{id name description state progress startDate targetDate health lead{name} teams{nodes{id name key}}} pageInfo{hasNextPage endCursor}}}`,
        { after: projectCursor },
      );
      if (generation !== projectGeneration.current) return;
      const page = d.projects;
      if (
        page.pageInfo?.hasNextPage &&
        (!page.pageInfo.endCursor ||
          page.pageInfo.endCursor === projectCursor ||
          !page.nodes.length)
      )
        throw Error("Projects pagination did not advance.");
      setProjects((old) => {
        const ids = new Set(old.map((p) => p.id));
        return [...old, ...page.nodes.filter((p: Project) => !ids.has(p.id))];
      });
      setProjectCursor(page.pageInfo?.endCursor || null);
      setProjectHasMore(Boolean(page.pageInfo?.hasNextPage));
    } catch (e) {
      if (generation === projectGeneration.current)
        setProjectError((e as Error).message);
    } finally {
      projectLoadRef.current = false;
      if (generation === projectGeneration.current) setProjectLoading(false);
    }
  };
  // Live activity state: one in-flight request at a time. A refresh dropped by a load-more (or the
  // reverse) is queued once and retried when the in-flight request settles, so neither is lost.
  const activityRows = useRef(activityEvents);
  activityRows.current = activityEvents;
  const activityNext = useRef<{
    after: string | null;
    refresh: boolean;
  } | null>(null);
  const loadActivity = async (after: string | null = null, refresh = false) => {
    if (activityLoadRef.current) {
      activityNext.current = { after, refresh };
      return;
    }
    activityLoadRef.current = true;
    const generation = activityGeneration.current;
    // A background refresh keeps the rows on screen instead of flipping the panel back to "Loading activity…".
    if (!refresh) setActivityLoading(true);
    setActivityError("");
    try {
      const d = await gql(
        `query MyIssueActivity($after:String){myIssueActivity(first:50,after:$after){nodes{id eventType createdAt actor{name avatarSeed} changes issue ${issueFields}} pageInfo{hasNextPage endCursor}}}`,
        { after },
      );
      if (generation !== activityGeneration.current) return;
      const page = d.myIssueActivity;
      const events: IssueActivityEvent[] = page.nodes;
      const next = page.pageInfo;
      if (
        next?.hasNextPage &&
        (!next.endCursor || next.endCursor === after || !events.length)
      )
        throw Error("Activity pagination did not advance. Retry later.");
      if (refresh) {
        // Newest page first, then the pages already loaded, de-duplicated by event id. The loaded tail keeps
        // its own cursor, so "Load more activity" still continues from where it stopped. If more than a page
        // of new events landed since the last refresh the old rows sit past a gap, so restart from this page.
        const previous = activityRows.current;
        const known = new Set(previous.map((e) => e.id));
        const continuous =
          !previous.length || events.some((e) => known.has(e.id));
        setActivityEvents((old) => {
          if (!continuous) return events;
          const seen = new Set(events.map((e) => e.id));
          return [...events, ...old.filter((e) => !seen.has(e.id))].sort(
            (a, b) => b.createdAt.localeCompare(a.createdAt),
          );
        });
        if (!continuous || !previous.length) {
          setActivityCursor(next?.endCursor || null);
          setActivityHasMore(Boolean(next?.hasNextPage));
        }
      } else {
        setActivityEvents((previous) => {
          const seen = new Set((after ? previous : []).map((e) => e.id));
          return [
            ...(after ? previous : []),
            ...events.filter((e) => !seen.has(e.id)),
          ];
        });
        setActivityCursor(next?.endCursor || null);
        setActivityHasMore(Boolean(next?.hasNextPage));
      }
    } catch (e) {
      if (generation === activityGeneration.current)
        setActivityError((e as Error).message);
    } finally {
      activityLoadRef.current = false;
      if (generation === activityGeneration.current) setActivityLoading(false);
      const queued = activityNext.current;
      activityNext.current = null;
      if (queued && generation === activityGeneration.current)
        void loadActivity(queued.after, queued.refresh);
    }
  };
  // Stable handle for the effects below: they call through the ref, so their dependency lists stay honest.
  const loadActivityRef = useRef(loadActivity);
  loadActivityRef.current = loadActivity;
  useEffect(() => {
    if (token && view === "My issues" && myIssuesTab === "Activity")
      void loadActivityRef.current();
  }, [token, view, myIssuesTab, workspace.id]);
  // The activity panel follows the same realtime feed that keeps the issue list current: issue/comment
  // change events bump `activityVersion`, which refetches the newest page and merges it (see loadActivity).
  const activityVersion = useEntityVersion(
    ["issue", "comment"],
    undefined,
    500,
  );
  const activitySeenVersion = useRef(activityVersion);
  useEffect(() => {
    const fresh = activityVersion !== activitySeenVersion.current;
    activitySeenVersion.current = activityVersion;
    if (fresh && token && view === "My issues" && myIssuesTab === "Activity")
      void loadActivityRef.current(null, true);
  }, [activityVersion, token, view, myIssuesTab]);
  // A reconnect the server answers with {reset:true} carries no events, so refresh the panel directly.
  useEffect(() => {
    if (!(token && view === "My issues" && myIssuesTab === "Activity")) return;
    return onRealtimeFrame((frame) => {
      if (frame.reset) void loadActivityRef.current(null, true);
    });
  }, [token, view, myIssuesTab]);
  // Closed issues older than the sync window, limited to the viewer's own (assigned or created). Fetched once per
  // sync generation when a My issues view needs them, then adopted into the store so they stay live.
  const loadOlderMine = async (field: OlderField, retry = false) => {
    const cur = olderMineRef.current[field];
    if (
      !closedCutoff ||
      !viewer.id ||
      (cur && !(retry && cur.status === "error"))
    )
      return;
    const generation = issueLoadGeneration.current;
    const set = (v: NonNullable<OlderMine[OlderField]>) => {
      if (generation !== issueLoadGeneration.current) return;
      olderMineRef.current = { ...olderMineRef.current, [field]: v };
      setOlderMine(olderMineRef.current);
    };
    set({ status: "loading" });
    let after: string | null = null;
    try {
      do {
        const d = await gql(
          `query MyOlderClosedIssues($after:String,$filter:IssueFilter){issues(first:100,after:$after,filter:$filter,orderBy:updatedAt){nodes ${issueFields} pageInfo{hasNextPage endCursor}}}`,
          {
            after,
            filter: {
              [field]: { id: { eq: viewer.id } },
              state: { type: { in: ["completed", "canceled"] } },
              updatedAt: { lt: closedCutoff },
            },
          },
        );
        if (generation !== issueLoadGeneration.current) return;
        const page = d.issues;
        const rows: Issue[] = page.nodes;
        if (
          page.pageInfo?.hasNextPage &&
          (!page.pageInfo.endCursor ||
            page.pageInfo.endCursor === after ||
            !rows.length)
        )
          throw Error("Task pagination did not advance. Retry later.");
        if (rows.length) {
          if (syncRef.current) syncRef.current.adopt(rows);
          else
            setIssues((previous) => {
              const seen = new Set(previous.map((x) => x.id));
              return [...previous, ...rows.filter((x) => !seen.has(x.id))];
            });
        }
        after = page.pageInfo?.hasNextPage ? page.pageInfo.endCursor : null;
      } while (after);
      set({ status: "done" });
    } catch (e) {
      set({ status: "error", error: (e as Error).message });
    }
  };
  const loadOlderRef = useRef(loadOlderMine);
  loadOlderRef.current = loadOlderMine;
  const olderField: OlderField =
    myIssuesTab === "Created" ? "creator" : "assignee";
  const onLoadOlder = useCallback(
    (retry?: boolean) => {
      void loadOlderRef.current(olderField, retry);
    },
    [olderField],
  );
  const filtered = useMemo(() => {
    const base = issues.filter(
      (i) =>
        (!search ||
          `${i.identifier} ${i.title}`
            .toLowerCase()
            .includes(search.toLowerCase())) &&
        (!filter || i.state?.name === filter),
    );
    const mine = view === "My issues" && myIssuesTab === "Assigned";
    return base.filter((i) => !mine || i.assignee?.id === viewer.id);
  }, [issues, search, filter, view, viewer.id, myIssuesTab]);
  // /signup is its own screen, outside the workspace shell: a person signing up has no workspace yet, and it must render for both an anonymous browser (the Google button) and a google_verified one (choosing an address). Checked before the auth gates so a Google callback that returns to /signup lands on the page rather than on /login.
  if (
    location.pathname === "/signup" ||
    location.pathname.startsWith("/signup/")
  )
    return <SignupPage />;
  if (inviteCode)
    return (
      <InviteAcceptance
        code={inviteCode}
        onDone={() => {
          history.replaceState({}, "", "/");
          setInviteCode("");
          setToken("session");
        }}
      />
    );
  if (!token && !onboarding.checked)
    return (
      <div className="app min-h-dvh has-data-workspace-page:h-dvh has-data-workspace-page:min-w-0 has-data-workspace-page:overflow-x-clip shell-phone:has-data-workspace-page:flex shell-phone:has-data-workspace-page:flex-col shell-phone:has-data-workspace-page:overflow-hidden shell-desktop:grid shell-desktop:grid-cols-[244px_minmax(0,1fr)] shell-desktop:grid-rows-[48px_minmax(calc(100dvh-48px),auto)]">
        <header
          className="border-b border-line shadow-[0_2px_10px_#1b1b1b08] h-17 flex items-center gap-5.5 pt-0 pr-7 pb-0 pl-7 bg-panel sticky top-0 z-30 max-sm:pt-0 max-sm:pr-3.5 max-sm:pb-0 max-sm:pl-3.5 max-sm:gap-2 max-lg:pt-0 max-lg:pr-4 max-lg:pb-0 max-lg:pl-4 max-lg:gap-3 max-md:h-12 max-md:pt-0 max-md:pr-5 max-md:pb-0 max-md:pl-5 max-md:min-w-0 max-md:overflow-visible shell-phone:relative shell-phone:z-40 shell-phone:h-15 shell-phone:px-3 shell-phone:gap-2 shell-desktop:col-start-2 shell-desktop:row-start-1 shell-desktop:h-12 shell-desktop:px-5 route-fullbleed:hidden shell-phone:route-fullbleed:flex shell-phone:route-fullbleed:h-15 shell-phone:route-fullbleed:relative shell-phone:route-fullbleed:z-40 shell-desktop:data-headerless:hidden! route-review:hidden! [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-3.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-3.5 [@media_(max-width:560px)]:gap-3 [@media_(max-width:900px)]:pt-0 [@media_(max-width:900px)]:pr-4.5 [@media_(max-width:900px)]:pb-0 [@media_(max-width:900px)]:pl-4.5 [@media_(max-width:900px)]:gap-3 shell-phone:pt-0 shell-phone:pr-3 shell-phone:pb-0 shell-phone:pl-3 shell-phone:min-w-0 [@media_(max-width:420px)]:gap-2 shell-desktop:pt-0 shell-desktop:pr-5 shell-desktop:pb-0 shell-desktop:pl-5"
          data-topbar
        >
          <span className="inline-flex items-center gap-2.25 bg-transparent pt-0 pr-0 pb-0 pl-0 text-[16px] font-bold tracking-[-.02em] flex flex-none max-md:hidden">
            <img
              className="block w-5 h-5 object-contain"
              data-brand-mark=""
              src="/hoja-clover.8371400e.svg"
              alt=""
            />
            <span className="max-sm:last:hidden max-md:last:inline">Hoja</span>
          </span>
        </header>
        <div
          className="flex shell-phone:flex shell-phone:flex-1 shell-phone:min-h-0 shell-phone:overflow-hidden shell-desktop:contents shell-phone:min-h-[calc(100dvh-60px)] min-h-[calc(100dvh-68px)]"
          data-app-body=""
        >
          <main
            data-main
            className="main max-w-360 w-full pt-10 pr-[clamp(24px,4vw,64px)] pb-10 pl-[clamp(24px,4vw,64px)] mt-0 mr-auto mb-0 ml-auto max-md:pt-6 max-md:pr-4 max-md:pb-6 max-md:pl-4 max-sm:pt-5 max-sm:pr-3 max-sm:pb-5 max-sm:pl-3 flex-1 min-w-0 max-lg:pt-7 max-lg:pr-6 max-lg:pb-7 max-lg:pl-6 has-data-settings-page:mt-2 has-data-settings-page:mr-2 has-data-settings-page:mb-2 has-data-settings-page:rounded-xl has-data-workspace-page:w-full! has-data-workspace-page:m-0! has-data-workspace-page:p-0! has-data-workspace-page:max-w-none! has-data-workspace-page:min-w-0! has-data-workspace-page:min-h-0! has-data-workspace-page:flex! has-data-workspace-page:flex-col! has-data-workspace-page:overflow-hidden! has-data-workspace-page:bg-sidebar! has-data-workspace-page:rounded-none! overflow-hidden bg-bg text-foreground rounded-none flex flex-col min-h-0 h-full max-h-full shell-phone:h-auto [@media_(max-width:850px)]:pt-7 [@media_(max-width:850px)]:pr-5.5 [@media_(max-width:850px)]:pb-7 [@media_(max-width:850px)]:pl-5.5 [@media_(max-width:560px)]:pt-6 [@media_(max-width:560px)]:pr-3.5 [@media_(max-width:560px)]:pb-6 [@media_(max-width:560px)]:pl-3.5 [@media_(max-width:900px)]:pt-7.5 [@media_(max-width:900px)]:pr-6 [@media_(max-width:900px)]:pb-7.5 [@media_(max-width:900px)]:pl-6 shell-phone:pt-6.25 shell-phone:pr-4 shell-phone:pb-6.25 shell-phone:pl-4 [@media_(max-width:420px)]:pt-5.5 [@media_(max-width:420px)]:pr-3 [@media_(max-width:420px)]:pb-5.5 [@media_(max-width:420px)]:pl-3 has-data-pulse-page:w-[calc(100%-16px)] has-data-pulse-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-pulse-page:w-[calc(100%-8px)] shell-phone:has-data-pulse-page:min-h-[calc(100dvh-60px)] has-data-inbox-page:w-[calc(100%-16px)] has-data-inbox-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-inbox-page:w-[calc(100%-8px)] shell-phone:has-data-inbox-page:min-h-[calc(100dvh-60px)] has-data-initiatives-page:w-[calc(100%-16px)] has-data-initiatives-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-initiatives-page:w-[calc(100%-8px)] shell-phone:has-data-initiatives-page:min-h-[calc(100dvh-60px)] has-data-projects-reference:w-[calc(100%-8px)] has-data-projects-reference:min-h-[calc(100dvh-16px)] shell-phone:has-data-projects-reference:min-h-[calc(100dvh-60px)] has-data-project-reference:w-[calc(100%-8px)] has-data-project-reference:min-h-[calc(100dvh-16px)] shell-phone:has-data-project-reference:min-h-[calc(100dvh-60px)] has-data-workspace-views-page:w-[calc(100%-8px)] has-data-workspace-views-page:min-h-[calc(100dvh-16px)] shell-phone:has-data-workspace-views-page:min-h-[calc(100dvh-60px)] shell-desktop:has-data-my-issues-view:w-[calc(100%-16px)] shell-desktop:has-data-my-issues-view:min-h-[calc(100dvh-16px)] shell-phone:[&:has([data-my-issues-view])_[data-my-issue-tabs]]:scrollbar-none"
            aria-busy="true"
          >
            <p
              role="status"
              className="pt-10 pr-0 pb-10 pl-0 text-center text-muted text-[13px]"
            >
              Checking your workspace…
            </p>
            <div aria-hidden="true">
              <span
                className="skeleton block rounded-md bg-subtle h-6.5 mb-5 w-[70%] animate-[skeleton-pulse_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
                data-skeleton-title=""
              />
              <span
                className="skeleton block rounded-md bg-subtle h-3 w-full mb-3 animate-[skeleton-pulse_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
                data-skeleton-line=""
              />
              <span
                className="skeleton block rounded-md bg-subtle h-3 w-full mb-3 animate-[skeleton-pulse_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
                data-skeleton-line=""
              />
              <span
                className="skeleton block rounded-md bg-subtle h-3 w-[55%] mb-3 animate-[skeleton-pulse_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
                data-skeleton-line=""
              />
              <span
                className="skeleton block rounded-md bg-subtle h-3 w-[55%] mb-3 animate-[skeleton-pulse_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
                data-skeleton-line=""
              />
            </div>
          </main>
        </div>
      </div>
    );
  if (!token && !onboarding.initialized && !hostedSignIn)
    return <SetupWizard adapter={setupAdapter} mode="first-run" />;
  if (!token && pendingWorkspace)
    return (
      <WorkspaceChooser
        pending={pendingWorkspace}
        onBack={() => setPendingWorkspace(null)}
        onLogin={() => {
          setPendingWorkspaceState(null);
          setToken("session");
          setError("");
          history.replaceState({}, "", "/workspace/my-issues/assigned");
          setView("My issues");
        }}
      />
    );
  if (!token)
    return (
      <Login
        onWorkspaceRequired={setPendingWorkspace}
        onLogin={(t) => {
          if (t !== "session") localStorage.setItem("hoja_token", t);
          setToken(t);
          setError("");
          history.replaceState({}, "", "/workspace/my-issues/assigned");
          setView("My issues");
        }}
        error={error}
      />
    );
  const setMyTab = (tab: MyIssuesTab) => {
    setMyIssuesTab(tab);
    const path = `/workspace/my-issues/${tab.toLowerCase()}`;
    history.pushState({}, "", path);
  };
  /* A composer opened from a team surface has to default to that team; without this it
   * always fell back to the first team in the workspace, so a task created from Design
   * landed in Engineering. Workspace-wide surfaces have no team in the path and keep
   * the old default. */
  const teamIdFromRoute = () => {
    const parts = location.pathname.split("/");
    const at = parts.indexOf("team");
    const key = at >= 0 ? parts[at + 1] : "";
    return key
      ? teams.find((t) => t.key.toLowerCase() === key.toLowerCase())?.id || ""
      : "";
  };
  const openNewIssue = (stateId?: string) => {
    setNewStateId(stateId || "");
    setNewTeamId(teamIdFromRoute());
    setNewOpen(true);
  };
  const allStates = teams.flatMap((t) => t.states.nodes);
  const uniqueStates = [
    ...new Map(allStates.map((s) => [stateKey(s), s])).values(),
  ];
  const logout = async () => {
    setOrgMenu(false);
    setMoreOpen(false);
    setMobile(false);
    /* Cookies are host-only, so this POST can only end this host's session.
     * The control plane's `hoja_signup_session` lives on app.hoja.work and would
     * silently resume the same identity on the next sign-in, so the server
     * answers with the one URL that can finish the job and we navigate there at
     * top level. Resetting state here and staying put is what "sign out, then
     * sign in again, and I'm back in" looked like. */
    let signOutUrl = "";
    try {
      const response = await fetch("/auth/logout", {
        method: "POST",
        credentials: "include",
      });
      const body = await response.json().catch(() => null);
      if (response.ok && typeof body?.signOutUrl === "string")
        signOutUrl = body.signOutUrl;
    } finally {
      void syncRef.current?.forget();
      try {
        for (const k of Object.keys(sessionStorage))
          if (k.startsWith("hoja_page_cache:")) sessionStorage.removeItem(k);
      } catch {
        /* storage unavailable */
      }
      localStorage.removeItem("hoja_token");
      setToken(null);
      setViewer({});
      setWorkspace({ id: "", name: "", urlKey: "" });
      setTeams([]);
      setIssues([]);
      setActivityEvents([]);
      setActivityCursor(null);
      setActivityHasMore(false);
      setActivityError("");
      activityGeneration.current++;
      activityLoadRef.current = false;
      setActivityLoading(false);
      issueLoadGeneration.current++;
      olderMineRef.current = {};
      setOlderMine({});
      projectGeneration.current++;
      setProjects([]);
      setProjectCursor(null);
      setProjectHasMore(false);
      setProjectError("");
      setLabels([]);
      setCycles([]);
    }
    if (signOutUrl) location.assign(signOutUrl);
  };
  const moveIssueState = async (issueId: string, state: IssueState) => {
    const issue = issues.find((i) => i.id === issueId);
    const teamStates = issue?.team?.id
      ? teams.find((t) => t.id === issue.team?.id)?.states.nodes
      : undefined;
    const target = teamStates
      ? teamStates.find((s) => stateKey(s) === stateKey(state))
      : state;
    if (!target) throw Error("This team has no matching status");
    const settle = syncRef.current?.patch(issueId, { state: target });
    try {
      const d = await gql(
        `mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}`,
        { id: issueId, input: { stateId: target.id } },
      );
      if (!d.issueUpdate.success) throw Error("Could not move task");
    } catch (e) {
      settle?.(false);
      throw e;
    }
    if (settle) settle(true);
    else
      setIssues((old) =>
        old.map((i) => (i.id === issueId ? { ...i, state: target } : i)),
      );
  };
  const updateIssue = async (
    issueId: string,
    input: Record<string, unknown>,
  ) => {
    const sync = syncRef.current;
    const optimistic = sync ? sync.patchFromInput(input) : {};
    const before =
      activeIssue && activeIssue.id === issueId ? activeIssue : null;
    const settle = sync?.patch(issueId, optimistic);
    if (before)
      setActiveIssue((old) =>
        old && old.id === issueId ? { ...old, ...optimistic } : old,
      );
    let updated: Issue;
    try {
      const d = await gql(
        `mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue ${issueFields}}}`,
        { id: issueId, input },
      );
      if (!d.issueUpdate.success || !d.issueUpdate.issue)
        throw Error("Update was rejected");
      updated = d.issueUpdate.issue;
    } catch (e) {
      settle?.(false);
      if (before)
        setActiveIssue((old) => (old && old.id === issueId ? before : old));
      throw e;
    }
    settle?.(true);
    setActiveIssue((old) =>
      old && old.id === issueId ? { ...old, ...updated } : old,
    );
    if (!sync)
      setIssues((old) =>
        old.map((i) => (i.id === issueId ? { ...i, ...updated } : i)),
      );
  };
  /* The generic workspace pages (Inbox, Pulse fallbacks) list the recent issues under their head. */
  const issueListContent =
    dataLoading && !issues.length ? (
      <p
        role="status"
        className="pt-10 pr-0 pb-10 pl-0 text-center text-muted text-[13px]"
      >
        Loading tasks…
      </p>
    ) : (
      <IssueList
        issues={filtered}
        onOpen={openIssue}
        teams={teams}
        onStatusChange={(id, stateId) => updateIssue(id, { stateId })}
        header={view === "My issues" ? "Assigned to me" : "Recent issues"}
        emptyTitle={
          view === "My issues"
            ? "Nothing assigned to you"
            : "You’re all caught up"
        }
        emptyText={
          view === "My issues"
            ? "Tasks assigned to you will appear here."
            : "New tasks will appear here as your team adds work."
        }
        emptyAction={
          view === "Inbox" ? (
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:opacity-[.65]"
              data-control-primary=""
              onClick={() => openNewIssue()}
            >
              <Plus size={15} />
              Create a task
            </button>
          ) : undefined
        }
      />
    );
  const pageContent =
    view === "Issue detail" ? (
      <IssueDetailPage
        workspaceName={workspace?.name}
        viewer={viewer}
        issue={activeIssue}
        issueList={issues}
        onOpenIssue={openIssue}
        loading={issueLoading}
        error={issueError}
        onArchive={async () => {
          if (!activeIssue) return;
          const target = activeIssue;
          const settle = hideIssueLocally(target.id);
          setActiveIssue(null);
          navigate("Issues", "/workspace/issues");
          try {
            const d = await gql(
              `mutation($id:String!){issueArchive(id:$id){success}}`,
              { id: target.identifier },
            );
            if (!d.issueArchive?.success)
              throw Error("Issue could not be archived.");
            settle(true);
          } catch (e) {
            settle(false);
            setError(
              `Could not archive ${target.identifier}: ${(e as Error).message}`,
            );
            throw e;
          }
        }}
        onDelete={async () => {
          if (!activeIssue) return;
          const target = activeIssue;
          const settle = hideIssueLocally(target.id);
          setActiveIssue(null);
          navigate("Issues", "/workspace/issues");
          try {
            const d = await gql(
              `mutation($id:String!){issueDelete(id:$id){success}}`,
              { id: target.identifier },
            );
            if (!d.issueDelete?.success)
              throw Error("Issue could not be deleted.");
            settle(true);
          } catch (e) {
            settle(false);
            setError(
              `Could not delete ${target.identifier}: ${(e as Error).message}`,
            );
            throw e;
          }
        }}
        onDeleteComment={async (cid) => {
          const d = await gql(
            `mutation($id:String!){commentDelete(id:$id){success}}`,
            { id: cid },
          );
          if (!d.commentDelete?.success)
            throw Error("Comment could not be deleted.");
          setActiveIssue((a) =>
            a
              ? {
                  ...a,
                  comments: {
                    nodes: (a.comments?.nodes || []).filter(
                      (c) => c.id !== cid,
                    ),
                  },
                }
              : a,
          );
        }}
        teams={teams}
        projects={projects}
        labels={labels}
        cycles={cycles}
        onUpdate={(input) => updateIssue(activeIssue?.id || "", input)}
        onBack={() => {
          const ret = issueReturn;
          setActiveIssue(null);
          setIssueReturn(null);
          setIssueReturnProject(null);
          if (ret) navigate(ret.view, ret.path);
          else navigate("My issues");
        }}
        backLabel={issueReturn?.label || "My issues"}
        onRetry={() => setIssueRetry((r) => r + 1)}
        onComment={async (body) => {
          const d = await gql(
            `mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment{id body createdAt user{name avatarSeed}}}}`,
            { input: { issueId: activeIssue?.id, body } },
          );
          if (!d.commentCreate.success) throw Error("Comment was rejected");
          setActiveIssue((old) =>
            old
              ? {
                  ...old,
                  comments: {
                    nodes: [
                      ...(old.comments?.nodes || []),
                      d.commentCreate.comment,
                    ],
                  },
                }
              : old,
          );
        }}
      />
    ) : view === "Cycle detail" ? (
      activeCycle && !cycleDetailError ? (
        <CycleDetail
          cycle={activeCycle}
          team={teams.find((t) => t.id === activeCycle.team?.id)}
          teams={teams}
          viewer={viewer}
          orgId={workspace.id}
          workspaceName={workspace.name}
          projects={projects}
          labels={labels}
          cycles={cycles}
          onBack={() => navigate("Cycles")}
          onSelect={openIssue}
          revision={revision}
          onNavigate={navigate}
        />
      ) : (
        <section
          className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
          data-team-scoped-page=""
          role={cycleDetailError ? "alert" : "status"}
        >
          {cycleDetailError || "Loading cycle…"}
        </section>
      )
    ) : view === "Team cycle" ? (
      <TeamCyclePage
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        teams={teams}
        viewer={viewer}
        orgId={workspace.id}
        workspaceName={workspace.name}
        projects={projects}
        labels={labels}
        cycles={cycles}
        upcoming={location.pathname.endsWith("/upcoming")}
        onBack={() =>
          navigate(
            "Team cycles",
            `/workspace/team/${location.pathname.split("/")[3]}/cycles`,
          )
        }
        onIssue={openIssue}
        revision={revision}
        onNavigate={navigate}
      />
    ) : view === "Team Home" ? (
      teams.some(
        (t) =>
          t.key.toLowerCase() ===
          location.pathname.split("/")[3]?.toLowerCase(),
      ) ? (
        <TeamHome
          team={teams.find(
            (t) =>
              t.key.toLowerCase() ===
              location.pathname.split("/")[3]?.toLowerCase(),
          )}
          issues={issues}
          onNavigate={navigate}
          onSelectIssue={openIssue}
        />
      ) : (
        <section
          className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
          data-team-scoped-page=""
          role="alert"
        >
          Team not found in this workspace.
        </section>
      )
    ) : view === "Team issues" ? (
      <TeamIssuesPage
        teams={teams}
        projects={projects}
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        onIssue={openIssue}
        layout={issueLayout}
        onLayoutChange={changeIssueLayout}
        onMoved={moveIssueState}
        onUpdate={updateIssue}
        onNew={(stateId?: string) => openNewIssue(stateId)}
      />
    ) : view === "Team cycle settings" ? (
      <TeamCycleSettings
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        admin={viewer.admin}
        onBack={() =>
          navigate(
            "Team cycles",
            `/workspace/team/${location.pathname.split("/")[3]}/cycles`,
          )
        }
      />
    ) : view === "Team cycles" ? (
      <TeamCyclesPage
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        teams={teams}
        viewer={viewer}
        workspaceName={workspace.name}
        revision={revision}
        onCycle={(c) => {
          setActiveCycle(c);
          navigate("Cycle detail", `/workspace/cycle/${c.id}`);
        }}
        onSettings={() =>
          navigate(
            "Team cycle settings",
            `/workspace/team/${location.pathname.split("/")[3]}/cycles/settings`,
          )
        }
        onNavigate={navigate}
      />
    ) : view === "Team initiatives" ? (
      <InitiativesList
        key={`team-initiatives-${location.pathname.split("/")[3] || ""}`}
        viewer={viewer}
        path={window.location.pathname}
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        onNew={() =>
          setInitiativeCreate({
            leadTeamId: teams.find(
              (t) =>
                t.key.toLowerCase() ===
                location.pathname.split("/")[3]?.toLowerCase(),
            )?.id,
          })
        }
        onOpenInitiative={openInitiative}
      />
    ) : view === "Team projects" ? (
      <TeamProjectsPage
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        revision={revision}
        onCreate={() => setProjectOpen(true)}
        onProject={(p) => {
          setActiveProject(p);
          navigate("Project detail", `/workspace/project/${p.id}`);
        }}
      />
    ) : view === "Team views" ? (
      <TeamViewsPage
        team={teams.find(
          (t) =>
            t.key.toLowerCase() ===
            location.pathname.split("/")[3]?.toLowerCase(),
        )}
        onIssue={openIssue}
        onNavigate={navigate}
        revision={revision}
      />
    ) : view === "Create workspace" ? (
      <CreateWorkspacePage
        onCreated={async (w) => {
          const switched = await gql(
            `mutation($id:String!){workspaceSwitch(organizationId:$id){success redirectUrl organization{id name urlKey}}}`,
            { id: w.id },
          );
          if (!switched.workspaceSwitch?.success)
            throw Error("Workspace created, but could not switch into it");
          if (switched.workspaceSwitch.redirectUrl) {
            window.location.assign(switched.workspaceSwitch.redirectUrl);
            return;
          }
          history.pushState({}, "", `/workspace/inbox`);
          setWorkspace(switched.workspaceSwitch.organization || w);
          setView("Inbox");
          const d = await gql(query);
          setViewer(d.viewer);
          setTeams(d.teams.nodes);
          setIssues(d.issues.nodes);
          const wd = await gql(
            `query MyWorkspaces{myWorkspaces{id organizationId name urlKey role}}`,
          );
          setWorkspaces(wd.myWorkspaces);
          setSetupResume({ workspaceId: w.id, workspaceUrlKey: w.urlKey });
        }}
      />
    ) : view === "Settings" ? (
      settingsSection === "members" && viewer.admin ? (
        <MembersPage
          teams={teams}
          onTeamsChanged={setTeams}
          revision={revision}
          viewer={viewer}
          onOpenMember={openMember}
        />
      ) : (
        <WorkspaceSettings
          workspace={workspace}
          viewer={viewer}
          onSaved={setWorkspace}
          onProfileSaved={(name) =>
            setViewer((current) => ({ ...current, name }))
          }
          onTeamsChanged={setTeams}
          teams={teams}
          section={settingsSection}
          theme={theme}
          onTheme={(value) => void savePreference("theme", value)}
          onFontScale={(value) => void savePreference("fontSize", value)}
          preferenceMessage={preferenceMessage}
          fontScale={fontScale}
          onOpenCycleSettings={(team) =>
            navigate(
              "Team cycle settings",
              `/workspace/team/${team.key.toLowerCase()}/cycles/settings`,
            )
          }
          onBack={() => {
            const p = settingsReturn.current;
            history.pushState({}, "", p);
            setView(routeView(p));
          }}
        />
      )
    ) : view === "Leaderboard" ? (
      <LeaderboardPage
        key={workspace.id}
        viewerId={viewer.id}
        onOpenMember={openMember}
        onOpenTask={openTaskByRef}
      />
    ) : view === "Member profile" ? (
      <MemberProfilePage
        key={`${workspace.id}:${window.location.pathname}`}
        userId={
          window.location.pathname.includes("/member/")
            ? decodeURIComponent(
                window.location.pathname.split("/member/")[1]?.split("/")[0] ||
                  viewer.id ||
                  "",
              )
            : viewer.id || ""
        }
        viewerId={viewer.id}
        onBack={() => navigate("Members", "/workspace/members")}
        onOpenLeaderboard={() =>
          navigate("Leaderboard", "/workspace/leaderboard")
        }
        onOpenTask={openTaskByRef}
        onOpenSettingsProfile={() =>
          navigate("Settings", "/workspace/settings/profile")
        }
      />
    ) : view === "Members" ? (
      <MembersPage
        teams={teams}
        onTeamsChanged={setTeams}
        revision={revision}
        viewer={viewer}
        onOpenMember={openMember}
      />
    ) : view === "Projects" ? (
      <ProjectsPage
        projects={projects}
        hasMore={projectHasMore}
        loading={projectLoading}
        error={projectError}
        onLoadMore={loadMoreProjects}
        onOpen={(p) => {
          navigate("Project detail", `/workspace/project/${p.id}`);
          setActiveProject(p);
        }}
        onCreate={() => setProjectOpen(true)}
      />
    ) : view === "Project view" && activeProject && !projectDetailError ? (
      <ProjectViewsPage
        key={activeProject.id}
        project={activeProject}
        onBack={() =>
          navigate(
            "Project detail",
            `/workspace/project/${activeProject.id}/issues`,
          )
        }
        onOpenIssue={(issue) => openIssue(issue, activeProject.id)}
        revision={revision}
      />
    ) : view === "Project view" ? (
      <section
        className="pt-20 pr-6 pb-20 pl-6 text-center text-muted"
        data-project-reference-state=""
        role={projectDetailError ? "alert" : "status"}
      >
        {projectDetailError || "Loading project…"}{" "}
        <button
          className="mt-2.5 mr-2.5 mb-2.5 ml-2.5 pt-1.75 pr-1.75 pb-1.75 pl-1.75 border border-line rounded-[5px] bg-panel focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          onClick={() => navigate("Projects")}
        >
          Back to projects
        </button>
      </section>
    ) : view === "Project detail" && activeProject && !projectDetailError ? (
      <ProjectDetails
        key={activeProject.id}
        project={activeProject}
        onNewView={() =>
          navigate(
            "Project view",
            `/workspace/project/${activeProject.id}/view/new`,
          )
        }
        issues={issues}
        teams={teams}
        projects={projects}
        layout={issueLayout}
        onLayoutChange={changeIssueLayout}
        onMoved={moveIssueState}
        onUpdate={updateIssue}
        onNew={(stateId?: string) => openNewIssue(stateId)}
        onOpenIssue={(issue) => openIssue(issue, activeProject.id)}
        onBack={() => navigate("Projects")}
        onSaved={(updated) => {
          setActiveProject(updated);
          setProjects((old) =>
            old.map((p) => (p.id === updated.id ? updated : p)),
          );
        }}
        revision={revision}
      />
    ) : view === "Project detail" ? (
      <section
        className="pt-20 pr-6 pb-20 pl-6 text-center text-muted"
        data-project-reference-state=""
        role={projectDetailError ? "alert" : "status"}
      >
        {projectDetailError || "Loading project…"}{" "}
        <button
          className="mt-2.5 mr-2.5 mb-2.5 ml-2.5 pt-1.75 pr-1.75 pb-1.75 pl-1.75 border border-line rounded-[5px] bg-panel focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          onClick={() => navigate("Projects")}
        >
          Back to projects
        </button>
      </section>
    ) : view === "Inbox" ? (
      <InboxPage
        key={workspace.id}
        scope={`${workspace.id}:${viewer?.id || ""}`}
        path={window.location.pathname}
        onOpenIssue={openIssue}
        onOpenSubject={(type, id) =>
          type === "project"
            ? navigate("Project detail", `/workspace/project/${id}`)
            : type === "cycle"
              ? navigate("Cycle detail", `/workspace/cycle/${id}`)
              : navigate(
                  "Initiative detail",
                  `/workspace/initiative/${encodeURIComponent(id)}`,
                )
        }
        onOpenSettings={() =>
          navigate("Settings", "/workspace/settings/notifications")
        }
        renderDetail={({ issue, onClose }) => (
          <InboxIssueDetail
            issue={issue}
            onClose={onClose}
            onOpenIssue={openIssue}
            workspaceName={workspace?.name}
            viewer={viewer}
            issueList={issues}
            teams={teams}
            projects={projects}
            labels={labels}
            cycles={cycles}
            updateIssue={updateIssue}
            onError={setError}
          />
        )}
      />
    ) : view === "Pulse" ? (
      <PulsePage
        key={workspace.id}
        path={window.location.pathname}
        onOpenProject={(p) => {
          navigate("Project detail", `/workspace/project/${p.id}`);
          setActiveProject(p);
        }}
        revision={revision}
      />
    ) : view === "Initiative detail" ? (
      <InitiativeRoute
        key={`${workspace.id}:${window.location.pathname.split("/")[3] || ""}`}
        viewer={viewer}
        projects={projects}
        onBack={() => navigate("Initiatives", "/workspace/initiatives")}
        onArchived={() => navigate("Initiatives", "/workspace/initiatives")}
        onOpenProject={(id) => {
          navigate("Project detail", `/workspace/project/${id}`);
          setActiveProject(projects.find((p) => p.id === id) || null);
        }}
      />
    ) : view === "Initiatives" ? (
      <InitiativesList
        key={workspace.id}
        viewer={viewer}
        path={window.location.pathname}
        onNew={() => setInitiativeCreate({})}
        onOpenInitiative={openInitiative}
      />
    ) : view === "Favorite views" ? (
      <ViewsPage
        key={`favorites-${workspace.id}`}
        orgId={workspace.id}
        issues={issues}
        projects={projects}
        path={window.location.pathname}
        onNavigate={navigate}
        onSelect={openIssue}
        favoriteOnly
        revision={revision}
      />
    ) : view === "Agent" ? (
      <AgentChatPage workspaceName={workspace.name} />
    ) : view === "Views" ? (
      <ViewsPage
        key={workspace.id}
        orgId={workspace.id}
        issues={issues}
        projects={projects}
        path={window.location.pathname}
        onNavigate={navigate}
        onSelect={openIssue}
        revision={revision}
      />
    ) : view === "Reviews" ? (
      <ReviewsInbox
        links={reviewsLinks}
        tasks={reviewsTasks}
        tasksError={reviewsTasksError}
        error={reviewsError}
        gql={gql}
        onOpenNav={() => setMobile(true)}
        onRefresh={() => setReviewsReload((n) => n + 1)}
      />
    ) : view === "Agents" ? (
      <AgentsSurface
        workspaceName={workspace.name}
        viewerName={viewer.name || viewer.email || "You"}
        canEnable={Boolean(viewer.admin)}
      />
    ) : view === "Agent detail" ? (
      <AgentsSurface
        workspaceName={workspace.name}
        viewerName={viewer.name || viewer.email || "You"}
        canEnable={Boolean(viewer.admin)}
      />
    ) : view === "Board" ? (
      <BoardPage
        teams={teams}
        projects={projects}
        onIssue={openIssue}
        onMoved={moveIssueState}
        onUpdate={updateIssue}
        onNew={(stateId?: string) => openNewIssue(stateId)}
      />
    ) : view === "Cycles" ? (
      <CyclesPage
        teams={teams}
        viewer={viewer}
        workspaceName={workspace.name}
        onCycle={(c) => {
          setActiveCycle(c);
          navigate("Cycle detail", `/workspace/cycle/${c.id}`);
        }}
        onSettings={(t) =>
          navigate(
            "Team cycle settings",
            `/workspace/team/${t.key.toLowerCase()}/cycles/settings`,
          )
        }
        revision={revision}
        onNavigate={navigate}
      />
    ) : view === "Not found" ? (
      <section
        className="pt-16 pr-12 pb-16 pl-12 max-w-160"
        data-not-found-page=""
        role="alert"
      >
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-[24px] font-semibold leading-normal tracking-normal ">
          Page not found
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 text-muted">
          This page does not exist.
        </p>
      </section>
    ) : view === "My issues" ? (
      <>
        <div
          className="m-0 flex h-11 min-h-11 flex-none items-center border-b border-line bg-bg px-4 py-0 text-foreground max-md:px-3 [@media_(max-width:560px)]:gap-3.75 shell-phone:gap-3.5"
          data-page-head=""
          data-my-issues-head=""
        >
          <div>
            <h1 className=" text-sm tracking-[-.045em] font-medium leading-normal text-foreground ">
              {displayLabel("My issues")}
            </h1>
            <p className="mt-1.5 mr-0 mb-0 ml-0 text-muted max-sm:text-xs hidden [@media_(max-width:560px)]:text-[12px]">
              {viewer.name} · {workspace.name}
            </p>
          </div>
          <div
            className="flex items-center gap-2 max-md:gap-1.5 hidden shell-phone:gap-1.5"
            data-page-actions=""
          >
            <button
              className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 max-md:pt-2 max-md:pr-2 max-md:pb-2 max-md:pl-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:pt-2 shell-phone:pr-2 shell-phone:pb-2 shell-phone:pl-2"
              data-control-outline=""
              onClick={() => setCommandOpen(true)}
            >
              <Search size={15} />
              Search
            </button>
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-lg bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 max-sm:pt-2 max-sm:pr-2 max-sm:pb-2 max-sm:pl-2 max-md:w-auto max-md:text-xs max-md:pt-2 max-md:pr-2.5 max-md:pb-2 max-md:pl-2.5 max-md:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [@media_(max-width:560px)]:text-[0] [@media_(max-width:560px)]:pt-2.25 [@media_(max-width:560px)]:pr-2.25 [@media_(max-width:560px)]:pb-2.25 [@media_(max-width:560px)]:pl-2.25 disabled:opacity-[.65] shell-phone:w-auto shell-phone:text-[12px] shell-phone:pt-2 shell-phone:pr-2.5 shell-phone:pb-2 shell-phone:pl-2.5"
              data-control-primary=""
              onClick={() => openNewIssue()}
            >
              <Plus size={16} />
              New task
            </button>
          </div>
        </div>
        <MyIssuesContent
          tabs={
            <TaskViewTabs
              label="My task views"
              views={["Assigned", "Created", "Subscribed", "Activity"] as const}
              active={myIssuesTab}
              onChange={setMyTab}
            />
          }
          onOpenIssue={openIssue}
          issues={
            myIssuesTab === "Created"
              ? issues.filter((i) => i.creator?.id === viewer.id)
              : myIssuesTab === "Subscribed"
                ? issues.filter((i) =>
                    i.subscribers?.nodes.some((u) => u.id === viewer.id),
                  )
                : myIssuesTab === "Activity"
                  ? issues.filter(
                      (i) =>
                        i.creator?.id === viewer.id ||
                        i.assignee?.id === viewer.id,
                    )
                  : filtered
          }
          tab={myIssuesTab}
          activityEvents={activityEvents}
          activityHasMore={activityHasMore}
          activityLoading={activityLoading}
          activityError={activityError}
          onLoadActivity={() => loadActivity(activityCursor)}
          key={workspace.id}
          onNew={(stateId?: string) => openNewIssue(stateId)}
          layout={issueLayout}
          onLayoutChange={changeIssueLayout}
          states={uniqueStates}
          onMoved={moveIssueState}
          onUpdate={updateIssue}
          teams={teams}
          projects={projects}
          older={olderMine[olderField]}
          onLoadOlder={onLoadOlder}
        />
      </>
    ) : (
      <>
        <div
          className="flex items-center justify-between mb-7 max-sm:items-start max-sm:gap-3.5 max-md:items-start max-md:gap-3.5 [@media_(max-width:560px)]:gap-3.75 shell-phone:gap-3.5"
          data-page-head=""
        >
          <div>
            <h1 className="mt-2.25 mr-0 mb-1 ml-0 text-[30px] tracking-[-.045em] max-sm:text-2xl max-md:text-3xl font-semibold leading-normal [@media_(max-width:560px)]:text-[24px] shell-phone:text-[25px] [@media_(max-width:420px)]:text-[23px]">
              {view === "Inbox" ? "Inbox" : displayLabel(view)}
            </h1>
            <p className="mt-1.5 mr-0 mb-0 ml-0 text-muted max-sm:text-xs [@media_(max-width:560px)]:text-[12px]">
              {viewer.name} · {workspace.name}
            </p>
          </div>
          <div
            className="flex items-center gap-2 max-md:gap-1.5 shell-phone:gap-1.5"
            data-page-actions=""
          >
            <button
              className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 max-md:pt-2 max-md:pr-2 max-md:pb-2 max-md:pl-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:pt-2 shell-phone:pr-2 shell-phone:pb-2 shell-phone:pl-2"
              data-control-outline=""
              onClick={() => setCommandOpen(true)}
            >
              <Search size={15} />
              Search
            </button>
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-lg bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 max-sm:pt-2 max-sm:pr-2 max-sm:pb-2 max-sm:pl-2 max-md:w-auto max-md:text-xs max-md:pt-2 max-md:pr-2.5 max-md:pb-2 max-md:pl-2.5 max-md:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [@media_(max-width:560px)]:text-[0] [@media_(max-width:560px)]:pt-2.25 [@media_(max-width:560px)]:pr-2.25 [@media_(max-width:560px)]:pb-2.25 [@media_(max-width:560px)]:pl-2.25 disabled:opacity-[.65] shell-phone:w-auto shell-phone:text-[12px] shell-phone:pt-2 shell-phone:pr-2.5 shell-phone:pb-2 shell-phone:pl-2.5"
              data-control-primary=""
              onClick={() => openNewIssue()}
            >
              <Plus size={16} />
              New task
            </button>
          </div>
        </div>
        <div
          className="grid grid-cols-4 gap-3 mt-0 mr-0 mb-6 ml-0 shell-phone:gap-2 shell-phone:mb-4 shell-phone:grid-cols-2"
          data-inbox-summary=""
        >
          <div className="grid gap-1.5 min-h-27 pt-4 pr-4 pb-4 pl-4 bg-panel border border-line shadow-(--ui-shadow-card) rounded-(--ui-radius-card) shell-phone:min-h-21.5 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3">
            <span className="text-muted font-medium">Open tasks</span>
            <b className="self-end text-foreground text-[28px] leading-none font-[620] tracking-[-.045em] shell-phone:text-[24px]">
              {
                issues.filter(
                  (i) =>
                    i.state?.type !== "completed" &&
                    i.state?.type !== "canceled",
                ).length
              }
            </b>
            <small className="text-muted text-[11px]">Across your teams</small>
          </div>
          <div className="grid gap-1.5 min-h-27 pt-4 pr-4 pb-4 pl-4 bg-panel border border-line shadow-(--ui-shadow-card) rounded-(--ui-radius-card) shell-phone:min-h-21.5 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3">
            <span className="text-muted font-medium">In progress</span>
            <b className="self-end text-foreground text-[28px] leading-none font-[620] tracking-[-.045em] shell-phone:text-[24px]">
              {issues.filter((i) => i.state?.type === "started").length}
            </b>
            <small className="text-muted text-[11px]">
              Currently being worked
            </small>
          </div>
          <div className="grid gap-1.5 min-h-27 pt-4 pr-4 pb-4 pl-4 bg-panel border border-line shadow-(--ui-shadow-card) rounded-(--ui-radius-card) shell-phone:min-h-21.5 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3">
            <span className="text-muted font-medium">Completed</span>
            <b className="self-end text-foreground text-[28px] leading-none font-[620] tracking-[-.045em] shell-phone:text-[24px]">
              {issues.filter((i) => i.state?.type === "completed").length}
            </b>
            <small className="text-muted text-[11px]">In loaded tasks</small>
          </div>
          <div className="grid gap-1.5 min-h-27 pt-4 pr-4 pb-4 pl-4 bg-panel border border-line shadow-(--ui-shadow-card) rounded-(--ui-radius-card) shell-phone:min-h-21.5 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3">
            <span className="text-muted font-medium">Active projects</span>
            <b className="self-end text-foreground text-[28px] leading-none font-[620] tracking-[-.045em] shell-phone:text-[24px]">
              {
                projects.filter(
                  (p) =>
                    p.state === "started" ||
                    p.state === "in progress" ||
                    p.state === "In progress",
                ).length
              }
            </b>
            <small className="text-muted text-[11px]">
              {projects.length} total
            </small>
          </div>
        </div>
        {issueListContent}
      </>
    );
  if (token && setupResume)
    return (
      <SetupWizard
        adapter={setupAdapter}
        mode="existing"
        initial={{ ...setupResume, teamId: teams[0]?.id }}
        onFinish={() => setupAdapter.openWorkspace()}
      />
    );
  return (
    <div
      className="app min-h-dvh has-data-workspace-page:h-dvh has-data-workspace-page:min-w-0 has-data-workspace-page:overflow-x-clip shell-phone:has-data-workspace-page:flex shell-phone:has-data-workspace-page:flex-col shell-phone:has-data-workspace-page:overflow-hidden shell-desktop:grid shell-desktop:grid-cols-[244px_minmax(0,1fr)] shell-desktop:grid-rows-[48px_minmax(calc(100dvh-48px),auto)]"
      data-sidebar-collapsed={
        sidebarCollapsed && view !== "Settings" ? "" : undefined
      }
    >
      {view !== "Reviews" && (
        <header
          data-topbar
          className="hidden h-15 flex-none items-center gap-2 border-b border-line bg-panel px-3 text-foreground shell-phone:relative shell-phone:z-40 shell-phone:flex"
        >
          <button
            type="button"
            className="inline-flex min-h-11 min-w-11 flex-none items-center justify-center rounded-lg bg-transparent text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
            aria-label={mobile ? "Close navigation" : "Open navigation"}
            aria-expanded={mobile}
            aria-controls="workspace-sidebar"
            onClick={() => setMobile((open) => !open)}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
            {workspace.name || "Hoja"}
          </span>
          <button
            type="button"
            className="inline-flex min-h-11 min-w-11 flex-none items-center justify-center rounded-lg bg-transparent text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
            aria-label="Search workspace"
            onClick={() => setCommandOpen(true)}
          >
            <Search size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="inline-flex min-h-11 min-w-11 flex-none items-center justify-center rounded-lg bg-transparent text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
            aria-label="Create new task"
            onClick={() => openNewIssue()}
          >
            <Plus size={18} aria-hidden="true" />
          </button>
        </header>
      )}
      <div
        className="flex shell-phone:flex shell-phone:flex-1 shell-phone:min-h-0 shell-phone:overflow-hidden shell-desktop:contents shell-phone:min-h-[calc(100dvh-60px)] min-h-[calc(100dvh-68px)]"
        data-app-body=""
      >
        {view === "Settings" ? (
          <SettingsSidebar
            workspace={workspace}
            viewer={viewer}
            section={settingsSection}
            onNavigate={(id) =>
              navigate("Settings", `/workspace/settings/${id}`)
            }
            onBack={() => {
              const p = settingsReturn.current;
              history.pushState({}, "", p);
              setView(routeView(p));
            }}
            onLogout={logout}
            mobile={mobile}
            onCloseMobile={() => setMobile(false)}
          />
        ) : (
          <>
            <button
              type="button"
              className={
                ` hidden ${mobile ? "visible" : ""}` +
                "" +
                "" +
                " focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2" +
                " shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11" +
                " shell-phone:fixed shell-phone:inset-x-0 shell-phone:top-15 shell-phone:bottom-0 invisible opacity-0 data-visible:visible data-visible:opacity-100 transition-opacity duration-150 motion-reduce:transition-none" +
                " [@media_(max-width:700px)]:z-34 [@media_(max-width:700px)]:bg-[#14141466] [@media_(max-width:700px)]:opacity-[1]"
              }
              data-workspace-drawer-scrim=""
              aria-label="Close navigation"
              tabIndex={mobile ? 0 : -1}
              onClick={closeMobileNav}
              data-visible={mobile || undefined}
            />
            <aside
              ref={mobileSidebarRef}
              id="workspace-sidebar"
              data-workspace-sidebar
              data-open={mobile || undefined}
              className="flex min-h-0 min-w-0 flex-none flex-col overflow-x-hidden overflow-y-auto overscroll-contain bg-sidebar px-3 pt-3 pb-0 text-foreground shell-desktop:sticky shell-desktop:top-0 shell-desktop:col-start-1 shell-desktop:row-span-2 shell-desktop:h-dvh shell-desktop:max-h-dvh shell-desktop:w-61 shell-desktop:rail-collapsed:w-11 shell-desktop:rail-collapsed:px-2 shell-desktop:rail-collapsed:py-3 shell-desktop:rail-collapsed:overflow-hidden shell-phone:fixed shell-phone:z-35 shell-phone:inset-y-0 shell-phone:top-15 shell-phone:left-0 shell-phone:w-[min(290px,85vw)] shell-phone:max-w-full shell-phone:-translate-x-full shell-phone:data-open:transform-[translateX(0)]! shell-phone:data-open:translate-x-0 shell-phone:transition-transform shell-phone:duration-150 shell-phone:shadow-xl route-review:relative route-review:top-auto route-review:self-stretch route-review:col-auto route-review:row-auto review-drawer:route-review:fixed! review-drawer:route-review:z-35! review-drawer:route-review:inset-y-0! review-drawer:route-review:left-0! review-drawer:route-review:w-[min(290px,85vw)]! review-drawer:route-review:-translate-x-full! review-drawer:route-review:data-open:translate-x-0! review-drawer:route-review:shadow-xl!"
              aria-label="Workspace sidebar"
              role={mobile ? "dialog" : undefined}
              aria-modal={mobile || undefined}
            >
              <div
                className="relative z-10 flex flex-none items-center gap-1 min-h-9 mb-3 overscroll-none shell-desktop:rail-collapsed:flex-col shell-desktop:rail-collapsed:justify-start shell-desktop:rail-collapsed:gap-1.5 shell-desktop:rail-collapsed:mb-2 min-w-0"
                data-sidebar-top=""
              >
                <div
                  className="relative static min-w-0 flex-1 shell-desktop:rail-collapsed:flex-none shell-desktop:rail-collapsed:w-full shell-phone:min-w-0"
                  data-workspace-picker=""
                >
                  <UIPopover
                    open={orgMenu}
                    onOpenChange={(open) => {
                      setOrgMenu(open);
                      if (open) setMoreOpen(false);
                    }}
                  >
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="border flex items-center gap-1.5 pt-0 pr-1 pb-0 pl-1 bg-transparent text-left border-transparent rounded-lg h-7 border-line hover:bg-subtle hover:border-line w-full min-w-0 max-w-full border-0 text-foreground [&>span:nth-child(2)]:grid [&>span:nth-child(2)]:gap-0.5 [&>span:nth-child(2)]:font-semibold shell-desktop:rail-collapsed:justify-center shell-desktop:rail-collapsed:h-7 shell-desktop:rail-collapsed:p-0 hover:bg-[#e5e5e5] shell-phone:pt-1 shell-phone:pr-1 shell-phone:pb-1 shell-phone:pl-1 shell-phone:max-w-none shell-phone:w-9.5"
                        data-workspace-trigger=""
                        aria-label={
                          onboarding.singleOrg
                            ? "Workspace menu"
                            : "Switch workspace"
                        }
                        aria-haspopup="menu"
                        aria-controls="workspace-switcher-menu"
                        disabled={switching}
                      >
                        <span
                          className="grid place-items-center w-5 h-5 flex-none rounded-md bg-[#6d6d73] text-[white] font-[750] text-[10px] shell-desktop:rail-collapsed:size-6 shell-phone:w-8 shell-phone:h-8"
                          data-workspace-trigger-mark=""
                        >
                          {(workspace.name || "W").slice(0, 1).toUpperCase()}
                        </span>
                        <span
                          className="grid gap-px max-w-none min-w-0 flex-1"
                          data-workspace-trigger-copy=""
                        >
                          <b className="text-[13px] font-medium overflow-hidden text-ellipsis whitespace-nowrap text-foreground">
                            {switching
                              ? "Switching…"
                              : workspace.name || "Workspace"}
                          </b>
                          <small className="hidden text-muted text-[10px] font-normal">
                            {workspace.urlKey}
                          </small>
                        </span>
                        <ChevronDown size={15} />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="start"
                      side="bottom"
                      sideOffset={4}
                      portal={false}
                      id="workspace-switcher-menu"
                      role="menu"
                      className="sidebar-popover workspace-menu z-50 min-w-0 max-w-none rounded-[10px] border-line bg-panel p-1.5 text-foreground shadow-(--shadow)"
                      style={
                        workspaceMenuWidth === undefined
                          ? undefined
                          : {
                              width: workspaceMenuWidth,
                              maxWidth: workspaceMenuWidth,
                            }
                      }
                    >
                      <div
                        className="border-b border-line flex items-center gap-2.25 pt-2.25 pr-2.25 pb-2.25 pl-2.25 mb-1.25"
                        data-workspace-menu-head=""
                      >
                        <span
                          className="grid place-items-center w-5 h-5 flex-none rounded-md bg-[#6d6d73] text-[white] font-[750] text-[10px] last:grid last:gap-0.5 last:min-w-0 shell-desktop:rail-collapsed:size-6 shell-phone:w-8 shell-phone:h-8"
                          data-workspace-trigger-mark=""
                        >
                          {(workspace.name || "W").slice(0, 1).toUpperCase()}
                        </span>
                        <span className="last:grid last:gap-0.5 last:min-w-0">
                          <b className="text-[12px] overflow-hidden text-ellipsis whitespace-nowrap">
                            {workspace.name}
                          </b>
                          <small className="text-muted text-[10px]">
                            {workspace.urlKey}
                          </small>
                        </span>
                      </div>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setOrgMenu(false);
                          setMobile(false);
                          navigate("Settings");
                        }}
                        className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                      >
                        Workspace settings
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setOrgMenu(false);
                          setMobile(false);
                          navigate("Members");
                        }}
                        className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                      >
                        Manage members
                      </button>
                      {!onboarding.singleOrg && (
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setOrgMenu(false);
                            setMobile(false);
                            navigate("Create workspace");
                          }}
                          className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                        >
                          Create workspace
                        </button>
                      )}
                      {!onboarding.singleOrg && workspaces.length > 1 && (
                        <>
                          <div
                            className="pt-2.25 pr-2.25 pb-1 pl-2.25 text-muted text-[10px] normal-case tracking-[.08em]"
                            data-workspace-menu-label=""
                          >
                            Switch workspace
                          </div>
                          {workspaces.map((w) => (
                            <button
                              type="button"
                              role="menuitem"
                              key={w.organizationId}
                              onClick={() => {
                                setOrgMenu(false);
                                setMobile(false);
                                switchWorkspace(w.organizationId);
                              }}
                              disabled={
                                switching || w.organizationId === workspace.id
                              }
                              className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                            >
                              {w.name}
                              <small>{w.urlKey}</small>
                            </button>
                          ))}
                        </>
                      )}
                      <button
                        type="button"
                        role="menuitem"
                        onClick={logout}
                        className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                      >
                        Log out
                      </button>
                    </PopoverContent>
                  </UIPopover>
                  {switchError && (
                    <small
                      role="alert"
                      className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
                    >
                      {switchError}
                    </small>
                  )}
                </div>
                <button
                  type="button"
                  className="grid w-7 h-7 flex-none place-items-center rounded-[7px] border-0 bg-transparent text-muted hover:bg-subtle hover:text-foreground focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-desktop:rail-collapsed:flex-none shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:size-11"
                  data-sidebar-icon=""
                  data-sidebar-search=""
                  aria-label="Search workspace"
                  title="Search (Ctrl+K)"
                  onClick={() => setCommandOpen(true)}
                >
                  <Search size={15} aria-hidden="true" />
                </button>
                <button
                  className="grid w-7 h-7 flex-none place-items-center rounded-[7px] border-0 bg-[#f9f9fa] pt-1.5 pr-1.5 pb-1.5 pl-1.5 text-[#2e2e30] hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-foreground focus-visible:outline-offset-2 focus-visible:outline-accent shell-desktop:rail-collapsed:flex-none shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:size-11 hover:bg-[#efeff0] focus-visible:bg-[#efeff0]"
                  data-sidebar-icon=""
                  data-sidebar-create=""
                  aria-label="Create new task"
                  onClick={() => openNewIssue()}
                >
                  <svg
                    className="w-4 h-4 flex-none"
                    width="16"
                    height="16"
                    viewBox="0 0 16 16"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path
                      fillRule="evenodd"
                      clipRule="evenodd"
                      d="M7.25 1C7.66414 1 8 1.33589 8 1.75S7.66421 2.5 7.25 2.5H4.75A2.25 2.25 0 0 0 2.5 4.75v6.5a2.25 2.25 0 0 0 2.25 2.25h6.5a2.25 2.25 0 0 0 2.25-2.25v-2.5a.75.75 0 0 1 1.5 0v2.5A3.75 3.75 0 0 1 11.25 15h-6.5A3.75 3.75 0 0 1 1 11.25v-6.5A3.75 3.75 0 0 1 4.75 1h2.5Z"
                    />
                    <path d="M13.4326 1.26953a.919.919 0 0 1 1.2988 1.2988L9.2373 8.0615a3.25 3.25 0 0 1-2.082.9385c-.0877.0052-.1606-.0675-.1553-.1553a3.25 3.25 0 0 1 .9385-2.082L13.4326 1.26953Z" />
                  </svg>
                </button>
                <button
                  type="button"
                  className="grid flex-none w-7 h-7 rounded-lg bg-transparent text-muted hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-desktop:rail-collapsed:flex-none shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:size-11 group focus-visible:bg-[#e1e1e1]"
                  data-sidebar-icon=""
                  data-sidebar-toggle=""
                  aria-label={
                    sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
                  }
                  aria-expanded={!sidebarCollapsed}
                  aria-controls="workspace-sidebar"
                  title={
                    sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
                  }
                  onClick={() => setSidebarCollapsed((v) => !v)}
                >
                  {sidebarCollapsed ? (
                    <PanelLeftOpen size={16} />
                  ) : (
                    <PanelLeftClose size={16} />
                  )}
                </button>
              </div>
              <nav
                className="relative z-0 min-h-0 flex-1 overflow-x-hidden overflow-y-auto"
                aria-label="Workspace navigation"
              >
                {sections.map(([section, items]) => (
                  <div
                    className={
                      ` mb-4.25` + " mt-0 mr-0 mb-4.25 ml-0" + "" + " mb-4.25"
                    }
                    data-nav-group=""
                    key={section}
                  >
                    {section !== "primary" && (
                      <button
                        type="button"
                        className="flex items-center justify-between w-full h-7 mt-0 mr-1 mb-0.5 ml-1 pt-0 pr-1.25 pb-0 pl-1.25 rounded-md bg-transparent text-muted text-[13px] font-normal text-left hover:bg-subtle focus-visible:bg-subtle hover:text-foreground focus-visible:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:h-auto group shell-desktop:rail-collapsed:hidden"
                        data-nav-group-toggle=""
                        aria-expanded={!collapsedSections[section]}
                        onClick={() =>
                          setCollapsedSections((c) => ({
                            ...c,
                            [section]: !c[section],
                          }))
                        }
                      >
                        <span>{section}</span>
                        <ChevronDown
                          size={12}
                          className="transition-transform duration-150 motion-reduce:transition-none group-aria-[expanded=false]:-rotate-90"
                        />
                      </button>
                    )}
                    {!collapsedSections[section] && (
                      <>
                        {section === "Your teams" &&
                          teams.map((team) => (
                            <div
                              data-team-block=""
                              key={team.id}
                              className="mb-1 first-of-type:mt-2"
                            >
                              <div
                                className="max-md:justify-center flex items-center gap-0"
                                data-team-row=""
                              >
                                <button
                                  type="button"
                                  className="group flex min-h-7  w-full min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-md border-0 bg-transparent px-2 py-1 text-left text-[13px] font-medium text-[#202023] whitespace-nowrap text-ellipsis hover:text-foreground aria-[current=page]:text-foreground aria-[current=page]:font-semibold shell-phone:min-h-11 shell-phone:min-w-11 shell-desktop:rail-collapsed:justify-center shell-desktop:rail-collapsed:px-0 hover:bg-transparent hover:text-[#202023]"
                                  data-team-name=""
                                  aria-expanded={!collapsedTeams[team.id]}
                                  onClick={() =>
                                    setCollapsedTeams((c) => ({
                                      ...c,
                                      [team.id]: !c[team.id],
                                    }))
                                  }
                                >
                                  <Users
                                    size={15}
                                    className="size-4 flex-none"
                                  />
                                  <span className=" shell-desktop:rail-collapsed:hidden">
                                    {team.name}
                                  </span>
                                </button>
                              </div>
                              {!collapsedTeams[team.id] && (
                                <div
                                  className="border-l border-line grid gap-px mt-px mr-0 mb-2 ml-3.5 pl-2.5 shell-desktop:rail-collapsed:hidden"
                                  data-team-nav-links=""
                                >
                                  <button
                                    className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                    type="button"
                                    onClick={() => {
                                      navigate(
                                        "Team Home",
                                        `/workspace/team/${team.key.toLowerCase()}/home`,
                                      );
                                      setMobile(false);
                                    }}
                                    aria-current={
                                      view === "Team Home" &&
                                      location.pathname.endsWith("/home")
                                        ? "page"
                                        : undefined
                                    }
                                  >
                                    Home
                                  </button>
                                  <button
                                    className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                    type="button"
                                    onClick={() => {
                                      navigate(
                                        "Team issues",
                                        `/workspace/team/${team.key.toLowerCase()}/active`,
                                      );
                                      setMobile(false);
                                    }}
                                    aria-current={
                                      view === "Team issues"
                                        ? "page"
                                        : undefined
                                    }
                                  >
                                    Tasks
                                  </button>
                                  <button
                                    className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                    type="button"
                                    onClick={() => {
                                      navigate(
                                        "Team cycles",
                                        `/workspace/team/${team.key.toLowerCase()}/cycles`,
                                      );
                                      setMobile(false);
                                    }}
                                    aria-current={
                                      view === "Team cycles"
                                        ? "page"
                                        : undefined
                                    }
                                  >
                                    Cycles
                                  </button>
                                  <div
                                    className="border-l border-line grid gap-px mt-px mr-0 mb-1 ml-3 pl-2.25 shell-desktop:rail-collapsed:hidden"
                                    data-team-cycle-links=""
                                  >
                                    <button
                                      className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-1.75 pr-1.75 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                      type="button"
                                      aria-current={
                                        view === "Team cycle" &&
                                        location.pathname.includes(
                                          `/team/${team.key.toLowerCase()}/cycle/active`,
                                        )
                                          ? "page"
                                          : undefined
                                      }
                                      onClick={() => {
                                        navigate(
                                          "Team cycle",
                                          `/workspace/team/${team.key.toLowerCase()}/cycle/active`,
                                        );
                                        setTeamCycleRoute(
                                          `/workspace/team/${team.key.toLowerCase()}/cycle/active`,
                                        );
                                        setMobile(false);
                                      }}
                                    >
                                      Current
                                    </button>
                                    <button
                                      className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-1.75 pr-1.75 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                      type="button"
                                      aria-current={
                                        view === "Team cycle" &&
                                        location.pathname.includes(
                                          `/team/${team.key.toLowerCase()}/cycle/upcoming`,
                                        )
                                          ? "page"
                                          : undefined
                                      }
                                      onClick={() => {
                                        navigate(
                                          "Team cycle",
                                          `/workspace/team/${team.key.toLowerCase()}/cycle/upcoming`,
                                        );
                                        setTeamCycleRoute(
                                          `/workspace/team/${team.key.toLowerCase()}/cycle/upcoming`,
                                        );
                                        setMobile(false);
                                      }}
                                    >
                                      Upcoming
                                    </button>
                                  </div>
                                  <button
                                    className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                    type="button"
                                    onClick={() => {
                                      navigate(
                                        "Team projects",
                                        `/workspace/team/${team.key.toLowerCase()}/projects/all`,
                                      );
                                      setMobile(false);
                                    }}
                                    aria-current={
                                      view === "Team projects"
                                        ? "page"
                                        : undefined
                                    }
                                  >
                                    Projects
                                  </button>
                                  {team.showInitiatives && (
                                    <button
                                      className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                      type="button"
                                      data-team-initiatives=""
                                      onClick={() => {
                                        navigate(
                                          "Team initiatives",
                                          `/workspace/team/${team.key.toLowerCase()}/initiatives`,
                                        );
                                        setMobile(false);
                                      }}
                                      aria-current={
                                        view === "Team initiatives" &&
                                        location.pathname
                                          .split("/")[3]
                                          ?.toLowerCase() ===
                                          team.key.toLowerCase()
                                          ? "page"
                                          : undefined
                                      }
                                    >
                                      Initiatives
                                    </button>
                                  )}
                                  <button
                                    className="flex min-h-6.75 w-full items-center rounded-[5px] bg-transparent pl-2 pr-2 pt-1.25 pb-1.25 text-left text-[12px] text-muted hover:bg-neutral-200 hover:text-foreground aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 focus-visible:bg-subtle focus-visible:text-foreground"
                                    type="button"
                                    onClick={() => {
                                      navigate(
                                        "Team views",
                                        `/workspace/team/${team.key.toLowerCase()}/views/issues`,
                                      );
                                      setMobile(false);
                                    }}
                                    aria-current={
                                      view === "Team views" ? "page" : undefined
                                    }
                                  >
                                    Views
                                  </button>
                                </div>
                              )}
                            </div>
                          ))}
                        {[
                          ...items,
                          /* Admins always see the entry, so the screens can be found and turned on
                           * from where they live; the page offers the switch while it is off. */
                          ...(section === "primary" &&
                          (agentsOn || viewer.admin)
                            ? ([["Agents", GitBranch]] as [
                                string,
                                typeof GitBranch,
                              ][])
                            : []),
                        ].map(([label, Icon]) => (
                          <button
                            type="button"
                            className="flex w-full  items-center gap-2 overflow-hidden rounded-lg bg-transparent px-2.5 py-1 hover:bg-neutral-200  text-left text-[13px] font-normal text-muted whitespace-nowrap text-ellipsis aria-[current=page]:bg-accent-soft aria-[current=page]:text-foreground aria-[current=page]:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11 shell-desktop:rail-collapsed:justify-center shell-desktop:rail-collapsed:px-0  aria-[current=page]:bg-subtle aria-[current=page]:font-medium"
                            data-nav-item=""
                            key={label}
                            onClick={() => {
                              navigate(label);
                              setMobile(false);
                            }}
                            aria-current={view === label ? "page" : undefined}
                          >
                            <Icon size={15} />
                            <span className="overflow-hidden text-ellipsis shell-desktop:rail-collapsed:hidden">
                              {label === "Favorite views"
                                ? "Saved views"
                                : label}
                            </span>
                            {label === "Inbox" && <InboxBadge />}
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                ))}
                <div
                  className="mb-4.25 mt-0 mr-0 ml-0"
                  data-nav-group=""
                  data-nav-group-more-control=""
                >
                  <UIPopover
                    open={moreOpen}
                    onOpenChange={(open) => {
                      setMoreOpen(open);
                      if (open) setOrgMenu(false);
                    }}
                  >
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="flex items-center gap-2 w-full pt-0 pr-2.25 pb-0 pl-2.25 rounded-lg bg-transparent text-muted text-left min-h-7 text-[13px] hover:bg-subtle hover:text-foreground h-7 font-normal whitespace-nowrap overflow-hidden text-ellipsis [&_em]:ml-auto [&_em]:not-italic [&_em]:text-xs [&_em]:text-accent [&_em]:bg-accent-soft [&_em]:pt-0 [&_em]:pr-1.5 [&_em]:pb-0 [&_em]:pl-1.5 [&_em]:rounded-xl [&_svg]:flex-none hover:bg-[#e5e5e5] hover:text-muted aria-[current=page]:bg-subtle aria-[current=page]:text-foreground aria-[current=page]:font-medium"
                        data-nav-item=""
                        aria-label="Workspace More"
                        aria-haspopup="menu"
                        aria-controls="workspace-more-menu"
                      >
                        <MoreHorizontal size={15} />
                        <span className="overflow-hidden text-ellipsis">
                          More
                        </span>
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="start"
                      side="bottom"
                      sideOffset={6}
                      id="workspace-more-menu"
                      role="menu"
                      data-popover-panel
                      className="z-50 w-64 max-w-full rounded-[10px] border-line bg-panel p-1.5 text-foreground shadow-(--shadow)"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMoreOpen(false);
                          navigate("Members");
                          setMobile(false);
                        }}
                        className="flex w-full min-h-9 items-center justify-between gap-2 rounded-lg bg-transparent p-2 text-left text-sm text-inherit hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
                      >
                        Members
                      </button>
                      <p className="m-0 px-2 py-1.5 text-xs leading-relaxed text-muted">
                        Customers, team administration and sidebar customization
                        are not available yet.
                      </p>
                    </PopoverContent>
                  </UIPopover>
                </div>
              </nav>
            </aside>
          </>
        )}
        <div
          className="min-w-0 flex-1 bg-bg min-h-0 overflow-hidden shell-phone:flex shell-phone:flex-col shell-desktop:col-start-2 shell-desktop:row-start-2 has-data-my-issues-view:row-span-2 has-data-pulse-page:row-span-2 has-data-inbox-page:row-span-2 has-data-initiatives-page:row-span-2 has-data-projects-reference:row-span-2 has-data-project-reference:row-span-2 has-data-workspace-views-page:row-span-2 has-data-mt-page:row-span-2 has-data-settings-page:row-span-2 shell-desktop:data-headerless:row-start-1! shell-desktop:data-headerless:row-span-2"
          data-workspace-content=""
          data-headerless={HEADERLESS_VIEWS.has(view) ? "" : undefined}
        >
          <main
            data-main
            className="main max-w-360 w-full pt-10 pr-[clamp(24px,4vw,64px)] pb-10 pl-[clamp(24px,4vw,64px)] mt-0 mr-auto mb-0 ml-auto max-sm:pt-5 max-sm:pr-3 max-sm:pb-5 max-sm:pl-3 flex-1 min-w-0 max-lg:pt-7 max-lg:pr-6 max-lg:pb-7 max-lg:pl-6 has-data-settings-page:mt-2 has-data-settings-page:mr-2 has-data-settings-page:mb-2 has-data-settings-page:rounded-xl has-data-workspace-page:w-full! has-data-workspace-page:m-0! has-data-workspace-page:p-0! has-data-workspace-page:max-w-none! has-data-workspace-page:min-w-0! has-data-workspace-page:min-h-0! has-data-workspace-page:flex! has-data-workspace-page:flex-col! has-data-workspace-page:overflow-hidden! has-data-workspace-page:bg-sidebar! has-data-workspace-page:rounded-none! overflow-hidden bg-bg text-foreground rounded-none flex flex-col min-h-0 h-full max-h-full shell-phone:h-auto [@media_(max-width:850px)]:pt-7 [@media_(max-width:850px)]:pr-5.5 [@media_(max-width:850px)]:pb-7 [@media_(max-width:850px)]:pl-5.5 [@media_(max-width:560px)]:pt-6 [@media_(max-width:560px)]:pr-3.5 [@media_(max-width:560px)]:pb-6 [@media_(max-width:560px)]:pl-3.5 [@media_(max-width:900px)]:pt-7.5 [@media_(max-width:900px)]:pr-6 [@media_(max-width:900px)]:pb-7.5 [@media_(max-width:900px)]:pl-6 shell-phone:pt-6.25 shell-phone:pr-4 shell-phone:pb-6.25 shell-phone:pl-4 [@media_(max-width:420px)]:pt-5.5 [@media_(max-width:420px)]:pr-3 [@media_(max-width:420px)]:pb-5.5 [@media_(max-width:420px)]:pl-3 has-data-pulse-page:w-[calc(100%-16px)] has-data-pulse-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-pulse-page:w-[calc(100%-8px)] shell-phone:has-data-pulse-page:min-h-[calc(100dvh-60px)] has-data-inbox-page:w-[calc(100%-16px)] has-data-inbox-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-inbox-page:w-[calc(100%-8px)] shell-phone:has-data-inbox-page:min-h-[calc(100dvh-60px)] has-data-initiatives-page:w-[calc(100%-16px)] has-data-initiatives-page:min-h-[calc(100dvh-16px)] shell-desktop:has-data-initiatives-page:w-[calc(100%-8px)] shell-phone:has-data-initiatives-page:min-h-[calc(100dvh-60px)] has-data-projects-reference:w-[calc(100%-8px)] has-data-projects-reference:min-h-[calc(100dvh-16px)] shell-phone:has-data-projects-reference:min-h-[calc(100dvh-60px)] has-data-project-reference:w-[calc(100%-8px)] has-data-project-reference:min-h-[calc(100dvh-16px)] shell-phone:has-data-project-reference:min-h-[calc(100dvh-60px)] has-data-workspace-views-page:w-[calc(100%-8px)] has-data-workspace-views-page:min-h-[calc(100dvh-16px)] shell-phone:has-data-workspace-views-page:min-h-[calc(100dvh-60px)] shell-desktop:has-data-my-issues-view:w-[calc(100%-16px)] shell-desktop:has-data-my-issues-view:min-h-[calc(100dvh-16px)] shell-phone:[&:has([data-my-issues-view])_[data-my-issue-tabs]]:scrollbar-none"
          >
            {error && (
              <div
                className="flex items-center gap-3 mt-0 mr-0 mb-5 ml-0 pt-3.5 pr-4 pb-3.5 pl-4 rounded-[10px] text-danger border border-danger/40 bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
                data-app-error=""
                role="alert"
              >
                <b>Couldn’t load this workspace</b>
                <span className="flex-1 text-foreground min-w-0 wrap-break-word">
                  {error}
                </span>
                <button
                  className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-control-outline=""
                  onClick={() => setError("")}
                >
                  Dismiss
                </button>
              </div>
            )}
            {SHELL_VIEWS.has(view) ? (
              <div
                className="bg-bg shadow-sm overflow-y-auto overscroll-contain m-1 flex min-h-0 flex-1 flex-col rounded-2xl border-[0.5px] border-neutral-300/70 has-data-settings-page:py-6 has-data-settings-page:px-4 md:has-data-settings-page:py-8 has-data-my-issues-view:p-0 has-data-pulse-page:p-0 has-data-inbox-page:p-0 has-data-initiatives-page:p-0 has-data-workspace-views-page:p-0 has-data-projects-reference:p-0 has-data-surface:p-0 has-data-my-issues-view:overflow-hidden has-data-surface:overflow-hidden [font-optical-sizing:auto]"
                data-workspace-page=""
              >
                {pageContent}
              </div>
            ) : (
              pageContent
            )}
          </main>
        </div>
        {projectOpen && (
          <NewProject
            teams={teams}
            onClose={() => setProjectOpen(false)}
            onCreated={() => {
              setProjectOpen(false);
              gql(
                `query Projects{projects(first:100){nodes{id name description state progress startDate targetDate health lead{name} teams{nodes{id name key}}} pageInfo{hasNextPage endCursor}}}`,
              )
                .then((d) => {
                  setProjects(d.projects.nodes);
                  setProjectCursor(d.projects.pageInfo?.endCursor || null);
                  setProjectHasMore(Boolean(d.projects.pageInfo?.hasNextPage));
                })
                .catch((e) => setProjectError((e as Error).message));
            }}
          />
        )}
        {newOpen && (
          <NewIssue
            teams={teams}
            initialStateId={newStateId || undefined}
            initialTeamId={newTeamId || undefined}
            onClose={() => {
              setNewOpen(false);
              setNewStateId("");
              setNewTeamId("");
            }}
            onCreated={() => {
              setNewOpen(false);
              setNewStateId("");
              setNewTeamId("");
              void syncRef.current?.pull();
            }}
          />
        )}
        {initiativeCreate && (
          <CreateInitiativeDialog
            defaults={initiativeCreate}
            initiatives={initiativeOptions}
            projects={projects}
            onClose={() => setInitiativeCreate(null)}
            onCreated={(row, more) => {
              if (more) return;
              setInitiativeCreate(null);
              openInitiative(row.identifier || row.id);
            }}
          />
        )}
        {commandOpen && (
          <CommandPalette
            query={commandQuery}
            setQuery={setCommandQuery}
            issues={issues}
            onClose={() => {
              setCommandOpen(false);
              setCommandQuery("");
            }}
            onIssue={(i) => {
              setCommandOpen(false);
              openIssue(i);
            }}
            onNavigate={(v) => {
              navigate(v);
              setCommandOpen(false);
            }}
            onCreateInitiative={() => {
              setCommandOpen(false);
              setInitiativeCreate({});
            }}
            onOpenInitiative={(id) => {
              setCommandOpen(false);
              openInitiative(id);
            }}
          />
        )}
      </div>
    </div>
  );
}

export function relTime(iso?: string) {
  if (!iso) return "";
  const d = Date.parse(iso);
  if (Number.isNaN(d)) return iso;
  const m = Math.round((Date.now() - d) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.round(h / 24);
  if (days < 30) return `${days}d ago`;
  const mo = Math.round(days / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.round(mo / 12)}y ago`;
}
function IssueDetailPage({
  workspaceName,
  issue,
  issueList,
  onOpenIssue,
  loading,
  error,
  onBack,
  backLabel,
  onRetry,
  onComment,
  teams,
  projects,
  labels,
  cycles,
  onUpdate,
  viewer,
  onArchive,
  onDelete,
  onDeleteComment,
}: {
  workspaceName?: string;
  issueList: Issue[];
  onOpenIssue: (issue: Issue) => void;
  viewer: Viewer;
  onArchive: () => Promise<void>;
  onDelete: () => Promise<void>;
  onDeleteComment: (id: string) => Promise<void>;
  issue: Issue | null;
  loading: boolean;
  error: string;
  onBack: () => void;
  backLabel: string;
  onRetry?: () => void;
  onComment: (body: string) => Promise<void>;
  teams: Team[];
  projects: Project[];
  labels: { id: string; name: string; color?: string }[];
  cycles: Cycle[];
  onUpdate: (input: Record<string, unknown>) => Promise<void>;
}) {
  return (
    <GraphIssueDetail
      key={issue?.id || "none"}
      workspaceName={workspaceName}
      viewer={viewer}
      issue={issue}
      issueList={issueList}
      onOpenIssue={onOpenIssue}
      loading={loading}
      error={error}
      onArchive={onArchive}
      onDelete={onDelete}
      onDeleteComment={onDeleteComment}
      teams={teams}
      projects={projects}
      labels={labels}
      cycles={cycles}
      onUpdate={onUpdate}
      onBack={onBack}
      backLabel={backLabel}
      onRetry={onRetry}
      onComment={onComment}
    />
  );
}
import { InitiativeRoute } from "./initiatives/InitiativeDetail";
import { InitiativesList } from "./initiatives/InitiativesList";
import { InitiativeSettings } from "./initiatives/InitiativeSettings";
import { CreateInitiativeDialog } from "./initiatives/CreateInitiativeDialog";
import { fetchAllInitiatives, type InitiativeRef } from "./initiatives/api";
// Local-first helpers for the projects / initiatives / pulse pages (see projectStore.ts).
import {
  addPendingProject,
  hasPage,
  hideProject,
  isPendingProject,
  optimisticPage,
  patchProject,
  rememberProjects,
  setProjectNotice,
  useProject,
  useProjectIssueIndex,
  useProjectIssues,
  useProjectNotice,
  useProjects,
  usePagedCache,
  type PageOf,
  useProjectEntityVersion,
  useProjectSyncMeta,
} from "./projectStore";
import "./mobile-touch.css";
const PROJECT_FIELDS = `id name description state progress startDate targetDate health lead{name} teams{nodes{id name key}}`;
const PULSE_FIELDS = `id status body createdAt progress health actor{id name avatarSeed} project{id name health progress targetDate lead{name}} comments(first:20){nodes{id body createdAt user{id name avatarSeed}}}`;
const pulseKey = (meta: SyncMeta | null) =>
  meta ? `pulse:${meta.workspace.id}:${meta.viewer.id || ""}` : null;
const upsertFirst = <T extends { id: string }>(rows: T[], row: T) =>
  rows.some((r) => r.id === row.id)
    ? rows.map((r) => (r.id === row.id ? row : r))
    : [row, ...rows];
function useMemberOptions() {
  const meta = useProjectSyncMeta();
  return useMemo(
    () =>
      (meta?.users || [])
        .filter((u): u is [string, string] => !!u[1])
        .map(([id, name]) => ({ id, name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [meta],
  );
}
/* The task pane inside the inbox: the same task view as the full page, loaded by identifier and kept
   live from the sync store, with archive and delete closing the pane instead of navigating. */
function InboxIssueDetail({
  issue: seed,
  onClose,
  onOpenIssue,
  workspaceName,
  viewer,
  issueList,
  teams,
  projects,
  labels,
  cycles,
  updateIssue,
  onError,
}: {
  issue: Issue;
  onClose: () => void;
  onOpenIssue: (issue: Issue) => void;
  workspaceName?: string;
  viewer: Viewer;
  issueList: Issue[];
  teams: Team[];
  projects: Project[];
  labels: IssueLabel[];
  cycles: Cycle[];
  updateIssue: (id: string, input: Record<string, unknown>) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [issue, setIssue] = useState<Issue>(
    () =>
      peekIssue(seed.id) ||
      (cachedIssueDetail(seed.id)?.detail as Issue | undefined) ||
      seed,
  );
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setIssue(
      (old) =>
        peekIssue(seed.id) || (old.id === seed.id ? { ...old, ...seed } : seed),
    );
  }, [seed]);
  useEffect(() => {
    let live = true;
    setError("");
    const off = onStoreIssue(seed.id, (i) => {
      if (live) setIssue((old) => ({ ...old, ...i }));
    });
    fetchIssueDetail(seed.identifier || seed.id, seed.id)
      .then((e) => {
        if (live) setIssue((old) => ({ ...old, ...(e.detail as Issue) }));
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
      off();
    };
  }, [seed.id, seed.identifier, retry]);
  const lifecycle = (verb: "archive" | "delete") => async () => {
    const settle = hideIssueLocally(issue.id);
    onClose();
    try {
      const field = verb === "archive" ? "issueArchive" : "issueDelete";
      const d = await gql(`mutation($id:String!){${field}(id:$id){success}}`, {
        id: issue.identifier,
      });
      if (!d[field]?.success) throw Error(`Task could not be ${verb}d.`);
      settle(true);
    } catch (e) {
      settle(false);
      onError(`Could not ${verb} ${issue.identifier}: ${errorText(e)}`);
      throw e;
    }
  };
  return (
    <GraphIssueDetail
      key={issue.id}
      workspaceName={workspaceName}
      viewer={viewer}
      issue={issue}
      issueList={issueList}
      onOpenIssue={onOpenIssue}
      loading={false}
      error={error}
      onArchive={lifecycle("archive")}
      onDelete={lifecycle("delete")}
      onDeleteComment={async (cid) => {
        const d = await gql(
          `mutation($id:String!){commentDelete(id:$id){success}}`,
          { id: cid },
        );
        if (!d.commentDelete?.success)
          throw Error("Comment could not be deleted.");
      }}
      teams={teams}
      projects={projects}
      labels={labels}
      cycles={cycles}
      onUpdate={(input) => updateIssue(issue.id, input)}
      onBack={onClose}
      backLabel="Inbox"
      onRetry={() => setRetry((r) => r + 1)}
    />
  );
}

function PulsePage({
  path,
  onOpenProject,
}: {
  path: string;
  onOpenProject: (project: Project) => void;
  revision: number;
}) {
  const meta = useProjectSyncMeta();
  const projects = useProjects();
  const projectById = useMemo(
    () => new Map(projects.map((p) => [p.id, p])),
    [projects],
  );
  // Project fields on a post are read live from the store, so only new/changed project updates refetch the feed.
  const version = useProjectEntityVersion(["projectUpdate"]);
  const fetchPage = React.useCallback(
    (first: number, after: string | null) =>
      gql(
        `query PulsePosts($first:Int,$after:String){projectUpdates(first:$first,after:$after){nodes{${PULSE_FIELDS}} pageInfo{hasNextPage endCursor}}}`,
        { first, after },
      ).then((d) => d.projectUpdates as PageOf<PulsePost>),
    [],
  );
  const feed = usePagedCache<PulsePost>(
    pulseKey(meta),
    fetchPage,
    version,
    20,
    "Pulse",
  );
  const hasMore = feed.more,
    error = feed.error;
  const posts = useMemo(
    () =>
      feed.rows.map((post) => {
        const live = projectById.get(post.project.id);
        return live
          ? {
              ...post,
              project: {
                ...post.project,
                name: live.name,
                targetDate: live.targetDate,
                lead: live.lead,
                health: live.health ?? post.project.health,
                progress: live.progress ?? post.project.progress,
              },
            }
          : post;
      }),
    [feed.rows, projectById],
  );
  const [openComments, setOpenComments] = useState<string | null>(null),
    [selectedTab, setSelectedTab] = useState<"For me" | "Popular" | "Recent">(
      () =>
        path.endsWith("/following")
          ? "For me"
          : path.endsWith("/popular")
            ? "Popular"
            : "Recent",
    );
  // Pulse comments. The list is seeded from the query and extended locally on send, so a
  // reply appears without refetching the feed; count reads from the same list.
  const [commentBodies, setCommentBodies] = useState<Record<string, string>>(
    {},
  );
  const [commentExtra, setCommentExtra] = useState<
    Record<string, PulseComment[]>
  >({});
  const [commentStatus, setCommentStatus] = useState<
    Record<string, { pending?: boolean; error?: string; posted?: boolean }>
  >({});
  const inFlight = useRef(new Set<string>());
  const sendComment = async (post: PulsePost) => {
    const body = (commentBodies[post.id] || "").trim();
    if (!body || inFlight.current.has(post.id)) return;
    inFlight.current.add(post.id);
    setCommentStatus((s) => ({ ...s, [post.id]: { pending: true } }));
    try {
      const d = await gql(
        `mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment{id body createdAt user{id name avatarSeed}}}}`,
        { input: { projectUpdateId: post.id, body } },
      );
      if (!d.commentCreate?.success || !d.commentCreate.comment)
        throw Error("The reply was rejected");
      setCommentExtra((s) => ({
        ...s,
        [post.id]: [...(s[post.id] || []), d.commentCreate.comment],
      }));
      setCommentBodies((s) => ({ ...s, [post.id]: "" }));
      setCommentStatus((s) => ({ ...s, [post.id]: { posted: true } }));
    } catch (e) {
      setCommentStatus((s) => ({
        ...s,
        [post.id]: {
          error: e instanceof Error ? e.message : "Could not post the reply",
        },
      }));
    } finally {
      inFlight.current.delete(post.id);
    }
  };
  const tab = selectedTab;
  useEffect(() => {
    const onPop = () =>
      setSelectedTab(
        location.pathname.endsWith("/following")
          ? "For me"
          : location.pathname.endsWith("/popular")
            ? "Popular"
            : "Recent",
      );
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const time = (date: string) => {
    const t = Date.parse(date);
    if (!Number.isFinite(t)) return "Date unavailable";
    const days = Math.max(0, Math.floor((Date.now() - t) / 86400000));
    const ago = (n: number, unit: string) =>
      `${n} ${unit}${n === 1 ? "" : "s"} ago`;
    return days < 1
      ? "Today"
      : days < 7
        ? ago(days, "day")
        : days < 35
          ? ago(Math.floor(days / 7), "week")
          : ago(Math.floor(days / 30), "month");
  };
  const health = (post: PulsePost) => post.health || post.status;
  const views = [
    ["For me", "following"],
    ["Popular", "popular"],
    ["Recent", "all"],
  ] as const;
  const eraOf = (iso: string) =>
    Date.now() - Date.parse(iso) >= 30 * 86400000 ? "Older" : "Recent";
  const setView = (label: "For me" | "Popular" | "Recent", slug: string) => {
    history.pushState({}, "", `/workspace/pulse/${slug}`);
    setSelectedTab(label);
  };
  return (
    <section
      className="flex p-4 flex-col text-foreground overflow-hidden flex-1 min-h-0 min-w-0"
      data-pulse-page=""
      aria-label="Pulse"
    >
      {/* One column for the whole page: the title, the tabs and the posts share this
          box. Centring the chrome and the posts separately put them 5px apart,
          because the posts' scroller is narrower by its own scrollbar. */}
      <div
        className="flex flex-1 min-h-0 flex-col w-[min(100%,var(--ds-feed-width))] shell-wide:w-[min(100%,calc(var(--ds-feed-width)+var(--ds-feed-rail)+var(--ds-feed-rail-gap)))] mt-0 mr-auto mb-0 ml-auto pt-0 pr-(--ds-feed-gutter) pb-0 pl-(--ds-feed-gutter)"
        data-pulse-column=""
      >
        <header
          className="border-b border-line flex flex-none items-center justify-between h-(--ds-tabstrip-height)"
          data-pulse-heading=""
        >
          <h1 className="mt-0 mr-0 mb-0 ml-0 text-(length:--hoja-title-size) font-(--hoja-title-weight) tracking-normal leading-[normal] ">
            Pulse
          </h1>
          <button
            className="flex items-center pt-1.25 pr-1.75 pb-1.25 pl-1.75 bg-transparent text-muted hover:bg-subtle cursor-not-allowed opacity-50 gap-(--ds-group-bar-gap) rounded-(--ds-tab-radius) focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 text-(length:--ds-feed-meta-size)"
            data-pulse-subscription=""
            type="button"
            aria-disabled="true"
            aria-describedby="pulse-subscription-unavailable"
            title="Pulse email subscriptions are not available on this server"
            aria-label="Subscription unavailable"
          >
            <Bell size={15} aria-hidden="true" />
          </button>
          <span id="pulse-subscription-unavailable" className="sr-only">
            Email subscriptions to Pulse are not available on this server, so
            there is nothing to turn on here.
          </span>
        </header>
        <TabStrip
          className="flex-none h-(--ds-tabstrip-height) gap-(--ds-tabstrip-gap)"
          data-pulse-tabs=""
          role="tablist"
          label="Pulse views"
        >
          {views.map(([label, slug]) => (
            <Tab
              key={slug}
              role="tab"
              aria-selected={tab === label}
              title={
                label === "Recent"
                  ? undefined
                  : `${label} ranking is not available; showing recent project updates`
              }
              onClick={() => setView(label, slug)}
              className="h-(--ds-feed-tab-height) rounded-(--ds-feed-tab-radius) p-(--ds-feed-tab-padding) shell-phone:h-11 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            >
              {label}
            </Tab>
          ))}
          <button
            className="grid place-items-center ml-0.5 bg-transparent text-muted hover:bg-subtle cursor-not-allowed opacity-50 rounded-(--ds-tab-radius) w-(--ds-tab-height) h-(--ds-tab-height) focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-pulse-add-view=""
            type="button"
            aria-disabled="true"
            aria-describedby="pulse-views-unavailable"
            title="Custom Pulse views are not available"
            aria-label="Add new view unavailable"
          >
            <Plus size={15} aria-hidden="true" />
          </button>
        </TabStrip>
        <span id="pulse-views-unavailable" className="sr-only">
          Custom Pulse views are not available yet; For me, Popular and Recent
          are the views this server supports.
        </span>
        <ScrollArea className="min-h-0" variant="list">
          <div
            className="mt-5.5 mr-0 mb-0 ml-0 pt-0 pr-0 pb-15 pl-0 shell-phone:mt-5.5 shell-phone:pt-0 shell-phone:pb-10"
            data-pulse-content=""
          >
            {tab !== "Recent" && (
              <p
                className="border mt-0 mr-0 mb-5 ml-0 pt-2.25 pr-3 pb-2.25 pl-3 border-line rounded-md bg-panel text-muted text-[12px]"
                role="status"
              >
                {tab} ranking is not available in Hoja yet. Showing recent
                project updates.
              </p>
            )}
            {posts.map((post, index) => {
              // A refetch moves a locally-appended reply into post.comments.nodes while it
              // is still in commentExtra; keyed by id, the later copy wins and nothing is
              // rendered twice.
              const replies = [
                ...new Map(
                  [
                    ...(post.comments?.nodes || []),
                    ...(commentExtra[post.id] || []),
                  ].map((c) => [c.id, c] as const),
                ).values(),
              ];
              const status = commentStatus[post.id] || {};
              return (
                <React.Fragment key={post.id}>
                  {(index === 0 ||
                    eraOf(post.createdAt) !==
                      eraOf(posts[index - 1].createdAt)) && (
                    <GroupBar
                      as="h2"
                      className="flex items-center h-auto mt-0 mr-0 mb-4.5 ml-0 pt-0 pr-0 pb-0 pl-0 rounded-none text-muted font-normal [&:after]:h-px [&:after]:flex-1 [&:after]:bg-line gap-(--ds-group-bar-gap) min-h-(--ds-group-bar-height) after:content-[''] text-(length:--ds-feed-page-title-size)"
                      data-pulse-era=""
                      label={eraOf(post.createdAt)}
                    />
                  )}
                  <article
                    className="pt-6 pr-0 pb-6 pl-0 mt-0 mr-0 mb-0 ml-0 border-t border-line shell-wide:grid shell-wide:grid-cols-[minmax(0,1fr)_var(--ds-feed-rail)] shell-wide:gap-x-(--ds-feed-rail-gap)"
                    aria-labelledby={`pulse-post-${post.id}`}
                  >
                    <header
                      className="flex items-start gap-3 shell-wide:col-start-1"
                      data-pulse-post-heading=""
                    >
                      <div className="min-w-0 flex-1">
                        <h3
                          className="mt-0 mr-0 mb-1.25 ml-0 text-(length:--ds-feed-title-size) font-semibold tracking-tight wrap-break-word max-md:text-lg shell-phone:text-[17px]"
                          id={`pulse-post-${post.id}`}
                        >
                          <a
                            className="text-inherit no-underline hover:underline focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
                            href={`/workspace/project/${encodeURIComponent(post.project.id)}`}
                            onClick={(e) => {
                              e.preventDefault();
                              onOpenProject(post.project);
                            }}
                          >
                            {post.project.name}
                          </a>
                        </h3>
                        <div
                          className="flex flex-wrap items-center gap-2 text-muted leading-(--ds-feed-meta-line) text-(length:--ds-feed-meta-size)"
                          data-pulse-meta=""
                        >
                          {post.actor?.name && (
                            <>
                              <span
                                className="inline-grid place-items-center leading-none w-(--ds-feed-avatar-size) h-(--ds-feed-avatar-size)"
                                data-pulse-avatar=""
                                aria-hidden="true"
                              >
                                <UserBlobatar user={post.actor} />
                              </span>
                              <span>{post.actor.name}</span>
                            </>
                          )}
                          <time
                            dateTime={post.createdAt}
                            title={new Date(post.createdAt).toLocaleString()}
                          >
                            {time(post.createdAt)}
                          </time>
                          {health(post) && (
                            <span
                              className={`inline-flex items-center gap-(--ds-group-bar-gap) ${/risk/i.test(health(post) ?? "") ? "text-[var(--health-risk)]" : "text-[var(--health-ok)]"}`}
                              data-pulse-health=""
                            >
                              <Activity size={14} aria-hidden="true" />
                              Project {health(post)}
                            </span>
                          )}
                        </div>
                      </div>
                    </header>
                    <div
                      className="mt-4.5 mr-0 mb-6 ml-0 wrap-break-word leading-[1.6] shell-wide:col-start-1"
                      data-pulse-post-body=""
                    >
                      <Markdown text={post.body} density="block" />
                    </div>
                    {(post.progress != null ||
                      post.project.targetDate ||
                      post.project.lead?.name) && (
                      <dl
                        className="grid gap-1.75 mt-0 mr-0 mb-6 ml-0 pl-3 text-(length:--ds-feed-meta-size) border-l-2 border-line shell-wide:col-start-2 shell-wide:row-start-2 shell-wide:self-start shell-wide:mt-4.5 shell-wide:mb-0 shell-wide:border-l-0 shell-wide:pl-0 shell-wide:[&_dt]:w-auto shell-wide:[&_dt]:flex-[0_0_auto]"
                        data-pulse-changes=""
                      >
                        {post.project.lead?.name && (
                          <PropertyRow label="Lead">
                            {post.project.lead.name}
                          </PropertyRow>
                        )}
                        {post.project.targetDate && (
                          <PropertyRow label="Target date">
                            {post.project.targetDate}
                          </PropertyRow>
                        )}
                        {post.progress != null && (
                          <PropertyRow label="Progress">
                            {Math.round(post.progress)}%
                          </PropertyRow>
                        )}
                      </dl>
                    )}
                    <div
                      className="flex items-center gap-2 shell-wide:col-start-1"
                      data-pulse-actions=""
                    >
                      <button
                        className="flex items-center gap-1.5 pt-1.25 pr-2.25 pb-1.25 pl-2.25 border border-line bg-panel text-muted hover:bg-subtle aria-expanded:bg-subtle aria-expanded:text-foreground rounded-(--ds-feed-tab-radius) focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                        type="button"
                        aria-expanded={openComments === post.id}
                        aria-controls={
                          openComments === post.id
                            ? `pulse-comments-${post.id}`
                            : undefined
                        }
                        aria-label={`${openComments === post.id ? "Hide" : "Show"} ${replies.length} ${
                          replies.length === 1 ? "reply" : "replies"
                        } to ${post.project.name}`}
                        onClick={() =>
                          setOpenComments((v) =>
                            v === post.id ? null : post.id,
                          )
                        }
                      >
                        <MessageCircle size={15} aria-hidden="true" />
                        {replies.length > 0 && (
                          <span data-pulse-comment-count="">
                            {replies.length}
                          </span>
                        )}
                      </button>
                    </div>
                    {openComments === post.id && (
                      <div
                        className="mt-3 mr-0 mb-0 ml-0 flex flex-col gap-3 shell-wide:col-start-1"
                        id={`pulse-comments-${post.id}`}
                        data-pulse-comments=""
                      >
                        {replies.length ? (
                          <ul className="flex flex-col gap-3 mt-0 mr-0 mb-0 ml-0 pt-1 pr-0 pb-0 pl-0 list-none">
                            {replies.map((reply) => (
                              <li
                                key={reply.id}
                                className="flex items-start gap-2"
                              >
                                <span
                                  className="inline-grid place-items-center leading-none flex-none w-(--ds-feed-avatar-size) h-(--ds-feed-avatar-size)"
                                  aria-hidden="true"
                                >
                                  <UserBlobatar user={reply.user} />
                                </span>
                                <div className="min-w-0">
                                  <p className="flex items-center gap-(--ds-group-bar-gap) mt-0 mr-0 mb-1 ml-0 text-(length:--ds-feed-meta-size) text-muted">
                                    <span>{reply.user?.name || "Someone"}</span>
                                    {reply.createdAt && (
                                      <time dateTime={reply.createdAt}>
                                        {time(reply.createdAt)}
                                      </time>
                                    )}
                                  </p>
                                  <div className="mt-0 mr-0 mb-0 ml-0 wrap-break-word">
                                    <Markdown text={reply.body} />
                                  </div>
                                </div>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="mt-0 mr-0 mb-0 ml-0 text-muted text-[12px]">
                            No replies yet. Reply to ask a question or leave
                            context for whoever reads this next.
                          </p>
                        )}
                        <Composer
                          className="mt-0 max-w-none bg-subtle rounded-(--ds-composer-radius) focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 [&_input]:text-[14px] [&_input::placeholder]:text-muted [&_button]:rounded-(--ds-composer-radius)"
                          value={commentBodies[post.id] || ""}
                          placeholder={`Reply to ${post.project.name}…`}
                          sendDisabled={!(commentBodies[post.id] || "").trim()}
                          sendIcon={<Send size={15} aria-hidden="true" />}
                          sendLabel="Post reply"
                          onChange={(v) =>
                            setCommentBodies((s) => ({ ...s, [post.id]: v }))
                          }
                          onSend={() => void sendComment(post)}
                          data-pulse-comment-composer=""
                        />
                        <span className="sr-only" role="status">
                          {status.posted ? "Reply posted" : ""}
                        </span>
                        {status.pending && (
                          <p className="mt-0 mr-0 mb-0 ml-0 text-[12px] text-muted">
                            Posting…
                          </p>
                        )}
                        {status.error && (
                          <p
                            className="mt-0 mr-0 mb-0 ml-0 text-[12px] text-[var(--health-risk)]"
                            role="alert"
                          >
                            {status.error}
                          </p>
                        )}
                      </div>
                    )}
                  </article>
                </React.Fragment>
              );
            })}
            {!feed.hasData && !error && (
              <p
                className="max-w-180 mt-15 mr-auto mb-15 ml-auto pt-7 pr-7 pb-7 pl-7 text-center text-muted text-[13px] gap-(--ds-empty-gap)"
                data-pulse-status=""
                role="status"
              >
                Loading project updates…
              </p>
            )}
            {feed.hasData && !posts.length && !error && (
              <EmptyState
                className="max-w-180 mt-15 mr-auto mb-15 ml-auto pt-7 pr-7 pb-7 pl-7 text-center text-muted text-[13px] gap-(--ds-empty-gap)"
                data-pulse-status=""
              >
                <Activity size={24} aria-hidden="true" />
                <h2 className="mt-2 mr-0 mb-2 ml-0 text-foreground text-[17px]">
                  No project updates yet
                </h2>
                <p>
                  Project updates will appear here when they are published. Task
                  activity is separate from Pulse.
                </p>
              </EmptyState>
            )}
            {error && (
              <div
                className="max-w-180 mt-15 mr-auto mb-15 ml-auto pt-7 pr-7 pb-7 pl-7 text-center text-muted text-[13px] gap-(--ds-empty-gap)"
                data-pulse-status=""
                role="alert"
              >
                <h2 className="mt-2 mr-0 mb-2 ml-0 text-foreground text-[17px]">
                  Couldn’t load Pulse
                </h2>
                <p>{error}</p>
                <button
                  className="mt-3 pt-2 pr-3 pb-2 pl-3 border border-line bg-panel rounded-(--ds-tab-radius) focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  type="button"
                  onClick={feed.retry}
                >
                  Retry
                </button>
              </div>
            )}
            {hasMore && (
              <button
                className="block mt-4.5 mr-auto mb-4.5 ml-auto pt-2 pr-3 pb-2 pl-3 border border-line bg-panel rounded-(--ds-tab-radius) focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-pulse-load-more=""
                type="button"
                disabled={feed.loadingMore}
                onClick={feed.loadMore}
              >
                {feed.loadingMore ? "Loading updates…" : "Load more updates"}
              </button>
            )}
          </div>
        </ScrollArea>
      </div>
    </section>
  );
}

export function StatusIcon({
  state,
  className = "size-3.5",
}: {
  state: { name?: string; type?: string; color?: string };
  className?: string;
}) {
  const c = state.color || "#a1a1aa",
    n = (state.name || "").toLowerCase(),
    t = state.type || "";
  const svg = (body: React.ReactNode) => (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke={c}
      strokeWidth="1.5"
      aria-hidden="true"
      className={`status-icon ${className}`}
    >
      {body}
    </svg>
  );
  if (n === "duplicate")
    return svg(
      <>
        <circle cx="8" cy="8" r="6" />
        <path d="M4.5 11.5l7-7" />
      </>,
    );
  if (t === "completed")
    return svg(
      <>
        <circle cx="8" cy="8" r="6" fill={c} />
        <path d="M5 8.2l2 2 4-4.4" stroke="#fff" />
      </>,
    );
  if (t === "canceled")
    return svg(
      <>
        <circle cx="8" cy="8" r="6" fill={c} opacity=".6" />
        <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke="#fff" />
      </>,
    );
  if (t === "backlog")
    return svg(<circle cx="8" cy="8" r="6" strokeDasharray="2 2" />);
  if (n === "in review")
    return svg(
      <>
        <circle cx="8" cy="8" r="6" />
        <path d="M8 8V2A6 6 0 1 1 2 8Z" fill={c} />
      </>,
    );
  if (t === "started")
    return svg(
      <>
        <circle cx="8" cy="8" r="6" />
        <path d="M8 2A6 6 0 0 1 8 14Z" fill={c} />
      </>,
    );
  return svg(<circle cx="8" cy="8" r="6" />);
}
/* The one issue list + board surface: My issues, team issues and project issues all render this,
 * so rows, groups, toolbar, filters, display options and the Kanban stay identical everywhere. */
function MyIssuesContent({
  issues,
  onNew,
  onOpenIssue,
  tab,
  tabs,
  activityEvents = NO_EVENTS,
  activityHasMore = false,
  activityLoading = false,
  activityError = "",
  onLoadActivity = noop,
  teams,
  projects,
  older,
  onLoadOlder,
  emptyCopy: emptyOverride,
  layout,
  onLayoutChange,
  states,
  onMoved,
  onUpdate,
}: {
  layout: IssueLayout;
  onLayoutChange: (layout: IssueLayout) => void;
  states: IssueState[];
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  issues: Issue[];
  onNew: (stateId?: string) => void;
  onOpenIssue: (issue: Issue) => void;
  tab: MyIssuesTab;
  tabs?: React.ReactNode;
  activityEvents?: IssueActivityEvent[];
  activityHasMore?: boolean;
  activityLoading?: boolean;
  activityError?: string;
  onLoadActivity?: () => void;
  teams: Team[];
  projects: Project[];
  older?: OlderState;
  onLoadOlder?: (retry?: boolean) => void;
  emptyCopy?: [string, string];
}) {
  const [collapsedTree, setCollapsedTree] = useState<Record<string, boolean>>(
    {},
  );
  const [status, setStatus] = useState(""),
    [priority, setPriority] = useState(""),
    [teamId, setTeamId] = useState(""),
    [projectId, setProjectId] = useState(""),
    [textQuery, setTextQuery] = useState(""),
    [cycleOnly, setCycleOnly] = useState(false),
    [labelId, setLabelId] = useState(""),
    [assigneeId, setAssigneeId] = useState(""),
    [creatorId, setCreatorId] = useState(""),
    [subscriberId, setSubscriberId] = useState(""),
    [dateField, setDateField] = useState(""),
    [dateFrom, setDateFrom] = useState(""),
    [dateTo, setDateTo] = useState(""),
    [showProject, setShowProject] = useState(
      () => localStorage.getItem("hoja_my_issues_project") !== "false",
    ),
    [showDate, setShowDate] = useState(
      () => localStorage.getItem("hoja_my_issues_date") !== "false",
    ),
    [grouping, setGrouping] = useState(
      () => localStorage.getItem("hoja_my_issues_grouping") || "Focus",
    ),
    /* Collapsed parents, keyed by issue id. Per-surface and deliberately not persisted:
       a collapsed epic in one view must not hide work in another. */
    [treeCollapsed, setTreeCollapsed] = useState<Record<string, boolean>>({}),
    [showAssignee, setShowAssignee] = useState(
      () => localStorage.getItem("hoja_my_issues_assignee") !== "false",
    ),
    [showPriority, setShowPriority] = useState(
      () => localStorage.getItem("hoja_my_issues_priority") !== "false",
    ),
    [showStatus, setShowStatus] = useState(
      () => localStorage.getItem("hoja_my_issues_status") !== "false",
    ),
    [showLabels, setShowLabels] = useState(
      () => localStorage.getItem("hoja_my_issues_labels") !== "false",
    ),
    [showDue, setShowDue] = useState(
      () => localStorage.getItem("hoja_my_issues_due") === "true",
    ),
    [filterOpen, setFilterOpen] = useState(false),
    [displayOpen, setDisplayOpen] = useState(false),
    [collapsed, setCollapsed] = useState<string[]>([]),
    [completedRange, setCompletedRange] = useState<CompletedRange>(() => {
      const v = localStorage.getItem("hoja_my_issues_completed");
      return v === "none" || v === "month" || v === "all" ? v : "week";
    });
  useEffect(() => {
    localStorage.setItem("hoja_my_issues_project", String(showProject));
    localStorage.setItem("hoja_my_issues_date", String(showDate));
    localStorage.setItem("hoja_my_issues_grouping", grouping);
    localStorage.setItem("hoja_my_issues_assignee", String(showAssignee));
    localStorage.setItem("hoja_my_issues_priority", String(showPriority));
    localStorage.setItem("hoja_my_issues_status", String(showStatus));
    localStorage.setItem("hoja_my_issues_labels", String(showLabels));
    localStorage.setItem("hoja_my_issues_due", String(showDue));
  }, [
    showProject,
    showDate,
    grouping,
    showAssignee,
    showPriority,
    showStatus,
    showLabels,
    showDue,
  ]);
  useEffect(() => {
    localStorage.setItem("hoja_my_issues_completed", completedRange);
  }, [completedRange]);
  // Closed issues outside the synced window are fetched only when this view can show them.
  const need =
    Boolean(onLoadOlder) &&
    (tab === "Created" || (tab === "Assigned" && completedRange === "all"));
  const needStatus = need ? older?.status : undefined;
  useEffect(() => {
    if (need) onLoadOlder?.();
  }, [need, needStatus, onLoadOlder]);
  // Filter options only depend on the issue set, not on each keystroke.
  const availableUsers = useMemo(
    () => [
      ...new Map(
        issues
          .flatMap((i) =>
            [i.assignee, i.creator].filter(
              (u): u is { id: string; name?: string } => Boolean(u?.id),
            ),
          )
          .map((u) => [u.id, u]),
      ).values(),
    ],
    [issues],
  );
  const availableLabels = useMemo(
    () => [
      ...new Map(
        issues.flatMap((i) => i.labels?.nodes || []).map((l) => [l.id, l]),
      ).values(),
    ],
    [issues],
  );
  const clearMyFilters = () => {
    setStatus("");
    setPriority("");
    setTeamId("");
    setProjectId("");
    setCycleOnly(false);
    setTextQuery("");
    setLabelId("");
    setAssigneeId("");
    setCreatorId("");
    setSubscriberId("");
    setDateField("");
    setDateFrom("");
    setDateTo("");
  };
  const scoped = useMemo(
    () =>
      tab === "Activity"
        ? activityEvents.map((e) => e.issue)
        : tab === "Assigned"
          ? issues.filter((i) => {
              if (!["completed", "canceled"].includes(i.state?.type || ""))
                return true;
              if (completedRange === "all") return true;
              if (completedRange === "none") return false;
              const at = Date.parse(i.stateChangedAt || i.updatedAt || "");
              return (
                Number.isFinite(at) &&
                at >= Date.now() - (completedRange === "week" ? 7 : 30) * 864e5
              );
            })
          : issues,
    [tab, activityEvents, issues, completedRange],
  );
  const deferredQuery = React.useDeferredValue(textQuery);
  const visible = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase();
    const df = dateField as "createdAt" | "updatedAt" | "dueDate" | "";
    return scoped.filter(
      (i) =>
        (!status || (i.state && stateKey(i.state) === status)) &&
        (!priority || String(i.priority) === priority) &&
        (!teamId || i.team?.id === teamId) &&
        (!projectId || i.project?.id === projectId) &&
        (!cycleOnly || Boolean(i.cycle)) &&
        (!q || `${i.identifier} ${i.title}`.toLowerCase().includes(q)) &&
        (!labelId || i.labels?.nodes.some((l) => l.id === labelId)) &&
        (!assigneeId || i.assignee?.id === assigneeId) &&
        (!creatorId || i.creator?.id === creatorId) &&
        (!subscriberId ||
          i.subscribers?.nodes.some((u) => u.id === subscriberId)) &&
        (!df ||
          ((!dateFrom ||
            (Boolean(i[df]) && (i[df] || "").slice(0, 10) >= dateFrom)) &&
            (!dateTo ||
              (Boolean(i[df]) && (i[df] || "").slice(0, 10) <= dateTo)))),
    );
  }, [
    scoped,
    status,
    priority,
    teamId,
    projectId,
    cycleOnly,
    deferredQuery,
    labelId,
    assigneeId,
    creatorId,
    subscriberId,
    dateField,
    dateFrom,
    dateTo,
  ]);
  // Activity rows: events grouped by day once, instead of re-filtering every event for every row.
  const eventsByDate = useMemo(() => {
    const visibleIds = new Set(visible.map((i) => i.id));
    const m = new Map<string, IssueActivityEvent[]>();
    if (tab !== "Activity") return m;
    for (const e of activityEvents) {
      if (!visibleIds.has(e.issue.id)) continue;
      const d = e.createdAt.slice(0, 10);
      const list = m.get(d);
      if (list) list.push(e);
      else m.set(d, [e]);
    }
    return m;
  }, [tab, activityEvents, visible]);
  const buckets = useMemo(() => {
    const ordered = [...visible].sort((a, b) => {
      const ak = a.createdAt || "",
        bk = b.createdAt || "";
      return ak === bk ? 0 : ak < bk ? 1 : -1;
    });
    const out: { name: string; items: Issue[]; state?: IssueState }[] = [];
    const add = (name: string, items: Issue[]) => {
      if (items.length) out.push({ name, items });
    };
    if (tab === "Assigned" && grouping === "Focus") {
      const byPriority = [...ordered].sort(
        (a, b) => (a.priority || 5) - (b.priority || 5),
      );
      const byState = new Map<string, Issue[]>(
        states.map((s) => [stateKey(s), []]),
      );
      const none: Issue[] = [];
      for (const i of byPriority) {
        const list = i.state ? byState.get(stateKey(i.state)) : undefined;
        if (list) list.push(i);
        else none.push(i);
      }
      const closedLast = (s: IssueState) =>
        s.type === "completed" || s.type === "canceled" ? 1 : 0;
      for (const state of [...states].sort(
        (a, b) => closedLast(a) - closedLast(b),
      )) {
        const items = byState.get(stateKey(state)) || [];
        if (items.length) out.push({ name: state.name, items, state });
      }
      add("No status", none);
    } else if (tab === "Assigned" && grouping !== "Focus") {
      const groupName = (i: Issue) =>
        grouping === "Status"
          ? i.state?.name || "Unknown status"
          : grouping === "Project"
            ? i.project?.name || "No project"
            : grouping === "Priority"
              ? i.priorityLabel || "No priority"
              : grouping === "Cycle"
                ? i.cycle?.name || "No cycle"
                : grouping === "Label"
                  ? i.labels?.nodes[0]?.name || "No labels"
                  : i.team?.name || "No team";
      const groups = new Map<string, Issue[]>();
      for (const i of ordered) {
        const n = groupName(i);
        const list = groups.get(n);
        if (list) list.push(i);
        else groups.set(n, [i]);
      }
      for (const [name, items] of groups) add(name, items);
    } else if (tab === "Activity") {
      for (const [date, events] of eventsByDate)
        add(
          date,
          events.map((e) => e.issue),
        );
    } else add("", ordered);
    return out;
  }, [visible, tab, grouping, states, eventsByDate]);
  const emptyCopy =
    emptyOverride ||
    (tab === "Assigned"
      ? ["You’re all caught up", "Tasks assigned to you will show up here."]
      : tab === "Created"
        ? ["No tasks created yet", "Tasks you create will be collected here."]
        : tab === "Subscribed"
          ? ["No subscribed tasks", "Tasks you subscribe to will appear here."]
          : [
              "No recorded activity",
              "New task events will appear here as work happens.",
            ]);
  // Long lists are windowed: past WINDOW_ROWS rows only the rows near the viewport are mounted (spacers keep the scroll height).
  const shownTotal = buckets.reduce(
    (n, g) => n + (collapsed.includes(g.name) ? 0 : g.items.length),
    0,
  );
  const windowed = false;
  const listRef = useRef<HTMLDivElement>(null);
  const win = useScrollWindow(listRef, listRef, windowed);
  const [rowH, setRowH] = useState(36),
    [headH, setHeadH] = useState(36);
  useLayoutEffect(() => {
    if (!windowed) return;
    const root = listRef.current;
    const r = root?.querySelector<HTMLElement>("[data-my-issue-row]");
    if (r && r.offsetHeight && Math.abs(r.offsetHeight - rowH) > 0.5)
      setRowH(r.offsetHeight);
    const h = root?.querySelector<HTMLElement>("[data-my-issue-group]>header");
    if (h) {
      const cs = getComputedStyle(h);
      const full =
        h.offsetHeight + parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
      if (Math.abs(full - headH) > 0.5) setHeadH(full);
    }
  }, [windowed, rowH, headH, shownTotal]);
  let y = 0;
  const teamStates = (i: Issue) =>
    teams.find((t) => t.id === i.team?.id)?.states.nodes;
  // Activity paging is lazy: a sentinel after the list requests the next page when it scrolls into
  // view. The button stays as the explicit keyboard path and the fallback when IntersectionObserver
  // is unavailable. Loads stay serialised by the caller, which also rejects a non-advancing cursor and
  // an empty page, so a repeated intersection can never duplicate rows or loop forever.
  const loadMoreRef = useRef<HTMLDivElement | null>(null);
  const loadMoreCb = useRef(onLoadActivity);
  loadMoreCb.current = onLoadActivity;
  // A filter that hides every loaded row keeps the sentinel in view; paging from there would drain
  // the feed for nothing, so auto-load only runs while rows are actually rendered.
  const canAutoLoad =
    tab === "Activity" &&
    activityHasMore &&
    !activityLoading &&
    !activityError &&
    visible.length > 0;
  useEffect(() => {
    if (!canAutoLoad) return;
    const el = loadMoreRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    let requested = false;
    const io = new IntersectionObserver(
      (entries) => {
        if (requested || !entries.some((e) => e.isIntersecting)) return;
        requested = true;
        loadMoreCb.current();
      },
      { rootMargin: "0px 0px 600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [canAutoLoad]);
  const toolbarRef = useRef<HTMLDivElement>(null),
    filterPanelRef = useRef<HTMLDivElement>(null),
    displayPanelRef = useRef<HTMLDivElement>(null);
  return (
    <div
      className="relative flex p-4 gap-4 min-h-0 min-w-0 flex-1 flex-col bg-bg text-foreground max-md:flex-1 max-md:flex max-md:flex-col max-md:min-h-0 max-md:h-auto [&_[data-my-issue-group]>header_button:last-child:not(:first-child)]:ml-auto shell-phone:flex-1 shell-desktop:min-h-0 shell-phone:min-h-0 shell-phone:h-auto"
      data-my-issues-view=""
    >
      <div
        className="flex min-w-0 shrink-0 flex-wrap items-center gap-2"
        data-my-issue-header=""
      >
        {tabs}
        <div
          className="ml-auto flex flex-none items-center gap-1.5 bg-bg  text-foreground"
          data-my-issue-toolbar=""
          ref={toolbarRef}
        >
          <LayoutSwitch layout={layout} onChange={onLayoutChange} />
          <UIPopover
            open={filterOpen}
            onOpenChange={(open) => {
              setFilterOpen(open);
              if (open) setDisplayOpen(false);
            }}
          >
            <PopoverTrigger asChild>
              <button
                className="inline-flex size-7 flex-none items-center justify-center gap-1.5 rounded-full border border-line bg-panel  text-sm text-foreground hover:bg-subtle aria-expanded:bg-accent-soft aria-expanded:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11"
                aria-label="Add filter"
                aria-expanded={filterOpen}
              >
                <ListFilter size={16} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              side="bottom"
              sideOffset={4}
              className="z-50 grid w-[min(245px,calc(100vw-35px))] max-w-full max-h-[calc(100dvh-110px)] content-start gap-2.5 overflow-y-auto overscroll-contain rounded-[9px] border-line bg-panel p-3 text-[12px] text-foreground shadow-[0_12px_32px_#0002]"
              data-my-issue-popover=""
              aria-label="Task filters"
              onCloseAutoFocus={(event) => event.preventDefault()}
              onEscapeKeyDown={() =>
                toolbarRef.current
                  ?.querySelector<HTMLButtonElement>(
                    '[aria-label="Add filter"]',
                  )
                  ?.focus()
              }
              ref={filterPanelRef}
            >
              <div
                className="grid gap-1"
                data-my-status-filter=""
                role="group"
                aria-label="Status"
              >
                <span>Status</span>
                <button
                  type="button"
                  aria-pressed={!status}
                  className="w-full rounded-md bg-transparent pl-0 pr-0 pt-1 pb-1 text-left text-[#4f5d9d] aria-pressed:bg-accent-soft aria-pressed:font-semibold shell-phone:min-h-11"
                  data-my-status-filter-all=""
                  onClick={() => setStatus("")}
                >
                  All statuses
                </button>
                <div
                  className="grid grid-cols-2 gap-x-2 gap-y-0.5"
                  data-my-status-filter-grid=""
                >
                  {states.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      aria-pressed={status === stateKey(s)}
                      className="flex min-w-0 items-center gap-2 rounded-md bg-transparent pl-0 pr-0 pt-1 pb-1 text-left text-[#4f5d9d] aria-pressed:bg-accent-soft aria-pressed:font-semibold shell-phone:min-h-11"
                      onClick={() =>
                        setStatus(status === stateKey(s) ? "" : stateKey(s))
                      }
                    >
                      <StatusIcon state={s} />
                      {s.name}
                    </button>
                  ))}
                </div>
              </div>
              <label className="grid gap-1.25">
                Priority
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                >
                  <option value="">All priorities</option>
                  <option value="1">Urgent</option>
                  <option value="2">High</option>
                  <option value="3">Medium</option>
                  <option value="4">Low</option>
                </select>
              </label>
              <label className="grid gap-1.25">
                Team
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                >
                  <option value="">All teams</option>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Project
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                >
                  <option value="">All projects</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Cycle
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={cycleOnly ? "current" : ""}
                  onChange={(e) => setCycleOnly(Boolean(e.target.value))}
                >
                  <option value="">Any cycle</option>
                  <option value="current">Has cycle</option>
                </select>
              </label>
              <label className="grid gap-1.25">
                Label
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={labelId}
                  onChange={(e) => setLabelId(e.target.value)}
                >
                  <option value="">All labels</option>
                  {availableLabels.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Assignee
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={assigneeId}
                  onChange={(e) => setAssigneeId(e.target.value)}
                >
                  <option value="">Any assignee</option>
                  {availableUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.name || u.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Creator
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={creatorId}
                  onChange={(e) => setCreatorId(e.target.value)}
                >
                  <option value="">Any creator</option>
                  {availableUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.name || u.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Subscriber
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={subscriberId}
                  onChange={(e) => setSubscriberId(e.target.value)}
                >
                  <option value="">Any subscriber</option>
                  {availableUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.name || u.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Date field
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={dateField}
                  onChange={(e) => setDateField(e.target.value)}
                >
                  <option value="">Any date</option>
                  <option value="createdAt">Created date</option>
                  <option value="updatedAt">Updated date</option>
                  <option value="dueDate">Due date</option>
                </select>
              </label>
              {dateField && (
                <>
                  <label className="grid gap-1.25">
                    From
                    <input
                      className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 border shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                      type="text"
                      inputMode="numeric"
                      placeholder="YYYY-MM-DD"
                      pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}"
                      value={dateFrom}
                      onChange={(e) =>
                        setDateFrom(
                          e.target.value.replace(/[^0-9-]/g, "").slice(0, 10),
                        )
                      }
                    />
                  </label>
                  <label className="grid gap-1.25">
                    Through
                    <input
                      className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 border shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                      type="text"
                      inputMode="numeric"
                      placeholder="YYYY-MM-DD"
                      pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}"
                      value={dateTo}
                      onChange={(e) =>
                        setDateTo(
                          e.target.value.replace(/[^0-9-]/g, "").slice(0, 10),
                        )
                      }
                    />
                  </label>
                </>
              )}
              <label className="grid gap-1.25">
                Content
                <input
                  className="w-full pt-1.5 pr-1.5 pb-1.5 pl-1.5 rounded-md bg-panel text-foreground max-md:text-base border-line placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 border shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  type="search"
                  value={textQuery}
                  onChange={(e) => setTextQuery(e.target.value)}
                  placeholder="Title or ID"
                />
              </label>
              <button
                className="justify-self-start pt-1 pr-0 pb-1 pl-0 bg-transparent text-[#4f5d9d] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                onClick={clearMyFilters}
              >
                Clear filters
              </button>
            </PopoverContent>
          </UIPopover>
          <UIPopover
            open={displayOpen}
            onOpenChange={(open) => {
              setDisplayOpen(open);
              if (open) setFilterOpen(false);
            }}
          >
            <PopoverTrigger asChild>
              <button
                className="inline-flex size-7 flex-none items-center justify-center gap-1.5 rounded-full border border-line bg-panel  text-sm text-foreground hover:bg-subtle aria-expanded:bg-accent-soft aria-expanded:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11"
                aria-label="Display options"
                aria-expanded={displayOpen}
              >
                <Settings size={16} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              side="bottom"
              sideOffset={4}
              className="z-50 grid w-[min(245px,calc(100vw-35px))] max-w-full max-h-[calc(100dvh-110px)] content-start gap-2.5 overflow-y-auto overscroll-contain rounded-[9px] border-line bg-panel p-3 text-[12px] text-foreground shadow-[0_12px_32px_#0002]"
              data-my-issue-popover=""
              aria-label="Display options"
              onCloseAutoFocus={(event) => event.preventDefault()}
              onEscapeKeyDown={() =>
                toolbarRef.current
                  ?.querySelector<HTMLButtonElement>(
                    '[aria-label="Display options"]',
                  )
                  ?.focus()
              }
              ref={displayPanelRef}
            >
              <label className="grid gap-1.25">
                Grouping
                <select
                  className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={grouping}
                  onChange={(e) => setGrouping(e.target.value)}
                  disabled={tab !== "Assigned"}
                >
                  <option>Focus</option>
                  <option>Status</option>
                  <option>Project</option>
                  <option>Priority</option>
                  <option>Cycle</option>
                  <option>Label</option>
                  <option>Team</option>
                </select>
              </label>
              {tab === "Assigned" && (
                <label className="grid gap-1.25">
                  Completed tasks
                  <select
                    className="w-full bg-panel text-foreground max-md:text-base border border-line rounded-md pt-2 pr-2.5 pb-2 pl-2.5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    value={completedRange}
                    onChange={(e) =>
                      setCompletedRange(e.target.value as CompletedRange)
                    }
                  >
                    <option value="none">None</option>
                    <option value="week">Past week</option>
                    <option value="month">Past month</option>
                    <option value="all">All</option>
                  </select>
                </label>
              )}
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showProject}
                  onChange={(e) => setShowProject(e.target.checked)}
                />{" "}
                Project
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showDate}
                  onChange={(e) => setShowDate(e.target.checked)}
                />{" "}
                Created date
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showAssignee}
                  onChange={(e) => setShowAssignee(e.target.checked)}
                />{" "}
                Assignee
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showPriority}
                  onChange={(e) => setShowPriority(e.target.checked)}
                />{" "}
                Priority
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showStatus}
                  onChange={(e) => setShowStatus(e.target.checked)}
                />{" "}
                Status
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showLabels}
                  onChange={(e) => setShowLabels(e.target.checked)}
                />{" "}
                Labels
              </label>
              <label className="grid gap-1.25">
                <input
                  className="max-md:text-base bg-panel border-line text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 accent-accent border shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showDue}
                  onChange={(e) => setShowDue(e.target.checked)}
                />{" "}
                Due date
              </label>
              <small className="text-[#666]">
                {tab === "Assigned"
                  ? "Group assigned tasks by focus or status."
                  : "This view uses Linear’s flat or date-grouped layout."}
              </small>
            </PopoverContent>
          </UIPopover>
        </div>
      </div>

      {(status ||
        priority ||
        teamId ||
        projectId ||
        cycleOnly ||
        textQuery ||
        labelId ||
        assigneeId ||
        creatorId ||
        subscriberId ||
        dateField) && (
        <p
          className="mt-0 mr-0 mb-0 ml-0 pt-1.5 pr-3 pb-1.5 pl-3 text-[12px] text-muted"
          data-my-filter-summary=""
        >
          Showing {visible.length} of {scoped.length} tasks{" "}
          <button
            className="justify-self-start pt-1 pr-0 pb-1 pl-0 bg-transparent text-[#4f5d9d] ml-2 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            onClick={clearMyFilters}
          >
            Clear filters
          </button>
        </p>
      )}
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain"
        data-my-issues-list=""
      >
        {layout === "Board" ? (
          <IssueBoard
            issues={visible}
            states={states}
            teams={teams}
            onSelect={onOpenIssue}
            onNew={onNew}
            onMoved={onMoved}
            onUpdate={onUpdate}
          />
        ) : (
          <div className="my-issue-groups shrink-0" ref={listRef}>
            {buckets.map((g) => {
              const open = !collapsed.includes(g.name);
              const n = open ? g.items.length : 0;
              const rowsTop = y + (g.name ? headH : 0);
              const [start, end] = windowed
                ? visibleRange(win, rowsTop, rowH, n)
                : [0, n];
              y = rowsTop + n * rowH;
              /* Parent then children, so a sub-task is never rendered above the task that
                 created it. Activity rows are events, not a hierarchy, and stay flat. */
              const treeRows =
                tab === "Activity" || !open
                  ? []
                  : orderTree(g.items, treeCollapsed);
              const items =
                tab === "Activity"
                  ? (start > 0 || end < n
                      ? g.items.slice(start, end)
                      : g.items
                    ).map((issue, index) => ({
                      issue,
                      index,
                      depth: 0,
                      isLast: true,
                      openRails: [] as number[],
                      childCount: 0,
                      doneCount: 0,
                      parentOutside: false,
                    }))
                  : treeRows.slice(start, end).map((row, index) => ({
                      issue: row.node,
                      index,
                      depth: row.depth,
                      isLast: row.isLast,
                      openRails: row.openRails,
                      childCount: row.childCount,
                      doneCount: row.doneCount,
                      parentOutside: row.parentOutside,
                    }));
              const dayEvents =
                tab === "Activity"
                  ? (eventsByDate.get(g.name) || []).slice(start, end)
                  : [];
              return (
                <section
                  className="mt-0 mr-0 mb-0 ml-0"
                  data-my-issue-group=""
                  key={g.name || "flat"}
                >
                  {g.name && (
                    <header className="flex items-center gap-1.75 h-8.5 pt-0 pr-2 pb-0 pl-2 text-muted text-[12px] mt-0 mr-2.5 mb-0.5 ml-2.5 rounded-[7px] bg-subtle">
                      <button
                        className="grid place-items-center w-5.5 h-5.5 ml-0 rounded-md bg-transparent text-[#626269] hover:bg-subtle mt-0 mr-0 mb-0 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group"
                        aria-label={`${collapsed.includes(g.name) ? "Expand" : "Collapse"} ${g.name}`}
                        aria-expanded={!collapsed.includes(g.name)}
                        onClick={() =>
                          setCollapsed((old) =>
                            old.includes(g.name)
                              ? old.filter((x) => x !== g.name)
                              : [...old, g.name],
                          )
                        }
                      >
                        <ChevronDown
                          size={14}
                          className="group-aria-[expanded=false]:-rotate-90"
                        />
                      </button>
                      {g.state && <StatusIcon state={g.state} />}
                      <b className="text-foreground font-medium">{g.name}</b>
                      <span className="text-muted text-[11px]">
                        {g.items.length}
                      </span>
                      {tab === "Assigned" && (
                        <button
                          className="grid place-items-center w-5.5 h-5.5 ml-0 rounded-md bg-transparent text-[#626269] hover:bg-subtle mt-0 mr-0 mb-0 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                          aria-label={`Create task in ${g.name}`}
                          onClick={() => onNew(g.state?.id)}
                        >
                          <Plus size={14} />
                        </button>
                      )}
                    </header>
                  )}
                  {start > 0 && (
                    <div aria-hidden="true" style={{ height: start * rowH }} />
                  )}
                  {items.map(
                    ({
                      issue: i,
                      index,
                      depth,
                      isLast,
                      openRails,
                      childCount,
                      doneCount,
                      parentOutside,
                    }) => (
                      <div
                        className="relative flex w-full min-w-0 min-h-9 items-center gap-2 bg-transparent py-0 pr-6 text-left text-foreground hover:bg-subtle cursor-pointer max-md:gap-1.5 shell-phone:pr-3 [@media_(max-width:650px)]:gap-1.5"
                        style={{
                          paddingLeft: ROW_INSET + depth * SIBLING_STEP,
                        }}
                        data-my-issue-row=""
                        data-depth={depth}
                        role="presentation"
                        key={
                          tab === "Activity"
                            ? `${g.name}-${dayEvents[index]?.id || index}`
                            : i.id
                        }
                        onClick={() => onOpenIssue(i)}
                      >
                        {/* Ancestry rails: one 2px line per open ancestor, then this row's own
                          connector — a T for a middle child, a rounded corner for the last. */}
                        {openRails.map((column) => (
                          <span
                            key={`rail-${column}`}
                            aria-hidden="true"
                            className="absolute top-0 bottom-0 w-0.5 bg-line"
                            style={{ left: railLeft(column) }}
                            data-tree-rail=""
                          />
                        ))}
                        {depth > 0 && isLast && (
                          <span
                            aria-hidden="true"
                            className="absolute w-2.5 border-l-2 border-b-2 border-line rounded-bl-[6px]"
                            style={{
                              left: railLeft(depth - 1),
                              top: 0,
                              height: 18,
                            }}
                            data-tree-rail="last"
                          />
                        )}
                        {depth > 0 && !isLast && (
                          <>
                            <span
                              aria-hidden="true"
                              className="absolute top-0 bottom-0 w-0.5 bg-line"
                              style={{ left: railLeft(depth - 1) }}
                              data-tree-rail="through"
                            />
                            <span
                              aria-hidden="true"
                              className="absolute h-0.5 w-2.5 bg-line"
                              style={{ left: railLeft(depth - 1), top: 18 }}
                            />
                          </>
                        )}
                        {/* Reserved 16px so every column lines up whether or not a row has children. */}
                        <span className="relative grid place-items-center w-4 h-4 flex-none">
                          {childCount > 0 && (
                            <button
                              type="button"
                              aria-expanded={!treeCollapsed[i.id]}
                              aria-label={`${treeCollapsed[i.id] ? "Expand" : "Collapse"} ${childCount} sub-tasks of ${i.identifier}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setTreeCollapsed((old) => ({
                                  ...old,
                                  [i.id]: !old[i.id],
                                }));
                              }}
                              className="grid place-items-center w-4 h-4 rounded bg-transparent text-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent"
                              data-tree-toggle=""
                            >
                              <ChevronDown
                                size={12}
                                className={
                                  treeCollapsed[i.id] ? "-rotate-90" : ""
                                }
                              />
                            </button>
                          )}
                        </span>
                        {showPriority && (
                          <span
                            className="text-muted text-[13px] data-[p=1]:text-danger"
                            data-my-issue-priority=""
                            data-p={i.priority}
                          >
                            ≡
                          </span>
                        )}
                        <code className="shrink-0 whitespace-nowrap text-muted text-[12px] font-[inherit]">
                          {i.identifier}
                        </code>
                        {i.decisionNeeded ? (
                          <span
                            className="inline-grid place-items-center w-3.75 h-3.75 ml-1.5 rounded-[50%] text-[10px] font-bold leading-none cursor-default bg-[#fdf3cf] text-[#5b4a0f] align-middle border border-amber-400"
                            data-my-issue-decision=""
                            role="img"
                            aria-label="Needs a decision"
                            title="Needs a decision"
                          >
                            ?
                          </span>
                        ) : null}
                        {showStatus && (
                          <span
                            className="text-[12px] text-[#89948d] data-[status=started]:text-foreground"
                            data-my-issue-status=""
                            data-status={i.state?.type}
                          >
                            <StatusPicker
                              compact
                              issue={i}
                              teams={teams}
                              icon={<StateGlyph state={i.state} />}
                              onChange={(stateId) => {
                                const st = teamStates(i)?.find(
                                  (x) => x.id === stateId,
                                );
                                if (!st) throw Error("Status not found");
                                return onMoved(i.id, st);
                              }}
                            />
                          </span>
                        )}
                        {/* A sub-task whose parent sits in another status section cannot nest
                          there, so it carries the parent identifier instead of reading as an
                          orphan. The board does the same. */}
                        {parentOutside && i.parent?.identifier ? (
                          <span
                            className="shrink-0 rounded border border-line py-0 px-1 text-[10px] text-muted shell-phone:hidden"
                            data-my-issue-parent=""
                            title={i.parent.title}
                          >
                            ↳{i.parent.identifier}
                          </span>
                        ) : null}
                        <button
                          type="button"
                          className="overflow-hidden text-ellipsis whitespace-nowrap text-[12px] flex-1 min-w-0 [background:none] border-0 pt-0 pr-0 pb-0 pl-0 mt-0 mr-0 mb-0 ml-0 text-inherit text-left cursor-pointer block font-[inherit] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                          data-my-issue-title=""
                          data-my-issue-title-open=""
                          aria-label={`Open ${i.identifier}: ${i.title}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenIssue(i);
                          }}
                        >
                          {i.title}
                          {tab === "Activity" && (
                            <small
                              className="text-[11px] font-normal text-[#777]"
                              data-my-activity-kind=""
                            >
                              {" "}
                              · {dayEvents[index]?.eventType || "event"}
                            </small>
                          )}
                        </button>
                        {childCount > 0 && (
                          <SubIssuePill done={doneCount} total={childCount} />
                        )}
                        {showLabels && i.labels?.nodes.length ? (
                          <span
                            className="max-w-25 overflow-hidden text-ellipsis whitespace-nowrap text-[#777] text-[11px] max-md:hidden"
                            data-my-issue-labels=""
                            title={i.labels.nodes.map((l) => l.name).join(", ")}
                          >
                            {i.labels.nodes.map((l) => l.name).join(", ")}
                          </span>
                        ) : null}
                        {showAssignee && (
                          <span
                            className={
                              i.assignee
                                ? "grid place-items-center w-5.5 h-5.5 flex-none"
                                : "grid place-items-center w-5.5 h-5.5 rounded-[50%] bg-[#e5e5e7] text-[#46464b] text-[10px] flex-none"
                            }
                            data-my-issue-assignee=""
                            title={i.assignee?.name || "Unassigned"}
                          >
                            {i.assignee ? (
                              <UserBlobatar user={i.assignee} />
                            ) : (
                              "·"
                            )}
                          </span>
                        )}
                        {showDue && i.dueDate && (
                          <span
                            className="whitespace-nowrap text-[#ad5757] text-[11px] max-md:hidden"
                            data-my-issue-due=""
                          >
                            {new Date(i.dueDate).toLocaleDateString(undefined, {
                              month: "short",
                              day: "numeric",
                            })}
                          </span>
                        )}
                        {showProject && (
                          <span
                            className="overflow-hidden text-ellipsis whitespace-nowrap text-muted text-[11px] max-md:hidden max-w-37.5"
                            data-my-issue-project=""
                          >
                            {i.project?.name || i.team?.name || ""}
                          </span>
                        )}
                        {showDate && (
                          <span
                            className="text-right whitespace-nowrap text-[11px] text-muted min-w-11.5 shell-phone:hidden"
                            data-my-issue-date=""
                          >
                            {tab === "Activity"
                              ? new Date(
                                  dayEvents[index]?.createdAt || "",
                                ).toLocaleDateString(undefined, {
                                  month: "short",
                                  day: "numeric",
                                })
                              : i.createdAt
                                ? new Date(i.createdAt).toLocaleDateString(
                                    undefined,
                                    { month: "short", day: "numeric" },
                                  )
                                : ""}
                          </span>
                        )}
                      </div>
                    ),
                  )}
                  {end < n && (
                    <div
                      aria-hidden="true"
                      style={{ height: (n - end) * rowH }}
                    />
                  )}
                </section>
              );
            })}
          </div>
        )}
        {!visible.length && !activityLoading && (
          <section
            className="flex flex-col items-center gap-3.5 min-h-75 justify-center text-[#65656a] text-[13px] [@media_(max-width:650px)]:min-h-[calc(100dvh-280px)]"
            data-my-issues-empty=""
            aria-labelledby="my-issues-empty-title"
          >
            <Inbox size={30} />
            <h2
              className="mt-0 mr-0 mb-0 ml-0 text-[16px] text-foreground"
              id="my-issues-empty-title"
            >
              {scoped.length ? "No matching tasks" : emptyCopy[0]}
            </h2>
            <p className="mt-0 mr-0 mb-0 ml-0">
              {scoped.length
                ? "Clear filters to see these tasks."
                : emptyCopy[1]}
            </p>
            {!scoped.length && tab !== "Activity" && (
              <button
                className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-[999px] bg-[#6567d8] pl-3.5 pr-3.5 pt-2 pb-2 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
                data-control-primary=""
                onClick={() => onNew()}
              >
                <Plus size={15} />
                Create task
              </button>
            )}
          </section>
        )}
        {activityLoading && tab === "Activity" && (
          <p
            role="status"
            className="pt-3 pr-6 pb-3 pl-6 text-[11px] text-muted"
            data-my-activity-note=""
          >
            Loading activity…
          </p>
        )}
        {activityError && tab === "Activity" && (
          <p
            role="alert"
            className="pt-2.5 pr-5 pb-2.5 pl-5 text-[12px] text-[#b42318]"
            data-my-issue-page-error=""
          >
            {activityError}{" "}
            <button
              onClick={onLoadActivity}
              className="focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            >
              Retry
            </button>
          </p>
        )}
        {tab === "Activity" && (
          <div
            ref={loadMoreRef}
            className="my-issue-load-sentinel"
            aria-hidden="true"
          />
        )}
        {activityHasMore && tab === "Activity" && (
          <button
            className="block mt-4 mr-auto mb-4 ml-auto pt-2 pr-3.5 pb-2 pl-3.5 rounded-md bg-white text-[12px] text-[#333] disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.6]"
            data-my-issue-load-more=""
            onClick={onLoadActivity}
            disabled={activityLoading}
          >
            {activityLoading ? "Loading…" : "Load more activity"}
          </button>
        )}
        {needStatus === "loading" && (
          <p
            role="status"
            className="pt-3 pr-6 pb-3 pl-6 text-[11px] text-muted"
            data-my-activity-note=""
          >
            Loading older closed tasks…
          </p>
        )}
        {need && needStatus === "error" && (
          <p
            role="alert"
            className="pt-2.5 pr-5 pb-2.5 pl-5 text-[12px] text-[#b42318]"
            data-my-issue-page-error=""
          >
            Couldn’t load older closed tasks: {older?.error}{" "}
            <button
              onClick={() => onLoadOlder?.(true)}
              className="focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            >
              Retry
            </button>
          </p>
        )}
        {tab === "Activity" && (
          <p
            className="pt-3 pr-6 pb-3 pl-6 text-[11px] text-muted"
            data-my-activity-note=""
          >
            Activity is recorded from this server’s event-log migration onward;
            earlier changes are unavailable.
          </p>
        )}
      </div>
    </div>
  );
}

const KANBAN_PAGE = 60,
  KANBAN_STEP = 200;
/* Material Symbols outlined `person`, used for an issue with no assignee. */
function MaterialPersonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 12q-1.65 0-2.825-1.175T8 8q0-1.65 1.175-2.825T12 4q1.65 0 2.825 1.175T16 8q0 1.65-1.175 2.825T12 12Zm-8 8v-2.8q0-.85.438-1.562T5.6 14.55q1.55-.775 3.15-1.162T12 13q1.65 0 3.25.388t3.15 1.162q.725.375 1.163 1.088T20 17.2V20H4Zm2-2h12v-.8q0-.275-.137-.5t-.363-.35q-1.35-.675-2.725-1.012T12 15q-1.4 0-2.775.338T6.5 16.35q-.225.125-.363.35T6 17.2V18Zm6-8q.825 0 1.413-.587T14 8q0-.825-.587-1.412T12 6q-.825 0-1.413.588T10 8q0 .825.588 1.413T12 10Zm0 8Z" />
    </svg>
  );
}
/* The board card keeps assignee editing on the card: the assigned avatar opens a
 * menu of team members, and the chosen change patches the shared store. */
function KanbanAssignee({
  issue,
  members,
  busy,
  onChange,
}: {
  issue: Issue;
  members: {
    id: string;
    name?: string;
    email?: string;
    avatarSeed?: string | null;
  }[];
  busy: boolean;
  onChange: (assigneeId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const assign = (id: string | null) => {
    setOpen(false);
    onChange(id);
  };
  return (
    <UIPopover open={open} onOpenChange={setOpen}>
      <div className="relative shrink-0">
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex items-center rounded-full focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group"
            aria-label={`Assignee of ${issue.identifier}: ${issue.assignee?.name || "Unassigned"}. Choose assignee`}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={`kanban-assignee-${issue.id}`}
            disabled={busy}
          >
            {issue.assignee ? (
              <AvatarGroup>
                <Avatar size="sm" title={issue.assignee.name || "Assigned"}>
                  <img
                    src={blobatarSrc(
                      issue.assignee.avatarSeed ||
                        issue.assignee.name ||
                        issue.assignee.id,
                    )}
                    alt=""
                    draggable={false}
                    className="aspect-square size-full scale-[1.2]"
                  />
                </Avatar>
              </AvatarGroup>
            ) : (
              <span className="border grid rounded-full border-dashed border-line text-muted">
                <MaterialPersonIcon />
              </span>
            )}
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          sideOffset={6}
          id={`kanban-assignee-${issue.id}`}
          role="menu"
          aria-label={`Assignee of ${issue.identifier}`}
          data-popover-panel
          className="border origin-top-left shadow-(--shadow) z-55 pt-1.5 pr-1.5 pb-1.5 pl-1.5 border-line rounded-[10px] bg-panel w-60 overflow-y-auto max-h-[var(--radix-popover-content-available-height)] animate-[workspace-popover-in_.14s_ease-out_both]"
        >
          <button
            type="button"
            role="menuitem"
            className="flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            onClick={() => assign(null)}
          >
            <span className="border grid rounded-full border-dashed border-line text-muted">
              <MaterialPersonIcon />
            </span>
            <span className="min-w-0 flex-1 truncate">Unassigned</span>
            {!issue.assignee && (
              <Check size={14} className="text-accent" aria-hidden="true" />
            )}
          </button>
          {members.map((m) => (
            <button
              type="button"
              role="menuitem"
              key={m.id}
              className="flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => assign(m.id)}
            >
              <Avatar size="sm">
                <img
                  src={blobatarSrc(m.avatarSeed || m.name || m.email || m.id)}
                  alt=""
                  draggable={false}
                  className="aspect-square size-full scale-[1.2]"
                />
              </Avatar>
              <span className="min-w-0 flex-1 truncate">
                {m.name || m.email || "Member"}
              </span>
              {issue.assignee?.id === m.id && (
                <Check size={14} className="text-accent" aria-hidden="true" />
              )}
            </button>
          ))}
        </PopoverContent>
      </div>
    </UIPopover>
  );
}
const PRIORITIES = ["No priority", "Urgent", "High", "Medium", "Low"];
function KanbanPriority({
  issue,
  busy,
  onChange,
}: {
  issue: Issue;
  busy: boolean;
  onChange: (priority: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const pick = (priority: number) => {
    setOpen(false);
    if (priority !== (issue.priority || 0)) onChange(priority);
  };
  const current = issue.priority || 0;
  return (
    <UIPopover open={open} onOpenChange={setOpen}>
      <div className="relative min-w-0 flex-1">
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 text-left focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group"
            aria-label={`Priority of ${issue.identifier}: ${PRIORITIES[current]}`}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={`kanban-priority-${issue.id}`}
            disabled={busy}
          >
            <span className="grid size-4 shrink-0 place-items-center">
              <span
                className="inline-block w-2 h-2 shrink-0 rounded-[50%] bg-room-muted data-[p=1]:bg-red-500 data-[p=2]:bg-orange-500 data-[p=3]:bg-yellow-500 data-[p=4]:bg-blue-400 data-[p=1]:bg-subtle data-[p=2]:bg-subtle data-[p=3]:bg-subtle data-[p=4]:bg-subtle"
                data-p={current}
                aria-hidden="true"
              />
            </span>
            <span className="min-w-0 flex-1 truncate">
              {PRIORITIES[current]}
            </span>
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          sideOffset={6}
          id={`kanban-priority-${issue.id}`}
          role="menu"
          aria-label={`Priority of ${issue.identifier}`}
          data-popover-panel
          className="border origin-top-left shadow-(--shadow) z-55 pt-1.5 pr-1.5 pb-1.5 pl-1.5 border-line rounded-[10px] bg-panel w-60 overflow-y-auto max-h-[var(--radix-popover-content-available-height)] animate-[workspace-popover-in_.14s_ease-out_both]"
        >
          {PRIORITIES.map((label, priority) => (
            <button
              type="button"
              role="menuitem"
              key={label}
              className="flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => pick(priority)}
            >
              <span className="grid size-4 shrink-0 place-items-center">
                <span
                  className="inline-block w-2 h-2 shrink-0 rounded-[50%] bg-room-muted data-[p=1]:bg-red-500 data-[p=2]:bg-orange-500 data-[p=3]:bg-yellow-500 data-[p=4]:bg-blue-400 data-[p=1]:bg-subtle data-[p=2]:bg-subtle data-[p=3]:bg-subtle data-[p=4]:bg-subtle"
                  data-p={priority}
                  aria-hidden="true"
                />
              </span>
              <span className="min-w-0 flex-1 truncate">{label}</span>
              {current === priority && (
                <Check size={14} className="text-accent" aria-hidden="true" />
              )}
            </button>
          ))}
        </PopoverContent>
      </div>
    </UIPopover>
  );
}
function KanbanCard({
  issue,
  teams,
  dragged,
  onDragStart,
  onDragEnd,
  onSelect,
  onUpdate,
  onMoveNext,
  canMoveNext,
  depth = 0,
  parentIdent,
  rollup,
  subtasks = [],
  compact = false,
}: {
  issue: Issue;
  teams: Team[];
  dragged: boolean;
  onDragStart: (e: React.DragEvent, task?: Issue) => void;
  onDragEnd: () => void;
  onSelect: (issue: Issue) => void;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  onMoveNext: (task?: Issue) => void;
  canMoveNext: boolean;
  /* Board hierarchy (HOJA-33): depth 0 for a root, 1+ for a sub-task whose parent is in this
   * column; parentIdent is set only when the parent is elsewhere, so the card is not mistaken
   * for an orphan; rollup counts the direct children rendered in this column. */
  depth?: number;
  parentIdent?: string;
  rollup?: { done: number; total: number };
  subtasks?: Issue[];
  compact?: boolean;
}) {
  const [subtasksOpen, setSubtasksOpen] = useState(false);
  const [stackMounted, setStackMounted] = useState(false);
  const reduceMotion = useReducedMotion();
  const stackRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const [stackPosition, setStackPosition] = useState({
    top: 16,
    left: 16,
    width: 280,
  });
  // An expanded parent opens its own task page; collapsed, it expands the stack.
  const openOrSelect = () => {
    if (subtasks.length && !stackMounted) toggleStack();
    else onSelect(issue);
  };
  const closeStack = () => {
    // Return to the current anchor, even if the underlying board moved.
    const rect = anchorRef.current?.getBoundingClientRect();
    if (rect)
      setStackPosition({
        top: rect.top,
        left: rect.left,
        width: Math.min(rect.width, window.innerWidth - 32),
      });
    setSubtasksOpen(false);
  };
  const toggleStack = () => {
    if (stackMounted) {
      closeStack();
      return;
    }
    const rect = anchorRef.current?.getBoundingClientRect();
    if (rect)
      setStackPosition({
        top: rect.top,
        left: rect.left,
        width: Math.min(rect.width, window.innerWidth - 32),
      });
    setStackMounted(true);
    setSubtasksOpen(true);
  };
  const [cardHeights, setCardHeights] = useState<number[]>([]);
  useLayoutEffect(() => {
    const root = stackRef.current;
    if (!root || !subtasks.length) return;
    const cards = Array.from(root.children).filter((el) =>
      el.hasAttribute("data-stack-layer"),
    );
    const measure = () =>
      setCardHeights((previous) => {
        const next = cards.map(
          (el) =>
            (
              (el.hasAttribute("data-kanban-card")
                ? el
                : el.firstElementChild) as HTMLElement
            ).offsetHeight,
        );
        return next.length === previous.length &&
          next.every((h, i) => h === previous[i])
          ? previous
          : next;
      });
    const observer = new ResizeObserver(measure);
    cards.forEach((card) =>
      observer.observe(
        card.hasAttribute("data-kanban-card") ? card : card.firstElementChild!,
      ),
    );
    measure();
    return () => observer.disconnect();
  }, [subtasks, stackMounted]);
  const spring = reduceMotion
    ? { duration: 0 }
    : { type: "spring" as const, stiffness: 200, damping: 30 };
  const parentHeight = cardHeights[0] || 210;
  const collapsedStackOffset = 8;
  const startsStatusGroup = (index: number) =>
    index === 0 ||
    (subtasks[index].state?.id || subtasks[index].state?.name) !==
      (subtasks[index - 1].state?.id || subtasks[index - 1].state?.name);
  const dividerHeight = 40;
  const stackHeight = subtasksOpen
    ? parentHeight +
      subtasks.reduce(
        (sum, _, index) =>
          sum +
          (cardHeights[index + 1] || parentHeight) +
          8 +
          (startsStatusGroup(index) ? dividerHeight : 0),
        0,
      )
    : parentHeight + Math.min(3, subtasks.length) * collapsedStackOffset;

  const members =
    teams.find((t) => t.id === issue.team?.id)?.members?.nodes || [];
  const due = (issue.dueDate || "").slice(0, 10);
  const overdue = dueIsOverdue(issue.dueDate);
  const [busy, setBusy] = useState(false);
  /* The board shows the failure banner; swallow the rejection here so an inline edit
   * never surfaces as an unhandled promise rejection. */
  const edit = async (input: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await onUpdate(issue.id, input);
    } catch {
      /* banner shown by Kanban */
    } finally {
      setBusy(false);
    }
  };
  const stack = (
    <motion.div
      ref={stackRef}
      className="relative isolate min-w-0 rounded-2xl"
      style={{
        perspective: "1000px",
        overflow: stackMounted || !subtasks.length ? "visible" : "clip",
      }}
      animate={subtasks.length ? { height: stackHeight } : undefined}
      transition={spring}
      data-kanban-stack={
        subtasks.length ? (subtasksOpen ? "open" : "closed") : undefined
      }
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- drag surface only; the keyboard path (alt+arrow) lives on the inner button */}
      <div
        className="relative z-10 border flex flex-col gap-2.25 text-left cursor-grab touch-none rounded-2xl shadow-sm border-line bg-panel p-4 hover:border-line active:cursor-grabbing focus-visible:relative focus-visible:z-1 data-dragging:opacity-45 [&[data-depth]:not([data-depth=0])]:relative [&[data-depth]:not([data-depth=0])::before]:absolute [&[data-depth]:not([data-depth=0])::before]:-left-2 [&[data-depth]:not([data-depth=0])::before]:top-1.5 [&[data-depth]:not([data-depth=0])::before]:bottom-1.5 [&[data-depth]:not([data-depth=0])::before]:w-0.5 [&[data-depth]:not([data-depth=0])::before]:rounded-sm [&[data-depth]:not([data-depth=0])::before]:bg-line focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 pointer-coarse:touch-pan-y pointer-coarse:cursor-auto [&[data-depth]:not([data-depth=0])]:before:content-['']"
        data-stack-layer=""
        data-kanban-card=""
        data-issue-id={issue.id}
        data-depth={depth}
        data-parent={parentIdent || undefined}
        data-dragging={dragged ? true : undefined}
        style={depth ? { marginLeft: depth * 18 } : undefined}
        draggable
        onDragStart={(e) => {
          if ((e.target as HTMLElement).closest("input,select,label,button")) {
            e.preventDefault();
            return;
          }
          onDragStart(e);
        }}
        onDragEnd={onDragEnd}
        onClick={(event) => {
          if (
            subtasks.length &&
            !(event.target as HTMLElement).closest(
              "button,input,label,select,a,[data-kanban-card-props]",
            )
          ) {
            openOrSelect();
          }
        }}
      >
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            className="flex min-w-0 text-left focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11"
            onClick={openOrSelect}
            aria-label={`Open ${issue.identifier}`}
          >
            <small className="flex min-w-0 items-center gap-1 text-muted text-xs">
              {parentIdent ? (
                <span
                  className="border text-xs text-muted border-line rounded-full  px-1.25  bg-neutral-50 inset-shadow-sm inset-shadow-bg-white"
                  title={`Parent ${parentIdent}`}
                >{`↳${parentIdent}`}</span>
              ) : null}
              {issue.identifier}
              {issue.decisionNeeded ? (
                <span
                  className="inline-grid place-items-center w-3.75 h-3.75 ml-1.5 rounded-[50%] text-[10px] font-bold leading-none cursor-default bg-[#fdf3cf] text-[#5b4a0f] align-middle border border-amber-400"
                  data-my-issue-decision=""
                  role="img"
                  aria-label="Needs a decision"
                  title="Needs a decision"
                >
                  ?
                </span>
              ) : null}
            </small>
          </button>
          <div className="flex shrink-0 items-center gap-2">
            {rollup && rollup.total > 0 && (
              <span
                className="flex shrink-0 items-center gap-1.5"
                title={`${rollup.done} of ${rollup.total} subtasks completed`}
              >
                <span
                  role="progressbar"
                  aria-label="Subtask completion"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round((rollup.done / rollup.total) * 100)}
                  className="h-1.5 w-16 overflow-hidden rounded-full bg-neutral-100"
                >
                  <span
                    className="block h-full rounded-full bg-[#10B981] transition-[width] motion-reduce:transition-none"
                    style={{ width: `${(rollup.done / rollup.total) * 100}%` }}
                  />
                </span>
                <span
                  className="border text-xs bg-neutral-100 text-neutral-500 border border-neutral-200 tabular-nums border-line rounded-full px-1.25"
                  aria-label={`${rollup.done} of ${rollup.total} sub-tasks complete`}
                >{`${rollup.done}/${rollup.total}`}</span>
              </span>
            )}
            <KanbanAssignee
              issue={issue}
              members={members}
              busy={busy}
              onChange={(assigneeId) => void edit({ assigneeId })}
            />
          </div>
        </div>
        <button
          type="button"
          className="flex w-full flex-col gap-1.5 pt-0 pr-0 pb-0 pl-0 bg-transparent text-inherit text-left min-w-0 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          data-kanban-card-open=""
          onClick={openOrSelect}
          onKeyDown={(e) => {
            if (e.altKey && e.key === "ArrowRight") {
              e.preventDefault();
              onMoveNext();
            }
          }}
          aria-expanded={subtasks.length ? subtasksOpen : undefined}
          aria-label={
            subtasks.length
              ? `${subtasksOpen ? "Collapse" : "Expand"} ${issue.identifier}: ${subtasks.length} subtasks`
              : `Open ${issue.identifier}: ${issue.title}`
          }
        >
          <strong
            className={
              compact
                ? "truncate text-xs font-medium text-muted"
                : "text-sm font-medium"
            }
          >
            {issue.title}
          </strong>
        </button>
        {!compact && (
          <div
            className="flex flex-col min-w-0 mt-1 text-[12px] text-muted [&>label]:gap-2 [&>div]:gap-2 [&>label]:h-7 [&>div]:h-7 [&>label]:px-2.25 [&>div]:px-2.25 [&>label]:rounded-lg [&>div]:rounded-lg **:data-status-picker:min-w-0 **:data-status-picker:self-center **:data-status-picker-trigger:w-auto **:data-status-picker-trigger:max-w-full **:data-status-picker-trigger:h-auto **:data-status-picker-trigger:p-0 **:data-status-picker-trigger:border-0 **:data-status-picker-trigger:bg-transparent **:data-status-picker-trigger:text-inherit **:data-status-picker-trigger:gap-2 [&>label:hover]:[transition:none] [&>div:hover]:[transition:none] [&>label:focus-within]:[transition:none]"
            data-kanban-card-props=""
            aria-busy={busy || undefined}
          >
            {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- pointer shortcut; the nested status button is the keyboard and screen-reader target. */}
            <div
              className="cursor-pointer flex items-center gap-2 h-7 min-w-0 pt-0 pr-2 pb-0 pl-2 rounded-lg hover:bg-subtle [transition:background-color_120ms_ease-out]"
              onClick={(e) => {
                if (!(e.target as HTMLElement).closest("button"))
                  e.currentTarget
                    .querySelector<HTMLButtonElement>(
                      "[data-status-picker-trigger]",
                    )
                    ?.click();
              }}
            >
              <StatusPicker
                compact
                showLabel
                iconClassName="size-4 shrink-0"
                issue={issue}
                teams={teams}
                disabled={busy}
                onChange={(stateId) => {
                  const st = teams
                    .find((t) => t.id === issue.team?.id)
                    ?.states.nodes.find((x) => x.id === stateId);
                  if (!st) throw Error("Status not found");
                  return onUpdate(issue.id, { stateId: st.id });
                }}
              />
            </div>
            <label
              className={
                (overdue ? "relative text-danger" : "relative") +
                " flex items-center gap-2 h-7 min-w-0 pt-0 pr-2.25 pb-0 pl-2.25 rounded-lg hover:bg-subtle focus-within:bg-subtle" +
                " hover:bg-subtle focus-within:bg-subtle" +
                " [transition:background-color_120ms_ease-out]"
              }
              title={due ? `Due ${due}` : "Set due date"}
            >
              <CalendarDays
                aria-hidden="true"
                size={16}
                className="size-4 shrink-0"
              />
              <span className="truncate">
                {due
                  ? new Date(due).toLocaleDateString(undefined, {
                      month: "short",
                      day: "numeric",
                    })
                  : "Set due date"}
              </span>
              <input
                type="date"
                value={due}
                disabled={busy}
                aria-label={`Due date of ${issue.identifier}`}
                className="absolute inset-0 h-full w-full cursor-pointer opacity-0 max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                onClick={(e) => {
                  try {
                    e.currentTarget.showPicker?.();
                  } catch {
                    /* the native picker is unavailable in this browser */
                  }
                }}
                onChange={(e) => void edit({ dueDate: e.target.value || null })}
              />
            </label>
            {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- pointer shortcut; the nested priority button is the keyboard and screen-reader target. */}
            <div
              className="cursor-pointer flex items-center gap-2 h-7 min-w-0 pt-0 pr-2.25 pb-0 pl-2.25 rounded-lg hover:bg-subtle [transition:background-color_120ms_ease-out]"
              onClick={(e) => {
                if (!(e.target as HTMLElement).closest("button"))
                  e.currentTarget
                    .querySelector<HTMLButtonElement>("button")
                    ?.click();
              }}
            >
              <KanbanPriority
                issue={issue}
                busy={busy}
                onChange={(priority) => void edit({ priority })}
              />
            </div>
          </div>
        )}
      </div>
      {subtasks.map((child, index) => {
        const beforeDivider =
          parentHeight +
          8 +
          subtasks
            .slice(0, index)
            .reduce(
              (sum, _, i) =>
                sum +
                (cardHeights[i + 1] || parentHeight) +
                8 +
                (startsStatusGroup(i) ? dividerHeight : 0),
              0,
            );
        const offset =
          beforeDivider + (startsStatusGroup(index) ? dividerHeight : 0);
        return (
          <React.Fragment key={child.id}>
            {startsStatusGroup(index) && (
              <motion.div
                className="absolute top-0 flex h-8 items-center"
                data-subtask-status-divider=""
                style={{
                  left: "50%",
                  width: "max(100%, 33.333vw)",
                  x: "-50%",
                  pointerEvents: "none",
                }}
                initial={{ opacity: 0 }}
                animate={{
                  y: subtasksOpen ? beforeDivider : 0,
                  opacity: subtasksOpen ? 1 : 0,
                }}
                transition={spring}
                aria-hidden={!subtasksOpen}
              >
                <span
                  className={`relative isolate inline-flex h-7 w-36 shrink-0 items-center pl-3 pr-8 text-[11px] font-medium ${child.state?.type === "completed" ? "text-[#5151C1]" : child.state?.type === "started" ? "text-yellow-950" : "text-muted"}`}
                >
                  <svg
                    aria-hidden="true"
                    className={`absolute inset-0 -z-1 h-full w-full ${child.state?.type === "completed" ? "text-[#ECEEFF]" : child.state?.type === "started" ? "text-yellow-300" : "text-neutral-100"}`}
                    viewBox="0 0 144 28"
                  >
                    <path
                      d="M5 1 H138 Q143 1 140 5 L120 25 Q118 27 115 27 H5 Q1 27 1 23 V5 Q1 1 5 1 Z"
                      fill="currentColor"
                      stroke={
                        child.state?.type === "completed"
                          ? "#5151C1"
                          : "var(--line)"
                      }
                      strokeWidth="1"
                      strokeLinejoin="round"
                    />
                  </svg>
                  {child.state?.name || "No status"}
                </span>
                <span
                  aria-hidden="true"
                  className={`flex-1 border-t-2 border-dashed ${child.state?.type === "completed" ? "border-[#ECEEFF]" : "border-line"}`}
                />
              </motion.div>
            )}
            <motion.div
              data-stack-layer=""
              className="absolute inset-x-0 top-0 rounded-2xl"
              style={{
                zIndex: subtasksOpen ? 1 : -index - 1,
                pointerEvents: subtasksOpen ? "auto" : "none",
                height: subtasksOpen ? undefined : parentHeight,
                overflow: subtasksOpen ? "visible" : "hidden",
                background: subtasksOpen ? "transparent" : "var(--panel)",
                boxShadow: subtasksOpen
                  ? undefined
                  : "inset 0 0 0 1px var(--line)",
              }}
              inert={!subtasksOpen}
              aria-hidden={!subtasksOpen}
              initial={
                subtasksOpen && !reduceMotion
                  ? {
                      y: Math.min(index + 1, 3) * collapsedStackOffset,
                      scaleX: 1 - Math.min(index + 1, 3) * 0.04,
                      opacity: index < 3 ? 1 : 0,
                    }
                  : false
              }
              animate={{
                y: subtasksOpen
                  ? offset
                  : Math.min(index + 1, 3) * collapsedStackOffset,
                z: 0,
                scaleX: subtasksOpen ? 1 : 1 - Math.min(index + 1, 3) * 0.04,
                opacity: subtasksOpen || index < 3 ? 1 : 0,
              }}
              transition={spring}
            >
              <div style={{ visibility: subtasksOpen ? "visible" : "hidden" }}>
                <KanbanCard
                  issue={child}
                  teams={teams}
                  dragged={false}
                  onDragStart={(e) => onDragStart(e, child)}
                  onDragEnd={onDragEnd}
                  onSelect={onSelect}
                  onUpdate={onUpdate}
                  onMoveNext={() => onMoveNext(child)}
                  canMoveNext={canMoveNext}
                />
              </div>
            </motion.div>
          </React.Fragment>
        );
      })}
    </motion.div>
  );
  return (
    <div
      ref={anchorRef}
      className="relative min-w-0 shrink-0"
      style={
        stackMounted
          ? {
              paddingBottom:
                Math.min(3, subtasks.length) * collapsedStackOffset,
            }
          : undefined
      }
    >
      {stackMounted && (
        <div
          aria-hidden="true"
          data-kanban-stack-placeholder=""
          className="rounded-2xl border-2 border-dashed border-line bg-neutral-100"
          style={{ height: parentHeight }}
        />
      )}
      {!stackMounted && stack}
      <StackDialog.Root
        open={stackMounted}
        onOpenChange={(open) => {
          if (!open) closeStack();
        }}
      >
        <StackDialog.Portal>
          <StackDialog.Overlay asChild>
            <motion.div
              className="fixed inset-0 z-100 bg-black/30"
              initial={{ opacity: 0 }}
              animate={{ opacity: subtasksOpen ? 1 : 0 }}
              transition={{ duration: reduceMotion ? 0 : 0.25 }}
            />
          </StackDialog.Overlay>
          <StackDialog.Content
            aria-describedby={undefined}
            className="fixed inset-0 z-101 overflow-y-auto overscroll-contain outline-none pointer-events-none"
            onPointerDown={(event) => {
              if (event.target === event.currentTarget) closeStack();
            }}
          >
            <StackDialog.Title className="sr-only">
              {issue.identifier} subtasks
            </StackDialog.Title>
            <motion.div
              className="absolute pointer-events-auto pb-6"
              style={{ width: stackPosition.width }}
              initial={{
                top: stackPosition.top,
                left: stackPosition.left,
                width: stackPosition.width,
              }}
              animate={
                subtasksOpen
                  ? (() => {
                      const expandedWidth = Math.min(
                        Math.max(stackPosition.width, 460),
                        window.innerWidth - 32,
                      );
                      return {
                        top: Math.max(48, window.innerHeight / 5),
                        left: (window.innerWidth - expandedWidth) / 2,
                        width: expandedWidth,
                      };
                    })()
                  : {
                      top: stackPosition.top,
                      left: stackPosition.left,
                      width: stackPosition.width,
                    }
              }
              transition={spring}
              onAnimationComplete={() => {
                if (!subtasksOpen) setStackMounted(false);
              }}
            >
              <StackDialog.Close asChild>
                <motion.button
                  type="button"
                  aria-label="Collapse subtask stack"
                  className="absolute -top-9 right-0 rounded-lg border border-line bg-panel px-3 py-1 text-[12px] text-muted focus-visible:outline-2 focus-visible:outline-accent"
                  initial={{ opacity: 0, y: 4 }}
                  animate={{
                    opacity: subtasksOpen ? 1 : 0,
                    y: subtasksOpen ? 0 : 4,
                  }}
                  transition={{ duration: reduceMotion ? 0 : 0.2 }}
                  style={{ pointerEvents: subtasksOpen ? "auto" : "none" }}
                >
                  Collapse subtasks ×
                </motion.button>
              </StackDialog.Close>
              {stack}
            </motion.div>
          </StackDialog.Content>
        </StackDialog.Portal>
      </StackDialog.Root>
    </div>
  );
}
function KanbanScrollFade({ children }: { children: React.ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ top: 0, bottom: 0 });
  useLayoutEffect(() => {
    const root = rootRef.current;
    const viewport = root?.querySelector<HTMLElement>("[data-kanban-cards]");
    if (!viewport) return;
    const strength = (distance: number) => {
      const t = Math.max(0, Math.min(distance / 32, 1));
      return t * t * (3 - 2 * t);
    };
    const update = () => {
      const max = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
      const offset = Math.max(0, Math.min(viewport.scrollTop, max));
      const top = strength(offset),
        bottom = strength(max - offset);
      setEdges((previous) =>
        previous.top === top && previous.bottom === bottom
          ? previous
          : { top, bottom },
      );
    };
    const resize = new ResizeObserver(update);
    const observe = () => {
      resize.disconnect();
      resize.observe(viewport);
      Array.from(viewport.children).forEach((child) => resize.observe(child));
      update();
    };
    const mutations = new MutationObserver(observe);
    mutations.observe(viewport, { childList: true, subtree: true });
    viewport.addEventListener("scroll", update, { passive: true });
    observe();
    return () => {
      viewport.removeEventListener("scroll", update);
      resize.disconnect();
      mutations.disconnect();
    };
  }, []);
  return (
    <div
      ref={rootRef}
      className="relative isolate flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      {children}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 z-20 h-8"
        style={{
          opacity: edges.top,
          background:
            "radial-gradient(ellipse at 50% 0%, var(--panel) 0%, transparent 100%)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 z-20 h-8"
        style={{
          opacity: edges.bottom,
          background:
            "radial-gradient(ellipse at 50% 100%, var(--panel) 0%, transparent 100%)",
        }}
      />
    </div>
  );
}

function Kanban({
  grouped,
  allIssues,
  onSelect,
  onNew,
  onMoved,
  onUpdate,
  teams = [],
}: {
  allIssues: Issue[];
  grouped: {
    state: { id: string; name: string; color: string; type: string };
    items: Issue[];
  }[];
  onSelect: (issue: Issue) => void;
  onNew: (stateId?: string) => void;
  onMoved: (
    issueId: string,
    state: { id: string; name: string; color: string; type: string },
  ) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  teams?: Team[];
}) {
  const columnsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const columns = columnsRef.current;
    if (!columns) return;
    const onWheel = (event: WheelEvent) => {
      if (
        !event.shiftKey ||
        event.ctrlKey ||
        columns.scrollWidth <= columns.clientWidth
      ) {
        return;
      }
      // Browsers may already translate Shift+wheel into deltaX.
      const delta = event.deltaX || event.deltaY;
      if (!delta) return;
      const scale =
        event.deltaMode === WheelEvent.DOM_DELTA_LINE
          ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? columns.clientWidth
            : 1;
      event.preventDefault();
      columns.scrollLeft += delta * scale;
    };
    // A non-passive listener lets us prevent vertical scrolling as we scroll sideways.
    columns.addEventListener("wheel", onWheel, { passive: false });
    return () => columns.removeEventListener("wheel", onWheel);
  }, []);
  const [dragged, setDragged] = useState("");
  const [error, setError] = useState("");
  const [dropLine, setDropLine] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);
  const showDropLine = (e: React.DragEvent<HTMLElement>) => {
    const cardsEl = (e.currentTarget as HTMLElement).querySelector<HTMLElement>(
      "[data-kanban-cards]",
    );
    if (!cardsEl) return;
    const cards = [
      ...cardsEl.querySelectorAll<HTMLElement>("[data-kanban-card]"),
    ].filter((card) => card.dataset.issueId !== dragged);
    const container = cardsEl.getBoundingClientRect(),
      first = cards[0],
      last = cards[cards.length - 1];
    let top: number | null = null;
    for (const card of cards) {
      const r = card.getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) {
        top = r.top - 5;
        break;
      }
    }
    if (top === null)
      top = last ? last.getBoundingClientRect().bottom + 5 : container.top + 10;
    const cardRect = first?.getBoundingClientRect();
    setDropLine({
      top,
      left: cardRect ? cardRect.left : container.left + 10,
      width: cardRect ? cardRect.width : Math.max(0, container.width - 20),
    });
  };
  // Columns mount their first cards and grow on scroll, so a 30k-issue board stays cheap.
  const [limits, setLimits] = useState<Record<string, number>>({});
  const grow = (id: string) =>
    setLimits((l) => ({ ...l, [id]: (l[id] || KANBAN_PAGE) + KANBAN_STEP }));
  const moveNext = async (issue: Issue, stateId: string) => {
    const idx = grouped.findIndex((g) => g.state.id === stateId),
      next = grouped[idx + 1];
    if (!next) return;
    try {
      await onMoved(issue.id, next.state);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const update = async (issueId: string, input: Record<string, unknown>) => {
    try {
      await onUpdate(issueId, input);
    } catch (err) {
      setError((err as Error).message);
      throw err;
    }
  };
  // Progress and stack contents must include completed children hidden by board filters.
  const subtasksByParent = useMemo(() => {
    const m = new Map<string, Issue[]>();
    for (const child of allIssues) {
      const parentId = child.parent?.id;
      if (!parentId || parentId === child.id) continue;
      const children = m.get(parentId) || [];
      children.push(child);
      m.set(parentId, children);
    }
    const statusRank = (child: Issue) => {
      if (child.state?.name.toLowerCase() === "todo") return 0;
      switch (child.state?.type) {
        case "unstarted":
          return 0;
        case "backlog":
          return 1;
        case "started":
          return 2;
        case "canceled":
          return 4;
        case "completed":
          return 5;
        default:
          return 3;
      }
    };
    // Creation uses <parent>-<letter slot>-<sibling ordinal> at each depth.
    // Compare the final ordinal numerically (2 before 10), not the slot letters.
    const siblingOrdinal = (child: Issue) =>
      Number(
        child.identifier.match(/(?:-|\/)(\d+)$/)?.[1] ??
          Number.MAX_SAFE_INTEGER,
      );
    for (const children of m.values())
      children.sort(
        (a, b) =>
          statusRank(a) - statusRank(b) ||
          (a.state?.name || "").localeCompare(b.state?.name || "") ||
          siblingOrdinal(a) - siblingOrdinal(b) ||
          a.identifier.localeCompare(b.identifier, undefined, {
            numeric: true,
          }),
      );
    return m;
  }, [allIssues]);
  const rollups = useMemo(
    () =>
      new Map(
        Array.from(subtasksByParent, ([id, children]) => [
          id,
          {
            total: children.length,
            done: children.filter((child) => child.state?.type === "completed")
              .length,
          },
        ]),
      ),
    [subtasksByParent],
  );
  return (
    <div className="kanban flex min-h-0 min-w-0 flex-1 flex-col w-full">
      <div
        className="grid grid-flow-col auto-cols-[minmax(260px,1fr)] gap-x-3 bg-bg flex-1 overflow-x-auto overflow-y-hidden min-w-0 w-full items-stretch min-h-0 [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-track]:bg-subtle [&::-webkit-scrollbar-thumb]:rounded-lg [&::-webkit-scrollbar-thumb]:bg-muted overscroll-x-contain focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 scrollbar-thin [scrollbar-color:var(--muted)_var(--subtle)] [&::-webkit-scrollbar-thumb]:border-[2px_solid_var(--subtle)]"
        data-kanban-columns=""
        ref={columnsRef}
      >
        {grouped.map((group) => (
          <section
            role="presentation"
            className={
              ` flex flex-col min-w-0 inset-shadow-neutral-50 shadow-md  inset-shadow-sm rounded-xl border border-neutral-200 bg-neutral-100 overflow-hidden ${dragged ? "" : ""}` +
              " flex flex-col min-h-0 max-h-full" +
              " data-drop-ready:bg-[color-mix(in_srgb,var(--accent-soft)_18%,transparent)]"
            }
            data-kanban-column=""
            data-drop-ready={dragged ? "" : undefined}
            key={group.state.id}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              showDropLine(e);
            }}
            onDragLeave={(e) => {
              if (
                !(e.currentTarget as HTMLElement).contains(
                  e.relatedTarget as Node,
                )
              )
                setDropLine(null);
            }}
            onDrop={async (e) => {
              e.preventDefault();
              const id = e.dataTransfer.getData("text/hoja-issue") || dragged;
              setDragged("");
              setDropLine(null);
              if (id && group.state.id !== "unknown") {
                try {
                  await onMoved(id, group.state);
                } catch (err) {
                  setError((err as Error).message);
                }
              }
            }}
          >
            <header className="flex items-center gap-2 h-12 pt-0 pr-3.5 pb-0 pl-3.5 text-muted text-[12px] flex-none basis-12">
              <span
                data-status-mark=""
                style={{ borderColor: group.state.color }}
                className="border-2"
              ></span>
              <b className="text-foreground">{group.state.name}</b>
              <span className="mr-auto">{group.items.length}</span>
              <button
                className="grid place-items-center w-6.25 h-6.25 rounded-full hover:inset-shadow-sm hover:inset-shadow-neutral-50 border-neutral-200 hover:border-[0.5px] hover:shadow-sm hover:shadow active:scale-[0.95]    bg-transparent text-muted  hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                aria-label={`Create task in ${group.state.name}`}
                onClick={() =>
                  onNew(
                    group.state.id === "unknown" ? undefined : group.state.id,
                  )
                }
              >
                <Plus size={15} />
              </button>
            </header>
            <KanbanScrollFade>
              <div
                className="flex flex-col gap-1 p-1 overflow-y-auto flex-auto min-h-0 overscroll-contain [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-track]:bg-subtle [&::-webkit-scrollbar-thumb]:rounded-lg [&::-webkit-scrollbar-thumb]:bg-muted max-h-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 scrollbar-thin [&::-webkit-scrollbar-thumb]:border-[2px_solid_var(--subtle)]"
                data-kanban-cards=""
                onScroll={
                  group.items.length > (limits[group.state.id] || KANBAN_PAGE)
                    ? (e) => {
                        const el = e.currentTarget;
                        if (
                          el.scrollTop + el.clientHeight >
                          el.scrollHeight - 800
                        )
                          grow(group.state.id);
                      }
                    : undefined
                }
              >
                {group.items.length === 0 && (
                  <p
                    className="mt-1 mr-0 mb-1 ml-0 pt-3 pr-3 pb-3 pl-3 rounded-lg text-muted text-[12px] text-center border  border-dashed border-neutral-300"
                    data-kanban-empty=""
                  >
                    No tasks in {group.state.name}.
                  </p>
                )}
                {orderTree(group.items, {})
                  .filter(
                    ({ node: i }) =>
                      !i.parent?.id ||
                      !grouped.some((g) =>
                        g.items.some(
                          (parent) =>
                            parent.id === i.parent?.id && parent.id !== i.id,
                        ),
                      ),
                  )
                  .slice(0, limits[group.state.id] || KANBAN_PAGE)
                  .map(({ node: issue, depth, parentOutside }) => (
                    <KanbanCard
                      key={issue.id}
                      issue={issue}
                      teams={teams}
                      dragged={dragged === issue.id}
                      depth={depth}
                      parentIdent={
                        parentOutside ? issue.parent?.identifier : undefined
                      }
                      rollup={rollups.get(issue.id)}
                      subtasks={subtasksByParent.get(issue.id)}
                      compact={group.state.type === "completed"}
                      onDragStart={(e, task = issue) => {
                        setDragged(task.id);
                        setDropLine(null);
                        e.dataTransfer.effectAllowed = "move";
                        e.dataTransfer.setData("text/hoja-issue", task.id);
                      }}
                      onDragEnd={() => {
                        setDragged("");
                        setDropLine(null);
                      }}
                      onSelect={onSelect}
                      onUpdate={update}
                      onMoveNext={(task = issue) =>
                        void moveNext(task, task.state?.id || group.state.id)
                      }
                      canMoveNext={Boolean(
                        grouped[
                          grouped.findIndex(
                            (g) => g.state.id === group.state.id,
                          ) + 1
                        ],
                      )}
                    />
                  ))}
                {group.items.length >
                  (limits[group.state.id] || KANBAN_PAGE) && (
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 rounded-md border border-line bg-panel px-3 py-2 text-sm text-inherit hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    onClick={() => grow(group.state.id)}
                  >
                    Show{" "}
                    {Math.min(
                      group.items.length -
                        (limits[group.state.id] || KANBAN_PAGE),
                      KANBAN_STEP,
                    )}{" "}
                    more of{" "}
                    {group.items.length -
                      (limits[group.state.id] || KANBAN_PAGE)}
                  </button>
                )}
              </div>
            </KanbanScrollFade>
          </section>
        ))}
      </div>
      {dropLine && (
        <div
          className="shadow-[0_0_0_2px_color-mix(in_srgb,var(--accent)_18%,transparent)] fixed z-60 h-0.5 rounded-[999px] bg-accent pointer-events-none"
          data-kanban-drop-indicator=""
          style={{
            top: dropLine.top,
            left: dropLine.left,
            width: dropLine.width,
          }}
          aria-hidden="true"
        />
      )}
      {error && (
        <div
          className="border shadow-(--shadow) sticky bottom-2 self-center flex gap-3 items-center pt-2.25 pr-3 pb-2.25 pl-3 bg-panel text-danger border-line rounded-[7px] text-[12px]"
          data-kanban-error=""
          role="alert"
        >
          {error}
          <button
            className="bg-transparent text-accent focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            onClick={() => setError("")}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
function Stat({
  label,
  value,
  trend,
}: {
  label: string;
  value: string;
  trend: string;
}) {
  return (
    <div className="grid gap-1.75 pt-4 pr-4 pb-4 pl-4 border border-line rounded-xl bg-panel shadow-[0_1px_2px_#1b1b1b08] [@media_(max-width:560px)]:pt-3 [@media_(max-width:560px)]:pr-3 [@media_(max-width:560px)]:pb-3 [@media_(max-width:560px)]:pl-3">
      <span className="text-muted text-[12px]">{label}</span>
      <strong className="text-[25px] tracking-tighter [@media_(max-width:560px)]:text-[21px]">
        {value}
      </strong>
      <small className="flex items-center gap-1 text-[#626269] text-[11px]">
        <Zap size={12} />
        {trend}
      </small>
    </div>
  );
}
function Placeholder({ view }: { view: string }) {
  return (
    <div className="grid place-items-center text-center mt-20 mr-auto mb-20 ml-auto max-w-105">
      <div
        className="grid place-items-center w-16 h-16 rounded-2xl bg-accent-soft text-accent"
        data-placeholder-icon=""
      >
        <LayoutDashboard size={28} />
      </div>
      <h2 className="mt-5 mr-0 mb-1.25 ml-0">{view} is ready for your team</h2>
      <p className="text-muted">
        This dashboard surface is connected to your self-hosted workspace. More{" "}
        {view.toLowerCase()} workflows are coming next.
      </p>
    </div>
  );
}

function InviteAcceptance({
  code,
  onDone,
}: {
  code: string;
  onDone: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState<{ name?: string } | null>(null);
  const [googleUrl, setGoogleUrl] = useState("");
  const [mode, setMode] = useState<
    "checking" | "google" | "password" | "failed"
  >("checking");
  /* One code, two ways to prove the address. A workspace that signs people in
   * with Google has no password to create, so the Google path comes first there;
   * an instance that still supports credentials keeps the form. */
  const accept = async (payload: Record<string, string>): Promise<boolean> => {
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/auth/invite/accept", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const b = await r.json();
      if (!r.ok) throw Error(b.error || "Invite code is invalid or expired");
      setAccepted({ name: b.organization?.name });
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let live = true;
    (async () => {
      let hosted = false,
        creds = true;
      try {
        const c = await fetch("/api/signup/config").then((r) =>
          r.ok ? r.json() : null,
        );
        hosted = Boolean(c?.hosted);
        creds = c?.credentialSignIn !== false;
      } catch {}
      if (!live) return;
      if (hosted)
        setGoogleUrl(
          `/auth/google/start?invite=${encodeURIComponent(code)}&return_to=%2F`,
        );
      /* Coming back from Google means a session already exists: accept with the
       * code alone instead of asking for a password the person never had. */
      /* Validate the code before asking for anything: an expired invitation should
       * say so on arrival, not after someone has chosen a password. */
      let valid = true;
      try {
        const probe = await fetch("/auth/invite/probe", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ code }),
        });
        /* Only an explicit "this code is dead" blocks the form. An outage, a 429 or
         * an older server without the route must not tell someone their invitation
         * expired. */
        if (probe.status === 404) {
          const b = await probe.json().catch(() => ({}));
          if (b?.valid === false) {
            valid = false;
            setError(
              "This invitation has expired or been replaced. Ask a workspace admin to resend it.",
            );
          }
        }
      } catch {}
      if (!live) return;
      if (!valid) {
        setMode("failed");
        return;
      }
      let signedIn = false;
      try {
        signedIn = (await fetch("/auth/me")).ok;
      } catch {}
      if (!live) return;
      if (signedIn) {
        const ok = await accept({ code });
        if (!live) return;
        if (!ok) setMode("failed");
        return;
      }
      setMode(!creds && hosted ? "google" : "password");
    })();
    return () => {
      live = false;
    };
  }, [code]);
  if (accepted)
    return (
      <main className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 sm:p-6 bg-bg text-foreground [@media_(max-width:_600px)]:pt-4 [@media_(max-width:_600px)]:pr-4 [@media_(max-width:_600px)]:pb-4 [@media_(max-width:_600px)]:pl-4">
        <section className="w-[min(100%,420px)] max-w-105 border border-line bg-panel pt-8 pr-8 pb-8 pl-8 sm:p-8 shadow-(--shadow) [@media_(max-width:_600px)]:pt-6.5 [@media_(max-width:_600px)]:pr-5.5 [@media_(max-width:_600px)]:pb-6.5 [@media_(max-width:_600px)]:pl-5.5">
          <div
            className="mb-6 flex items-center gap-2.5 text-[18px]"
            data-onboarding-brand=""
          >
            <img
              className="block w-5 h-5 object-contain"
              data-brand-mark=""
              src="/hoja-clover.8371400e.svg"
              alt=""
            />
            <span>Hoja</span>
          </div>
          <h1 className="mb-1.5 text-[22px] font-semibold leading-[1.3] tracking-[-0.02em] ">
            You're in{accepted.name ? `, ${accepted.name}` : ""}.
          </h1>
          <p className="mb-6 text-muted leading-normal">
            Your invitation is accepted and this account is now a member.
          </p>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[0.65]"
            data-control-primary=""
            onClick={onDone}
          >
            Open my workspace →
          </button>
        </section>
      </main>
    );
  if (mode === "checking")
    return (
      <main className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 sm:p-6 bg-bg text-foreground [@media_(max-width:_600px)]:pt-4 [@media_(max-width:_600px)]:pr-4 [@media_(max-width:_600px)]:pb-4 [@media_(max-width:_600px)]:pl-4">
        <section className="w-[min(100%,420px)] max-w-105 border border-line bg-panel pt-8 pr-8 pb-8 pl-8 sm:p-8 shadow-(--shadow) [@media_(max-width:_600px)]:pt-6.5 [@media_(max-width:_600px)]:pr-5.5 [@media_(max-width:_600px)]:pb-6.5 [@media_(max-width:_600px)]:pl-5.5">
          <p className="mb-6 text-muted leading-normal" role="status">
            Checking your invitation…
          </p>
        </section>
      </main>
    );
  if (mode === "failed")
    return (
      <main className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 sm:p-6 bg-bg text-foreground [@media_(max-width:_600px)]:pt-4 [@media_(max-width:_600px)]:pr-4 [@media_(max-width:_600px)]:pb-4 [@media_(max-width:_600px)]:pl-4">
        <section className="w-[min(100%,420px)] max-w-105 border border-line bg-panel pt-8 pr-8 pb-8 pl-8 sm:p-8 shadow-(--shadow) [@media_(max-width:_600px)]:pt-6.5 [@media_(max-width:_600px)]:pr-5.5 [@media_(max-width:_600px)]:pb-6.5 [@media_(max-width:_600px)]:pl-5.5">
          <div
            className="mb-6 flex items-center gap-2.5 text-[18px]"
            data-onboarding-brand=""
          >
            <img
              className="block w-5 h-5 object-contain"
              data-brand-mark=""
              src="/hoja-clover.8371400e.svg"
              alt=""
            />
            <span>Hoja</span>
          </div>
          <h1 className="mb-1.5 text-[22px] font-semibold leading-[1.3] tracking-[-0.02em] ">
            We couldn't accept this invitation.
          </h1>
          {error && (
            <p
              role="alert"
              className="mb-3 text-danger leading-normal block mt-3 mr-0 ml-0 max-w-72 text-xs "
            >
              {error}
            </p>
          )}
          <p
            className="text-muted text-[11px] text-center mt-5 mr-0 mb-0 ml-0 leading-normal"
            data-login-help=""
          >
            The code may have expired or been replaced by a newer invitation.
            Ask a workspace admin to resend it.
          </p>
          <a
            className="w-full focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
            href="/signin/signout"
          >
            Use a different account
          </a>
        </section>
      </main>
    );
  if (mode === "google")
    return (
      <main className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 sm:p-6 bg-bg text-foreground [@media_(max-width:_600px)]:pt-4 [@media_(max-width:_600px)]:pr-4 [@media_(max-width:_600px)]:pb-4 [@media_(max-width:_600px)]:pl-4">
        <section className="w-[min(100%,420px)] max-w-105 border border-line bg-panel pt-8 pr-8 pb-8 pl-8 sm:p-8 shadow-(--shadow) [@media_(max-width:_600px)]:pt-6.5 [@media_(max-width:_600px)]:pr-5.5 [@media_(max-width:_600px)]:pb-6.5 [@media_(max-width:_600px)]:pl-5.5">
          <div
            className="mb-6 flex items-center gap-2.5 text-[18px]"
            data-onboarding-brand=""
          >
            <img
              className="block w-5 h-5 object-contain"
              data-brand-mark=""
              src="/hoja-clover.8371400e.svg"
              alt=""
            />
            <span>Hoja</span>
          </div>
          <h1 className="mb-1.5 text-[22px] font-semibold leading-[1.3] tracking-[-0.02em] ">
            Join your team.
          </h1>
          <p className="mb-6 text-muted leading-normal">
            Sign in with the Google account this invitation was sent to, and
            we'll add you to the workspace.
          </p>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[0.65]"
            data-control-primary=""
            disabled={busy}
            onClick={() => location.assign(googleUrl)}
          >
            {busy ? "Joining…" : "Continue with Google →"}
          </button>
          {error && (
            <p
              role="alert"
              className="mb-3 text-danger leading-normal block mt-3 mr-0 ml-0 max-w-72 text-xs "
            >
              {error}
            </p>
          )}
          <p
            className="text-muted text-[11px] text-center mt-5 mr-0 mb-0 ml-0 leading-normal"
            data-login-help=""
          >
            The link works once and expires in 72 hours.
          </p>
        </section>
      </main>
    );
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    await accept({ code, email, password });
  };
  return (
    <main className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 sm:p-6 bg-bg text-foreground [@media_(max-width:_600px)]:pt-4 [@media_(max-width:_600px)]:pr-4 [@media_(max-width:_600px)]:pb-4 [@media_(max-width:_600px)]:pl-4">
      <section className="w-[min(100%,420px)] max-w-105 border border-line bg-panel pt-8 pr-8 pb-8 pl-8 sm:p-8 shadow-(--shadow) [@media_(max-width:_600px)]:pt-6.5 [@media_(max-width:_600px)]:pr-5.5 [@media_(max-width:_600px)]:pb-6.5 [@media_(max-width:_600px)]:pl-5.5">
        <div
          className="mb-6 flex items-center gap-2.5 text-[18px]"
          data-onboarding-brand=""
        >
          <img
            className="block w-5 h-5 object-contain"
            data-brand-mark=""
            src="/hoja-clover.8371400e.svg"
            alt=""
          />
          <span>Hoja</span>
        </div>
        <h1 className="mb-1.5 text-[22px] font-semibold leading-[1.3] tracking-[-0.02em] ">
          Join your team.
        </h1>
        <p className="mb-6 text-muted leading-normal">
          Enter the email address this invitation was sent to and choose a
          password.
        </p>
        <form onSubmit={submit}>
          <label className="mt-3.5 block text-[12px] font-semibold">
            Invite code
            <input
              className="border mt-1.75 mb-3.5 block w-full border-line bg-bg pt-2.5 pr-2.5 pb-2.5 pl-2.5 text-foreground focus:outline-2 focus:outline-accent focus:outline-offset-2 max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Invite code"
              value={code}
              readOnly
            />
          </label>
          <label className="mt-3.5 block text-[12px] font-semibold">
            Your email
            <input
              className="border mt-1.75 mb-3.5 block w-full border-line bg-bg pt-2.5 pr-2.5 pb-2.5 pl-2.5 text-foreground focus:outline-2 focus:outline-accent focus:outline-offset-2 max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="mt-3.5 block text-[12px] font-semibold">
            Create a password
            <input
              className="border mt-1.75 mb-3.5 block w-full border-line bg-bg pt-2.5 pr-2.5 pb-2.5 pl-2.5 text-foreground focus:outline-2 focus:outline-accent focus:outline-offset-2 max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 12 characters"
            />
          </label>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[0.65]"
            data-control-primary=""
            disabled={busy}
          >
            {busy ? "Joining…" : "Accept invitation →"}
          </button>
        </form>
        {error && (
          <p
            role="alert"
            className="mb-3 text-danger leading-normal block mt-3 mr-0 ml-0 max-w-72 text-xs "
          >
            {error}
          </p>
        )}
        <p
          className="text-muted text-[11px] text-center mt-5 mr-0 mb-0 ml-0 leading-normal"
          data-login-help=""
        >
          Invite codes expire and can only be used once.
        </p>
      </section>
    </main>
  );
}
type Workspace = { organizationId: string; name: string; urlKey: string };
type PendingWorkspace = {
  email: string;
  password: string;
  workspaces: Workspace[];
};
function WorkspaceChooser({
  pending,
  onLogin,
  onBack,
}: {
  pending: PendingWorkspace;
  onLogin: () => void;
  onBack: () => void;
}) {
  const [organizationId, setOrganizationId] = useState(
    pending.workspaces[0]?.organizationId || "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: pending.email,
          password: pending.password,
          organizationId,
        }),
      });
      const b = await r.json();
      if (!r.ok) throw Error(b.error || "Could not open this workspace");
      if (b.redirectUrl) {
        window.location.assign(b.redirectUrl);
        return;
      }
      onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 bg-bg max-md:pt-6 max-md:pr-6 max-md:pb-6 max-md:pl-6 [@media_(max-width:750px)]:pt-6 [@media_(max-width:750px)]:pr-6 [@media_(max-width:750px)]:pb-6 [@media_(max-width:750px)]:pl-6"
      data-login-page=""
    >
      <div
        className="border shadow-(--shadow) pt-8 pr-8 pb-8 pl-8 border-line rounded-none bg-panel max-md:mt-auto max-md:mr-auto max-md:mb-auto max-md:ml-auto max-md:w-full w-[min(100%,420px)] max-w-105 [@media_(max-width:750px)]:mt-auto [@media_(max-width:750px)]:mr-auto [@media_(max-width:750px)]:mb-auto [@media_(max-width:750px)]:ml-auto [@media_(max-width:750px)]:w-full"
        data-login-card=""
      >
        <div
          className="flex items-center justify-start gap-2.5 mb-6 text-[18px] max-md:mb-auto [@media_(max-width:750px)]:mb-auto"
          data-login-brand=""
        >
          <img
            className="block w-5 h-5 object-contain rounded-none"
            data-brand-mark=""
            src="/hoja-clover.8371400e.svg"
            alt=""
          />
          <b>Hoja</b>
        </div>
        <div data-login-card-head="">
          <p className="text-muted mt-0 mr-0 mb-6 ml-0">Step 2 of 2</p>
          <h2 className="text-[22px] mt-0 mr-0 mb-1.5 ml-0">
            Choose workspace
          </h2>
          <p className="text-muted mt-0 mr-0 mb-6 ml-0">
            Select the workspace to open for {pending.email}.
          </p>
        </div>
        <form onSubmit={open}>
          <label
            className="block mt-3.5 text-[12px] font-semibold"
            htmlFor="organization"
          >
            Workspace
          </label>
          <select
            className="w-full mt-1.75 mr-0 mb-3.5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border border-line rounded-none bg-bg text-foreground max-md:text-base focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            id="organization"
            value={organizationId}
            onChange={(e) => setOrganizationId(e.target.value)}
            required
          >
            {pending.workspaces.map((w) => (
              <option key={w.organizationId} value={w.organizationId}>
                {w.name} ({w.urlKey})
              </option>
            ))}
          </select>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full relative mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
            data-control-primary=""
            disabled={busy || !organizationId}
          >
            {busy ? "Opening…" : "Open workspace"}{" "}
            <span className="absolute right-3.5 ml-0">→</span>
          </button>
          <button
            type="button"
            className="mt-2.5 block bg-panel text-[12px] text-muted hover:bg-accent-soft hover:text-accent disabled:opacity-50 rounded-none min-h-11 w-full mr-0 mb-0 ml-0 border border-line font-[550] hover:border-accent focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            onClick={onBack}
          >
            Back to sign in
          </button>
        </form>
        {error && (
          <small
            className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {error}
          </small>
        )}
      </div>
    </div>
  );
}
function Login({
  onLogin,
  error,
  onWorkspaceRequired,
}: {
  onLogin: (t: string) => void;
  error: string;
  onWorkspaceRequired: (p: PendingWorkspace) => void;
}) {
  const [t, setT] = useState("");
  const [show, setShow] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [credentialMode, setCredentialMode] = useState<"email" | "apiKey">(
    "email",
  );
  const [mode, setMode] = useState<"login" | "setup">("login");
  const [authError, setAuthError] = useState("");
  const [busy, setBusy] = useState(false);
  const [googleUrl, setGoogleUrl] = useState("");
  const [credentialSignIn, setCredentialSignIn] = useState(true);
  useEffect(() => {
    let live = true;
    fetch("/api/signup/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        if (live && c?.hosted) {
          const entry =
            typeof c?.loginUrl === "string" && c.loginUrl
              ? c.loginUrl
              : c?.signInUrl || "";
          setGoogleUrl(entry);
          setCredentialSignIn(c?.credentialSignIn !== false);
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setAuthError("");
    try {
      const apiKey = credentialMode === "apiKey" ? email.trim() : undefined;
      const r = await fetch("/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify(apiKey ? { apiKey } : { email, password }),
      });
      const b = await r.json();
      if (r.status === 409) {
        const wr = await fetch("/auth/workspaces", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        const wb = await wr.json();
        onWorkspaceRequired({
          email,
          password,
          workspaces: wb.workspaces || [],
        });
        return;
      }
      if (!r.ok) throw Error(b.error || "Sign in failed");
      if (b.redirectUrl) {
        // An enterprise workspace is opened on its own address.
        window.location.assign(b.redirectUrl);
        return;
      }
      onLogin("session");
    } catch (err) {
      setAuthError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const setup = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setAuthError("");
    try {
      const r = await fetch("/auth/setup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: t, password, email }),
      });
      const b = await r.json();
      if (!r.ok) throw Error(b.error || "Password setup failed");
      setMode("login");
      setAuthError("Password set. Sign in with your email and password.");
    } catch (err) {
      setAuthError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="min-w-0 min-h-dvh grid place-items-center pt-6 pr-6 pb-6 pl-6 bg-bg max-md:pt-6 max-md:pr-6 max-md:pb-6 max-md:pl-6 [@media_(max-width:750px)]:pt-6 [@media_(max-width:750px)]:pr-6 [@media_(max-width:750px)]:pb-6 [@media_(max-width:750px)]:pl-6"
      data-login-page=""
    >
      <div
        className="min-w-0 [&_form>input]:min-w-0 [&_form>select]:min-w-0 border shadow-(--shadow) pt-8 pr-8 pb-8 pl-8 border-line rounded-none bg-panel max-md:mt-auto max-md:mr-auto max-md:mb-auto max-md:ml-auto max-md:w-full w-[min(100%,420px)] max-w-105 [@media_(max-width:750px)]:mt-auto [@media_(max-width:750px)]:mr-auto [@media_(max-width:750px)]:mb-auto [@media_(max-width:750px)]:ml-auto [@media_(max-width:750px)]:w-full"
        data-login-card=""
      >
        <div
          className="flex items-center justify-start gap-2.5 mb-6 text-[18px] max-md:mb-auto [@media_(max-width:750px)]:mb-auto"
          data-login-brand=""
        >
          <img
            className="block w-5 h-5 object-contain rounded-none"
            data-brand-mark=""
            src="/hoja-clover.8371400e.svg"
            alt=""
          />
          <b>Hoja</b>
        </div>
        <div data-login-card-head="">
          <h2 className="text-[22px] mt-0 mr-0 mb-1.5 ml-0">Welcome back</h2>
          <p className="text-muted mt-0 mr-0 mb-6 ml-0">
            Sign in with your email or API key.
          </p>
        </div>
        {mode === "login" && googleUrl && (
          <a
            data-login-google=""
            href={googleUrl}
            className="flex items-center justify-center min-h-11 mb-4.5 border border-line bg-panel text-[13px] font-semibold no-underline text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 hover:bg-accent-soft hover:text-accent"
          >
            Continue with Google
          </a>
        )}
        {mode === "login" && !credentialSignIn ? (
          <p
            className="text-muted text-[11px] text-center mt-5 mr-0 mb-0 ml-0"
            data-login-help=""
          >
            This workspace uses Google sign-in. Continue with the Google account
            your workspace knows.
          </p>
        ) : mode === "login" ? (
          <form onSubmit={login}>
            <div
              className="border grid gap-0 mt-0 mr-0 mb-5.5 ml-0 border-line [&_button+button]:border-l [&_button+button]:border-line grid-cols-2 [&>button+button]:border-l [&>button+button]:border-line"
              data-credential-modes=""
              role="group"
              aria-label="Sign-in method"
            >
              <button
                type="button"
                aria-pressed={credentialMode === "email"}
                className=" rounded-none min-h-10.5 bg-accent-soft text-accent font-semibold focus-visible:relative aria-pressed:bg-accent-soft aria-pressed:text-accent border-line not-first:border-l focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                onClick={() => {
                  setCredentialMode("email");
                  setAuthError("");
                }}
              >
                Email
              </button>
              <button
                type="button"
                aria-pressed={credentialMode === "apiKey"}
                className=" rounded-none min-h-10.5 bg-accent-soft text-accent font-semibold focus-visible:relative aria-pressed:bg-accent-soft aria-pressed:text-accent border-line not-first:border-l focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                onClick={() => {
                  setCredentialMode("apiKey");
                  setAuthError("");
                  setPassword("");
                }}
              >
                API key
              </button>
            </div>
            <label
              className="block mt-3.5 text-[12px] font-semibold"
              htmlFor="email"
            >
              {credentialMode === "email" ? "Email" : "API key"}
            </label>
            <input
              className="w-full mt-1.75 mr-0 mb-3.5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border border-line rounded-none bg-bg text-foreground max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              id="email"
              type={credentialMode === "email" ? "email" : "password"}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete={credentialMode === "email" ? "username" : "off"}
              placeholder={
                credentialMode === "email" ? "you@example.com" : "hoja_…"
              }
              required
            />
            {credentialMode === "email" && (
              <>
                <label
                  className="block mt-3.5 text-[12px] font-semibold"
                  htmlFor="password"
                >
                  Password
                </label>
                <div
                  className="flex items-center border border-line rounded-none bg-bg mt-1.75"
                  data-password-field=""
                >
                  <input
                    className="flex-1 min-w-0 border-0 bg-transparent text-foreground pt-2.5 pr-2.5 pb-2.5 pl-2.5 max-md:text-base outline-none placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    id="password"
                    type={show ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                  <button
                    className="bg-transparent text-muted pt-2 pr-2 pb-2 pl-2 text-[12px] rounded-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    type="button"
                    onClick={() => setShow(!show)}
                  >
                    {show ? "Hide" : "Show"}
                  </button>
                </div>
              </>
            )}
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full relative mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
              data-control-primary=""
              disabled={busy}
            >
              {busy ? "Signing in…" : "Continue"}{" "}
              <span className="absolute right-3.5 ml-0">→</span>
            </button>
            <button
              type="button"
              className="mt-3 block w-full bg-transparent py-2 text-center text-xs text-muted underline underline-offset-2 hover:text-foreground disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11"
              onClick={() => {
                setMode("setup");
                setAuthError("");
              }}
            >
              Set up password using an API key
            </button>
          </form>
        ) : (
          <form onSubmit={setup}>
            <label
              className="block mt-3.5 text-[12px] font-semibold"
              htmlFor="setup-token"
            >
              Existing workspace API key
            </label>
            <input
              className="w-full mt-1.75 mr-0 mb-3.5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border border-line rounded-none bg-bg text-foreground max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              id="setup-token"
              type="password"
              value={t}
              onChange={(e) => setT(e.target.value)}
              placeholder="hoja_..."
              required
            />
            <label
              className="block mt-3.5 text-[12px] font-semibold"
              htmlFor="setup-email"
            >
              Email
            </label>
            <input
              className="w-full mt-1.75 mr-0 mb-3.5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border border-line rounded-none bg-bg text-foreground max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              id="setup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <label
              className="block mt-3.5 text-[12px] font-semibold"
              htmlFor="setup-password"
            >
              Create password (12+ characters)
            </label>
            <input
              className="w-full mt-1.75 mr-0 mb-3.5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border border-line rounded-none bg-bg text-foreground max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              id="setup-password"
              type="password"
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full relative mt-4.5 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
              data-control-primary=""
              disabled={busy}
            >
              Set password
            </button>
            <button
              type="button"
              className="mt-2.5 block bg-panel text-[12px] text-muted hover:bg-accent-soft hover:text-accent disabled:opacity-50 rounded-none min-h-11 w-full mr-0 mb-0 ml-0 border border-line font-[550] hover:border-accent focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => setMode("login")}
            >
              Back to sign in
            </button>
          </form>
        )}
        {(authError || error) && (
          <small
            className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {authError || error}
          </small>
        )}
        <p
          className="mt-5 mb-0 text-center text-xs text-muted"
          data-login-help=""
        >
          New to Hoja?{" "}
          <a
            href="/signup"
            className="font-medium text-foreground underline underline-offset-2"
          >
            Request a workspace
          </a>
        </p>
      </div>
    </div>
  );
}
function IssueDrawer({
  issue,
  teams,
  projects,
  labels,
  onClose,
  onUpdated,
}: {
  issue: Issue;
  teams: Team[];
  projects: Project[];
  labels: { id: string; name: string; color?: string }[];
  onClose: () => void;
  onUpdated: (issue: Issue) => void;
}) {
  const live = useIssueSelect(issue.id);
  const [loaded, setLoaded] = useState<Issue>(() => ({
    ...issue,
    ...(cachedIssueDetail(issue.id)?.detail as Issue | undefined),
  }));
  const [local, setLocal] = useState<Partial<Issue>>({});
  const detail: Issue = {
    ...loaded,
    ...(live
      ? {
          title: live.title,
          priority: live.priority,
          priorityLabel: live.priorityLabel,
          state: live.state,
          team: live.team,
          project: live.project,
          assignee: live.assignee,
        }
      : {}),
    ...local,
  };
  const [body, setBody] = useState("");
  const [pending, setPending] = useState<Comment[]>([]);
  const [error, setError] = useState("");
  /* eslint-disable react-hooks/exhaustive-deps -- route or session driven; the listed values are written by this effect or are recreated each render */
  useEffect(() => {
    fetchIssueDetail(issue.identifier || issue.id, issue.id)
      .then((e) => {
        setLoaded((old) => ({ ...old, ...(e.detail as Issue) }));
        onUpdated(e.detail as Issue);
      })
      .catch((e) => setError(e.message));
  }, [issue.id]);
  /* eslint-enable react-hooks/exhaustive-deps */
  const update = async (input: Record<string, unknown>) => {
    setError("");
    const sync = getSync();
    const optimistic: Partial<Issue> = sync ? sync.patchFromInput(input) : {};
    if (Object.prototype.hasOwnProperty.call(input, "description"))
      optimistic.description = (input.description as string) || undefined;
    const settle = sync?.patch(detail.id, optimistic);
    setLocal((l) => ({ ...l, ...optimistic }));
    try {
      const d = await gql(
        `mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier title description priority priorityLabel state{id name color type} team{id key name} project{id name} assignee{id name avatarSeed} comments{nodes{id body createdAt user{name avatarSeed}}}}}}`,
        { id: detail.id, input },
      );
      if (!d.issueUpdate.success) throw Error("Update was rejected");
      settle?.(true);
      setLoaded(d.issueUpdate.issue);
      onUpdated(d.issueUpdate.issue);
    } catch (e) {
      settle?.(false);
      setError((e as Error).message);
    } finally {
      setLocal((l) => {
        const n = { ...l };
        for (const k of Object.keys(optimistic))
          delete (n as Record<string, unknown>)[k];
        return n;
      });
    }
  };
  const comment = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = body.trim();
    if (!text) return;
    const temp: Comment = {
      id: `local-comment-${Date.now()}`,
      body: text,
      createdAt: "Sending…",
      user: { name: "You" },
    };
    setPending((p) => [...p, temp]);
    setBody("");
    setError("");
    try {
      const d = await gql(
        `mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment{id body createdAt user{name avatarSeed}}}}`,
        { input: { issueId: detail.id, body: text } },
      );
      if (!d.commentCreate.success) throw Error("Comment was rejected");
      setLoaded((old) => ({
        ...old,
        comments: {
          nodes: [...(old.comments?.nodes || []), d.commentCreate.comment],
        },
      }));
    } catch (e) {
      setBody((b) => b || text);
      setError((e as Error).message);
    } finally {
      setPending((p) => p.filter((x) => x !== temp));
    }
  };
  const toggleLabel = async (labelId: string) => {
    const current = labels
      .filter((l) => (detail as any).labelIds?.includes(l.id))
      .map((l) => l.id);
    const next = current.includes(labelId)
      ? current.filter((id) => id !== labelId)
      : [...current, labelId];
    await update({ labelIds: next });
  };
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <div
      className="fixed inset-0 z-10 bg-[#0008]"
      data-drawer-backdrop=""
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside className="drawer shadow-(--shadow) absolute right-0 top-0 h-full pt-6 pr-6 pb-6 pl-6 bg-panel w-[min(500px,100%)] animate-[slide_.2s_ease]">
        <div className="flex items-start justify-between" data-drawer-head="">
          <span className="text-muted text-[13px]">{detail.identifier}</span>
          <button
            className="grid w-8 h-8 place-items-center rounded-md bg-transparent hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>
        <h2 className="text-[24px] leading-[1.2] mt-8 mr-0 mb-4 ml-0">
          {detail.title}
        </h2>
        <textarea
          className="border w-full min-h-27.5 mt-1 mr-0 mb-5 ml-0 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border-line rounded-md bg-bg text-foreground resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
          value={draft ?? detail.description ?? ""}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add description..."
        />
        <button
          onClick={() => {
            const text = draft ?? detail.description ?? "";
            setDraft(null);
            void update({ description: text });
          }}
          disabled={draft === null}
          className="focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        >
          Save description
        </button>
        <div
          className="border-t border-line grid pt-5 grid-cols-[1fr_2fr] gap-x-3 gap-y-3.5"
          data-detail-grid=""
        >
          <span className="text-muted">Status</span>
          <select
            className="pt-1.25 pr-1.25 pb-1.25 pl-1.25 text-[12px] max-md:text-base border border-line rounded-md bg-panel text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={detail.state?.id || ""}
            onChange={(e) => update({ stateId: e.target.value })}
          >
            <option value="">No state</option>
            {teams
              .find((t) => t.id === detail.team?.id)
              ?.states.nodes.map((st) => (
                <option key={st.id} value={st.id}>
                  {st.name}
                </option>
              ))}
          </select>
          <span className="text-muted">Priority</span>
          <select
            className="pt-1.25 pr-1.25 pb-1.25 pl-1.25 text-[12px] max-md:text-base border border-line rounded-md bg-panel text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={detail.priority || 0}
            onChange={(e) => update({ priority: Number(e.target.value) })}
          >
            <option value="0">No priority</option>
            <option value="1">Urgent</option>
            <option value="2">High</option>
            <option value="3">Medium</option>
            <option value="4">Low</option>
          </select>
          <span className="text-muted">Team</span>
          <select
            className="pt-1.25 pr-1.25 pb-1.25 pl-1.25 text-[12px] max-md:text-base border border-line rounded-md bg-panel text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={detail.team?.id || ""}
            onChange={(e) => update({ teamId: e.target.value })}
          >
            <option value="">No team</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <span className="text-muted">Assignee</span>
          <select
            className="pt-1.25 pr-1.25 pb-1.25 pl-1.25 text-[12px] max-md:text-base border border-line rounded-md bg-panel text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={detail.assignee?.id || ""}
            onChange={(e) => update({ assigneeId: e.target.value || null })}
          >
            <option value="">Unassigned</option>
            {(
              teams.find((t) => t.id === detail.team?.id)?.members?.nodes || []
            ).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name || m.email}
              </option>
            ))}
          </select>
          <span className="text-muted">Project</span>
          <select
            className="pt-1.25 pr-1.25 pb-1.25 pl-1.25 text-[12px] max-md:text-base border border-line rounded-md bg-panel text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={detail.project?.id || ""}
            onChange={(e) => update({ projectId: e.target.value || null })}
          >
            <option value="">No project</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <b>Labels</b>
          {labels.map((l) => (
            <label key={l.id}>
              <input
                className="max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
                type="checkbox"
                checked={Boolean((detail as any).labelIds?.includes(l.id))}
                onChange={() => toggleLabel(l.id)}
              />
              {l.name}
            </label>
          ))}
        </div>
        {error && (
          <p
            className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {error}
          </p>
        )}
        <div
          className="border-t border-line mt-5.5 pt-4.5"
          data-drawer-comments=""
        >
          <h3 className="text-[13px]">Comments</h3>
          {!detail.comments && (
            <p className="text-muted" data-control-muted="">
              Loading comments…
            </p>
          )}
          {detail.comments?.nodes.length === 0 && !pending.length && (
            <p className="text-muted" data-control-muted="">
              No comments yet.
            </p>
          )}
          {[...(detail.comments?.nodes || []), ...pending].map((c) => (
            <article
              className="pt-2.5 pr-0 pb-2.5 pl-0 border-b border-line text-[12px]"
              key={c.id}
              aria-busy={c.id.startsWith("local-") || undefined}
            >
              <b>{c.user?.name || "User"}</b>
              <time>{c.createdAt || ""}</time>
              <Markdown text={c.body} />
            </article>
          ))}
          <form className="grid gap-2 mt-3.5" onSubmit={comment}>
            <textarea
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-bg text-foreground resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Leave a comment..."
            />
            <button
              className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
              data-control-primary=""
              disabled={!body.trim()}
            >
              Comment
            </button>
          </form>
        </div>
      </aside>
    </div>
  );
}
function NewIssue({
  teams,
  onClose,
  onCreated,
  initialStateId,
  initialTeamId,
}: {
  teams: Team[];
  onClose: () => void;
  onCreated: () => void;
  initialStateId?: string;
  initialTeamId?: string;
}) {
  const meta = useSyncMeta();
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  /* Dialog polish: a short exit animation before unmount (collapsed by reduced motion),
   * focus kept inside the dialog while it is open, and focus returned to the trigger. */
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  /* Captured during render: by the time effects run, autoFocus has already moved focus into the title. */
  const returnFocus = useRef<HTMLElement | null>(
    typeof document !== "undefined" &&
      document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  const requestClose = () => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.setTimeout(onClose, reduced ? 0 : 150);
  };
  useEffect(
    () => () => {
      const el = returnFocus.current;
      if (el && document.contains(el)) el.focus();
    },
    [],
  );
  /* Lock the page behind the dialog without moving it: the gutter stays reserved. */
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("hoja-modal-open");
    return () => root.classList.remove("hoja-modal-open");
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const root = dialogRef.current;
      if (!root) return;
      const items = Array.from(
        root.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[href],[tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0],
        last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);
  const [createMore, setCreateMore] = useState(false);
  const [title, setTitle] = useState(""),
    [description, setDescription] = useState(""),
    [team, setTeam] = useState(
      /* A column prefill names the status, so its team wins; otherwise the team the
       * composer was opened from; otherwise the workspace default. */
      teams.find(
        (t) =>
          Boolean(initialStateId) &&
          t.states.nodes.some((s) => s.id === initialStateId),
      )?.id ||
        teams.find((t) => Boolean(initialTeamId) && t.id === initialTeamId)
          ?.id ||
        teams[0]?.id ||
        "",
    ),
    [priority, setPriority] = useState("0"),
    [stateId, setStateId] = useState(initialStateId || ""),
    [assigneeId, setAssigneeId] = useState(""),
    [projectId, setProjectId] = useState(""),
    [cycleId, setCycleId] = useState(""),
    [dueDate, setDueDate] = useState(""),
    [labelIds, setLabelIds] = useState<string[]>([]),
    [projects, setProjects] = useState<Project[]>(
      () => meta?.projects.nodes || [],
    ),
    [cycles, setCycles] = useState<Cycle[]>(() => meta?.cycles.nodes || []),
    [labels, setLabels] = useState<
      { id: string; name: string; color?: string }[]
    >(() => meta?.issueLabels.nodes || []),
    [loadingOptions, setLoadingOptions] = useState(!meta),
    [busy, setBusy] = useState(false),
    [err, setErr] = useState("");
  const selectedTeam = teams.find((t) => t.id === team);
  const states = selectedTeam?.states.nodes || [];
  const members = selectedTeam?.members?.nodes || [];
  // The synced workspace metadata already carries projects, cycles and labels; only fetch them when sync is unavailable.
  /* eslint-disable react-hooks/exhaustive-deps -- route or session driven; the listed values are written by this effect or are recreated each render */
  useEffect(() => {
    if (meta) return;
    let live = true;
    Promise.all([
      gql(
        `query Projects{projects(first:100){nodes{id name state teams{nodes{id name key}}}}}`,
      ),
      gql(
        `query Cycles{cycles(first:100){nodes{id name number startsAt endsAt completedAt team{id name key}}}}`,
      ),
      gql(`query Labels{issueLabels(first:100){nodes{id name color}}}`),
    ])
      .then(([p, c, l]) => {
        if (live) {
          setProjects(p.projects.nodes);
          setCycles(c.cycles.nodes);
          setLabels(l.issueLabels.nodes);
        }
      })
      .catch((e) => {
        if (live)
          setErr(`Could not load task options: ${(e as Error).message}`);
      })
      .finally(() => {
        if (live) setLoadingOptions(false);
      });
    return () => {
      live = false;
    };
  }, []);
  /* eslint-enable react-hooks/exhaustive-deps */
  const resetForMore = () => {
    setTitle("");
    setDescription("");
    setDueDate("");
    setLabelIds([]);
    setErr("");
    requestAnimationFrame(() =>
      document
        .querySelector<HTMLInputElement>("[data-issue-create-title]")
        ?.focus(),
    );
  };
  /* Optimistic create: a placeholder issue appears in every list/board at once and the dialog closes;
   * the server's row replaces it. A rejected create removes the placeholder and reports the error. */
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !title.trim() || !team) return;
    setErr("");
    const input = {
      title: title.trim(),
      description: description.trim() || null,
      teamId: team,
      priority: Number(priority),
      ...(stateId ? { stateId } : {}),
      ...(assigneeId ? { assigneeId } : {}),
      ...(projectId ? { projectId } : {}),
      ...(cycleId ? { cycleId } : {}),
      ...(dueDate ? { dueDate } : {}),
      labelIds,
    };
    if (!getSync()) {
      setBusy(true);
      try {
        const d = await gql(
          `mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier title}}}`,
          { input },
        );
        if (!d.issueCreate.success) throw Error("Could not create task");
        void attachGithubReferences(d.issueCreate.issue.id, input.description);
        if (createMore) resetForMore();
        else onCreated();
      } catch (e) {
        setErr((e as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }
    const now = new Date().toISOString();
    const viewer = meta?.viewer;
    const member = members.find((m) => m.id === assigneeId);
    const project = projects.find((p) => p.id === projectId);
    const cycle = cycles.find((c) => c.id === cycleId);
    const placeholder: Issue = {
      id: localIssueId(),
      identifier: guessNextIdentifier(selectedTeam?.key || "ISS"),
      title: input.title,
      description: input.description || undefined,
      dueDate: dueDate || undefined,
      createdAt: now,
      updatedAt: now,
      stateChangedAt: now,
      priority: input.priority,
      priorityLabel: ["No priority", "Urgent", "High", "Medium", "Low"][
        input.priority
      ],
      state:
        states.find((s) => s.id === stateId) || guessDefaultState(selectedTeam),
      team: selectedTeam
        ? {
            id: selectedTeam.id,
            key: selectedTeam.key,
            name: selectedTeam.name,
          }
        : undefined,
      assignee: member
        ? { id: member.id, name: member.name || member.email || "" }
        : undefined,
      project: project ? { id: project.id, name: project.name } : undefined,
      cycle: cycle
        ? { id: cycle.id, name: cycle.name, number: cycle.number }
        : undefined,
      creator: viewer?.id ? { id: viewer.id, name: viewer.name } : undefined,
      labels: { nodes: labels.filter((l) => labelIds.includes(l.id)) },
      subscribers: { nodes: viewer?.id ? [{ id: viewer.id }] : [] },
    };
    const resolve = addLocalIssue(placeholder);
    if (createMore) resetForMore();
    else requestClose();
    try {
      const d = await gql(
        `mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue ${issueFields}}}`,
        { input },
      );
      if (!d.issueCreate?.success || !d.issueCreate.issue)
        throw Error("Could not create task");
      void attachGithubReferences(d.issueCreate.issue.id, input.description);
      resolve(d.issueCreate.issue);
    } catch (e) {
      resolve(null);
      const msg = `Could not create “${input.title}”: ${(e as Error).message}`;
      if (mounted.current) {
        setErr(msg);
        setTitle((t) => t || input.title);
        setDescription((d) => d || input.description || "");
      } else void alertDialog(msg);
    }
  };
  return (
    <div
      className="fixed inset-0 z-10 bg-[#0008] has-data-issue-create-modal:p-[clamp(60px,13vh,118px)_12px_12px] [&:has(>[data-issue-create-modal])]:animate-[issue-backdrop-in_var(--motion-overlay)_var(--ease-standard)_both] [&:has(>[data-issue-create-modal][data-is-closing])]:animate-[issue-backdrop-out_var(--motion-fast)_var(--ease-standard)_both] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[background:rgba(9,9,11,.42)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[backdrop-filter:blur(2px)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[-webkit-backdrop-filter:blur(2px)]"
      data-modal-backdrop=""
    >
      <form
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="New task"
        aria-busy={busy || undefined}
        className={
          (closing
            ? "modal shadow-[0_14px_40px_#0003] relative top-auto left-auto mt-0 mr-0 mb-0 ml-0 overflow-auto pt-0 pr-0 pb-0 pl-0 rounded-2xl bg-panel"
            : "modal shadow-[0_14px_40px_#0003] relative top-auto left-auto mt-0 mr-0 mb-0 ml-0 overflow-auto pt-0 pr-0 pb-0 pl-0 rounded-2xl bg-panel") +
          " absolute pt-0 pr-0 pb-0 pl-0 border border-line rounded-2xl bg-panel shadow-[0_14px_40px_#0003] max-sm:w-full" +
          "font-['Inter_Variable',Inter,ui-sans-serif,system-ui,sans-serif] [&_[data-issue-property]_input[type=date]::-webkit-calendar-picker-indicator]:opacity-50 [&_[data-issue-property]_input[type=date]::-webkit-calendar-picker-indicator]:cursor-pointer max-md:[&_[data-issue-property]:hover_input[type=date]::-webkit-calendar-picker-indicator]:opacity-100" +
          " transform-none" +
          " [@media_(max-width:560px)]:w-[calc(100vw-16px)] [@media_(max-width:560px)]:max-h-[94dvh] [@media_(max-width:600px)]:w-full [@media_(max-width:600px)]:max-h-[calc(100dvh-24px)]" +
          " top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(746px,calc(100vw-24px))] max-h-[calc(100dvh-140px)] [@media_(max-width:560px)]:w-[calc(100vw-16px)] [@media_(max-width:560px)]:max-h-[94dvh] [@media_(max-width:600px)]:max-h-[calc(100dvh-24px)] animate-[issue-modal-out_var(--motion-fast)_var(--ease-standard)_both]" +
          " data-is-closing:animate-[issue-modal-out_var(--motion-fast)_var(--ease-standard)_both]"
        }
        data-issue-create-modal=""
        data-is-closing={closing ? "" : undefined}
        onSubmit={create}
      >
        <div
          className="sticky top-0 z-3 flex items-center gap-1.75 pt-2.25 pr-3.5 pb-2.25 pl-3.5 bg-panel h-11 border-line max-sm:pt-3 max-sm:pr-3.5 max-sm:pb-3 max-sm:pl-3.5 border-b-0 [@media_(max-width:560px)]:pt-3 [@media_(max-width:560px)]:pr-3.5 [@media_(max-width:560px)]:pb-3 [@media_(max-width:560px)]:pl-3.5"
          data-issue-create-header=""
        >
          <div
            className="grid place-items-center w-5.5 h-5.5 flex-none rounded-[50%] bg-accent-soft text-accent text-[9px] font-bold"
            data-issue-create-team-mark=""
          >
            {selectedTeam?.key.slice(0, 2) || "IS"}
          </div>
          <div className="flex-1 min-w-0" data-issue-create-header-copy="">
            <div
              className="flex gap-1.5 text-foreground text-[12px]"
              data-issue-create-breadcrumb=""
            >
              <span>{selectedTeam?.key || "Team"}</span>
              <span>›</span>
              <span>New task</span>
            </div>
          </div>
          <button
            type="button"
            className="grid w-8 h-8 place-items-center rounded-md bg-transparent hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            aria-label="Close"
            onClick={requestClose}
          >
            <X size={18} />
          </button>
        </div>
        <div
          className="pt-2.5 pr-4.5 pb-2 pl-4.5 max-sm:pt-2.5 max-sm:pr-3.5 max-sm:pb-2.5 max-sm:pl-3.5 overscroll-contain [@media_(max-width:560px)]:pt-3.25 [@media_(max-width:560px)]:pr-3.5 [@media_(max-width:560px)]:pb-4 [@media_(max-width:560px)]:pl-3.5 [@media_(max-width:600px)]:pt-2.5 [@media_(max-width:600px)]:pr-3.5 [@media_(max-width:600px)]:pb-2.5 [@media_(max-width:600px)]:pl-3.5"
          data-issue-create-body=""
        >
          <input
            className="w-full text-foreground bg-transparent border-0 text-[18px] font-[450] tracking-tight h-9 rounded-none resize-y max-sm:text-lg shadow-[none] focus-visible:rounded-none max-md:text-base placeholder:text-muted outline-none focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 caret-accent selection:bg-accent/20 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:text-[18px] shell-phone:text-[16px] p-0"
            data-issue-create-title=""
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            // eslint-disable-next-line jsx-a11y/no-autofocus -- the title input mount-focus is captured at render (line 399); an effect would move focus after that snapshot
            autoFocus
            placeholder="Task title"
            aria-label="Task title"
            required
          />
          <textarea
            className="w-full min-h-16.25 mt-1.25 mr-0 mb-2 ml-0 text-foreground bg-transparent border-0 resize-y text-[14px] leading-normal rounded-none max-sm:min-h-16 shadow-[none] focus-visible:rounded-none max-md:text-base placeholder:text-muted outline-none focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 caret-accent selection:bg-accent/20 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:min-h-16 shell-phone:text-[16px] p-0"
            data-issue-create-description=""
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Add description…"
            aria-label="Task description"
          />
          <div
            className="border-b border-line flex flex-wrap gap-1.5 pt-0 pr-0 pb-1.25 pl-0 border-0 max-sm:gap-1 [@media_(max-width:560px)]:gap-1.25"
            data-issue-property-pills=""
          >
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Team
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={team}
                onChange={(e) => {
                  setTeam(e.target.value);
                  setStateId("");
                  setAssigneeId("");
                  setCycleId("");
                }}
                required
              >
                {teams.map((t) => (
                  <option
                    className="bg-panel text-foreground"
                    key={t.id}
                    value={t.id}
                  >
                    {t.key}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Status
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={stateId}
                onChange={(e) => setStateId(e.target.value)}
              >
                <option className="bg-panel text-foreground" value="">
                  Team default
                </option>
                {states.map((s) => (
                  <option
                    className="bg-panel text-foreground"
                    key={s.id}
                    value={s.id}
                  >
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Priority
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              >
                <option className="bg-panel text-foreground" value="0">
                  No priority
                </option>
                <option className="bg-panel text-foreground" value="1">
                  Urgent
                </option>
                <option className="bg-panel text-foreground" value="2">
                  High
                </option>
                <option className="bg-panel text-foreground" value="3">
                  Medium
                </option>
                <option className="bg-panel text-foreground" value="4">
                  Low
                </option>
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Assignee
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={assigneeId}
                onChange={(e) => setAssigneeId(e.target.value)}
              >
                <option className="bg-panel text-foreground" value="">
                  Unassigned
                </option>
                {members.map((m) => (
                  <option
                    className="bg-panel text-foreground"
                    key={m.id}
                    value={m.id}
                  >
                    {m.name || m.email}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Project
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
              >
                <option className="bg-panel text-foreground" value="">
                  No project
                </option>
                {projects
                  .filter(
                    (p) =>
                      !p.teams?.nodes.length ||
                      p.teams.nodes.some((t) => t.id === team),
                  )
                  .map((p) => (
                    <option
                      className="bg-panel text-foreground"
                      key={p.id}
                      value={p.id}
                    >
                      {p.name}
                    </option>
                  ))}
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Cycle
              </span>
              <select
                className="w-auto min-w-0 max-w-37.5 h-6 pt-0 pr-4.25 pb-0 pl-0.5 border-0 bg-transparent text-foreground text-[12px] max-sm:max-w-28 inline-block min-h-6 mt-0 mr-0 mb-0 ml-0 font-[450] max-md:text-base border border-line rounded-[7px] focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 [@media_(max-width:560px)]:max-w-30 [@media_(max-width:600px)]:max-w-27.5 shell-phone:text-[16px]"
                value={cycleId}
                onChange={(e) => setCycleId(e.target.value)}
              >
                <option className="bg-panel text-foreground" value="">
                  No cycle
                </option>
                {cycles
                  .filter((c) => c.team?.id === team && !c.completedAt)
                  .map((c) => (
                    <option
                      className="bg-panel text-foreground"
                      key={c.id}
                      value={c.id}
                    >
                      {c.name || `Cycle ${c.number ?? ""}`}
                    </option>
                  ))}
              </select>
            </label>
            <label
              className="inline-flex items-center gap-1 max-w-full h-6.5 pt-0 pr-2 pb-0 pl-2 bg-panel border-transparent rounded-[20px] text-muted text-[12px] mt-0 font-normal focus-within:border-accent max-sm:h-7 max-sm:pt-0 max-sm:pr-1.5 max-sm:pb-0 max-sm:pl-1.5 mr-0 mb-0 ml-0 border border-line focus-within:shadow-sm max-md:hover:text-foreground transition-colors duration-150 ease-out hover:transition-none hover:bg-subtle hover:border-accent/40 motion-reduce:transition-none [@media_(max-width:560px)]:h-6.75 [@media_(max-width:560px)]:pt-0 [@media_(max-width:560px)]:pr-1.5 [@media_(max-width:560px)]:pb-0 [@media_(max-width:560px)]:pl-1.5 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_16%,transparent)] [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-[color-mix(in_srgb,var(--subtle)_58%,var(--panel))] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
              data-issue-property=""
            >
              <span className="whitespace-nowrap inline text-muted text-[11px]">
                Due date
              </span>
              <input
                className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base max-w-32 h-5 border-0 text-xs font-medium placeholder:text-muted focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                aria-label="Due date"
              />
            </label>
          </div>
          <fieldset
            className="border-t border-line flex flex-wrap gap-1.5 mt-3.25 mr-0 mb-0 ml-0 pt-1.75 pr-2.25 pb-1.75 pl-2.25 border-0 rounded-none max-sm:ml-4 max-sm:mr-4 min-w-0 [[hidden]]:hidden [@media_(max-width:560px)]:ml-4.25 [@media_(max-width:560px)]:mr-4.25"
            data-issue-label-field=""
            hidden={!labelIds.length && labels.length === 0}
          >
            <legend className="pt-0 pr-1.25 pb-0 pl-1.25 text-[11px] font-semibold text-muted">
              Labels
            </legend>
            {loadingOptions ? (
              <span className="text-muted" data-control-muted="">
                Loading labels…
              </span>
            ) : labels.filter(
                (l) => !(l as any).teamId || (l as any).teamId === team,
              ).length ? (
              labels.map((l) => (
                <label
                  key={l.id}
                  className="border inline-flex items-center gap-1.5 pt-0.75 pr-1.75 pb-0.75 pl-1.75 border-line rounded-[5px] bg-panel text-[11px] h-6.25 mt-0 font-normal"
                  data-issue-label-option=""
                >
                  <input
                    className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base accent-accent placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
                    type="checkbox"
                    checked={labelIds.includes(l.id)}
                    onChange={(e) =>
                      setLabelIds((v) =>
                        e.target.checked
                          ? [...v, l.id]
                          : v.filter((id) => id !== l.id),
                      )
                    }
                  />
                  <span>{l.name}</span>
                </label>
              ))
            ) : (
              <span className="text-muted" data-control-muted="">
                No labels available
              </span>
            )}
          </fieldset>
        </div>
        {err && (
          <small
            className="pt-0 pr-4 pb-0 pl-4 block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {err}
          </small>
        )}
        <footer
          className="border-t border-line sticky bottom-0 flex items-center justify-between gap-3 pt-1.75 pr-3.5 pb-1.75 pl-3.5 bg-panel z-3 min-h-12 max-sm:pt-2 max-sm:pr-3 max-sm:pb-2 max-sm:pl-3 max-sm:flex-wrap max-md:[&_[data-control-primary]:not(:disabled):hover]:shadow-sm [&_[data-control-primary]:not(:disabled):active]:shadow-none [@media_(max-width:560px)]:pt-2.25 [@media_(max-width:560px)]:pr-3 [@media_(max-width:560px)]:pb-2.25 [@media_(max-width:560px)]:pl-3"
          data-issue-create-footer=""
        >
          <label
            className="inline-flex items-center gap-1.5 mt-0 mr-0 mb-0 ml-0 text-muted text-[12px] font-normal max-sm:text-xs flex-row w-auto min-h-7.5 whitespace-nowrap max-md:hover:text-foreground transition-colors duration-150 [@media_(max-width:600px)]:text-[11px] [@media_(hover:hover)_and_(pointer:fine)]:hover:text-foreground"
            data-issue-create-more=""
          >
            <input
              className="w-3.75 border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y h-3.75 mt-0 mr-0 mb-0 ml-0 flex-none max-md:text-base accent-accent placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
              type="checkbox"
              checked={createMore}
              onChange={(e) => setCreateMore(e.target.checked)}
            />{" "}
            Create more
          </label>
          <div
            className="flex items-center gap-2 ml-auto max-sm:justify-end"
            data-issue-create-footer-actions=""
          >
            <button
              type="button"
              className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.5 pr-2.5 pt-1.25 pb-1.25 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 min-h-7.5 text-[12px] max-md:hover:bg-subtle hover:border-accent/25 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [@media_(hover:hover)_and_(pointer:fine)]:hover:bg-subtle"
              data-control-outline=""
              onClick={requestClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              className="inline-flex min-h-7.5 items-center justify-center gap-1.75 rounded-[20px] bg-accent pl-2.5 pr-2.5 pt-1.25 pb-1.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 text-[12px] disabled:opacity-55 disabled:cursor-not-allowed disabled:shadow-none disabled:transform-none disabled:filter-none enabled:hover:-translate-y-px enabled:hover:filter-none enabled:active:translate-y-0 enabled:active:scale-100 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.55] disabled:shadow-[none]"
              data-control-primary=""
              disabled={
                busy || (loadingOptions && !meta) || !title.trim() || !team
              }
            >
              {busy ? (
                <>
                  <span
                    className="inline-block w-3 h-3 mr-0.5 rounded-[50%] border-2 border-white/50 border-t-white animate-[issue-spin_.7s_linear_infinite]"
                    data-issue-create-spinner=""
                    aria-hidden="true"
                  />
                  {"Creating…"}
                </>
              ) : (
                "Create task"
              )}
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <>
    <App />
    <DialogHost />
    <ReactGrabDev />
  </>,
);
const PROJECT_STATES = [
  "backlog",
  "planned",
  "started",
  "paused",
  "completed",
  "canceled",
];
function ProjectsPage({
  projects: given,
  onOpen,
  onCreate,
  hasMore = false,
  onLoadMore,
  loading = false,
  error = "",
  teamId,
}: {
  projects: Project[];
  onOpen: (p: Project) => void;
  onCreate: () => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
  loading?: boolean;
  error?: string;
  teamId?: string;
}) {
  const [search, setSearch] = useState(""),
    [state, setState] = useState(""),
    [team, setTeam] = useState(""),
    [layout, setLayout] = useState<"List" | "Board" | "Timeline">("List");
  const [filterOpen, setFilterOpen] = useState(false),
    [displayOpen, setDisplayOpen] = useState(false),
    [group, setGroup] = useState("No grouping"),
    [ordering, setOrdering] = useState("Name"),
    [showClosed, setShowClosed] = useState(true),
    [summary, setSummary] = useState(false);
  // Rendered from the local store: sync metadata, extra pages, pending creates and optimistic edits.
  const all = useProjects(given);
  const projects = useMemo(
    () =>
      teamId
        ? all.filter((p) => p.teams?.nodes.some((t) => t.id === teamId))
        : all,
    [all, teamId],
  );
  const openIssues = useProjectIssueIndex().open;
  const notice = useProjectNotice();
  const states = useMemo(
    () =>
      [
        ...new Set(
          projects.map((p) => p.state).filter((v): v is string => !!v),
        ),
      ].sort(),
    [projects],
  );
  const teams = useMemo(
    () =>
      [
        ...new Map(
          projects.flatMap((p) => p.teams?.nodes || []).map((t) => [t.key, t]),
        ).values(),
      ].sort((a, b) => a.name.localeCompare(b.name)),
    [projects],
  );
  const normalized = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      projects
        .filter(
          (p) =>
            (!normalized ||
              [
                p.name,
                p.description,
                p.state,
                ...(p.teams?.nodes || []).flatMap((t) => [t.name, t.key]),
              ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase()
                .includes(normalized)) &&
            (!state || p.state === state) &&
            (!team || p.teams?.nodes.some((t) => t.key === team)) &&
            (showClosed || !["completed", "canceled"].includes(p.state || "")),
        )
        .sort((a, b) =>
          ordering === "Target date"
            ? (a.targetDate || "9999").localeCompare(b.targetDate || "9999") ||
              a.name.localeCompare(b.name)
            : ordering === "Status"
              ? (a.state || "").localeCompare(b.state || "") ||
                a.name.localeCompare(b.name)
              : a.name.localeCompare(b.name),
        ),
    [projects, normalized, state, team, showClosed, ordering],
  );
  const clear = () => {
    setSearch("");
    setState("");
    setTeam("");
    setShowClosed(true);
  };
  const title = (v: string) =>
    (
      ({
        started: "In Progress",
        backlog: "Backlog",
        planned: "Planned",
        completed: "Completed",
        canceled: "Canceled",
        paused: "Paused",
      }) as Record<string, string>
    )[v] || v;
  const columns =
    group === "Status"
      ? PROJECT_STATES.map((key) => ({ key, label: title(key) }))
      : group === "Team"
        ? [
            ...teams.map((t) => ({ key: t.key, label: t.name })),
            { key: "none", label: "No team" },
          ]
        : [{ key: "all", label: "All projects" }];
  // One pass buckets every visible project into its columns (no per-column rescans).
  const buckets = useMemo(() => {
    const byState = new Map<string, Project[]>(),
      byGroup = new Map<string, Project[]>();
    const add = (m: Map<string, Project[]>, k: string, p: Project) => {
      const l = m.get(k);
      if (l) l.push(p);
      else m.set(k, [p]);
    };
    for (const p of visible) {
      add(byState, p.state || "", p);
      if (group === "Status") add(byGroup, p.state || "", p);
      else if (group === "Team") {
        if (!p.teams?.nodes.length) add(byGroup, "none", p);
        else
          for (const t of new Set(p.teams.nodes.map((t) => t.key)))
            add(byGroup, t, p);
      } else add(byGroup, "all", p);
    }
    return { byState, byGroup };
  }, [visible, group]);
  const inColumn = (key: string) => buckets.byGroup.get(key) || [];
  const row = (p: Project) => {
    const count = openIssues.get(p.id) || 0;
    return (
      <button
        type="button"
        className="border-b border-line grid items-center gap-1.5 pt-0 pr-4.25 pb-0 pl-4.25 min-h-12 bg-transparent w-full text-left text-[13px] h-12 border-0 font-normal hover:bg-subtle shell-phone:min-w-96 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:w-[max(620px,100%)] shell-phone:min-w-175 grid-cols-[minmax(0,4.3fr)_minmax(52px,.48fr)_minmax(90px,1fr)_minmax(84px,.9fr)_minmax(42px,.45fr)_minmax(104px,1fr)]"
        data-projects-table-row=""
        key={p.id}
        onClick={() => onOpen(p)}
        disabled={isPendingProject(p)}
        aria-busy={isPendingProject(p) || undefined}
        aria-label={`Open project ${p.name}`}
      >
        <span
          className="flex items-center gap-2.5 overflow-hidden min-w-0 text-ellipsis whitespace-nowrap"
          data-projects-table-name=""
        >
          <span
            className="grid place-items-center w-4.5 h-4.5 rounded-lg [background:none] text-muted font-bold last:grid last:gap-0.5 last:min-w-0 flex-none border-0 last:gap-0.75"
            data-project-mark=""
            data-project-list-icon=""
            aria-hidden="true"
          >
            <FolderKanban size={15} />
          </span>
          <span className="last:grid last:gap-0.5 last:min-w-0 last:gap-0.75">
            <strong className="overflow-hidden text-ellipsis font-medium">
              {p.name}
            </strong>
            {summary && p.description && (
              <small className="overflow-hidden text-ellipsis text-muted">
                {p.description}
              </small>
            )}
          </span>
        </span>
        <span
          className="text-[19px] text-muted leading-none min-w-0 overflow-hidden text-ellipsis whitespace-nowrap"
          aria-label={p.health || "No health updates"}
          title={p.health || "No health updates"}
        >
          {p.health ? "◉" : "◌"}
        </span>
        <span className="text-muted min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
          {p.lead?.name || "—"}
        </span>
        <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
          {p.targetDate || "—"}
        </span>
        <span
          className="text-center text-muted min-w-0 overflow-hidden text-ellipsis whitespace-nowrap"
          aria-label={`${count} open task${count === 1 ? "" : "s"}`}
          title={`${count} open task${count === 1 ? "" : "s"}`}
        >
          {count}
        </span>
        <span className="flex items-center gap-1.75 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
          <span
            className="w-3 h-3 rounded-[50%] data-[state=started]:border-line data-[state=completed]:border-line border-2 border-dotted border-orange-400"
            data-projects-status-dot=""
            data-state={p.state || "backlog"}
          />
          {Math.round(p.progress || 0)}%
        </span>
      </button>
    );
  };
  return (
    <section
      className="max-w-none mt-0 mr-auto mb-0 ml-auto min-w-0 w-full overflow-x-hidden relative text-[12px] leading-normal font-['Inter_Variable',Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif] min-h-[calc(100dvh-16px)] [font-optical-sizing:auto]"
      data-projects-page=""
      data-projects-reference=""
      aria-labelledby="projects-title"
    >
      <header
        className="border-b border-line h-11.25 min-h-11.25 flex items-center justify-between gap-2.5 pt-0 pr-4.5 pb-0 pl-4.5 leading-normal shell-phone:pt-0 shell-phone:pr-3 shell-phone:pb-0 shell-phone:pl-3"
        data-projects-reference-head=""
      >
        <h1
          className="mt-0 mr-0 mb-0 ml-0 text-(length:--hoja-title-size) font-(--hoja-title-weight) tracking-normal leading-[normal] "
          id="projects-title"
        >
          Projects
        </h1>
        <button
          className="inline-flex items-center gap-1.5 pt-0 pr-3 pb-0 pl-3 rounded-[7px] bg-panel text-[12px] hover:bg-subtle h-7.5 border border-line text-foreground font-[550] cursor-pointer max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
          type="button"
          onClick={onCreate}
        >
          <Plus size={15} /> New project
        </button>
      </header>
      <div
        className="h-11 min-w-0 flex items-center gap-1.25 pt-0 pr-2.25 pb-0 pl-2.25 overflow-x-auto justify-between border-line [&::-webkit-scrollbar]:hidden border-b-0 scrollbar-none"
        data-projects-reference-tabs=""
      >
        <span
          className="inline-flex items-center h-7 pt-0 pr-2.75 pb-0 pl-2.75 border border-line rounded-[99px] bg-subtle text-[12px] font-medium text-foreground"
          aria-current="page"
        >
          All projects
        </span>
        <div
          className="flex gap-1.25 items-center"
          data-projects-reference-actions=""
        >
          <UIPopover
            open={filterOpen}
            onOpenChange={(open) => {
              setFilterOpen(open);
              if (open) setDisplayOpen(false);
            }}
          >
            <PopoverTrigger asChild>
              <button
                className="inline-flex min-h-7 min-w-7 flex-none items-center justify-center gap-1.5 rounded-[99px] border border-line bg-transparent pl-2.75 pr-2.75 pt-0 pb-0 text-[12px] text-muted hover:bg-subtle aria-expanded:bg-accent-soft aria-expanded:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11 hover:text-foreground"
                type="button"
                aria-label="Add filter"
                aria-expanded={filterOpen}
              >
                <ListFilter size={15} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              sideOffset={6}
              className="z-50 w-auto border shadow-(--shadow) pt-2 pr-2 pb-2 pl-2 border-line rounded-[9px] bg-panel grid gap-2.25 text-[12px] w-[min(260px,calc(100vw-24px))]"
              data-projects-reference-menu=""
              aria-label="Project filters"
            >
              <label className="grid gap-1.25">
                Search
                <input
                  className="shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  type="search"
                  aria-label="Search projects"
                  value={search}
                  placeholder="Search projects"
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <label className="grid gap-1.25">
                Status
                <select
                  className="shell-phone:text-base border border-line rounded-[5px] bg-panel text-foreground pt-1.5 pr-1.5 pb-1.5 pl-1.5 w-full max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  aria-label="Filter projects by status"
                  value={state}
                  onChange={(e) => setState(e.target.value)}
                >
                  <option value="">All statuses</option>
                  {states.map((x) => (
                    <option key={x} value={x}>
                      {title(x)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.25">
                Team
                <select
                  className="shell-phone:text-base border border-line rounded-[5px] bg-panel text-foreground pt-1.5 pr-1.5 pb-1.5 pl-1.5 w-full max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  aria-label="Filter projects by team"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                >
                  <option value="">All teams</option>
                  {teams.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={clear}
                className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              >
                Clear filters
              </button>
            </PopoverContent>
          </UIPopover>
          <UIPopover
            open={displayOpen}
            onOpenChange={(open) => {
              setDisplayOpen(open);
              if (open) setFilterOpen(false);
            }}
          >
            <PopoverTrigger asChild>
              <button
                className="inline-flex min-h-7 min-w-7 flex-none items-center justify-center gap-1.5 rounded-[99px] border border-line bg-transparent pl-2.75 pr-2.75 pt-0 pb-0 text-[12px] text-muted hover:bg-subtle aria-expanded:bg-accent-soft aria-expanded:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11 hover:text-foreground"
                type="button"
                aria-label="Display options"
                aria-expanded={displayOpen}
              >
                <MoreHorizontal size={16} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              sideOffset={6}
              className="z-50 w-auto border shadow-(--shadow) pt-2 pr-2 pb-2 pl-2 border-line rounded-[9px] bg-panel grid gap-2.25 text-[12px] w-[min(260px,calc(100vw-24px))]"
              data-projects-reference-menu=""
              aria-label="Project display options"
            >
              <div
                className="flex gap-1"
                role="tablist"
                aria-label="Project layout"
              >
                {(["List", "Board", "Timeline"] as const).map((x) => (
                  <button
                    className="inline-flex h-8 min-h-7 flex-none items-center rounded-full border border-line bg-panel px-3 text-sm text-inherit transition-none aria-selected:bg-subtle aria-selected:text-foreground aria-selected:font-semibold focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    key={x}
                    type="button"
                    role="tab"
                    aria-selected={layout === x}
                    onClick={() => setLayout(x)}
                  >
                    {x}
                  </button>
                ))}
              </div>
              <label className="grid gap-1.25">
                Grouping
                <select
                  className="shell-phone:text-base border border-line rounded-[5px] bg-panel text-foreground pt-1.5 pr-1.5 pb-1.5 pl-1.5 w-full max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  aria-label="Change grouping"
                  value={group}
                  onChange={(e) => setGroup(e.target.value)}
                >
                  <option>No grouping</option>
                  <option>Status</option>
                  <option>Team</option>
                </select>
              </label>
              <label className="grid gap-1.25">
                Ordering
                <select
                  className="shell-phone:text-base border border-line rounded-[5px] bg-panel text-foreground pt-1.5 pr-1.5 pb-1.5 pl-1.5 w-full max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  aria-label="View ordering"
                  value={ordering}
                  onChange={(e) => setOrdering(e.target.value)}
                >
                  <option>Name</option>
                  <option>Status</option>
                  <option>Target date</option>
                </select>
              </label>
              <label className="grid gap-1.25">
                <input
                  className="shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
                  type="checkbox"
                  checked={showClosed}
                  onChange={(e) => setShowClosed(e.target.checked)}
                />{" "}
                Show closed projects
              </label>
              <label className="grid gap-1.25">
                <input
                  className="shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
                  type="checkbox"
                  checked={summary}
                  onChange={(e) => setSummary(e.target.checked)}
                />{" "}
                Summary
              </label>
              <p className="mt-0 mr-0 mb-0 ml-0 text-muted">
                Only available project fields can be displayed; manual ordering
                and external data are not available.
              </p>
            </PopoverContent>
          </UIPopover>
        </div>
      </div>

      {notice && (
        <p
          role="alert"
          className="pt-12.5 pr-5 pb-12.5 pl-5 text-center text-muted text-[13px]"
        >
          {notice.message}{" "}
          {notice.retry && (
            <button
              type="button"
              onClick={notice.retry}
              className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={() => setProjectNotice(null)}
            className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          >
            Dismiss
          </button>
        </p>
      )}
      {error && (
        <p
          role="alert"
          className="pt-12.5 pr-5 pb-12.5 pl-5 text-center text-muted text-[13px]"
        >
          {error}{" "}
          <button
            type="button"
            onClick={onLoadMore}
            className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          >
            Retry
          </button>
        </p>
      )}
      {layout === "Timeline" ? (
        <div className="pt-12.5 pr-5 pb-12.5 pl-5 text-center text-muted text-[13px]">
          <h2>Timeline</h2>
          <p>
            Project date ranges and timeline controls are not available in Hoja
            yet. Switch to List or Board to see the same projects.
          </p>
        </div>
      ) : visible.length ? (
        layout === "Board" ? (
          <div
            className="min-w-0 w-full flex gap-3 overflow-x-auto overflow-y-hidden pt-3 pr-3 pb-3 pl-3 shell-phone:pt-2.5 shell-phone:pr-2.5 shell-phone:pb-2.5 shell-phone:pl-2.5 overscroll-x-contain"
            data-projects-board=""
          >
            {(group === "No grouping"
              ? PROJECT_STATES.map((key) => ({ key, label: title(key) }))
              : columns
            ).map((col) => {
              const cards =
                group === "No grouping"
                  ? buckets.byState.get(col.key) || []
                  : inColumn(col.key);
              return (
                <section
                  className="border flex min-w-0 flex-col gap-2 pt-2.5 pr-2.5 pb-2.5 pl-2.5 border-line rounded-[9px] bg-[color-mix(in_srgb,var(--subtle)_55%,var(--bg))] flex-[0_0_min(280px,80vw)] shell-phone:basis-[min(280px,calc(100vw-48px))]"
                  data-projects-board-column=""
                  key={col.key}
                >
                  <h2 className="mt-0 mr-0 mb-1 ml-0 text-[12px]">
                    {col.label}{" "}
                    <span className="text-muted font-normal">
                      {cards.length}
                    </span>
                  </h2>
                  {cards.map((p) => (
                    <button
                      type="button"
                      className="border shadow-[0_1px_2px_#1b1b1b08] min-w-0 pt-3 pr-3 pb-3 pl-3 border-line rounded-lg bg-panel grid gap-2 w-full text-left text-[12px] hover:border-accent max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-projects-board-card=""
                      key={p.id}
                      onClick={() => onOpen(p)}
                      disabled={isPendingProject(p)}
                      aria-busy={isPendingProject(p) || undefined}
                    >
                      <strong className="min-w-0 wrap-break-word">
                        {p.name}
                      </strong>
                      {summary && p.description && (
                        <small className="min-w-0 wrap-break-word text-muted">
                          {p.description}
                        </small>
                      )}
                      <span className="min-w-0 wrap-break-word text-muted">
                        {p.lead?.name || "No lead"} ·{" "}
                        {Math.round(p.progress || 0)}%
                      </span>
                    </button>
                  ))}
                </section>
              );
            })}
          </div>
        ) : (
          columns.map((col) => {
            const rows = inColumn(col.key);
            return (
              <section
                className="w-full min-w-0 border-collapse table-fixed text-[12px] shell-phone:block shell-phone:overflow-x-auto shell-phone:whitespace-nowrap"
                data-projects-table=""
                key={col.key}
              >
                {group !== "No grouping" && (
                  <h2 className="mt-0 mr-0 mb-0 ml-0 pt-3 pr-4 pb-3 pl-4 border-b border-line text-[12px]">
                    {col.label}{" "}
                    <span className="text-muted font-normal">
                      {rows.length}
                    </span>
                  </h2>
                )}
                <div
                  className="border-b border-line grid items-center gap-1.5 pt-0 pr-4.25 pb-0 pl-4.25 min-h-8.75 text-muted text-[12px] w-full text-left border-0 font-medium shell-phone:min-w-96 shell-phone:w-[max(620px,100%)] shell-phone:min-w-175 grid-cols-[minmax(0,4.3fr)_minmax(52px,.48fr)_minmax(90px,1fr)_minmax(84px,.9fr)_minmax(42px,.45fr)_minmax(104px,1fr)]"
                  data-projects-table-head=""
                >
                  <span>Name</span>
                  <span>Health</span>
                  <span>Lead</span>
                  <span>Target date</span>
                  <span>Tasks</span>
                  <span>Status</span>
                </div>
                {rows.map(row)}
              </section>
            );
          })
        )
      ) : (
        <div
          className="grid justify-items-center text-center pt-18 pr-5 pb-18 pl-5 rounded-[9px] text-muted border border-dashed border-line"
          data-empty-projects=""
          role="status"
        >
          <FolderKanban size={28} />
          <h2 className="text-foreground text-[17px] mt-3.5 mr-0 mb-1 ml-0">
            {projects.length ? "No matching projects" : "No projects yet"}
          </h2>
          <p className="mt-0 mr-0 mb-4 ml-0">
            {projects.length
              ? "Try broader filters or show closed projects."
              : "Create a project to organize work across teams."}
          </p>
          {projects.length ? (
            <button
              className="h-7.5 pt-0 pr-3 pb-0 pl-3 border border-line rounded-[7px] bg-panel text-foreground text-[12px] font-[550] cursor-pointer inline-flex items-center gap-1.5 hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
              type="button"
              onClick={clear}
            >
              Clear filters
            </button>
          ) : (
            <button
              className="h-7.5 pt-0 pr-3 pb-0 pl-3 border border-line rounded-[7px] bg-panel text-foreground text-[12px] font-[550] cursor-pointer inline-flex items-center gap-1.5 hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [font:inherit]"
              type="button"
              onClick={onCreate}
            >
              New project
            </button>
          )}
        </div>
      )}
      {hasMore && (
        <button
          type="button"
          className="border block mt-5 mr-auto mb-5 ml-auto pt-2 pr-3 pb-2 pl-3 border-line rounded-[5px] bg-panel max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          onClick={onLoadMore}
          disabled={loading}
        >
          {loading ? "Loading…" : "Load more projects"}
        </button>
      )}
    </section>
  );
}
function ProjectDetails({
  project: given,
  onBack,
  onSaved,
  onOpenIssue,
  onNewView,
  teams,
  projects,
  layout,
  onLayoutChange,
  onMoved,
  onUpdate,
  onNew,
}: {
  project: Project;
  issues: Issue[];
  onNewView: () => void;
  onBack: () => void;
  onSaved: (p: Project) => void;
  onOpenIssue: (issue: Issue) => void;
  revision: number;
  teams: Team[];
  projects: Project[];
  layout: IssueLayout;
  onLayoutChange: (layout: IssueLayout) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  onNew: (stateId?: string) => void;
}) {
  // The project and its issues come from the local store; only project updates and older closed issues are fetched.
  const project = useProject(given);
  const meta = useProjectSyncMeta();
  const members = useMemberOptions();
  const [tab, setTab] = useState<"Overview" | "Activity" | "Issues">(() =>
    location.pathname.endsWith("/activity")
      ? "Activity"
      : location.pathname.endsWith("/issues")
        ? "Issues"
        : "Overview",
  );
  useEffect(() => {
    const onPop = () =>
      setTab(
        location.pathname.endsWith("/activity")
          ? "Activity"
          : location.pathname.endsWith("/issues")
            ? "Issues"
            : "Overview",
      );
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  // Store rows only; closed issues older than the synced window are loaded by the list when it shows them all.
  const related = useProjectIssues(project.id, gql, issueFields, false).issues;
  const olderClosed = useOlderClosed("project", project.id);
  const projectStates = useMemo(() => {
    const ids = new Set((project.teams?.nodes || []).map((t) => t.id));
    const scoped = teams.filter((t) => ids.has(t.id));
    return [
      ...new Map(
        (scoped.length ? scoped : teams)
          .flatMap((t) => t.states.nodes)
          .map((st) => [stateKey(st), st]),
      ).values(),
    ];
  }, [project.teams, teams]);
  const updatesKey = `projectUpdates:${project.id}:${meta?.viewer.id || ""}`;
  const updatesVersion = useProjectEntityVersion(["projectUpdate"]);
  const fetchUpdates = React.useCallback(
    (first: number, after: string | null) =>
      gql(
        `query ProjectActivity($first:Int,$after:String,$filter:ProjectUpdateFilter){projectUpdates(first:$first,after:$after,filter:$filter){nodes{id body status createdAt actor{id name avatarSeed} progress project{id name}} pageInfo{hasNextPage endCursor}}}`,
        { first, after, filter: { project: { id: { eq: project.id } } } },
      ).then((d) => d.projectUpdates as PageOf<PulsePost>),
    [project.id],
  );
  const activity = usePagedCache<PulsePost>(
    tab === "Activity" || tab === "Overview" ? updatesKey : null,
    fetchUpdates,
    updatesVersion,
    20,
    "Project update",
  );
  const updates = activity.rows;
  const summary = useMemo(() => {
    const counts = new Map<string, number>();
    let done = 0,
      canceled = 0;
    for (const i of related) {
      const t = i.state?.type;
      if (t === "completed") done++;
      else if (t === "canceled") canceled++;
      const k = i.state ? stateKey(i.state) : "";
      counts.set(k, (counts.get(k) || 0) + 1);
    }
    const scope = related.length - canceled;
    const byStatus = projectStates
      .filter((st) => counts.get(stateKey(st)))
      .map((st) => ({ state: st, count: counts.get(stateKey(st)) || 0 }));
    const none = counts.get("") || 0;
    return {
      total: related.length,
      done,
      canceled,
      open: scope - done,
      pct: Math.round(project.progress ?? (scope ? (done / scope) * 100 : 0)),
      byStatus,
      none,
    };
  }, [related, projectStates, project.progress]);
  const userNames = useMemo(
    () =>
      new Map(
        (meta?.users || []).map(([id, name]) => [id, name || ""]) as [
          string,
          string,
        ][],
      ),
    [meta],
  );
  const goTab = (v: "Overview" | "Activity" | "Issues") => {
    history.pushState(
      {},
      "",
      `/workspace/project/${project.id}/${v.toLowerCase()}`,
    );
    setTab(v);
  };
  const [detailMenu, setDetailMenu] = useState(false);
  const [propsOpen, setPropsOpen] = useState(() => {
    try {
      return localStorage.getItem("hoja_project_properties_open") === "true";
    } catch {
      return false;
    }
  });
  const togglePropsOpen = () =>
    setPropsOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("hoja_project_properties_open", String(next));
      } catch {
        /* ignore storage failures */
      }
      return next;
    });
  const [editOpen, setEditOpen] = useState(false);
  const leadIdOf = (p: Project) => {
    const matches = members.filter((m) => m.name === p.lead?.name);
    return matches.length === 1 ? matches[0].id : "";
  };
  const currentLead = useMemo(
    () => leadIdOf(project),
    [project.lead?.name, members], // eslint-disable-line react-hooks/exhaustive-deps -- leadIdOf is rebuilt each render
  );
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || "");
  const [state, setState] = useState(project.state || "backlog");
  const [leadId, setLeadId] = useState(currentLead);
  const [startDate, setStartDate] = useState(project.startDate || "");
  const [targetDate, setTargetDate] = useState(project.targetDate || "");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  type Draft = {
    name: string;
    description: string;
    state: string;
    leadId: string;
    startDate: string;
    targetDate: string;
  };
  const restoreDraft = useRef<Draft | null>(null);
  const applyDraft = useCallback((d: Draft) => {
    setName(d.name);
    setDescription(d.description);
    setState(d.state);
    setLeadId(d.leadId);
    setStartDate(d.startDate);
    setTargetDate(d.targetDate);
  }, []);
  useEffect(() => {
    const draft = restoreDraft.current;
    restoreDraft.current = null;
    applyDraft(
      draft || {
        name: project.name,
        description: project.description || "",
        state: project.state || "backlog",
        leadId: currentLead,
        startDate: project.startDate || "",
        targetDate: project.targetDate || "",
      },
    );
  }, [
    project.id,
    project.name,
    project.description,
    project.state,
    project.startDate,
    project.targetDate,
    currentLead,
    applyDraft,
  ]);
  const cancelEdit = () => {
    applyDraft({
      name: project.name,
      description: project.description || "",
      state: project.state || "backlog",
      leadId: currentLead,
      startDate: project.startDate || "",
      targetDate: project.targetDate || "",
    });
    setStatus("");
  };
  const changed =
    name !== project.name ||
    description !== (project.description || "") ||
    state !== (project.state || "backlog") ||
    leadId !== currentLead ||
    startDate !== (project.startDate || "") ||
    targetDate !== (project.targetDate || "");
  const datesInvalid = Boolean(
    startDate && targetDate && targetDate < startDate,
  );
  const update = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!changed || !name.trim() || datesInvalid) return;
    const input: Record<string, unknown> = {},
      patch: Partial<Project> = {};
    if (name !== project.name) {
      input.name = name;
      patch.name = name;
    }
    if (description !== (project.description || "")) {
      input.description = description;
      patch.description = description;
    }
    if (state !== (project.state || "backlog")) {
      input.state = state;
      patch.state = state;
    }
    if (leadId !== currentLead) {
      input.leadId = leadId || null;
      patch.lead = leadId
        ? { name: members.find((m) => m.id === leadId)?.name }
        : undefined;
    }
    if (startDate !== (project.startDate || "")) {
      input.startDate = startDate || null;
      patch.startDate = startDate || undefined;
    }
    if (targetDate !== (project.targetDate || "")) {
      input.targetDate = targetDate || null;
      patch.targetDate = targetDate || undefined;
    }
    const draft: Draft = {
      name,
      description,
      state,
      leadId,
      startDate,
      targetDate,
    };
    // Optimistic: the page shows the edit at once; a failure restores the server values and keeps the draft in the form.
    const settle = patchProject(project.id, patch);
    setStatus("Saving…");
    try {
      const d = await gql(
        `mutation($id:String!,$input:ProjectUpdateInput!){projectUpdate(id:$id,input:$input){success project{${PROJECT_FIELDS}}}}`,
        { id: project.id, input },
      );
      if (!d.projectUpdate?.success || !d.projectUpdate.project)
        throw Error("Project update failed");
      settle(true, d.projectUpdate.project);
      onSaved(d.projectUpdate.project);
      setStatus("Project saved.");
    } catch (err) {
      restoreDraft.current = draft;
      settle(false);
      applyDraft(draft);
      setStatus((err as Error).message);
    }
  };
  const publish = async (body: string, health: string) => {
    const temp: PulsePost = {
      id: `optimistic:${crypto.randomUUID()}`,
      body,
      status: health,
      createdAt: new Date().toISOString(),
      progress: project.progress,
      actor: { id: meta?.viewer.id || "", name: meta?.viewer.name },
      project: {
        id: project.id,
        name: project.name,
        health: project.health,
        progress: project.progress,
        targetDate: project.targetDate,
        lead: project.lead,
      },
    };
    const feeds = [updatesKey, pulseKey(meta)]
      .filter((k): k is string => !!k && (k === updatesKey || hasPage(k)))
      .map((k) => optimisticPage<PulsePost>(k, (r) => [temp, ...r]));
    try {
      const d = await gql(
        `mutation($input:ProjectUpdateCreateInput!){projectUpdateCreate(input:$input){success projectUpdate{${PULSE_FIELDS}}}}`,
        { input: { projectId: project.id, body, status: health } },
      );
      if (
        !d.projectUpdateCreate.success ||
        !d.projectUpdateCreate.projectUpdate
      )
        throw Error("Could not publish update.");
      const created: PulsePost = d.projectUpdateCreate.projectUpdate;
      for (const settle of feeds) settle((r) => upsertFirst(r, created));
    } catch (err) {
      for (const settle of feeds) settle();
      throw err;
    }
  };
  const archive = async () => {
    if (busy) return;
    setBusy(true);
    const settle = hideProject(project.id);
    const label = project.name;
    setConfirmArchive(false);
    setProjectNotice(null);
    onBack();
    try {
      const d = await gql(
        `mutation($id:String!){projectArchive(id:$id){success}}`,
        { id: project.id },
      );
      if (!d.projectArchive?.success) throw Error("Archive failed");
      settle(true);
    } catch (err) {
      settle(false);
      setProjectNotice({
        message: `Could not archive ${label}: ${(err as Error).message}`,
      });
    }
  };
  return (
    <section
      className="max-w-none mt-0 mr-auto mb-0 ml-auto min-w-0 w-full overflow-x-hidden text-[12px] leading-normal shell-phone:flex shell-phone:flex-col font-['Inter_Variable',Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif] [font-optical-sizing:auto] shell-desktop:[&:has(>[data-project-reference-properties])]:grid-cols-[minmax(0,1fr)_minmax(260px,38%)]"
      data-project-detail=""
      data-project-reference=""
    >
      <div
        className="border-b border-line h-11.25 min-h-11.25 flex items-center justify-between gap-2.5 pt-0 pr-4.5 pb-0 pl-4.5 relative shell-phone:order-0 md:col-span-full leading-normal shell-phone:pt-0 shell-phone:pr-3 shell-phone:pb-0 shell-phone:pl-3"
        data-project-reference-head=""
      >
        <button
          className="flex-none pt-1.25 pr-1.25 pb-1.25 pl-1.25 rounded-[5px] bg-transparent text-[12px] text-muted max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          data-back-link=""
          onClick={onBack}
        >
          ← Projects
        </button>
        <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[12px] shell-phone:max-w-36 shell-phone:max-w-37.5">
          {project.name}
        </span>
        <FollowButton
          type="project"
          id={project.id}
          onError={(m) => setStatus(m)}
        />
        {tab === "Overview" && (
          <button
            className={`flex-none inline-flex items-center gap-1.5 pt-1.25 pr-2 pb-1.25 pl-2 rounded-[5px] border border-line text-[12px] max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 ${
              propsOpen
                ? "bg-subtle text-foreground font-medium"
                : "bg-panel text-muted hover:bg-subtle hover:text-foreground"
            }`}
            type="button"
            aria-expanded={propsOpen}
            aria-label={
              propsOpen ? "Hide project properties" : "Show project properties"
            }
            onClick={togglePropsOpen}
          >
            <SlidersHorizontal size={13} aria-hidden="true" />
            Properties
          </button>
        )}
        <button
          className="flex-none pt-1.25 pr-1.25 pb-1.25 pl-1.25 rounded-[5px] bg-transparent text-[12px] max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          aria-label="Copy page URL"
          onClick={() => {
            void navigator.clipboard
              .writeText(location.href)
              .then(() => setStatus("Project URL copied."))
              .catch(() => setStatus("Could not copy project URL."));
          }}
        >
          Copy URL
        </button>
        <UIPopover open={detailMenu} onOpenChange={setDetailMenu}>
          <PopoverTrigger asChild>
            <button
              className="flex-none pt-1.25 pr-1.25 pb-1.25 pl-1.25 rounded-[5px] bg-transparent text-[12px] max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group"
              type="button"
              aria-label="Project actions"
              aria-expanded={detailMenu}
            >
              <MoreHorizontal size={17} />
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            sideOffset={6}
            className="z-50 w-auto pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-[7px] bg-panel shadow-(--shadow)"
            data-project-reference-menu=""
          >
            <button
              className="bg-transparent pt-1.75 pr-2.5 pb-1.75 pl-2.5 max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="button"
              onClick={() => {
                setConfirmArchive(true);
                setDetailMenu(false);
              }}
            >
              Archive project
            </button>
          </PopoverContent>
        </UIPopover>
      </div>

      <nav
        className="h-11 min-w-0 flex items-center gap-1.25 pt-0 pr-3 pb-0 pl-3 overflow-x-auto border-line [&::-webkit-scrollbar]:hidden border-b-0 md:col-span-full scrollbar-none"
        data-project-reference-tabs=""
        aria-label="Project views"
      >
        {(["Overview", "Activity", "Issues"] as const).map((v) => (
          <button
            className="h-7 flex-none inline-flex items-center gap-1.5 pt-0 pr-2.75 pb-0 pl-2.75 border border-line rounded-[99px] bg-panel text-muted no-underline text-[12px] aria-[current=page]:bg-subtle aria-[current=page]:text-foreground aria-[current=page]:font-semibold max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            key={v}
            type="button"
            aria-current={tab === v ? "page" : undefined}
            onClick={() => {
              history.pushState(
                {},
                "",
                `/workspace/project/${project.id}/${v.toLowerCase()}`,
              );
              setTab(v);
            }}
          >
            {displayLabel(v)}
          </button>
        ))}
      </nav>
      <button
        type="button"
        className="absolute right-4.75 top-13.25 z-2 grid place-items-center w-7 h-7 rounded-md bg-transparent text-muted hover:bg-subtle hover:text-foreground max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 md:col-start-2 md:row-start-2 md:justify-self-end shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:static shell-phone:right-auto shell-phone:top-auto shell-phone:mr-1"
        data-project-add-view=""
        aria-label="Add new view"
        onClick={onNewView}
      >
        <Plus size={15} />
      </button>
      {tab === "Overview" ? (
        <>
          <header className="flex items-center gap-3.5 mb-0 mt-15 block relative max-w-none mr-0 ml-14.5 pt-0 pr-0 pb-0 pl-0 border-0 max-lg:w-auto max-lg:mt-7 max-lg:mr-3.5 max-lg:mb-0 max-lg:ml-3.5 shell-phone:order-3 shell-phone:ml-3.5 shell-phone:mr-3.5 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:mt-7 [@media_(max-width:900px)]:mr-3.5 [@media_(max-width:900px)]:mb-0 [@media_(max-width:900px)]:ml-3.5 shell-phone:mt-4.5 shell-phone:mb-0 w-[calc(62%-87px)]">
            <span
              className="grid place-items-center w-9 h-9 rounded-lg bg-accent-soft text-accent font-bold text-[0] mb-3"
              data-project-mark=""
              aria-hidden="true"
            >
              <FolderKanban size={23} />
            </span>
            <div className="flex-1 max-w-full">
              <h1 className="text-[24px] tracking-tight mt-0 mr-0 mb-2 ml-0 shell-phone:text-2xl font-semibold leading-[1.33] shell-phone:mt-1.5 shell-phone:text-[23px] shell-phone:mt-1.75">
                {project.name}
              </h1>
            </div>
          </header>
          {propsOpen && (
            <div
              className="border absolute right-2.75 top-22.5 min-w-65 max-w-96.25 grid content-start gap-4 pt-4 pr-3.25 pb-4 pl-3.25 border-line rounded-[10px] bg-panel max-lg:static max-lg:w-auto max-lg:max-w-none max-lg:min-w-0 max-lg:mt-5 max-lg:mr-3.5 max-lg:mb-5 max-lg:ml-3.5 shell-phone:mt-0 shell-phone:mb-4 shell-phone:pt-3 shell-phone:pb-3 shell-phone:order-2 md:col-start-2 md:row-start-3 md:row-span-6 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:max-w-none [@media_(max-width:900px)]:min-w-0 [@media_(max-width:900px)]:mt-5 [@media_(max-width:900px)]:mr-3.5 [@media_(max-width:900px)]:mb-5 [@media_(max-width:900px)]:ml-3.5 shell-phone:mr-2.5 shell-phone:mb-4.5 shell-phone:ml-2.5 shell-phone:pt-3.25 shell-phone:pr-3.25 shell-phone:pb-3.25 shell-phone:pl-3.25 w-[calc(38%-11px)]"
              data-project-reference-properties=""
            >
              <h2 className="mt-0 mr-0 mb-0.5 ml-0 text-[12px] font-semibold">
                Properties
              </h2>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Status</span>
                <b className="text-[12px] font-medium wrap-break-word">
                  {(
                    {
                      started: "In Progress",
                      backlog: "Backlog",
                      planned: "Planned",
                      paused: "Paused",
                      completed: "Completed",
                      canceled: "Canceled",
                    } as Record<string, string>
                  )[project.state || "backlog"] || project.state}
                </b>
              </div>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Lead</span>
                <b className="text-[12px] font-medium wrap-break-word">
                  {project.lead?.name || "No lead"}
                </b>
              </div>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Members</span>
                <ProjectMembers projectId={project.id} people={members} />
              </div>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Start date</span>
                <b className="text-[12px] font-medium wrap-break-word">
                  {project.startDate || "Not set"}
                </b>
              </div>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Target date</span>
                <b className="text-[12px] font-medium wrap-break-word">
                  {project.targetDate || "Not set"}
                </b>
              </div>
              <div className="grid items-start gap-2 grid-cols-2">
                <span className="text-muted">Teams</span>
                <b className="text-[12px] font-medium wrap-break-word">
                  {project.teams?.nodes.map((t) => t.name).join(", ") ||
                    "No teams"}
                </b>
              </div>
              <ProjectRepository projectId={project.id} />
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 w-full text-[12px] max-w-full font-sans shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 group"
                data-control-outline=""
                aria-expanded={editOpen}
                onClick={() => setEditOpen((v) => !v)}
              >
                {editOpen ? "Close editor" : "Edit project"}
              </button>
            </div>
          )}
          {editOpen && (
            <form
              className="border shadow-(--ui-shadow-card) bg-panel border-line static top-auto left-auto w-[calc(62%-87px)] max-w-none mt-7 mr-auto mb-5 ml-10.75 pt-5.5 pr-6 pb-5.5 pl-6 overflow-visible shell-phone:pt-4 shell-phone:pr-4 shell-phone:pb-4 shell-phone:pl-4 relative max-lg:w-auto max-lg:mt-5 max-lg:mr-3.5 max-lg:mb-5 max-lg:ml-3.5 transform-none max-h-none rounded-(--ui-radius-card) shell-phone:order-6 shell-phone:order-7 md:col-start-1 md:row-start-7 shell-phone:pt-4.5 shell-phone:pb-4.5 shell-phone:ml-2.5 shell-phone:mr-2.5 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:mt-5 [@media_(max-width:900px)]:mr-3.5 [@media_(max-width:900px)]:mb-5 [@media_(max-width:900px)]:ml-3.5"
              data-project-editor=""
              data-ui-card=""
              onSubmit={update}
            >
              <div
                className="border-b border-line flex justify-between items-end gap-4 pb-4.25 shell-phone:items-start"
                data-project-editor-head=""
              >
                <div>
                  <span className="text-[10px] font-bold normal-case text-muted mt-0 mr-0 mb-0 ml-0 tracking-[.12em]">
                    Project properties
                  </span>
                  <h2 className="mt-1.25 mr-0 mb-0 ml-0 text-[16px] tracking-tight">
                    Edit project
                  </h2>
                </div>
                <span
                  className="text-muted text-[11px] text-right shell-phone:max-w-28 shell-phone:max-w-26.25"
                  data-control-muted=""
                >
                  Changes are saved explicitly
                </span>
              </div>
              <div
                className="grid pt-4.75 pr-0 pb-5.25 pl-0 shell-phone:gap-3.5 grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2 shell-phone:gap-3.75"
                data-project-editor-fields=""
              >
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold">
                  Name
                  <input
                    className="w-full min-w-0 min-h-10 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                  />
                </label>
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold col-span-full">
                  Description
                  <textarea
                    className="w-full min-w-0 min-h-32.5 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal leading-normal resize-y wrap-break-word shell-phone:text-base font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </label>
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold col-span-full">
                  State
                  <select
                    className="w-full min-w-0 min-h-10 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal shell-phone:text-base max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    value={state}
                    onChange={(e) => setState(e.target.value)}
                  >
                    {PROJECT_STATES.map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                </label>
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold col-span-full">
                  Lead
                  <select
                    className="w-full min-w-0 min-h-10 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal shell-phone:text-base max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    value={leadId}
                    onChange={(e) => setLeadId(e.target.value)}
                  >
                    <option value="">No lead</option>
                    {members.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold">
                  Start date
                  <input
                    className="w-full min-w-0 min-h-10 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </label>
                <label className="grid content-start gap-1.75 min-w-0 mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold">
                  Target date
                  <input
                    className="w-full min-w-0 min-h-10 mt-0 mr-0 mb-0 ml-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border border-line rounded-[7px] bg-bg text-foreground font-normal shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                    type="date"
                    value={targetDate}
                    min={startDate || undefined}
                    onChange={(e) => setTargetDate(e.target.value)}
                  />
                </label>
                <div
                  className="flex items-center gap-2.5 pt-0 shell-phone:flex-wrap shell-phone:max-w-none col-span-full"
                  data-project-editor-actions=""
                >
                  <button
                    className="inline-flex min-h-9.5 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:opacity-55 disabled:cursor-not-allowed whitespace-nowrap max-w-full font-sans shell-phone:flex-1 shell-phone:basis-32 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.55]"
                    data-control-primary=""
                    disabled={busy || !changed || !name.trim() || datesInvalid}
                  >
                    {busy ? "Saving…" : "Save changes"}
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:opacity-55 whitespace-nowrap min-h-9.5 max-w-full font-sans shell-phone:flex-1 shell-phone:basis-32 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.55]"
                    data-control-outline=""
                    onClick={cancelEdit}
                    disabled={busy || !changed}
                  >
                    Discard changes
                  </button>
                </div>
              </div>
              {status && (
                <p
                  className="mt-3 mr-0 mb-0 ml-0 text-muted text-[12px]"
                  role="status"
                >
                  {status}
                </p>
              )}
              <div
                className="border-t border-line flex items-center justify-between gap-4 mt-5.5 pt-4.75 shell-phone:items-start shell-phone:flex-col"
                data-project-editor-danger=""
              >
                <div>
                  <b className="text-[12px]">Archive project</b>
                  <p className="mt-1.25 mr-0 mb-0 ml-0 text-muted text-[11px]">
                    Hide this project from active lists. This can affect how
                    related work is found.
                  </p>
                </div>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-danger transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 flex-none shell-phone:w-full shell-phone:justify-center max-w-full font-sans shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-control-outline=""
                  onClick={() => setConfirmArchive(true)}
                  disabled={busy}
                >
                  Archive project
                </button>
              </div>
            </form>
          )}
          <section
            className="border min-w-0 mt-6 mr-3.5 mb-0 ml-3.5 pt-4 pr-4 pb-4 pl-4 border-line rounded-[10px] bg-panel shell-phone:order-4 md:col-start-1 md:row-start-4 shell-phone:mt-4.5 shell-phone:mr-2.5 shell-phone:mb-0 shell-phone:ml-2.5"
            data-project-overview-progress=""
            aria-labelledby="project-progress-title"
          >
            <header className="flex items-baseline justify-between gap-2.5">
              <h2
                className="mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold"
                id="project-progress-title"
              >
                Progress
              </h2>
              <b
                className="text-xl font-semibold tabular-nums"
                data-overview="progress"
              >
                {summary.pct}%
              </b>
            </header>
            <div
              className="h-1.5 mt-2.5 mr-0 mb-2 ml-0 rounded-[99px] bg-subtle overflow-hidden"
              data-project-progress-bar=""
              role="progressbar"
              aria-label="Project progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={summary.pct}
            >
              <i
                className="block h-full bg-accent rounded-[inherit] "
                style={{ width: `${summary.pct}%` }}
              />
            </div>
            <p className="mt-0 mr-0 mb-0 ml-0 text-muted text-[12px]">
              {summary.total
                ? `${summary.done} done · ${summary.open} open${summary.canceled ? ` · ${summary.canceled} canceled` : ""}`
                : "No tasks in this project yet"}
            </p>
            {summary.total > 0 && (
              <ul
                className="flex flex-wrap gap-1.5 mt-3 mr-0 mb-3 ml-0 pt-0 pr-0 pb-0 pl-0 list-none"
                data-project-status-counts=""
                aria-label="Tasks by status"
              >
                {summary.byStatus.map(({ state: st, count }) => (
                  <li key={st.id}>
                    <button
                      className="inline-flex items-center gap-1.5 min-h-7 pt-0 pr-2.5 pb-0 pl-2.5 border border-line rounded-[99px] bg-transparent text-foreground text-[12px] hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      type="button"
                      onClick={() => goTab("Issues")}
                      aria-label={`${count} ${st.name} tasks`}
                    >
                      <StatusIcon state={st} />
                      <span>{st.name}</span>
                      <b className="text-muted font-semibold tabular-nums">
                        {count}
                      </b>
                    </button>
                  </li>
                ))}
                {summary.none > 0 && (
                  <li>
                    <button
                      className="inline-flex items-center gap-1.5 min-h-7 pt-0 pr-2.5 pb-0 pl-2.5 border border-line rounded-[99px] bg-transparent text-foreground text-[12px] hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      type="button"
                      onClick={() => goTab("Issues")}
                    >
                      <span>No status</span>
                      <b className="text-muted font-semibold tabular-nums">
                        {summary.none}
                      </b>
                    </button>
                  </li>
                )}
              </ul>
            )}
            <button
              className="text-accent bg-transparent border-0 text-[11px] pt-0 pr-0 pb-0 pl-0 max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="button"
              data-text-action=""
              onClick={() => goTab("Issues")}
            >
              {summary.total
                ? `View all ${summary.total} tasks →`
                : "Open tasks →"}
            </button>
          </section>
          <section
            className="border min-w-0 mt-6 mr-3.5 mb-0 ml-3.5 pt-4 pr-4 pb-4 pl-4 border-line rounded-[10px] bg-panel shell-phone:mt-3 shell-phone:order-5 md:col-start-1 md:row-start-5 shell-phone:mr-2.5 shell-phone:mb-0 shell-phone:ml-2.5"
            data-project-overview-update=""
            aria-labelledby="project-update-title"
          >
            <header className="flex items-baseline justify-between gap-2.5">
              <h2
                className="mt-0 mr-0 mb-0 ml-0 text-[12px] font-semibold"
                id="project-update-title"
              >
                Latest update
              </h2>
              <button
                className="text-accent bg-transparent border-0 text-[11px] pt-0 pr-0 pb-0 pl-0 max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                data-text-action=""
                onClick={() => goTab("Activity")}
              >
                Write update
              </button>
            </header>
            {updates[0] ? (
              <article className="mt-2.5 text-[12px]">
                <strong>{updates[0].status || "Update"}</strong>
                <span
                  className="text-muted mt-0 mr-0 mb-0 ml-0 text-[12px]"
                  data-control-muted=""
                >
                  {" "}
                  · {updates[0].actor?.name || "Member"} ·{" "}
                  {updates[0].createdAt?.slice(0, 10)}
                </span>
                <Markdown text={updates[0].body} density="block" />
              </article>
            ) : activity.error ? (
              <p role="alert">
                {activity.error}{" "}
                <button
                  type="button"
                  onClick={activity.retry}
                  className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                >
                  Retry
                </button>
              </p>
            ) : (
              <p
                className="text-muted mt-0 mr-0 mb-0 ml-0 text-[12px]"
                data-control-muted=""
              >
                {activity.hasData
                  ? "No updates yet. Post one to tell the team how the project is going."
                  : "Loading updates…"}
              </p>
            )}
          </section>
          <section
            className="border-t border-line mt-9 mr-auto mb-0 ml-10.75 pt-3.5 pr-3.75 pb-3.5 pl-3.75 max-lg:w-auto max-lg:ml-3.5 max-lg:mr-3.5 shell-phone:mb-6 shell-phone:order-5 shell-phone:order-6 md:col-start-1 md:row-start-6 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:ml-3.5 [@media_(max-width:900px)]:mr-3.5 shell-phone:mt-4.5 shell-phone:mr-2.5 shell-phone:ml-2.5 w-[calc(62%-87px)]"
            data-project-overview-description=""
          >
            <h2 className="text-[12px] font-medium text-muted">Description</h2>
            {project.description ? (
              <Markdown text={project.description} density="block" />
            ) : (
              <p
                className="text-muted text-[13px] leading-[1.55] whitespace-pre-wrap wrap-break-word"
                data-control-muted=""
              >
                No description yet.
              </p>
            )}
          </section>
          {confirmArchive && (
            <div
              className="fixed inset-0 z-10 bg-[#0008] shell-phone:order-9 has-data-issue-create-modal:p-[clamp(60px,13vh,118px)_12px_12px] [&:has(>[data-issue-create-modal])]:animate-[issue-backdrop-in_var(--motion-overlay)_var(--ease-standard)_both] [&:has(>[data-issue-create-modal][data-is-closing])]:animate-[issue-backdrop-out_var(--motion-fast)_var(--ease-standard)_both] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[background:rgba(9,9,11,.42)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[backdrop-filter:blur(2px)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[-webkit-backdrop-filter:blur(2px)]"
              data-modal-backdrop=""
            >
              <section
                className="modal absolute pt-6.25 pr-6.25 pb-6.25 pl-6.25 border border-line rounded-[10px] bg-panel shadow-(--shadow) top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(570px,calc(100%-32px))]"
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="archive-title"
              >
                <h2 className="mt-1.25 mr-0 mb-5.5 ml-0" id="archive-title">
                  Archive project?
                </h2>
                <p>
                  This project will be archived and removed from active project
                  lists.
                </p>
                <button
                  type="button"
                  onClick={() => setConfirmArchive(false)}
                  disabled={busy}
                  className="max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 max-w-full font-sans shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
                  data-control-primary=""
                  onClick={archive}
                  disabled={busy}
                >
                  {busy ? "Archiving…" : "Archive project"}
                </button>
              </section>
            </div>
          )}
        </>
      ) : tab === "Activity" ? (
        <ProjectActivity
          project={project}
          teams={teams}
          users={userNames}
          updates={activity}
          onPublish={publish}
          onOpenIssue={(i) => onOpenIssue(i as Issue)}
        />
      ) : (
        <MyIssuesContent
          key={project.id}
          tab="Assigned"
          issues={related}
          states={projectStates}
          teams={teams}
          projects={projects}
          onOpenIssue={onOpenIssue}
          onNew={onNew}
          layout={layout}
          onLayoutChange={onLayoutChange}
          onMoved={onMoved}
          onUpdate={onUpdate}
          older={olderClosed.older}
          onLoadOlder={olderClosed.onLoadOlder}
          emptyCopy={[
            "No project tasks yet",
            "Tasks in this project will show up here.",
          ]}
        />
      )}
    </section>
  );
}

function submitProjectCreate(
  input: Record<string, unknown>,
  draft: Omit<Project, "id">,
) {
  const settle = addPendingProject(draft);
  setProjectNotice(null);
  gql(
    `mutation($input:ProjectCreateInput!){projectCreate(input:$input){success project{${PROJECT_FIELDS}}}}`,
    { input },
  )
    .then((d) => {
      if (!d.projectCreate?.success || !d.projectCreate.project)
        throw Error("Could not create project");
      settle(d.projectCreate.project);
    })
    .catch((e) => {
      settle(null);
      setProjectNotice({
        message: `Could not create project “${draft.name}”: ${(e as Error).message}`,
        retry: () => submitProjectCreate(input, draft),
      });
    });
}
function NewProject({
  teams,
  onClose,
}: {
  teams: Team[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState(""),
    [summary, setSummary] = useState(""),
    [description, setDescription] = useState("");
  const [teamIds, setTeamIds] = useState<string[]>(
      teams[0] ? [teams[0].id] : [],
    ),
    [leadId, setLeadId] = useState(""),
    [state, setState] = useState("backlog"),
    [startDate, setStartDate] = useState(""),
    [targetDate, setTargetDate] = useState("");
  const members = useMemberOptions();
  const error = "",
    busy = false;
  // Optimistic: the project appears in every project list at once; a failure surfaces on the projects page with Retry.
  const create = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || (startDate && targetDate && targetDate < startDate))
      return;
    const text =
      [summary.trim(), description.trim()].filter(Boolean).join("\n\n") || null;
    const lead = members.find((m) => m.id === leadId);
    submitProjectCreate(
      {
        name: name.trim(),
        description: text,
        teamIds,
        leadId: leadId || null,
        state,
        startDate: startDate || null,
        targetDate: targetDate || null,
      },
      {
        name: name.trim(),
        description: text || undefined,
        state,
        progress: 0,
        startDate: startDate || undefined,
        targetDate: targetDate || undefined,
        lead: lead ? { name: lead.name } : undefined,
        teams: {
          nodes: teams
            .filter((t) => teamIds.includes(t.id))
            .map((t) => ({ id: t.id, name: t.name, key: t.key })),
        },
      },
    );
    onClose();
  };
  return (
    <div
      className="grid pt-3 pr-3 pb-3 pl-3 fixed inset-0 z-10 bg-[#0008] has-data-issue-create-modal:p-[clamp(60px,13vh,118px)_12px_12px] [&:has(>[data-issue-create-modal])]:animate-[issue-backdrop-in_var(--motion-overlay)_var(--ease-standard)_both] [&:has(>[data-issue-create-modal][data-is-closing])]:animate-[issue-backdrop-out_var(--motion-fast)_var(--ease-standard)_both] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[background:rgba(9,9,11,.42)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[backdrop-filter:blur(2px)] [@supports_(backdrop-filter:blur(2px))_or_(-webkit-backdrop-filter:blur(2px))]:[&:has(>[data-issue-create-modal])]:[-webkit-backdrop-filter:blur(2px)]"
      data-modal-backdrop=""
    >
      <form
        className="modal relative top-auto left-auto overflow-y-auto absolute pt-6.25 pr-6.25 pb-6.25 pl-6.25 border border-line rounded-[10px] bg-panel shadow-(--shadow) max-sm:pt-4 max-sm:pr-4 max-sm:pb-4 max-sm:pl-4 transform-none w-[min(640px,100%)] max-w-2xl [@media_(max-width:600px)]:pt-4.5 [@media_(max-width:600px)]:pr-4.5 [@media_(max-width:600px)]:pb-4.5 [@media_(max-width:600px)]:pl-4.5 top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(570px,calc(100%-32px))] max-h-[calc(100dvh-24px)]"
        data-project-create-reference=""
        onSubmit={create}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-project-title"
      >
        <div className="flex items-start justify-between" data-modal-head="">
          <div>
            <span className="text-[10px] font-bold normal-case text-muted mt-0 mr-2.5 mb-1.75 ml-2.5 tracking-[.12em]">
              Workspace
            </span>
            <h2 className="mt-1.25 mr-0 mb-5.5 ml-0" id="new-project-title">
              New project
            </h2>
          </div>
          <button
            type="button"
            className="grid w-8 h-8 place-items-center rounded-md bg-transparent hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            aria-label="Discard project"
            onClick={onClose}
            disabled={busy}
          >
            <X size={18} />
          </button>
        </div>
        <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
          Project name
          <input
            className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
          Project summary
          <input
            className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Add a short summary"
          />
        </label>
        <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
          Project description
          <textarea
            className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
          />
        </label>
        <div
          className="grid grid-cols-1 gap-x-3 sm:grid-cols-2"
          data-project-create-fields=""
        >
          <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
            Status
            <select
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-panel text-foreground max-md:text-base focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              value={state}
              onChange={(e) => setState(e.target.value)}
            >
              {[
                "backlog",
                "planned",
                "started",
                "paused",
                "completed",
                "canceled",
              ].map((x) => (
                <option key={x} value={x}>
                  {x === "started"
                    ? "In Progress"
                    : x[0].toUpperCase() + x.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
            Lead
            <select
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-panel text-foreground max-md:text-base focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              value={leadId}
              onChange={(e) => setLeadId(e.target.value)}
            >
              <option value="">No lead</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
            Start date
            <input
              className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
            Target date
            <input
              className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              type="date"
              value={targetDate}
              min={startDate || undefined}
              onChange={(e) => setTargetDate(e.target.value)}
            />
          </label>
        </div>
        <fieldset className="mt-3.75 mr-0 mb-3.75 ml-0 border border-line rounded-md">
          <legend>Teams</legend>
          {teams.map((t) => (
            <label
              className="flex gap-1.75 mt-3.75 text-[12px] font-semibold items-center"
              key={t.id}
            >
              <input
                className="w-auto border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:text-[16px]"
                type="checkbox"
                checked={teamIds.includes(t.id)}
                onChange={(e) =>
                  setTeamIds((old) =>
                    e.target.checked
                      ? [...old, t.id]
                      : old.filter((id) => id !== t.id),
                  )
                }
              />
              {t.name} ({t.key})
            </label>
          ))}
        </fieldset>
        <p className="text-muted" data-control-muted="">
          Priority, labels, dependencies and milestones are unavailable in this
          project form.
        </p>
        {error && (
          <small
            className="block text-danger mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
            role="alert"
          >
            {error}
          </small>
        )}
        <footer className="flex justify-end gap-2 mt-4.5">
          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.75 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-inherit transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-control-outline=""
            disabled={busy}
            onClick={onClose}
          >
            Discard
          </button>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
            data-control-primary=""
            disabled={
              busy ||
              !name.trim() ||
              !!(startDate && targetDate && targetDate < startDate)
            }
          >
            {busy ? "Creating…" : "Create project"}
          </button>
        </footer>
      </form>
    </div>
  );
}
export const stateKey = (s: { name: string; type: string }) =>
  `${s.name}|${s.type}`;
export type IssueLayout = "List" | "Board";
export type IssueState = {
  id: string;
  name: string;
  color: string;
  type: string;
};
/* The switch shows the view it leads to: the layout (board) icon from the list, the list icon
 * from the board. MorphIcon animates between the two Tabler shapes; it stays aria-hidden
 * because the button already carries the label. */
function LayoutSwitch({
  layout,
  onChange,
}: {
  layout: IssueLayout;
  onChange: (layout: IssueLayout) => void;
}) {
  const next = layout === "List" ? "Board" : "List";
  return (
    <button
      type="button"
      className="border grid place-items-center size-7 rounded-full border-line bg-panel text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 "
      data-layout-switch=""
      aria-label={`Switch to ${next.toLowerCase()} view`}
      title={`Switch to ${next.toLowerCase()} view`}
      onClick={() => onChange(next)}
    >
      <MorphIcon
        icon={layout === "List" ? layoutIcon : listIcon}
        size={16}
        reducedMotion="user"
      />
    </button>
  );
}
function IssueBoard({
  issues,
  states,
  teams,
  onSelect,
  onMoved,
  onUpdate,
  onNew,
}: {
  issues: Issue[];
  states: IssueState[];
  teams: Team[];
  onSelect: (issue: Issue) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  onNew: (stateId?: string) => void;
}) {
  const syncedIssues = useSyncIssues() as Issue[];
  const allIssues = useMemo(
    () => [
      ...new Map(
        [...syncedIssues, ...issues].map((issue) => [issue.id, issue]),
      ).values(),
    ],
    [syncedIssues, issues],
  );
  const grouped = useMemo(() => {
    // Canceled work stays in the list; the board hides its column until it has a design.
    const boardStates = states.filter((s) => s.type !== "canceled");
    const unique = [...new Map(issues.map((i) => [i.id, i])).values()];
    const by = new Map<string, Issue[]>(
      boardStates.map((s) => [stateKey(s), []]),
    );
    const unknown: Issue[] = [];
    for (const i of unique) {
      if (i.state?.type === "canceled") continue;
      const list = i.state ? by.get(stateKey(i.state)) : undefined;
      if (list) list.push(i);
      else unknown.push(i);
    }
    const known = boardStates.map((state) => ({
      state,
      items: by.get(stateKey(state)) || [],
    }));
    return [
      ...known,
      ...(unknown.length
        ? [
            {
              state: {
                id: "unknown",
                name: "Unknown state",
                color: "#a1a1aa",
                type: "unknown",
              },
              items: unknown,
            },
          ]
        : []),
    ];
  }, [issues, states]);
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col  overflow-hidden bg-panel "
        data-issues-board=""
      >
        <Kanban
          teams={teams}
          grouped={grouped}
          allIssues={allIssues}
          onSelect={onSelect}
          onNew={onNew}
          onMoved={onMoved}
          onUpdate={onUpdate}
        />
      </div>
    </div>
  );
}
/* Closed issues older than the synced window, for one team or project: fetched page by page only when
 * the list shows "All" completed issues, then adopted into the store so they render and stay live. */
function useOlderClosed(
  scope: "team" | "project",
  id: string | undefined,
): { older?: OlderState; onLoadOlder: (retry?: boolean) => void } {
  const { closedCutoff } = useSyncStatus();
  const [older, setOlder] = useState<OlderState | undefined>();
  const gen = useRef(0);
  const cur = useRef<OlderState | undefined>(undefined);
  useEffect(() => {
    gen.current++;
    cur.current = undefined;
    setOlder(undefined);
  }, [scope, id, closedCutoff]);
  const onLoadOlder = useCallback(
    (retry?: boolean) => {
      if (
        !id ||
        !closedCutoff ||
        (cur.current && !(retry && cur.current.status === "error"))
      )
        return;
      const g = gen.current;
      const set = (v: OlderState) => {
        if (g !== gen.current) return;
        cur.current = v;
        setOlder(v);
      };
      set({ status: "loading" });
      void (async () => {
        let after: string | null = null;
        try {
          do {
            const d = await gql(
              `query OlderClosedIssues($after:String,$filter:IssueFilter){issues(first:100,after:$after,filter:$filter,orderBy:updatedAt){nodes ${issueFields} pageInfo{hasNextPage endCursor}}}`,
              {
                after,
                filter: {
                  [scope]: { id: { eq: id } },
                  state: { type: { in: ["completed", "canceled"] } },
                  updatedAt: { lt: closedCutoff },
                },
              },
            );
            if (g !== gen.current) return;
            const page = d.issues;
            if (
              page.pageInfo?.hasNextPage &&
              (!page.pageInfo.endCursor ||
                page.pageInfo.endCursor === after ||
                !page.nodes.length)
            )
              throw Error("Task pagination did not advance. Retry later.");
            if (page.nodes.length) getSync()?.adopt(page.nodes);
            after = page.pageInfo?.hasNextPage ? page.pageInfo.endCursor : null;
          } while (after);
          set({ status: "done" });
        } catch (e) {
          set({ status: "error", error: (e as Error).message });
        }
      })();
    },
    [scope, id, closedCutoff],
  );
  return { older, onLoadOlder };
}
type TeamIssuesTab = "All issues" | "Active" | "Backlog";
function TaskViewTabs<T extends string>({
  label,
  views,
  active,
  onChange,
}: {
  label: string;
  views: readonly T[];
  active: T;
  onChange: (view: T) => void;
}) {
  return (
    <nav
      className="flex min-w-0 flex-1 items-center gap-1.5 border-0 bg-bg  text-foreground overflow-x-auto shell-phone:basis-full [@media_(max-width:650px)]:gap-1 "
      data-my-issue-tabs=""
      aria-label={label}
    >
      {views.map((view) => (
        <button
          key={view}
          role="tab"
          aria-selected={active === view}
          className="inline-flex  min-h-7 flex-none items-center rounded-full border-0 outline outline-1 outline-line -outline-offset-1 bg-panel pl-2.75 pr-2.75 text-[12px] text-foreground transition-none aria-selected:bg-subtle aria-selected:inset-shadow-sm aria-selected:inset-shadow-neutral-50 aria-selected:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [@media_(max-width:650px)]:h-7.75 [@media_(max-width:650px)]:pt-0 [@media_(max-width:650px)]:pr-2.75 [@media_(max-width:650px)]:pb-0 [@media_(max-width:650px)]:pl-2.75 [@media_(max-width:650px)]:text-[12px]"
          onClick={() => onChange(view)}
        >
          {displayLabel(view)}
        </button>
      ))}
    </nav>
  );
}

function TeamIssuesPage({
  team,
  teams,
  projects,
  onIssue,
  layout,
  onLayoutChange,
  onMoved,
  onUpdate,
  onNew,
}: {
  team?: Team;
  teams: Team[];
  projects: Project[];
  onIssue: (issue: Issue) => void;
  layout: IssueLayout;
  onLayoutChange: (layout: IssueLayout) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  onNew: (stateId?: string) => void;
}) {
  const all = useSyncIssues() as Issue[];
  const [tab, setTab] = useState<TeamIssuesTab>("All issues");
  const teamId = team?.id;
  const rows = useMemo(
    () =>
      all.filter(
        (i) =>
          i.team?.id === teamId &&
          (tab === "All issues" ||
            (tab === "Active"
              ? i.state?.type === "started" || i.state?.type === "unstarted"
              : i.state?.type === "backlog")),
      ),
    [all, teamId, tab],
  );
  const { older, onLoadOlder } = useOlderClosed(
    "team",
    tab === "All issues" ? teamId : undefined,
  );
  if (!team)
    return (
      <section
        className="max-w-275 mt-0 mr-auto mb-0 ml-auto"
        data-projects-page=""
      >
        <p role="alert">Team not found in this workspace.</p>
      </section>
    );
  return (
    <>
      <div
        className="m-0 flex h-11 min-h-11 flex-none items-center border-b border-line bg-bg px-4 py-0 text-foreground max-md:px-3  [@media_(max-width:560px)]:gap-3.75 shell-phone:gap-3.5"
        data-page-head=""
        data-my-issues-head=""
      >
        <div>
          <h1 className="text-sm font-medium leading-normal tracking-[-.045em] text-foreground">
            {team.name}
          </h1>
          <p className="mt-1.5 mr-0 mb-0 ml-0 text-muted max-sm:text-xs hidden [@media_(max-width:560px)]:text-[12px]">
            Team tasks
          </p>
        </div>
      </div>
      <MyIssuesContent
        tabs={
          <TaskViewTabs
            label="Team task views"
            views={["All issues", "Active", "Backlog"] as const}
            active={tab}
            onChange={setTab}
          />
        }
        key={team.id}
        tab="Assigned"
        issues={rows}
        states={team.states.nodes}
        teams={teams}
        projects={projects}
        onOpenIssue={onIssue}
        onNew={onNew}
        layout={layout}
        onLayoutChange={onLayoutChange}
        onMoved={onMoved}
        onUpdate={onUpdate}
        older={older}
        onLoadOlder={onLoadOlder}
        emptyCopy={
          tab === "All issues"
            ? ["No tasks yet", `Tasks in ${team.name} will show up here.`]
            : [
                `No ${tab.toLowerCase()} issues`,
                `${tab} tasks in ${team.name} will show up here.`,
              ]
        }
      />
    </>
  );
}
/* The workspace board is the team-issue surface without a team: the same head, tabs, toolbar and
 * board, over every team's states. It opens as a board whatever the saved list/board preference
 * says, because that is what the page is, while the toolbar switch still offers the list view. */
function BoardPage({
  teams,
  projects,
  onIssue,
  onMoved,
  onUpdate,
  onNew,
}: {
  teams: Team[];
  projects: Project[];
  onIssue: (issue: Issue) => void;
  onMoved: (issueId: string, state: IssueState) => Promise<void>;
  onUpdate: (issueId: string, input: Record<string, unknown>) => Promise<void>;
  onNew: (stateId?: string) => void;
}) {
  const all = useSyncIssues() as Issue[];
  const [tab, setTab] = useState<BoardTab>("All issues"),
    [layout, setLayout] = useState<IssueLayout>("Board");
  const rows = useMemo(
    () =>
      all.filter(
        (i) =>
          tab === "All issues" ||
          (tab === "Active"
            ? i.state?.type === "started" || i.state?.type === "unstarted"
            : i.state?.type === "backlog"),
      ),
    [all, tab],
  );
  const states = useMemo(
    () => [
      ...new Map(
        teams.flatMap((t) => t.states.nodes).map((s) => [stateKey(s), s]),
      ).values(),
    ],
    [teams],
  );
  return (
    <>
      <div
        className="m-0 flex h-11 min-h-11 flex-none items-center border-b border-line bg-bg py-0 text-foreground max-md: [@media_(max-width:560px)]:gap-3.75 shell-phone:gap-3.5"
        data-page-head=""
        data-my-issues-head=""
      >
        <div>
          <h1 className="mt-2.25 mr-0 mb-1 ml-0 text-[30px] tracking-[-.045em] max-sm:text-2xl max-md:text-3xl font-semibold leading-normal text-foreground [@media_(max-width:560px)]:text-[24px] shell-phone:text-[25px] [@media_(max-width:420px)]:text-[23px]">
            Board
          </h1>
          <p className="mt-1.5 mr-0 mb-0 ml-0 text-muted max-sm:text-xs hidden [@media_(max-width:560px)]:text-[12px]">
            All teams
          </p>
        </div>
      </div>
      <MyIssuesContent
        tabs={
          <nav
            className="mt-0 -mr-1 -ml-1 flex h-13 min-w-0 flex-1 items-center gap-1.5 border-0 bg-bg pl-1 pr-1 pt-0 pb-0 text-foreground overflow-x-auto [@media_(max-width:650px)]:gap-1 [@media_(max-width:650px)]:h-12"
            data-my-issue-tabs=""
            aria-label="Board views"
          >
            {(["All issues", "Active", "Backlog"] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                className="inline-flex h-8.5 min-h-7 flex-none items-center rounded-[999px] border-0 outline outline-1 outline-line -outline-offset-1 bg-panel pl-2.75 pr-2.75 text-[12px] text-foreground transition-none aria-selected:bg-subtle aria-selected:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [@media_(max-width:650px)]:h-7.75 [@media_(max-width:650px)]:pt-0 [@media_(max-width:650px)]:pr-2.75 [@media_(max-width:650px)]:pb-0 [@media_(max-width:650px)]:pl-2.75 [@media_(max-width:650px)]:text-[12px]"
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </nav>
        }
        tab="Assigned"
        issues={rows}
        states={states}
        teams={teams}
        projects={projects}
        onOpenIssue={onIssue}
        onNew={onNew}
        layout={layout}
        onLayoutChange={setLayout}
        onMoved={onMoved}
        onUpdate={onUpdate}
        emptyCopy={
          tab === "All issues"
            ? ["No tasks yet", "Tasks across your teams will show up here."]
            : [
                `No ${tab.toLowerCase()} issues`,
                `${tab} tasks across your teams will show up here.`,
              ]
        }
      />
    </>
  );
}
function TeamCycleSettings({
  team,
  onBack,
  admin,
}: {
  team?: Team;
  onBack: () => void;
  admin?: boolean;
}) {
  return <GraphTeamCycleSettings team={team} onBack={onBack} admin={admin} />;
}
function TeamCyclesPage({
  team,
  teams,
  viewer,
  workspaceName,
  onCycle,
  onSettings,
  revision,
  onNavigate,
}: {
  team?: Team;
  teams: Team[];
  viewer: Viewer;
  workspaceName: string;
  onCycle: (c: Cycle) => void;
  onSettings: () => void;
  revision: number;
  onNavigate: (view: string, path?: string) => void;
}) {
  return (
    <GraphTeamCycles
      team={team}
      teams={teams}
      viewer={viewer}
      workspaceName={workspaceName}
      onCycle={onCycle}
      onSettings={onSettings}
      onNavigate={onNavigate}
    />
  );
}
function TeamProjectsPage({
  team,
  onProject,
  onCreate,
}: {
  team?: Team;
  onProject: (project: Project) => void;
  onCreate: () => void;
  revision: number;
}) {
  // The store's metadata holds the first 250 projects; team pages are fetched only when the workspace has more than that.
  const meta = useProjectSyncMeta();
  const partial = Boolean(meta?.projects.pageInfo?.hasNextPage);
  const version = useProjectEntityVersion(["project"]);
  const [rows, setRows] = useState<Project[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [more, setMore] = useState(false),
    [cursor, setCursor] = useState<string | null>(null);
  const load = async (after: string | null = null) => {
    if (!team) return;
    setLoading(true);
    setError("");
    try {
      const d = await gql(
        `query TeamProjects($after:String,$filter:ProjectFilter){projects(first:50,after:$after,filter:$filter){nodes{${PROJECT_FIELDS}} pageInfo{hasNextPage endCursor}}}`,
        { after, filter: { team: { id: { eq: team.id } } } },
      );
      const page = d.projects;
      if (
        page.pageInfo?.hasNextPage &&
        (!page.pageInfo.endCursor ||
          page.pageInfo.endCursor === after ||
          !page.nodes.length)
      )
        throw Error("Team project pagination did not advance.");
      rememberProjects(page.nodes);
      setRows((old) => {
        const prior = after ? old : [];
        const ids = new Set(prior.map((x) => x.id));
        return [...prior, ...page.nodes.filter((p: Project) => !ids.has(p.id))];
      });
      setCursor(page.pageInfo?.endCursor || null);
      setMore(Boolean(page.pageInfo?.hasNextPage));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    if (partial && team) void load();
  }, [team?.id, partial, version]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!team)
    return (
      <section
        className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
        data-team-scoped-page=""
      >
        <p className="text-muted leading-normal" role="alert">
          Team not found in this workspace.
        </p>
      </section>
    );
  return (
    <ProjectsPage
      projects={rows}
      teamId={team.id}
      onOpen={onProject}
      onCreate={onCreate}
      hasMore={partial && more}
      onLoadMore={() => void load(cursor)}
      loading={loading}
      error={error}
    />
  );
}
function TeamViewsPage({
  team,
  onIssue,
  onNavigate,
  revision,
}: {
  team?: Team;
  onIssue: (issue: Issue) => void;
  onNavigate: (view: string, path?: string) => void;
  revision: number;
}) {
  // Issues and projects come from the local-first store: no fetch, live through deltas.
  const all = useSyncIssues(),
    meta = useSyncMeta(),
    { ready } = useSyncStatus();
  const teamId = team?.id;
  const issues = useMemo(
    () => (teamId ? all.filter((i) => i.team?.id === teamId) : []),
    [all, teamId],
  );
  const projects = useMemo(
    () =>
      teamId && meta
        ? meta.projects.nodes.filter((p) =>
            p.teams?.nodes.some((t) => t.id === teamId),
          )
        : [],
    [meta, teamId],
  );
  const notice = meta?.projects.pageInfo?.hasNextPage
    ? `Only the first ${meta.projects.nodes.length} workspace projects are available.`
    : "";
  if (!team)
    return (
      <section
        className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
        data-team-scoped-page=""
      >
        <p className="text-muted leading-normal" role="alert">
          Team not found in this workspace.
        </p>
      </section>
    );
  if (!ready && !meta)
    return (
      <section
        className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
        data-team-scoped-page=""
      >
        <p className="text-muted leading-normal" role="status">
          Loading team views…
        </p>
      </section>
    );
  return (
    <>
      {notice && (
        <p
          role="alert"
          className="max-w-235 mt-0 mr-auto mb-0 ml-auto pt-6.25 pr-5 pb-6.25 pl-5 min-w-0 max-md:pt-4 max-md:pr-2.5 max-md:pb-4 max-md:pl-2.5 font-(family-name:--hoja-font-ui) shell-phone:pt-4 shell-phone:pr-2.5 shell-phone:pb-4 shell-phone:pl-2.5"
          data-team-scoped-page=""
        >
          {notice}
        </p>
      )}
      <ViewsPage
        key={team.id}
        orgId={team.id}
        issues={issues}
        projects={projects}
        path={location.pathname}
        onNavigate={onNavigate}
        onSelect={onIssue}
        basePath={`/workspace/team/${team.key.toLowerCase()}/views`}
        viewName="Team views"
        title={`${team.name} views`}
        revision={revision}
      />
    </>
  );
}
function TeamCyclePage({
  team,
  teams,
  viewer,
  orgId,
  workspaceName,
  projects,
  labels,
  cycles,
  upcoming,
  onBack,
  onIssue,
  revision,
  onNavigate,
}: {
  team?: Team;
  teams: Team[];
  viewer: Viewer;
  orgId: string;
  workspaceName: string;
  projects: Project[];
  labels: { id: string; name: string; color?: string }[];
  cycles: Cycle[];
  upcoming: boolean;
  onBack: () => void;
  onIssue: (issue: Issue) => void;
  revision: number;
  onNavigate: (view: string, path?: string) => void;
}) {
  return (
    <GraphTeamCycle
      team={team}
      teams={teams}
      viewer={viewer}
      orgId={orgId}
      workspaceName={workspaceName}
      projects={projects}
      labels={labels}
      cycles={cycles}
      upcoming={upcoming}
      onBack={onBack}
      onSelect={onIssue}
      onNavigate={onNavigate}
    />
  );
}
function CyclesPage({
  teams,
  viewer,
  workspaceName,
  onCycle,
  onSettings,
  revision,
  onNavigate,
}: {
  teams: Team[];
  viewer: Viewer;
  workspaceName: string;
  onCycle: (c: Cycle) => void;
  onSettings: (team: Team) => void;
  revision: number;
  onNavigate: (view: string, path?: string) => void;
}) {
  return (
    <GraphWorkspaceCycles
      teams={teams}
      viewer={viewer}
      workspaceName={workspaceName}
      onCycle={onCycle}
      onSettings={onSettings}
      onNavigate={onNavigate}
    />
  );
}
function CycleDetail({
  cycle,
  team,
  teams,
  viewer,
  orgId,
  workspaceName,
  projects,
  labels,
  cycles,
  revision,
  onBack,
  onSelect,
  onNavigate,
}: {
  cycle: Cycle;
  team?: Team;
  teams: Team[];
  viewer: Viewer;
  orgId: string;
  workspaceName: string;
  projects: Project[];
  labels: { id: string; name: string; color?: string }[];
  cycles: Cycle[];
  revision: number;
  onBack: () => void;
  onSelect: (i: Issue) => void;
  onNavigate: (view: string, path?: string) => void;
}) {
  return (
    <GraphCycleDetail
      cycle={cycle}
      team={team}
      teams={teams}
      viewer={viewer}
      orgId={orgId}
      workspaceName={workspaceName}
      projects={projects}
      labels={labels}
      cycles={cycles}
      onBack={onBack}
      onSelect={onSelect}
      onNavigate={onNavigate}
    />
  );
}
function TeamHome({
  team,
  issues,
  onNavigate,
  onSelectIssue,
}: {
  team?: Team;
  issues: Issue[];
  onNavigate: (view: string, path?: string) => void;
  onSelectIssue: (issue: Issue) => void;
}) {
  const teamKey = team?.key;
  const { open, started } = useMemo(() => {
    const open: Issue[] = [];
    let started = 0;
    for (const i of issues) {
      if (i.team?.key !== teamKey) continue;
      if (i.state?.type !== "completed") open.push(i);
      if (i.state?.type === "started") started++;
    }
    return { open, started };
  }, [issues, teamKey]);
  return (
    <section className="max-w-7xl mx-auto w-full p-4">
      <header
        className="flex items-center gap-3.75 mb-6.5 [&>div:nth-child(2)]:flex-1"
        data-team-home-head=""
      >
        <div className="grid place-items-center w-12.5 h-12.5 rounded-xl bg-accent-soft text-accent font-extrabold">
          {team?.key.slice(0, 2) || "TM"}
        </div>
        <div>
          <div className="flex gap-2 text-[12px] text-muted">
            <span>Your teams</span>
            <span>/</span>
            <b className="text-foreground font-medium">
              {team?.name || "Team"}
            </b>
          </div>
          <h1 className="text-[27px] tracking-[-.04em] mt-1.5 mr-0 mb-1.5 ml-0 font-semibold leading-normal ">
            {team?.name || "Team overview"}
          </h1>
          {team?.description ? (
            <Markdown text={team.description} />
          ) : (
            <p className="mt-0 mr-0 mb-0 ml-0 text-muted text-[12px]">
              Your team workspace and current work.
            </p>
          )}
        </div>
        <div className="flex pl-2.5 max-md:hidden" data-team-member-stack="">
          {(team?.members?.nodes || []).slice(0, 5).map((m, i) => (
            <span
              className="grid place-items-center w-7.5 h-7.5 -ml-2 rounded-[50%] border-2 border-bg"
              key={m.id}
              title={m.name || m.email}
              style={{ zIndex: 6 - i }}
            >
              <UserBlobatar user={m} />
            </span>
          ))}
        </div>
      </header>
      <div
        className="border grid grid-cols-4 border-line rounded-lg bg-panel mb-5 max-md:grid-cols-2 max-md:[&>div:nth-child(2)]:border-r-0 max-md:[&>div:nth-child(3)]:border-t max-md:[&>div:nth-child(3)]:border-line max-md:[&>div:nth-child(4)]:border-t max-md:[&>div:nth-child(4)]:border-line"
        data-team-home-stats=""
      >
        <div className="grid gap-2 pt-3.75 pr-3.75 pb-3.75 pl-3.75 border-r border-line last:border-0">
          <span className="text-muted text-[11px]">Open tasks</span>
          <b className="text-[20px]">{open.length}</b>
        </div>
        <div className="grid gap-2 pt-3.75 pr-3.75 pb-3.75 pl-3.75 border-r border-line last:border-0">
          <span className="text-muted text-[11px]">In progress</span>
          <b className="text-[20px]">{started}</b>
        </div>
        <div className="grid gap-2 pt-3.75 pr-3.75 pb-3.75 pl-3.75 border-r border-line last:border-0">
          <span className="text-muted text-[11px]">Members</span>
          <b className="text-[20px]">{team?.members?.nodes.length || 0}</b>
        </div>
        <div className="grid gap-2 pt-3.75 pr-3.75 pb-3.75 pl-3.75 border-r border-line last:border-0">
          <span className="text-muted text-[11px]">Workflow states</span>
          <b className="text-[20px]">{team?.states.nodes.length || 0}</b>
        </div>
      </div>
      <div
        className="grid gap-4 grid-cols-1 md:grid-cols-2"
        data-team-home-grid=""
      >
        <section
          className="border border-line rounded-xl  shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          <div
            className="border-b border-line flex justify-between pt-3 pr-4 pb-3 pl-4 text-[12px]"
            data-card-head=""
          >
            <span>
              Active tasks{" "}
              <b className="ml-1.25 text-muted font-normal">{open.length}</b>
            </span>
            <button
              className="text-accent bg-transparent border-0 text-[11px] w-auto pt-0 pr-0 pb-0 pl-0 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              data-text-action=""
              onClick={() =>
                onNavigate(
                  "Team issues",
                  `/workspace/team/${team?.key.toLowerCase()}/active`,
                )
              }
            >
              View team tasks →
            </button>
          </div>
          <IssueList
            issues={open.slice(0, 6)}
            onOpen={onSelectIssue}
            emptyTitle="No open tasks"
            emptyText="Open team tasks will appear here."
          />
        </section>
        <aside className="grid content-start gap-3">
          <section
            className="border border-line rounded-xl shadow-[0_1px_2px_#1b1b1b08] pt-4 pr-4 pb-4 pl-4 bg-panel"
            data-side-card=""
          >
            <span className="text-xs font-semibold normal-case text-muted">
              Current cycle
            </span>
            <h3 className="text-[15px] mt-3 mr-0 mb-1.25 ml-0">
              {team?.activeCycle?.name || "No active cycle"}
            </h3>
            {team?.activeCycle && (
              <>
                <p className="text-[11px] text-muted">
                  {team.activeCycle.startsAt?.slice(0, 10) || "Start unset"} —{" "}
                  {team.activeCycle.endsAt?.slice(0, 10) || "End unset"}
                </p>
                <div
                  className="block h-1.25 mt-1.75 rounded-[5px] bg-subtle overflow-hidden"
                  data-progress-track=""
                >
                  <i
                    className="block h-full bg-accent"
                    style={{
                      width: `${Math.min(100, Math.max(0, team.activeCycle.progress || 0))}%`,
                    }}
                  />
                </div>
              </>
            )}
            <button
              className="flex justify-between w-full pt-2.25 pr-0 pb-2.25 pl-0 bg-transparent border-t border-line text-left text-[11px] text-accent border-0 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              data-text-action=""
              onClick={() =>
                onNavigate(
                  "Team cycles",
                  `/workspace/team/${team?.key.toLowerCase()}/cycles`,
                )
              }
            >
              View team cycles →
            </button>
          </section>
          <section
            className="border border-line rounded-xl shadow-[0_1px_2px_#1b1b1b08] pt-4 pr-4 pb-4 pl-4 bg-panel"
            data-side-card=""
          >
            <span className="text-xs font-semibold normal-case text-muted">
              Team links
            </span>
            <button
              className="flex justify-between w-full pt-2.25 pr-0 pb-2.25 pl-0 bg-transparent border-t border-line text-left text-[12px] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => onNavigate("Board")}
            >
              Tasks board <span>→</span>
            </button>
            <button
              className="flex justify-between w-full pt-2.25 pr-0 pb-2.25 pl-0 bg-transparent border-t border-line text-left text-[12px] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => onNavigate("Projects")}
            >
              Projects <span>→</span>
            </button>
            <button
              className="flex justify-between w-full pt-2.25 pr-0 pb-2.25 pl-0 bg-transparent border-t border-line text-left text-[12px] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              onClick={() => onNavigate("Cycles")}
            >
              Cycles <span>→</span>
            </button>
          </section>
        </aside>
      </div>
    </section>
  );
}

/* Command palette: searches the local issue store, projects and teams on every keystroke with no
   network round trip; the server is asked (debounced) only for issues outside the synced set.
   The query lives here, not in App, so typing never re-renders the page behind the palette. */
function CommandPalette({
  query: initialQuery,
  onClose,
  onIssue,
  onNavigate,
  onCreateInitiative,
  onOpenInitiative,
}: {
  query: string;
  setQuery: (q: string) => void;
  issues: Issue[];
  onClose: () => void;
  onIssue: (i: Issue) => void;
  onNavigate: (view: string) => void;
  onCreateInitiative: () => void;
  onOpenInitiative: (id: string) => void;
}) {
  const [initiativeRows, setInitiativeRows] = useState<InitiativeRef[]>([]);
  useEffect(() => {
    fetchAllInitiatives()
      .then(setInitiativeRows)
      .catch(() => undefined);
  }, []);
  const [query, setQuery] = useState(initialQuery),
    [active, setActive] = useState(0),
    [remote, setRemote] = useState<Issue[]>([]);
  const deferred = React.useDeferredValue(query);
  const issues = useIssues(),
    meta = useSyncMeta();
  const issuesRef = useRef(issues);
  issuesRef.current = issues;
  useEffect(() => warmSearchIndex(issues), [issues]);
  const { results } = useIssueSearch(issues, deferred, 8);
  const terms = useMemo(() => searchTerms(deferred), [deferred]);
  const projects = useMemo(
    () =>
      terms.length
        ? (meta?.projects.nodes || [])
            .filter((p) => matchesTerms(p.name, terms))
            .slice(0, 4)
        : [],
    [meta, terms],
  );
  const teams = useMemo(
    () =>
      terms.length
        ? (meta?.teams.nodes || [])
            .filter((t) => matchesTerms(`${t.name} ${t.key}`, terms))
            .slice(0, 4)
        : [],
    [meta, terms],
  );
  const initiativeHits = useMemo(
    () =>
      terms.length
        ? initiativeRows
            .filter((i) => matchesTerms(`${i.identifier} ${i.name}`, terms))
            .slice(0, 4)
        : [],
    [initiativeRows, terms],
  );
  const actions = [
    {
      key: "a:new-initiative",
      label: "Create new initiative",
      hint: "N then I",
    },
  ].filter(
    (a) => !terms.length || matchesTerms(`${a.label} initiative`, terms),
  );
  const pages = [
    "Inbox",
    "My issues",
    "Board",
    "Projects",
    "Initiatives",
    "Cycles",
    "Home",
  ].filter((x) => !terms.length || matchesTerms(x, terms));
  const localCount = results.length;
  useEffect(() => {
    const term = deferred.trim();
    setRemote([]);
    if (term.length < 2 || localCount >= 8) return;
    let live = true;
    const timer = window.setTimeout(() => {
      gql(
        `query PaletteSearch($term:String!){searchIssues(term:$term,first:8){nodes ${issueFields}}}`,
        { term },
      )
        .then((d) => {
          if (!live) return;
          const known = new Set(issuesRef.current.map((i) => i.id));
          setRemote(
            (d.searchIssues?.nodes || [])
              .filter((i: Issue) => !known.has(i.id))
              .slice(0, 5),
          );
        })
        .catch(() => {});
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [deferred, localCount]);
  const go = (path: string) => {
    onClose();
    navigateTo(path);
  };
  const entries: { key: string; run: () => void }[] = [
    ...results.map((i) => ({ key: `i:${i.id}`, run: () => onIssue(i) })),
    ...remote.map((i) => ({ key: `r:${i.id}`, run: () => onIssue(i) })),
    ...projects.map((p) => ({
      key: `p:${p.id}`,
      run: () => go(`/workspace/project/${p.id}`),
    })),
    ...teams.map((t) => ({
      key: `t:${t.id}`,
      run: () => go(`/workspace/team/${t.key.toLowerCase()}/active`),
    })),
    ...initiativeHits.map((i) => ({
      key: `in:${i.id}`,
      run: () => onOpenInitiative(i.identifier || i.id),
    })),
    ...actions.map((a) => ({ key: a.key, run: onCreateInitiative })),
    ...pages.map((x) => ({ key: `n:${x}`, run: () => onNavigate(x) })),
  ];
  useEffect(() => setActive(0), [deferred]);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  /* Captured during the first render, before the input takes focus, so closing the palette
   * can put the keyboard back where it was. */
  const openerRef = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  /* Escape closes from anywhere inside the palette and hands focus back to the opener.
   * Entries that navigate keep focus at the destination, which is why this is not an
   * unmount effect. */
  const closeAndRestore = () => {
    onClose();
    const back = openerRef.current;
    if (back && document.contains(back)) back.focus();
  };
  useEffect(() => {
    listRef.current
      ?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const at = Math.min(active, Math.max(0, entries.length - 1));
  const optionId = (key: string) =>
    `command-option-${key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const row = (key: string) => {
    const selected = entries[at]?.key === key;
    return {
      id: optionId(key),
      role: "option" as const,
      "aria-selected": selected,
      "data-active": selected ? "true" : undefined,
      style: selected ? { background: "var(--subtle)" } : undefined,
      onMouseEnter: () => {
        const i = entries.findIndex((e) => e.key === key);
        if (i >= 0) setActive(i);
      },
    };
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeAndRestore();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(at + 1, entries.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(at - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      entries[at]?.run();
    }
  };
  return bodyLayer(
    <div
      className="inset-0 pr-4.5 pl-4.5 bg-[#0008] fixed z-100 flex justify-center items-start py-[15vh] px-4.5"
      data-command-backdrop=""
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closeAndRestore();
      }}
    >
      <section
        className="border shadow-(--shadow) overflow-hidden border-line rounded-[10px] bg-panel w-[min(570px,100%)] max-w-xl"
        data-command-palette=""
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        data-results-for={deferred}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div
          className="border-b border-line flex items-center gap-2.5 pt-3.5 pr-3.5 pb-3.5 pl-3.5 text-muted"
          data-command-input=""
        >
          <Search size={17} />
          <input
            ref={inputRef}
            className="flex-1 border-0 bg-transparent text-foreground text-[14px] max-md:text-base outline-none placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tasks or navigate..."
            aria-label="Search tasks or navigate"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-autocomplete="list"
            aria-activedescendant={
              entries[at] ? optionId(entries[at].key) : undefined
            }
            onKeyDown={onKeyDown}
          />
          <kbd className="text-[10px] text-muted">ESC</kbd>
        </div>
        <div
          ref={listRef}
          id="command-palette-list"
          role="listbox"
          aria-label="Search results"
          style={{ maxHeight: "min(60vh,520px)", overflowY: "auto" }}
        >
          {terms.length > 0 && results.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"

                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Tasks
              </p>
              {results.map((i) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={i.id}
                  {...row(`i:${i.id}`)}
                  onClick={() => onIssue(i)}
                >
                  <span
                    className="inline-block w-2 h-2 shrink-0 rounded-[50%] bg-room-muted data-[p=1]:bg-red-500 data-[p=2]:bg-orange-500 data-[p=3]:bg-yellow-500 data-[p=4]:bg-blue-400 last:text-muted last:overflow-hidden last:text-ellipsis data-[p=1]:bg-subtle data-[p=2]:bg-subtle data-[p=3]:bg-subtle data-[p=4]:bg-subtle"
                    data-p={i.priority}
                  />
                  <b>{i.identifier}</b>
                  <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                    {i.title}
                  </span>
                </button>
              ))}
            </div>
          )}
          {terms.length > 0 && remote.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"

                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Older tasks
              </p>
              {remote.map((i) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={i.id}
                  {...row(`r:${i.id}`)}
                  onClick={() => onIssue(i)}
                >
                  <span
                    className="inline-block w-2 h-2 shrink-0 rounded-[50%] bg-room-muted data-[p=1]:bg-red-500 data-[p=2]:bg-orange-500 data-[p=3]:bg-yellow-500 data-[p=4]:bg-blue-400 last:text-muted last:overflow-hidden last:text-ellipsis data-[p=1]:bg-subtle data-[p=2]:bg-subtle data-[p=3]:bg-subtle data-[p=4]:bg-subtle"
                    data-p={i.priority}
                  />
                  <b>{i.identifier}</b>
                  <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                    {i.title}
                  </span>
                </button>
              ))}
            </div>
          )}
          {projects.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"

                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Projects
              </p>
              {projects.map((p) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={p.id}
                  {...row(`p:${p.id}`)}
                  onClick={() => go(`/workspace/project/${p.id}`)}
                >
                  <FolderKanban size={14} />
                  <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                    {p.name}
                  </span>
                </button>
              ))}
            </div>
          )}
          {teams.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"

                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Teams
              </p>
              {teams.map((t) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={t.id}
                  {...row(`t:${t.id}`)}
                  onClick={() =>
                    go(`/workspace/team/${t.key.toLowerCase()}/active`)
                  }
                >
                  <Users size={14} />
                  <b>{t.key}</b>
                  <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                    {t.name}
                  </span>
                </button>
              ))}
            </div>
          )}
          {initiativeHits.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"
                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Initiatives
              </p>
              {initiativeHits.map((i) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={i.id}
                  {...row(`in:${i.id}`)}
                  onClick={() => onOpenInitiative(i.identifier || i.id)}
                >
                  <Target size={14} />
                  <b>{i.identifier}</b>
                  <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                    {i.name}
                  </span>
                </button>
              ))}
            </div>
          )}
          {actions.length > 0 && (
            <div
              className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
              data-command-results=""
              role="group"
            >
              <p
                role="presentation"
                className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
              >
                Actions
              </p>
              {actions.map((a) => (
                <button
                  className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  key={a.key}
                  {...row(a.key)}
                  onClick={onCreateInitiative}
                >
                  <Plus size={14} />
                  <span className="flex-1">{a.label}</span>
                  <kbd className="text-[10px] text-muted">{a.hint}</kbd>
                </button>
              ))}
            </div>
          )}
          <div
            className="pt-1.75 pr-1.75 pb-1.75 pl-1.75"
            data-command-results=""
            role="group"
          >
            <p
              role="presentation"

              className="pt-1.25 pr-2 pb-1.25 pl-2 text-muted text-[10px] normal-case tracking-widest"
            >
              Navigate
            </p>
            {pages.map((x) => (
              <button
                className="flex items-center gap-2.25 w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 rounded-md bg-transparent text-left text-[12px] hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                key={x}
                {...row(`n:${x}`)}
                onClick={() => onNavigate(x)}
              >
                <Command size={14} />
                <span className="last:text-muted last:overflow-hidden last:text-ellipsis">
                  {x}
                </span>
              </button>
            ))}
          </div>
        </div>
        <footer className="pt-2.5 pr-3.5 pb-2.5 pl-3.5 border-t border-line text-muted text-[10px]">
          ↑↓ to navigate · Enter to select · Esc to close
        </footer>
      </section>
    </div>,
  );
}

/* Panes the reference has and Hoja cannot serve. Every id here is proven backend-less by the
 * live schema introspection recorded in reference/linear/SETTINGS-SPEC.md (Query/Mutation have no
 * field for it) and owns a sub-issue under HOJA-421. Cycles and Extensions are NOT here: the live
 * API answers teamCycleCadenceEnable/cycleUpdate and extensionInstall/extensionUpdate/
 * extensionUninstall, so calling them "missing" would be false.
 * label -> the noun the unavailable pane prints. */
const SETTINGS_UNAVAILABLE: [string, string, string, string][] = [
  [
    "import-export",
    "Import & export",
    "No importer/exporter endpoints exist; the CLI is the only bulk path.",
    "HOJA-493",
  ],
  [
    "usage",
    "Usage & limits",
    "The agent ledger is read on the Billing screen; per-seat plan history and rate-limit analytics are not modelled.",
    "HOJA-495",
  ],
];

function SettingsSidebar({
  workspace,
  viewer,
  section,
  onNavigate,
  onBack,
  onLogout,
  mobile,
  onCloseMobile,
}: {
  workspace: { name: string };
  viewer: Viewer;
  section: string;
  onNavigate: (section: string) => void;
  onBack: () => void;
  onLogout: () => void;
  mobile: boolean;
  onCloseMobile: () => void;
}) {
  const [query, setQuery] = useState("");
  const panel = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  /* The reference nav (38 entries, 7 groups) is inventoried in reference/linear/SETTINGS-SPEC.md.
   * Hoja renders a group only where it has a screen for at least one entry, and every entry it
   * cannot serve is present but marked, so the shell never advertises a capability it lacks.
   * SETTINGS-UNAVAILABLE is filled by scripts/check-settings.mjs: each id there must also have a
   * Hoja sub-issue describing the backend it needs. */
  const groups: [
    string,
    [string, string, React.ComponentType<{ size?: number }>][],
  ][] = [
    [
      "Personal",
      [
        ["preferences", "Preferences", SlidersHorizontal],
        ["profile", "Profile", UserRound],
        ["notifications", "Notifications", Bell],
        ["api-keys", "Connected apps", KeyRound],
        ["agent-conversations", "Agent conversations", MessageCircle],
        ["connections", "Connections", Plug],
        ["knowledge", "Knowledge", BookOpen],
      ],
    ],
    [
      "Tasks",
      [
        ["issue-labels", "Labels", Tags],
        ["issue-templates", "Task templates", LayoutTemplate],
        ["slas", "SLAs", Clock],
        ["cycles", "Cycles", CalendarDays],
      ],
    ],
    [
      "Projects",
      [
        ["project-statuses", "Project statuses", CircleDot],
        ["project-templates", "Project templates", Layers3],
      ],
    ],
    ["Initiatives", [["initiatives", "Initiatives", Target]]],
    [
      "Administration",
      [
        ["workspace", "Workspace", Building2],
        ["teams", "Teams", Users],
        ["members", "Members", UserRound],
        ["permissions", "Permissions", Shield],
        ["security", "Security", KeyRound],
        ["billing", "Billing", BarChart3],
        ["model-providers", "Model providers", Bot],
        ["extensions", "Extensions", Blocks],
      ],
    ],
    [
      "Not yet available",
      SETTINGS_UNAVAILABLE.map(
        ([id, label]) =>
          [id, label, Blocks] as [
            string,
            string,
            React.ComponentType<{ size?: number }>,
          ],
      ),
    ],
  ];
  const isUnavailable = (id: string) =>
    SETTINGS_UNAVAILABLE.some(([unavailableId]) => unavailableId === id);
  const filtered = groups
    .map(
      ([group, items]) =>
        [
          group,
          items
            .filter(
              ([id]) =>
                ![
                  "workspace",
                  "teams",
                  "members",
                  "permissions",
                  "security",
                  "project-statuses",
                  "project-templates",
                  "billing",
                  "model-providers",
                  "extensions",
                ].includes(id) || viewer.admin,
            )
            .filter(([, label]) =>
              `${label} ${group}`
                .toLowerCase()
                .includes(query.trim().toLowerCase()),
            ),
        ] as [
          string,
          [string, string, React.ComponentType<{ size?: number }>][],
        ],
    )
    .filter(([, items]) => items.length);
  useEffect(() => {
    if (!mobile) return;
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseMobile();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = [
        ...panel.current.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),a[href]",
        ),
      ].filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0],
        last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [mobile, onCloseMobile]);
  return (
    <>
      <button
        type="button"
        className={
          `fixed inset-x-0 top-14 bottom-0 z-30 bg-black/40 lg:hidden ${mobile ? "visible opacity-100" : "invisible opacity-0"}` +
          " focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2" +
          " shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11" +
          " [@media_(max-width:_700px)]:z-34 [@media_(max-width:_700px)]:bg-[#14141466] [@media_(max-width:_700px)]:opacity-[1]"
        }
        data-settings-drawer-scrim=""
        aria-label="Close settings navigation"
        tabIndex={mobile ? 0 : -1}
        onClick={onCloseMobile}
      />
      <aside
        ref={panel}
        className={
          `[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e8e8e8] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#6a6a6a] [--settings-line:#dfdfdf] [--settings-card:#fff] [--settings-subtle:#ededed] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#eee] [:root[data-theme=dark]_&]:[--settings-accent-strong:#eee] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#383838] [:root[data-theme=dark]_&]:[--settings-card:#1d1d1d] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] [:root[data-theme=dark]_&]:bg-[#121212] sidebar flex w-64 flex-none flex-col overflow-y-auto border-line bg-subtle p-4 text-foreground max-lg:w-80 max-lg:max-w-full ${mobile ? "open" : ""}` +
          " flex flex-col w-(--ds-settings-nav-width) flex-none p-(--ds-settings-nav-padding) border-r-0 bg-[#f5f5f5] max-md:sticky max-md:z-35 max-md:top-14 max-md:bottom-0 max-md:left-0 min-w-0 overflow-y-auto min-h-0 overflow-x-hidden overscroll-contain sticky top-17 max-lg:w-56 max-md:shadow-sm max-md:w-60 max-md:h-dvh max-md:max-h-dvh max-md:flex-none text-(--settings-ink)" +
          " [@media_(max-width:850px)]:z-20 [@media_(max-width:850px)]:top-15.5 [@media_(max-width:850px)]:bottom-0 [@media_(max-width:850px)]:-left-62.5 [@media_(max-width:900px)]:w-55 shell-phone:z-36 shell-phone:top-15 shell-phone:bottom-0 shell-phone:left-0 shell-phone:w-[min(290px,85vw)] shell-phone:h-[calc(100dvh-60px)] shell-phone:shadow-(--shadow) shell-desktop:top-0 shell-desktop:w-61 shell-desktop:h-dvh shell-desktop:max-h-dvh [@media_(max-width:_700px)]:w-(--ds-settings-nav-drawer-width) [@media_(max-width:_700px)]:pt-3.5 [@media_(max-width:_880px)]:w-(--ds-settings-nav-drawer-width) [@media_(max-width:_880px)]:max-w-(--ds-settings-nav-drawer-width) shell-phone:fixed! shell-phone:top-15! shell-phone:bottom-0! shell-phone:left-0! shell-phone:w-[min(290px,85vw)]! shell-phone:max-w-full! shell-phone:-translate-x-full shell-phone:data-open:translate-x-0 shell-desktop:col-start-1 shell-desktop:row-span-2"
        }
        data-settings-sidebar=""
        data-open={mobile || undefined}
        aria-label="Settings navigation"
        role={mobile ? "dialog" : undefined}
        aria-modal={mobile || undefined}
      >
        <div
          className="mb-5.5 flex items-center gap-1.5"
          data-settings-sidebar-top=""
        >
          <button
            type="button"
            className="mt-0 mr-0 mb-0 ml-0 flex h-(--ds-settings-back-height) min-h-(--ds-settings-back-height) w-full items-center gap-2 rounded-lg bg-transparent pl-2.25 pr-2.25 pt-0 pb-0 text-left text-[#555] text-(length:--ds-settings-nav-font-size) [:root[data-theme=dark]_&]:text-[#aaa] font-(--ds-settings-nav-font-weight) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:h-auto hover:bg-subtle hover:text-foreground"
            data-settings-back=""
            onClick={onBack}
          >
            <ArrowLeft size={15} />
            <span>Back to app</span>
          </button>
          <button
            className="ml-auto hidden w-8 h-8 place-items-center rounded-lg bg-transparent text-(--settings-muted) max-lg:grid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:size-11"
            ref={close}
            type="button"
            data-settings-drawer-close=""
            aria-label="Close settings navigation"
            onClick={onCloseMobile}
          >
            <X size={17} />
          </button>
        </div>
        <div
          className="flex items-center gap-2.75 pl-2 pr-2 pb-5 font-[650]"
          data-settings-workspace-name=""
        >
          <span
            className="grid w-9.25 h-9.25 flex-none place-items-center rounded-[11px] border border-line bg-[#e8e8e8] text-[14px] font-[750] text-[#171717] last:gap-0.5 last:min-w-0"
            data-settings-workspace-mark=""
            aria-hidden="true"
          >
            {(workspace.name || "W").slice(0, 1).toUpperCase()}
          </span>
          <span className="grid min-w-0 gap-0.5 last:gap-0.5 last:min-w-0">
            <b className="font-[650] truncate text-[14px]">
              {workspace.name || "Workspace"}
            </b>
            <small className="text-[12px] leading-normal text-[#6a6a6a]">
              Workspace settings
            </small>
          </span>
        </div>
        <label
          className="ml-0.5 mr-0.5 mb-4.5 flex min-h-(--ds-settings-search-height) items-center gap-2 rounded-[9px] border border-line bg-white pl-2.25 pr-2.25 pt-0 pb-0 text-muted"
          data-settings-search=""
        >
          <Search size={14} />
          <input
            className="h-(--ds-settings-search-height) text-(length:--ds-settings-nav-font-size) min-w-0 w-full border-0 bg-transparent p-0 text-[#171717] outline-none placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            aria-label="Search settings"
            placeholder="Find a setting…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              className="grid w-6 h-6 flex-none place-items-center rounded-md bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-muted) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="button"
              aria-label="Clear settings search"
              onClick={() => setQuery("")}
            >
              <X size={13} />
            </button>
          )}
        </label>
        <nav
          className="block min-h-0 flex-1 overflow-y-auto overscroll-contain"
          id="settings-navigation"
          aria-label="Settings sections"
        >
          {filtered.map(([group, items]) => (
            <div
              className="m-(--ds-settings-group-gap) mt-0 mr-0 ml-0"
              data-nav-group=""
              key={group}
            >
              <p className="ml-2.5 mr-2.5 mt-0 mb-1.5 text-(--settings-muted) text-(length:--ds-settings-group-label-size) leading-(--ds-settings-group-label-height) font-(--ds-settings-nav-font-weight) normal-case tracking-normal flex items-center h-auto pt-0 pr-1.25 pb-0 pl-1.25">
                {group}
              </p>
              {items.map(([id, label, Icon]) => (
                <button
                  type="button"
                  key={id}
                  className="group relative flex h-(--ds-settings-nav-row-height) min-h-(--ds-settings-nav-row-height) w-full items-center gap-2 overflow-hidden rounded-(--ds-settings-nav-row-radius) border border-transparent m-[var(--ds-settings-nav-row-gap)_0] p-(--ds-settings-nav-row-padding) bg-transparent text-left text-(--settings-muted) text-(length:--ds-settings-nav-font-size) font-(--ds-settings-nav-font-weight) whitespace-nowrap hover:bg-[#e9e9e9] hover:text-[#171717] aria-[current=page]:border-[#171717] aria-[current=page]:bg-[#171717] aria-[current=page]:text-white aria-[current=page]:font-[620] [:root[data-theme=dark]_&]:hover:bg-[#252525] [:root[data-theme=dark]_&]:hover:text-white [:root[data-theme=dark]_&]:aria-[current=page]:border-[#eee] [:root[data-theme=dark]_&]:aria-[current=page]:bg-[#eee] [:root[data-theme=dark]_&]:aria-[current=page]:text-[#171717] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 shell-phone:min-w-11 pointer-coarse:min-h-11 data-[availability=unavailable]:cursor-not-allowed data-[availability=unavailable]:opacity-[.72] data-[availability=unavailable]:hover:bg-transparent data-[availability=unavailable]:hover:text-(--settings-muted)"
                  data-nav-item=""
                  aria-current={section === id ? "page" : undefined}
                  data-nav-id={id}
                  data-availability={
                    isUnavailable(id) ? "unavailable" : undefined
                  }
                  title={
                    isUnavailable(id)
                      ? `${label}: no backend yet (${SETTINGS_UNAVAILABLE.find(([u]) => u === id)?.[3]})`
                      : "undefined"
                  }
                  onClick={() => {
                    onNavigate(id);
                    onCloseMobile();
                  }}
                >
                  <Icon size={15} />
                  <span className="flex-1 overflow-hidden text-ellipsis shell-desktop:rail-collapsed:hidden">
                    {label}
                  </span>
                  {isUnavailable(id) ? (
                    <span
                      className="flex-none text-xs font-semibold overflow-hidden text-ellipsis shell-desktop:rail-collapsed:hidden"
                      data-settings-nav-state=""
                    >
                      Not&nbsp;yet
                    </span>
                  ) : (
                    <ChevronRight
                      className="opacity-0 -translate-x-0.75 transition-[opacity,transform] duration-150 group-aria-[current=page]:translate-x-0 group-aria-[current=page]:opacity-[.65] motion-reduce:transition-none size-4 flex-none"
                      data-settings-nav-chevron=""
                      size={13}
                    />
                  )}
                </button>
              ))}
            </div>
          ))}
          {!filtered.length && (
            <div
              className="ml-2 mr-2 mt-8 mb-8 grid justify-items-center gap-1.75 text-center text-(--settings-muted)"
              data-settings-search-empty=""
            >
              <Search size={18} />
              <b className="font-semibold">No matching settings</b>
              <p>Try a different name or clear your search.</p>
              <button
                className="bg-transparent pl-2 pr-2 pt-1 pb-1 text-[11px] text-(--settings-accent-strong) underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                onClick={() => setQuery("")}
              >
                Clear search
              </button>
            </div>
          )}
        </nav>
        <div
          className="sticky bottom-0 mt-auto -mx-3.5 -mb-3.5 flex flex-none items-center gap-2.25 border-t-0 bg-transparent pl-4 pr-4 pt-3.25 pb-3.25 text-[12px] text-foreground shell-phone:bottom-0"
          data-sidebar-bottom=""
        >
          <span
            className="grid w-7.25 h-7.25 flex-none place-items-center rounded-[9px]"
            data-settings-user-avatar=""
            aria-hidden="true"
          >
            <UserBlobatar user={viewer} />
          </span>
          <span
            className="grid min-w-0 flex-1 gap-px"
            data-settings-user-copy=""
          >
            <b className="font-semibold truncate text-[12px]">
              {viewer.name || "Account"}
            </b>
            <small className="text-[12px] leading-normal text-(--settings-muted) block font-normal overflow-hidden text-ellipsis whitespace-nowrap">
              {viewer.email}
            </small>
          </span>
          <button
            className="grid place-items-center w-8.5 h-8.5 rounded-lg bg-transparent text-[#52525b] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 hover:bg-subtle hover:text-foreground focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:size-11"
            data-logout-button=""
            onClick={onLogout}
            aria-label="Log out"
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
    </>
  );
}

/* Workspace switches (revision 76). Admin-only, like the rest of this page; every member's app
 * reads the result through `workspaceFeatures`. */
function WorkspaceFeaturesGroup({ hidden }: { hidden: boolean }) {
  const enabled = useWorkspaceAgentsEnabled();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const change = async (next: boolean) => {
    const previous = enabled;
    setBusy(true);
    setMessage("Saving…");
    setWorkspaceAgentsEnabled(next);
    try {
      const d = await gql(
        `mutation($on:Boolean!){workspaceFeaturesUpdate(agentsEnabled:$on){agentsEnabled}}`,
        { on: next },
      );
      const saved = Boolean(d.workspaceFeaturesUpdate.agentsEnabled);
      setWorkspaceAgentsEnabled(saved);
      setMessage(
        saved
          ? "Agents are on for everyone in this workspace."
          : "Agents are off for this workspace.",
      );
    } catch (err) {
      setWorkspaceAgentsEnabled(previous);
      setMessage(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) p-0 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line p-4 md:p-7 data-[settings-hidden]:hidden transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
      data-settings-group=""
      data-workspace-features=""
      data-settings-hidden={hidden ? "" : undefined}
    >
      <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
        Features
      </h2>
      <div
        className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-row md:items-center md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
        data-setting-row=""
      >
        <div>
          <b className="font-[620]">Agents</b>
          <p>
            Show the Agents screens (agents, run queue, runners and audit) to
            everyone in this workspace. Changes are recorded in the audit log.
          </p>
        </div>
        <input
          className="relative mt-0 mr-0 mb-0 ml-0 h-5.5 w-9.5 flex-none cursor-pointer appearance-none rounded-[999px] border border-line bg-(--settings-subtle) after:absolute after:top-1 after:left-1 after:size-3.5 after:rounded-full after:bg-muted checked:border-foreground checked:bg-foreground checked:after:translate-x-4 checked:after:bg-panel checked:after:transition-transform checked:after:duration-150 checked:after:ease-out motion-reduce:after:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px] [@media_(max-width:_700px)]:w-10.5 [@media_(max-width:_700px)]:h-6"
          aria-label="Agents"
          data-setting-switch=""
          role="switch"
          type="checkbox"
          checked={enabled === true}
          disabled={busy || enabled === null}
          onChange={(e) => void change(e.target.checked)}
        />
      </div>
      {message && (
        <p
          role={
            message === "Saving…" || message.startsWith("Agents are")
              ? "status"
              : "alert"
          }
          className="wrap-break-word"
        >
          {message}
        </p>
      )}
    </section>
  );
}
function LegacyWorkspaceSettings({
  workspace,
  viewer,
  onSaved,
  onTeamsChanged,
  teams,
  section,
}: {
  workspace: { id: string; name: string; urlKey: string };
  viewer: Viewer;
  onSaved: (w: { id: string; name: string; urlKey: string }) => void;
  onTeamsChanged: (teams: Team[]) => void;
  teams: Team[];
  section: string;
}) {
  const [name, setName] = useState(workspace.name);
  const [urlKey, setUrlKey] = useState(workspace.urlKey);
  const [message, setMessage] = useState("");
  const [teamName, setTeamName] = useState("");
  const [teamKey, setTeamKey] = useState("");
  const [teamMessage, setTeamMessage] = useState("");
  const [teamBusy, setTeamBusy] = useState(false);
  useEffect(() => {
    setName(workspace.name);
    setUrlKey(workspace.urlKey);
  }, [workspace]);
  const createTeam = async (e: React.FormEvent) => {
    e.preventDefault();
    if (teamBusy) return;
    setTeamBusy(true);
    setTeamMessage("");
    try {
      const d = await gql(
        `mutation($input:TeamCreateInput!){teamCreate(input:$input){success team{id name key description states{nodes{id name color type}} members{nodes{id name avatarSeed email}} activeCycle{id name number startsAt endsAt progress}}}}`,
        { input: { name: teamName, key: teamKey } },
      );
      if (!d.teamCreate?.success || !d.teamCreate?.team)
        throw Error("Team creation was rejected");
      onTeamsChanged([...teams, d.teamCreate.team]);
      void getSync()?.pull();
      setTeamName("");
      setTeamKey("");
      setTeamMessage(
        `Created ${d.teamCreate.team.name} (${d.teamCreate.team.key}).`,
      );
    } catch (err) {
      setTeamMessage((err as Error).message);
    } finally {
      setTeamBusy(false);
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-wide=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">
          {section === "api-keys" ? "Personal" : "Administration"}
        </span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          {section === "api-keys"
            ? "Connected apps"
            : section === "workspace"
              ? "Workspace"
              : "Settings"}
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          {section === "api-keys"
            ? "Apps signed in as you, API keys for automation, and your browser sessions."
            : "Manage your workspace identity and create teams for your organization."}
        </p>
      </header>
      <section
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) p-0 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line p-4 md:p-7 data-[settings-hidden]:hidden transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
        data-workspace-settings-form=""
        data-settings-hidden={!(section === "account") ? "" : undefined}
      >
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
          My account
        </h2>
        <p className="wrap-break-word">
          {viewer.name || "Account"} · {viewer.email || ""}
        </p>
        <p className="wrap-break-word">
          Role: {viewer.admin ? "Workspace admin" : "Member"}
        </p>
      </section>
      {section === "api-keys" && (
        <ConnectedAppsSettings isAdmin={!!viewer.admin} />
      )}
      {viewer.admin && (
        <>
          <section
            className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) p-0 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line p-4 md:p-7 data-[settings-hidden]:hidden transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
            data-settings-group=""
            data-workspace-settings-form=""
            data-settings-hidden={!(section === "workspace") ? "" : undefined}
          >
            <span className="hidden">Organization</span>
            <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
              Workspace identity
            </h2>
            <p className="wrap-break-word">
              This name and key identify the workspace across Hoja.
            </p>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const previous = workspace;
                setMessage("Saving…");
                onSaved({ ...workspace, name, urlKey });
                try {
                  const d = await gql(
                    `mutation($input:WorkspaceUpdateInput!){workspaceUpdate(input:$input){success organization{id name urlKey}}}`,
                    { input: { name, urlKey } },
                  );
                  if (!d.workspaceUpdate.success)
                    throw Error("Only workspace admins can update settings");
                  onSaved(d.workspaceUpdate.organization);
                  setMessage("Workspace settings saved.");
                } catch (err) {
                  onSaved(previous);
                  setMessage(errorText(err));
                }
              }}
            >
              <label className="min-w-0 text-[12px] wrap-break-word">
                Workspace name
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </label>
              <label className="min-w-0 text-[12px] wrap-break-word">
                Workspace key
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                  value={urlKey}
                  onChange={(e) => setUrlKey(e.target.value)}
                  required
                />
              </label>
              {message && (
                <p role="status" className="wrap-break-word">
                  {message}
                </p>
              )}
              <button className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]">
                Save settings
              </button>
            </form>
          </section>
          <WorkspaceFeaturesGroup hidden={section !== "workspace"} />
          <form
            className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) p-0 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line p-4 md:p-7 data-[settings-hidden]:hidden transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
            data-settings-group=""
            data-workspace-settings-form=""
            data-settings-hidden={!(section === "workspace") ? "" : undefined}
            onSubmit={createTeam}
          >
            <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
              Create team
            </h2>
            <label className="min-w-0 text-[12px] grid gap-1.5 font-[550] text-foreground wrap-break-word">
              Team name
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base w-full h-9 pt-0 pr-2.5 pb-0 pl-2.5 max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px] [font:inherit]"
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
                required
              />
            </label>
            <label className="min-w-0 text-[12px] grid gap-1.5 font-[550] text-foreground wrap-break-word">
              Team key
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base w-full h-9 pt-0 pr-2.5 pb-0 pl-2.5 max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px] [font:inherit]"
                value={teamKey}
                onChange={(e) => setTeamKey(e.target.value)}
                required
                pattern="[A-Za-z0-9]+"
                title="Use letters and numbers only"
              />
            </label>
            {teamMessage && (
              <p
                role={teamMessage.startsWith("Created ") ? "status" : "alert"}
                className="wrap-break-word"
              >
                {teamMessage}
              </p>
            )}
            <button
              type="submit"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none justify-self-start h-9 min-w-35 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
              disabled={teamBusy || !teamName.trim() || !teamKey.trim()}
            >
              {teamBusy ? "Creating…" : "Create team"}
            </button>
          </form>
        </>
      )}
    </section>
  );
}

type TeamMutate = (
  layer: Layer<Team>,
  run: () => Promise<void>,
) => Promise<void>;
/* One team card: rename, manage members, archive or delete. Every edit overlays the
 * change on the synced team list first (mutate) and rolls it back if the server says no. */
function TeamEditor({
  team,
  admin,
  pending,
  mutate,
  onChanged,
  onRemoved,
  people,
  issueCount,
  projectCount,
}: {
  team: Team;
  admin: boolean;
  pending?: boolean;
  mutate?: TeamMutate;
  onChanged: () => Promise<void> | void;
  onRemoved: () => void;
  people: { id: string; name?: string; email?: string }[];
  issueCount: number;
  projectCount: number;
}) {
  const [name, setName] = useState(team.name),
    [key, setKey] = useState(team.key),
    [description, setDescription] = useState(team.description || "");
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [confirmDelete, setConfirmDelete] = useState(false),
    [confirmText, setConfirmText] = useState(""),
    [deleteIssues, setDeleteIssues] = useState(false);
  useEffect(() => {
    setName(team.name);
    setKey(team.key);
    setDescription(team.description || "");
  }, [team.id, team.name, team.key, team.description]);
  const members = team.members?.nodes || [];
  const candidates = people.filter((p) => !members.some((m) => m.id === p.id));
  const locked = busy || !!pending;
  const run = async (layer: Layer<Team>, fn: () => Promise<void>) => {
    if (mutate) return mutate(layer, fn);
    await fn();
    await onChanged();
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (locked || !admin) return;
    if (
      name.trim() === team.name &&
      key.trim().toUpperCase() === team.key &&
      (description || "") === (team.description || "")
    ) {
      setMessage("No changes to save.");
      return;
    }
    setMessage("Saving…");
    const input = {
      name: name.trim(),
      key: key.trim().toUpperCase(),
      description,
    };
    try {
      await run({ id: team.id, patch: input }, async () => {
        const d = await gql(
          `mutation($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success team{id name key description}}}`,
          { id: team.id, input },
        );
        if (!d.teamUpdate?.success) throw Error("Team could not be updated.");
      });
      setMessage("Team saved.");
    } catch (err) {
      setMessage(errorText(err));
    }
  };
  const archive = async () => {
    if (locked || !admin) return;
    if (
      !(await confirmDialog(
        `Archive ${team.name}? It will be hidden from the sidebar and team lists. Its tasks and projects stay.`,
      ))
    )
      return;
    setMessage("Archiving…");
    try {
      await run({ id: team.id, remove: true }, async () => {
        const d = await gql(
          `mutation($id:String!){teamArchive(id:$id){success}}`,
          { id: team.id },
        );
        if (!d.teamArchive?.success) throw Error("Team could not be archived.");
      });
      onRemoved();
      setMessage("");
    } catch (err) {
      setMessage(errorText(err));
    }
  };
  /* Deleting a team removes what only the team owns: its cycles go with it, its projects
   * stay in the workspace minus this team, and its issues go when deleteIssues is set. */
  const remove = async () => {
    if (locked || !admin || confirmText !== team.key) return;
    const scope = deleteIssues
      ? `Permanently delete ${team.name} and the tasks it owns, with their comments and attachments? Tasks that are closed and not loaded on this screen are deleted too.`
      : `Permanently delete ${team.name}? Its cycles are deleted and its ${projectCount} project${projectCount === 1 ? "" : "s"} stay in the workspace.`;
    if (!(await confirmDialog(scope, { danger: true }))) return;
    setMessage("Deleting…");
    setBusy(true);
    try {
      const d = await gql(
        `mutation($id:String!,$issues:Boolean!){teamDelete(id:$id,deleteIssues:$issues){success}}`,
        { id: team.id, issues: deleteIssues },
      );
      if (!d.teamDelete?.success) throw Error("Team could not be deleted.");
      onRemoved();
      setMessage("");
    } catch (err) {
      const text = errorText(err);
      /* The server refuses while the team still owns any work. Ticking the box is the
       * explicit "delete it anyway" switch: issues go, cycles go, projects stay. */
      if (!deleteIssues && /still has/i.test(text)) {
        setDeleteIssues(true);
        setMessage(
          `${text} Tick the box below and confirm again: its tasks go with it, its cycles are deleted, and its projects stay in the workspace.`,
        );
      } else setMessage(text);
    } finally {
      setBusy(false);
    }
  };
  const setMember = async (userId: string, add: boolean) => {
    if (locked || !admin) return;
    const person = people.find((p) => p.id === userId);
    const next = add
      ? [
          ...members.filter((m) => m.id !== userId),
          { id: userId, name: person?.name || "", email: person?.email },
        ]
      : members.filter((m) => m.id !== userId);
    setMessage(add ? "Adding member…" : "Removing member…");
    try {
      await run(
        { id: team.id, patch: { members: { nodes: next } } },
        async () => {
          const d = await gql(
            `mutation($t:String!,$u:String!){${add ? "teamMemberAdd" : "teamMemberRemove"}(teamId:$t,userId:$u){success}}`,
            { t: team.id, u: userId },
          );
          const res = add ? d.teamMemberAdd : d.teamMemberRemove;
          if (!res?.success)
            throw Error(
              add
                ? "Only workspace admins can add someone to a team."
                : "Only workspace admins can remove a team member.",
            );
        },
      );
      setMessage(add ? "Member added." : "Member removed.");
    } catch (err) {
      setMessage(errorText(err));
    }
  };
  return (
    <article
      className="grid min-w-0 gap-4.5 border-0 border-t border-line bg-transparent pl-0 pr-0 pt-6.75 pb-6.75 rounded-none shadow-[none] last:border-b max-sm:pt-3 max-sm:pr-3 max-sm:pb-3 max-sm:pl-3 [@media_(max-width:560px)]:pt-3.25 [@media_(max-width:560px)]:pr-3.25 [@media_(max-width:560px)]:pb-3.25 [@media_(max-width:560px)]:pl-3.25 [@media_(max-width:_700px)]:pt-4.25 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.25 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
      data-team-card=""
      aria-label={`Team ${team.name}`}
      aria-busy={pending || undefined}
    >
      <header
        className="flex min-w-0 items-center gap-3"
        data-team-card-head=""
      >
        <span
          className="grid w-9.75 h-9.75 flex-none place-items-center rounded-[5px] bg-(--settings-accent-soft) text-[11px] font-bold text-(--settings-accent-strong)"
          data-team-card-mark=""
          aria-hidden="true"
        >
          {(team.key || team.name).slice(0, 2).toUpperCase()}
        </span>
        <div className="grid min-w-0 gap-0.5" data-team-card-title="">
          <b className="font-[550] text-[20px]">{team.name}</b>
          <small className="text-[11px] leading-normal text-(--settings-muted) wrap-break-word">
            {team.key} · {members.length} member
            {members.length === 1 ? "" : "s"} · {issueCount} task
            {issueCount === 1 ? "" : "s"} · {projectCount} project
            {projectCount === 1 ? "" : "s"}
          </small>
        </div>
        {pending && (
          <span className="self-center ml-auto text-muted text-[11px]">
            Creating…
          </span>
        )}
      </header>
      {admin ? (
        <form
          className="grid min-w-0 grid-cols-1 items-end gap-[14px_12px] rounded-md border border-line bg-(--settings-card) pt-4.5 pr-4.5 pb-4.5 pl-4.5 md:grid-cols-2 md:grid-cols-3"
          data-team-card-fields=""
          onSubmit={save}
        >
          <label className="min-w-0 text-[11px] grid gap-1.25 text-(--settings-muted)">
            Name
            <input
              className="min-w-0 rounded-lg border border-line bg-(--settings-card) text-[13px] text-(--settings-ink) hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full pt-1.75 pr-2.25 pb-1.75 pl-2.25 disabled:opacity-60 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px] disabled:opacity-[.6]"
              aria-label={`Name for ${team.name}`}
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={locked}
            />
          </label>
          <label className="min-w-0 text-[11px] grid gap-1.25 text-(--settings-muted)">
            Key
            <input
              className="min-w-0 rounded-lg border border-line bg-(--settings-card) text-[13px] text-(--settings-ink) hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full pt-1.75 pr-2.25 pb-1.75 pl-2.25 disabled:opacity-60 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px] disabled:opacity-[.6]"
              aria-label={`Key for ${team.name}`}
              required
              maxLength={8}
              pattern="[A-Za-z0-9]+"
              title="Letters and numbers only"
              value={key}
              onChange={(e) => setKey(e.target.value.toUpperCase())}
              disabled={locked}
            />
          </label>
          <label
            className="col-span-full min-w-0 text-[11px] grid gap-1.25 text-(--settings-muted) md:col-span-1"
            data-team-card-description=""
          >
            Description
            <input
              className="min-w-0 rounded-lg border border-line bg-(--settings-card) text-[13px] text-(--settings-ink) hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full pt-1.75 pr-2.25 pb-1.75 pl-2.25 disabled:opacity-60 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px] disabled:opacity-[.6]"
              aria-label={`Description for ${team.name}`}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={locked}
            />
          </label>
          <div
            className="col-span-full flex flex-wrap gap-1.75 pt-0.5 [&_[data-control-outline].danger:hover:not(:disabled)]:border-danger"
            data-team-card-actions=""
          >
            <button
              type="submit"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-[7px] border border-foreground bg-accent pl-3 pr-3 pt-1.5 pb-1.5 text-[12px] font-[550] text-white shadow-[none] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent enabled:hover:bg-danger/10 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
              disabled={locked || !name.trim() || !key.trim()}
            >
              {busy ? "Saving…" : "Save team"}
            </button>
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-[7px] border border-line bg-panel pl-3 pr-3 pt-1.5 pb-1.5 text-[12px] text-inherit hover:bg-subtle hover:transition-none focus-visible:outline-accent enabled:hover:bg-danger/10 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
              disabled={locked}
              onClick={() => void archive()}
            >
              Archive
            </button>
            {!confirmDelete && (
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-[7px] border border-line bg-transparent pl-3 pr-3 pt-1.5 pb-1.5 text-[12px] text-danger hover:bg-subtle hover:transition-none focus-visible:outline-accent enabled:hover:bg-danger/10 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
                disabled={locked}
                onClick={() => {
                  setConfirmDelete(true);
                  setMessage("");
                }}
              >
                Delete team…
              </button>
            )}
          </div>
        </form>
      ) : (
        <p className="mt-0 mr-0 mb-0 ml-0 text-muted text-[12px]">
          Only workspace admins can edit teams.
        </p>
      )}
      <section
        className="grid min-w-0 gap-3 border-t border-line pt-5"
        data-team-card-members=""
        aria-label={`Members of ${team.name}`}
      >
        <div
          className="flex flex-wrap items-baseline gap-2"
          data-team-card-members-head=""
        >
          <b className="font-semibold text-[12px]">Members</b>
          <small className="text-[11px] leading-normal text-muted">
            Assignees must belong to the team.
          </small>
        </div>
        <ul className="flex flex-wrap gap-1.5 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0 list-none min-w-0">
          {members.map((m) => (
            <li
              key={m.id}
              className="inline-flex max-w-full items-center gap-1.5 rounded-[99px] border border-line bg-(--settings-subtle) pl-0.75 pr-1 pt-0.75 pb-0.75 text-[12px]"
              data-team-member=""
            >
              <span
                className="grid w-4.5 h-4.5 flex-none place-items-center rounded-[50%]"
                data-team-member-avatar=""
                aria-hidden="true"
              >
                <UserBlobatar user={m} />
              </span>
              <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                {m.name || m.email}
              </span>
              {admin && (
                <button
                  type="button"
                  className="grid flex-none w-5 h-5 rounded-[50%] bg-transparent text-muted pt-0 pr-0 pb-0 pl-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 [&:hover:not(:disabled)]:bg-subtle [&:hover:not(:disabled)]:text-danger focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-team-member-remove=""
                  aria-label={`Remove ${m.name || m.email} from ${team.name}`}
                  title="Remove from team"
                  disabled={locked}
                  onClick={() => void setMember(m.id, false)}
                >
                  <X size={13} />
                </button>
              )}
            </li>
          ))}
          {!members.length && (
            <li className="pt-1.5 pr-0 pb-1.5 pl-0 text-muted text-[12px]">
              No members yet. Anyone you add here can be assigned tasks in this
              team.
            </li>
          )}
        </ul>
        {admin && (
          <div
            className="grid gap-1.25 max-w-[320px] text-muted text-[11px]"
            data-team-member-add=""
          >
            <span
              className="text-xs font-[560] text-(--settings-muted)"
              data-settings-field-label=""
            >
              Add member
            </span>
            <SettingsSelect
              label={`Add a member to ${team.name}`}
              value=""
              disabled={locked || !candidates.length}
              placeholder={
                candidates.length
                  ? "Choose a person…"
                  : "Everyone is already in this team"
              }
              options={candidates.map((p) => ({
                value: p.id,
                label: p.name || p.email || "Workspace member",
                description: p.name ? p.email : "Eligible workspace member",
                meta: "Add to team",
              }))}
              onChange={(id) => {
                if (id) void setMember(id, true);
              }}
            />
          </div>
        )}
      </section>
      {confirmDelete && (
        <section
          className="grid min-w-0 gap-2.5 rounded-[10px] border border-line bg-[color-mix(in_srgb,var(--settings-danger)_5%,var(--settings-card))] pt-3 pr-3 pb-3 pl-3 border-danger/40"
          data-team-card-delete=""
          role="group"
          aria-label={`Delete ${team.name}`}
        >
          <p className="mt-0 mr-0 mb-0 ml-0 text-[12px] leading-normal">
            <b className="font-semibold">Delete {team.name}?</b> Cycles are
            deleted with the team. Projects stay in the workspace without this
            team.
          </p>
          <label
            className="flex items-start gap-2 text-[12px] min-w-0"
            data-team-card-delete-check=""
          >
            <input
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 mt-0.5 flex-none max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
              type="checkbox"
              checked={deleteIssues}
              onChange={(e) => setDeleteIssues(e.target.checked)}
              disabled={busy}
            />{" "}
            Also delete this team’s tasks ({issueCount} loaded here) with their
            comments and attachments
          </label>
          <label
            className="grid gap-1.25 max-w-65 text-muted text-[11px] min-w-0"
            data-team-card-delete-confirm=""
          >
            Type {team.key} to confirm
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full pt-1.5 pr-2.25 pb-1.5 pl-2.25 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label={`Confirm delete ${team.name}`}
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              disabled={busy}
            />
          </label>
          <div
            className="col-span-full flex flex-wrap gap-1.75 pt-0.5 [&_[data-control-outline].danger:hover:not(:disabled)]:border-danger"
            data-team-card-actions=""
          >
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 border border-danger bg-transparent text-danger min-h-8 pt-1.5 pr-3 pb-1.5 pl-3 rounded-[7px] text-[12px] font-[550] focus-visible:outline-accent enabled:hover:bg-danger/10 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
              disabled={busy || confirmText !== team.key}
              onClick={() => void remove()}
            >
              {busy ? "Deleting…" : "Permanently delete"}
            </button>
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-[7px] border border-line bg-panel pl-3 pr-3 pt-1.5 pb-1.5 text-[12px] text-inherit hover:bg-subtle hover:transition-none focus-visible:outline-accent enabled:hover:bg-danger/10 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
              disabled={busy}
              onClick={() => {
                setConfirmDelete(false);
                setConfirmText("");
                setDeleteIssues(false);
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}
      {message && (
        <p
          className={
            (message.endsWith("saved.") ||
            message.endsWith("added.") ||
            message.endsWith("removed.")
              ? "mt-0 mr-0 mb-0 ml-0 text-[12px] text-danger"
              : "mt-0 mr-0 mb-0 ml-0 text-[12px] text-danger max-w-72") +
            " text-danger"
          }
          data-team-card-message=""
          role={
            message.endsWith("saved.") ||
            message.endsWith("added.") ||
            message.endsWith("removed.") ||
            message === "Saving…" ||
            message === "Adding member…" ||
            message === "Removing member…" ||
            message === "Deleting…" ||
            message === "Archiving…" ||
            message === "No changes to save."
              ? "status"
              : "alert"
          }
        >
          {message}
        </p>
      )}
    </article>
  );
}
let teamPeopleCache: { id: string; name?: string; email?: string }[] | null =
  null;
// Cycle settings are per team (as in Linear); this is the workspace-level way in.
function CycleSettingsIndex({
  teams,
  onOpen,
  onBack,
}: {
  teams: Team[];
  onOpen: (team: Team) => void;
  onBack: () => void;
}) {
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <button
          className="hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:mb-3 shell-phone:bg-transparent shell-phone:text-muted"
          data-settings-mobile-back=""
          onClick={onBack}
        >
          ← Settings
        </button>
        <span className="hidden">Tasks</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Cycles
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Cycles are configured per team: automatic cycles, start day, length,
          cooldown, auto-complete and automations.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Teams
        </h2>
        {teams.length ? (
          teams.map((team) => (
            <div
              key={team.id}
              className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-row md:items-center md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
              data-setting-row=""
            >
              <div>
                <b className="font-[620]">{team.name}</b>
                <p>{team.key}</p>
              </div>
              <div
                className="flex w-full max-w-full flex-none flex-wrap gap-1.25 rounded-[10px] border border-line bg-(--settings-subtle) pt-0.75 pr-0.75 pb-0.75 pl-0.75 md:w-auto first:min-w-0 shell-phone:w-full [@media_(max-width:_700px)]:w-full"
                data-setting-control=""
              >
                <button
                  className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-7.5 flex-1 rounded-(--ui-radius-control) border border-transparent bg-transparent pl-2.5 pr-2.5 text-[12px] text-(--settings-muted) aria-pressed:border-line aria-pressed:bg-panel aria-pressed:text-foreground focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
                  type="button"
                  onClick={() => onOpen(team)}
                >
                  Cycle settings
                </button>
              </div>
            </div>
          ))
        ) : (
          <p>No teams yet. Create one under Teams.</p>
        )}
      </div>
    </section>
  );
}
function TeamsSettings({
  teams: teamsProp,
  onTeamsChanged,
  viewer,
}: {
  teams: Team[];
  onTeamsChanged: (t: Team[]) => void;
  viewer: Viewer;
}) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [people, setPeople] = useState<
    { id: string; name?: string; email?: string }[]
  >(() => teamPeopleCache || []);
  const [error, setError] = useState("");
  const { teams, push, refresh } = useSyncedTeams(teamsProp, onTeamsChanged);
  // Per-team issue and project counts, straight from the shared stores (cards stay cheap).
  const syncedIssues = useSyncIssues();
  const syncedProjects = useProjects();
  const issueCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of syncedIssues) {
      const id = i.team?.id;
      if (id) m.set(id, (m.get(id) || 0) + 1);
    }
    return m;
  }, [syncedIssues]);
  const projectCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of syncedProjects)
      for (const t of p.teams?.nodes || []) m.set(t.id, (m.get(t.id) || 0) + 1);
    return m;
  }, [syncedProjects]);
  const peopleVersion = useEntityVersion(["member", "user"]);
  useEffect(() => {
    let live = true;
    gql(
      "query TeamPeople{organizationMembers(first:100){nodes{id name email active}}}",
    )
      .then((d) => {
        if (!live) return;
        const active = d.organizationMembers.nodes.filter(
          (m: { active?: boolean }) => m.active !== false,
        );
        teamPeopleCache = active;
        setPeople(active);
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [peopleVersion]);
  const reload = () => refresh();
  // Optimistic team edit: overlay now, keep it until the sync pull has the server's version; roll back on failure.
  const mutate: TeamMutate = async (layer, run) => {
    setError("");
    const handle = push(layer);
    try {
      await run();
      handle.settleAfter(refresh());
    } catch (e) {
      handle.drop();
      if (layer.remove) setError(errorText(e));
      throw e;
    }
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const input = {
      name: name.trim(),
      key: key.trim().toUpperCase(),
      memberIds,
    };
    const tempId = `pending-team-${input.key}-${Date.now()}`;
    const placeholder: Team = {
      id: tempId,
      name: input.name,
      key: input.key,
      states: { nodes: [] },
      members: {
        nodes: people
          .filter((p) => memberIds.includes(p.id))
          .map((p) => ({ id: p.id, name: p.name || "", email: p.email })),
      },
    };
    setName("");
    setKey("");
    setMemberIds([]);
    try {
      await mutate(
        {
          id: tempId,
          add: placeholder,
          replacedBy: (t) => t.key === input.key,
        },
        async () => {
          const d = await gql(
            "mutation($input:TeamCreateInput!){teamCreate(input:$input){success team{id key}}}",
            { input },
          );
          if (!d.teamCreate?.success) throw Error("Could not create team");
        },
      );
    } catch (err) {
      setName(input.name);
      setKey(input.key);
      setMemberIds(input.memberIds);
      setError(errorText(err));
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-wide=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Teams
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Shape how your organization works. Manage team identity, membership,
          and lifecycle in one place.
        </p>
      </header>
      {viewer.admin && (
        <form
          className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) pt-7 pr-7 pb-7 pl-7 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line md:p-7 transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
          data-settings-group=""
          data-workspace-settings-form=""
          onSubmit={create}
        >
          <span className="hidden">Team setup</span>
          <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
            Create a new team
          </h2>
          <p
            className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted) wrap-break-word"
            data-settings-section-intro=""
          >
            Give the team a clear identity and choose its initial members.
          </p>
          <div
            className="grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-4 [@media_(max-width:_700px)]:gap-3.5"
            data-settings-form-grid=""
          >
            <label className="min-w-0 text-xs wrap-break-word">
              Name
              <input
                className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="min-w-0 text-xs wrap-break-word">
              Key
              <input
                className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                required
                maxLength={8}
                pattern="[A-Za-z0-9]+"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="ENG"
              />
            </label>
            <fieldset className="col-span-full rounded-[10px] border border-line pt-3 pr-3.5 pb-3 pl-3.5 grid gap-2 mt-0 mr-0 mb-0 ml-0">
              <legend className="pl-1.5 pr-1.5 text-[12px] text-muted pt-0 pb-0 font-[550]">
                Members
              </legend>
              {people.map((p) => (
                <label
                  className="min-w-0 text-[13px] flex items-center gap-2 text-foreground flex-row justify-start w-auto justify-self-start wrap-break-word"
                  key={p.id}
                >
                  <input
                    className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-4 h-4 mt-0 mr-0 mb-0 ml-0 flex-none max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                    type="checkbox"
                    checked={memberIds.includes(p.id)}
                    onChange={(e) =>
                      setMemberIds((c) =>
                        e.target.checked
                          ? [...c, p.id]
                          : c.filter((x) => x !== p.id),
                      )
                    }
                  />{" "}
                  {p.name || p.email}
                </label>
              ))}
            </fieldset>
          </div>
          <div
            className="mt-4.5 flex flex-col items-start gap-3.5 md:flex-row md:items-center md:gap-3.5 [@media_(max-width:_700px)]:gap-2.25"
            data-settings-form-actions=""
          >
            <button className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:min-h-10">
              Create team
            </button>
            <span>Team keys use letters and numbers.</span>
          </div>
        </form>
      )}
      {error && (
        <p
          className="block text-(--settings-danger) mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
          role="alert"
        >
          {error}
        </p>
      )}
      <div className="grid min-w-0 gap-4.5" data-team-card-list="">
        {teams.length ? (
          teams.map((t) => (
            <TeamEditor
              key={t.id}
              team={t}
              admin={!!viewer.admin}
              pending={t.id.startsWith("pending-team-")}
              mutate={mutate}
              people={people}
              issueCount={issueCounts.get(t.id) || 0}
              projectCount={projectCounts.get(t.id) || 0}
              onChanged={() => reload()}
              onRemoved={() => void reload()}
            />
          ))
        ) : (
          <p
            className="mt-0 mr-0 mb-0 ml-0 pt-4.5 pr-4.5 pb-4.5 pl-4.5 rounded-xl text-muted text-[13px] border border-dashed border-line"
            data-team-card-empty=""
          >
            No teams yet. Create one above to start tracking work.
          </p>
        )}
      </div>
    </section>
  );
}

function WorkspaceSettings({
  workspace,
  viewer,
  onSaved,
  onProfileSaved,
  onTeamsChanged,
  teams,
  section,
  theme,
  onTheme,
  onFontScale,
  fontScale,
  preferenceMessage,
  onBack,
  onOpenCycleSettings,
}: {
  workspace: { id: string; name: string; urlKey: string };
  viewer: Viewer;
  onSaved: (w: { id: string; name: string; urlKey: string }) => void;
  onProfileSaved: (name: string) => void;
  onTeamsChanged: (teams: Team[]) => void;
  teams: Team[];
  section: string;
  theme: string;
  onTheme: (theme: string) => void;
  onFontScale: (scale: string) => void;
  fontScale: string;
  preferenceMessage: string;
  onBack: () => void;
  onOpenCycleSettings: (team: Team) => void;
}) {
  if (section === "initiatives")
    return <InitiativeSettings viewer={viewer} teams={teams} />;
  if (section === "cycles")
    return (
      <CycleSettingsIndex
        teams={teams}
        onOpen={onOpenCycleSettings}
        onBack={onBack}
      />
    );
  if (section === "preferences")
    return (
      <section
        className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
        data-settings-page=""
      >
        <header
          className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
          data-settings-page-heading=""
        >
          <button
            className="hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:mb-3 shell-phone:bg-transparent shell-phone:text-muted"
            data-settings-mobile-back=""
            onClick={onBack}
          >
            ← Settings
          </button>
          <span className="hidden">{workspace.name} · Settings</span>
          <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
            Preferences
          </h1>
          <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
            Appearance preferences are saved to your account and follow you
            across devices.
          </p>
        </header>
        <div
          className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          data-settings-group=""
        >
          <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
            Appearance
          </h2>
          {preferenceMessage && (
            <p
              role={
                preferenceMessage.startsWith("Could not") ? "alert" : "status"
              }
            >
              {preferenceMessage}
            </p>
          )}
          <div
            className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-col md:items-start md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
            data-setting-row=""
            data-style-hooks="settings-theme-row"
          >
            <div>
              <b className="font-[620]">Theme</b>
              <p>Choose the appearance used in this browser.</p>
              <small className="text-[10px] leading-normal text-(--settings-muted)">
                Saved to your account
              </small>
            </div>
            <div
              className="flex w-[min(100%,560px)] max-w-full flex-none flex-wrap gap-3.5 rounded-[10px] border border-line bg-transparent pt-0 pr-0 pb-0 pl-0 md:w-full flex-row border-0 first:min-w-0 shell-phone:w-full [@media_(max-width:_700px)]:w-full"
              data-setting-control=""
              data-theme-control=""
              data-style-hooks="settings-theme-control"
            >
              <button
                disabled={preferenceMessage === "Saving…"}
                onClick={() => onTheme("light")}
                aria-pressed={theme === "light"}
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 grid min-w-0 flex-1 grid-cols-2 overflow-hidden rounded-lg border border-line bg-(--settings-card) pt-0 pr-0 pb-0 pl-0 text-left text-(--settings-accent-strong) aria-pressed:border-foreground aria-pressed:ring-1 aria-pressed:ring-foreground focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:min-w-0"
              >
                <span
                  className="col-span-full grid h-23 grid-cols-[5px_minmax(0,1fr)] grid-rows-[10px_8px_8px] gap-2 bg-[#e9e9e9] pt-4.5 pr-4.5 pb-4.5 pl-4.5 [@media_(max-width:_700px)]:h-18 [@media_(max-width:_700px)]:pt-3.25 [@media_(max-width:_700px)]:pr-3.25 [@media_(max-width:_700px)]:pb-3.25 [@media_(max-width:_700px)]:pl-3.25 [@media_(max-width:_390px)]:h-20.5"
                  data-theme-choice-preview=""
                  aria-hidden="true"
                >
                  <i className="block rounded-xs bg-[#b7b7b7] first:row-span-3 first:bg-neutral-600 first:bg-[#555]" />
                  <i className="block rounded-xs bg-[#b7b7b7] first:row-span-3 first:bg-neutral-600 first:bg-[#555]" />
                  <i className="block rounded-xs bg-[#b7b7b7] first:row-span-3 first:bg-neutral-600 first:bg-[#555]" />
                </span>
                <span
                  className="grid gap-0.75 pt-3.25 pr-3.5 pb-3.25 pl-3.5 text-left [@media_(max-width:_700px)]:pt-2.75 [@media_(max-width:_700px)]:pr-2.75 [@media_(max-width:_700px)]:pb-2.75 [@media_(max-width:_700px)]:pl-2.75"
                  data-theme-choice-copy=""
                >
                  <b className="font-[620]">Light</b>
                  <small className="text-[10px] leading-normal text-(--settings-muted)">
                    Bright and clear
                  </small>
                </span>
                <Sun
                  className="mr-3 hidden self-center justify-self-end text-muted md:block"
                  size={16}
                />
              </button>
              <button
                disabled={preferenceMessage === "Saving…"}
                onClick={() => onTheme("dark")}
                aria-pressed={theme === "dark"}
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 grid min-w-0 flex-1 grid-cols-2 overflow-hidden rounded-lg border border-line bg-(--settings-card) pt-0 pr-0 pb-0 pl-0 text-left text-(--settings-accent-strong) aria-pressed:border-foreground aria-pressed:ring-1 aria-pressed:ring-foreground focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:min-w-0"
              >
                <span
                  className="col-span-full grid h-23 grid-cols-[5px_minmax(0,1fr)] grid-rows-[10px_8px_8px] gap-2 bg-[#202020] pt-4.5 pr-4.5 pb-4.5 pl-4.5 [@media_(max-width:_700px)]:h-18 [@media_(max-width:_700px)]:pt-3.25 [@media_(max-width:_700px)]:pr-3.25 [@media_(max-width:_700px)]:pb-3.25 [@media_(max-width:_700px)]:pl-3.25 [@media_(max-width:_390px)]:h-20.5"
                  data-theme-choice-preview=""
                  aria-hidden="true"
                >
                  <i className="block rounded-xs bg-[#707070] first:row-span-3 first:bg-neutral-600 first:bg-[#eee]" />
                  <i className="block rounded-xs bg-[#707070] first:row-span-3 first:bg-neutral-600 first:bg-[#eee]" />
                  <i className="block rounded-xs bg-[#707070] first:row-span-3 first:bg-neutral-600 first:bg-[#eee]" />
                </span>
                <span
                  className="grid gap-0.75 pt-3.25 pr-3.5 pb-3.25 pl-3.5 text-left [@media_(max-width:_700px)]:pt-2.75 [@media_(max-width:_700px)]:pr-2.75 [@media_(max-width:_700px)]:pb-2.75 [@media_(max-width:_700px)]:pl-2.75"
                  data-theme-choice-copy=""
                >
                  <b className="font-[620]">Dark</b>
                  <small className="text-[10px] leading-normal text-(--settings-muted)">
                    Low-light focus
                  </small>
                </span>
                <Moon
                  className="mr-3 hidden self-center justify-self-end text-muted md:block"
                  size={16}
                />
              </button>
            </div>
          </div>
          <div
            className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-row md:items-center md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
            data-setting-row=""
          >
            <div>
              <b className="font-[620]">Font size</b>
              <p>Adjust interface text size.</p>
              <small className="text-[10px] leading-normal text-(--settings-muted)">
                Saved to your account
              </small>
            </div>
            <SettingsSelect
              label="Font size"
              value={fontScale}
              disabled={preferenceMessage === "Saving…"}
              options={[
                {
                  value: "90",
                  label: "Compact",
                  description: "Denser interface text for smaller screens",
                  meta: "90%",
                },
                {
                  value: "100",
                  label: "Default",
                  description: "The balanced Hoja reading size",
                  meta: "100%",
                },
                {
                  value: "110",
                  label: "Large",
                  description: "Larger text across the workspace",
                  meta: "110%",
                },
              ]}
              onChange={onFontScale}
            />
          </div>
        </div>
      </section>
    );
  if (section === "profile")
    return <ProfileSettings viewer={viewer} onSaved={onProfileSaved} />;
  if (section === "notifications") return <NotificationSettings />;
  if (section === "model-providers")
    return viewer.admin ? (
      <ModelProvidersSettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "extensions")
    return viewer.admin ? (
      <ExtensionsSettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "agent-conversations") return <AgentConversationsSettings />;
  if (section === "connections")
    return <ConnectorsSettings isAdmin={!!viewer.admin} />;
  if (section === "knowledge") return <KnowledgeSettings />;
  if (section === "teams")
    return (
      <TeamsSettings
        teams={teams}
        onTeamsChanged={onTeamsChanged}
        viewer={viewer}
      />
    );
  if (section === "issue-labels")
    return <IssueLabelsSettings viewer={viewer} />;
  if (section === "issue-templates")
    return viewer.admin ? (
      <TaskTemplatesSettings teams={teams} />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "billing")
    return viewer.admin ? (
      <BillingUsageSettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "slas")
    return viewer.admin ? (
      <SlaSettings teams={teams} />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "project-statuses")
    return viewer.admin ? (
      <ProjectStatusesSettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "project-templates")
    return viewer.admin ? (
      <ProjectTemplatesSettings teams={teams} />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "permissions")
    return viewer.admin ? (
      <PermissionsSettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  if (section === "security")
    return viewer.admin ? (
      <SecuritySettings />
    ) : (
      <SettingsNotFound onBack={onBack} />
    );
  const unavailable = SETTINGS_UNAVAILABLE.find(([id]) => id === section);
  if (unavailable)
    return (
      <SettingsUnavailable
        name={unavailable[1]}
        needs={unavailable[2]}
        issue={unavailable[3]}
        onBack={onBack}
      />
    );
  if (["account", "api-keys", "workspace", "members"].includes(section))
    return (
      <LegacyWorkspaceSettings
        workspace={workspace}
        viewer={viewer}
        onSaved={onSaved}
        onTeamsChanged={onTeamsChanged}
        teams={teams}
        section={section}
      />
    );
  return <SettingsNotFound onBack={onBack} />;
}
function ProfileSettings({
  viewer,
  onSaved,
}: {
  viewer: Viewer;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState(viewer.name || ""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const saving = useRef(false);
  useEffect(() => {
    if (!saving.current) setName(viewer.name || "");
  }, [viewer.name]);
  // The new name shows across the app at once; it is restored if the server refuses it.
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const previous = viewer.name || "";
    const next = name;
    setBusy(true);
    saving.current = true;
    setMessage("Saving…");
    onSaved(next);
    try {
      const d = await gql(
        `mutation($name:String!){profileNameUpdate(name:$name){success user{id name avatarSeed displayName email admin}}}`,
        { name: next },
      );
      if (!d.profileNameUpdate.success)
        throw Error("Name must be 1–80 characters.");
      onSaved(d.profileNameUpdate.user.name);
      setMessage("Profile name saved.");
    } catch (e) {
      onSaved(previous);
      setMessage(errorText(e));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Personal</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Profile
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Update your name. Email is managed by your sign-in identity.
        </p>
      </header>
      <form
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[7px] bg-(--settings-card) pt-7 pr-7 pb-7 pl-7 text-(--settings-ink) shadow-[none] md:mb-9 border border-line md:p-7 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
        data-profile-form=""
        onSubmit={save}
      >
        <div
          className="mb-5.75 flex flex-wrap items-start gap-3 border-b border-line pb-5 md:items-center"
          data-profile-summary=""
        >
          <span
            className="grid w-10.75 h-10.75 flex-none place-items-center rounded-[50%]"
            data-profile-avatar=""
            aria-hidden="true"
          >
            <UserBlobatar user={viewer} />
          </span>
          <div className="min-w-0 flex-1">
            <b className="font-semibold">{viewer.name || "Your profile"}</b>
            <p className="mt-0.75 mb-0 break-all text-[12px] text-(--settings-muted)">
              {viewer.email}
            </p>
          </div>
          <span
            className="inline-flex w-fit min-h-6.25 items-center whitespace-nowrap rounded-[999px] border border-line bg-(--settings-subtle) pl-2 pr-2 pt-0.5 pb-0.5 text-[11px] font-semibold text-(--settings-muted) [@media_(max-width:_700px)]:ml-13.75"
            data-settings-badge=""
          >
            {viewer.admin ? "Workspace admin" : "Member"}
          </span>
        </div>
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-4 [@media_(max-width:_700px)]:gap-3.5"
          data-settings-form-grid=""
        >
          <label className="min-w-0 text-xs">
            Display name
            <input
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Profile display name"
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="min-w-0 text-xs">
            Email
            <input
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Profile email"
              readOnly
              value={viewer.email || ""}
            />
          </label>
          {message && (
            <p
              role={
                message.includes("saved") || message === "Saving…"
                  ? "status"
                  : "alert"
              }
            >
              {message}
            </p>
          )}
        </div>
        <div
          className="mt-4.5 flex flex-col items-start gap-3.5 md:flex-row md:items-center md:gap-3.5 [@media_(max-width:_700px)]:gap-2.25"
          data-settings-form-actions=""
        >
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:min-h-10"
            disabled={busy || !name.trim()}
          >
            {busy ? "Saving…" : "Save profile"}
          </button>
          <span>Your email is managed by your sign-in identity.</span>
        </div>
      </form>
    </section>
  );
}
const NOTIFICATION_GROUPS = [
  [
    "Tasks",
    [
      [
        "issueAssigned",
        "Assigned to me",
        "Someone makes you the assignee of a task.",
      ],
      ["issueMention", "Mentioned", "Someone @mentions you in a task comment."],
      [
        "issueDecision",
        "Decision requested",
        "A task you own or follow asks for a decision.",
      ],
      [
        "issueComment",
        "New comments",
        "Someone comments on a task you follow.",
      ],
      [
        "issueStatusChanged",
        "Status changes",
        "A task you follow moves to another status.",
      ],
      [
        "issueUpdated",
        "Priority and due date changes",
        "A task you follow changes priority or due date.",
      ],
    ],
  ],
  [
    "Projects",
    [
      [
        "projectLeadAssigned",
        "Made project lead",
        "Someone makes you the lead of a project.",
      ],
      [
        "projectMemberAdded",
        "Added to a project",
        "Someone adds you as a member of a project.",
      ],
      [
        "projectMention",
        "Mentioned",
        "Someone @mentions you in a project update or a reply.",
      ],
      [
        "projectUpdatePosted",
        "Project updates",
        "A project you follow posts an update.",
      ],
      [
        "projectUpdateReply",
        "Replies",
        "Someone replies to an update you posted or replied to.",
      ],
      [
        "projectUpdated",
        "Status, target date and lead changes",
        "A project you follow changes status, target date or lead.",
      ],
    ],
  ],
  [
    "Initiatives",
    [
      [
        "initiativeOwnerAssigned",
        "Made initiative owner",
        "Someone makes you the owner of an initiative.",
      ],
      [
        "initiativeUpdatePosted",
        "Initiative updates",
        "An initiative you follow posts an update.",
      ],
      [
        "initiativeUpdated",
        "Status, target date and owner changes",
        "An initiative you follow changes status, target date or owner.",
      ],
    ],
  ],
  [
    "Cycles",
    [
      ["cycleStarted", "Cycle started", "A cycle you subscribed to starts."],
      [
        "cycleCompleted",
        "Cycle ended",
        "A cycle you subscribed to ends, with what got done.",
      ],
    ],
  ],
] as const;
type NotificationKind =
  (typeof NOTIFICATION_GROUPS)[number][1][number][0] | "issueCreated";
type NotificationPrefs = Record<NotificationKind, boolean>;
const NOTIFICATION_KIND_KEYS: NotificationKind[] = [
  ...NOTIFICATION_GROUPS.flatMap(([, kinds]) => kinds.map(([k]) => k)),
  "issueCreated",
];
const NOTIFICATION_PREF_FIELDS = NOTIFICATION_KIND_KEYS.join(" ");
const allNotificationsOn = (): NotificationPrefs =>
  Object.fromEntries(
    NOTIFICATION_KIND_KEYS.map((k) => [k, true]),
  ) as NotificationPrefs;
const readNotificationPrefs = (
  d: Record<string, unknown>,
): NotificationPrefs => {
  const next = allNotificationsOn();
  for (const k of Object.keys(next) as NotificationKind[])
    if (typeof d[k] === "boolean") next[k] = d[k] as boolean;
  return next;
};
let notificationPrefsCache: NotificationPrefs | null = null;
function NotificationSettings() {
  const [prefs, setPrefs] = useState<NotificationPrefs>(
      () => notificationPrefsCache || allNotificationsOn(),
    ),
    [message, setMessage] = useState(() =>
      notificationPrefsCache ? "" : "Loading…",
    );
  const loaded = useRef(!!notificationPrefsCache);
  useEffect(() => {
    let live = true;
    gql(`{myNotificationPreferences{success ${NOTIFICATION_PREF_FIELDS}}}`)
      .then((d) => {
        if (!live) return;
        const next = readNotificationPrefs(d.myNotificationPreferences);
        notificationPrefsCache = next;
        loaded.current = true;
        setPrefs(next);
        setMessage((m) => (m === "Loading…" ? "" : m));
      })
      .catch((e) => {
        if (live) setMessage(errorText(e));
      });
    return () => {
      live = false;
    };
  }, []);
  // Toggle immediately; restore the previous value if the server refuses.
  const change = async (key: keyof NotificationPrefs, enabled: boolean) => {
    const previous = prefs[key];
    setPrefs((p) => ({ ...p, [key]: enabled }));
    setMessage("Saving…");
    try {
      const d = await gql(
        `mutation($kind:String!,$enabled:Boolean!){notificationPreferenceSet(kind:$kind,enabled:$enabled){success ${NOTIFICATION_PREF_FIELDS}}}`,
        { kind: key, enabled },
      );
      if (!d.notificationPreferenceSet.success)
        throw Error("Preference could not be saved");
      const next = readNotificationPrefs(d.notificationPreferenceSet);
      notificationPrefsCache = next;
      setPrefs(next);
      setMessage("Notification preference saved.");
    } catch (e) {
      setPrefs((p) => ({ ...p, [key]: previous }));
      setMessage(`Preference could not be saved: ${errorText(e)}`);
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Personal</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Notifications
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Choose which task events appear in your Hoja inbox. Email and push
          delivery are not available.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Delivery</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Inbox notifications
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          You follow tasks you create, are assigned, comment on or are mentioned
          in; projects you lead, create or post to; initiatives you own or
          create; and cycles you subscribe to. Choose which events reach your
          inbox. Things addressed to you, like assignments and mentions, are
          listed first in each group.
        </p>
        {NOTIFICATION_GROUPS.map(([group, kinds]) => (
          <section
            key={group}
            className="mt-5 first-of-type:mt-0"
            data-setting-notification-group={group}
            aria-label={`${group} notifications`}
          >
            <h3 className="mt-0 mb-2 text-sm font-semibold text-(--settings-ink)">
              {group}
            </h3>
            {kinds.map(([key, label, hint]) => (
              <div
                className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-row md:items-center md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
                data-setting-row=""
                key={key}
              >
                <div>
                  <b className="font-[620]">{label}</b>
                  <p>{hint}</p>
                </div>
                <input
                  className="relative mt-0 mr-0 mb-0 ml-0 h-5.5 w-9.5 flex-none cursor-pointer appearance-none rounded-[999px] border border-line bg-(--settings-subtle) after:absolute after:top-1 after:left-1 after:size-3.5 after:rounded-full after:bg-muted checked:border-foreground checked:bg-foreground checked:after:translate-x-4 checked:after:bg-panel checked:after:transition-transform checked:after:duration-150 checked:after:ease-out motion-reduce:after:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px] [@media_(max-width:_700px)]:w-10.5 [@media_(max-width:_700px)]:h-6"
                  aria-label={label}
                  data-setting-switch=""
                  role="switch"
                  type="checkbox"
                  checked={prefs[key]}
                  disabled={!loaded.current && message === "Loading…"}
                  onChange={(e) => void change(key, e.target.checked)}
                />
              </div>
            ))}
          </section>
        ))}
        {message && (
          <p role={message.includes("could not") ? "alert" : "status"}>
            {message}
          </p>
        )}
      </div>
    </section>
  );
}
type SettingsLabel = {
  id: string;
  name: string;
  color?: string;
  description?: string;
};
function LabelRow({
  label,
  admin,
  pending,
  onSave,
  onDelete,
}: {
  label: SettingsLabel;
  admin: boolean;
  pending?: boolean;
  onSave: (
    id: string,
    input: { name: string; color: string; description: string },
  ) => Promise<void>;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false),
    [name, setName] = useState(label.name),
    [color, setColor] = useState(label.color || "#6b7280"),
    [description, setDescription] = useState(label.description || ""),
    [message, setMessage] = useState("");
  // Close the editor at once (the list shows the edit); reopen it with the error if the server refuses.
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage("");
    setEditing(false);
    try {
      await onSave(label.id, { name: name.trim(), color, description });
    } catch (err) {
      setEditing(true);
      setMessage(errorText(err));
    }
  };
  if (!editing)
    return (
      <div
        className="flex min-h-14.5 w-full items-center gap-2.5 border-b border-line bg-panel pl-4.5 pr-4.5 pt-3.5 pb-3.5 text-left hover:bg-panel hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0"
        data-issue-row=""
        data-label-row=""
        aria-busy={pending || undefined}
      >
        <span
          className="w-2.5 h-2.5 flex-none rounded-[50%] ring-4 ring-subtle"
          style={{ background: label.color || undefined }}
        />
        <span className="grid gap-1 flex-1" data-issue-copy="">
          <b className="font-[550]">{label.name}</b>
          <small className="text-[12px] leading-normal text-muted">
            {pending ? "Saving…" : label.description || "Workspace task label"}
          </small>
        </span>
        {admin && (
          <>
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-xs text-inherit hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              disabled={pending}
              onClick={() => {
                setName(label.name);
                setColor(label.color || "#6b7280");
                setDescription(label.description || "");
                setEditing(true);
              }}
            >
              Edit
            </button>
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-xs text-inherit hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              disabled={pending}
              onClick={() => onDelete(label.id)}
            >
              Delete
            </button>
          </>
        )}
      </div>
    );
  return (
    <form
      className="flex min-h-14.5 w-full items-center gap-2.5 border-b border-line bg-panel pl-4.5 pr-4.5 pt-3.5 pb-3.5 text-left hover:bg-panel grid grid-cols-1 md:grid-cols-2 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0"
      data-issue-row=""
      data-label-row=""
      onSubmit={save}
    >
      <label className="min-w-0 text-xs last-of-type:col-span-full">
        Name
        <input
          className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
          aria-label={`Name for label ${label.name}`}
          required
          maxLength={64}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="min-w-0 text-xs last-of-type:col-span-full">
        Color
        <input
          className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
          aria-label={`Color for label ${label.name}`}
          type="color"
          value={color}
          onChange={(e) => setColor(e.target.value)}
        />
      </label>
      <label className="min-w-0 text-xs last-of-type:col-span-full">
        Description
        <input
          className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
          aria-label={`Description for label ${label.name}`}
          maxLength={240}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <button
        className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-11 rounded-none border border-foreground bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-xs font-[650] text-white shadow-[none] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
        disabled={!name.trim()}
      >
        Save label
      </button>
      <button
        type="button"
        className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-md border border-line bg-panel pl-2.75 pr-2.75 pt-2 pb-2 text-xs text-inherit hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        onClick={() => {
          setEditing(false);
          setMessage("");
        }}
      >
        Cancel
      </button>
      {message && (
        <small className="text-xs leading-normal text-muted" role="alert">
          {message}
        </small>
      )}
    </form>
  );
}
let labelSettingsCache: { key: string; labels: SettingsLabel[] } | null = null;
function IssueLabelsSettings({ viewer }: { viewer: Viewer }) {
  const meta = useSyncMeta();
  const cacheKey = meta?.workspace.id || "";
  const [base, setBase] = useState<SettingsLabel[]>(() =>
    labelSettingsCache && labelSettingsCache.key === cacheKey
      ? labelSettingsCache.labels
      : meta?.issueLabels.nodes || [],
  );
  const [labels, push] = useOptimisticList(base);
  const [name, setName] = useState(""),
    [color, setColor] = useState("#6b7280"),
    [error, setError] = useState("");
  const labelVersion = useEntityVersion(["label"]);
  useEffect(() => {
    let live = true;
    gql("{issueLabelSettings{id name color description}}")
      .then((d) => {
        if (!live) return;
        setBase(d.issueLabelSettings);
        if (cacheKey)
          labelSettingsCache = { key: cacheKey, labels: d.issueLabelSettings };
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [labelVersion, cacheKey]);
  const commit = (next: (b: SettingsLabel[]) => SettingsLabel[]) =>
    setBase((b) => {
      const out = next(b);
      if (cacheKey) labelSettingsCache = { key: cacheKey, labels: out };
      return out;
    });
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const input = { name: name.trim() || name, color };
    const tempId = `pending-label-${Date.now()}`;
    const layer = push({ id: tempId, add: { id: tempId, ...input } });
    setName("");
    try {
      const d = await gql(
        `mutation($input:IssueLabelCreateInput!){issueLabelCreate(input:$input){success issueLabel{id name color description}}}`,
        { input },
      );
      if (!d.issueLabelCreate.success)
        throw Error("Only workspace admins can manage labels.");
      const created: SettingsLabel = d.issueLabelCreate.issueLabel || {
        id: tempId,
        ...input,
      };
      commit((b) => (b.some((l) => l.id === created.id) ? b : [...b, created]));
      layer.drop();
    } catch (err) {
      layer.drop();
      setName(input.name);
      setError(errorText(err));
    }
  };
  const save = async (
    id: string,
    input: { name: string; color: string; description: string },
  ) => {
    const layer = push({ id, patch: input });
    try {
      const d = await gql(
        `mutation($id:String!,$input:IssueLabelUpdateInput!){issueLabelUpdate(id:$id,input:$input){success issueLabel{id name color description}}}`,
        { id, input },
      );
      if (!d.issueLabelUpdate?.success)
        throw Error("Label could not be updated.");
      commit((b) =>
        b.map((l) =>
          l.id === id
            ? { ...l, ...input, ...(d.issueLabelUpdate.issueLabel || {}) }
            : l,
        ),
      );
      layer.drop();
    } catch (err) {
      layer.drop();
      throw err;
    }
  };
  const remove = async (id: string) => {
    if (
      !(await confirmDialog(
        "Delete this task label? Existing tasks may lose this label.",
        { danger: true },
      ))
    )
      return;
    setError("");
    const layer = push({ id, remove: true });
    try {
      const d = await gql(
        `mutation($id:String!){issueLabelDelete(id:$id){success}}`,
        { id },
      );
      if (!d.issueLabelDelete.success)
        throw Error("Label deletion was denied.");
      commit((b) => b.filter((l) => l.id !== id));
      layer.drop();
    } catch (err) {
      layer.drop();
      setError(errorText(err));
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Tasks</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Labels
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Manage workspace task labels.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
        data-label-settings=""
      >
        <span className="hidden">Organization</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Task labels
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          A shared vocabulary for finding and organizing work.
        </p>
        {viewer.admin ? (
          <form
            className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3"
            data-label-create=""
            onSubmit={create}
          >
            <label className="min-w-0 text-[12px]">
              Label name
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label="Task label name"
                required
                maxLength={64}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="min-w-0 text-[12px]">
              Color
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label="Task label color"
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
              />
            </label>
            <button
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
              disabled={!name.trim()}
            >
              Create label
            </button>
          </form>
        ) : (
          <p role="status">
            Only workspace admins can create or delete labels.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {labels.map((l) => (
            <LabelRow
              key={l.id}
              label={l}
              admin={!!viewer.admin}
              pending={l.id.startsWith("pending-label-")}
              onSave={save}
              onDelete={(id) => void remove(id)}
            />
          ))}
          {!labels.length && <p>No task labels yet.</p>}
        </div>
      </div>
    </section>
  );
}

type ProjectStatusRow = {
  id: string;
  name: string;
  category: string;
  color?: string | null;
  position: number;
};
type ProjectTemplateRow = {
  id: string;
  name: string;
  description?: string | null;
  defaultState?: string | null;
  taskSet: string[];
  icon?: string | null;
  color?: string | null;
  lead?: { id: string; name?: string | null } | null;
  teams: { id: string; name: string; key: string }[];
};
const PROJECT_STATUS_QUERY =
  "{projectStatuses{id name category color position}}";
const PROJECT_TEMPLATE_QUERY =
  "{projectTemplates{id name description defaultState taskSet icon color lead{id name} teams{id name key}}}";

/* HOJA-489. Real controls over a real store: the status list is the ordered set the
 * server validates project transitions against, and a template is expanded by
 * projectCreate, so nothing here is a toggle with nothing behind it. */
function ProjectStatusesSettings() {
  const [rows, setRows] = useState<ProjectStatusRow[]>([]);
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const [name, setName] = useState(""),
    [category, setCategory] = useState("started"),
    [color, setColor] = useState("#6b7280");
  const load = useCallback(async () => {
    try {
      const d = await gql(PROJECT_STATUS_QUERY);
      setRows(d.projectStatuses);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const run = async (fn: () => Promise<void>, ok: string) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
      await load();
      setStatus(ok);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const label = name.trim();
    await run(async () => {
      const d = await gql(
        "mutation($input:ProjectStatusInput!){projectStatusCreate(input:$input){success}}",
        { input: { name: label, category, color } },
      );
      if (!d.projectStatusCreate?.success) throw Error("Status was rejected.");
      setName("");
    }, `Created ${label}.`);
  };
  const save = (row: ProjectStatusRow) => async (e: React.FormEvent) => {
    e.preventDefault();
    await run(async () => {
      const d = await gql(
        "mutation($id:String!,$input:ProjectStatusInput!){projectStatusUpdate(id:$id,input:$input){success}}",
        {
          id: row.id,
          input: {
            name: row.name.trim(),
            category: row.category,
            color: row.color || null,
          },
        },
      );
      if (!d.projectStatusUpdate?.success)
        throw Error("Status could not be updated.");
    }, `Saved ${row.name}.`);
  };
  const move = (index: number, delta: number) => async () => {
    const target = index + delta;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    const [row] = next.splice(index, 1);
    next.splice(target, 0, row);
    await run(async () => {
      const d = await gql(
        "mutation($ids:[String!]!){projectStatusReorder(ids:$ids)}",
        { ids: next.map((r) => r.id) },
      );
      if (!d.projectStatusReorder) throw Error("Reorder was rejected.");
    }, "Order saved.");
  };
  const remove = (row: ProjectStatusRow) => async () => {
    if (
      !(await confirmDialog(
        `Delete the project status "${row.name}"? Existing projects keep their state text; new projects stop defaulting to it.`,
        { danger: true },
      ))
    )
      return;
    await run(async () => {
      const d = await gql(
        "mutation($id:String!){projectStatusDelete(id:$id)}",
        { id: row.id },
      );
      if (!d.projectStatusDelete) throw Error("Status could not be deleted.");
    }, "Status deleted.");
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="project-statuses"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Project statuses
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          The ordered set a project's state comes from. The first status is the
          default for a new project, and the server refuses a transition to any
          status that is not in this list.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Projects</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Statuses
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          Reorder with the arrows; the order is enforced on project transitions,
          not just displayed.
        </p>
        <form
          className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3"
          data-label-create=""
          onSubmit={create}
        >
          <label className="min-w-0 text-[12px]">
            Name
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Project status name"
              required
              maxLength={40}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Category
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Project status category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="backlog">Backlog</option>
              <option value="started">In progress</option>
              <option value="completed">Completed</option>
              <option value="canceled">Canceled</option>
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Color
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Project status color"
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
            />
          </label>
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
            disabled={busy || !name.trim()}
          >
            Add status
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {rows.map((row, index) => (
            <form
              className="grid grid-cols-[28px_10px_minmax(90px,1fr)_minmax(110px,auto)_auto_auto_auto_auto] [@media_(max-width:640px)]:grid-cols-[24px_10px_minmax(0,1fr)] items-center gap-2 border-b border-line pl-3 pr-3 pt-2.5 pb-2.5 last:border-b-0 [&>button]:whitespace-nowrap"
              data-project-status-row=""
              key={row.id}
              onSubmit={save(row)}
            >
              <span
                className="text-center font-mono text-[11px] tabular-nums text-(--settings-muted)"
                data-project-status-order=""
                aria-label={`Position ${index + 1}`}
              >
                {index + 1}
              </span>
              <span
                className="w-2.5 h-2.5 rounded-[999px] shadow-[0_0_0_3px_var(--settings-subtle)]"
                data-projects-status-dot=""
                style={{ background: row.color || undefined }}
                aria-hidden="true"
              />
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label={`Status ${index + 1} name`}
                value={row.name}
                onChange={(e) =>
                  setRows((rs) =>
                    rs.map((r) =>
                      r.id === row.id ? { ...r, name: e.target.value } : r,
                    ),
                  )
                }
              />
              <select
                className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label={`Status ${row.name} category`}
                value={row.category}
                onChange={(e) =>
                  setRows((rs) =>
                    rs.map((r) =>
                      r.id === row.id ? { ...r, category: e.target.value } : r,
                    ),
                  )
                }
              >
                <option value="backlog">Backlog</option>
                <option value="started">In progress</option>
                <option value="completed">Completed</option>
                <option value="canceled">Canceled</option>
              </select>
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-7 border-0 p-0 [background:none] max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label={`Status ${row.name} color`}
                type="color"
                value={row.color || "#6b7280"}
                onChange={(e) =>
                  setRows((rs) =>
                    rs.map((r) =>
                      r.id === row.id ? { ...r, color: e.target.value } : r,
                    ),
                  )
                }
              />
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-4 pr-4 pt-0 pb-0 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Move ${row.name} up`}
                disabled={busy || index === 0}
                onClick={move(index, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-4 pr-4 pt-0 pb-0 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Move ${row.name} down`}
                disabled={busy || index === rows.length - 1}
                onClick={move(index, 1)}
              >
                ↓
              </button>
              <button
                type="submit"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
                disabled={busy}
              >
                Save
              </button>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-4 pr-4 pt-0 pb-0 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Delete ${row.name}`}
                disabled={busy || rows.length === 1}
                onClick={remove(row)}
              >
                Delete
              </button>
            </form>
          ))}
          {!rows.length && (
            <p>
              No statuses yet; the server seeds the six defaults on first read.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function ProjectTemplatesSettings({ teams }: { teams: Team[] }) {
  const blank = {
    name: "",
    description: "",
    defaultState: "",
    icon: "",
    color: "#6b7280",
    leadId: "",
    teamIds: [] as string[],
    taskSet: "",
  };
  const [draft, setDraft] = useState(blank),
    [editing, setEditing] = useState<string | null>(null);
  const [rows, setRows] = useState<ProjectTemplateRow[]>([]),
    [statuses, setStatuses] = useState<ProjectStatusRow[]>([]);
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const people = useMemo(
    () => [
      ...new Map(
        teams.flatMap((t) => t.members?.nodes || []).map((m) => [m.id, m]),
      ).values(),
    ],
    [teams],
  );
  const load = useCallback(async () => {
    try {
      const d = await gql(PROJECT_TEMPLATE_QUERY);
      setRows(d.projectTemplates);
    } catch (e) {
      setError(errorText(e));
    }
    try {
      const s = await gql(PROJECT_STATUS_QUERY);
      setStatuses(s.projectStatuses);
    } catch {
      /* the select degrades to empty; save still validates server-side */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const run = async (fn: () => Promise<void>, ok: string) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
      await load();
      setStatus(ok);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const input = {
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      state: draft.defaultState || null,
      icon: draft.icon.trim() || null,
      color: draft.color || null,
      leadId: draft.leadId || null,
      teamIds: draft.teamIds,
      taskSet: draft.taskSet
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean),
    };
    await run(
      async () => {
        const d = editing
          ? await gql(
              "mutation($id:String!,$input:ProjectTemplateInput!){projectTemplateUpdate(id:$id,input:$input){success}}",
              { id: editing, input },
            )
          : await gql(
              "mutation($input:ProjectTemplateInput!){projectTemplateCreate(input:$input){success}}",
              { input },
            );
        const ok = editing
          ? d.projectTemplateUpdate?.success
          : d.projectTemplateCreate?.success;
        if (!ok) throw Error("Template was rejected.");
        setDraft(blank);
        setEditing(null);
      },
      editing ? "Template saved." : "Template created.",
    );
  };
  const archive = (row: ProjectTemplateRow) => async () => {
    if (
      !(await confirmDialog(
        `Archive the template "${row.name}"? Projects already created from it are unchanged.`,
        { danger: true },
      ))
    )
      return;
    await run(async () => {
      const d = await gql(
        "mutation($id:String!){projectTemplateArchive(id:$id)}",
        { id: row.id },
      );
      if (!d.projectTemplateArchive)
        throw Error("Template could not be archived.");
    }, "Template archived.");
  };
  const edit = (row: ProjectTemplateRow) => {
    setEditing(row.id);
    setDraft({
      name: row.name,
      description: row.description || "",
      defaultState: row.defaultState || "",
      icon: row.icon || "",
      color: row.color || "#6b7280",
      leadId: row.lead?.id || "",
      teamIds: row.teams.map((t) => t.id),
      taskSet: row.taskSet.join("\n"),
    });
  };
  const toggleTeam = (id: string) => {
    setDraft((d) => ({
      ...d,
      teamIds: d.teamIds.includes(id)
        ? d.teamIds.filter((t) => t !== id)
        : [...d.teamIds, id],
    }));
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="project-templates"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Project templates
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          A template supplies name, description, lead, teams, default status and
          an optional task set. Creating a project from one expands the same
          fields the API would, and its tasks are created as real tasks.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Projects</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          {editing ? "Edit template" : "New template"}
        </h2>
        {editing && (
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 text-accent bg-transparent border-0 text-[11px] focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
            type="button"
            data-text-action=""
            onClick={() => {
              setEditing(null);
              setDraft(blank);
            }}
          >
            Cancel editing
          </button>
        )}
        <form
          className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3 grid-cols-1 md:grid-cols-2"
          data-label-create=""
          data-project-template-form=""
          onSubmit={submit}
        >
          <label className="min-w-0 text-[12px]">
            Name
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template name"
              required
              maxLength={80}
              value={draft.name}
              onChange={(e) =>
                setDraft((d) => ({ ...d, name: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Description
            <textarea
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template description"
              rows={2}
              value={draft.description}
              onChange={(e) =>
                setDraft((d) => ({ ...d, description: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Default status
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template default status"
              value={draft.defaultState}
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultState: e.target.value }))
              }
            >
              <option value="">Server default (first status)</option>
              {statuses.map((s) => (
                <option value={s.name} key={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Lead
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template lead"
              value={draft.leadId}
              onChange={(e) =>
                setDraft((d) => ({ ...d, leadId: e.target.value }))
              }
            >
              <option value="">No lead</option>
              {people.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name || p.email}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Color
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template color"
              type="color"
              value={draft.color}
              onChange={(e) =>
                setDraft((d) => ({ ...d, color: e.target.value }))
              }
            />
          </label>
          <fieldset
            className="col-span-full mt-0 mr-0 mb-0 ml-0 flex flex-wrap gap-x-4 gap-y-2 rounded-lg border border-line pl-3 pr-3 pt-2.5 pb-2.5"
            data-project-template-teams=""
          >
            <legend className="pl-1 pr-1 text-[11px] text-(--settings-muted)">
              Teams
            </legend>
            {teams.map((t) => (
              <label className="min-w-0 text-[12px]" key={t.id}>
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  checked={draft.teamIds.includes(t.id)}
                  onChange={() => toggleTeam(t.id)}
                />
                {t.name}
              </label>
            ))}
            {!teams.length && <p>No teams yet.</p>}
          </fieldset>
          <label className="min-w-0 text-[12px]">
            Task set, one title per line
            <textarea
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-full max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Template task set"
              rows={3}
              placeholder={"Write the brief\nShip the build"}
              value={draft.taskSet}
              onChange={(e) =>
                setDraft((d) => ({ ...d, taskSet: e.target.value }))
              }
            />
          </label>
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
            disabled={busy || !draft.name.trim()}
          >
            {editing ? "Save template" : "Create template"}
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {rows.map((row) => (
            <div
              className="flex min-h-14.5 w-full items-center gap-3 border-b border-line bg-(--settings-card) pl-3.5 pr-3.5 pt-3 pb-3 text-left hover:bg-panel flex-wrap hover:bg-(--settings-card)"
              data-label-row=""
              data-project-template-row=""
              key={row.id}
            >
              <span
                className="w-3 h-3 rounded-[50%] border-2 border-dotted border-orange-400"
                data-projects-status-dot=""
                style={{ background: row.color || undefined }}
                aria-hidden="true"
              />
              <span
                className="flex min-w-0 flex-auto flex-col gap-0.5"
                data-project-template-copy=""
              >
                <b className="font-semibold">{row.name}</b>
                <small className="text-[11px] leading-normal text-(--settings-muted)">
                  {row.description || "No description"} · {row.teams.length}{" "}
                  team{row.teams.length === 1 ? "" : "s"} · {row.taskSet.length}{" "}
                  task{row.taskSet.length === 1 ? "" : "s"} · default{" "}
                  {row.defaultState || "first status"}
                </small>
              </span>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                onClick={() => edit(row)}
              >
                Edit
              </button>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Archive ${row.name}`}
                disabled={busy}
                onClick={archive(row)}
              >
                Archive
              </button>
            </div>
          ))}
          {!rows.length && <p>No project templates yet.</p>}
        </div>
      </div>
    </section>
  );
}

/* HOJA-491 SLAs. The pane edits policies the server actually enforces: every
 * clock below is started, paused and stopped by the events in sla.py, and the
 * task card reads the same row this pane configures. */
type SlaPolicyRow = {
  id: string;
  name: string;
  team?: { id: string; key: string } | null;
  appliesPriorities: number[];
  firstResponseMinutes?: number | null;
  resolutionMinutes?: number | null;
  pauseStates: { id: string; name: string }[];
  enabled: boolean;
  cycleBound: boolean;
  stopsOn: string;
  businessDaysOnly: boolean;
};
const SLA_POLICY_QUERY =
  "{slaPolicies{id name team{id key} appliesPriorities firstResponseMinutes resolutionMinutes pauseStates{id name} enabled cycleBound stopsOn businessDaysOnly}}";

function SlaSettings({ teams }: { teams: Team[] }) {
  const blank = {
    name: "",
    teamId: "",
    firstResponse: "",
    resolution: "",
    priorities: [] as number[],
    pauseStateIds: [] as string[],
    // HOJA-704. The defaults are today's behaviour, except ``applyToOpen``:
    // a task that was already open when its policy arrived had no clock, and
    // starting one now is the gap this pane closes. Untick it for a policy that
    // should only govern work created after it.
    cycleBound: false,
    stopsOn: "completed",
    businessDaysOnly: false,
    applyToOpen: true,
  };
  const [draft, setDraft] = useState(blank),
    [editing, setEditing] = useState<string | null>(null),
    [rows, setRows] = useState<SlaPolicyRow[]>([]);
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const d = await gql(SLA_POLICY_QUERY);
      setRows(d.slaPolicies);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const run = async (fn: () => Promise<void>, done: string) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
      await load();
      setStatus(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const input = {
      name: draft.name.trim(),
      teamId: draft.teamId || null,
      firstResponseMinutes: draft.firstResponse
        ? Number(draft.firstResponse)
        : null,
      resolutionMinutes: draft.resolution ? Number(draft.resolution) : null,
      appliesPriorities: draft.priorities,
      pauseStateIds: draft.pauseStateIds,
      cycleBound: draft.cycleBound,
      stopsOn: draft.stopsOn,
      businessDaysOnly: draft.businessDaysOnly,
      applyToOpen: draft.applyToOpen,
    };
    await run(
      async () => {
        const d = editing
          ? await gql(
              "mutation($id:String!,$input:SlaPolicyInput!){slaPolicyUpdate(id:$id,input:$input){success}}",
              { id: editing, input },
            )
          : await gql(
              "mutation($input:SlaPolicyInput!){slaPolicyCreate(input:$input){success}}",
              { input },
            );
        const ok = editing
          ? d.slaPolicyUpdate?.success
          : d.slaPolicyCreate?.success;
        if (!ok) throw Error("The policy was rejected.");
        setDraft(blank);
        setEditing(null);
      },
      editing ? "Policy saved." : "Policy created.",
    );
  };
  const edit = (row: SlaPolicyRow) => {
    setEditing(row.id);
    setDraft({
      name: row.name,
      teamId: row.team?.id || "",
      firstResponse: row.firstResponseMinutes
        ? String(row.firstResponseMinutes)
        : "",
      resolution: row.resolutionMinutes ? String(row.resolutionMinutes) : "",
      priorities: row.appliesPriorities,
      pauseStateIds: row.pauseStates.map((s) => s.id),
      cycleBound: row.cycleBound,
      stopsOn: row.stopsOn,
      businessDaysOnly: row.businessDaysOnly,
      // A save starts clocks only when asked; editing a policy is not consent to
      // restart the clock of every task it covers.
      applyToOpen: false,
    });
  };
  const archive = (row: SlaPolicyRow) => async () => {
    if (
      !(await confirmDialog(
        `Archive the SLA policy "${row.name}"? Tasks already running keep their clock; new tasks stop starting one.`,
        { danger: true },
      ))
    )
      return;
    await run(async () => {
      const d = await gql("mutation($id:String!){slaPolicyArchive(id:$id)}", {
        id: row.id,
      });
      if (!d.slaPolicyArchive) throw Error("The policy could not be archived.");
    }, "Policy archived.");
  };
  /* HOJA-704's cycle preset: the five policies a workspace running weekly cycles
   * actually wants, created in one press. Every policy is workspace-wide and
   * pauses in every Backlog state, so a task waiting on somebody is not late.
   * "Cycle-bound, else N days" keeps a target for tasks that sit outside a
   * cycle, which is why the fallback minutes are always sent. */
  const cyclePreset = () => {
    const backlog = [
      ...new Set(
        teams.flatMap((t) =>
          (t.states?.nodes || [])
            .filter((st) => st.type === "backlog")
            .map((st) => st.id),
        ),
      ),
    ];
    const day = 24 * 60;
    const presets = [
      {
        name: "Urgent",
        appliesPriorities: [1],
        firstResponseMinutes: 120,
        resolutionMinutes: day,
        cycleBound: false,
      },
      {
        name: "High",
        appliesPriorities: [2],
        firstResponseMinutes: day,
        resolutionMinutes: 7 * day,
        cycleBound: true,
      },
      {
        name: "Medium",
        appliesPriorities: [3],
        firstResponseMinutes: null,
        resolutionMinutes: 14 * day,
        cycleBound: true,
      },
      {
        name: "Low",
        appliesPriorities: [4],
        firstResponseMinutes: null,
        resolutionMinutes: 28 * day,
        cycleBound: true,
      },
      {
        // The triage policy: an untriaged task must get a priority within two
        // days, and the clock stops the moment one arrives.
        name: "No priority",
        appliesPriorities: [0],
        firstResponseMinutes: null,
        resolutionMinutes: 2 * day,
        cycleBound: false,
        stopsOn: "prioritized",
      },
    ];
    return presets.map((preset) => ({
      teamId: null,
      pauseStateIds: backlog,
      businessDaysOnly: false,
      applyToOpen: true,
      stopsOn: "completed",
      ...preset,
    }));
  };
  const [presetBusy, setPresetBusy] = useState(false);
  const createPreset = async () => {
    setPresetBusy(true);
    setError("");
    setStatus("");
    let made = 0;
    const failures: string[] = [];
    try {
      for (const input of cyclePreset()) {
        try {
          const d = await gql(
            "mutation($input:SlaPolicyInput!){slaPolicyCreate(input:$input){success}}",
            { input },
          );
          if (d.slaPolicyCreate?.success) made += 1;
          else failures.push(input.name);
        } catch (e) {
          failures.push(`${input.name} (${errorText(e)})`);
        }
      }
      await load();
      setStatus(
        failures.length
          ? `Created ${made} of ${cyclePreset().length} policies. ${failures.join("; ")}`
          : `Created ${made} policies from the cycle preset.`,
      );
    } finally {
      setPresetBusy(false);
    }
  };
  const toggle = (list: number[], value: number) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  const pauseStates = teams
    .filter((t) => !draft.teamId || t.id === draft.teamId)
    .flatMap((t) => t.states?.nodes || []);
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="slas"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Issues</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          SLAs
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          A policy gives a task a clock: a first-response and a resolution
          deadline that start when the task is created, pause while it waits in
          the states you name, and stop when it is completed. Breaches are
          recorded once, not on every page view.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Policies</span>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
            {editing ? "Edit policy" : "New policy"}
          </h2>
          <button
            className="min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-1.5 pb-1.5 text-[11px] text-(--settings-ink) hover:bg-(--settings-subtle) hover:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11"
            type="button"
            data-sla-preset=""
            disabled={busy || presetBusy}
            onClick={() => void createPreset()}
          >
            {presetBusy ? "Creating…" : "Cycle preset"}
          </button>
        </div>
        {editing && (
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 text-accent bg-transparent border-0 text-[11px] focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
            type="button"
            data-text-action=""
            onClick={() => {
              setEditing(null);
              setDraft(blank);
            }}
          >
            Cancel editing
          </button>
        )}
        <form
          className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3"
          data-label-create=""
          onSubmit={submit}
        >
          <label className="min-w-0 text-[12px]">
            Name
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="SLA policy name"
              required
              maxLength={80}
              value={draft.name}
              onChange={(e) =>
                setDraft((d) => ({ ...d, name: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Scope
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="SLA policy scope"
              value={draft.teamId}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  teamId: e.target.value,
                  pauseStateIds: [],
                }))
              }
            >
              <option value="">Whole workspace</option>
              {teams.map((t) => (
                <option value={t.id} key={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          {/* HOJA-681: the two targets are one decision and sit on one row, so
              "how fast do you answer" and "how fast do you finish" are read
              together instead of stacking into two rows of the grid. */}
          <div
            className="col-span-full grid min-w-0 grid-cols-2 items-end gap-3.5"
            data-sla-targets=""
          >
            <label className="min-w-0 text-[12px]">
              First response, minutes
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label="First response minutes"
                type="number"
                min="1"
                value={draft.firstResponse}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, firstResponse: e.target.value }))
                }
              />
            </label>
            <label className="min-w-0 text-[12px]">
              {draft.cycleBound
                ? "Resolution, minutes if no cycle"
                : "Resolution, minutes"}
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label="Resolution minutes"
                type="number"
                min="1"
                value={draft.resolution}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, resolution: e.target.value }))
                }
              />
            </label>
          </div>
          <fieldset
            className="col-span-full rounded-lg border border-line p-3"
            data-sla-clock-options=""
          >
            <legend className="px-1 text-xs text-muted">Clock</legend>
            <label className="flex min-w-0 items-center gap-2 text-[12px]">
              <input
                className="size-3.5 accent-foreground"
                type="checkbox"
                checked={draft.cycleBound}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, cycleBound: e.target.checked }))
                }
              />
              Resolution follows the task&#39;s cycle, ending when the cycle
              ends
            </label>
            <label className="flex min-w-0 items-center gap-2 text-[12px]">
              <input
                className="size-3.5 accent-foreground"
                type="checkbox"
                checked={draft.businessDaysOnly}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    businessDaysOnly: e.target.checked,
                  }))
                }
              />
              Count weekdays only, in the team&#39;s timezone
            </label>
            <label className="flex min-w-0 items-center gap-2 text-[12px]">
              <input
                className="size-3.5 accent-foreground"
                type="checkbox"
                checked={draft.applyToOpen}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, applyToOpen: e.target.checked }))
                }
              />
              Start clocks for tasks that are already open
            </label>
            <label className="mt-1.5 flex min-w-0 flex-col gap-1 text-[12px]">
              Stops on
              <select
                className="min-w-0 rounded-[7px] border border-line bg-panel pt-0 pr-2.5 pb-0 pl-2.5 text-[13px] text-foreground hover:border-muted"
                aria-label="What stops the clock"
                value={draft.stopsOn}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, stopsOn: e.target.value }))
                }
              >
                <option value="completed">Completed</option>
                <option value="started">A started state</option>
                <option value="prioritized">A priority arrives</option>
              </select>
            </label>
          </fieldset>
          <fieldset className="col-span-full rounded-lg border border-line p-3">
            <legend className="px-1 text-xs text-muted">Priorities</legend>
            {[1, 2, 3, 4].map((p) => (
              <label className="min-w-0 text-[12px]" key={p}>
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  checked={draft.priorities.includes(p)}
                  onChange={() =>
                    setDraft((d) => ({
                      ...d,
                      priorities: toggle(d.priorities, p),
                    }))
                  }
                />
                {["Urgent", "High", "Medium", "Low"][p - 1]}
              </label>
            ))}
            {!draft.priorities.length && (
              <small className="text-xs leading-normal text-muted">
                Every priority
              </small>
            )}
          </fieldset>
          <fieldset className="col-span-full rounded-lg border border-line p-3">
            <legend className="px-1 text-xs text-muted">Pause while in</legend>
            {pauseStates.map((st) => (
              <label className="min-w-0 text-[12px]" key={st.id}>
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  checked={draft.pauseStateIds.includes(st.id)}
                  onChange={() =>
                    setDraft((d) => ({
                      ...d,
                      pauseStateIds: d.pauseStateIds.includes(st.id)
                        ? d.pauseStateIds.filter((x) => x !== st.id)
                        : [...d.pauseStateIds, st.id],
                    }))
                  }
                />
                {st.name}
              </label>
            ))}
            {!pauseStates.length && (
              <small className="text-xs leading-normal text-muted">
                No states to pick from.
              </small>
            )}
          </fieldset>
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
            disabled={
              busy ||
              !draft.name.trim() ||
              (!draft.firstResponse && !draft.resolution)
            }
          >
            {editing ? "Save policy" : "Create policy"}
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {rows.map((row) => (
            <div
              className="flex min-h-14.5 w-full items-center gap-3 border-b border-line bg-(--settings-card) pl-3.5 pr-3.5 pt-3 pb-3 text-left hover:bg-panel hover:bg-(--settings-card)"
              data-label-row=""
              key={row.id}
              data-sla-policy={row.id}
            >
              <span>
                <b className="font-semibold">{row.name}</b>
                <small className="text-xs leading-normal text-muted">
                  {row.team ? `Team ${row.team.key}` : "Whole workspace"} ·
                  first response{" "}
                  {row.firstResponseMinutes
                    ? `${row.firstResponseMinutes}m`
                    : "—"}{" "}
                  · resolution{" "}
                  {row.resolutionMinutes ? `${row.resolutionMinutes}m` : "—"}
                  {row.cycleBound ? " (cycle-bound)" : ""} ·{" "}
                  {row.businessDaysOnly ? "weekdays" : "every day"} ·{" "}
                  {row.stopsOn === "started"
                    ? "stops when started"
                    : row.stopsOn === "prioritized"
                      ? "stops when prioritized"
                      : "stops when completed"}{" "}
                  · {row.pauseStates.length} pause state
                  {row.pauseStates.length === 1 ? "" : "s"}
                </small>
              </span>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                onClick={() => edit(row)}
              >
                Edit
              </button>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Archive ${row.name}`}
                disabled={busy}
                onClick={archive(row)}
              >
                Archive
              </button>
            </div>
          ))}
          {!rows.length && (
            <div
              className="flex flex-col items-start gap-2 bg-(--settings-card) pl-3.5 pr-3.5 pt-3 pb-3"
              data-sla-empty=""
            >
              <p className="m-0 text-[13px] text-(--settings-ink)">
                No SLA policies yet. A task has no clock until a policy covers
                it, so nothing is measured today.
              </p>
              <p className="m-0 text-xs leading-normal text-(--settings-muted)">
                The cycle preset creates five workspace policies — Urgent, High,
                Medium, Low and a triage clock — with every Backlog state
                pausing the clock.
              </p>
              <button
                className="min-h-8 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-3 pr-3 pt-1.5 pb-1.5 text-[11px] font-[620] text-(--settings-on-accent) hover:bg-foreground/90 hover:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11"
                type="button"
                data-sla-empty-action=""
                disabled={busy || presetBusy}
                onClick={() => void createPreset()}
              >
                Start from the cycle preset
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* HOJA-492 task templates. The pane edits a store the server expands in
 * issueCreate: every field here becomes a real default on a real task, and the
 * "preview" row below is the same expansion the API performs, not a mock. */
type TaskTemplateRow = {
  id: string;
  name: string;
  description?: string | null;
  team?: { id: string; key: string } | null;
  defaultTitle: string;
  defaultDescription?: string | null;
  defaultPriority?: number | null;
  defaultAssignee?: { id: string; name?: string; email?: string } | null;
  defaultProject?: { id: string; name: string } | null;
  defaultLabels: { id: string; name: string }[];
};
const TASK_TEMPLATE_QUERY =
  "query TaskTemplates($teamId:String){taskTemplates(teamId:$teamId){id name description defaultTitle defaultDescription defaultPriority team{id key} defaultAssignee{id name email} defaultProject{id name} defaultLabels{id name}}}";
const TASK_TEMPLATE_FIELDS =
  "id name description defaultTitle defaultDescription defaultPriority team{id key} defaultAssignee{id name email} defaultProject{id name} defaultLabels{id name}";
const TASK_TEMPLATE_SELECTION = "taskTemplate{" + TASK_TEMPLATE_FIELDS + "}";

function TaskTemplatesSettings({ teams }: { teams: Team[] }) {
  const blank = {
    name: "",
    description: "",
    teamId: "",
    defaultTitle: "",
    defaultDescription: "",
    defaultPriority: "0",
    defaultAssigneeId: "",
    defaultProjectId: "",
    defaultLabelIds: [] as string[],
  };
  const [draft, setDraft] = useState(blank),
    [editing, setEditing] = useState<string | null>(null),
    [scopeTeam, setScopeTeam] = useState("");
  const [rows, setRows] = useState<TaskTemplateRow[]>([]),
    [projectOptions, setProjectOptions] = useState<
      { id: string; name: string }[]
    >([]),
    [labelOptions, setLabelOptions] = useState<
      { id: string; name: string; teamId: string | null }[]
    >([]);
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const people = useMemo(
    () => [
      ...new Map(
        teams.flatMap((t) => t.members?.nodes || []).map((m) => [m.id, m]),
      ).values(),
    ],
    [teams],
  );
  const load = useCallback(async () => {
    try {
      const d = await gql(TASK_TEMPLATE_QUERY, { teamId: scopeTeam || null });
      setRows(d.taskTemplates);
    } catch (e) {
      setError(errorText(e));
    }
  }, [scopeTeam]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    Promise.all([
      gql("query TemplateProjects{projects(first:100){nodes{id name}}}"),
      gql(
        "query TemplateLabels{issueLabels(first:100){nodes{id name team{id}}}}",
      ),
    ])
      .then(([p, l]) => {
        setProjectOptions(p.projects.nodes);
        setLabelOptions(
          l.issueLabels.nodes.map(
            (x: { id: string; name: string; team?: { id: string } }) => ({
              id: x.id,
              name: x.name,
              teamId: x.team?.id || null,
            }),
          ),
        );
      })
      .catch(() => {
        /* the selects degrade to empty; the server still validates every default it is sent */
      });
  }, []);
  const run = async (fn: () => Promise<void>, done: string) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
      await load();
      setStatus(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const input = {
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      teamId: draft.teamId || null,
      defaultTitle: draft.defaultTitle,
      defaultDescription: draft.defaultDescription.trim() || null,
      defaultPriority: Number(draft.defaultPriority),
      defaultAssigneeId: draft.defaultAssigneeId || null,
      defaultProjectId: draft.defaultProjectId || null,
      defaultLabelIds: draft.defaultLabelIds,
    };
    await run(
      async () => {
        const d = editing
          ? await gql(
              "mutation($id:String!,$input:TaskTemplateInput!){taskTemplateUpdate(id:$id,input:$input){success " +
                TASK_TEMPLATE_SELECTION +
                "}}",
              { id: editing, input },
            )
          : await gql(
              "mutation($input:TaskTemplateInput!){taskTemplateCreate(input:$input){success " +
                TASK_TEMPLATE_SELECTION +
                "}}",
              { input },
            );
        const ok = editing
          ? d.taskTemplateUpdate?.success
          : d.taskTemplateCreate?.success;
        if (!ok) throw Error("The template was rejected.");
        setDraft(blank);
        setEditing(null);
      },
      editing ? "Template saved." : "Template created.",
    );
  };
  const edit = (row: TaskTemplateRow) => {
    setEditing(row.id);
    setDraft({
      name: row.name,
      description: row.description || "",
      teamId: row.team?.id || "",
      defaultTitle: row.defaultTitle,
      defaultDescription: row.defaultDescription || "",
      defaultPriority: String(row.defaultPriority ?? 0),
      defaultAssigneeId: row.defaultAssignee?.id || "",
      defaultProjectId: row.defaultProject?.id || "",
      defaultLabelIds: row.defaultLabels.map((l) => l.id),
    });
  };
  const archive = (row: TaskTemplateRow) => async () => {
    if (
      !(await confirmDialog(
        `Archive the template "${row.name}"? Tasks already created from it are unchanged; the defaults stop being offered.`,
        { danger: true },
      ))
    )
      return;
    await run(async () => {
      const d = await gql(
        "mutation($id:String!){taskTemplateArchive(id:$id)}",
        { id: row.id },
      );
      if (!d.taskTemplateArchive)
        throw Error("The template could not be archived.");
    }, "Template archived.");
  };
  const toggleLabel = (id: string) => {
    setDraft((d) => ({
      ...d,
      defaultLabelIds: d.defaultLabelIds.includes(id)
        ? d.defaultLabelIds.filter((x) => x !== id)
        : [...d.defaultLabelIds, id],
    }));
  };
  const visibleLabels = labelOptions.filter(
    (l) => !draft.teamId || l.teamId === null || l.teamId === draft.teamId,
  );
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="issue-templates"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Issues</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Task templates
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          A template is a stored set of defaults. Creating a task from one — in
          the web client or through the API — fills only the fields you leave
          blank, and the task that lands is the same one either way.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Templates</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          {editing ? "Edit template" : "New template"}
        </h2>
        {editing && (
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 text-accent bg-transparent border-0 text-[11px] focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5]"
            type="button"
            data-text-action=""
            onClick={() => {
              setEditing(null);
              setDraft(blank);
            }}
          >
            Cancel editing
          </button>
        )}
        <form
          className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3"
          data-label-create=""
          onSubmit={submit}
        >
          <label className="min-w-0 text-[12px]">
            Name
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Task template name"
              required
              maxLength={80}
              value={draft.name}
              onChange={(e) =>
                setDraft((d) => ({ ...d, name: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Scope
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Task template scope"
              value={draft.teamId}
              onChange={(e) =>
                setDraft((d) => ({ ...d, teamId: e.target.value }))
              }
            >
              <option value="">Whole workspace</option>
              {teams.map((t) => (
                <option value={t.id} key={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Description
            <textarea
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Task template description"
              rows={2}
              value={draft.description}
              onChange={(e) =>
                setDraft((d) => ({ ...d, description: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Default title
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Default title"
              required
              value={draft.defaultTitle}
              placeholder="Bug: "
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultTitle: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Default description
            <textarea
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Default description"
              rows={2}
              value={draft.defaultDescription}
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultDescription: e.target.value }))
              }
            />
          </label>
          <label className="min-w-0 text-[12px]">
            Default priority
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Default priority"
              value={draft.defaultPriority}
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultPriority: e.target.value }))
              }
            >
              <option value="0">No priority</option>
              <option value="1">Urgent</option>
              <option value="2">High</option>
              <option value="3">Medium</option>
              <option value="4">Low</option>
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Default assignee
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Default assignee"
              value={draft.defaultAssigneeId}
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultAssigneeId: e.target.value }))
              }
            >
              <option value="">Nobody</option>
              {people.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name || p.email}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0 text-[12px]">
            Default project
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Default project"
              value={draft.defaultProjectId}
              onChange={(e) =>
                setDraft((d) => ({ ...d, defaultProjectId: e.target.value }))
              }
            >
              <option value="">None</option>
              {projectOptions.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="col-span-full rounded-lg border border-line p-3">
            <legend className="px-1 text-xs text-muted">Default labels</legend>
            {visibleLabels.map((l) => (
              <label className="min-w-0 text-[12px]" key={l.id}>
                <input
                  className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  checked={draft.defaultLabelIds.includes(l.id)}
                  onChange={() => toggleLabel(l.id)}
                />
                {l.name}
              </label>
            ))}
            {!visibleLabels.length && <p>No labels yet.</p>}
          </fieldset>
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
            disabled={busy || !draft.name.trim() || !draft.defaultTitle.trim()}
          >
            {editing ? "Save template" : "Create template"}
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        <div>
          <label className="min-w-0 text-xs">
            Show
            <select
              className="min-w-0 rounded-[7px] border border-line bg-panel text-[13px] text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base pt-0 pr-2.5 pb-0 pl-2.5 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Show task templates for"
              value={scopeTeam}
              onChange={(e) => setScopeTeam(e.target.value)}
            >
              <option value="">All templates</option>
              {teams.map((t) => (
                <option value={t.id} key={t.id}>
                  {t.name} and inherited
                </option>
              ))}
            </select>
          </label>
        </div>
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {rows.map((row) => (
            <div
              className="flex min-h-14.5 w-full items-center gap-3 border-b border-line bg-(--settings-card) pl-3.5 pr-3.5 pt-3 pb-3 text-left hover:bg-panel hover:bg-(--settings-card)"
              data-label-row=""
              key={row.id}
              data-task-template={row.id}
            >
              <span>
                <b className="font-semibold">{row.name}</b>
                <small className="text-xs leading-normal text-muted">
                  {row.team ? `Team ${row.team.key}` : "Whole workspace"} ·
                  defaults to “{row.defaultTitle}” · priority{" "}
                  {row.defaultPriority ?? "unset"} · {row.defaultLabels.length}{" "}
                  label{row.defaultLabels.length === 1 ? "" : "s"}
                  {row.defaultAssignee
                    ? ` · ${row.defaultAssignee.name || row.defaultAssignee.email}`
                    : ""}
                </small>
              </span>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                onClick={() => edit(row)}
              >
                Edit
              </button>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                aria-label={`Archive ${row.name}`}
                disabled={busy}
                onClick={archive(row)}
              >
                Archive
              </button>
            </div>
          ))}
          {!rows.length && <p>No task templates in this scope yet.</p>}
        </div>
      </div>
    </section>
  );
}

/* HOJA-490. Both panes control something the server actually enforces: every
 * capability listed here is consulted by the resolver or writer on the call it
 * names, and the security policy is read in the auth path (browser session,
 * websocket revalidation and sign-in). Nothing here is a toggle without a
 * refusal behind it. */
type WorkspacePermissionRow = {
  capability: string;
  label: string;
  description: string;
  memberAllowed: boolean;
};
type WorkspaceSecurityPolicyRow = {
  idleTimeoutMinutes?: number | null;
  allowedSigninMethods: string[];
  ipAllowlist: string[];
  updatedAt?: string | null;
};
const WORKSPACE_PERMISSIONS_QUERY =
  "{workspacePermissions{capability label description memberAllowed}}";
const WORKSPACE_SECURITY_QUERY =
  "{workspaceSecurityPolicy{idleTimeoutMinutes allowedSigninMethods ipAllowlist updatedAt}}";

function PermissionsSettings() {
  const [rows, setRows] = useState<WorkspacePermissionRow[]>([]);
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try {
      const d = await gql(WORKSPACE_PERMISSIONS_QUERY);
      setRows(d.workspacePermissions);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const toggle = async (row: WorkspacePermissionRow) => {
    const next = !row.memberAllowed;
    setBusy(row.capability);
    setError("");
    setStatus("");
    try {
      const d = await gql(
        "mutation($capability:String!,$memberAllowed:Boolean!){workspacePermissionUpdate(capability:$capability,memberAllowed:$memberAllowed){capability memberAllowed}}",
        { capability: row.capability, memberAllowed: next },
      );
      setRows((rs) =>
        rs.map((r) =>
          r.capability === row.capability
            ? { ...r, memberAllowed: d.workspacePermissionUpdate.memberAllowed }
            : r,
        ),
      );
      setStatus(
        `${row.label}: ${next ? "members may now use this" : "admins only again"}.`,
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="permissions"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Permissions
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          What a member may do without being made an admin. Admins always hold
          every capability, and the server refuses a call the role does not hold
          — the control is not a hidden button.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Access</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Member capabilities
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          Every capability starts off, which is how the workspace behaved before
          this screen existed. Turning one on applies it on the very next
          request, and turning it off revokes it immediately.
        </p>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        <div
          className="border border-line rounded-lg shadow-[0_1px_2px_#1b1b1b08] overflow-hidden bg-panel **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
        >
          {rows.map((row) => (
            <div
              className="flex min-h-14.5 w-full items-center gap-3 border-b border-line bg-(--settings-card) pl-3.5 pr-3.5 pt-3 pb-3 text-left hover:bg-panel hover:bg-(--settings-card)"
              data-label-row=""
              key={row.capability}
            >
              <span
                className="flex min-w-0 flex-auto flex-col gap-0.5"
                data-permission-copy=""
              >
                <b className="font-semibold">{row.label}</b>
                <small className="text-[11px] leading-normal text-(--settings-muted)">
                  {row.description}
                </small>
                <small
                  className="font-(family-name:--settings-mono,ui-monospace,monospace) text-[11px] text-(--settings-muted) leading-normal"
                  data-permission-code=""
                >
                  {row.capability}
                </small>
              </span>
              <label
                className="inline-flex items-center gap-2 whitespace-nowrap text-[11px] text-(--settings-ink) min-w-0"
                data-permission-toggle=""
              >
                <input
                  className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm w-3.5 h-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  aria-label={`Members may ${row.label.toLowerCase()}`}
                  checked={row.memberAllowed}
                  disabled={busy === row.capability}
                  onChange={() => void toggle(row)}
                />
                <span>
                  {row.memberAllowed ? "Members allowed" : "Admins only"}
                </span>
              </label>
            </div>
          ))}
          {!rows.length && <p>No capabilities loaded.</p>}
        </div>
      </div>
    </section>
  );
}

function SecuritySettings() {
  const [policy, setPolicy] = useState<WorkspaceSecurityPolicyRow | null>(null);
  const [idle, setIdle] = useState(""),
    [methods, setMethods] = useState<string[]>([]),
    [allowlist, setAllowlist] = useState("");
  const [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const d = await gql(WORKSPACE_SECURITY_QUERY);
      const p = d.workspaceSecurityPolicy;
      setPolicy(p);
      setIdle(p.idleTimeoutMinutes ? String(p.idleTimeoutMinutes) : "");
      setMethods(p.allowedSigninMethods);
      setAllowlist(p.ipAllowlist.join("\n"));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const effectiveMethods =
    methods.length === 0 ? ["password", "google"] : methods;
  const toggleMethod = (method: string) => {
    const next = effectiveMethods.includes(method)
      ? effectiveMethods.filter((m) => m !== method)
      : [...effectiveMethods, method];
    if (!next.length) return;
    setMethods(next.length === 2 ? [] : next);
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setStatus("");
    const minutes = idle.trim() === "" ? null : Number(idle.trim());
    if (
      minutes !== null &&
      (!Number.isInteger(minutes) || minutes < 1 || minutes > 10080)
    ) {
      setError(
        "Idle timeout must be a whole number of minutes between 1 and 10080, or empty for none.",
      );
      setBusy(false);
      return;
    }
    const list = allowlist
      .split("\n")
      .map((v) => v.trim())
      .filter(Boolean);
    try {
      const d = await gql(
        "mutation($input:WorkspaceSecurityPolicyInput!){workspaceSecurityUpdate(input:$input){idleTimeoutMinutes allowedSigninMethods ipAllowlist updatedAt}}",
        {
          input: {
            idleTimeoutMinutes: minutes,
            allowedSigninMethods: methods,
            ipAllowlist: list,
          },
        },
      );
      const p = d.workspaceSecurityUpdate;
      setPolicy(p);
      setIdle(p.idleTimeoutMinutes ? String(p.idleTimeoutMinutes) : "");
      setMethods(p.allowedSigninMethods);
      setAllowlist(p.ipAllowlist.join("\n"));
      setStatus(
        "Security policy saved. It applies to the next request, not the next sign-in only.",
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="security"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Security
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Session and sign-in policy, enforced in the server's auth path. A
          setting that could not be enforced is not shown here.
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <span className="hidden">Sessions</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          Session policy
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          The idle timeout is measured from the last request. A browser session
          past it is refused on the next request, and an open realtime
          connection stops being revalidated.
        </p>
        <form
          className="grid grid-cols-2 items-end gap-3.5 md:grid-cols-3 flex flex-wrap items-start"
          data-label-create=""
          data-security-form=""
          onSubmit={save}
        >
          <label className="min-w-0 text-[12px]">
            Idle timeout, minutes
            <input
              className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="Idle timeout minutes"
              inputMode="numeric"
              placeholder="No idle timeout"
              value={idle}
              onChange={(e) => setIdle(e.target.value)}
            />
          </label>
          <fieldset
            className="col-span-full mt-0 mr-0 mb-0 ml-0 flex flex-wrap gap-x-4 gap-y-2 rounded-lg border border-line pl-3 pr-3 pt-2.5 pb-2.5 min-w-0 flex-1 basis-64"
            data-project-template-teams=""
          >
            <legend className="pl-1 pr-1 text-[11px] text-(--settings-muted)">
              Allowed sign-in methods
            </legend>
            <label className="min-w-0 text-[12px]">
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                type="checkbox"
                aria-label="Allow email and password sign-in"
                checked={effectiveMethods.includes("password")}
                onChange={() => toggleMethod("password")}
              />
              Email and password
            </label>
            <label className="min-w-0 text-[12px]">
              <input
                className="min-w-0 rounded-[7px] border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                type="checkbox"
                aria-label="Allow Google sign-in"
                checked={effectiveMethods.includes("google")}
                onChange={() => toggleMethod("google")}
              />
              Google
            </label>
            <small className="text-[11px] leading-normal text-(--settings-muted)">
              {methods.length === 0
                ? "Both methods are allowed. Untick one to restrict sign-in to the other."
                : `Sign-in is limited to ${methods.join(" and ")}.`}
            </small>
          </fieldset>
          <label className="min-w-0 text-[12px]">
            IP allowlist, one CIDR per line
            <textarea
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              aria-label="IP allowlist"
              rows={3}
              placeholder={"203.0.113.0/24\n198.51.100.7"}
              value={allowlist}
              onChange={(e) => setAllowlist(e.target.value)}
            />
          </label>
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-4 pr-4 pt-0 pb-0 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none col-span-full w-full md:col-span-1 md:w-auto focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:w-full"
            disabled={busy}
          >
            Save security policy
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {status && <p role="status">{status}</p>}
        {policy && (
          <p
            className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
            data-settings-section-intro=""
          >
            Current:{" "}
            {policy.idleTimeoutMinutes
              ? `${policy.idleTimeoutMinutes} minute idle timeout`
              : "no idle timeout"}
            ;{" "}
            {policy.allowedSigninMethods.length
              ? policy.allowedSigninMethods.join(", ") + " sign-in"
              : "any sign-in method"}
            ;{" "}
            {policy.ipAllowlist.length
              ? `${policy.ipAllowlist.length} allowlisted range${policy.ipAllowlist.length === 1 ? "" : "s"}`
              : "no IP restriction"}
            .
          </p>
        )}
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          An admin is exempt from the IP allowlist so a wrong list cannot lock
          the workspace out of this screen, and the workspace owner is exempt
          from the sign-in method restriction. SSO with an external identity
          provider and domain capture are not offered: the server has no
          SAML/OIDC tenant flow to enforce them, so there is no control here for
          them.
        </p>
      </div>
    </section>
  );
}

/* A pane Hoja cannot serve. It states what the backend is missing and offers the real
 * alternative, instead of rendering a control that would silently do nothing. */
function SettingsUnavailable({
  name,
  needs,
  issue,
  onBack,
}: {
  name: string;
  needs: string;
  issue: string;
  onBack: () => void;
}) {
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-pane="unavailable"
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <button
          className="hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:mb-3 shell-phone:bg-transparent shell-phone:text-muted"
          data-settings-mobile-back=""
          onClick={onBack}
        >
          ← Settings
        </button>
        <span className="hidden">Not yet available</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          {name}
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          {needs}
        </p>
      </header>
      <div
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
      >
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
          What this screen needs
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          This entry exists so the settings shell matches the reference, and so
          the gap is visible rather than hidden. There is no control here to
          click, because a control with nothing behind it would be a lie.
        </p>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
          data-settings-section-intro=""
        >
          The work is tracked as <b className="font-semibold">{issue}</b>, a
          sub-issue of HOJA-421. Until it lands, this screen stays listed and
          stays marked.
        </p>
      </div>
    </section>
  );
}
function SettingsNotFound({ onBack }: { onBack: () => void }) {
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <button
          className="hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 shell-phone:mb-3 shell-phone:bg-transparent shell-phone:text-muted"
          data-settings-mobile-back=""
          onClick={onBack}
        >
          ← Settings
        </button>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Page not found
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          This settings page does not exist.
        </p>
      </header>
    </section>
  );
}

function CreateWorkspacePage({
  onCreated,
}: {
  onCreated: (w: { id: string; name: string; urlKey: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [urlKey, setUrlKey] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const d = await gql(
        `mutation($input:WorkspaceCreateInput!){workspaceCreate(input:$input){success organization{id name urlKey}}}`,
        { input: { name, urlKey } },
      );
      if (!d.workspaceCreate?.success || !d.workspaceCreate?.organization)
        throw Error("Workspace creation was rejected");
      await onCreated(d.workspaceCreate.organization);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="max-w-275 mt-0 mr-auto mb-0 ml-auto"
      data-projects-page=""
    >
      <header
        className="flex justify-between items-end mb-6 max-md:items-center max-md:gap-2.5 shell-phone:gap-2.5"
        data-projects-heading=""
      >
        <div>
          <h1 className="text-[30px] tracking-[-.045em] mt-2.25 mr-0 mb-1 ml-0 max-md:text-3xl font-semibold leading-normal shell-phone:text-[25px]">
            Create workspace
          </h1>
          <p className="mt-1.5 mr-0 mb-0 ml-0 text-muted">
            Create another workspace under your account.
          </p>
        </div>
      </header>
      <form
        className="modal absolute pt-6.25 pr-6.25 pb-6.25 pl-6.25 border border-line rounded-[10px] bg-panel shadow-(--shadow) top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(570px,calc(100%-32px))]"
        onSubmit={create}
      >
        <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
          Workspace name
          <input
            className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            aria-label="Workspace name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="grid gap-1.75 mt-3.75 text-[12px] font-semibold">
          Workspace key
          <input
            className="w-full border border-line rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y max-md:text-base placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
            aria-label="Workspace key"
            required
            pattern="[A-Za-z0-9-]+"
            title="Use letters, numbers, and hyphens"
            value={urlKey}
            onChange={(e) => setUrlKey(e.target.value)}
          />
        </label>
        {message && <p role="alert">{message}</p>}
        <button
          type="submit"
          className="inline-flex min-h-11 items-center justify-center gap-1.75 rounded-none bg-accent pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-white font-[650] shadow-[none] transition-colors duration-150 ease-out hover:brightness-110 hover:transition-none active:translate-y-px disabled:cursor-wait disabled:opacity-65 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.65]"
          data-control-primary=""
          disabled={busy || !name.trim() || !urlKey.trim()}
        >
          {busy ? "Creating…" : "Create workspace"}
        </button>
      </form>
    </section>
  );
}

type OrgMember = {
  id: string;
  name?: string;
  email?: string;
  admin?: boolean;
  active?: boolean;
};
const TEAMS_QUERY = `query Teams{teams{nodes{id name key description showInitiatives states{nodes{id name color type}} members{nodes{id name avatarSeed email}} activeCycle{id name number startsAt endsAt progress}}}}`;
/* Teams come from the synced workspace metadata (falls back to the App prop before the first sync).
   After a team mutation the overlay stays until a sync pull (or a GraphQL reload without sync) has the server's version. */
function useSyncedTeams(teams: Team[], onTeamsChanged?: (t: Team[]) => void) {
  const meta = useSyncMeta();
  const base = meta?.teams.nodes || teams;
  const [view, push] = useOptimisticList(base);
  const refresh = () => {
    const sync = getSync();
    if (sync) return sync.pull();
    return gql(TEAMS_QUERY).then((d) => onTeamsChanged?.(d.teams.nodes));
  };
  return { teams: view, push, refresh };
}
let membersCache: { key: string; members: OrgMember[] } | null = null;
function MembersPage({
  teams: teamsProp,
  onTeamsChanged,
  viewer,
  onOpenMember,
}: {
  teams: Team[];
  onTeamsChanged?: (t: Team[]) => void;
  revision?: number;
  viewer: Viewer;
  onOpenMember?: (userId: string) => void;
}) {
  const [inviteTeams, setInviteTeams] = useState<string[]>([]);
  const meta = useSyncMeta();
  const cacheKey = meta ? `${meta.workspace.id}:${meta.viewer.id || ""}` : "";
  const [base, setBase] = useState<OrgMember[]>(() =>
    membersCache && membersCache.key === cacheKey ? membersCache.members : [],
  );
  const [members, pushMember] = useOptimisticList(base);
  const {
    teams,
    push: pushTeam,
    refresh: refreshTeams,
  } = useSyncedTeams(teamsProp, onTeamsChanged);
  const [error, setError] = useState("");
  const memberVersion = useEntityVersion(["member", "user"]);
  useEffect(() => {
    let live = true;
    gql(
      `query Members{organizationMembers{nodes{id name avatarSeed email admin active}}}`,
    )
      .then((d) => {
        if (!live) return;
        setBase(d.organizationMembers.nodes);
        if (cacheKey)
          membersCache = {
            key: cacheKey,
            members: d.organizationMembers.nodes,
          };
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [memberVersion, cacheKey]);
  const [removed, setRemoved] = useState<OrgMember[]>([]);
  useEffect(() => {
    if (!viewer.admin) return;
    let live = true;
    gql("query RemovedMembers{archivedMembers{id name email active}}")
      .then((d) => {
        if (live)
          setRemoved(
            (d.archivedMembers || []).map((m: any) => ({
              ...m,
              active: false,
            })),
          );
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [memberVersion, cacheKey, viewer.admin]);
  const restoreMember = async (id: string) => {
    setError("");
    try {
      const d = await gql(
        `mutation($id:String!){memberRestore(userId:$id){success user{id active}}}`,
        { id },
      );
      if (!d.memberRestore.success) throw Error("Could not restore member.");
      setRemoved((r) => r.filter((m) => m.id !== id));
      void getSync()?.pull();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const removeMember = async (id: string, label: string) => {
    if (
      !(await confirmDialog(
        `Remove ${label} from this workspace? Their history is kept and they can be restored.`,
        { danger: true },
      ))
    )
      return;
    setError("");
    try {
      const d = await gql(
        `mutation($id:String!){memberArchive(userId:$id){success user{id active}}}`,
        { id },
      );
      if (!d.memberArchive.success)
        throw Error(
          "Could not remove member. You cannot remove yourself or the last admin.",
        );
      setBase((b) => b.filter((m) => m.id !== id));
    } catch (e) {
      setError(errorText(e));
    }
  };
  const [pendingBase, setPendingBase] = useState<
    { id: string; userId: string; name?: string; email?: string }[]
  >([]);
  const [pendingInvites, pushInvite] = useOptimisticList(pendingBase);
  useEffect(() => {
    if (!viewer.admin) return;
    let live = true;
    gql(
      "query PendingInvites{pendingMembershipInvitations{userId name email expiresAt}}",
    )
      .then((d) => {
        if (live)
          setPendingBase(
            d.pendingMembershipInvitations.map((p: { userId: string }) => ({
              ...p,
              id: p.userId,
            })),
          );
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [memberVersion, viewer.admin]);
  // Apply a member edit now; commit it into the list when the server accepts, roll back otherwise.
  const editMember = async (
    id: string,
    patch: Partial<OrgMember>,
    run: () => Promise<void>,
  ) => {
    setError("");
    const layer = pushMember({ id, patch });
    try {
      await run();
      setBase((b) => b.map((m) => (m.id === id ? { ...m, ...patch } : m)));
      layer.drop();
    } catch (e) {
      layer.drop();
      setError(errorText(e));
    }
  };
  const setMemberActive = (id: string, active: boolean) =>
    editMember(id, { active }, async () => {
      const d = await gql(
        active
          ? `mutation($id:String!){memberReactivate(userId:$id){success}}`
          : `mutation($id:String!){memberDeactivate(userId:$id){success}}`,
        { id },
      );
      if (!(active ? d.memberReactivate : d.memberDeactivate).success)
        throw Error(
          active
            ? "Could not reactivate member."
            : "Could not deactivate member. You cannot deactivate yourself or the last admin.",
        );
    });
  const revokeInvite = async (id: string) => {
    setError("");
    const layer = pushInvite({ id, remove: true });
    try {
      const d = await gql(
        `mutation($id:String!){membershipInvitationRevoke(userId:$id){success}}`,
        { id },
      );
      if (!d.membershipInvitationRevoke.success)
        throw Error("Invite could not be revoked.");
      setPendingBase((p) => p.filter((x) => x.userId !== id));
      layer.drop();
    } catch (e) {
      layer.drop();
      setError(errorText(e));
    }
  };
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteResult, setInviteResult] = useState<{
    email: string;
    url: string;
    emailed: boolean;
  } | null>(null);
  const [inviteCopied, setInviteCopied] = useState(false);
  const role = (id: string, admin: boolean) =>
    editMember(id, { admin }, async () => {
      const d = await gql(
        `mutation($id:String!,$admin:Boolean!){memberRoleUpdate(userId:$id,admin:$admin){success}}`,
        { id, admin },
      );
      if (!d.memberRoleUpdate.success)
        throw Error(
          admin
            ? "Role update denied"
            : "The last admin cannot be demoted. Promote another member first.",
        );
    });
  const toggleTeam = async (teamId: string, userId: string, add: boolean) => {
    setError("");
    const team = teams.find((t) => t.id === teamId);
    const person = members.find((m) => m.id === userId);
    if (!team) return;
    const current = team.members?.nodes || [];
    const nodes = add
      ? [
          ...current.filter((x) => x.id !== userId),
          { id: userId, name: person?.name || "", email: person?.email },
        ]
      : current.filter((x) => x.id !== userId);
    const layer = pushTeam({ id: teamId, patch: { members: { nodes } } });
    try {
      const d = await gql(
        `mutation($t:String!,$u:String!){${add ? "teamMemberAdd" : "teamMemberRemove"}(teamId:$t,userId:$u){success}}`,
        { t: teamId, u: userId },
      );
      if (!d[add ? "teamMemberAdd" : "teamMemberRemove"].success)
        throw Error(
          "Team change denied; only workspace admins can change team membership.",
        );
      layer.settleAfter(refreshTeams());
    } catch (e) {
      layer.drop();
      setError(errorText(e));
    }
  };
  const [inviting, setInviting] = useState(false);
  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (inviting) return;
    setError("");
    setInviteResult(null);
    setInviting(true);
    try {
      const d = await gql(
        `mutation($input:MemberInviteInput!){memberInvite(input:$input){success user{id name avatarSeed email}}}`,
        {
          input: { name: inviteName, email: inviteEmail, teamIds: inviteTeams },
        },
      );
      if (!d.memberInvite.success)
        throw Error(
          "Invite failed; check admin rights or whether this email already exists.",
        );
      const user = d.memberInvite.user;
      setBase((b) =>
        b.some((m) => m.id === user.id)
          ? b
          : [...b, { ...user, admin: false, active: true }],
      );
      const created = await gql(
        `mutation($id:String!){membershipInvitationCreate(userId:$id){success inviteCode emailQueued}}`,
        { id: user.id },
      );
      if (
        !created.membershipInvitationCreate?.success ||
        !created.membershipInvitationCreate.inviteCode
      )
        throw Error(
          "Member created, but invite code could not be created. Retry from Members.",
        );
      setPendingBase((p) =>
        p.some((x) => x.userId === user.id)
          ? p
          : [
              ...p,
              {
                id: user.id,
                userId: user.id,
                name: user.name,
                email: user.email,
              },
            ],
      );
      setInviteCopied(false);
      setInviteResult({
        email: user.email,
        url: `${location.origin}/#invite=${encodeURIComponent(created.membershipInvitationCreate.inviteCode)}`,
        emailed: created.membershipInvitationCreate.emailQueued === true,
      });
      setInviteName("");
      setInviteEmail("");
      const joined = inviteTeams;
      setInviteTeams([]);
      if (joined.length) void refreshTeams();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setInviting(false);
    }
  };
  return (
    <section
      className="[--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-on-accent:#fff] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-danger:#242424] [--settings-success:#242424] [:root[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [:root[data-theme=dark]_&]:[--settings-on-accent:#171717] [:root[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [:root[data-theme=dark]_&]:[--settings-muted:#aaa] [:root[data-theme=dark]_&]:[--settings-line:#353535] [:root[data-theme=dark]_&]:[--settings-card:#1b1b1b] [:root[data-theme=dark]_&]:[--settings-subtle:#262626] [:root[data-theme=dark]_&]:[--settings-danger:#eee] [:root[data-theme=dark]_&]:[--settings-success:#d8d8d8] [font-optical-sizing:auto] ml-auto mr-auto w-[min(100%,var(--ds-settings-column-width))] max-w-(--ds-settings-column-width) min-w-0 p-[var(--ds-settings-column-padding)_var(--ds-settings-column-padding)_64px] text-(--settings-ink) font-(family-name:--hoja-font-ui) md:pt-4 shell-phone:pt-0 [@media_(max-width:_700px)]:pt-0 [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) "
      data-settings-page=""
      data-settings-wide=""
    >
      <header
        className="mt-0 mr-0 mb-8 ml-0 border-0 p-0 [@media_(max-width:_700px)]:mb-6.75 [@media_(max-width:_700px)]:pb-5"
        data-settings-page-heading=""
      >
        <span className="hidden">Administration</span>
        <h1 className="mt-0 mr-0 mb-2 ml-0 text-(--settings-ink) text-(length:--ds-settings-title-size) font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) tracking-[-.035em] [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Workspace members
        </h1>
        <p className="mt-0 mr-0 mb-0 ml-0 max-w-full text-[13px] leading-[1.6] text-(--settings-muted) [@media_(max-width:_700px)]:text-[13px]">
          Invite people, assign team access, and manage workspace roles.
        </p>
      </header>
      <form
        className="mb-9 w-full min-w-0 max-w-none border-0 rounded-[10px] bg-(--settings-card) pt-7 pr-7 pb-7 pl-7 text-(--settings-ink) shadow-[none] md:mb-9 static grid gap-3.5 border border-line md:p-7 transform-none shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5 [@media_(max-width:_700px)]:pr-5 [@media_(max-width:_700px)]:pb-5 [@media_(max-width:_700px)]:pl-5 [@media_(max-width:_700px)]:rounded-[13px]"
        data-settings-group=""
        data-workspace-settings-form=""
        onSubmit={invite}
      >
        <span className="hidden">Workspace access</span>
        <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
          Invite a member
        </h2>
        <p
          className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted) wrap-break-word"
          data-settings-section-intro=""
        >
          Create a single-use invite and assign team access from the start.
        </p>
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-4 [@media_(max-width:_700px)]:gap-3.5"
          data-settings-form-grid=""
        >
          <label className="min-w-0 text-xs wrap-break-word">
            Name
            <input
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              required
              value={inviteName}
              onChange={(e) => setInviteName(e.target.value)}
            />
          </label>
          <label className="min-w-0 text-xs wrap-break-word">
            Email
            <input
              className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              required
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
          </label>
          <fieldset className="col-span-full rounded-[10px] border border-line pt-3 pr-3.5 pb-3 pl-3.5 grid gap-2 mt-0 mr-0 mb-0 ml-0">
            <legend className="pl-1.5 pr-1.5 text-[12px] text-muted pt-0 pb-0 font-[550]">
              Teams
            </legend>
            <p className="mt-0 mr-0 mb-0 ml-0 text-[12px] text-muted wrap-break-word">
              Assignees must belong to the team. Pick teams now or add them
              later below.
            </p>
            {teams.map((t) => (
              <label
                className="min-w-0 text-[13px] flex items-center gap-2 text-foreground flex-row justify-start w-auto justify-self-start wrap-break-word"
                key={t.id}
              >
                <input
                  className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm size-3.5 accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 w-4 h-4 mt-0 mr-0 mb-0 ml-0 flex-none max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:text-[16px]"
                  type="checkbox"
                  checked={inviteTeams.includes(t.id)}
                  onChange={(e) =>
                    setInviteTeams((c) =>
                      e.target.checked
                        ? [...c, t.id]
                        : c.filter((x) => x !== t.id),
                    )
                  }
                />{" "}
                {t.name} ({t.key})
              </label>
            ))}
          </fieldset>
        </div>
        <div
          className="mt-4.5 flex flex-col items-start gap-3.5 md:flex-row md:items-center md:gap-3.5 [@media_(max-width:_700px)]:gap-2.25"
          data-settings-form-actions=""
        >
          <button
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-9 rounded-lg border border-foreground bg-(--settings-accent-strong) pl-3.5 pr-3.5 pt-2.25 pb-2.25 text-[12px] font-[620] text-(--settings-on-accent) shadow-[0_1px_2px_#24242412] hover:bg-foreground/90 hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 disabled:opacity-[.5] [@media_(max-width:_700px)]:min-h-10"
            disabled={inviting}
          >
            {inviting ? "Sending invitation…" : "Send invitation"}
          </button>
          <span>One-time link, expires in 72 hours.</span>
        </div>
        {inviteResult && (
          <div
            role="status"
            className="mt-4.5 rounded-[11px] border border-line bg-(--settings-accent-soft) pt-4 pr-4 pb-4 pl-4"
            data-invite-result=""
          >
            <div>
              <span aria-hidden="true">
                {inviteResult.emailed ? "✓" : "link"}
              </span>
              <div>
                <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em] wrap-break-word">
                  {inviteResult.emailed
                    ? `Invite emailed to ${inviteResult.email}`
                    : `Invite ready for ${inviteResult.email}`}
                </h2>
                <p className="wrap-break-word">
                  {inviteResult.emailed
                    ? "They can join straight from that email. The link works once and expires in 72 hours."
                    : "Email is not configured on this server, so send this link yourself. It works once and expires in 72 hours."}
                </p>
              </div>
            </div>
            <label className="min-w-0 text-xs wrap-break-word">
              Invite link
              <input
                className="min-w-0 rounded-lg border border-line bg-panel text-base text-foreground hover:border-muted read-only:bg-subtle read-only:text-muted md:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 max-md:text-base max-w-full placeholder:text-muted focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                aria-label="Copy invite link"
                readOnly
                value={inviteResult.url}
              />
            </label>
            <button
              type="button"
              className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
              onClick={() => {
                navigator.clipboard?.writeText(inviteResult.url);
                setInviteCopied(true);
              }}
            >
              {inviteCopied ? "Link copied" : "Copy link"}
            </button>
          </div>
        )}
        {inviteResult?.emailed && (
          <p className="wrap-break-word">
            Not in their inbox? Ask them to check spam, or send the copied link
            directly.
          </p>
        )}
      </form>
      {error && (
        <p
          className="block text-(--settings-danger) mt-3 mr-0 mb-3 ml-0 max-w-72 text-xs "
          role="alert"
        >
          {error}
        </p>
      )}
      {pendingInvites.length > 0 && (
        <div
          className="mt-4.5 overflow-hidden rounded-none border-0 border-t border-line bg-transparent pt-4.5 shadow-[0_1px_2px_#1b1b1b08] **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
          data-issue-card=""
          data-pending-invites=""
          aria-label="Pending invites"
        >
          <h2 className="mt-0 mr-5 mb-3 ml-5 text-[13px] font-medium leading-6 tracking-tight text-foreground">
            Pending invites
          </h2>
          {pendingInvites.map((p) => (
            <div
              className="border-b border-line flex items-center gap-2.5 w-full pt-3.5 pr-4.5 pb-3.5 pl-4.5 bg-panel text-left min-h-14.5 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0"
              data-issue-row=""
              key={p.userId}
            >
              <span className="grid gap-1 flex-1" data-issue-copy="">
                <b className="font-[550]">{p.name || p.email}</b>
                <small className="text-[12px] leading-normal text-muted">
                  {p.email} · Invite not yet accepted
                </small>
              </span>
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle)"
                onClick={() => void revokeInvite(p.userId)}
              >
                Revoke invite
              </button>
            </div>
          ))}
        </div>
      )}
      <div
        className="mt-4.5 overflow-hidden rounded-none border-0 border-t border-line bg-transparent shadow-[0_1px_2px_#1b1b1b08] **:data-status-history:max-w-[min(240px,calc(100vw-16px))] **:data-status-menu:max-w-[min(240px,calc(100vw-16px))]"
        data-issue-card=""
        data-member-roster=""
      >
        <div
          className="flex items-center justify-between gap-4 border-b border-line pl-0 pr-0 pt-5 pb-3.5 [@media_(max-width:_700px)]:pt-4 [@media_(max-width:_700px)]:pr-4 [@media_(max-width:_700px)]:pb-4 [@media_(max-width:_700px)]:pl-4"
          data-member-roster-head=""
        >
          <div>
            <h2 className="mt-0 mr-0 mb-0 ml-0 text-[21px] font-[550] leading-6 tracking-[-.035em] text-foreground">
              People
            </h2>
            <p>
              {members.length} workspace{" "}
              {members.length === 1 ? "member" : "members"}
            </p>
          </div>
          <span
            className="inline-flex w-fit min-h-6.25 items-center whitespace-nowrap rounded-[999px] border border-line bg-(--settings-subtle) pl-2 pr-2 pt-0.5 pb-0.5 text-[11px] font-semibold text-(--settings-muted)"
            data-settings-badge=""
          >
            Roles & teams
          </span>
        </div>
        {members.map((m) => (
          <div
            className="grid [@media_(max-width:700px)]:*:data-member-avatar:row-start-1 [@media_(max-width:700px)]:*:data-issue-copy:col-start-2 [@media_(max-width:700px)]:*:data-issue-copy:row-start-1 [@media_(max-width:1100px)]:*:data-member-status:col-start-4 [@media_(max-width:1100px)]:*:data-member-status:row-start-1 [@media_(max-width:700px)]:*:data-member-status:col-start-3 [@media_(max-width:700px)]:[&>button]:col-start-3 [@media_(max-width:700px)]:[&>button]:row-start-3 [@media_(max-width:700px)]:[&>button]:justify-self-end grid-cols-[auto_minmax(140px,1.25fr)_minmax(115px,.85fr)_auto_auto_auto] [@media_(max-width:1100px)]:grid-cols-[auto_minmax(140px,1fr)_auto_auto] [@media_(max-width:700px)]:grid-cols-[auto_minmax(0,1fr)_auto] min-h-16.5 w-full items-center gap-3 border-b border-line bg-transparent pl-1 pr-1 pt-4.5 pb-4.5 text-left hover:bg-transparent hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2 last:border-b-0 [@media_(max-width:_700px)]:gap-2.5 [@media_(max-width:_700px)]:pt-3.5 [@media_(max-width:_700px)]:pr-3.5 [@media_(max-width:_700px)]:pb-3.5 [@media_(max-width:_700px)]:pl-3.5"
            data-issue-row=""
            data-member-row=""
            key={m.id}
          >
            <span
              className="grid w-7.75 h-7.75 flex-none place-items-center rounded-[50%]"
              data-member-avatar=""
              aria-hidden="true"
            >
              <UserBlobatar user={m} />
            </span>
            <span className="grid gap-1 flex-1" data-issue-copy="">
              {onOpenMember ? (
                <button
                  type="button"
                  onClick={() => onOpenMember(m.id)}
                  className="w-fit bg-transparent p-0 text-left font-[550] text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-accent"
                >
                  {m.name || m.email}
                </button>
              ) : (
                <b className="font-[550]">{m.name || m.email}</b>
              )}
              <small className="text-[12px] leading-normal text-muted">
                {m.email}
              </small>
            </span>
            <span
              className="flex flex-wrap gap-1.25 [@media_(max-width:1100px)]:col-[2/-1] [@media_(max-width:1100px)]:row-start-2 [@media_(max-width:700px)]:col-span-full"
              data-member-teams=""
              role="group"
              aria-label={`Teams for ${m.name || m.email}`}
            >
              {teams.map((t) => {
                const on = !!t.members?.nodes?.some((x) => x.id === m.id);
                return (
                  <button
                    type="button"
                    key={t.id}
                    className="min-h-7.25 rounded-[7px] border border-line bg-(--settings-card) pl-2 pr-2 pt-0.75 pb-0.75 text-[11px] text-(--settings-muted) aria-pressed:bg-[#e9e9e9] aria-pressed:text-[#171717] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 aria-pressed:border-[#cfcfcf]"
                    data-team-chip=""
                    aria-pressed={on}
                    onClick={() => toggleTeam(t.id, m.id, !on)}
                  >
                    {t.key}
                    {on ? " (member)" : ""}
                  </button>
                );
              })}
            </span>
            <span
              className="[@media_(max-width:1100px)]:col-start-3 [@media_(max-width:1100px)]:row-start-1 [@media_(max-width:700px)]:col-start-2 [@media_(max-width:700px)]:row-start-3"
              data-member-role=""
            >
              <SettingsSelect
                label={`Role for ${m.name || m.email}`}
                value={m.admin ? "admin" : "member"}
                options={[
                  {
                    value: "member",
                    label: "Member",
                    description: "Work in assigned teams",
                    meta: "Team access",
                  },
                  {
                    value: "admin",
                    label: "Admin",
                    description: "Manage the workspace, members, and teams",
                    meta: "Full access",
                  },
                ]}
                onChange={(value) => void role(m.id, value === "admin")}
              />
            </span>
            <span
              className="inline-flex items-center gap-1.25 whitespace-nowrap text-[10px] text-(--settings-muted) before:w-1.5 before:h-1.5 before:rounded-[50%] before:content-[''] before:bg-(--settings-muted) data-[active=true]:before:bg-(--settings-success)"
              data-member-status=""
              data-active={m.active ? "true" : undefined}
            >
              {m.active ? "Active" : "Inactive"}
            </span>
            {viewer.admin && m.id !== viewer.id && (
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle)"
                onClick={() => void setMemberActive(m.id, !m.active)}
              >
                {m.active ? "Deactivate" : "Reactivate"}
              </button>
            )}
            {viewer.admin && m.id !== viewer.id && (
              <button
                type="button"
                className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-danger) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle)"
                onClick={() =>
                  void removeMember(m.id, m.name || m.email || "this member")
                }
              >
                Remove
              </button>
            )}
          </div>
        ))}
        {viewer.admin && removed.length > 0 && (
          <div
            className="mb-9 w-full min-w-0 max-w-none border-0 rounded-none bg-transparent pt-0 pr-0 pb-0 pl-0 text-(--settings-ink) shadow-[none] md:mb-9 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
            data-settings-group=""
          >
            <h2 className="mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-(length:--ds-settings-section-size) font-(--ds-settings-section-weight) leading-(--ds-settings-section-line) tracking-[-.035em]">
              Removed members
            </h2>
            <p
              className="mt-1.5 mb-5 max-w-147.5 text-[13px] leading-[1.55] text-(--settings-muted)"
              data-settings-section-intro=""
            >
              Removed people keep their task history and can be restored at any
              time.
            </p>
            {removed.map((m) => (
              <div
                className="flex min-h-(--ds-settings-row-height) min-w-0 flex-col items-start justify-between gap-5 border-t border-line p-(--ds-settings-row-padding) first-of-type:border-t-0 first-of-type:pt-0 last-of-type:pb-0.5 md:flex-row md:items-center md:gap-5 shell-phone:gap-3 [@media_(max-width:_700px)]:gap-3"
                data-setting-row=""
                key={m.id}
              >
                <div>
                  <b className="font-[620]">{m.name || m.email}</b>
                  <p>{m.email}</p>
                </div>
                <div>
                  <span
                    className="inline-flex items-center gap-1.25 whitespace-nowrap text-[10px] text-(--settings-muted) before:w-1.5 before:h-1.5 before:rounded-[50%] before:content-[''] before:bg-(--settings-muted)"
                    data-member-status=""
                  >
                    Removed
                  </span>
                  <button
                    type="button"
                    className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:cursor-not-allowed disabled:opacity-50 min-h-8 rounded-lg border border-line bg-(--settings-card) pl-2.75 pr-2.75 pt-2 pb-2 text-[11px] text-(--settings-ink) hover:bg-subtle hover:transition-none focus-visible:outline-accent shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) disabled:opacity-[.5]"
                    onClick={() => void restoreMember(m.id)}
                  >
                    Restore
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

type LocalProjectView = BrowserView;
function ProjectViewsPage({
  project: given,
  onBack,
  onOpenIssue,
  revision,
}: {
  project: Project;
  onBack: () => void;
  onOpenIssue: (issue: Issue) => void;
  revision: number;
}) {
  // Issues come from the local store; closed issues older than the sync window are paged on demand.
  const project = useProject(given);
  const projectIssues = useProjectIssues(project.id, gql, issueFields);
  const issues = projectIssues.issues,
    loading = !projectIssues.ready,
    issueError = projectIssues.error;
  const [rows, setRows] = useState<LocalProjectView[]>([]),
    [name, setName] = useState("All issues"),
    [query, setQuery] = useState(""),
    [state, setState] = useState(""),
    [active, setActive] = useState<string | null>(null),
    [error, setError] = useState(""),
    [editing, setEditing] = useState(location.pathname.endsWith("/new")),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    listViews("issue", "project")
      .then((rows) => {
        if (!live) return;
        const scoped = rows.filter((v) => v.project?.id === project.id);
        setRows(scoped);
        setError("");
        const id = location.pathname.split("/view/")[1];
        const found = scoped.find((v) => v.id === id);
        if (found) {
          const f = fieldsFromFilters(found.filters);
          setActive(found.id);
          setName(found.name);
          setQuery(f.query);
          setState(f.state);
          setEditing(false);
        } else if (id && id !== "new") {
          setError("Project view not found.");
        }
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      });
    return () => {
      live = false;
    };
  }, [project.id, revision]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return issues.filter(
      (i) =>
        `${i.identifier} ${i.title}`.toLowerCase().includes(q) &&
        (!state || i.state?.name === state),
    );
  }, [issues, query, state]);
  const states = useMemo(
    () =>
      [
        ...new Set(
          issues.map((i) => i.state?.name).filter((x): x is string => !!x),
        ),
      ].sort(),
    [issues],
  );
  const cancel = () => {
    setEditing(false);
    setError("");
    onBack();
  };
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Enter a view name.");
      return;
    }
    if (rows.some((v) => v.name.toLowerCase() === name.trim().toLowerCase())) {
      setError("A project view with this name already exists.");
      return;
    }
    if (busy) return;
    setBusy(true);
    createView({
      name: name.trim(),
      kind: "issue",
      scope: "project",
      projectId: project.id,
      visibility: "private",
      filters: filtersFromFields("issue", query, state),
      display: {},
    })
      .then((created) => {
        setRows((r) => [...r, created]);
        setActive(created.id);
        setEditing(false);
        setError("");
        history.replaceState(
          {},
          "",
          `/workspace/project/${project.id}/view/${created.id}`,
        );
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };
  return (
    <section
      className="max-w-none min-w-0 w-full overflow-x-hidden text-[12px] leading-normal shell-phone:flex shell-phone:flex-col font-['Inter_Variable',Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif] [font-optical-sizing:auto] min-h-[calc(100dvh-16px)] shell-desktop:[&:has(>[data-project-reference-properties])]:grid-cols-[minmax(0,1fr)_minmax(260px,38%)]"
      data-project-local-views=""
      data-project-reference=""
    >
      <header
        className="border-b border-line h-11.25 min-h-11.25 items-center justify-between gap-2.5 pt-0 pr-0 pb-0 pl-0 relative max-w-none border-0 leading-normal block m-0 p-0 w-[calc(62%-87px)] [@media_(max-width:900px)]:w-auto shell-phone:order-3"
        data-project-reference-head=""
      >
        <button
          className="flex-none pt-1.25 pr-1.25 pb-1.25 pl-1.25 rounded-[5px] bg-transparent text-[12px] max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          onClick={onBack}
        >
          ← {project.name}
        </button>
        <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[12px] shell-phone:max-w-36 shell-phone:max-w-37.5">
          Views
        </span>
      </header>
      <nav
        className="h-11 min-w-0 flex items-center gap-1.25 pt-0 pr-3 pb-0 pl-3 overflow-x-auto border-line [&::-webkit-scrollbar]:hidden border-b-0 md:col-span-full scrollbar-none"
        data-project-reference-tabs=""
        aria-label="Project views"
      >
        <button
          className="h-7 flex-none inline-flex items-center gap-1.5 pt-0 pr-2.75 pb-0 pl-2.75 border border-line rounded-[99px] bg-panel text-muted no-underline text-[12px] max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          onClick={onBack}
        >
          Tasks
        </button>
        <span
          className="self-center pt-1.25 pr-3 pb-1.25 pl-3 border border-line rounded-[20px] bg-subtle text-foreground font-semibold"
          aria-current="page"
        >
          Views
        </span>
      </nav>
      <div
        className="max-w-195 mt-5 mr-auto mb-5 ml-auto pt-3 pr-5 pb-3 pl-5 text-[12px]     shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3"
        data-project-local-view-body=""
      >
        <header className="flex items-center justify-between gap-2.5">
          <h1 className="mt-0 mr-0 mb-0 ml-0 text-[18px] font-semibold leading-normal tracking-normal ">
            {editing
              ? "New view"
              : active
                ? rows.find((v) => v.id === active)?.name || "Project views"
                : "Project views"}
          </h1>
          <button
            className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            type="button"
            onClick={() => {
              setName("All issues");
              setQuery("");
              setState("");
              setEditing(true);
              history.pushState(
                {},
                "",
                `/workspace/project/${project.id}/view/new`,
              );
            }}
          >
            New view
          </button>
        </header>
        <p className="text-muted leading-normal">
          Views saved for this project. Private views are yours; share them from
          the workspace Views page.
        </p>
        {editing && (
          <form
            onSubmit={save}
            className="border grid gap-3 mt-4.5 mr-0 mb-4.5 ml-0 pt-4.5 pr-4.5 pb-4.5 pl-4.5 border-line rounded-lg bg-panel"
            data-project-view-form=""
          >
            <label className="grid gap-1.25">
              View name
              <input
                className="w-full pt-2 pr-2 pb-2 pl-2 border border-line rounded-[5px] bg-bg text-foreground shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                value={name}
                maxLength={60}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            <label className="grid gap-1.25">
              Search tasks
              <input
                className="w-full pt-2 pr-2 pb-2 pl-2 border border-line rounded-[5px] bg-bg text-foreground shell-phone:text-base max-w-full font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Title or identifier"
              />
            </label>
            <label className="grid gap-1.25">
              Status
              <select
                className="w-full pt-2 pr-2 pb-2 pl-2 border border-line rounded-[5px] bg-bg text-foreground shell-phone:text-base max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
                value={state}
                onChange={(e) => setState(e.target.value)}
              >
                <option value="">All statuses</option>
                {states.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </label>
            <div className="flex justify-end gap-2">
              <button
                className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                onClick={cancel}
              >
                Cancel
              </button>
              <button
                className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="submit"
                disabled={!name.trim()}
              >
                Create view
              </button>
            </div>
          </form>
        )}
        {error && (
          <p className="text-muted leading-normal" role="alert">
            {error}
          </p>
        )}
        {rows.length ? (
          <div
            className="flex flex-wrap gap-1.5 mt-5 mr-0 mb-5 ml-0"
            data-project-local-view-list=""
          >
            {rows.map((v) => (
              <button
                className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle aria-pressed:bg-subtle max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                key={v.id}
                type="button"
                aria-pressed={active === v.id}
                onClick={() => {
                  const f = fieldsFromFilters(v.filters);
                  setActive(v.id);
                  setName(v.name);
                  setQuery(f.query);
                  setState(f.state);
                  setEditing(false);
                  history.pushState(
                    {},
                    "",
                    `/workspace/project/${project.id}/view/${v.id}`,
                  );
                }}
              >
                {v.name}
              </button>
            ))}
          </div>
        ) : (
          !editing && (
            <p className="text-muted leading-normal" role="status">
              No project views saved yet.
            </p>
          )
        )}
        {issueError && (
          <p className="text-muted leading-normal" role="alert">
            {issueError}
          </p>
        )}
        {loading && (
          <p className="text-muted leading-normal" role="status">
            Loading project tasks…
          </p>
        )}
        {!loading && (
          <section className="grid gap-0.75" data-project-view-results="">
            <h2 className="text-[13px]">
              {visible.length} matching project tasks
            </h2>
            {visible.map((i) => (
              <button
                className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle text-left wrap-break-word max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                key={i.id}
                type="button"
                onClick={() => onOpenIssue(i)}
              >
                {i.identifier} · {i.title || "Untitled task"}
              </button>
            ))}
            {!visible.length && (
              <p className="text-muted leading-normal">
                No matching project tasks.
              </p>
            )}
            {projectIssues.more && (
              <button
                className="pt-1.75 pr-2.5 pb-1.75 pl-2.5 border border-line rounded-md bg-panel text-[12px] hover:bg-subtle text-left wrap-break-word max-w-full font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                disabled={projectIssues.loading}
                onClick={projectIssues.loadMore}
              >
                {projectIssues.loading ? "Loading…" : "Load older closed tasks"}
              </button>
            )}
          </section>
        )}
      </div>
    </section>
  );
}
type BrowserView = ServerView;
function ViewsPage({
  orgId,
  issues,
  projects,
  path,
  onNavigate,
  onSelect,
  favoriteOnly = false,
  basePath = "/workspace/views",
  viewName = "Views",
  title,
  revision = 0,
}: {
  orgId: string;
  issues: Issue[];
  projects: Project[];
  path: string;
  onNavigate: (view: string, path?: string) => void;
  onSelect: (issue: Issue) => void;
  favoriteOnly?: boolean;
  basePath?: string;
  viewName?: string;
  title?: string;
  revision?: number;
}) {
  const [kind, setKind] = useState<"issues" | "projects">(() =>
    path.includes("/projects") ? "projects" : "issues",
  );
  const [saved, setSaved] = useState<BrowserView[]>([]),
    [name, setName] = useState(""),
    [query, setQuery] = useState(""),
    [state, setState] = useState(""),
    [active, setActive] = useState(""),
    [editing, setEditing] = useState(path.endsWith("/new")),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const teamScoped = basePath.startsWith("/workspace/team/");
  useEffect(() => {
    const syncKind = () =>
      setKind(location.pathname.includes("/projects") ? "projects" : "issues");
    window.addEventListener("popstate", syncKind);
    return () => window.removeEventListener("popstate", syncKind);
  }, []);
  const load = React.useCallback(async () => {
    if (!orgId) {
      setSaved([]);
      return;
    }
    try {
      const rows = await listViews(undefined, teamScoped ? "team" : undefined);
      const scoped = teamScoped
        ? rows.filter((v) => v.team?.id === orgId)
        : rows;
      setSaved(scoped);
      setError("");
      const id = location.pathname.split("/").pop();
      const found = scoped.find((v) => v.id === id);
      if (found) {
        const f = fieldsFromFilters(found.filters);
        setActive(found.id);
        setName(found.name);
        setQuery(f.query);
        setState(f.state);
        setEditing(false);
      } else if (
        id &&
        id !== "new" &&
        id !== "issues" &&
        id !== "projects" &&
        location.pathname.includes("/views/")
      )
        setError("View not found.");
    } catch (e) {
      setSaved([]);
      setError((e as Error).message);
    }
  }, [orgId, teamScoped]);
  useEffect(() => {
    void revision;
    void load();
  }, [load, revision]);
  const relevant = saved.filter(
    (v) => (v.kind === "project" ? "projects" : "issues") === kind,
  );
  const shown = favoriteOnly ? relevant.filter((v) => v.favorite) : relevant;
  const items: (Issue | Project)[] = kind === "issues" ? issues : projects;
  const statuses = useMemo(
    () =>
      [
        ...new Set(
          items
            .map((v) =>
              kind === "issues"
                ? (v as Issue).state?.name
                : (v as Project).state,
            )
            .filter((v): v is string => !!v),
        ),
      ].sort(),
    [items, kind],
  );
  const deferredQuery = React.useDeferredValue(query);
  const matches = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase();
    return items.filter((v) => {
      const text =
        kind === "issues"
          ? `${(v as Issue).identifier} ${(v as Issue).title}`
          : (v as Project).name;
      const status =
        kind === "issues" ? (v as Issue).state?.name : (v as Project).state;
      return text.toLowerCase().includes(q) && (!state || status === state);
    });
  }, [items, kind, deferredQuery, state]);
  const [resultLimit, setResultLimit] = useState(LIST_RENDER_STEP);
  const destination = (k: string) => `${basePath}/${k}`;
  const describe = (v: BrowserView) => {
    const f = fieldsFromFilters(v.filters);
    return (
      [f.query, f.state].filter(Boolean).join(" · ") ||
      `All ${v.kind === "project" ? "projects" : "issues"}`
    );
  };
  const start = () => {
    setName(kind === "issues" ? "All issues" : "All projects");
    setQuery("");
    setState("");
    setEditing(true);
    onNavigate(viewName, `${destination(kind)}/new`);
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    if (
      relevant.some((v) => v.name.toLowerCase() === name.trim().toLowerCase())
    ) {
      setError("A view with that name already exists.");
      return;
    }
    setBusy(true);
    try {
      const viewKind =
        kind === "issues" ? ("issue" as const) : ("project" as const);
      const created = await createView({
        name: name.trim(),
        kind: viewKind,
        scope: teamScoped ? "team" : "workspace",
        ...(teamScoped ? { teamId: orgId } : {}),
        visibility: teamScoped ? "team" : "workspace",
        filters: filtersFromFields(viewKind, query, state),
        display: {},
      });
      setSaved((rows) => [...rows, created]);
      setActive(created.id);
      setEditing(false);
      setError("");
      history.replaceState({}, "", `${destination(kind)}/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const select = (v: BrowserView) => {
    const f = fieldsFromFilters(v.filters);
    setActive(v.id);
    setName(v.name);
    setQuery(f.query);
    setState(f.state);
    setEditing(false);
    onNavigate(
      viewName,
      `${destination(v.kind === "project" ? "projects" : "issues")}/${v.id}`,
    );
  };
  const favorite = async (v: BrowserView) => {
    try {
      const next = await toggleViewFavorite(v.id);
      setSaved((rows) =>
        rows.map((x) => (x.id === v.id ? { ...x, favorite: next } : x)),
      );
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <section
      className="text-[12px] font-['Inter_Variable',Inter,ui-sans-serif,system-ui,sans-serif] min-h-[calc(100dvh-16px)] min-w-0"
      data-workspace-views-page=""
      aria-label={favoriteOnly ? "Favorite views" : "Workspace views"}
    >
      <header
        className="border-b border-line h-11.25 flex items-center justify-between pt-0 pr-4.5 pb-0 pl-4.5"
        data-workspace-views-head=""
      >
        <h1 className="text-(length:--hoja-title-size) font-(--hoja-title-weight) mt-0 mr-0 mb-0 ml-0 leading-[normal] tracking-normal ">
          {title || (favoriteOnly ? "Favorite views" : "Views")}
        </h1>
        {!favoriteOnly && (
          <button
            className="flex gap-1.25 items-center pt-1.25 pr-1.25 pb-1.25 pl-1.25 bg-transparent text-[12px] font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            type="button"
            onClick={start}
          >
            <Plus size={15} /> New view
          </button>
        )}
      </header>
      <nav
        className="flex gap-1.25 pt-2 pr-2.25 pb-2 pl-2.25 h-11"
        data-workspace-views-tabs=""
        aria-label="View type"
      >
        <button
          className="pt-0 pr-2.75 pb-0 pl-2.75 border border-line rounded-[20px] bg-transparent text-[12px] aria-[current=page]:bg-subtle aria-[current=page]:font-medium font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          aria-current={kind === "issues" ? "page" : undefined}
          onClick={() => {
            setKind("issues");
            onNavigate(viewName, destination("issues"));
          }}
        >
          Tasks
        </button>
        <button
          className="pt-0 pr-2.75 pb-0 pl-2.75 border border-line rounded-[20px] bg-transparent text-[12px] aria-[current=page]:bg-subtle aria-[current=page]:font-medium font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          type="button"
          aria-current={kind === "projects" ? "page" : undefined}
          onClick={() => {
            setKind("projects");
            onNavigate(viewName, destination("projects"));
          }}
        >
          Projects
        </button>
      </nav>
      {editing && (
        <form
          className="border grid gap-3 mt-5 mr-auto mb-5 ml-auto pt-4.5 pr-4.5 pb-4.5 pl-4.5 border-line rounded-lg w-[min(550px,calc(100%-30px))]"
          data-workspace-view-form=""
          onSubmit={save}
        >
          <h2 className="text-[16px] mt-0 mr-0 mb-0 ml-0">
            Create {kind === "issues" ? "issue" : "project"} view
          </h2>
          <label className="grid gap-1.5">
            View name
            <input
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-panel text-foreground max-md:text-base font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={80}
            />
          </label>
          <label className="grid gap-1.5">
            Filter
            <input
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-panel text-foreground max-md:text-base font-sans placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name or identifier"
            />
          </label>
          <label className="grid gap-1.5">
            Status
            <select
              className="w-full pt-2.25 pr-2.25 pb-2.25 pl-2.25 border border-line rounded-md bg-panel text-foreground max-md:text-base font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[16px]"
              value={state}
              onChange={(e) => setState(e.target.value)}
            >
              <option value="">All statuses</option>
              {statuses.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <div className="flex justify-end gap-2">
            <button
              className="pt-1.75 pr-3 pb-1.75 pl-3 rounded-[20px] bg-accent text-panel text-[12px] first:bg-subtle first:text-foreground font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="button"
              onClick={() => {
                setEditing(false);
                onNavigate(viewName, destination(kind));
              }}
            >
              Cancel
            </button>
            <button
              className="pt-1.75 pr-3 pb-1.75 pl-3 rounded-[20px] bg-accent text-panel text-[12px] first:bg-subtle first:text-foreground font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="submit"
              disabled={!name.trim() || busy}
            >
              {busy ? "Creating…" : "Create view"}
            </button>
          </div>
          <p className="text-[12px] text-muted">
            {matches.length} matching loaded {kind}; saved to{" "}
            {teamScoped ? "this team" : "your workspace"}.
          </p>
        </form>
      )}
      {error && (
        <p
          className="text-[12px] text-muted"
          role="alert"
          data-workspace-views-error=""
        >
          {error}
        </p>
      )}
      {shown.length ? (
        <div
          className="max-w-185 mt-4 mr-auto mb-4 ml-auto pt-3 pr-3 pb-3 pl-3 max-md:pt-3 max-md:pr-3 max-md:pb-3 max-md:pl-3 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3"
          data-workspace-views-list=""
        >
          {shown.map((v) => (
            <div
              className="flex justify-between items-center border-b border-line"
              key={v.id}
            >
              <button
                className="flex gap-2.25 items-center pt-2.75 pr-2.75 pb-2.75 pl-2.75 bg-transparent text-left font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                onClick={() => select(v)}
              >
                <Layers3 size={17} />
                <span className="grid gap-0.75">
                  <b>{v.name}</b>
                  <small className="text-muted">{describe(v)}</small>
                </span>
              </button>
              <button
                className="flex gap-2.25 items-center pt-2.75 pr-2.75 pb-2.75 pl-2.75 bg-transparent text-left font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                aria-label={`${v.favorite ? "Remove favorite" : "Favorite"} ${v.name}`}
                onClick={() => favorite(v)}
              >
                {v.favorite ? "★" : "☆"}
              </button>
            </div>
          ))}
        </div>
      ) : (
        !editing && (
          <div
            className="grid justify-items-start content-center gap-3.5 mt-0 mr-auto mb-0 ml-auto text-muted shell-phone:min-h-[calc(100dvh-170px)] w-[min(365px,calc(100%-32px))] min-h-[calc(100dvh-170px)]"
            data-workspace-views-empty=""
            role="status"
          >
            <Layers3 size={44} strokeWidth={1} />
            <h2 className="mt-0 mr-0 mb-0 ml-0 text-[16px] font-semibold text-foreground">
              {favoriteOnly ? "Favorite views" : "Views"}
            </h2>
            <p className="leading-normal text-[13px] mt-0 mr-0 mb-2 ml-0">
              {favoriteOnly
                ? "Favorite a saved view to keep it here."
                : `Create views using filters to see only the ${kind === "issues" ? "tasks" : kind} you want. Views are saved to ${teamScoped ? "this team" : "your workspace"} and can be shared.`}
            </p>
            {!favoriteOnly && (
              <button
                className="pt-1.75 pr-3 pb-1.75 pl-3 rounded-[20px] bg-accent text-panel text-[12px] font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                onClick={start}
              >
                Create new view
              </button>
            )}
          </div>
        )
      )}
      {active && (
        <div
          className="max-w-185 mt-4 mr-auto mb-4 ml-auto pt-3 pr-3 pb-3 pl-3 grid gap-1.25 max-md:pt-3 max-md:pr-3 max-md:pb-3 max-md:pl-3 shell-phone:pt-3 shell-phone:pr-3 shell-phone:pb-3 shell-phone:pl-3"
          data-workspace-views-results=""
        >
          <h2 className="text-[13px]">
            {matches.length} matching loaded {kind}
          </h2>
          {kind === "issues"
            ? matches.slice(0, resultLimit).map((x) => (
                <button
                  className="flex gap-2.25 items-center pt-2.75 pr-2.75 pb-2.75 pl-2.75 bg-transparent text-left border-b border-line font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  type="button"
                  key={x.id}
                  onClick={() => onSelect(x as Issue)}
                >
                  {(x as Issue).identifier} · {(x as Issue).title}
                </button>
              ))
            : matches
                .slice(0, resultLimit)
                .map((x) => <p key={x.id}>{(x as Project).name}</p>)}
          {matches.length > resultLimit && (
            <button
              className="flex gap-2.25 items-center pt-2.75 pr-2.75 pb-2.75 pl-2.75 bg-transparent text-left border-b border-line font-sans focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              type="button"
              onClick={() => setResultLimit((l) => l + LIST_RENDER_STEP)}
            >
              Show {Math.min(LIST_RENDER_STEP, matches.length - resultLimit)}{" "}
              more of {matches.length - resultLimit} not shown
            </button>
          )}
        </div>
      )}
    </section>
  );
}
