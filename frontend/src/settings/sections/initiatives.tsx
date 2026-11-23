import * as React from "react";
import {
  Archive,
  FolderPlus,
  MoreHorizontal,
  Palette,
  Plus,
  Tag,
} from "lucide-react";
import { refreshInitiativeLabels } from "../../initiatives/fields";
import { Button } from "../../components/ui/button";
import { Input, Label } from "../../components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/menu";
import { Select } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import { useSettings, type SettingsTeam } from "../context";
import {
  ConfirmAction,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  SaveStatusLine,
  SettingRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useResource,
  useSetting,
  useSettingText,
} from "../ui";
import { ColorDot, ColorPicker } from "./project-statuses/ColorPicker";
import {
  WEEKDAYS,
  archiveLabel,
  createLabel,
  fetchLabelData,
  fetchReminderPolicy,
  policyLabel,
  saveReminderPolicy,
  setTeamShowsInitiatives,
  updateLabel,
  type InitiativeLabel,
  type ReminderPolicy,
} from "./initiatives/api";

/* Settings → Initiatives: the workspace update-reminder schedule (admins), initiative
 * labels and label groups (any signed-in member, as the API allows), and which teams list
 * Initiatives in their sidebar (any member who can see the team). */

export default function InitiativesSettings() {
  return (
    <SettingsPage
      title="Initiatives"
      description="How owners are reminded to post updates, the labels initiatives can carry, and which teams list initiatives in their sidebar."
    >
      <ReminderSchedule />
      <Labels />
      <TeamSidebars />
    </SettingsPage>
  );
}

/* ------------------------------------------------------------ reminders */

const CADENCES = [
  { value: "never", label: "Never" },
  { value: "weekly", label: "Every week" },
  { value: "biweekly", label: "Every two weeks" },
  { value: "monthly", label: "Every month", description: "The first one" },
];
const HOURS = Array.from({ length: 24 }, (_, h) => ({
  value: String(h),
  label: `${String(h).padStart(2, "0")}:00`,
}));

