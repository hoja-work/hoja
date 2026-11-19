/* The inbox's right-hand panel for a project, an initiative or a cycle (a task uses the task view).
 *
 * It answers "what happened and do I need to act": the subject at a glance (status, health,
 * lead or owner, target date, progress), the update or reply that triggered the notification
 * rendered in full, every event of this subject that is in the inbox, and the way out: open
 * the subject, or follow / unfollow it. */
import { CalendarDays, FolderKanban, RefreshCcw, Target } from "lucide-react";
import { Markdown } from "../markdown";
import { Avatar } from "../graph/shared";
import { FollowButton, type FollowType } from "./follow";
import {
  dataOf,
  describe,
  fullTime,
  healthLabel,
  isSystemEvent,
  shortTime,
  subjectLabel,
  subjectTitle,
  type InboxGroup,
} from "./model";

const ICON = { project: FolderKanban, initiative: Target, cycle: RefreshCcw };

function day(iso?: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  return Number.isFinite(t)
    ? new Date(t).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: iso.length <= 10 ? "UTC" : undefined,
      })
    : "";
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="m-0 truncate text-sm">{value}</dd>
    </div>
  );
}

export function SubjectPanel({
  group,
  onOpen,
  onError,
}: {
  group: InboxGroup;
  onOpen: () => void;
  onError: (message: string) => void;
}) {
  const type = group.type as Exclude<FollowType, "issue">;
  const Icon = ICON[type];
  const latest = group.latest;
  const data = dataOf(latest);
  const update = latest.projectUpdate || latest.initiativeUpdate;
  const reply = latest.comment;
  const p = group.project,
    i = group.initiative,
    c = group.cycle;
  const health = update?.health || data?.health || p?.health;
  const progress =
    type === "project" ? p?.progress : type === "cycle" ? c?.progress : null;
  const openLabel =
    type === "project"
      ? "Open project"
      : type === "initiative"
        ? "Open initiatives"
        : "Open cycle";

  return (
    <article
      className="flex flex-col gap-5 p-4 md:p-6"
      data-inbox-subject={type}
    >
      <header className="flex flex-col gap-3">
        <div className="flex items-center gap-2 text-xs text-muted">
          <Icon size={14} />
          <span>{subjectLabel(type)}</span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="m-0 min-w-0 flex-1 text-lg font-semibold">
            {subjectTitle(group)}
          </h2>
          <div className="flex items-center gap-2">
            <FollowButton type={type} id={group.key} onError={onError} />
            <button
              type="button"
              className="rounded-md border border-line bg-panel px-2.5 py-1 text-xs hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
              onClick={onOpen}
            >
              {openLabel}
            </button>
          </div>
        </div>
        {type === "initiative" && i?.summary && (
          <p className="m-0 text-sm text-muted">{i.summary}</p>
        )}
        <dl className="m-0 grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-line p-4 sm:grid-cols-4">
          <Fact
            label="Status"
            value={
              type === "project"
                ? p?.state
                : type === "initiative"
                  ? i?.status
                  : c?.completedAt
                    ? "Completed"
                    : "Active"
            }
          />
          {type !== "cycle" && (
            <Fact label="Health" value={healthLabel(health)} />
          )}
          {type === "project" && <Fact label="Lead" value={p?.lead?.name} />}
          {type === "initiative" && (
            <Fact label="Owner" value={i?.owner?.name} />
          )}
          {type === "cycle" && (
            <Fact
              label="Dates"
              value={`${day(c?.startsAt)} – ${day(c?.endsAt)}`}
            />
          )}
          {type !== "cycle" && (
            <Fact
              label="Target"
              value={day(type === "project" ? p?.targetDate : i?.targetDate)}
            />
          )}
          {typeof progress === "number" && (
            <Fact
              label="Progress"
              value={`${Math.round(progress > 1 ? progress : progress * 100)}%`}
            />
          )}
          {type === "cycle" && typeof data?.total === "number" && (
            <Fact
              label="Done"
              value={`${data.completed ?? 0} of ${data.total}${data.carriedOver ? ` · ${data.carriedOver} carried over` : ""}`}
            />
          )}
        </dl>
      </header>

      {(update || reply) && (
        <section
          className="flex flex-col gap-3 rounded-lg border border-line bg-panel p-4"
          aria-label={reply ? "Reply" : "Update"}
        >
          <div className="flex items-center gap-2 text-xs text-muted">
            <Avatar name={latest.actor?.name || null} size={20} />
            <span className="text-foreground">
              {latest.actor?.name || "Someone"}
            </span>
            <span>{describe(latest)}</span>
            <time
              className="ml-auto"
              dateTime={latest.createdAt}
              title={fullTime(latest.createdAt)}
            >
              {shortTime(latest.createdAt)}
            </time>
          </div>
          <Markdown text={reply?.body || update?.body || ""} density="block" />
          {reply && update && (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">
                The update this replies to
              </summary>
              <div className="mt-2 border-l-2 border-line pl-3 text-sm text-foreground">
                <Markdown text={update.body} density="block" />
              </div>
            </details>
          )}
        </section>
      )}

      <section className="flex flex-col gap-2" aria-label="In your inbox">
        <h3 className="m-0 text-xs font-semibold text-muted">
          In your inbox · {group.items.length}
        </h3>
        <ol className="m-0 flex list-none flex-col gap-1 p-0">
          {[...group.items]
            .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
            .map((n) => (
              <li key={n.id} className="flex items-center gap-2 text-sm">
                <span
                  aria-hidden="true"
                  className={`size-1.5 flex-none rounded-full ${n.readAt ? "bg-transparent" : "bg-accent"}`}
                />
                {isSystemEvent(n) ? (
                  <CalendarDays size={14} className="flex-none text-muted" />
                ) : (
                  <Avatar name={n.actor?.name || null} size={18} />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {!isSystemEvent(n) && (
                    <span className="text-foreground">
                      {n.actor?.name || "Someone"}{" "}
                    </span>
                  )}
                  <span className="text-muted">{describe(n)}</span>
                </span>
                <time
                  className="flex-none text-xs text-muted"
                  dateTime={n.createdAt}
                  title={fullTime(n.createdAt)}
                >
                  {shortTime(n.createdAt)}
                </time>
              </li>
            ))}
        </ol>
      </section>
    </article>
  );
}
