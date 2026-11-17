import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { fmtTime, meetingsApi, type Meeting, fmtDuration } from "./api";
import "./meetings-page.css";

type View = "month" | "week";
const HOUR_PX = 48;
const MAX_CHIPS = 3;

const startOfDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d: Date, n: number) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d: Date) =>
  addDays(startOfDay(d), -((d.getDay() + 6) % 7)); // weeks start on Monday
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const sameDay = (a: Date, b: Date) => dayKey(a) === dayKey(b);

/** Scheduled meetings sit at their start; instant ones at when they began (or were created). */
function eventStart(m: Meeting): Date {
  const raw =
    m.scheduledStart ||
    m.startedAt ||
    (m as Meeting & { createdAt?: string }).createdAt;
  return raw ? new Date(raw) : new Date();
}

function eventClass(m: Meeting): string {
  const parts = [
    "flex items-center gap-[5px] w-full min-w-0 py-[2px] px-[5px] rounded-[4px] bg-accent-soft text-foreground text-[11px] text-left cursor-pointer [&:hover]:filter-[brightness(.96)]",
  ];
  if (m.isHost) parts.push("**:data-[fidelity-state~=mt-dot]:bg-[#2563eb]");
  if (m.status === "live")
    parts.push(
      "[&]:bg-[#16a34a22] **:data-[fidelity-state~=mt-dot]:bg-[#16a34a] **:data-[fidelity-state~=mt-dot]:shadow-[0_0_0_3px_#16a34a33]",
    );
  if (m.myResponse === "needsAction" && !m.isHost)
    parts.push("[&&]:bg-transparent shadow-[inset_0_0_0_1px_var(--line)]");
  if (m.myResponse === "tentative")
    parts.push(
      "[&&]:bg-[#d9770622] [&&_[data-fidelity-state~=mt-dot]]:bg-[#d97706]",
    );
  if (m.myResponse === "declined") parts.push("opacity-55 line-through");
  if (m.status === "ended")
    parts.push("opacity-70 [&&&_[data-fidelity-state~=mt-dot]]:bg-muted");
  return parts.join(" ");
}

function eventLabel(m: Meeting): string {
  const when = m.scheduledStart
    ? fmtTime(m.scheduledStart)
    : m.status === "live"
      ? "live now"
      : "instant";
  const state =
    m.status === "ended"
      ? `, ended${m.durationSeconds ? `, lasted ${fmtDuration(m.durationSeconds)}` : ""}`
      : m.status === "live"
        ? ", live"
        : m.isHost
          ? ", hosted by you"
          : m.myResponse && m.myResponse !== "accepted"
            ? `, ${m.myResponse === "needsAction" ? "no reply yet" : m.myResponse}`
            : "";
  return `${m.title}, ${when}${state}`;
}