function ReminderSchedule() {
  const { isAdmin } = useSettings();
  const resource = useResource(fetchReminderPolicy);
  const policy = resource.data;
  const save = React.useCallback(
    async (patch: Partial<ReminderPolicy>) => {
      const next = await saveReminderPolicy(patch);
      resource.setData(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setData is stable
    [],
  );
  const cadence = useSetting({
    value: policy?.cadence ?? "never",
    save: (v) => save({ cadence: v }),
  });
  const weekday = useSetting({
    value: String(policy?.weekday ?? 4),
    save: (v) => save({ weekday: Number(v) }),
  });
  const hour = useSetting({
    value: String(policy?.hour ?? 9),
    save: (v) => save({ hour: Number(v) }),
  });
  const followUps = useSetting({
    value: policy?.followUps ?? true,
    save: (v) => save({ followUps: v }),
  });
  const locked = isAdmin
    ? undefined
    : "Only workspace admins can change the schedule.";
  const off = cadence.value === "never";
  const preview = policyLabel({
    cadence: cadence.value,
    weekday: Number(weekday.value),
    hour: Number(hour.value),
    followUps: followUps.value,
  });

  return (
    <SettingsSection
      title="Update reminders"
      description="Owners of active initiatives who have not posted since the last reminder get one on this schedule, in their own timezone. An initiative can use its own schedule or opt out."
    >
      <Loadable resource={resource} rows={4}>
        {() => (
          <>
            <SettingRow
              label="Remind owners"
              htmlFor="initiative-cadence"
              description={
                off
                  ? "No workspace reminders. Initiatives with their own schedule still get theirs."
                  : `${preview}. Changing the schedule restarts the reminder cycle.`
              }
              status={cadence.status}
              locked={locked}
            >
              <Select
                id="initiative-cadence"
                aria-label="Remind owners"
                value={cadence.value}
                onValueChange={(v) => void cadence.set(v)}
                options={CADENCES}
                disabled={!isAdmin}
              />
            </SettingRow>
            <SettingRow
              label="Day"
              htmlFor="initiative-weekday"
              description={
                cadence.value === "monthly"
                  ? "The first of this weekday in each month."
                  : undefined
              }
              status={weekday.status}
            >
              <Select
                id="initiative-weekday"
                aria-label="Reminder day"
                value={weekday.value}
                onValueChange={(v) => void weekday.set(v)}
                options={WEEKDAYS.map((d, i) => ({
                  value: String(i),
                  label: d,
                }))}
                disabled={!isAdmin || off}
              />
            </SettingRow>
            <SettingRow
              label="Time"
              htmlFor="initiative-hour"
              description="In each owner's own timezone."
              status={hour.status}
            >
              <Select
                id="initiative-hour"
                aria-label="Reminder time"
                value={hour.value}
                onValueChange={(v) => void hour.set(v)}
                options={HOURS}
                disabled={!isAdmin || off}
              />
            </SettingRow>
            <SettingRow
              label="Follow-ups"
              htmlFor="initiative-followups"
              description="If there is still no update, remind again one working day later, then two working days after that."
              status={followUps.status}
            >
              <Switch
                id="initiative-followups"
                checked={followUps.value}
                onCheckedChange={(v) => void followUps.set(v)}
                disabled={!isAdmin || off}
              />
            </SettingRow>
          </>
        )}
      </Loadable>
    </SettingsSection>
  );
}

/* --------------------------------------------------------------- labels */

type LabelDialog =
  | { kind: "label"; label: InitiativeLabel | null; groupId?: string | null }
  | { kind: "group" };

function Labels() {
  const resource = useResource(fetchLabelData);
  const [dialog, setDialog] = React.useState<LabelDialog | null>(null);
  const [archiving, setArchiving] = React.useState<InitiativeLabel | null>(
    null,
  );
  const labels = resource.data?.labels ?? [];
  const usage = resource.data?.usage ?? null;
  const groups = labels.filter((l) => l.isGroup);

  // Merge one changed label into the list (and tell the rest of the app's label menus).
  const replace = React.useCallback(
    (next: InitiativeLabel) => {
      resource.setData((cur) =>
        cur
          ? {
              ...cur,
              labels: cur.labels.some((l) => l.id === next.id)
                ? cur.labels.map((l) => (l.id === next.id ? next : l))
                : [...cur.labels, next],
            }
          : cur,
      );
      void refreshInitiativeLabels();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setData is stable
    [],
  );

  return (
    <SettingsSection
      title="Labels"
      description="Labels sort initiatives across teams. A group collects related labels; an initiative takes one label from a group unless the group allows several."
      bare
      action={
        <>
          <Button
            size="sm"
            onClick={() => setDialog({ kind: "group" })}
            disabled={!resource.data}
          >
            <FolderPlus className="size-3.5" aria-hidden="true" />
            New group
          </Button>
          <Button
            size="sm"
            onClick={() => setDialog({ kind: "label", label: null })}
            disabled={!resource.data}
          >
            <Plus className="size-3.5" aria-hidden="true" />
            New label
          </Button>
        </>
      }
    >
      <Loadable
        resource={resource}
        rows={3}
        isEmpty={(d) => d.labels.length === 0}
        empty={
          <EmptyState
            icon={<Tag />}
            title="No initiative labels yet"
            description="Add labels like Q3 or Platform, or start with a group such as Horizon and put labels in it."
            action={
              <Button
                variant="primary"
                onClick={() => setDialog({ kind: "label", label: null })}
              >
                New label
              </Button>
            }
          />
        }
      >
        {(d) => (
          <div className="grid gap-4" data-initiative-label-settings="">
            {groups.map((g) => {
              const children = d.labels.filter(
                (l) => !l.isGroup && l.parent?.id === g.id,
              );
              return (
                <SettingsCard key={g.id}>
                  <GroupHeader
                    group={g}
                    onSaved={replace}
                    onAddLabel={() =>
                      setDialog({ kind: "label", label: null, groupId: g.id })
                    }
                    onArchive={() => setArchiving(g)}
                  />
                  {children.map((l) => (
                    <LabelRow
                      key={l.id}
                      label={l}
                      count={usage ? usage[l.id] || 0 : null}
                      onSaved={replace}
                      onEdit={() => setDialog({ kind: "label", label: l })}
                      onArchive={() => setArchiving(l)}
                    />
                  ))}
                  {!children.length && (
                    <p className="m-0 px-(--ds-settings-row-padding) py-3 text-sm text-muted-foreground">
                      No labels in this group yet.
                    </p>
                  )}
                </SettingsCard>
              );
            })}
            {d.labels.some((l) => !l.isGroup && !l.parent) && (
              <SettingsCard>
                {groups.length > 0 && (
                  <p className="m-0 px-(--ds-settings-row-padding) py-2.5 text-sm font-medium text-muted-foreground">
                    Not in a group
                  </p>
                )}
                {d.labels
                  .filter((l) => !l.isGroup && !l.parent)
                  .map((l) => (
                    <LabelRow
                      key={l.id}
                      label={l}
                      count={usage ? usage[l.id] || 0 : null}
                      onSaved={replace}
                      onEdit={() => setDialog({ kind: "label", label: l })}
                      onArchive={() => setArchiving(l)}
                    />
                  ))}
              </SettingsCard>
            )}
          </div>
        )}
      </Loadable>

      {dialog?.kind === "label" && (
        <LabelFormDialog
          key={dialog.label?.id || `new-${dialog.groupId || ""}`}
          label={dialog.label}
          initialGroupId={dialog.groupId ?? dialog.label?.parent?.id ?? null}
          labels={labels}
          onClose={() => setDialog(null)}
          onSaved={replace}
        />
      )}
      {dialog?.kind === "group" && (
        <GroupFormDialog
          labels={labels}
          onClose={() => setDialog(null)}
          onSaved={replace}
        />
      )}

      <ConfirmAction
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
        title={
          archiving?.isGroup
            ? `Archive the group “${archiving.name}”?`
            : `Archive “${archiving?.name ?? ""}”?`
        }
        description={
          archiving ? (
            <ArchiveConsequence
              label={archiving}
              count={usage ? usage[archiving.id] || 0 : null}
              childCount={
                labels.filter((l) => l.parent?.id === archiving.id).length
              }
            />
          ) : null
        }
        confirmLabel={archiving?.isGroup ? "Archive group" : "Archive label"}
        onConfirm={async () => {
          if (!archiving) return;
          await archiveLabel(archiving.id);
          await resource.reload();
          void refreshInitiativeLabels();
        }}
      />
    </SettingsSection>
  );
}

function ArchiveConsequence({
  label,
  count,
  childCount,
}: {
  label: InitiativeLabel;
  count: number | null;
  childCount: number;
}) {
  if (label.isGroup)
    return (
      <>
        {childCount
          ? `Its ${childCount === 1 ? "label stays" : `${childCount} labels stay`} on every initiative and ${childCount === 1 ? "moves" : "move"} out of the group. `
          : "The group is empty. "}
        This cannot be undone here.
      </>
    );
  return (
    <>
      {count === null
        ? "It is removed from every initiative that carries it"
        : count === 0
          ? "No initiative carries it right now"
          : `It is removed from ${count === 1 ? "the 1 initiative" : `the ${count} initiatives`} that carry it`}
      , and it stops being offered in label menus. This cannot be undone here.
    </>
  );
}

function RowMenu({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          className="flex-none text-muted-foreground"
        >
          <MoreHorizontal className="size-4" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>{children}</DropdownMenuContent>
    </DropdownMenu>
  );
}

const nameRule = (next: string) =>
  !next
    ? "Give it a name."
    : next.length > 60
      ? "Keep the name to 60 characters or fewer."
      : null;

/** Inline name that saves on blur or Enter; Escape puts the saved name back. */
function useInlineName({
  item,
  ariaLabel,
  onSaved,
  strong,
}: {
  item: InitiativeLabel;
  ariaLabel: string;
  onSaved: (l: InitiativeLabel) => void;
  strong?: boolean;
}) {
  const text = useSettingText({
    value: item.name,
    validate: nameRule,
    save: async (next) => onSaved(await updateLabel(item.id, { name: next })),
  });
  // useSettingText blurs on Escape, and that blur commits the stale draft it closed over,
  // so Escape would save instead of revert. Skip the commit for the Escape-triggered blur.
  const escaping = React.useRef(false);
  const inputProps = {
    ...text.inputProps,
    onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") escaping.current = true;
      text.inputProps.onKeyDown(e);
      escaping.current = false;
    },
    onBlur: () => {
      if (!escaping.current) text.inputProps.onBlur();
    },
  };
  return {
    status: text.status,
    input: (
      <Input
        aria-label={ariaLabel}
        maxLength={60}
        className={
          "h-8 min-w-0 flex-1 border-transparent bg-transparent shadow-none hover:border-input focus-visible:bg-panel" +
          (strong ? " font-medium" : "")
        }
        {...inputProps}
      />
    ),
  };
}

function GroupHeader({
  group,
  onSaved,
  onAddLabel,
  onArchive,
}: {
  group: InitiativeLabel;
  onSaved: (l: InitiativeLabel) => void;
  onAddLabel: () => void;
  onArchive: () => void;
}) {
  const name = useInlineName({
    item: group,
    ariaLabel: `Name of group ${group.name}`,
    onSaved,
    strong: true,
  });
  const several = useSetting({
    value: group.exclusive === false,
    save: async (allow) =>
      onSaved(await updateLabel(group.id, { exclusive: !allow })),
  });
  const switchId = React.useId();
  return (
    <div
      className="grid gap-1 bg-subtle/60 px-(--ds-settings-row-padding) py-2"
      data-initiative-label-group=""
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-1 basis-48 items-center gap-2">
          <Tag
            className="size-3.5 flex-none text-muted-foreground"
            aria-hidden="true"
          />
          {name.input}
        </div>
        <div className="flex flex-none items-center gap-2">
          <Label
            htmlFor={switchId}
            className="text-sm font-normal text-muted-foreground"
          >
            Allow several
          </Label>
          <Switch
            id={switchId}
            aria-label={`Allow several labels from ${group.name}`}
            checked={several.value}
            onCheckedChange={(v) => void several.set(v)}
          />
          <RowMenu label={`Actions for group ${group.name}`}>
            <DropdownMenuItem onSelect={onAddLabel}>
              <Plus aria-hidden="true" />
              Add a label to this group…
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onSelect={onArchive}>
              <Archive aria-hidden="true" />
              Archive group…
            </DropdownMenuItem>
          </RowMenu>
        </div>
      </div>
      <SaveStatusLine status={name.status} />
      <SaveStatusLine status={several.status} />
    </div>
  );
}

function LabelRow({
  label,
  count,
  onSaved,
  onEdit,
  onArchive,
}: {
  label: InitiativeLabel;
  count: number | null;
  onSaved: (l: InitiativeLabel) => void;
  onEdit: () => void;
  onArchive: () => void;
}) {
  const name = useInlineName({
    item: label,
    ariaLabel: `Name of label ${label.name}`,
    onSaved,
  });
  return (
    <div
      className="grid gap-0.5 px-(--ds-settings-row-padding) py-1.5"
      data-initiative-label-row=""
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="grid size-6 flex-none place-items-center">
          <ColorDot color={label.color} />
        </span>
        {name.input}
        {count !== null && (
          <span className="flex-none text-sm text-muted-foreground tabular-nums">
            {count === 1 ? "1 initiative" : `${count} initiatives`}
          </span>
        )}
        <RowMenu label={`Actions for label ${label.name}`}>
          <DropdownMenuItem onSelect={onEdit}>
            <Palette aria-hidden="true" />
            Colour and group…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem destructive onSelect={onArchive}>
            <Archive aria-hidden="true" />
            Archive label…
          </DropdownMenuItem>
        </RowMenu>
      </div>
      <div className="pl-8">
        <SaveStatusLine status={name.status} />
      </div>
    </div>
  );
}

const NO_GROUP = "__none";

function LabelFormDialog({
  label,
  initialGroupId,
  labels,
  onClose,
  onSaved,
}: {
  label: InitiativeLabel | null;
  initialGroupId: string | null;
  labels: InitiativeLabel[];
  onClose: () => void;
  onSaved: (l: InitiativeLabel) => void;
}) {
  const [open, setOpen] = React.useState(true);
  const [name, setName] = React.useState(label?.name ?? "");
  const [groupId, setGroupId] = React.useState(initialGroupId || NO_GROUP);
  const [color, setColor] = React.useState<string | null>(label?.color ?? null);
  const groups = labels.filter((l) => l.isGroup);
  const clash =
    !label &&
    labels.some(
      (l) => l.name.trim().toLowerCase() === name.trim().toLowerCase(),
    );
  const problem = label
    ? null
    : clash
      ? "A label or group with this name already exists."
      : name.trim().length > 60
        ? "Keep the name to 60 characters or fewer."
        : null;
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) window.setTimeout(onClose, 150);
      }}
      title={label ? `Edit “${label.name}”` : "New initiative label"}
      submitLabel={label ? "Save label" : "Add label"}
      canSubmit={(!!label || !!name.trim()) && !problem}
      onSubmit={async () => {
        const parentId = groupId === NO_GROUP ? "" : groupId;
        if (label) {
          onSaved(
            await updateLabel(label.id, { parentId, color: color || "" }),
          );
        } else {
          onSaved(
            await createLabel(name.trim(), {
              parentId: parentId || null,
              color,
            }),
          );
        }
      }}
    >
      {!label && (
        <Field label="Name" htmlFor="initiative-label-name" error={problem}>
          <Input
            id="initiative-label-name"
            autoFocus
            value={name}
            maxLength={60}
            aria-invalid={!!problem || undefined}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
      )}
      <Field
        label="Group"
        htmlFor="initiative-label-group"
        help={
          groups.length
            ? undefined
            : "There are no groups yet. Create one with New group."
        }
      >
        <Select
          id="initiative-label-group"
          value={groupId}
          onValueChange={setGroupId}
          disabled={!groups.length}
          options={[
            { value: NO_GROUP, label: "No group" },
            ...groups.map((g) => ({ value: g.id, label: g.name })),
          ]}
          className="sm:w-full"
        />
      </Field>
      <Field label="Colour" htmlFor="initiative-label-color">
        <ColorPicker
          id="initiative-label-color"
          label="Label colour"
          value={color}
          onChange={setColor}
        />
      </Field>
    </FormDialog>
  );
}

