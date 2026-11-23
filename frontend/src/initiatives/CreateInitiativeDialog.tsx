/* Linear's "New initiative" dialog: icon, name and summary on top, every property as
 * a chip, a description, then Create. Opened by the list's button, the empty state,
 * the command palette, a sub-initiative "+" and the N then I shortcut. */
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Box,
  Calendar,
  Tag,
  Users,
  X,
  BellRing,
  CornerLeftUp,
} from "lucide-react";
import { PropertyChip } from "../graph/detail";
import { Avatar, fmtDate, PriorityIcon } from "../graph/shared";
import type { Project } from "../main";
import {
  createInitiative,
  type InitiativeRef,
  type InitiativeRow,
} from "./api";
import {
  DateMenu,
  IconColorMenu,
  InitiativeIcon,
  InitiativeMenu,
  InitiativeStatusIcon,
  initiativeStatusLabel,
  LabelMenu,
  MemberMenu,
  PRIORITIES,
  PriorityMenu,
  ProjectsMenu,
  REMINDERS,
  ReminderMenu,
  StatusMenu,
  TeamMenu,
  useInitiativeLabels,
  useMembers,
  useProjectOptions,
  useTeams,
} from "./fields";

type Draft = {
  name: string;
  summary: string;
  description: string;
  status: string;
  priority: number;
  ownerId: string | null;
  targetDate: string | null;
  leadTeamId: string | null;
  labelIds: string[];
  parentId: string | null;
  projectIds: string[];
  updateReminder: string | null;
  icon: string | null;
  color: string | null;
};

const blank = (over: Partial<Draft> = {}): Draft => ({
  name: "",
  summary: "",
  description: "",
  status: "planned",
  priority: 0,
  ownerId: null,
  targetDate: null,
  leadTeamId: null,
  labelIds: [],
  parentId: null,
  projectIds: [],
  updateReminder: null,
  icon: null,
  color: null,
  ...over,
});

