import React, { useEffect, useRef, useState } from "react";
import type { IssueLabel } from "../main";
import { blobatarUri } from "blobatar/uri";
import { statusAgeLabel, type StatusAgeIssue } from "./statusAge";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "../components/ui/popover";

/* Shared carry-over-style primitives for the 1:1 Issues/Cycles rebuild. */

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function initials(name?: string | null): string {
  if (!name) return "?";
  const words = name.trim().split(/\s+/);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** Deterministic pastel avatar background from a name. */
export function avatarColor(name?: string | null): string {
  const palette = [
    "#e5a8c9",
    "#c9a8e5",
    "#a8c9e5",
    "#a8e5c9",
    "#e5d3a8",
    "#e5a8a8",
    "#a8d3e5",
    "#d3a8e5",
  ];
  let h = 0;
  for (const c of name || "?") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}

/* A blobatar is the picture for anybody without an uploaded one. Users are drawn
 * from their stored random seed; anything else (a project, an agent, a GitHub
 * login) from its name, which is deterministic and needs no storage. */
const blobatarUris = new Map<string, string>();
export function blobatarSrc(source: string): string {
  let uri = blobatarUris.get(source);
  if (uri === undefined) {
    uri = blobatarUri(source);
    blobatarUris.set(source, uri);
  }
  return uri;
}

export function Avatar({
  name,
  seed,
  size = 20,
  title,
  className,
  "data-style-hooks": inheritedHooks,
}: {
  name?: string | null;
  seed?: string | null;
  size?: number;
  title?: string;
  className?: string;
  "data-style-hooks"?: string;
}) {
  const source = seed || name || null;
  return (
    <span
      className={styleClasses(
        cx(
          "rounded-[50%] grid place-items-center font-bold flex-none text-[#5b4a52] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:absolute [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:top-2 [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:right-2.5 in-data-[style-hooks~='lin-sublist']:-ml-1 in-data-[style-hooks~='lin-sublist']:border-2 in-data-[style-hooks~='lin-sublist']:border-solid in-data-[style-hooks~='lin-sublist']:border-panel [[data-style-hooks~='lin-sublist']_&:first-child]:ml-0",
          className,
        ),
      )}
      data-style-hooks={cx(
        inheritedHooks,
        styleHooks(
          cx(
            "lin-avatar rounded-[50%] grid place-items-center font-bold flex-none text-[#5b4a52] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:absolute [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:top-[8px] [[data-style-hooks~='lin-bcard']_[data-style-hooks~='top']_&]:right-[10px] in-data-[style-hooks~='lin-sublist']:ml-[-4px] in-data-[style-hooks~='lin-sublist']:border-2 in-data-[style-hooks~='lin-sublist']:border-solid in-data-[style-hooks~='lin-sublist']:border-panel [[data-style-hooks~='lin-sublist']_&:first-child]:ml-0",
            className,
          ),
        ),
      )}
      title={title || name || "Unassigned"}
      style={{
        width: size,
        height: size,
        fontSize: Math.max(8, size * 0.42),
        background: "var(--subtle)",
        color: "var(--muted)",
        overflow: "hidden",
      }}
    >
      {source ? (
        <img
          src={blobatarSrc(source)}
          alt=""
          width={size}
          height={size}
          draggable={false}
          className="block size-full"
        />
      ) : (
        "?"
      )}
    </span>
  );
}

/** carry-over-style priority icon. 0 none, 1 urgent, 2 high, 3 medium, 4 low. */
export function PriorityIcon({
  priority,
  size = 14,
}: {
  priority?: number | null;
  size?: number;
}) {
  const p = priority || 0;
  if (p === 1) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        aria-label="Urgent"
        className={styleClasses("")}
        data-style-hooks={styleHooks("lin-priority")}
      >
        <rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="#f2994a" />
        <path
          d="M8 4.2v4.4"
          stroke="#fff"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
        <circle cx="8" cy="11.4" r="1.05" fill="#fff" />
      </svg>
    );
  }
  const color =
    p === 2 ? "#f2994a" : p === 3 ? "#e5b800" : p === 4 ? "#8a94a6" : "#c4c4c9";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-label={
        ["No priority", "Urgent", "High", "Medium", "Low"][p] || "No priority"
      }
      className={styleClasses("")}
      data-style-hooks={styleHooks("lin-priority")}
    >
      <rect
        x="2"
        y="9.5"
        width="2.6"
        height="4.5"
        rx="1"
        fill={color}
        opacity={p === 0 ? 0.45 : 1}
      />
      <rect
        x="6.7"
        y="6"
        width="2.6"
        height="8"
        rx="1"
        fill={color}
        opacity={p === 0 ? 0.45 : p >= 3 ? 1 : 0.35}
      />
      <rect
        x="11.4"
        y="2.5"
        width="2.6"
        height="11.5"
        rx="1"
        fill={color}
        opacity={p === 0 ? 0.45 : p >= 2 && p !== 3 ? 1 : p === 3 ? 0.35 : 0.35}
      />
    </svg>
  );
}