export function Calendar({
  version,
  onOpen,
  onCreateOn,
}: {
  version: number;
  onOpen: (m: Meeting) => void;
  onCreateOn?: (day: Date) => void;
}) {
  const [view, setView] = useState<View>("month");
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Date | null>(() =>
    startOfDay(new Date()),
  );
  const [now, setNow] = useState(() => new Date());
  const scroller = useRef<HTMLDivElement>(null);

  const days = useMemo(() => {
    const first =
      view === "month"
        ? startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1))
        : startOfWeek(anchor);
    return Array.from({ length: view === "month" ? 42 : 7 }, (_, i) =>
      addDays(first, i),
    );
  }, [view, anchor]);
  const from = days[0];
  const to = addDays(days[days.length - 1], 1);

  useEffect(() => {
    let live = true;
    setLoading(true);
    meetingsApi
      .calendar(from, to)
      .then((rows) => {
        if (live) {
          setMeetings(rows.filter((m) => m.status !== "cancelled"));
          setError("");
        }
      })
      .catch((e) => {
        if (live)
          setError(
            e instanceof Error ? e.message : "Could not load the calendar.",
          );
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from.getTime(), to.getTime(), version]);

  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 60000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (view === "week" && scroller.current)
      scroller.current.scrollTop = Math.max(
        0,
        (new Date().getHours() - 1) * HOUR_PX,
      );
  }, [view]);

  const byDay = useMemo(() => {
    const map = new Map<string, Meeting[]>();
    for (const m of meetings) {
      const k = dayKey(eventStart(m));
      map.set(k, [...(map.get(k) || []), m]);
    }
    for (const list of map.values())
      list.sort((a, b) => eventStart(a).getTime() - eventStart(b).getTime());
    return map;
  }, [meetings]);

  const step = useCallback(
    (dir: -1 | 1) => {
      setAnchor((a) =>
        view === "month"
          ? new Date(a.getFullYear(), a.getMonth() + dir, 1)
          : addDays(a, 7 * dir),
      );
    },
    [view],
  );

  const title =
    view === "month"
      ? new Intl.DateTimeFormat(undefined, {
          month: "long",
          year: "numeric",
        }).format(anchor)
      : `${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(days[0])} – ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(days[6])}`;
  const weekdays = days
    .slice(0, 7)
    .map((d) =>
      new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(d),
    );
  const today = startOfDay(now);
  const agenda = selected ? byDay.get(dayKey(selected)) || [] : [];

  return (
    <section
      className="grid gap-3 min-w-0"
      data-fidelity-state="mt-cal"
      aria-label="Meetings calendar"
    >
      <header
        className="flex flex-wrap items-center gap-2.5"
        data-fidelity-state="mt-cal-head"
      >
        <div
          className="flex items-center gap-1"
          data-fidelity-state="mt-cal-nav"
        >
          <button
            type="button"
            className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-icon-btn"
            aria-label={view === "month" ? "Previous month" : "Previous week"}
            onClick={() => step(-1)}
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-icon-btn"
            aria-label={view === "month" ? "Next month" : "Next week"}
            onClick={() => step(1)}
          >
            <ChevronRight size={16} />
          </button>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-btn mt-btn-small"
            onClick={() => {
              setAnchor(startOfDay(new Date()));
              setSelected(startOfDay(new Date()));
            }}
          >
            Today
          </button>
        </div>
        <h2
          className="flex-1 min-w-35 m-0 text-[16px] tracking-[-.02em]"
          data-fidelity-state="mt-cal-title"
          aria-live="polite"
        >
          {title}
        </h2>
        <div
          className="inline-flex border border-solid border-line rounded-[7px] bg-subtle p-0.5"
          data-fidelity-state="mt-segment"
          role="group"
          aria-label="Calendar view"
        >
          <button
            type="button"
            aria-pressed={view === "month"}
            className="min-h-6.5 py-0.75 px-2.75 rounded-[5px] bg-transparent text-muted text-[12px] font-medium cursor-pointer data-[fidelity-state~=mt-on]:bg-panel data-[fidelity-state~=mt-on]:text-foreground data-[fidelity-state~=mt-on]:shadow-[0_1px_2px_#0002] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state={view === "month" ? "mt-on" : ""}
            onClick={() => setView("month")}
          >
            Month
          </button>
          <button
            type="button"
            aria-pressed={view === "week"}
            className="min-h-6.5 py-0.75 px-2.75 rounded-[5px] bg-transparent text-muted text-[12px] font-medium cursor-pointer data-[fidelity-state~=mt-on]:bg-panel data-[fidelity-state~=mt-on]:text-foreground data-[fidelity-state~=mt-on]:shadow-[0_1px_2px_#0002] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state={view === "week" ? "mt-on" : ""}
            onClick={() => setView("week")}
          >
            Week
          </button>
        </div>
      </header>
      {error && (
        <p
          className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
          data-fidelity-state="mt-field-error"
          role="alert"
        >
          {error}
        </p>
      )}

      {view === "month" ? (
        <>
          <div
            className="grid grid-cols-7 border border-solid border-line rounded-[9px] bg-panel overflow-hidden data-loading:opacity-70"
            data-fidelity-state="mt-month"
            data-loading={loading || undefined}
          >
            {weekdays.map((w) => (
              <div
                key={w}
                className="py-1.75 px-2 border-b border-b-line text-muted text-[11px] font-semibold uppercase tracking-[.06em] shell-phone:py-1.5 shell-phone:px-0.5 shell-phone:text-center shell-phone:text-[10px]"
                data-fidelity-state="mt-weekday"
              >
                {w}
              </div>
            ))}
            {days.map((d) => {
              const list = byDay.get(dayKey(d)) || [];
              const outside = d.getMonth() !== anchor.getMonth();
              const cls = [
                "flex flex-col gap-[3px] min-w-0 min-h-[104px] p-[4px] border-r border-r-line border-b border-b-line nth-[7n+14]:border-r-0 [&:hover_[data-fidelity-state~=mt-day-add]]:opacity-100 shell-phone:min-h-[50px] shell-phone:p-[3px]",
                outside ? "bg-subtle text-muted" : "",
                selected && sameDay(d, selected)
                  ? "shadow-[inset_0_0_0_2px_var(--accent)]"
                  : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <div
                  key={dayKey(d)}
                  data-mt-day=""
                  className={cls}
                  data-fidelity-state={`mt-day${sameDay(d, today) ? " mt-day-today" : ""}`}
                >
                  <div
                    className="flex items-center justify-between"
                    data-fidelity-state="mt-day-top"
                  >
                    <button
                      type="button"
                      className="grid place-items-center min-w-6 h-6 py-0 px-1 rounded-xl bg-transparent text-inherit text-[12px] font-medium cursor-pointer [&:hover]:bg-accent-soft in-data-[fidelity-state~=mt-day-today]:bg-accent in-data-[fidelity-state~=mt-day-today]:text-white [:root[data-theme=dark]_[data-fidelity-state~=mt-day-today]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-fidelity-state="mt-day-num"
                      aria-pressed={!!selected && sameDay(d, selected)}
                      aria-label={`${new Intl.DateTimeFormat(undefined, { dateStyle: "full" }).format(d)}, ${list.length} meeting${list.length === 1 ? "" : "s"}`}
                      onClick={() => setSelected(d)}
                    >
                      {d.getDate()}
                    </button>
                    {onCreateOn && (
                      <button
                        type="button"
                        className="grid place-items-center w-5 h-5 rounded-[5px] bg-transparent text-muted opacity-0 cursor-pointer focus-visible:opacity-100 [&:hover]:bg-accent-soft shell-phone:hidden shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                        data-fidelity-state="mt-day-add"
                        aria-label={`Schedule a meeting on ${new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(d)}`}
                        onClick={() => onCreateOn(d)}
                      >
                        <Plus size={12} />
                      </button>
                    )}
                  </div>
                  <ul
                    className="grid gap-0.5 m-0 p-0 list-none min-w-0 shell-phone:flex shell-phone:flex-wrap shell-phone:gap-0.75"
                    data-fidelity-state="mt-day-events"
                  >
                    {list.slice(0, MAX_CHIPS).map((m) => (
                      <li key={m.id}>
                        <button
                          type="button"
                          className={`${eventClass(m)} shell-phone:w-auto shell-phone:p-0 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11`}
                          data-fidelity-state="mt-event"
                          aria-label={eventLabel(m)}
                          title={m.title}
                          onClick={() => onOpen(m)}
                        >
                          <i
                            className="flex-none w-1.75 h-1.75 rounded-[50%] bg-accent shell-phone:w-2 shell-phone:h-2"
                            data-fidelity-state="mt-dot"
                            aria-hidden="true"
                          />
                          <span
                            className="overflow-hidden text-ellipsis whitespace-nowrap shell-phone:hidden"
                            data-fidelity-state="mt-event-text"
                          >
                            <time className="text-muted">
                              {m.scheduledStart
                                ? fmtTime(m.scheduledStart)
                                : ""}
                            </time>{" "}
                            {m.title}
                          </span>
                        </button>
                      </li>
                    ))}
                    {list.length > MAX_CHIPS && (
                      <li>
                        <button
                          type="button"
                          className="py-px px-1.25 bg-transparent text-muted text-[11px] cursor-pointer [&:hover]:text-foreground shell-phone:hidden shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                          data-fidelity-state="mt-more"
                          onClick={() => setSelected(d)}
                        >
                          +{list.length - MAX_CHIPS} more
                        </button>
                      </li>
                    )}
                  </ul>
                </div>
              );
            })}
          </div>
          <section
            className="py-3 px-3.5 border border-solid border-line rounded-[9px] bg-panel"
            data-fidelity-state="mt-agenda"
            aria-label="Selected day"
          >
            <h3 className="mt-0 mx-0 mb-2 text-[13px]">
              {selected
                ? new Intl.DateTimeFormat(undefined, {
                    weekday: "long",
                    month: "long",
                    day: "numeric",
                  }).format(selected)
                : "Pick a day"}
            </h3>
            {selected && agenda.length === 0 && (
              <p className="text-muted m-0" data-fidelity-state="mt-muted">
                Nothing scheduled.{onCreateOn ? " " : ""}
                {onCreateOn && (
                  <button
                    type="button"
                    className="p-0 bg-transparent text-accent font-medium underline cursor-pointer shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-fidelity-state="mt-link-btn"
                    onClick={() => onCreateOn(selected)}
                  >
                    Schedule a meeting
                  </button>
                )}
              </p>
            )}
            <ul className="grid gap-1 m-0 p-0 list-none">
              {agenda.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    className={`${eventClass(m)} [&]:py-1.75 [&]:px-2.25 [&]:text-[12px] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11`}
                    data-fidelity-state="mt-event mt-agenda-item"
                    aria-label={eventLabel(m)}
                    onClick={() => onOpen(m)}
                  >
                    <i
                      className="flex-none w-1.75 h-1.75 rounded-[50%] bg-accent"
                      data-fidelity-state="mt-dot"
                      aria-hidden="true"
                    />
                    <time className="flex-none min-w-14 text-muted">
                      {m.scheduledStart
                        ? fmtTime(m.scheduledStart)
                        : m.status === "live"
                          ? "Live"
                          : "Instant"}
                    </time>
                    <span
                      className="overflow-hidden text-ellipsis whitespace-nowrap"
                      data-fidelity-state="mt-event-title"
                    >
                      {m.title}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : (
        <div
          className="border border-solid border-line rounded-[9px] bg-panel overflow-hidden data-loading:opacity-70"
          data-fidelity-state="mt-week"
          data-loading={loading || undefined}
        >
          <div
            className="grid grid-cols-[44px_repeat(7,minmax(0,1fr))] border-b border-b-line shell-phone:grid-cols-[34px_repeat(7,minmax(0,1fr))]"
            data-fidelity-state="mt-week-head"
            data-mt-week-head=""
          >
            <div className="relative" data-fidelity-state="mt-gutter" />
            {days.map((d) => (
              <div
                key={dayKey(d)}
                className="grid justify-items-center gap-px py-1.5 px-0.5 border-l border-l-line"
                data-fidelity-state={`mt-week-day${sameDay(d, today) ? " mt-day-today" : ""}`}
              >
                <small className="text-muted text-[11px] uppercase">
                  {new Intl.DateTimeFormat(undefined, {
                    weekday: "short",
                  }).format(d)}
                </small>
                <b className="in-data-[fidelity-state~=mt-day-today]:grid in-data-[fidelity-state~=mt-day-today]:place-items-center in-data-[fidelity-state~=mt-day-today]:min-w-6 in-data-[fidelity-state~=mt-day-today]:h-6 in-data-[fidelity-state~=mt-day-today]:rounded-xl in-data-[fidelity-state~=mt-day-today]:bg-accent in-data-[fidelity-state~=mt-day-today]:text-white [:root[data-theme=dark]_[data-fidelity-state~=mt-day-today]_&]:bg-accent [:root[data-theme=dark]_[data-fidelity-state~=mt-day-today]_&]:text-[#171719]">
                  {d.getDate()}
                </b>
              </div>
            ))}
          </div>
          <div
            className="max-h-[min(620px,70vh)] overflow-auto"
            data-fidelity-state="mt-week-scroll"
            data-mt-week-scroll=""
            ref={scroller}
          >
            <div
              className="relative grid grid-cols-[44px_repeat(7,minmax(0,1fr))] shell-phone:grid-cols-[34px_repeat(7,minmax(0,1fr))]"
              data-fidelity-state="mt-week-grid"
              data-mt-week-grid=""
              style={{ height: HOUR_PX * 24 }}
            >
              <div className="relative" data-fidelity-state="mt-gutter">
                {Array.from({ length: 24 }, (_, h) => (
                  <span
                    className="absolute right-1.5 transform-[translateY(-50%)] text-muted text-[10px] whitespace-nowrap"
                    key={h}
                    style={{ top: h * HOUR_PX }}
                  >
                    {h === 0
                      ? ""
                      : new Intl.DateTimeFormat(undefined, {
                          hour: "numeric",
                        }).format(new Date(2000, 0, 1, h))}
                  </span>
                ))}
              </div>
              {days.map((d) => {
                const list = byDay.get(dayKey(d)) || [];
                const lanes: number[] = [];
                const placed = list.map((m) => {
                  const s = eventStart(m);
                  const startMin = s.getHours() * 60 + s.getMinutes();
                  const endMin =
                    startMin + Math.max(m.durationMinutes || 60, 25);
                  let lane = lanes.findIndex((end) => end <= startMin);
                  if (lane < 0) {
                    lane = lanes.length;
                    lanes.push(endMin);
                  } else lanes[lane] = endMin;
                  return { m, startMin, endMin, lane };
                });
                const count = Math.max(1, lanes.length);
                return (
                  <div
                    key={dayKey(d)}
                    className="relative border-l border-l-line"
                    data-fidelity-state={`mt-week-col${sameDay(d, today) ? " mt-day-today" : ""}`}
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <i
                        key={h}
                        className="absolute left-0 right-0 h-0 border-t border-t-line pointer-events-none"
                        data-fidelity-state="mt-hour-line"
                        style={{ top: h * HOUR_PX }}
                      />
                    ))}
                    {placed.map(({ m, startMin, endMin, lane }) => (
                      <button
                        key={m.id}
                        type="button"
                        className={`${eventClass(m)} absolute flex-col [&]:items-start [&]:gap-px [&]:py-0.75 [&]:px-1.25 border-l-[3px] border-l-accent [&]:rounded-sm overflow-hidden shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11`}
                        data-fidelity-state="mt-event mt-week-event"
                        aria-label={eventLabel(m)}
                        title={m.title}
                        style={{
                          top: (startMin / 60) * HOUR_PX,
                          height:
                            Math.min(
                              ((endMin - startMin) / 60) * HOUR_PX,
                              HOUR_PX * 24 - (startMin / 60) * HOUR_PX,
                            ) - 2,
                          left: `${(lane / count) * 100}%`,
                          width: `${100 / count}%`,
                        }}
                        onClick={() => onOpen(m)}
                      >
                        <span
                          className="overflow-hidden text-ellipsis whitespace-nowrap"
                          data-fidelity-state="mt-event-title"
                        >
                          {m.title}
                        </span>
                        <small className="text-muted text-[10px]">
                          {m.scheduledStart
                            ? fmtTime(m.scheduledStart)
                            : "Instant"}
                        </small>
                      </button>
                    ))}
                    {sameDay(d, today) && (
                      <i
                        className='absolute left-0 right-0 h-0.5 bg-danger pointer-events-none [&::before]:[content:""] [&::before]:absolute [&::before]:-left-1 [&::before]:-top-0.75 [&::before]:w-2 [&::before]:h-2 [&::before]:rounded-[50%] [&::before]:bg-danger'
                        data-fidelity-state="mt-now-line"
                        style={{
                          top:
                            ((now.getHours() * 60 + now.getMinutes()) / 60) *
                            HOUR_PX,
                        }}
                        aria-hidden="true"
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
