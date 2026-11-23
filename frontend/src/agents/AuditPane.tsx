/* Screen 6 — Audit log (HOJA-602-A-16).
 *
 * `auditEvents` rows (WP05) in the issue list's layout, filtered by actor or action and paged with
 * the connection's own cursor. Grouping is the shared `DisplayPanel`, which is passed the two
 * groupings this data has (actor, action) and the three fields a row can show. The rows reuse
 * `.lin-row` so an audit row sits on the same 42 px rhythm as a task row.
 *
 * Two things this screen has to get right rather than smooth over:
 *
 *   - the ledger is admin-only, and a refusal is stated as a refusal. An empty connection would
 *     read as "nothing has happened", which is the opposite of what a 403 means;
 *   - `read_events` pages *oldest first*, so this list grows forward in time: `Load newer events`
 *     asks for the next page after the cursor, and the display order defaults to ascending to
 *     match. Sorting the loaded page newest-first instead would make every appended row jump to
 *     the top, which looks like a different row set rather than one more page.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Circle,
  Play,
  Settings2,
  ShieldOff,
  SlidersHorizontal,
  Square,
} from "lucide-react";
import { Avatar, IconBtn, cx } from "../graph/shared";
import { DisplayPanel, defaultDisplay, type LinDisplay } from "../graph/tasks";
import { AgentApiError, getAgentsAdapter } from "./adapter";
import {
  DISPLAY_ANCHOR,
  AGENTS_TABS,
  AgentsListShell,
  SearchField,
  TOUCH_LIST,
} from "./chrome";
import type { AuditEvent } from "./types";

export const AUDIT_DISPLAY: LinDisplay = {
  ...defaultDisplay,
  grouping: "action",
  ordering: "created",
  direction: "asc",
};

/* One 14 px glyph per action family. It occupies the issue row's status column: an audit row has
 * no status, and a column of identical blanks would read as a rendering bug. */
function actionGlyph(action: string) {
  if (action.startsWith("policy."))
    return <Settings2 size={14} aria-hidden="true" />;
  if (action.endsWith(".start")) return <Play size={14} aria-hidden="true" />;
  if (
    action.endsWith(".stop") ||
    action.endsWith(".close") ||
    action.endsWith(".revoke")
  )
    return <Square size={14} aria-hidden="true" />;
  if (action.startsWith("runner.") || action.startsWith("connect."))
    return <ShieldOff size={14} aria-hidden="true" />;
  return <Circle size={14} aria-hidden="true" />;
}

function stamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function AuditRow({
  event,
  show,
  selected,
}: {
  event: AuditEvent;
  show: { actor: boolean; target: boolean; time: boolean };
  selected: boolean;
}) {
  return (
    <div
      className={
        cx(
          "group relative flex items-center border-b border-line hover:bg-subtle",
          selected && "bg-accent-soft/60",
        ) +
        " flex items-center gap-2.25 min-h-10.5 pt-1.25 pr-2.5 pb-1.25 pl-2.5 border-b border-b-line text-[13px] relative hover:bg-subtle"
      }
      data-style-hooks="lin-row"
      data-action={event.action}
      data-target={event.target}
    >
      {actionGlyph(event.action)}
      {show.actor ? (
        <Avatar name={event.actor} size={14} title={event.actor} />
      ) : (
        <span />
      )}
      {/* Phone (HOJA-731): line 1 is the action and the time, line 2 the whole detail, line 3
          the target and actor. Squeezed onto one line the detail read "No …" and the target and
          actor were hidden, and nothing opens from a row to show them. */}
      <span
        className="whitespace-nowrap font-semibold text-foreground shell-phone:flex-1 shell-phone:min-w-0 shell-phone:truncate"
        data-style-hooks="ident"
      >
        {event.action}
      </span>
      {/* An audit row is a record, not a target: nothing opens from it, so it is not a button. */}
      <span
        className="flex-1 cursor-pointer truncate border-0 text-left min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-foreground [background:none] [font:inherit] pt-0 pr-0 pb-0 pl-0 shell-phone:order-2 shell-phone:basis-full shell-phone:cursor-auto shell-phone:whitespace-normal shell-phone:break-words shell-phone:pl-[46px]"
        title={event.detail}
      >
        {event.detail || "No detail recorded"}
      </span>
      <span
        className="flex flex-none items-center gap-2 ml-auto shell-phone:contents"
        data-style-hooks="meta"
      >
        {show.target && (
          <span className="hidden md:inline-flex shell-phone:order-3 shell-phone:inline-flex shell-phone:min-w-0 shell-phone:basis-0 shell-phone:grow shell-phone:ml-[46px]">
            <span
              className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel shell-phone:block shell-phone:min-w-0 shell-phone:max-w-full shell-phone:truncate shell-phone:leading-[22px]"
              title="Target"
            >
              {event.target}
            </span>
          </span>
        )}
        {show.actor && (
          <span
            className="whitespace-nowrap hidden sm:inline text-muted text-[12px] shell-phone:order-3 shell-phone:inline shell-phone:ml-auto"
            data-actor-kind={event.actorKind || undefined}
            title={
              event.actorKind
                ? `${event.actorKind} · ${event.actor}`
                : event.actor
            }
          >
            {event.actorKind === "agent" ? "agent " : ""}
            {event.actor.slice(0, 8)}
          </span>
        )}
        {show.time && (
          <span
            className="whitespace-nowrap text-muted text-[12px] shell-phone:order-1"
            title={event.at}
          >
            {stamp(event.at)}
          </span>
        )}
      </span>
    </div>
  );
}

