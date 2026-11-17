import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { gql, relTime, StatusIcon } from "./main";
import type { Issue, IssueState, Project, PulsePost, Team } from "./main";
import {
  usePagedCache,
  useProjectEntityVersion,
  type PageOf,
} from "./projectStore";
import { Markdown } from "./markdown";

/* Project Activity: one timeline of project updates and the events on the project's issues.
 * Features: merged timeline, day groups, type filters, readable event sentences, issue links,
 * health trend + staleness, a keyboard-friendly composer, "new since your last visit",
 * merged paging across both sources, and relative times with exact timestamps. */

type ActivityEvent = {
  id: string;
  eventType: string;
  createdAt: string;
  changes?: Record<string, { from: unknown; to: unknown }> | null;
  actor?: { id: string; name?: string } | null;
  issue: Pick<Issue, "id" | "identifier" | "title">;
};
type Paged<T> = {
  rows: T[];
  hasData: boolean;
  more: boolean;
  loadingMore: boolean;
  error: string;
  loadMore: () => void;
  retry: () => void;
};
type Entry =
  | { kind: "update"; at: string; id: string; update: PulsePost }
  | { kind: "event"; at: string; id: string; event: ActivityEvent };
export type ActivityFilter =
  "All" | "Updates" | "Status changes" | "Comments" | "New tasks";
const FILTERS: ActivityFilter[] = [
  "All",
  "Updates",
  "Status changes",
  "Comments",
  "New tasks",
];
const EVENT_TYPES: Record<ActivityFilter, string[] | null> = {
  All: null,
  Updates: null,
  "Status changes": ["issueUpdated"],
  Comments: ["commentCreated", "commentUpdated"],
  "New tasks": ["issueCreated"],
};
const HEALTH = ["on track", "at risk", "off track"] as const;
const PRIORITY = ["No priority", "Urgent", "High", "Medium", "Low"];
const STALE_DAYS = 7;
const EVENT_FIELDS =
  "id eventType changes createdAt actor{id name} issue{id identifier title}";

const dayKey = (iso: string) => iso.slice(0, 10);
function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const strip = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((strip(now) - strip(d)) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    weekday: diff < 7 ? "long" : undefined,
    month: "short",
    day: "numeric",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}
const healthColors = (health?: string) =>
  health === "on track"
    ? "bg-green-100 text-green-800"
    : health === "at risk"
      ? "bg-amber-100 text-amber-800"
      : health === "off track"
        ? "bg-red-100 text-red-800"
        : "bg-subtle text-muted";
const healthDot = (health?: string) =>
  health === "on track"
    ? "bg-green-600"
    : health === "at risk"
      ? "bg-amber-500"
      : health === "off track"
        ? "bg-red-600"
        : "bg-line";

function Time({ iso }: { iso: string }) {
  return (
    <time
      className="flex-none text-xs text-muted"
      dateTime={iso}
      title={new Date(iso).toLocaleString()}
    >
      {relTime(iso)}
    </time>
  );
}