/** carry-over-style workflow state icon (14px). */
export function GraphStatusIcon({
  state,
  size = 14,
}: {
  state?: { name?: string; type?: string; color?: string } | null;
  size?: number;
}) {
  const t = (state?.type || "").toLowerCase();
  const n = (state?.name || "").toLowerCase();
  const c = state?.color || "#8a94a6";
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    "data-style-hooks": "lin-status-icon",
    "aria-hidden": true,
  } as const;
  if (n.includes("duplicate"))
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" />
        <path d="M4.5 11.5l7-7" stroke={c} strokeWidth="1.5" />
      </svg>
    );
  if (t === "completed")
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6.5" fill="#5e6ad2" />
        <path
          d="M5 8.3l2.1 2.1L11.2 6"
          stroke="#fff"
          strokeWidth="1.7"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  if (t === "canceled")
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6.5" fill="#8a94a6" opacity="0.55" />
        <path
          d="M6 6l4 4M10 6l-4 4"
          stroke="#fff"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    );
  if (t === "backlog")
    return (
      <svg {...common}>
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="#8a94a6"
          strokeWidth="1.5"
          strokeDasharray="2.4 2.2"
        />
      </svg>
    );
  if (n.includes("review"))
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" />
        <path d="M8 8V2A6 6 0 0 1 8 14Z" fill={c} />
      </svg>
    );
  if (t === "started")
    return (
      <svg {...common}>
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="#e5b800"
          strokeWidth="1.5"
        />
        <path d="M8 2a6 6 0 0 1 0 12Z" fill="#e5b800" />
      </svg>
    );
  if (t === "unstarted")
    return (
      <svg {...common}>
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="#8a94a6"
          strokeWidth="1.5"
        />
      </svg>
    );
  return (
    <svg {...common}>
      <circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" />
    </svg>
  );
}

/** A status symbol that explains how long the issue has held that status on hover or
 *  keyboard focus. Rows use this instead of a bare GraphStatusIcon so every list
 *  (Team issues, team views, cycle issues) answers the question with the same
 *  stateChangedAt-based math as the My issues picker. */
export function StatusAgeIcon({
  issue,
  size = 14,
}: {
  issue: StatusAgeIssue;
  size?: number;
}) {
  const label = statusAgeLabel(issue);
  return (
    <span
      className={styleClasses(
        "relative inline-grid place-items-center flex-none rounded-sm focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 [[data-style-hooks~='lin-row']:has(&:hover)]:z-2 [[data-style-hooks~='lin-row']:has(&:focus-visible)]:z-2",
      )}
      data-style-hooks={styleHooks(
        "lin-status-age relative inline-grid place-items-center flex-none rounded-[4px] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 [[data-style-hooks~='lin-row']:has(&:hover)]:z-2 [[data-style-hooks~='lin-row']:has(&:focus-visible)]:z-2",
      )}
      tabIndex={0}
      role="img"
      aria-label={`Status: ${issue.state?.name || "No status"}. ${label}`}
    >
      <GraphStatusIcon state={issue.state} size={size} />
      <span
        className={styleClasses(
          "hidden absolute top-[calc(100%+7px)] left-0 z-120 whitespace-nowrap bg-[#1b1b1f] text-white text-[11px] font-medium py-1.25 px-2.25 rounded-md shadow-[0_4px_14px_#0003] pointer-events-none [[data-style-hooks~='lin-status-age']:hover_&]:block [[data-style-hooks~='lin-status-age']:focus-visible_&]:block [[data-style-hooks~='lin-row']:last-child_&]:top-auto [[data-style-hooks~='lin-row']:last-child_&]:bottom-[calc(100%+7px)]",
        )}
        data-style-hooks={styleHooks(
          "lin-status-age-tip hidden absolute top-[calc(100%+7px)] left-0 z-120 whitespace-nowrap bg-[#1b1b1f] text-white text-[11px] font-medium py-[5px] px-[9px] rounded-[6px] shadow-[0_4px_14px_#0003] pointer-events-none [[data-style-hooks~='lin-status-age']:hover_&]:block [[data-style-hooks~='lin-status-age']:focus-visible_&]:block [[data-style-hooks~='lin-row']:last-child_&]:top-auto [[data-style-hooks~='lin-row']:last-child_&]:bottom-[calc(100%+7px)]",
        )}
        role="tooltip"
      >
        {label}
      </span>
    </span>
  );
}

/** Cycle icon used in chips and the cycles timeline. */
export function CycleGlyph({
  size = 13,
  tone = "currentColor",
}: {
  size?: number;
  tone?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke={tone}
      strokeWidth="1.6"
      aria-hidden="true"
      className={styleClasses("")}
      data-style-hooks={styleHooks("lin-cycle-glyph")}
    >
      <circle cx="8" cy="8" r="5.6" strokeDasharray="3 2.4" />
      <path d="M8 4.6V8l2.4 1.6" strokeLinecap="round" />
    </svg>
  );
}

