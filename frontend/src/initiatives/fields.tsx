/* Initiative properties as reusable pieces: how each one is drawn, and the menu that
 * changes it. The page's chip row, its sidebar, the list and the create dialog all
 * use these, so a property looks and behaves the same everywhere. */
import React, { useEffect, useMemo, useState } from "react";
import {
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleX,
  Target,
} from "lucide-react";
import { PickerMenu, type PickerOption } from "../graph/detail";
import { healthColors, healthDot, healthLabel } from "../graph/health";
import { Avatar, cx, PriorityIcon } from "../graph/shared";
import { useProjectSyncMeta, useProjects } from "../projectStore";
import type { Project } from "../main";
import {
  createLabel,
  fetchLabels,
  type InitiativeLabel,
  type InitiativeRef,
} from "./api";

export const INITIATIVE_STATUSES = [
  { id: "planned", label: "Planned", Icon: CircleDashed, tone: "text-muted" },
  { id: "active", label: "Active", Icon: CircleDot, tone: "text-amber-600" },
  {
    id: "completed",
    label: "Completed",
    Icon: CircleCheck,
    tone: "text-indigo-600",
  },
  { id: "canceled", label: "Canceled", Icon: CircleX, tone: "text-muted" },
] as const;
export const PRIORITIES = ["No priority", "Urgent", "High", "Medium", "Low"];
export const REMINDERS = [
  { id: "default", label: "Workspace schedule" },
  { id: "never", label: "Never" },
  { id: "weekly", label: "Every week" },
  { id: "biweekly", label: "Every two weeks" },
  { id: "monthly", label: "Every month" },
] as const;
export const ICONS = [
  "🎯",
  "🚀",
  "📈",
  "🧭",
  "🌱",
  "🛠️",
  "💡",
  "🏁",
  "🔒",
  "🌍",
  "⚡",
  "📱",
];
export const COLORS = [
  "#5e6ad2",
  "#26b5ce",
  "#4cb782",
  "#f2c94c",
  "#f2994a",
  "#eb5757",
  "#bb87fc",
  "#95a2b3",
];

export function InitiativeStatusIcon({ status }: { status: string }) {
  const s =
    INITIATIVE_STATUSES.find((x) => x.id === status) || INITIATIVE_STATUSES[0];
  return <s.Icon size={14} className={cx("flex-none", s.tone)} aria-hidden />;
}
export const initiativeStatusLabel = (status: string) =>
  INITIATIVE_STATUSES.find((x) => x.id === status)?.label || status;

/** The initiative's own icon: its emoji, or the target glyph tinted with its colour. */
export function InitiativeIcon({
  icon,
  color,
  size = 14,
}: {
  icon?: string | null;
  color?: string | null;
  size?: number;
}) {
  if (icon)
    return (
      <span
        aria-hidden
        className="inline-grid flex-none place-items-center leading-none"
        style={{ width: size, height: size, fontSize: size * 0.9 }}
      >
        {icon}
      </span>
    );
  return (
    <Target
      size={size}
      aria-hidden
      className="flex-none text-muted"
      style={color ? { color } : undefined}
    />
  );
}

/** The health of the latest update, as a dot and a label. */
export function HealthBadge({
  health,
  className,
}: {
  health?: string | null;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium",
        healthColors(health),
        className,
      )}
    >
      <span className={cx("size-2 rounded-full", healthDot(health))} />
      {healthLabel(health)}
    </span>
  );
}

export function LabelDot({ color }: { color?: string | null }) {
  return (
    <span
      aria-hidden
      className="inline-block size-2 flex-none rounded-full bg-muted"
      style={color ? { background: color } : undefined}
    />
  );
}

/* ---------------- data the menus offer ---------------- */

