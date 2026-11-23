/**
 * The Reviews page: an inbox of pull requests and tasks in review, laid out the way the task
 * detail view is laid out. A list on the left; on the right a breadcrumb header, the title and
 * body in the main column, and a properties sidebar.
 *
 * Shared with the task detail: `Avatar`, `IconBtn`, `GraphStatusIcon`, `PropStatic` and the
 * `Markdown` renderer, so a pull request reads like a task instead of a separate product.
 * The data model lives in ./inbox.ts; this file only lays it out.
 */
import { useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ExternalLink,
  LayoutGrid,
  PanelLeft,
  List as ListIcon,
  Activity as ActivityIcon,
  TriangleAlert,
} from "lucide-react";
import { Avatar, fmtDate } from "../graph/shared";
import { Markdown } from "../markdown";
import { reviewGlyphPaths } from "../reviews-glyphs";
import { reviewGroupsOf } from "../reviews-gate";
import { MobileNavTrigger, useMobileNav } from "../mobileNav";
import { ConnectionMap } from "./ConnectionMap";
import { ReviewActions } from "./ReviewActions";
import { ReviewsActivity, ReviewsBoard } from "./ReviewsBoard";
import {
  REVIEW_SOURCES,
  activityOf,
  boardOf,
  buildInbox,
  connectionNodesOf,
  filterItems,
  groupItems,
  type InboxFilter,
  type InboxItem,
  type InboxSourceId,
  type TaskInReviewInput,
} from "./inbox";
import type { ReviewLink } from "./types";

type Gql = (query: string, variables?: Record<string, unknown>) => Promise<any>;
export type ReviewsView = "list" | "board" | "activity";

const VIEW_LABEL: Record<ReviewsView, string> = {
  list: "List",
  board: "Board",
  activity: "Activity",
};

function readView(): ReviewsView {
  try {
    const v = localStorage.getItem("hoja_reviews_view");
    return v === "board" || v === "activity" ? v : "list";
  } catch {
    return "list";
  }
}

function age(iso?: string | null): string {
  if (!iso) return "";
  const m = Math.max(1, Math.floor((Date.now() - Date.parse(iso)) / 60000));
  if (m < 60) return `${m}m`;
  if (m < 1440) return `${Math.floor(m / 60)}h`;
  return `${Math.floor(m / 1440)}d`;
}

function prNumber(externalId: string): string {
  const m = externalId.match(/(\d+)\s*$/);
  return m ? `#${m[1]}` : externalId;
}

function checksText(pr: ReviewLink): string | null {
  const total = pr.checksTotal ?? 0;
  if (
    !pr.checksState ||
    pr.checksState === "unknown" ||
    pr.checksState === "none" ||
    total === 0
  )
    return null;
  return `${pr.checksPassing ?? 0}/${total}`;
}

const SOURCE_LABEL: Record<InboxSourceId, string> = {
  "pull-request": "Pull request",
  "task-in-review": "Task",
  "github-review-request": "Review requests",
  "sentry-issue": "Sentry",
};

/** One property row in the sidebar: the same label-and-value layout as the task detail. */
function PropStatic({
  label,
  icon,
  value,
}: {
  label: string;
  icon: ReactNode;
  value: ReactNode;
}) {
  return (
    <div className="relative flex min-h-8 items-center gap-2.5 text-[13px]">
      <span className="text-muted">
        <span className="inline-flex items-center gap-2">
          {icon}
          {label}
        </span>
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1.5">{value}</span>
    </div>
  );
}

