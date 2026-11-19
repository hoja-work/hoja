/* Inbox: a triage surface, modelled on Linear's.
 *
 * - One row per task. The server keeps one notification per event, and the newest event leads.
 * - List on the left, the task on the right. Opening a row marks it read; nothing else is needed.
 * - Read, archive and snooze act on every notification of that task at once (issue-scoped mutations).
 * - Keyboard: j/k or arrows move and open, Enter opens the full page, e archives, u toggles read,
 *   h snoozes, s unsubscribes, Esc closes the task.
 * - Every change is optimistic and rolls back if the server refuses it. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  BellOff,
  Check,
  CircleDot,
  Clock,
  Inbox as InboxIcon,
  ListFilter,
  MoreHorizontal,
  X,
} from "lucide-react";
import { gql, StatusIcon } from "../main";
import type { Issue } from "../main";
import { errorText } from "../localFirst";
import { useEntityVersion, useIssues } from "../sync";
import { Avatar } from "../graph/shared";
import {
  Popover as UIPopover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import {
  NOTIFICATION_FIELDS,
  SNOOZE_OPTIONS,
  adjustInboxUnread,
  describe,
  excerpt,
  fullTime,
  groupNotifications,
  setInboxUnread,
  useInboxUnread,
  shortTime,
  type InboxGroup,
  type InboxNotification,
} from "./model";

export type InboxTab = "all" | "priority" | "snoozed" | "archived";
const TABS: { id: InboxTab; label: string; path: string }[] = [
  { id: "all", label: "All", path: "/workspace/inbox" },
  { id: "priority", label: "Priority", path: "/workspace/inbox/priority" },
  { id: "snoozed", label: "Snoozed", path: "/workspace/inbox/snoozed" },
  { id: "archived", label: "Archived", path: "/workspace/inbox/archived" },
];
export function inboxTabOf(path: string): InboxTab {
  const p = path.toLowerCase();
  if (p.endsWith("/priority")) return "priority";
  if (p.endsWith("/snoozed")) return "snoozed";
  if (p.endsWith("/archived")) return "archived";
  return "all"; // "/workspace/inbox" and the legacy "/workspace/inbox/other"
}

const QUERY = `query Inbox($after:String,$unreadOnly:Boolean,$archived:Boolean,$snoozed:Boolean){inboxNotifications(first:50,after:$after,unreadOnly:$unreadOnly,archived:$archived,snoozed:$snoozed){nodes{${NOTIFICATION_FIELDS}} pageInfo{hasNextPage endCursor}} inboxUnreadCount}`;

type Page = {
  nodes: InboxNotification[];
  cursor: string | null;
  hasMore: boolean;
  unread: number;
};
type Cache = Page & { key: string };
// The last list per tab, so returning to the inbox paints at once and revalidates behind it.
const pageCache = new Map<string, Cache>();

type Toast = { text: string; undo?: () => void };

const isEditable = (t: EventTarget | null) =>
  t instanceof HTMLElement &&
  (t.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) ||
    !!t.closest(
      "[role=dialog],[role=menu],[data-inbox-snooze-menu],[data-inbox-action-menu]",
    ));

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-5 items-center justify-center rounded border border-line bg-panel px-1 text-xs text-muted">
      {children}
    </kbd>
  );
}

export function InboxPage({
  path,
  scope,
  onOpenIssue,
  onOpenSettings,
  renderDetail,
}: {
  path: string;
  /** Workspace and viewer: the cache must never show one person's inbox to another. */
  scope: string;
  onOpenIssue: (issue: Issue) => void;
  onOpenSettings: () => void;
  renderDetail: (args: {
    issue: Issue;
    onClose: () => void;
  }) => React.ReactNode;
}) {
  const [tab, setTab] = useState<InboxTab>(() => inboxTabOf(path));
  const [unreadOnly, setUnreadOnly] = useState(false);
  const cacheKey = `${scope}:${tab}:${unreadOnly ? "unread" : "all"}`;
  const cached = pageCache.get(cacheKey);
  const [items, setItems] = useState<InboxNotification[]>(
    () => cached?.nodes || [],
  );
  const [cursor, setCursor] = useState<string | null>(cached?.cursor ?? null);
  const [hasMore, setHasMore] = useState(cached?.hasMore ?? false);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [snoozeFor, setSnoozeFor] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const listRef = useRef<HTMLUListElement>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const pending = useRef(0);
  const staleWhilePending = useRef(false);

  /* -------- loading -------- */

  const variables = useCallback(
    (after: string | null) => ({
      after,
      unreadOnly,
      archived: tab === "archived",
      snoozed: tab === "snoozed",
    }),
    [tab, unreadOnly],
  );
  const fetchPage = useCallback(
    async (after: string | null): Promise<Page> => {
      const d = await gql(QUERY, variables(after));
      const page = d.inboxNotifications;
      return {
        nodes: page.nodes,
        cursor: page.pageInfo?.endCursor || null,
        hasMore: Boolean(
          page.pageInfo?.hasNextPage && page.pageInfo?.endCursor,
        ),
        unread: Number(d.inboxUnreadCount) || 0,
      };
    },
    [variables],
  );

  /** First page, replacing what is shown but keeping older pages already loaded behind it. */
  const revalidate = useCallback(async () => {
    if (pending.current > 0) {
      staleWhilePending.current = true;
      return;
    }
    const seq = generation.current;
    try {
      const page = await fetchPage(null);
      if (seq !== generation.current || pending.current > 0) return;
      const fresh = new Set(page.nodes.map((n) => n.id));
      const oldest = page.nodes[page.nodes.length - 1]?.createdAt || "";
      const tail = page.hasMore
        ? itemsRef.current.filter(
            (n) => !fresh.has(n.id) && n.createdAt < oldest,
          )
        : [];
      setItems([...page.nodes, ...tail]);
      if (!tail.length) {
        setCursor(page.cursor);
        setHasMore(page.hasMore);
      }
      setInboxUnread(page.unread);
      setError("");
    } catch (e) {
      if (seq === generation.current) setError(errorText(e));
    } finally {
      if (seq === generation.current) setLoading(false);
    }
  }, [fetchPage]);

  const loadMore = async () => {
    if (!cursor || loading) return;
    const seq = generation.current;
    setLoading(true);
    try {
      const page = await fetchPage(cursor);
      if (seq !== generation.current) return;
      setItems((prev) => {
        const seen = new Set(prev.map((n) => n.id));
        return [...prev, ...page.nodes.filter((n) => !seen.has(n.id))];
      });
      setCursor(page.cursor);
      setHasMore(page.hasMore);
      setInboxUnread(page.unread);
    } catch (e) {
      if (seq === generation.current) setError(errorText(e));
    } finally {
      if (seq === generation.current) setLoading(false);
    }
  };

  // A tab or filter change starts over from that view's cache.
  useEffect(() => {
    generation.current++;
    const c = pageCache.get(cacheKey);
    setItems(c?.nodes || []);
    setCursor(c?.cursor ?? null);
    setHasMore(c?.hasMore ?? false);
    setLoading(!c);
    setError("");
    void revalidate();
  }, [cacheKey]); // eslint-disable-line react-hooks/exhaustive-deps -- revalidate is keyed by the same inputs

  useEffect(() => {
    if (!error)
      pageCache.set(cacheKey, {
        key: cacheKey,
        nodes: items,
        cursor,
        hasMore,
        unread: 0,
      });
  }, [items, cursor, hasMore, cacheKey, error]);

  const version = useEntityVersion(["notification"]);
  const firstVersion = useRef(version);
  useEffect(() => {
    if (version !== firstVersion.current) void revalidate();
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps -- only a change event triggers this

  useEffect(() => {
    const onPop = () => setTab(inboxTabOf(location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  /* -------- derived -------- */

  const storeIssues = useIssues();
  const liveIssue = useMemo(() => {
    const byId = new Map<string, Issue>();
    for (const i of storeIssues) byId.set(i.id, i);
    return (issue: Issue | null | undefined) =>
      issue ? { ...issue, ...(byId.get(issue.id) || {}) } : null;
  }, [storeIssues]);

  const groups = useMemo(() => {
    const all = groupNotifications(items);
    return tab === "priority" ? all.filter((g) => g.direct) : all;
  }, [items, tab]);
  const selected = groups.find((g) => g.key === selectedKey) || null;
  const unreadGroups = groups.filter((g) => g.unreadCount > 0).length;
  // The same number as the sidebar badge: unread notifications, not unread rows.
  const unreadTotal = useInboxUnread();

  // Keep the URL addressable: /workspace/inbox?task=HOJ-12 reopens the same row.
  useEffect(() => {
    const url = new URL(location.href);
    const ident = selected?.issue?.identifier;
    if (ident) url.searchParams.set("task", ident);
    else url.searchParams.delete("task");
    if (url.href !== location.href)
      history.replaceState(history.state, "", url);
  }, [selected?.issue?.identifier]);
  const wanted = useRef(new URL(location.href).searchParams.get("task"));
  useEffect(() => {
    if (!wanted.current || !groups.length) return;
    const g = groups.find((x) => x.issue?.identifier === wanted.current);
    wanted.current = null;
    if (g) setSelectedKey(g.key);
  }, [groups]);

  /* -------- mutations -------- */

  const say = (text: string, undo?: () => void) => setToast({ text, undo });
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  /** Apply locally, send, roll back on refusal. Revalidation waits for the write to settle. */
  const mutate = async (
    change: (list: InboxNotification[]) => InboxNotification[],
    request: () => Promise<unknown>,
    unreadDelta: number,
    failure: string,
  ) => {
    const snapshot = itemsRef.current;
    setItems(change(snapshot));
    adjustInboxUnread(unreadDelta);
    pending.current++;
    try {
      const d = (await request()) as Record<string, unknown>;
      if (d && Object.values(d).some((v) => v === false))
        throw Error("The server refused the change.");
    } catch (e) {
      setItems(snapshot);
      adjustInboxUnread(-unreadDelta);
      say(`${failure}: ${errorText(e)}`);
    } finally {
      pending.current--;
      if (pending.current === 0 && staleWhilePending.current) {
        staleWhilePending.current = false;
        void revalidate();
      }
    }
  };

  const issueIdOf = (g: InboxGroup) => g.issue?.id || g.latest.id;
  const inGroup = (g: InboxGroup) => {
    const ids = new Set(g.items.map((n) => n.id));
    return (n: InboxNotification) => ids.has(n.id);
  };

  const setRead = (g: InboxGroup, read: boolean) => {
    if (!g.issue) return;
    if (read ? g.unreadCount === 0 : g.unreadCount > 0) return;
    const readAt = read ? new Date().toISOString() : null;
    const has = inGroup(g);
    // Marking unread brings back only the newest event, the way Linear does.
    const target = read ? has : (n: InboxNotification) => n.id === g.latest.id;
    const delta = read ? -g.unreadCount : 1;
    return mutate(
      (list) => list.map((n) => (target(n) ? { ...n, readAt } : n)),
      () =>
        gql(
          `mutation($id:String!,$read:Boolean){notificationsMarkReadForIssue(issueId:$id,read:$read)}`,
          { id: issueIdOf(g), read },
        ),
      tab === "all" || tab === "priority" ? delta : 0,
      read ? "Couldn’t mark read" : "Couldn’t mark unread",
    );
  };

  /** Pick the row that takes a removed row's place, so triage can keep going without the mouse. */
  const neighbour = (g: InboxGroup) => {
    const i = groups.findIndex((x) => x.key === g.key);
    return groups[i + 1] || groups[i - 1] || null;
  };
  const removeGroup = (g: InboxGroup) => {
    if (selectedKey === g.key) setSelectedKey(neighbour(g)?.key || null);
    const has = inGroup(g);
    return (list: InboxNotification[]) => list.filter((n) => !has(n));
  };
  const liveDelta = (g: InboxGroup) =>
    tab === "all" || tab === "priority" ? -g.unreadCount : 0;

  const unarchive = (g: InboxGroup) =>
    Promise.all(
      g.items.map((n) =>
        gql(`mutation($id:String!){notificationUnarchive(id:$id)}`, {
          id: n.id,
        }),
      ),
    ).then(() => {
      void revalidate();
      return { ok: true };
    });

  const archive = (g: InboxGroup) => {
    if (tab === "archived") {
      return mutate(
        removeGroup(g),
        () => unarchive(g),
        0,
        "Couldn’t restore",
      ).then(() =>
        say(`Moved ${g.issue?.identifier || "notification"} back to the inbox`),
      );
    }
    return mutate(
      removeGroup(g),
      () =>
        g.issue
          ? gql(
              `mutation($id:String!){notificationsArchiveForIssue(issueId:$id)}`,
              {
                id: issueIdOf(g),
              },
            )
          : gql(`mutation($id:String!){notificationArchive(id:$id)}`, {
              id: g.latest.id,
            }),
      liveDelta(g),
      "Couldn’t archive",
    ).then(() =>
      say(
        `Archived ${g.issue?.identifier || "notification"}`,
        () =>
          void unarchive(g).catch((e) =>
            say(`Couldn’t restore: ${errorText(e)}`),
          ),
      ),
    );
  };

  const snooze = (g: InboxGroup, until: Date | null) => {
    setSnoozeFor(null);
    if (!g.issue) return;
    return mutate(
      removeGroup(g),
      () =>
        gql(
          `mutation($id:String!,$until:DateTime){notificationsSnoozeForIssue(issueId:$id,until:$until)}`,
          { id: issueIdOf(g), until: until ? until.toISOString() : null },
        ),
      until ? liveDelta(g) : 0,
      until ? "Couldn’t snooze" : "Couldn’t unsnooze",
    )
      .then(() =>
        say(
          until
            ? `Snoozed ${g.issue?.identifier} until ${until.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`
            : `${g.issue?.identifier} is back in the inbox`,
          until ? () => void snooze(g, null) : undefined,
        ),
      )
      .then(() => {
        if (!until) void revalidate();
      });
  };

  const unsubscribe = async (g: InboxGroup) => {
    if (!g.issue) return;
    try {
      const d = await gql(`mutation($id:String!){issueUnsubscribe(id:$id)}`, {
        id: g.issue.id,
      });
      if (d.issueUnsubscribe === false)
        throw Error("The server refused the change.");
      say(
        `Unsubscribed from ${g.issue.identifier}. Only assignments and mentions will reach you.`,
        () =>
          void gql(`mutation($id:String!){issueSubscribe(id:$id)}`, {
            id: g.issue!.id,
          }).then(() => say(`Subscribed to ${g.issue!.identifier} again`)),
      );
    } catch (e) {
      say(`Couldn’t unsubscribe: ${errorText(e)}`);
    }
  };

  const markAllRead = () =>
    mutate(
      (list) => {
        const readAt = new Date().toISOString();
        return list.map((n) => (n.readAt ? n : { ...n, readAt }));
      },
      () => {
        setInboxUnread(0);
        return gql(`mutation{notificationMarkAllRead}`);
      },
      0,
      "Couldn’t mark all as read",
    ).then(() => revalidate());

  const archiveAllRead = () =>
    mutate(
      (list) => list.filter((n) => !n.readAt),
      () => gql(`mutation{notificationsArchiveAllRead}`),
      0,
      "Couldn’t archive read notifications",
    ).then(() => say("Archived every read notification"));

  /* -------- selection & keyboard -------- */

  const open = (g: InboxGroup) => {
    setSelectedKey(g.key);
    if (g.unreadCount > 0 && tab !== "archived") void setRead(g, true);
  };
  const close = () => setSelectedKey(null);
  const focusRow = (key: string) =>
    requestAnimationFrame(() =>
      listRef.current
        ?.querySelector<HTMLElement>(
          `[data-inbox-key="${CSS.escape(key)}"] [data-inbox-notification-open]`,
        )
        ?.focus({ preventScroll: false }),
    );
  const move = (step: number) => {
    if (!groups.length) return;
    const i = groups.findIndex((g) => g.key === selectedKey);
    const next =
      groups[
        i < 0
          ? step > 0
            ? 0
            : groups.length - 1
          : Math.min(groups.length - 1, Math.max(0, i + step))
      ];
    open(next);
    focusRow(next.key);
  };

  const handlers = useRef({
    move,
    close,
    archive,
    setRead,
    unsubscribe,
    onOpenIssue,
    selected,
  });
  handlers.current = {
    move,
    close,
    archive,
    setRead,
    unsubscribe,
    onOpenIssue,
    selected,
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isEditable(e.target)) return;
      if (detailRef.current?.contains(e.target as Node) && e.key !== "Escape")
        return;
      const h = handlers.current;
      const g = h.selected;
      const key = e.key;
      if (key === "j" || key === "ArrowDown") h.move(1);
      else if (key === "k" || key === "ArrowUp") h.move(-1);
      else if (key === "Escape" && g) h.close();
      else if (!g) return;
      else if (key === "Enter") {
        // Enter on any other button presses that button; on a row or the page it opens the task.
        const t = e.target as HTMLElement;
        if (t.tagName === "BUTTON" && !t.dataset.inboxNotificationOpen) return;
        if (g.issue) h.onOpenIssue(g.issue);
      } else if (key === "e" || key === "Backspace" || key === "Delete")
        void h.archive(g);
      else if (key === "u") void h.setRead(g, g.unreadCount > 0);
      else if (key === "h") setSnoozeFor(g.key);
      else if (key === "s") void h.unsubscribe(g);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* -------- render -------- */

  const changeTab = (next: InboxTab) => {
    const t = TABS.find((x) => x.id === next)!;
    history.pushState({}, "", t.path);
    setTab(next);
    setSelectedKey(null);
  };
  const iconButton =
    "grid size-7 place-items-center rounded-md bg-transparent text-muted hover:bg-subtle hover:text-foreground aria-pressed:bg-subtle aria-pressed:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 pointer-coarse:size-11 shell-phone:size-11";
  const rowAction =
    "grid size-7 place-items-center rounded-md bg-transparent text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:size-10";
  const detailIssue = selected?.issue ? liveIssue(selected.issue) : null;

  const empty = {
    all: unreadOnly
      ? [
          "No unread notifications",
          "You’ve read everything. Turn off the filter to see the rest.",
        ]
      : [
          "Inbox zero",
          "Assignments, mentions, comments and status changes on tasks you follow land here.",
        ],
    priority: [
      "Nothing needs you",
      "Tasks assigned to you, mentions and decisions you’re asked for appear here.",
    ],
    snoozed: [
      "Nothing snoozed",
      "Snooze a notification with H to bring it back later.",
    ],
    archived: [
      "Nothing archived",
      "Archive a notification with E once you’re done with it.",
    ],
  }[tab];

  const list = (
    <div className="flex min-h-0 flex-1 flex-col">
      <header
        className="flex flex-none items-center justify-between gap-2 border-b border-line px-4 py-2"
        data-inbox-head=""
      >
        <h1 className="m-0 flex items-baseline gap-2 text-sm font-semibold">
          Inbox
          {unreadTotal > 0 && tab !== "archived" && (
            <span className="text-xs font-normal text-muted">
              {unreadTotal} unread
            </span>
          )}
        </h1>
        <div className="flex items-center gap-1" data-inbox-head-actions="">
          <button
            className={iconButton}
            type="button"
            aria-label={
              unreadOnly ? "Show all notifications" : "Show unread only"
            }
            title={unreadOnly ? "Showing unread only" : "Show unread only"}
            aria-pressed={unreadOnly}
            onClick={() => setUnreadOnly((v) => !v)}
          >
            <ListFilter size={15} />
          </button>
          <UIPopover open={menuOpen} onOpenChange={setMenuOpen}>
            <PopoverTrigger asChild>
              <button
                className={iconButton}
                type="button"
                aria-label="Inbox actions"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
              >
                <MoreHorizontal size={15} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              sideOffset={6}
              className="z-50 flex w-56 flex-col gap-0.5 rounded-lg border border-line bg-panel p-1 shadow-(--shadow)"
              data-inbox-action-menu=""
            >
              {(
                [
                  [
                    Check,
                    "Mark all as read",
                    () => void markAllRead(),
                    unreadGroups === 0,
                  ],
                  [
                    Archive,
                    "Archive all read",
                    () => void archiveAllRead(),
                    tab === "archived",
                  ],
                  [BellOff, "Notification settings", onOpenSettings, false],
                ] as const
              ).map(([Icon, label, run, disabled]) => (
                <button
                  key={label}
                  type="button"
                  disabled={disabled}
                  className="flex w-full items-center gap-2 rounded-md bg-transparent px-2 py-1.5 text-left text-sm hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
                  onClick={() => {
                    setMenuOpen(false);
                    run();
                  }}
                >
                  <Icon size={14} className="text-muted" />
                  {label}
                </button>
              ))}
            </PopoverContent>
          </UIPopover>
        </div>
      </header>
      <nav
        className="flex flex-none items-center gap-1 overflow-x-auto border-b border-line px-3 py-1.5 scrollbar-none"
        data-inbox-tabs=""
        aria-label="Inbox views"
      >
        {TABS.map((t) => (
          <a
            key={t.id}
            href={t.path}
            aria-current={tab === t.id ? "page" : undefined}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted no-underline hover:bg-subtle hover:text-foreground aria-[current=page]:bg-subtle aria-[current=page]:font-semibold aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:min-h-11"
            onClick={(e) => {
              e.preventDefault();
              changeTab(t.id);
            }}
          >
            {t.label}
          </a>
        ))}
      </nav>
      <div
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain scrollbar-thin"
        data-inbox-feed=""
      >
        {error && (
          <div
            role="alert"
            className="m-3 flex items-center justify-between gap-2 rounded-md border border-line bg-panel p-3 text-xs text-muted"
          >
            <span>
              {items.length
                ? "Couldn’t refresh the inbox"
                : "Couldn’t load the inbox"}
              : {error}
            </span>
            <button
              type="button"
              className="rounded-md border border-line bg-bg px-2 py-1 text-xs text-foreground hover:bg-subtle"
              onClick={() => {
                setError("");
                setLoading(true);
                void revalidate();
              }}
            >
              Retry
            </button>
          </div>
        )}
        {groups.length > 0 && (
          <ul
            ref={listRef}
            className="m-0 flex list-none flex-col p-0"
            data-inbox-list=""
            aria-label="Notifications"
          >
            {groups.map((g) => {
              const n = g.latest;
              const issue = liveIssue(g.issue);
              const unread = g.unreadCount > 0;
              const isSelected = g.key === selectedKey;
              const quote = excerpt(n);
              return (
                <li
                  key={g.key}
                  data-inbox-key={g.key}
                  data-inbox-notification=""
                  data-unread={unread ? "" : undefined}
                  data-selected={isSelected ? "true" : undefined}
                  className="group relative border-b border-line transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none data-selected:bg-subtle"
                >
                  <button
                    type="button"
                    data-inbox-notification-open="true"
                    className="flex w-full min-w-0 items-start gap-2.5 bg-transparent px-3 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
                    onClick={() => open(g)}
                    onDoubleClick={() => issue && onOpenIssue(issue)}
                    aria-current={isSelected ? "true" : undefined}
                    aria-label={`${unread ? "Unread: " : ""}${issue?.identifier || "Task"} ${issue?.title || "unavailable"} — ${n.actor?.name || "Someone"} ${describe(n)}`}
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-2 size-1.5 flex-none rounded-full ${unread ? "bg-accent" : "bg-transparent"}`}
                      data-inbox-notification-dot=""
                    />
                    <Avatar name={n.actor?.name || null} size={24} />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex min-w-0 items-center gap-1.5">
                        {issue?.state && (
                          <StatusIcon
                            state={issue.state}
                            className="size-3.5 flex-none"
                          />
                        )}
                        <span className="flex-none text-xs text-muted">
                          {issue?.identifier || "—"}
                        </span>
                        <span
                          className={`truncate text-sm ${unread ? "font-semibold text-foreground" : "text-foreground"}`}
                        >
                          {issue?.title || "Task unavailable"}
                        </span>
                      </span>
                      <span className="truncate text-xs text-muted">
                        <span className={unread ? "text-foreground" : ""}>
                          {n.actor?.name || "Someone"}
                        </span>{" "}
                        {describe(n)}
                        {quote ? `: ${quote}` : ""}
                      </span>
                    </span>
                    <span className="flex flex-none flex-col items-end gap-1 group-hover:invisible group-focus-within:invisible pointer-coarse:visible">
                      <time
                        className="text-xs text-muted"
                        dateTime={n.createdAt}
                        title={fullTime(n.createdAt)}
                      >
                        {tab === "snoozed" && n.snoozedUntil
                          ? `until ${new Date(n.snoozedUntil).toLocaleString(undefined, { weekday: "short", hour: "numeric" })}`
                          : shortTime(n.createdAt)}
                      </time>
                      {g.items.length > 1 && (
                        <span
                          className="rounded-full bg-subtle px-1.5 text-xs text-muted"
                          title={`${g.items.length} updates`}
                        >
                          {g.items.length}
                        </span>
                      )}
                    </span>
                  </button>
                  <div
                    className="absolute right-2 top-2 hidden items-center gap-0.5 rounded-md border border-line bg-panel p-0.5 shadow-(--shadow) group-hover:flex group-focus-within:flex"
                    data-inbox-notification-actions=""
                  >
                    {tab !== "archived" && (
                      <button
                        type="button"
                        className={rowAction}
                        title={unread ? "Mark read (U)" : "Mark unread (U)"}
                        aria-label={`${unread ? "Mark read" : "Mark unread"}: ${issue?.identifier || "notification"}`}
                        onClick={() => void setRead(g, unread)}
                      >
                        {unread ? <Check size={14} /> : <CircleDot size={14} />}
                      </button>
                    )}
                    {tab !== "archived" && (
                      <UIPopover
                        open={snoozeFor === g.key}
                        onOpenChange={(o) => setSnoozeFor(o ? g.key : null)}
                      >
                        <PopoverTrigger asChild>
                          <button
                            type="button"
                            className={rowAction}
                            title="Snooze (H)"
                            aria-label={`Snooze ${issue?.identifier || "notification"}`}
                            aria-haspopup="menu"
                            aria-expanded={snoozeFor === g.key}
                          >
                            <Clock size={14} />
                          </button>
                        </PopoverTrigger>
                        <PopoverContent
                          align="end"
                          sideOffset={6}
                          className="z-50 flex w-52 flex-col gap-0.5 rounded-lg border border-line bg-panel p-1 shadow-(--shadow)"
                          data-inbox-snooze-menu=""
                        >
                          {tab === "snoozed" && (
                            <button
                              type="button"
                              className="flex w-full items-center rounded-md bg-transparent px-2 py-1.5 text-left text-sm hover:bg-subtle pointer-coarse:min-h-11"
                              onClick={() => void snooze(g, null)}
                            >
                              Unsnooze now
                            </button>
                          )}
                          {SNOOZE_OPTIONS.map((o) => {
                            const until = o.until();
                            return (
                              <button
                                key={o.id}
                                type="button"
                                className="flex w-full items-center justify-between gap-2 rounded-md bg-transparent px-2 py-1.5 text-left text-sm hover:bg-subtle pointer-coarse:min-h-11"
                                onClick={() => void snooze(g, until)}
                              >
                                {o.label}
                                <span className="text-xs text-muted">
                                  {until.toLocaleString(undefined, {
                                    weekday: "short",
                                    hour: "numeric",
                                  })}
                                </span>
                              </button>
                            );
                          })}
                        </PopoverContent>
                      </UIPopover>
                    )}
                    <button
                      type="button"
                      className={rowAction}
                      title={
                        tab === "archived"
                          ? "Move back to inbox"
                          : "Archive (E)"
                      }
                      aria-label={`${tab === "archived" ? "Unarchive" : "Archive"} ${issue?.identifier || "notification"}`}
                      onClick={() => void archive(g)}
                    >
                      {tab === "archived" ? (
                        <ArchiveRestore size={14} />
                      ) : (
                        <Archive size={14} />
                      )}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {loading && !groups.length && (
          <p
            role="status"
            className="p-4 text-center text-xs text-muted"
            data-inbox-message=""
          >
            Loading notifications…
          </p>
        )}
        {!loading && !error && !groups.length && (
          <div
            className="flex flex-col items-center gap-2 px-6 py-16 text-center"
            data-inbox-message=""
          >
            <InboxIcon size={24} className="text-muted" />
            <h2 className="m-0 text-sm font-semibold">{empty[0]}</h2>
            <p className="m-0 max-w-xs text-xs text-muted">{empty[1]}</p>
          </div>
        )}
        {hasMore && (
          <div className="flex justify-center p-4">
            <button
              type="button"
              className="rounded-md border border-line bg-panel px-3 py-1.5 text-xs hover:bg-subtle disabled:opacity-50"
              data-inbox-load-more=""
              disabled={loading}
              onClick={() => void loadMore()}
            >
              {loading ? "Loading…" : "Load older notifications"}
            </button>
          </div>
        )}
      </div>
    </div>
  );

  const detail = detailIssue ? (
    <div
      ref={detailRef}
      className="flex min-h-0 flex-1 flex-col"
      data-inbox-detail=""
    >
      <div className="flex flex-none items-center justify-between gap-2 border-b border-line px-3 py-1.5">
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md bg-transparent px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-foreground md:hidden pointer-coarse:min-h-11"
          onClick={close}
        >
          ← Inbox
        </button>
        <span className="hidden truncate text-xs text-muted md:inline">
          {selected?.latest.actor?.name || "Someone"}{" "}
          {selected ? describe(selected.latest) : ""}
          {selected ? ` · ${fullTime(selected.latest.createdAt)}` : ""}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="rounded-md bg-transparent px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-foreground pointer-coarse:min-h-11"
            onClick={() => detailIssue && onOpenIssue(detailIssue)}
            title="Open the full page (Enter)"
          >
            Open
          </button>
          <button
            type="button"
            className={iconButton}
            aria-label="Unsubscribe from this task (S)"
            title="Unsubscribe (S)"
            onClick={() => selected && void unsubscribe(selected)}
          >
            <BellOff size={14} />
          </button>
          <button
            type="button"
            className={iconButton}
            aria-label="Close (Esc)"
            title="Close (Esc)"
            onClick={close}
          >
            <X size={15} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {renderDetail({ issue: detailIssue, onClose: close })}
      </div>
    </div>
  ) : (
    <div
      className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center"
      data-inbox-detail-empty=""
    >
      <InboxIcon size={28} className="text-muted" />
      <p className="m-0 text-sm text-muted">
        {groups.length
          ? "Select a notification to read it here"
          : "You’re all caught up"}
      </p>
      <dl className="m-0 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs text-muted">
        {(
          [
            ["J / K", "Next / previous"],
            ["E", "Archive"],
            ["U", "Read / unread"],
            ["H", "Snooze"],
            ["S", "Unsubscribe"],
            ["Enter", "Open full page"],
          ] as const
        ).map(([k, label]) => (
          <div key={k} className="contents">
            <dt className="flex justify-end">
              <Kbd>{k}</Kbd>
            </dt>
            <dd className="m-0 text-left">{label}</dd>
          </div>
        ))}
      </dl>
    </div>
  );

  return (
    <section
      data-inbox-page=""
      className="flex h-full min-h-0 min-w-0 flex-1 flex-col"
    >
      <div className="flex min-h-0 flex-1">
        <div
          className={`flex min-h-0 w-full flex-col border-line md:w-2/5 md:max-w-md md:flex-none md:border-r ${detailIssue ? "max-md:hidden" : ""}`}
          data-inbox-ledger=""
        >
          {list}
        </div>
        <div
          className={`min-h-0 min-w-0 flex-1 flex-col ${detailIssue ? "flex" : "hidden md:flex"}`}
        >
          {detail}
        </div>
      </div>
      {toast && (
        <div
          role="status"
          className="pointer-events-auto fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-line bg-panel px-3 py-2 text-sm shadow-(--shadow)"
          data-inbox-toast=""
        >
          <span>{toast.text}</span>
          {toast.undo && (
            <button
              type="button"
              className="rounded-md px-2 py-0.5 text-sm font-semibold hover:bg-subtle"
              onClick={() => {
                const undo = toast.undo;
                setToast(null);
                undo?.();
              }}
            >
              Undo
            </button>
          )}
          <button
            type="button"
            aria-label="Dismiss"
            className="grid size-6 place-items-center rounded-md text-muted hover:bg-subtle"
            onClick={() => setToast(null)}
          >
            <X size={13} />
          </button>
        </div>
      )}
    </section>
  );
}