function GroupFormDialog({
  labels,
  onClose,
  onSaved,
}: {
  labels: InitiativeLabel[];
  onClose: () => void;
  onSaved: (l: InitiativeLabel) => void;
}) {
  const [open, setOpen] = React.useState(true);
  const [name, setName] = React.useState("");
  const [several, setSeveral] = React.useState(false);
  const clash = labels.some(
    (l) => l.name.trim().toLowerCase() === name.trim().toLowerCase(),
  );
  const problem = clash
    ? "A label or group with this name already exists."
    : null;
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) window.setTimeout(onClose, 150);
      }}
      title="New label group"
      description="For example Horizon, with the labels Now, Next and Later."
      submitLabel="Add group"
      canSubmit={!!name.trim() && !problem}
      onSubmit={async () =>
        onSaved(
          await createLabel(name.trim(), {
            isGroup: true,
            exclusive: !several,
          }),
        )
      }
    >
      <Field label="Name" htmlFor="initiative-group-name" error={problem}>
        <Input
          id="initiative-group-name"
          autoFocus
          value={name}
          maxLength={60}
          aria-invalid={!!problem || undefined}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <div className="flex items-start justify-between gap-4">
        <div className="grid gap-0.5">
          <Label htmlFor="initiative-group-several" className="text-sm">
            Allow several labels from this group
          </Label>
          <p className="m-0 text-sm text-muted-foreground">
            Off: an initiative carries at most one of them.
          </p>
        </div>
        <Switch
          id="initiative-group-several"
          checked={several}
          onCheckedChange={setSeveral}
        />
      </div>
    </FormDialog>
  );
}