function StateIcon({ item }: { item: InboxItem }) {
  if (item.bucket === "Needs attention") {
    return (
      <TriangleAlert
        size={14}
        aria-hidden="true"
        className="flex-none text-amber-500"
      />
    );
  }
  const state = item.bucket === "Draft" ? "draft" : "open";
  return (
    <span
      className={`flex size-3.5 flex-none items-center justify-center ${state === "draft" ? "text-muted" : "text-green-500"}`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" focusable="false">
        <g fill="currentColor">
          {reviewGlyphPaths(state).map((d, i) => (
            <path key={i} fillRule="evenodd" clipRule="evenodd" d={d} />
          ))}
        </g>
      </svg>
    </span>
  );
}

function ViewSwitch({
  view,
  onChange,
}: {
  view: ReviewsView;
  onChange: (v: ReviewsView) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Reviews view"
      className="flex items-center gap-0.5"
    >
      {(["list", "board", "activity"] as ReviewsView[]).map((v) => (
        <button
          key={v}
          type="button"
          aria-pressed={view === v}
          aria-label={VIEW_LABEL[v]}
          title={VIEW_LABEL[v]}
          onClick={() => onChange(v)}
          className={`grid size-7 place-items-center rounded-md pointer-coarse:size-11 ${view === v ? "bg-subtle text-foreground" : "text-muted hover:bg-subtle"}`}
        >
          {v === "list" ? (
            <ListIcon size={15} aria-hidden="true" />
          ) : v === "board" ? (
            <LayoutGrid size={15} aria-hidden="true" />
          ) : (
            <ActivityIcon size={15} aria-hidden="true" />
          )}
        </button>
      ))}
    </div>
  );
}

function ListRow({
  item,
  pr,
  selected,
  onSelect,
}: {
  item: InboxItem;
  pr: ReviewLink | null;
  selected: boolean;
  onSelect: () => void;
}) {
  const taskKey = item.links.find((l) => l.kind === "task")?.label ?? item.ref;
  const checks = pr ? checksText(pr) : null;
  return (
    <li
      className={`group relative flex min-h-10 items-center gap-2.5 border-b border-line px-3 text-[13px] hover:bg-subtle max-lg:flex-wrap max-lg:gap-y-1 max-lg:py-2.5 pointer-coarse:min-h-11 ${selected ? "bg-subtle" : ""}`}
      data-reviews-row=""
      data-selected={selected ? "true" : undefined}
    >
      <StateIcon item={item} />
      <span className="flex-none whitespace-nowrap font-semibold text-foreground">
        {taskKey}
      </span>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        title={item.title}
        className="min-w-0 flex-1 truncate bg-transparent border-0 p-0 text-left text-foreground after:absolute after:inset-0 focus-visible:outline-2 focus-visible:outline-accent max-lg:order-last max-lg:basis-full max-lg:line-clamp-2 max-lg:whitespace-normal max-lg:pl-6"
      >
        {item.title}
        {item.bucket === "Needs attention" ? (
          <span className="ml-2 text-xs text-muted">{item.reason}</span>
        ) : null}
      </button>
      {checks ? (
        <span className="flex-none text-xs tabular-nums text-muted max-lg:ml-auto">
          {checks}
        </span>
      ) : null}
      {pr?.author ? (
        <Avatar
          name={pr.author}
          size={18}
          title={pr.author}
          className="max-lg:pointer-events-none pointer-coarse:pointer-events-none"
        />
      ) : null}
      <span
        className={`w-8 flex-none text-right text-xs tabular-nums text-muted ${checks ? "" : "max-lg:ml-auto"}`}
      >
        {age(item.updatedAt)}
      </span>
    </li>
  );
}

function DetailHeader({
  taskKey,
  ref_,
  url,
  onBack,
}: {
  taskKey: string | null;
  ref_: string;
  url?: string | null;
  onBack: () => void;
}) {
  return (
    <header className="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
      <button
        type="button"
        onClick={onBack}
        aria-label="Back to the review list"
        className="grid size-7 flex-none place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground md:hidden pointer-coarse:size-11 max-md:-ml-2 max-md:size-11"
      >
        <ArrowLeft size={16} aria-hidden="true" />
      </button>
      <nav
        aria-label="Breadcrumb"
        className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] text-muted"
      >
        <span>Reviews</span>
        {taskKey ? (
          <>
            <span aria-hidden="true">›</span>
            <span className="min-w-0 truncate font-medium text-foreground">
              {taskKey}
            </span>
          </>
        ) : null}
        {ref_ !== taskKey ? (
          <>
            <span aria-hidden="true">›</span>
            <span className="min-w-0 truncate tabular-nums">{ref_}</span>
          </>
        ) : null}
      </nav>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          aria-label="Open on GitHub"
          title="Open on GitHub"
          className="grid size-7 flex-none place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground pointer-coarse:size-11"
        >
          <ExternalLink size={15} aria-hidden="true" />
        </a>
      ) : null}
    </header>
  );
}