export function LabelChip({
  label,
  onClick,
  onRemove,
}: {
  label: IssueLabel;
  onClick?: (e: React.MouseEvent) => void;
  onRemove?: () => void;
}) {
  return (
    // Interactive only when onClick is passed; the role and key handler below apply in that case.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <span
      className={styleClasses(
        "inline-flex items-center gap-1 h-6 text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-2 text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-2.25 [&_i]:h-2.25 [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent)",
      )}
      data-style-hooks={styleHooks(
        "lin-chip inline-flex items-center gap-[4px] h-[24px] text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-[8px] text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-[9px] [&_i]:h-[9px] [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent) lin-label-chip",
      )}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter") onClick(e as unknown as React.MouseEvent);
            }
          : undefined
      }
    >
      <i style={{ background: label.color || "#8a94a6" }} />
      {label.name}
      {onRemove && (
        <button
          type="button"
          className={styleClasses(
            "in-data-[style-hooks~='lin-chip']:[background:none] in-data-[style-hooks~='lin-chip']:border-0 in-data-[style-hooks~='lin-chip']:text-muted in-data-[style-hooks~='lin-chip']:text-[13px] in-data-[style-hooks~='lin-chip']:pt-0 in-data-[style-hooks~='lin-chip']:pr-0 in-data-[style-hooks~='lin-chip']:pb-0 in-data-[style-hooks~='lin-chip']:pl-0.5 in-data-[style-hooks~='lin-chip']:leading-none [[data-style-hooks~='lin-chip']_&:hover]:text-danger",
          )}
          data-style-hooks={styleHooks(
            "lin-chip-x in-data-[style-hooks~='lin-chip']:[background:none] in-data-[style-hooks~='lin-chip']:border-0 in-data-[style-hooks~='lin-chip']:text-muted in-data-[style-hooks~='lin-chip']:text-[13px] in-data-[style-hooks~='lin-chip']:pt-0 in-data-[style-hooks~='lin-chip']:pr-0 in-data-[style-hooks~='lin-chip']:pb-0 in-data-[style-hooks~='lin-chip']:pl-[2px] in-data-[style-hooks~='lin-chip']:leading-none [[data-style-hooks~='lin-chip']_&:hover]:text-danger",
          )}
          aria-label={`Remove label ${label.name}`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          ×
        </button>
      )}
    </span>
  );
}

export function ProjectChip({ name }: { name?: string | null }) {
  if (!name) return null;
  return (
    <span
      className={styleClasses(
        "inline-flex items-center gap-1 h-6 text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-2 text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-2.25 [&_i]:h-2.25 [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent)",
      )}
      data-style-hooks={styleHooks(
        "lin-chip inline-flex items-center gap-[4px] h-[24px] text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-[8px] text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-[9px] [&_i]:h-[9px] [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent) lin-project-chip",
      )}
      title={`Project: ${name}`}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <path
          d="M8 1.8l5.6 3.2v6.4L8 14.6l-5.6-3.2V5L8 1.8Z"
          strokeLinejoin="round"
        />
        <path d="M8 1.8V8m0 0l5.6-3M8 8L2.4 5" />
      </svg>
      {name}
    </span>
  );
}

export function CycleChip({
  number,
  name,
}: {
  number?: number | null;
  name?: string | null;
}) {
  if (number == null && !name) return null;
  return (
    <span
      className={styleClasses(
        "inline-flex items-center gap-1 h-6 text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-2 text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-2.25 [&_i]:h-2.25 [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent)",
      )}
      data-style-hooks={styleHooks(
        "lin-chip inline-flex items-center gap-[4px] h-[24px] text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-[8px] text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-[9px] [&_i]:h-[9px] [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent) lin-cycle-chip",
      )}
      title={name || `Cycle ${number}`}
    >
      <CycleGlyph size={12} />
      {number ?? name}
    </span>
  );
}

