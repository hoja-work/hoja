/**
 * Board and Activity views for the Reviews page.
 *
 * Both read the same merged inbox items as the list, so a card means the same thing in every
 * view. Selecting an item opens it in the list with its detail pane.
 */
import type {
  ActivityEvent,
  BoardColumn,
  InboxItem,
  InboxSourceId,
} from "./inbox";

const SOURCE_LABEL: Record<InboxSourceId, string> = {
  "pull-request": "Pull request",
  "task-in-review": "Task",
  "github-review-request": "Review requests",
  "sentry-issue": "Sentry",
};

const TONE_DOT: Record<InboxItem["tone"], string> = {
  ready: "bg-green-500",
  open: "bg-green-500/60",
  draft: "bg-muted",
  attention: "bg-amber-500",
  info: "bg-accent",
};

export function ReviewsBoard({
  columns,
  selected,
  onSelect,
}: {
  columns: BoardColumn[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 gap-3 overflow-x-auto overscroll-x-contain p-4 max-sm:snap-x max-sm:snap-mandatory max-sm:scroll-px-4"
      data-reviews-board=""
    >
      {columns.map((col) => (
        <section
          key={col.bucket}
          className="flex w-72 flex-none min-h-0 flex-col gap-2 max-sm:w-4/5 max-sm:snap-start"
          aria-label={col.bucket}
        >
          <header className="flex items-center gap-2 px-1 text-[13px] font-medium">
            {col.bucket}
            <span className="rounded-full bg-subtle px-2 text-xs tabular-nums text-muted">
              {col.items.length}
            </span>
          </header>
          <div className="flex min-h-0 flex-col gap-2 overflow-y-auto">
            {col.items.length === 0 ? (
              <p className="m-0 px-1 text-sm text-muted">Nothing here.</p>
            ) : null}
            {col.items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                aria-pressed={selected === item.id}
                data-reviews-board-card=""
                className={`flex flex-col gap-2 rounded-lg border bg-panel p-3 text-left hover:bg-subtle ${selected === item.id ? "border-accent" : "border-line"}`}
              >
                <span className="flex min-w-0 items-start gap-2">
                  <span
                    aria-hidden="true"
                    className={`mt-1.5 size-2 flex-none rounded-full ${TONE_DOT[item.tone]}`}
                  />
                  <span className="min-w-0 text-[13px] font-medium leading-5">
                    {item.title}
                  </span>
                </span>
                <span className="text-xs text-muted">{item.reason}</span>
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-foreground">
                    {item.ref}
                  </span>
                  {item.sources.map((s) => (
                    <span
                      key={s}
                      className="rounded border border-line px-1.5 text-xs text-muted"
                    >
                      {SOURCE_LABEL[s]}
                    </span>
                  ))}
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function dayLabel(at: string | null): string {
  if (!at) return "No date";
  const d = new Date(at);
  const today = new Date();
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(d, today)) return "Today";
  if (sameDay(d, new Date(today.getTime() - 86_400_000))) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function ReviewsActivity({
  events,
  onSelect,
}: {
  events: ActivityEvent[];
  onSelect: (id: string) => void;
}) {
  if (events.length === 0)
    return <p className="m-0 p-4 text-sm text-muted">No activity yet.</p>;
  const groups: { label: string; items: ActivityEvent[] }[] = [];
  for (const e of events) {
    const label = dayLabel(e.at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(e);
    else groups.push({ label, items: [e] });
  }
  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-10"
      data-reviews-activity=""
    >
      <div className="mx-auto w-full max-w-3xl">
        {groups.map((g) => (
          <section key={g.label} className="flex flex-col">
            <h2 className="m-0 px-2 pb-1 pt-4 text-xs font-medium text-muted">
              {g.label}
            </h2>
            {g.items.map((e) => (
              <button
                key={e.id}
                type="button"
                onClick={() => onSelect(e.id)}
                data-reviews-activity-row=""
                className="flex items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-subtle pointer-coarse:min-h-11 max-sm:gap-2"
              >
                <span className="w-16 flex-none whitespace-nowrap text-xs tabular-nums text-muted max-sm:w-auto">
                  {e.at
                    ? new Date(e.at).toLocaleTimeString(undefined, {
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "—"}
                </span>
                <span
                  aria-hidden="true"
                  className={`size-2 flex-none rounded-full ${TONE_DOT[e.tone]}`}
                />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-[13px] max-sm:line-clamp-2 max-sm:whitespace-normal">
                    <span className="mr-1.5 font-semibold">{e.ref}</span>
                    {e.title}
                  </span>
                  <span className="truncate text-xs text-muted">
                    {e.reason} ·{" "}
                    {e.sources.map((s) => SOURCE_LABEL[s]).join(" + ")}
                  </span>
                </span>
              </button>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}