function FilesPane({ pr }: { pr: ReviewLink }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const files = pr.files ?? [];
  if (files.length === 0) {
    return (
      <p className="m-0 text-sm text-muted">
        No file changes recorded for this pull request.
      </p>
    );
  }
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {files.map((f) => (
        <li key={f.path} className="rounded-md border border-line">
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-subtle pointer-coarse:min-h-11"
            aria-expanded={!!open[f.path]}
            onClick={() => setOpen((o) => ({ ...o, [f.path]: !o[f.path] }))}
          >
            <span className="min-w-0 flex-1 truncate font-mono text-xs max-md:whitespace-normal max-md:break-all">
              {f.path}
            </span>
            <span className="flex-none text-xs tabular-nums text-green-600">
              +{f.additions}
            </span>
            <span className="flex-none text-xs tabular-nums text-red-500">
              −{f.deletions}
            </span>
          </button>
          {open[f.path] && f.patch ? (
            <pre className="m-0 max-h-96 overflow-auto border-t border-line bg-subtle p-3 font-mono text-xs leading-5">
              {f.patch.split("\n").map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith("+") && !line.startsWith("+++")
                      ? "text-green-700"
                      : line.startsWith("-") && !line.startsWith("---")
                        ? "text-red-600"
                        : line.startsWith("@@")
                          ? "text-muted"
                          : ""
                  }
                >
                  {line || " "}
                </div>
              ))}
            </pre>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Detail({
  item,
  pr,
  gql,
  onBack,
  onRefresh,
}: {
  item: InboxItem;
  pr: ReviewLink | null;
  gql: Gql;
  onBack: () => void;
  onRefresh?: () => void;
}) {
  const [tab, setTab] = useState<"overview" | "files">("overview");
  const task = item.links.find((l) => l.kind === "task");
  const reviewers = (Array.isArray(pr?.reviewers) ? pr!.reviewers : []) as {
    login?: string;
  }[];
  const headline = pr?.title || item.title;
  const ref_ = pr ? prNumber(pr.externalId) : item.ref;
  return (
    <article
      className="flex min-h-0 min-w-0 flex-1 flex-col"
      data-reviews-detail=""
    >
      <DetailHeader
        taskKey={task?.label ?? null}
        ref_={ref_}
        url={item.url}
        onBack={onBack}
      />
      <div className="flex min-h-0 flex-1 max-lg:flex-col max-lg:overflow-y-auto max-lg:overscroll-contain">
        <main className="min-w-0 flex-1 overflow-y-auto px-10 pb-20 pt-6 max-lg:flex-none max-lg:overflow-visible max-lg:pb-6 max-md:px-5">
          <h1 className="m-0 text-xl font-semibold leading-snug tracking-tight text-foreground">
            {headline}
          </h1>
          <p className="mb-5 mt-1.5 text-[13px] text-muted">
            {pr?.author ? `${pr.author} opened this` : "Linked task"}
            {pr?.createdAt ? ` · ${age(pr.createdAt)} ago` : ""}
            {item.bucket ? ` · ${item.reason}` : ""}
          </p>

          <div className="mb-6">
            <ConnectionMap nodes={connectionNodesOf(item, pr)} />
          </div>

          {pr ? (
            <ReviewActions
              linkId={pr.id}
              gql={gql}
              onChanged={() => onRefresh?.()}
            />
          ) : null}

          <div
            role="tablist"
            aria-label="Review sections"
            className="mb-4 mt-6 flex gap-1 border-b border-line"
          >
            {(["overview", "files"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                disabled={t === "files" && !pr}
                className={`-mb-px border-b-2 px-3 pb-2 text-[13px] capitalize pointer-coarse:min-h-11 pointer-coarse:pb-0 ${tab === t ? "border-accent text-foreground" : "border-transparent text-muted hover:text-foreground"} disabled:opacity-50`}
              >
                {t === "files"
                  ? `Files${pr?.files?.length ? ` (${pr.files.length})` : ""}`
                  : "Description"}
              </button>
            ))}
          </div>

          {tab === "files" && pr ? (
            <FilesPane pr={pr} />
          ) : pr?.body ? (
            <Markdown text={pr.body} />
          ) : (
            <p className="m-0 text-sm text-muted">
              {pr
                ? "No description."
                : "This task has no pull request to review yet."}
            </p>
          )}
        </main>

        <aside
          aria-label="Properties"
          className="flex w-72 flex-none flex-col overflow-y-auto border-l border-line px-5 pb-10 pt-5 max-lg:mx-10 max-lg:w-auto max-lg:overflow-visible max-lg:border-l-0 max-lg:border-t max-lg:px-0 max-md:mx-5"
        >
          <h3 className="m-0 mb-2.5 text-xs font-semibold text-muted">
            Properties
          </h3>
          <PropStatic
            label="Status"
            icon={
              <span
                className="size-2 rounded-full bg-muted"
                aria-hidden="true"
              />
            }
            value={<span className="text-[13px]">{item.bucket}</span>}
          />
          <PropStatic
            label="Task"
            icon={
              <span
                className="size-2 rounded-full bg-muted"
                aria-hidden="true"
              />
            }
            value={
              task?.url ? (
                <a
                  href={task.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[13px] text-foreground hover:underline"
                >
                  {task.label}
                </a>
              ) : (
                <span className="text-[13px] text-muted">
                  {task ? task.label : "None"}
                </span>
              )
            }
          />
          <PropStatic
            label="Checks"
            icon={
              <span
                className="size-2 rounded-full bg-muted"
                aria-hidden="true"
              />
            }
            value={
              <span className="text-[13px]">
                {pr
                  ? checksText(pr)
                    ? `${checksText(pr)} passing`
                    : "No checks"
                  : "—"}
              </span>
            }
          />
          {pr ? (
            <PropStatic
              label="Branch"
              icon={
                <span
                  className="size-2 rounded-full bg-muted"
                  aria-hidden="true"
                />
              }
              value={
                <span className="truncate font-mono text-xs">
                  {pr.headRef ?? "?"} → {pr.baseRef ?? "?"}
                </span>
              }
            />
          ) : null}
          {pr ? (
            <PropStatic
              label="Changes"
              icon={
                <span
                  className="size-2 rounded-full bg-muted"
                  aria-hidden="true"
                />
              }
              value={
                <span className="text-[13px] tabular-nums">
                  {pr.additions != null ? (
                    <span className="text-green-600">+{pr.additions}</span>
                  ) : null}{" "}
                  {pr.deletions != null ? (
                    <span className="text-red-500">−{pr.deletions}</span>
                  ) : null}
                  {pr.additions == null && pr.deletions == null ? (
                    <span className="text-muted">Not recorded</span>
                  ) : null}
                </span>
              }
            />
          ) : null}
          {pr?.author ? (
            <PropStatic
              label="Author"
              icon={<Avatar name={pr.author} size={16} />}
              value={<span className="text-[13px]">{pr.author}</span>}
            />
          ) : null}
          {reviewers.length ? (
            <PropStatic
              label="Reviewers"
              icon={
                <span
                  className="size-2 rounded-full bg-muted"
                  aria-hidden="true"
                />
              }
              value={
                <span className="text-[13px]">
                  {reviewers
                    .map((r) => r.login)
                    .filter(Boolean)
                    .join(", ")}
                </span>
              }
            />
          ) : null}
          <PropStatic
            label="Sources"
            icon={
              <span
                className="size-2 rounded-full bg-muted"
                aria-hidden="true"
              />
            }
            value={
              <span className="text-[13px]">
                {item.sources.map((s) => SOURCE_LABEL[s]).join(", ")}
              </span>
            }
          />
          {pr?.createdAt ? (
            <PropStatic
              label="Opened"
              icon={
                <span
                  className="size-2 rounded-full bg-muted"
                  aria-hidden="true"
                />
              }
              value={
                <span className="text-[13px]" title={pr.createdAt}>
                  {fmtDate(pr.createdAt)}
                </span>
              }
            />
          ) : null}
        </aside>
      </div>
    </article>
  );
}

/**
 * The shell hides its phone top bar on Reviews, so this header has to carry the way into the
 * app navigation. Uses the shared inline trigger when the shell provides one, otherwise a
 * button with the same look that opens the drawer through `onOpenNav`.
 */
function ReviewsNavTrigger({ onOpenNav }: { onOpenNav?: () => void }) {
  const ctx = useMobileNav();
  if (ctx) return <MobileNavTrigger />;
  if (!onOpenNav) return null;
  return (
    <button
      type="button"
      data-mobile-nav-toggle=""
      aria-label="Open navigation"
      aria-controls="workspace-sidebar"
      onClick={onOpenNav}
      className="group -ml-2 grid size-11 flex-none place-items-center rounded-lg border-0 bg-transparent p-0 text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-desktop:hidden"
    >
      <span className="grid size-7 place-items-center rounded-md transition-colors duration-120 ease-out group-hover:bg-sidebar-hover group-hover:text-foreground group-hover:transition-none">
        <PanelLeft size={16} aria-hidden="true" />
      </span>
    </button>
  );
}

export function ReviewsInbox({
  links,
  tasks = [],
  tasksError = "",
  error = "",
  gql,
  onRefresh,
  onOpenNav,
}: {
  links: ReviewLink[];
  tasks?: TaskInReviewInput[];
  tasksError?: string;
  error?: string;
  gql: Gql;
  onRefresh?: () => void;
  onOpenNav?: () => void;
}) {
  const [view, setView] = useState<ReviewsView>(() => readView());
  const [source, setSource] = useState<InboxFilter>("all");
  const [selected, setSelected] = useState<string | null>(null);

  const changeView = (next: ReviewsView) => {
    try {
      localStorage.setItem("hoja_reviews_view", next);
    } catch {
      // Storage can be blocked; the view still changes for this visit.
    }
    setView(next);
  };

  const inbox = buildInbox({ "pull-request": links, "task-in-review": tasks });
  const visible = filterItems(inbox.items, source);
  const groups = groupItems(visible);
  const selectedItem = inbox.items.find((i) => i.id === selected) ?? null;
  const pr = selectedItem?.pullRequestId
    ? (links.find((l) => l.id === selectedItem.pullRequestId) ?? null)
    : null;
  const terminal = reviewGroupsOf(links).terminal.length;

  const openItem = (id: string) => {
    setSelected(id);
    if (view !== "list") changeView("list");
  };

  const chips: {
    id: InboxFilter;
    label: string;
    connected: boolean;
    hint: string;
    count: number;
  }[] = [
    {
      id: "all",
      label: "All",
      connected: true,
      hint: "",
      count: inbox.items.length,
    },
    ...REVIEW_SOURCES.map((s) => ({
      id: s.id as InboxFilter,
      label: s.label,
      connected: s.connected,
      hint: s.connectHint ?? "",
      count: inbox.counts[s.id as InboxSourceId],
    })),
  ];

  const header = (
    <div className="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
      <ReviewsNavTrigger onOpenNav={onOpenNav} />
      <h1 className="m-0 text-[15px] font-semibold">Reviews</h1>
      <span className="text-xs text-muted tabular-nums">
        {inbox.items.length}
      </span>
      <div className="ml-auto">
        <ViewSwitch view={view} onChange={changeView} />
      </div>
    </div>
  );

  const banners = (
    <>
      {tasksError ? (
        <p role="alert" className="m-0 px-4 py-2 text-[13px] text-muted">
          {tasksError}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 px-4 py-2 text-[13px] text-red-500">
          {error}
        </p>
      ) : null}
    </>
  );

  if (view !== "list") {
    return (
      <section
        className="flex min-h-0 min-w-0 flex-1 flex-col bg-panel"
        data-reviews-page=""
        data-reviews-view={view}
      >
        {header}
        {banners}
        {view === "board" ? (
          <ReviewsBoard
            columns={boardOf(visible)}
            selected={selected}
            onSelect={openItem}
          />
        ) : (
          <ReviewsActivity events={activityOf(visible)} onSelect={openItem} />
        )}
      </section>
    );
  }

  return (
    <section
      className="flex min-h-0 min-w-0 flex-1 bg-panel"
      data-reviews-page=""
      data-reviews-view="list"
    >
      <div
        className={`${selectedItem ? "hidden md:flex" : "flex"} w-full min-h-0 flex-col border-r border-line md:w-96 md:flex-none`}
      >
        {header}
        <div
          role="toolbar"
          aria-label="Filter reviews by source"
          className="flex flex-none flex-wrap gap-1.5 border-b border-line px-3 py-2 pointer-coarse:flex-nowrap pointer-coarse:overflow-x-auto pointer-coarse:overscroll-x-contain pointer-coarse:scrollbar-none"
        >
          {chips.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={source === c.id}
              title={c.connected ? undefined : c.hint}
              onClick={() => setSource(c.id)}
              className={`inline-flex h-7 flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-xs pointer-coarse:h-11 pointer-coarse:px-3.5 ${source === c.id ? "border-line bg-subtle text-foreground" : "border-transparent text-muted hover:bg-subtle"} ${c.connected ? "" : "border-dashed"}`}
            >
              {c.label}
              {c.connected ? (
                <span className="tabular-nums text-muted">{c.count}</span>
              ) : (
                <span className="text-muted">connect</span>
              )}
            </button>
          ))}
        </div>
        {banners}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {inbox.items.length === 0 ? (
            <p className="m-0 p-4 text-sm text-muted">
              Nothing is waiting on you. Pull requests and tasks in review
              appear here when they are linked.
            </p>
          ) : visible.length === 0 ? (
            <p className="m-0 p-4 text-sm text-muted">
              {REVIEW_SOURCES.find((s) => s.id === source)?.connected === false
                ? REVIEW_SOURCES.find((s) => s.id === source)?.connectHint
                : "Nothing from this source right now."}
            </p>
          ) : (
            groups.map((g) => (
              <section key={g.bucket} aria-label={g.bucket}>
                <h2 className="m-0 flex items-center gap-2 bg-panel px-3 py-2 text-xs font-medium text-muted">
                  {g.bucket}
                  <span className="tabular-nums">{g.items.length}</span>
                </h2>
                <ul className="m-0 list-none p-0">
                  {g.items.map((item) => (
                    <ListRow
                      key={item.id}
                      item={item}
                      pr={
                        item.pullRequestId
                          ? (links.find((l) => l.id === item.pullRequestId) ??
                            null)
                          : null
                      }
                      selected={item.id === selected}
                      onSelect={() => openItem(item.id)}
                    />
                  ))}
                </ul>
              </section>
            ))
          )}
          {terminal > 0 ? (
            <p
              className="m-0 px-3 py-3 text-xs text-muted"
              title="Merged and closed pull requests are kept in the data, not in the queue."
            >
              {terminal} merged or closed pull{" "}
              {terminal === 1 ? "request is" : "requests are"} not listed
            </p>
          ) : null}
        </div>
      </div>

      <div
        className={`${selectedItem ? "flex" : "hidden md:flex"} min-h-0 min-w-0 flex-1 flex-col`}
      >
        {selectedItem ? (
          <Detail
            key={selectedItem.id}
            item={selectedItem}
            pr={pr}
            gql={gql}
            onBack={() => setSelected(null)}
            onRefresh={onRefresh}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-1 p-6 text-center text-sm text-muted">
            <p className="m-0 text-foreground">
              {visible.length} {visible.length === 1 ? "item" : "items"} to
              review
            </p>
            <p className="m-0">
              Select one to see its connections, files and actions.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

export default ReviewsInbox;