/* --------------------------------------------------------- team sidebars */

function TeamSidebars() {
  const { teams } = useSettings();
  return (
    <SettingsSection
      title="Team sidebars"
      description="A team that shows initiatives gets an Initiatives entry in its sidebar, listing the initiatives it leads. Initiatives led by a private team are visible only to its members."
    >
      {teams.length ? (
        teams.map((t) => <TeamRow key={t.id} team={t} />)
      ) : (
        <EmptyState
          title="No teams yet"
          description="Create a team first; then choose whether its sidebar lists initiatives."
        />
      )}
    </SettingsSection>
  );
}

function TeamRow({ team }: { team: SettingsTeam }) {
  const { teams, onTeamsChanged } = useSettings();
  // Read the latest list when the save resolves, so two quick toggles both stick.
  const latest = React.useRef(teams);
  latest.current = teams;
  const id = `team-initiatives-${team.id}`;
  const setting = useSetting({
    value: Boolean(team.showInitiatives),
    save: async (show) => {
      await setTeamShowsInitiatives(team.id, show);
      onTeamsChanged(
        latest.current.map((t) =>
          t.id === team.id ? { ...t, showInitiatives: show } : t,
        ),
      );
    },
  });
  return (
    <SettingRow
      label={team.name}
      htmlFor={id}
      description={
        setting.value
          ? "Initiatives appear in this team's sidebar."
          : "Hidden from this team's sidebar."
      }
      status={setting.status}
    >
      <Switch
        id={id}
        aria-label={`Show initiatives in the ${team.name} sidebar`}
        checked={setting.value}
        onCheckedChange={(v) => void setting.set(v)}
      />
    </SettingRow>
  );
}