export function AuditPane({
  workspaceName,
  tab,
  onTab,
}: {
  workspaceName: string;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refused, setRefused] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState("");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [displayOpen, setDisplayOpen] = useState(false);
  const [display, setDisplay] = useState<LinDisplay>({ ...AUDIT_DISPLAY });
  const [show, setShow] = useState({ actor: true, target: true, time: true });

  /* One page, from the top, whenever the server-side filter changes: `actorId` and `action` are
   * the resolver's arguments, so the filter has to be part of the request rather than a pass over
   * whatever happens to be loaded. */
  useEffect(() => {
    let live = true;
    setLoading(true);
    setRefused(false);
    getAgentsAdapter()
      .listAuditEvents({ actor, action })
      .then((page) => {
        if (!live) return;
        setEvents(page.events);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setError("");
      })
      .catch((e: Error) => {
        if (!live) return;
        const forbidden = e instanceof AgentApiError && e.code === "FORBIDDEN";
        setEvents([]);
        setCursor(null);
        setHasMore(false);
        setRefused(forbidden);
        setError(forbidden ? "" : e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [actor, action]);

  const loadMore = async () => {
    if (!cursor || paging) return;
    setPaging(true);
    try {
      const page = await getAgentsAdapter().listAuditEvents({
        actor,
        action,
        after: cursor,
      });
      setEvents((current) => [...current, ...page.events]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPaging(false);
    }
  };

  const actors = useMemo(
    () => [...new Set(events.map((e) => e.actor))].sort(),
    [events],
  );
  const actions = useMemo(
    () => [...new Set(events.map((e) => e.action))].sort(),
    [events],
  );

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = events.filter(
      (e) =>
        (!actor || e.actor === actor) &&
        (!action || e.action === action) &&
        (!needle ||
          `${e.actor} ${e.action} ${e.target} ${e.detail}`
            .toLowerCase()
            .includes(needle)),
    );
    const dir = display.direction === "asc" ? 1 : -1;
    return rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) * dir);
  }, [events, actor, action, q, display.direction]);

  const groups = useMemo(() => {
    if (display.grouping === "none")
      return [{ key: "all", name: "", items: visible }];
    const key = display.grouping === "action" ? "action" : "actor";
    const map = new Map<
      string,
      { key: string; name: string; items: AuditEvent[] }
    >();
    for (const e of visible) {
      const k = e[key];
      if (!map.has(k)) map.set(k, { key: k, name: k, items: [] });
      map.get(k)!.items.push(e);
    }
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [visible, display.grouping]);

  const chips = [
    actor ? { label: `Actor: ${actor}`, clear: () => setActor("") } : null,
    action ? { label: `Action: ${action}`, clear: () => setAction("") } : null,
  ].filter(Boolean) as { label: string; clear: () => void }[];

  return (
    <AgentsListShell
      ariaLabel="Audit log"
      surface="audit-log"
      workspaceName={workspaceName}
      trail="Audit log"
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbarWraps
      toolbar={
        <>
          <SearchField
            value={q}
            onChange={setQ}
            placeholder="Search the audit log…"
            label="Search the audit log"
            className="max-sm:basis-[calc(100%-50px)]"
          />
          <label className="flex max-w-72 flex-1 items-center border border-line max-w-44 gap-2 border-solid rounded-[7px] pt-0 pr-2.5 pb-0 pl-2.5 h-7.5 text-muted bg-panel min-w-37.5 flex-[0_1_180px] pointer-coarse:h-11 shrink-0 max-sm:order-2 max-sm:min-w-0 max-sm:max-w-none max-sm:flex-[1_1_0]">
            <span className="text-[12px] text-muted">Actor</span>
            <select
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              aria-label="Filter by actor"
              className="flex-1 border-0 bg-transparent text-foreground text-[13px] min-w-0 pointer-coarse:self-stretch pointer-coarse:-my-px"
            >
              <option value="">Anyone</option>
              {actors.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
          <label className="flex max-w-72 flex-1 items-center border border-line max-w-44 gap-2 border-solid rounded-[7px] pt-0 pr-2.5 pb-0 pl-2.5 h-7.5 text-muted bg-panel min-w-37.5 flex-[0_1_180px] pointer-coarse:h-11 shrink-0 max-sm:order-2 max-sm:min-w-0 max-sm:max-w-none max-sm:flex-[1_1_0]">
            <span className="text-[12px] text-muted">Action</span>
            <select
              value={action}
              onChange={(e) => setAction(e.target.value)}
              aria-label="Filter by action"
              className="flex-1 border-0 bg-transparent text-foreground text-[13px] min-w-0 pointer-coarse:self-stretch pointer-coarse:-my-px"
            >
              <option value="">Any action</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
          <span className="flex-1 max-sm:hidden" />
          <span className={`${DISPLAY_ANCHOR} max-sm:order-1`}>
            <IconBtn
              label="Display options"
              title="Display options"
              active={displayOpen}
              onClick={() => setDisplayOpen((v) => !v)}
            >
              <SlidersHorizontal size={15} />
            </IconBtn>
            {displayOpen && (
              <DisplayPanel
                display={display}
                setDisplay={(d) => setDisplay({ ...display, ...d })}
                onClose={() => setDisplayOpen(false)}
                groupingOptions={[
                  ["none", "No grouping"],
                  ["actor", "Actor"],
                  ["action", "Action"],
                ]}
                groupingLabel="Group audit events by"
                entityToggles={false}
                propertyRows={[
                  {
                    key: "actor",
                    label: "Actor",
                    checked: show.actor,
                    onToggle: () => setShow((s) => ({ ...s, actor: !s.actor })),
                  },
                  {
                    key: "target",
                    label: "Target",
                    checked: show.target,
                    onToggle: () =>
                      setShow((s) => ({ ...s, target: !s.target })),
                  },
                  {
                    key: "time",
                    label: "Time",
                    checked: show.time,
                    onToggle: () => setShow((s) => ({ ...s, time: !s.time })),
                  },
                ]}
              />
            )}
          </span>
        </>
      }
      chips={
        chips.length ? (
          <>
            {chips.map((c) => (
              <span
                key={c.label}
                className="inline-flex items-center border border-line gap-1.5 text-[12px] border-solid bg-subtle rounded-[99px] pt-0.75 pr-1.5 pb-0.75 pl-2.5 text-foreground"
              >
                {c.label}
                <button
                  className="border-0 hover:text-foreground [background:none] text-muted text-[13px] leading-none pt-0.5 pr-1 pb-0.5 pl-1 rounded-sm pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:-my-3 pointer-coarse:-mr-1.5"
                  type="button"
                  aria-label={`Remove filter ${c.label}`}
                  onClick={c.clear}
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground gap-2 [background:none] text-muted text-[13px] pt-1.25 pr-1.5 pb-1.25 pl-1.5 rounded-md pointer-coarse:min-h-11"
              onClick={() => {
                setActor("");
                setAction("");
              }}
            >
              Clear all
            </button>
          </>
        ) : null
      }
    >
      {loading && (
        <p
          className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
          role="status"
        >
          Loading the audit log…
        </p>
      )}
      {!loading && refused && (
        <div
          className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
          data-state="error"
          data-refusal="forbidden"
          data-style-hooks="lin-error"
          role="alert"
        >
          <h2 className="text-foreground text-base mt-3 mr-0 mb-1.5 ml-0">
            Only workspace admins can read the audit log
          </h2>
          <p>
            The ledger records who asked for every audited action, so this
            server refuses the read rather than showing you an empty log. Ask a
            workspace admin for access.
          </p>
        </div>
      )}
      {!loading && !refused && error && (
        <p
          className="border border-danger/40 mt-4 mr-6 mb-4 ml-6 pt-3 pr-4 pb-3 pl-4 border-solid rounded-lg text-danger text-sm bg-panel"
          role="alert"
        >
          {error}
        </p>
      )}
      {!loading && !refused && !error && (
        <div
          className={`pt-1.5 pr-3 pb-10 pl-3 ${TOUCH_LIST}`}
          data-style-hooks="lin-list"
        >
          {groups.map((g) => (
            <section
              key={g.key}
              className="mt-2 mr-2 mb-1 ml-2"
              data-style-hooks="lin-group"
            >
              {g.name && (
                <header
                  className="flex items-center gap-2 bg-subtle rounded-md min-h-9 pt-0 pr-2.5 pb-0 pl-2.5 text-sm"
                  data-style-hooks="lin-group-head"
                >
                  <b className="font-semibold">{g.name}</b>
                  <span className="text-muted font-medium text-xs">
                    {g.items.length}
                  </span>
                  <span className="flex-1" />
                </header>
              )}
              {g.items.map((e) => (
                <AuditRow key={e.id} event={e} show={show} selected={false} />
              ))}
            </section>
          ))}
          {!visible.length && (
            <div
              className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
              data-state="empty"
            >
              <h2 className="text-foreground text-base mt-3 mr-0 mb-1.5 ml-0">
                {events.length
                  ? "No audit events match"
                  : "Nothing recorded yet"}
              </h2>
              <p>
                {events.length
                  ? "The filters and the search box narrow the events loaded so far; clear them to see the whole page."
                  : "No audited action has been recorded in this workspace yet."}
              </p>
            </div>
          )}
          <div className="flex items-center gap-2 pt-2 pb-2 text-xs text-muted">
            <span>
              {events.length} event{events.length === 1 ? "" : "s"} loaded,
              oldest first
            </span>
            {hasMore && (
              <button
                type="button"
                className="inline-flex items-center rounded-md border border-line bg-panel px-2 py-1 text-foreground hover:bg-subtle pointer-coarse:min-h-11 pointer-coarse:px-3"
                data-action="load-more"
                disabled={paging}
                onClick={() => void loadMore()}
              >
                {paging ? "Loading…" : "Load newer events"}
              </button>
            )}
          </div>
        </div>
      )}
    </AgentsListShell>
  );
}