export function CreateInitiativeDialog({
  defaults,
  initiatives,
  projects,
  onClose,
  onCreated,
}: {
  defaults?: Partial<Draft>;
  initiatives: InitiativeRef[];
  projects: Project[];
  onClose: () => void;
  /** `more` is true when "Create more" is on: the dialog stays open. */
  onCreated: (row: InitiativeRow, more: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => blank(defaults));
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nameRef = useRef<HTMLInputElement | null>(null);
  const opener = useRef<Element | null>(document.activeElement);
  const members = useMembers();
  const teams = useTeams();
  const { labels } = useInitiativeLabels();
  const projectOptions = useProjectOptions(projects);
  useEffect(() => {
    nameRef.current?.focus();
    const back = opener.current;
    return () => {
      if (back instanceof HTMLElement && document.contains(back)) back.focus();
    };
  }, []);
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  const submit = async () => {
    if (!draft.name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const row = await createInitiative({
        ...draft,
        name: draft.name.trim(),
        summary: draft.summary.trim() || null,
        description: draft.description.trim() || null,
      });
      onCreated(row, more);
      if (more) {
        setDraft(
          blank({
            ...defaults,
            status: draft.status,
            parentId: draft.parentId,
          }),
        );
        nameRef.current?.focus();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const owner = members.find((m) => m.id === draft.ownerId);
  const team = teams.find((t) => t.id === draft.leadTeamId);
  const parent = initiatives.find((i) => i.id === draft.parentId);
  const chosenLabels = labels.filter((l) => draft.labelIds.includes(l.id));

  return createPortal(
    <div
      className="fixed inset-0 z-100 flex items-start justify-center overflow-y-auto overscroll-contain bg-black/40 px-4 py-16 shell-phone:px-2 shell-phone:py-2"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-label="New initiative"
        data-initiative-create-dialog=""
        className="grid w-full max-w-2xl gap-3 rounded-xl border border-line bg-panel p-4 shadow-xl pointer-coarse:[&_[data-initiative-create-chips]_button]:min-h-11 pointer-coarse:[&_button]:min-h-11 shell-phone:[&_button]:min-h-11 pointer-coarse:[&_button]:min-w-11 shell-phone:[&_button]:min-w-11"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void submit();
          }
        }}
      >
        <header className="flex items-center gap-2 text-sm text-muted">
          <span className="rounded-md border border-line px-2 py-0.5 text-xs">
            Initiatives
          </span>
          <span aria-hidden>›</span>
          <span className="text-foreground">New initiative</span>
          <span className="flex-1" />
          <button
            type="button"
            aria-label="Close"
            className="grid size-7 place-items-center rounded-md hover:bg-subtle pointer-coarse:size-11 shell-phone:-my-2 shell-phone:-mr-2 shell-phone:size-11"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>
        <PropertyChip
          label="Icon and colour"
          icon={
            <InitiativeIcon icon={draft.icon} color={draft.color} size={18} />
          }
          menu={(close) => (
            <IconColorMenu
              icon={draft.icon}
              color={draft.color}
              close={close}
              onChange={(next) => set(next)}
            />
          )}
        >
          <span className="sr-only">Icon</span>
        </PropertyChip>
        <input
          ref={nameRef}
          aria-label="Initiative name"
          placeholder="Initiative name"
          required
          className="w-full border-0 bg-transparent p-0 text-xl font-semibold text-foreground placeholder:text-muted focus:outline-none pointer-coarse:min-h-11 shell-phone:min-h-11"
          value={draft.name}
          onChange={(e) => set({ name: e.target.value })}
        />
        <input
          aria-label="Summary"
          placeholder="Add a short summary…"
          className="w-full border-0 bg-transparent p-0 text-base text-muted placeholder:text-muted focus:outline-none pointer-coarse:min-h-11 shell-phone:min-h-11"
          value={draft.summary}
          onChange={(e) => set({ summary: e.target.value })}
        />
        <div className="flex flex-wrap gap-1.5" data-initiative-create-chips="">
          <PropertyChip
            label={`Status: ${initiativeStatusLabel(draft.status)}`}
            icon={<InitiativeStatusIcon status={draft.status} />}
            menu={(close) => (
              <StatusMenu
                value={draft.status}
                close={close}
                onPick={(status) => set({ status })}
              />
            )}
          >
            {initiativeStatusLabel(draft.status)}
          </PropertyChip>
          <PropertyChip
            label={`Priority: ${PRIORITIES[draft.priority]}`}
            icon={<PriorityIcon priority={draft.priority} />}
            menu={(close) => (
              <PriorityMenu
                value={draft.priority}
                close={close}
                onPick={(priority) => set({ priority })}
              />
            )}
          >
            {PRIORITIES[draft.priority]}
          </PropertyChip>
          <PropertyChip
            label={`Owner: ${owner?.name || "none"}`}
            icon={<Avatar name={owner?.name} size={14} />}
            menu={(close) => (
              <MemberMenu
                label="Owner"
                value={draft.ownerId}
                close={close}
                onPick={(ownerId) => set({ ownerId })}
              />
            )}
          >
            {owner?.name || "Owner"}
          </PropertyChip>
          <PropertyChip
            label={`Target date: ${draft.targetDate || "none"}`}
            icon={<Calendar size={13} aria-hidden />}
            menu={(close) => (
              <DateMenu
                label="Target date"
                value={draft.targetDate}
                close={close}
                onPick={(targetDate) => set({ targetDate })}
              />
            )}
          >
            {draft.targetDate ? fmtDate(draft.targetDate) : "Target date"}
          </PropertyChip>
          <PropertyChip
            label={`Lead team: ${team?.name || "none"}`}
            icon={<Users size={13} aria-hidden />}
            menu={(close) => (
              <TeamMenu
                value={draft.leadTeamId}
                close={close}
                onPick={(leadTeamId) => set({ leadTeamId })}
              />
            )}
          >
            {team?.name || "Lead team"}
          </PropertyChip>
          <PropertyChip
            label={`Labels: ${chosenLabels.map((l) => l.name).join(", ") || "none"}`}
            icon={<Tag size={13} aria-hidden />}
            menu={(close) => (
              <LabelMenu
                value={draft.labelIds}
                close={close}
                onChange={(labelIds) => set({ labelIds })}
              />
            )}
          >
            {chosenLabels.length
              ? chosenLabels.map((l) => l.name).join(", ")
              : "Labels"}
          </PropertyChip>
          <PropertyChip
            label={`Parent: ${parent?.name || "none"}`}
            icon={<CornerLeftUp size={13} aria-hidden />}
            menu={(close) => (
              <InitiativeMenu
                label="Parent initiative"
                value={draft.parentId}
                options={initiatives}
                close={close}
                onPick={(parentId) => set({ parentId })}
              />
            )}
          >
            {parent ? `${parent.identifier} ${parent.name}` : "Parent"}
          </PropertyChip>
          <PropertyChip
            label={`Projects: ${draft.projectIds.length}`}
            icon={<Box size={13} aria-hidden />}
            menu={(close) => (
              <ProjectsMenu
                value={draft.projectIds}
                projects={projectOptions}
                close={close}
                onChange={(projectIds) => set({ projectIds })}
              />
            )}
          >
            {draft.projectIds.length
              ? `${draft.projectIds.length} project${draft.projectIds.length === 1 ? "" : "s"}`
              : "Projects"}
          </PropertyChip>
          <PropertyChip
            label={`Update reminders: ${draft.updateReminder || "never"}`}
            icon={<BellRing size={13} aria-hidden />}
            menu={(close) => (
              <ReminderMenu
                value={draft.updateReminder}
                close={close}
                onPick={(updateReminder) => set({ updateReminder })}
              />
            )}
          >
            {REMINDERS.find((r) => r.id === (draft.updateReminder || "never"))
              ?.label || "Reminders"}
          </PropertyChip>
        </div>
        <textarea
          aria-label="Description"
          placeholder="Write a description, a project brief, or collect ideas…"
          rows={5}
          className="min-h-28 w-full resize-y rounded-md border border-line bg-bg p-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent"
          value={draft.description}
          onChange={(e) => set({ description: e.target.value })}
        />
        {error && (
          <p className="m-0 text-sm text-danger" role="alert">
            {error}
          </p>
        )}
        <footer className="flex flex-wrap items-center justify-end gap-3">
          <label className="mr-auto inline-flex items-center gap-2 text-sm text-muted pointer-coarse:min-h-11 shell-phone:basis-full">
            <input
              type="checkbox"
              checked={more}
              onChange={(e) => setMore(e.target.checked)}
            />
            Create more
          </label>
          <button
            type="button"
            className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm hover:bg-subtle pointer-coarse:min-h-11 shell-phone:min-h-11 shell-phone:flex-1"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!draft.name.trim() || busy}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:brightness-110 disabled:opacity-60 pointer-coarse:min-h-11 shell-phone:min-h-11 shell-phone:flex-1 whitespace-nowrap"
          >
            {busy ? "Creating…" : "Create initiative"}
          </button>
        </footer>
      </form>
    </div>,
    document.body,
  );
}