export function ProjectActivity({
  project,
  teams,
  users,
  updates,
  onPublish,
  onOpenIssue,
}: {
  project: Project;
  teams: Team[];
  users: Map<string, string>;
  updates: Paged<PulsePost>;
  onPublish: (body: string, health: string) => Promise<void>;
  onOpenIssue: (issue: Pick<Issue, "id" | "identifier" | "title">) => void;
}) {
  const [filter, setFilter] = useState<ActivityFilter>("All");
  const types = EVENT_TYPES[filter];
  const eventsVersion = useProjectEntityVersion(["issue", "comment"]);
  const fetchEvents = useCallback(
    (first: number, after: string | null) =>
      gql(
        `query ProjectActivity($id:String!,$first:Int,$after:String,$types:[String!]){project(id:$id){activity(first:$first,after:$after,eventTypes:$types){nodes{${EVENT_FIELDS}} pageInfo{hasNextPage endCursor}}}}`,
        { id: project.id, first, after, types },
      ).then((d) => d.project.activity as PageOf<ActivityEvent>),
    [project.id, types],
  );
  const eventsKey =
    filter === "Updates" ? null : `projectActivity:${project.id}:${filter}`;
  const events = usePagedCache<ActivityEvent>(
    eventsKey,
    fetchEvents,
    eventsVersion,
    50,
    "Project activity",
  );

  // "New since your last visit": read the previous visit once, record this one.
  const seenKey = `hoja_project_activity_seen_${project.id}`;
  const [lastSeen] = useState(() => localStorage.getItem(seenKey) || "");
  // The visit counts as "seen" up to when it ends, so your own posts during it are not "new" next time.
  useEffect(() => {
    const mark = () => {
      try {
        localStorage.setItem(seenKey, new Date().toISOString());
      } catch {
        /* storage unavailable */
      }
    };
    mark();
    window.addEventListener("pagehide", mark);
    return () => {
      window.removeEventListener("pagehide", mark);
      mark();
    };
  }, [seenKey]);

  const states = useMemo(() => {
    const m = new Map<string, IssueState>();
    for (const t of teams) for (const s of t.states.nodes) m.set(s.id, s);
    return m;
  }, [teams]);

  // Merge both sources; only show entries newer than the oldest row of any source that still has
  // older pages, so a later page can never insert something above what is already on screen.
  const sources = useMemo(() => {
    const out: { rows: { at: string }[]; paged: Paged<unknown> }[] = [];
    if (filter === "All" || filter === "Updates")
      out.push({
        rows: updates.rows.map((u) => ({ at: u.createdAt })),
        paged: updates as Paged<unknown>,
      });
    if (filter !== "Updates")
      out.push({
        rows: events.rows.map((e) => ({ at: e.createdAt })),
        paged: events as unknown as Paged<unknown>,
      });
    return out;
  }, [filter, updates, events]);
  const cutoff = sources.reduce(
    (c, s) =>
      s.paged.more && s.rows.length
        ? s.rows[s.rows.length - 1].at > c
          ? s.rows[s.rows.length - 1].at
          : c
        : c,
    "",
  );
  const entries = useMemo(() => {
    const all: Entry[] = [];
    if (filter === "All" || filter === "Updates")
      for (const u of updates.rows)
        all.push({
          kind: "update",
          at: u.createdAt,
          id: `u:${u.id}`,
          update: u,
        });
    if (filter !== "Updates")
      for (const e of events.rows) {
        if (filter === "Status changes" && !e.changes?.stateId) continue;
        all.push({ kind: "event", at: e.createdAt, id: `e:${e.id}`, event: e });
      }
    return all
      .filter((x) => x.at >= cutoff)
      .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  }, [filter, updates.rows, events.rows, cutoff]);
  const more = sources.some((s) => s.paged.more);
  const loadingMore = sources.some((s) => s.paged.loadingMore);
  const loadOlder = () => {
    for (const s of sources)
      if (
        s.paged.more &&
        (!s.rows.length || s.rows[s.rows.length - 1].at === cutoff)
      )
        s.paged.loadMore();
  };
  const ready = sources.every((s) => s.paged.hasData || s.paged.error);
  const errors = sources.filter((s) => s.paged.error);
  const newCount = lastSeen ? entries.filter((x) => x.at > lastSeen).length : 0;

  const groups = useMemo(() => {
    const g: { day: string; label: string; items: Entry[] }[] = [];
    for (const x of entries) {
      const k = dayKey(x.at);
      if (g[g.length - 1]?.day !== k)
        g.push({ day: k, label: dayLabel(x.at), items: [] });
      g[g.length - 1].items.push(x);
    }
    return g;
  }, [entries]);

  // Health: newest update first; trend shows the last six, oldest to newest.
  const latest =
    updates.rows.find((u) => !u.id.startsWith("optimistic:")) ||
    updates.rows[0];
  const trend = updates.rows.slice(0, 6).reverse();
  const staleDays = latest
    ? Math.floor((Date.now() - Date.parse(latest.createdAt)) / 864e5)
    : null;

  // Composer
  const [body, setBody] = useState(""),
    [health, setHealth] = useState<string>(
      latest?.status &&
        HEALTH.includes(latest.status as (typeof HEALTH)[number])
        ? latest.status
        : "on track",
    );
  const [busy, setBusy] = useState(false),
    [note, setNote] = useState<{ error: boolean; text: string } | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  // Default the health choice to the latest update's, until the user picks one.
  const pickedHealth = useRef(false);
  useEffect(() => {
    if (
      !pickedHealth.current &&
      latest?.status &&
      HEALTH.includes(latest.status as (typeof HEALTH)[number])
    )
      setHealth(latest.status);
  }, [latest?.status]);
  const submit = async () => {
    const text = body.trim();
    if (!text || busy) return;
    setBusy(true);
    setNote(null);
    setBody("");
    try {
      await onPublish(text, health);
      setNote({ error: false, text: "Update posted." });
    } catch (err) {
      setBody(text);
      setNote({
        error: true,
        text: `${(err as Error).message} Your text was kept; check Pulse before retrying to avoid a duplicate.`,
      });
    } finally {
      setBusy(false);
    }
  };

  const issueRef = (i: ActivityEvent["issue"]) => (
    <button
      type="button"
      className="inline border-0 text-left cursor-pointer shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 max-w-full pt-0 pr-0 pb-0 pl-0 [background:none] text-foreground [font:inherit]"
      data-activity-issue=""
      onClick={() => onOpenIssue(i)}
      title={i.title}
    >
      <code className="text-muted font-[inherit] text-[12px]">
        {i.identifier}
      </code>{" "}
      <span className="underline decoration-line underline-offset-2 hover:decoration-current">
        {i.title}
      </span>
    </button>
  );
  const stateName = (id: unknown) => {
    const s = typeof id === "string" ? states.get(id) : undefined;
    return s ? (
      <span className="inline-flex items-center gap-1 font-medium">
        <StatusIcon state={s} />
        {s.name}
      </span>
    ) : (
      <em>unknown status</em>
    );
  };
  const person = (id: unknown) =>
    typeof id === "string" ? (
      <b>{users.get(id) || "a former member"}</b>
    ) : (
      <b>nobody</b>
    );
  type Line = {
    key: string;
    node: React.ReactNode;
    plural: (n: number) => React.ReactNode;
  };
  const lines = (e: ActivityEvent): Line[] => {
    const c = e.changes || {},
      ref = issueRef(e.issue),
      many = (n: number) => <b>{n} tasks</b>;
    switch (e.eventType) {
      case "issueCreated":
        return [
          {
            key: "created",
            node: <>created {ref}</>,
            plural: (n) => <>created {many(n)}</>,
          },
        ];
      case "commentCreated":
        return [
          {
            key: "comment",
            node: <>commented on {ref}</>,
            plural: (n) => <>commented on {many(n)}</>,
          },
        ];
      case "commentUpdated":
        return [
          {
            key: "commentEdit",
            node: <>edited a comment on {ref}</>,
            plural: (n) => <>edited comments on {many(n)}</>,
          },
        ];
      case "issueArchived":
        return [
          {
            key: "archived",
            node: <>archived {ref}</>,
            plural: (n) => <>archived {many(n)}</>,
          },
        ];
    }
    const out: Line[] = [];
    if (c.stateId)
      out.push({
        key: `state:${c.stateId.from}>${c.stateId.to}`,
        node: (
          <>
            moved {ref} from {stateName(c.stateId.from)} to{" "}
            {stateName(c.stateId.to)}
          </>
        ),
        plural: (n) => (
          <>
            moved {many(n)} from {stateName(c.stateId.from)} to{" "}
            {stateName(c.stateId.to)}
          </>
        ),
      });
    if (c.assigneeId)
      out.push(
        c.assigneeId.to
          ? {
              key: `assign:${c.assigneeId.to}`,
              node: (
                <>
                  assigned {ref} to {person(c.assigneeId.to)}
                </>
              ),
              plural: (n) => (
                <>
                  assigned {many(n)} to {person(c.assigneeId.to)}
                </>
              ),
            }
          : {
              key: "unassign",
              node: <>unassigned {ref}</>,
              plural: (n) => <>unassigned {many(n)}</>,
            },
      );
    if (c.priority)
      out.push({
        key: `priority:${c.priority.to}`,
        node: (
          <>
            set priority of {ref} to{" "}
            <b>{PRIORITY[Number(c.priority.to) || 0]}</b>
          </>
        ),
        plural: (n) => (
          <>
            set priority of {many(n)} to{" "}
            <b>{PRIORITY[Number(c.priority.to) || 0]}</b>
          </>
        ),
      });
    if (c.projectId)
      out.push(
        c.projectId.to === project.id
          ? {
              key: "projectIn",
              node: <>added {ref} to this project</>,
              plural: (n) => <>added {many(n)} to this project</>,
            }
          : {
              key: "projectOut",
              node: <>moved {ref} out of this project</>,
              plural: (n) => <>moved {many(n)} out of this project</>,
            },
      );
    if (c.title)
      out.push({
        key: "title",
        node: <>renamed {ref}</>,
        plural: (n) => <>renamed {many(n)}</>,
      });
    if (c.description)
      out.push({
        key: "description",
        node: <>updated the description of {ref}</>,
        plural: (n) => <>updated descriptions of {many(n)}</>,
      });
    return out.length
      ? out
      : [
          {
            key: "updated",
            node: <>updated {ref}</>,
            plural: (n) => <>updated {many(n)}</>,
          },
        ];
  };
  // Bursts (3+ same-kind changes by one person within 10 minutes, e.g. a bulk move) collapse into one expandable line.
  type Row =
    | { kind: "update"; x: Entry & { kind: "update" } }
    | {
        kind: "lines";
        at: string;
        actor: string;
        name: string;
        key: string;
        items: { id: string; at: string; type: string; line: Line }[];
      };
  const BURST_MS = 10 * 60e3,
    BURST_MIN = 3;
  const rowsOf = (items: Entry[]): Row[] => {
    const out: Row[] = [];
    for (const x of items) {
      if (x.kind === "update") {
        out.push({ kind: "update", x });
        continue;
      }
      const actor = x.event.actor?.id || "";
      for (const [i, line] of lines(x.event).entries()) {
        const prev = out[out.length - 1];
        const item = {
          id: `${x.id}:${i}`,
          at: x.at,
          type: x.event.eventType,
          line,
        };
        if (
          prev?.kind === "lines" &&
          prev.actor === actor &&
          prev.key === line.key &&
          Date.parse(prev.items[prev.items.length - 1].at) - Date.parse(x.at) <=
            BURST_MS
        )
          prev.items.push(item);
        else
          out.push({
            kind: "lines",
            at: x.at,
            actor,
            name: x.event.actor?.name || users.get(actor) || "Someone",
            key: line.key,
            items: [item],
          });
      }
    }
    // Short runs stay as individual lines.
    return out.flatMap((r) =>
      r.kind === "lines" && r.items.length > 1 && r.items.length < BURST_MIN
        ? r.items.map((it) => ({ ...r, at: it.at, items: [it] }))
        : [r],
    );
  };
  const isNew = (at: string) => Boolean(lastSeen && at > lastSeen) || undefined;

  let dividerShown = false;
  return (
    <section
      className="w-full border-b border-line min-w-0 pt-0 pr-5 pb-10 pl-5 border-b-line max-w-190 mt-4 mr-auto mb-4 ml-auto shell-phone:pt-0 shell-phone:pr-2.5 shell-phone:pb-6 shell-phone:pl-2.5 shell-phone:order-2 text-[13px]"
      data-project-reference-activity=""
      data-project-activity=""
      aria-labelledby="project-activity-title"
    >
      <header
        className="flex flex-wrap items-center justify-between gap-2.5 mb-3"
        data-activity-head=""
      >
        <h2
          className="flex items-center mt-0 mr-0 mb-0 ml-0 text-[15px] font-semibold gap-2"
          id="project-activity-title"
        >
          Activity
          {newCount > 0 && (
            <span
              className="pt-px pr-2 pb-px pl-2 rounded-[99px] bg-accent-soft text-accent text-[11px] font-semibold"
              aria-label={`${newCount} new since your last visit`}
            >
              {newCount} new
            </span>
          )}
        </h2>
        <div
          className="flex flex-wrap items-center gap-2 text-[12px]"
          aria-label="Project health"
        >
          {latest ? (
            <>
              <span
                className={
                  `inline-flex items-center ${healthColors(latest.status)}` +
                  " inline-flex items-center h-5 pt-0 pr-2 pb-0 pl-2 rounded-[99px] bg-subtle text-muted text-[11px] font-semibold capitalize"
                }
                data-activity-health-pill=""
              >
                {latest.status || "No health"}
              </span>
              <span
                className="inline-flex gap-0.75"
                data-activity-trend=""
                aria-label={`Health of the last ${trend.length} updates: ${trend.map((u) => u.status || "none").join(", ")}`}
              >
                {trend.map((u) => (
                  <i
                    key={u.id}
                    className={
                      `size-2 ${healthDot(u.status)}` +
                      " w-2 h-2 rounded-[50%] bg-line"
                    }
                    title={`${u.status || "No health"} · ${new Date(u.createdAt).toLocaleDateString()}`}
                  />
                ))}
              </span>
              <span
                className={
                  (staleDays !== null && staleDays >= STALE_DAYS ? "" : "") +
                  " text-[#b54708] font-medium"
                }
                data-activity-stale={
                  staleDays !== null && staleDays >= STALE_DAYS ? "" : undefined
                }
                data-stale={
                  (staleDays !== null && staleDays >= STALE_DAYS) || undefined
                }
              >
                Last update <Time iso={latest.createdAt} />
                {staleDays !== null && staleDays >= STALE_DAYS
                  ? " — time for a new one"
                  : ""}
              </span>
            </>
          ) : updates.hasData ? (
            <span
              className="text-[#b54708] font-medium"
              data-activity-stale=""
              data-stale
            >
              No updates yet
            </span>
          ) : null}
        </div>
      </header>

      <form
        className="grid border border-line max-w-180 mt-5 mr-0 mb-5 ml-0 gap-2 pt-3 pr-3 pb-3 pl-3 border-solid rounded-[10px] bg-panel"
        data-activity-composer=""
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        aria-label="Post a project update"
      >
        <label
          className="sr-only absolute w-px h-px pt-0 pr-0 pb-0 pl-0 -mt-px -mr-px -mb-px -ml-px overflow-hidden [clip:rect(0,0,0,0)] whitespace-nowrap border-0"
          htmlFor="activity-body"
        >
          Update
        </label>
        <textarea
          className="resize-y border border-line max-md:text-base shell-phone:min-h-11 pointer-coarse:min-h-11 w-full min-h-16 pt-2 pr-2 pb-2 pl-2 border-solid rounded-[7px] bg-bg text-foreground [font:inherit]"
          id="activity-body"
          ref={bodyRef}
          value={body}
          rows={3}
          placeholder="What changed, what's next, any risks?"
          onChange={(e) => {
            setBody(e.target.value);
            setNote(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <div
          className="flex flex-wrap items-center gap-2"
          data-activity-composer-row=""
        >
          <fieldset
            className="flex border-0 gap-1 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
            data-activity-health-pick=""
          >
            <legend className="sr-only absolute w-px h-px pt-0 pr-0 pb-0 pl-0 -mt-px -mr-px -mb-px -ml-px overflow-hidden [clip:rect(0,0,0,0)] whitespace-nowrap border-0">
              Health
            </legend>
            {HEALTH.map((h) => (
              <label
                key={h}
                className="inline-flex cursor-pointer items-center border border-line has-checked:bg-subtle has-checked:font-semibold has-focus-visible:outline-2 has-focus-visible:outline-accent has-focus-visible:outline-offset-2 gap-1.25 min-h-7 pt-0 pr-2.5 pb-0 pl-2.5 border-solid rounded-[99px] text-[12px]"
              >
                <input
                  className="accent-accent mt-0 mr-0 mb-0 ml-0"
                  type="radio"
                  name="activity-health"
                  value={h}
                  checked={health === h}
                  onChange={() => {
                    pickedHealth.current = true;
                    setHealth(h);
                  }}
                />
                {h[0].toUpperCase() + h.slice(1)}
              </label>
            ))}
          </fieldset>
          <small className="max-md:hidden text-muted shell-phone:hidden">
            Ctrl/⌘ + Enter
          </small>
          <button
            className="inline-flex items-center justify-center disabled:cursor-not-allowed disabled:opacity-50 max-md:w-full shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-none bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719] ml-auto shell-phone:flex-[1_1_100%]"
            disabled={busy || !body.trim()}
          >
            {busy ? "Posting…" : "Post update"}
          </button>
        </div>
        {note && (
          <p
            role={note.error ? "alert" : "status"}
            className={
              (note.error ? "block" : "") +
              " text-danger block mt-3 mr-0 mb-3 ml-0"
            }
          >
            {note.text}
          </p>
        )}
      </form>

      <div
        className="flex flex-wrap gap-1.5 mt-0 mr-0 mb-2.5 ml-0"
        data-activity-filters=""
        role="group"
        aria-label="Filter activity"
      >
        {FILTERS.map((f) => (
          <button
            className="border border-line aria-pressed:bg-subtle aria-pressed:font-semibold aria-pressed:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 min-h-7 pt-0 pr-2.75 pb-0 pl-2.75 border-solid rounded-[99px] bg-panel text-muted text-[12px]"
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      {errors.map((s, i) => (
        <p
          key={i}
          role="alert"
          className="block text-danger mt-3 mr-0 mb-3 ml-0"
        >
          {s.paged.error}{" "}
          <button
            type="button"
            onClick={s.paged.retry}
            className="shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          >
            Retry
          </button>
        </p>
      ))}
      {!ready && (
        <p role="status" className="text-muted">
          Loading activity…
        </p>
      )}
      {ready && !entries.length && !errors.length && (
        <div
          className="text-center pt-8 pr-3 pb-8 pl-3 text-muted"
          data-activity-empty=""
        >
          <h3 className="mt-0 mr-0 mb-1.5 ml-0 text-foreground text-[14px]">
            {filter === "All"
              ? "No activity yet"
              : `No ${filter.toLowerCase()} yet`}
          </h3>
          <p>
            {filter === "All" || filter === "Updates"
              ? "Post an update above to tell the team how the project is going."
              : "Changes to this project’s tasks will appear here."}
          </p>
          {filter !== "All" && (
            <button
              type="button"
              onClick={() => setFilter("All")}
              className="shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            >
              Show all activity
            </button>
          )}
        </div>
      )}

      <ol
        className="list-none mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
        data-activity-feed=""
        aria-label="Project activity"
      >
        {groups.map((g) => (
          <li key={g.day} data-activity-day="">
            <h3 className="sticky top-0 z-1 mt-3.5 mr-0 mb-1 ml-0 pt-1 pr-0 pb-1 pl-0 bg-bg text-muted text-[11px] font-semibold uppercase tracking-[.04em]">
              {g.label}
            </h3>
            <ol className="list-none mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0">
              {rowsOf(g.items).map((r) => {
                const at = r.kind === "update" ? r.x.at : r.items[0].at;
                const divider =
                  !dividerShown && lastSeen && newCount > 0 && at <= lastSeen
                    ? (dividerShown = true)
                    : false;
                const id = r.kind === "update" ? r.x.id : r.items[0].id;
                return (
                  <React.Fragment key={id}>
                    {divider && (
                      <li
                        className="flex items-center gap-2 mt-2 mr-0 mb-2 ml-0 text-muted text-[11px]"
                        data-activity-divider=""
                        role="separator"
                      >
                        Earlier — before your last visit
                      </li>
                    )}
                    {r.kind === "update" ? (
                      <li
                        className="block wrap-break-word data-new:bg-accent-soft border border-line flex-wrap items-baseline justify-between gap-[4px_10px] min-w-0 pt-2.5 pr-3 pb-2.5 pl-3 rounded-[7px] wrap-anywhere mt-1 mr-0 mb-1 ml-0 border-solid bg-panel"
                        data-activity-entry=""
                        data-activity-update=""
                        data-new={isNew(at)}
                        aria-busy={
                          r.x.update.id.startsWith("optimistic:") || undefined
                        }
                      >
                        <div
                          className="flex flex-wrap items-center gap-1.5 text-[12px]"
                          data-activity-meta=""
                        >
                          <span
                            className={
                              `inline-flex items-center ${healthColors(r.x.update.status)}` +
                              " inline-flex items-center h-5 pt-0 pr-2 pb-0 pl-2 rounded-[99px] bg-subtle text-muted text-[11px] font-semibold capitalize"
                            }
                            data-activity-health-pill=""
                          >
                            {r.x.update.status || "Update"}
                          </span>
                          <b>{r.x.update.actor?.name || "Member"}</b> posted an
                          update
                          {r.x.update.progress != null && (
                            <span className="text-muted">
                              {" "}
                              · {Math.round(r.x.update.progress)}% done
                            </span>
                          )}{" "}
                          · <Time iso={at} />
                        </div>
                        <Markdown
                          className="mt-1.5"
                          text={r.x.update.body}
                          density="block"
                        />
                      </li>
                    ) : r.items.length === 1 ? (
                      <li
                        className="flex flex-wrap items-baseline justify-between wrap-break-word data-new:bg-accent-soft gap-[4px_10px] min-w-0 pt-1.75 pr-2 pb-1.75 pl-2 rounded-[7px] wrap-anywhere"
                        data-activity-entry=""
                        data-type={r.items[0].type}
                        data-new={isNew(at)}
                      >
                        <span>
                          <b>{r.name}</b> {r.items[0].line.node}
                        </span>{" "}
                        <Time iso={at} />
                      </li>
                    ) : (
                      <li
                        className="block wrap-break-word data-new:bg-accent-soft flex-wrap items-baseline justify-between gap-[4px_10px] min-w-0 pt-1.75 pr-2 pb-1.75 pl-2 rounded-[7px] wrap-anywhere"
                        data-activity-entry=""
                        data-activity-burst=""
                        data-type={r.items[0].type}
                        data-count={r.items.length}
                        data-new={
                          r.items.some((it) => isNew(it.at)) || undefined
                        }
                      >
                        <details className="group">
                          <summary className="flex cursor-pointer list-none flex-wrap items-baseline justify-between gap-[4px_10px]">
                            <span
                              className="text-muted group-open:rotate-90"
                              aria-hidden="true"
                            >
                              ▸
                            </span>
                            <span>
                              <b>{r.name}</b>{" "}
                              {r.items[0].line.plural(r.items.length)}
                            </span>{" "}
                            <Time iso={at} />
                          </summary>
                          <ol className="list-none border-l border-line mt-1.5 mr-0 mb-0.5 ml-3.5 pt-0 pr-0 pb-0 pl-2.5 border-l-line">
                            {r.items.map((it) => (
                              <li
                                key={it.id}
                                className="flex flex-wrap items-baseline justify-between gap-[2px_10px] pt-0.75 pr-0 pb-0.75 pl-0 text-[12px]"
                                data-activity-burst-item=""
                              >
                                <span>{it.line.node}</span> <Time iso={it.at} />
                              </li>
                            ))}
                          </ol>
                        </details>
                      </li>
                    )}
                  </React.Fragment>
                );
              })}
            </ol>
          </li>
        ))}
      </ol>

      {ready &&
        entries.length > 0 &&
        (more ? (
          <button
            type="button"
            className="block border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 mt-3.5 mr-auto mb-0 ml-auto pt-1.75 pr-3.5 pb-1.75 pl-3.5 border-solid rounded-[7px] bg-panel text-[12px]"
            disabled={loadingMore}
            onClick={loadOlder}
          >
            {loadingMore ? "Loading…" : "Load older activity"}
          </button>
        ) : (
          <p className="text-center text-muted mt-3.5 mr-0 mb-0 ml-0 text-[12px]">
            You’ve reached the beginning of this project’s activity.
          </p>
        ))}
    </section>
  );
}