export function useMembers() {
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

export function useTeams() {
  const meta = useProjectSyncMeta();
  return useMemo(
    () =>
      (meta?.teams?.nodes || []).map((t) => ({
        id: t.id,
        name: t.name,
        key: t.key,
      })),
    [meta],
  );
}

let labelCache: InitiativeLabel[] | null = null;
const labelListeners = new Set<(rows: InitiativeLabel[]) => void>();
function publishLabels(rows: InitiativeLabel[]) {
  labelCache = rows;
  labelListeners.forEach((fn) => fn(rows));
}
/** Reload the shared label list, after labels are edited elsewhere (Settings). */
export function refreshInitiativeLabels() {
  return fetchLabels()
    .then(publishLabels)
    .catch(() => undefined);
}
export function useInitiativeLabels() {
  const [rows, setRows] = useState<InitiativeLabel[]>(labelCache || []);
  useEffect(() => {
    labelListeners.add(setRows);
    if (!labelCache)
      fetchLabels()
        .then(publishLabels)
        .catch(() => undefined);
    return () => {
      labelListeners.delete(setRows);
    };
  }, []);
  const create = async (name: string) => {
    const label = await createLabel(name);
    publishLabels(
      [...(labelCache || []).filter((l) => l.id !== label.id), label].sort(
        (a, b) => a.name.localeCompare(b.name),
      ),
    );
    return label;
  };
  return { labels: rows, create };
}

export function useProjectOptions(extra: Project[] = []) {
  return useProjects(extra).filter((p) => !p.id.startsWith("optimistic:"));
}

/* ---------------- menus ---------------- */

type Close = () => void;

export function StatusMenu({
  value,
  onPick,
  close,
}: {
  value: string;
  onPick: (status: string) => void;
  close: Close;
}) {
  return (
    <PickerMenu
      label="Status"
      onClose={close}
      onPick={(id) => id && onPick(id)}
      options={INITIATIVE_STATUSES.map((s) => ({
        id: s.id,
        keywords: s.label,
        selected: s.id === value,
        label: (
          <span className="inline-flex items-center gap-2">
            <InitiativeStatusIcon status={s.id} />
            {s.label}
          </span>
        ),
      }))}
    />
  );
}

export function PriorityMenu({
  value,
  onPick,
  close,
}: {
  value: number;
  onPick: (priority: number) => void;
  close: Close;
}) {
  return (
    <PickerMenu
      label="Priority"
      onClose={close}
      onPick={(id) => onPick(Number(id))}
      options={PRIORITIES.map((label, v) => ({
        id: String(v),
        keywords: label,
        selected: value === v,
        label: (
          <span className="inline-flex items-center gap-2">
            <PriorityIcon priority={v} />
            {label}
          </span>
        ),
      }))}
    />
  );
}

export function MemberMenu({
  label,
  value,
  onPick,
  close,
}: {
  label: string;
  value?: string | null;
  onPick: (id: string | null) => void;
  close: Close;
}) {
  const members = useMembers();
  return (
    <PickerMenu
      label={label}
      onClose={close}
      onPick={onPick}
      options={[
        {
          id: null,
          keywords: "no one none",
          selected: !value,
          label: `No ${label.toLowerCase()}`,
        },
        ...members.map((m) => ({
          id: m.id,
          keywords: m.name,
          selected: m.id === value,
          label: (
            <span className="inline-flex items-center gap-2">
              <Avatar name={m.name} size={16} />
              {m.name}
            </span>
          ),
        })),
      ]}
    />
  );
}

export function TeamMenu({
  value,
  onPick,
  close,
}: {
  value?: string | null;
  onPick: (id: string | null) => void;
  close: Close;
}) {
  const teams = useTeams();
  return (
    <PickerMenu
      label="Lead team"
      onClose={close}
      onPick={onPick}
      options={[
        { id: null, keywords: "none", selected: !value, label: "No lead team" },
        ...teams.map((t) => ({
          id: t.id,
          keywords: `${t.name} ${t.key}`,
          selected: t.id === value,
          label: `${t.name} (${t.key})`,
        })),
      ]}
    />
  );
}

export function LabelMenu({
  value,
  onChange,
  close,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
  close: Close;
}) {
  const { labels, create } = useInitiativeLabels();
  const [error, setError] = useState("");
  return (
    <PickerMenu
      label="Labels"
      multi
      onClose={close}
      placeholder="Search or create a label…"
      onPick={(id) => {
        if (!id) return;
        if (value.includes(id)) return onChange(value.filter((x) => x !== id));
        /* In an exclusive group a new pick replaces the group's other label. */
        const group = labels.find((l) => l.id === id)?.parent?.id;
        const exclusive =
          group && labels.find((l) => l.id === group)?.exclusive !== false;
        const keep = exclusive
          ? value.filter(
              (x) => labels.find((l) => l.id === x)?.parent?.id !== group,
            )
          : value;
        onChange([...keep, id]);
      }}
      onCreate={(name) => {
        setError("");
        create(name)
          .then((l) => onChange([...value, l.id]))
          .catch((e: Error) => setError(e.message));
      }}
      options={labels
        .filter((l) => !l.isGroup)
        .sort((a, b) =>
          `${a.parent?.name || ""} ${a.name}`.localeCompare(
            `${b.parent?.name || ""} ${b.name}`,
          ),
        )
        .map((l) => ({
          id: l.id,
          keywords: `${l.parent?.name || ""} ${l.name}`,
          selected: value.includes(l.id),
          label: (
            <span className="inline-flex min-w-0 items-center gap-2">
              <LabelDot color={l.color} />
              {l.parent && (
                <span className="text-muted">{l.parent.name} ›</span>
              )}
              <span className="truncate">{l.name}</span>
            </span>
          ),
        }))}
      footer={
        error ? (
          <p className="m-0 p-2 text-xs text-danger" role="alert">
            {error}
          </p>
        ) : null
      }
    />
  );
}

export function InitiativeMenu({
  label,
  value,
  options,
  onPick,
  close,
}: {
  label: string;
  value?: string | null;
  options: InitiativeRef[];
  onPick: (id: string | null) => void;
  close: Close;
}) {
  const items: PickerOption[] = [
    {
      id: null,
      keywords: "none top level",
      selected: !value,
      label: "No parent",
    },
    ...options.map((i) => ({
      id: i.id,
      keywords: `${i.identifier} ${i.name}`,
      selected: i.id === value,
      label: (
        <span className="inline-flex min-w-0 items-center gap-2">
          <InitiativeIcon icon={i.icon} color={i.color} />
          <span className="text-muted">{i.identifier}</span>
          <span className="truncate">{i.name}</span>
        </span>
      ),
    })),
  ];
  return (
    <PickerMenu label={label} onClose={close} onPick={onPick} options={items} />
  );
}

export function ProjectsMenu({
  value,
  onChange,
  close,
  projects,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
  close: Close;
  projects: Project[];
}) {
  return (
    <PickerMenu
      label="Projects"
      multi
      onClose={close}
      onPick={(id) =>
        id &&
        onChange(
          value.includes(id) ? value.filter((x) => x !== id) : [...value, id],
        )
      }
      options={projects.map((p) => ({
        id: p.id,
        keywords: p.name,
        selected: value.includes(p.id),
        label: p.name,
      }))}
    />
  );
}

export function ReminderMenu({
  value,
  onPick,
  close,
}: {
  value?: string | null;
  onPick: (cadence: string | null) => void;
  close: Close;
}) {
  return (
    <PickerMenu
      label="Update reminders"
      onClose={close}
      onPick={(id) => onPick(id === "default" ? null : id)}
      options={REMINDERS.map((r) => ({
        id: r.id,
        keywords: r.label,
        selected: (value || "default") === r.id,
        label: r.label,
      }))}
    />
  );
}

export function DateMenu({
  label,
  value,
  onPick,
  close,
}: {
  label: string;
  value?: string | null;
  onPick: (date: string | null) => void;
  close: Close;
}) {
  return (
    <div className="grid gap-2 p-1">
      <input
        type="date"
        aria-label={label}
        className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm text-foreground"
        defaultValue={(value || "").slice(0, 10)}
        onChange={(e) => {
          onPick(e.target.value || null);
          close();
        }}
      />
      {value && (
        <button
          type="button"
          className="justify-self-start rounded-md px-2 py-1 text-sm text-muted hover:bg-subtle"
          onClick={() => {
            onPick(null);
            close();
          }}
        >
          Clear date
        </button>
      )}
    </div>
  );
}

export function IconColorMenu({
  icon,
  color,
  onChange,
  close,
}: {
  icon?: string | null;
  color?: string | null;
  onChange: (next: { icon?: string | null; color?: string | null }) => void;
  close: Close;
}) {
  return (
    <div className="grid gap-3 p-2" role="group" aria-label="Icon and colour">
      <div className="grid grid-cols-6 gap-1">
        {ICONS.map((e) => (
          <button
            key={e}
            type="button"
            aria-label={`Icon ${e}`}
            aria-pressed={icon === e}
            className="grid size-8 place-items-center rounded-md text-base hover:bg-subtle aria-pressed:bg-subtle focus-visible:outline-2 focus-visible:outline-accent"
            onClick={() => onChange({ icon: e })}
          >
            {e}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {COLORS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`Colour ${c}`}
            aria-pressed={color === c}
            className="size-6 rounded-full border-2 border-transparent aria-pressed:border-foreground focus-visible:outline-2 focus-visible:outline-accent"
            style={{ background: c }}
            onClick={() => onChange({ color: c, icon: null })}
          />
        ))}
      </div>
      <button
        type="button"
        className="justify-self-start rounded-md px-2 py-1 text-sm text-muted hover:bg-subtle"
        onClick={() => {
          onChange({ icon: null, color: null });
          close();
        }}
      >
        Reset icon
      </button>
    </div>
  );
}