/** "2/4" sub-issue progress pill. */
export function SubIssuePill({ done, total }: { done: number; total: number }) {
  if (!total) return null;
  const frac = done / total;
  const r = 5,
    circ = 2 * Math.PI * r;
  return (
    <span
      className={styleClasses(
        "inline-flex items-center gap-1.25 text-[12px] text-muted border border-solid border-line rounded-[99px] py-0.5 px-2 bg-panel whitespace-nowrap",
      )}
      data-style-hooks={styleHooks(
        "lin-subissue-pill inline-flex items-center gap-[5px] text-[12px] text-muted border border-solid border-line rounded-[99px] py-[2px] px-[8px] bg-panel whitespace-nowrap",
      )}
      title={`${done} of ${total} sub-tasks complete`}
    >
      <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
        <circle
          cx="7"
          cy="7"
          r={r}
          fill="none"
          stroke="#d4d4d8"
          strokeWidth="1.6"
        />
        <circle
          cx="7"
          cy="7"
          r={r}
          fill="none"
          stroke="#5e6ad2"
          strokeWidth="1.6"
          strokeDasharray={`${circ * frac} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 7 7)"
        />
      </svg>
      {done}/{total}
    </span>
  );
}

/* Due dates arrive as date-only strings ("YYYY-MM-DD") or full ISO timestamps.
 * A date-only value is a calendar day, so it must not be parsed as UTC midnight:
 * that renders the previous day west of Greenwich. */
export function parseDueDay(iso?: string | null): Date | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** True once the due day is before today (the due day itself is not late). */
export function dueIsOverdue(iso?: string | null): boolean {
  const d = parseDueDay(iso);
  if (!d) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  d.setHours(0, 0, 0, 0);
  return d.getTime() < today.getTime();
}

export function formatDueDay(iso?: string | null): string {
  const d = parseDueDay(iso);
  return d
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : "";
}

/** Due-date chip; red when overdue. */
export function DueChip({ iso }: { iso?: string | null }) {
  const d = parseDueDay(iso);
  if (!d) return null;
  const overdue = dueIsOverdue(iso);
  const label = formatDueDay(iso);
  return (
    <span
      className={styleClasses(
        cx(
          "inline-flex items-center gap-1 h-6 text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-2 text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-2.25 [&_i]:h-2.25 [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent)",
          overdue &&
            "data-[style-hooks~='lin-due-chip']:text-[#d92d20] data-[style-hooks~='lin-due-chip']:border-[#f1b0aa] data-[style-hooks~='lin-due-chip']:bg-[#fef3f2] data-[style-hooks~='lin-card-due']:text-danger data-[style-hooks~='lin-card-due']:border-[color-mix(in_srgb,var(--danger)_45%,var(--line))]",
        ),
      )}
      data-style-hooks={styleHooks(
        cx(
          "lin-chip inline-flex items-center gap-[4px] h-[24px] text-[12px] font-[450] border border-solid border-(--card-line) rounded-[48px] py-0 px-[8px] text-(--card-meta) bg-panel whitespace-nowrap [&_i]:w-[9px] [&_i]:h-[9px] [&_i]:rounded-[50%] [&_i]:flex-none [&_i]:not-italic [[data-style-hooks~='lin-bcard']_&_i]:bg-(--card-accent) lin-due-chip",
          overdue &&
            "overdue data-[style-hooks~='lin-due-chip']:text-[#d92d20] data-[style-hooks~='lin-due-chip']:border-[#f1b0aa] data-[style-hooks~='lin-due-chip']:bg-[#fef3f2] data-[style-hooks~='lin-card-due']:text-danger data-[style-hooks~='lin-card-due']:border-[color-mix(in_srgb,var(--danger)_45%,var(--line))]",
        ),
      )}
      title={`Due ${d.toLocaleDateString()}`}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <rect x="2" y="3" width="12" height="11" rx="2" />
        <path d="M2 6.5h12M5.5 1.5V4M10.5 1.5V4" strokeLinecap="round" />
      </svg>
      {label}
    </span>
  );
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function fmtFullDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function fmtRange(start?: string | null, end?: string | null): string {
  const a = fmtDate(start),
    b = fmtDate(end);
  if (a && b) return `${a} → ${b}`;
  return a || b || "";
}

/** Inline primitive adapter preserving inherited style hooks and existing triggers.
 * Virtual anchoring lets callers keep their existing controlled trigger buttons.
 */
export function LinPopover({
  onClose,
  className,
  style,
  children,
  ...props
}: React.ComponentProps<"div"> & { onClose: () => void }) {
  const marker = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hooks =
    (props as { "data-style-hooks"?: string })["data-style-hooks"] || "";
  const upward = Boolean(style?.bottom) || hooks.split(/\s+/).includes("up");
  const right =
    Boolean(style?.right !== undefined) || hooks.includes("lin-dd-right");
  const anchor = React.useMemo(
    () => ({
      getBoundingClientRect: () =>
        trigger.current?.getBoundingClientRect() ||
        marker.current?.parentElement?.getBoundingClientRect() ||
        new DOMRect(),
    }),
    [],
  );
  // Radix owns positioning; retain all visual and ancestor-dependent classes.
  const positionedClass = className
    ?.split(/\s+/)
    .filter(
      (part) =>
        !/^(absolute|fixed|(?:top|bottom|left|right)-)/.test(part) &&
        !/:(?:top|bottom|left|right)-/.test(part),
    )
    .join(" ");
  const {
    top,
    bottom,
    left,
    right: _right,
    position,
    ...visualStyle
  } = style || {};
  return (
    <Popover
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <span
        ref={(node) => {
          marker.current = node;
          if (!node || trigger.current) return;
          // Resolve before the content focus scope runs. Anchor to the adjacent
          // trigger rather than a potentially full-width toolbar/list container.
          const sibling = node.previousElementSibling;
          const candidate = sibling?.matches("button, [role=button]")
            ? sibling
            : sibling?.querySelector<HTMLElement>("button, [role=button]");
          const active = document.activeElement;
          trigger.current =
            active instanceof HTMLElement &&
            active !== document.body &&
            node.parentElement?.contains(active)
              ? active
              : candidate instanceof HTMLElement
                ? candidate
                : null;
        }}
        hidden
      />
      <PopoverAnchor virtualRef={{ current: anchor }} />
      <PopoverContent
        {...props}
        portal={false}
        align={right ? "end" : "start"}
        side={upward ? "top" : "bottom"}
        sideOffset={6}
        className={cx("w-auto p-0 text-foreground", positionedClass)}
        style={visualStyle}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (trigger.current?.isConnected) trigger.current.focus();
        }}
        onInteractOutside={(event) => {
          // A trigger click is handled by its existing toggle, not twice.
          if (
            event.target instanceof Node &&
            trigger.current?.contains(event.target)
          )
            event.preventDefault();
        }}
        onKeyDown={(event) => {
          props.onKeyDown?.(event);
          if (
            event.defaultPrevented ||
            !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) ||
            (event.target instanceof HTMLElement &&
              event.target.matches("input, textarea, select"))
          )
            return;
          const items = Array.from(
            event.currentTarget.querySelectorAll<HTMLElement>(
              "button:not(:disabled), [role='menuitem']:not([aria-disabled='true']), [role='option']:not([aria-disabled='true'])",
            ),
          );
          if (!items.length) return;
          event.preventDefault();
          const index = items.indexOf(document.activeElement as HTMLElement);
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? items.length - 1
                : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) %
                  items.length;
          items[next].focus();
        }}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}

export type MenuItem =
  | {
      key: string;
      label: React.ReactNode;
      icon?: React.ReactNode;
      shortcut?: string;
      danger?: boolean;
      disabled?: boolean;
      divider?: never;
      onSelect?: () => void;
    }
  | { key: string; divider: true; label?: never };

/** carry-over-style dropdown menu. */
export function LinMenu({
  items,
  onClose,
  className,
  "data-style-hooks": inheritedHooks,
}: {
  items: MenuItem[];
  onClose: () => void;
  className?: string;
  "data-style-hooks"?: string;
}) {
  return (
    <LinPopover
      onClose={onClose}
      className={styleClasses(
        cx(
          "absolute z-130 min-w-55 max-w-[320px] p-1.25 border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
          className,
        ),
      )}
      data-style-hooks={cx(
        inheritedHooks,
        styleHooks(
          cx(
            "lin-menu absolute z-130 min-w-[220px] max-w-[320px] p-[5px] border border-solid border-line rounded-[9px] bg-panel shadow-[0_12px_40px_#1b1b1b1f]",
            className,
          ),
        ),
      )}
      role="menu"
    >
      {items.map((it) =>
        it.divider ? (
          <div
            key={it.key}
            className={styleClasses("h-px bg-line my-1.25 mx-1")}
            data-style-hooks={styleHooks(
              "lin-menu-divider h-px bg-line my-[5px] mx-[4px]",
            )}
            role="separator"
          />
        ) : (
          <button
            key={it.key}
            type="button"
            role="menuitem"
            className={styleClasses(
              cx(
                "flex items-center gap-2.25 w-full py-1.75 px-2.25 rounded-md bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle disabled:opacity-45 disabled:cursor-default",
                it.danger && "data-[style-hooks~='lin-menu-item']:text-danger",
              ),
            )}
            data-style-hooks={styleHooks(
              cx(
                "lin-menu-item flex items-center gap-[9px] w-full py-[7px] px-[9px] rounded-[6px] bg-transparent text-[13px] text-foreground text-left [&:hover]:bg-subtle disabled:opacity-45 disabled:cursor-default",
                it.danger &&
                  "danger data-[style-hooks~='lin-menu-item']:text-danger",
              ),
            )}
            disabled={it.disabled}
            onClick={() => {
              onClose();
              it.onSelect?.();
            }}
          >
            {it.icon && (
              <span
                className={styleClasses(
                  "grid place-items-center text-muted flex-none",
                )}
                data-style-hooks={styleHooks(
                  "lin-menu-icon grid place-items-center text-muted flex-none",
                )}
              >
                {it.icon}
              </span>
            )}
            <span
              className={styleClasses(
                "flex-1 overflow-hidden text-ellipsis whitespace-nowrap",
              )}
              data-style-hooks={styleHooks(
                "lin-menu-label flex-1 overflow-hidden text-ellipsis whitespace-nowrap",
              )}
            >
              {it.label}
            </span>
            {it.shortcut && (
              <kbd
                className={styleClasses(
                  "font-[inherit] text-[10px] text-muted border border-solid border-line rounded-sm py-px px-1.25 bg-subtle ml-auto",
                )}
                data-style-hooks={styleHooks(
                  "lin-kbd font-[inherit] text-[10px] text-muted border border-solid border-line rounded-[4px] py-px px-[5px] bg-subtle ml-auto",
                )}
              >
                {it.shortcut}
              </kbd>
            )}
          </button>
        ),
      )}
    </LinPopover>
  );
}

/** Pointer-hover tooltip with colocated Tailwind styling. */
export function Tip({
  label,
  children,
  className,
  "data-style-hooks": inheritedHooks,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
  "data-style-hooks"?: string;
}) {
  return (
    <span
      className={styleClasses(
        cx(
          "relative inline-grid place-items-center [&[data-tip]:hover::after]:content-[attr(data-tip)] [&[data-tip]:hover::after]:absolute [&[data-tip]:hover::after]:top-[calc(100%+7px)] [&[data-tip]:hover::after]:left-[50%] [&[data-tip]:hover::after]:transform-[translateX(-50%)] [&[data-tip]:hover::after]:z-120 [&[data-tip]:hover::after]:whitespace-nowrap [&[data-tip]:hover::after]:bg-[#1b1b1f] [&[data-tip]:hover::after]:text-white [&[data-tip]:hover::after]:text-[11px] [&[data-tip]:hover::after]:font-medium [&[data-tip]:hover::after]:py-1.25 [&[data-tip]:hover::after]:px-2.25 [&[data-tip]:hover::after]:rounded-md [&[data-tip]:hover::after]:pointer-events-none [&[data-tip]:hover::after]:shadow-[0_4px_14px_#0003] [&[data-tip]:hover::before]:content-[''] [&[data-tip]:hover::before]:absolute [&[data-tip]:hover::before]:top-[calc(100%+2px)] [&[data-tip]:hover::before]:left-[50%] [&[data-tip]:hover::before]:transform-[translateX(-50%)] [&[data-tip]:hover::before]:border-[5px] [&[data-tip]:hover::before]:border-solid [&[data-tip]:hover::before]:border-transparent [&[data-tip]:hover::before]:border-b-[#1b1b1f] [&[data-tip]:hover::before]:z-120 [&[data-tip]:hover::before]:pointer-events-none",
          className,
        ),
      )}
      data-style-hooks={cx(
        inheritedHooks,
        styleHooks(
          cx(
            "lin-tip relative inline-grid place-items-center [&[data-tip]:hover::after]:content-[attr(data-tip)] [&[data-tip]:hover::after]:absolute [&[data-tip]:hover::after]:top-[calc(100%+7px)] [&[data-tip]:hover::after]:left-[50%] [&[data-tip]:hover::after]:transform-[translateX(-50%)] [&[data-tip]:hover::after]:z-120 [&[data-tip]:hover::after]:whitespace-nowrap [&[data-tip]:hover::after]:bg-[#1b1b1f] [&[data-tip]:hover::after]:text-white [&[data-tip]:hover::after]:text-[11px] [&[data-tip]:hover::after]:font-medium [&[data-tip]:hover::after]:py-[5px] [&[data-tip]:hover::after]:px-[9px] [&[data-tip]:hover::after]:rounded-[6px] [&[data-tip]:hover::after]:pointer-events-none [&[data-tip]:hover::after]:shadow-[0_4px_14px_#0003] [&[data-tip]:hover::before]:content-[''] [&[data-tip]:hover::before]:absolute [&[data-tip]:hover::before]:top-[calc(100%+2px)] [&[data-tip]:hover::before]:left-[50%] [&[data-tip]:hover::before]:transform-[translateX(-50%)] [&[data-tip]:hover::before]:border-[5px] [&[data-tip]:hover::before]:border-solid [&[data-tip]:hover::before]:border-transparent [&[data-tip]:hover::before]:border-b-[#1b1b1f] [&[data-tip]:hover::before]:z-120 [&[data-tip]:hover::before]:pointer-events-none",
            className,
          ),
        ),
      )}
      data-tip={label}
    >
      {children}
    </span>
  );
}

/* ---------- favorites (browser-local, ALT+F) ---------- */
const FAV_KEY = "hoja_favorites_v1";
type FavStore = { issues: string[]; cycles: string[] };
function readFavs(): FavStore {
  try {
    const raw = localStorage.getItem(FAV_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      return { issues: p.issues || [], cycles: p.cycles || [] };
    }
  } catch {
    /* ignore */
  }
  return { issues: [], cycles: [] };
}
export function useFavorite(
  kind: "issues" | "cycles",
  id: string,
): [boolean, () => void] {
  const [favs, setFavs] = useState<FavStore>(readFavs);
  const toggle = () => {
    setFavs((prev) => {
      const next = {
        ...prev,
        [kind]: prev[kind].includes(id)
          ? prev[kind].filter((x) => x !== id)
          : [...prev[kind], id],
      };
      try {
        localStorage.setItem(FAV_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  return [favs[kind].includes(id), toggle];
}

/** Star button with ALT+F shortcut. */
export function FavStar({
  kind,
  id,
  label,
}: {
  kind: "issues" | "cycles";
  id: string;
  label: string;
}) {
  const [fav, toggle] = useFavorite(kind, id);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);
  return (
    <Tip label={fav ? "Remove from favorites" : "Add to favorites (Alt+F)"}>
      <button
        type="button"
        className={styleClasses(
          cx(
            "grid place-items-center w-7 h-7 rounded-md bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
            fav &&
              "data-[style-hooks~='lin-icon-btn']:text-foreground data-[style-hooks~='lin-icon-btn']:border-line data-[style-hooks~='lin-icon-btn']:bg-subtle",
          ),
        )}
        data-style-hooks={styleHooks(
          cx(
            "lin-icon-btn grid place-items-center w-[28px] h-[28px] rounded-[6px] bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
            fav &&
              "active data-[style-hooks~='lin-icon-btn']:text-foreground data-[style-hooks~='lin-icon-btn']:border-line data-[style-hooks~='lin-icon-btn']:bg-subtle",
          ),
        )}
        aria-label={`${fav ? "Remove from favorites: " : "Add to favorites: "}${label}`}
        aria-pressed={fav}
        onClick={toggle}
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill={fav ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <path
            d="M8 1.8l2 4.1 4.5.6-3.3 3.1.8 4.5L8 11.9l-4 2.2.8-4.5L1.5 6.5l4.5-.6z"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </Tip>
  );
}

/** Icon button matching the upstream contract's toolbar buttons. */
export function IconBtn({
  label,
  title,
  onClick,
  active,
  children,
  disabled,
}: {
  label: string;
  title?: string;
  onClick?: (e: React.MouseEvent) => void;
  active?: boolean;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <Tip label={title || label}>
      <button
        type="button"
        className={styleClasses(
          cx(
            "grid place-items-center w-7 h-7 rounded-md bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
            active &&
              "data-[style-hooks~='lin-icon-btn']:text-foreground data-[style-hooks~='lin-icon-btn']:border-line data-[style-hooks~='lin-icon-btn']:bg-subtle",
          ),
        )}
        data-style-hooks={styleHooks(
          cx(
            "lin-icon-btn grid place-items-center w-[28px] h-[28px] rounded-[6px] bg-transparent text-muted border border-solid border-transparent relative [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-40 disabled:cursor-default",
            active &&
              "active data-[style-hooks~='lin-icon-btn']:text-foreground data-[style-hooks~='lin-icon-btn']:border-line data-[style-hooks~='lin-icon-btn']:bg-subtle",
          ),
        )}
        aria-label={label}
        title={title}
        onClick={onClick}
        disabled={disabled}
      >
        {children}
      </button>
    </Tip>
  );
}

/** Small progress ring (0..1) used in cycle stat rows. */
export function Ring({ frac, size = 16 }: { frac: number; size?: number }) {
  const r = (size - 3) / 2,
    circ = 2 * Math.PI * r;
  const f = Math.min(1, Math.max(0, frac));
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
      className={styleClasses("")}
      data-style-hooks={styleHooks("lin-ring")}
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--line)"
        strokeWidth="2.4"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={f >= 1 ? "#5e6ad2" : "#f2994a"}
        strokeWidth="2.4"
        strokeDasharray={`${circ * f} ${circ}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

// Legacy styling hooks are data attributes; only Tailwind utilities reach className.
const legacyStyleHooks = new Set([
  "lin-page",
  "lin-scroll",
  "lin-head",
  "lin-crumb",
  "crumb-link",
  "sep",
  "current",
  "crumb-id",
  "lin-head-actions",
  "lin-nav-count",
  "lin-icon-btn",
  "active",
  "bordered",
  "lin-tip",
  "lin-kbd",
  "lin-menu",
  "lin-menu-item",
  "danger",
  "lin-menu-icon",
  "lin-menu-label",
  "lin-menu-divider",
  "lin-popover",
  "lin-viewbar",
  "lin-tabs",
  "lin-tab",
  "lin-viewchips",
  "lin-chipbtn",
  "lin-toolbar",
  "lin-search",
  "spacer",
  "lin-filterbar",
  "lin-fchip",
  "lin-filtermenu",
  "fm-search",
  "fm-item",
  "fm-ic",
  "fm-val",
  "fm-sub",
  "fm-opt",
  "fm-back",
  "lin-display",
  "dp-sec",
  "dp-title",
  "dp-row",
  "dp-seg",
  "dp-foot",
  "lin-check",
  "lin-list",
  "lin-group",
  "lin-group-head",
  "chev",
  "count",
  "grow",
  "add",
  "lin-row",
  "selected",
  "rowcheck",
  "ident",
  "title",
  "meta",
  "date",
  "lin-status-age",
  "lin-status-age-tip",
  "lin-avatar",
  "lin-chip",
  "lin-chip-x",
  "lin-subissue-pill",
  "overdue",
  "lin-empty",
  "lin-multibar",
  "n",
  "div",
  "lin-loadmore",
  "lin-error",
  "lin-board",
  "lin-bcol-hidden",
  "lin-hidden-col",
  "show",
  "lin-bcol",
  "sp",
  "lin-bcards",
  "lin-bcard",
  "top",
  "avatar",
  "ttl",
  "row",
  "assignee-name",
  "chips",
  "lin-bempty",
  "lin-detail",
  "lin-detail-main",
  "lin-detail-title",
  "lin-detail-desc",
  "lin-detail-empty",
  "lin-desc-edit",
  "empty",
  "lin-detail-actions",
  "lin-subsec",
  "lin-subrow",
  "t",
  "pending",
  "lin-activity",
  "lin-subscribe-btn",
  "lin-event",
  "lin-comment",
  "lin-comment-edited",
  "md",
  "del",
  "lin-commentbox",
  "lin-attach-list",
  "lin-attach",
  "thumb",
  "lin-props",
  "lin-prop",
  "k",
  "v",
  "plain",
  "trunc",
  "sec",
  "lin-addrow",
  "lin-ts",
  "lin-sublist",
  "lin-dd",
  "lin-dd-panel",
  "lin-dd-opt",
  "lin-cycles-head",
  "lin-cycles-wrap",
  "lin-cycle-workbench",
  "lin-cycle-browser",
  "lin-cycle-nav-head",
  "lin-cycles-tl",
  "lin-cycles-spine",
  "lin-cycle-row",
  "lin-cycle-tmark",
  "now",
  "lin-cycle-body",
  "cname",
  "lin-cycle-name-text",
  "lin-cycle-select-chevron",
  "stats",
  "range",
  "lin-badge",
  "lin-cap",
  "hot",
  "lin-cycle-detail-btn",
  "lin-cycle-chart",
  "chart",
  "legend",
  "lr",
  "sw",
  "lin-cycle-preview",
  "lin-cycle-preview-content",
  "lin-cycle-preview-head",
  "lin-cycle-preview-kicker",
  "lin-cycle-live-dot",
  "lin-cycle-open",
  "lin-cycle-preview-progress",
  "lin-cycle-progress-ring",
  "lin-cycle-preview-stats",
  "lin-cycle-preview-chart",
  "lin-cycle-preview-work",
  "lin-burndown",
  "lin-cycle-issue-id",
  "lin-cycle-issue-title",
  "lin-cycle-preview-empty",
  "lin-cycle-preview-more",
  "lin-cycle-stat-label",
  "lin-cdetail",
  "lin-cdetail-main",
  "lin-cdetail-count",
  "lin-cpane",
  "cur",
  "adddoc",
  "datenote",
  "datebtn",
  "lin-progress-head",
  "lin-ctabs",
  "lin-ctab",
  "lin-statrow",
  "nm",
  "pc",
  "axis",
  "grid",
  "lin-csettings",
  "desc",
  "card",
  "f",
  "lin-auto-row",
  "row2",
  "lin-btn",
  "primary",
  "lin-label-tip",
  "t1",
  "t2",
  "t3",
  "lin-loading",
  "lin-note",
  "lin-card-ctl",
  "lin-card-btn",
  "lin-card-menu",
  "lin-card-due",
  "lin-card-pop",
  "lin-bcard-error",
  "lin-dd-right",
  "lin-action-notice",
  "error",
  "lin-df",
  "lin-decisions",
  "lin-decision",
  "lin-decision-kicker",
  "lin-decision-dismiss",
  "lin-decision-q",
  "lin-decision-context",
  "lin-decision-actions",
  "lin-decision-option",
  "lin-decision-free",
  "lin-decision-meta",
  "lin-decision-settled",
  "lin-decision-answer",
  "ttl-row",
  "lin-priority-chip",
  "ds-split",
  "ds-split-ledger",
  "ds-split-detail",
  "ds-scroll",
  "ds-scroll--list",
  "ds-scroll--detail",
  "ds-scroll--diff",
  "ds-tabstrip",
  "ds-tab",
  "ds-group-bar",
  "ds-list-row",
  "ds-list-row-main",
  "ds-prop",
  "ds-prop-label",
  "ds-prop-value",
  "ds-empty",
  "ds-composer",
  "ds-composer-input",
  "ds-composer-action",
  "review-actions",
  "review-actions-gate",
  "review-actions-row",
  "review-actions-merge",
  "review-actions-blocked",
  "review-actions-error",
  "review-actions-notice",
  "review-actions-compose",
  "review-actions-confirm",
  "review-actions-confirm-gate",
  "lin-label-chip",
  "lin-project-chip",
  "lin-cycle-chip",
  "lin-due-chip",
  "lin-priority",
  "lin-cycle-glyph",
  "lin-ring",
  "done",
]);
const styleClasses = (value?: string) =>
  value
    ?.split(/\s+/)
    .filter((token) => !legacyStyleHooks.has(token))
    .join(" ");
const styleHooks = (value?: string) =>
  value
    ?.split(/\s+/)
    .filter((token) => legacyStyleHooks.has(token))
    .join(" ") || undefined;
export { styleClasses, styleHooks };
