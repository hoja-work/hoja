import * as React from "react";
import { ArrowLeft, CalendarDays, UserPlus, Users } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { Input, Textarea } from "../../../components/ui/input";
import { DropdownMenuItem } from "../../../components/ui/menu";
import { Select } from "../../../components/ui/select";
import { Checkbox } from "../../../components/ui/switch";
import { useSettings } from "../../context";
import {
  Badge,
  ConfirmAction,
  DangerZone,
  EmptyState,
  FormDialog,
  Notice,
  ResourceRow,
  SettingRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useSetting,
  useSettingText,
} from "../../ui";
import {
  archiveTeam,
  deleteTeam,
  depthOf,
  descendantIds,
  personLabel,
  setTeamMember,
  updateTeam,
  VISIBILITY_OPTIONS,
  type Person,
  type TeamRow,
  type Visibility,
} from "./api";
import { validateKey } from "./CreateTeamDialog";
import { PersonAvatar } from "./PersonAvatar";

const NO_PARENT = "__none__";
const ADMIN_ONLY = "Only workspace admins can change this.";

export function TeamDetail({
  team,
  teams,
  people,
  onBack,
  onChanged,
  onRemoved,
  onKeyChanged,
}: {
  team: TeamRow;
  teams: TeamRow[];
  people: Person[];
  onBack: () => void;
  /** Reload the list and the app sidebar after a change. */
  onChanged: () => Promise<void>;
  onRemoved: (message: string) => void;
  onKeyChanged: (key: string) => void;
}) {
  const { isAdmin, can, viewer, openMember, openTeamCycleSettings } =
    useSettings();
  const canManageMembers = can("member.invite");
  const membersCount = team.members.length;

  const name = useSettingText({
    value: team.name,
    validate: (v) => (v ? null : "Enter a team name."),
    save: async (next) => {
      await updateTeam(team.id, { name: next });
      await onChanged();
    },
  });
  const key = useSettingText({
    value: team.key,
    validate: (v) => {
      const problem = validateKey(v);
      if (problem) return problem;
      if (
        teams.some(
          (t) => t.id !== team.id && t.key.toUpperCase() === v.toUpperCase(),
        )
      )
        return `${v.toUpperCase()} is already used by another team.`;
      return null;
    },
    save: async (next) => {
      const upper = next.toUpperCase();
      await updateTeam(team.id, { key: upper });
      onKeyChanged(upper);
      await onChanged();
    },
  });
  const description = useSettingText({
    value: team.description || "",
    save: async (next) => {
      await updateTeam(team.id, { description: next });
      await onChanged();
    },
  });
  const visibility = useSetting<Visibility>({
    value: team.visibility,
    save: async (next) => {
      await updateTeam(team.id, { visibility: next });
      await onChanged();
    },
  });

  // Re-parenting has side effects (members join the new parent, the running cycle closes),
  // so a pick opens a confirmation instead of saving instantly.
  const [pendingParent, setPendingParent] = React.useState<string | null>(null);
  const blocked = descendantIds(teams, team.id);
  const subtreeHeight = Math.max(
    ...[...blocked].map(
      (id) => depthOf(teams, id) - depthOf(teams, team.id) + 1,
    ),
  );
  const parentOptions = [
    { value: NO_PARENT, label: "None" },
    ...teams
      .filter(
        (t) => !blocked.has(t.id) && depthOf(teams, t.id) + subtreeHeight <= 3,
      )
      .map((t) => ({ value: t.id, label: `${t.name} (${t.key})` })),
  ];
  const parentTeam = teams.find((t) => t.id === team.parentTeamId);
  const pendingParentTeam = teams.find((t) => t.id === pendingParent);

  const [adding, setAdding] = React.useState(false);
  const [removing, setRemoving] = React.useState<Person | null>(null);
  const [archiving, setArchiving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [deleteTasks, setDeleteTasks] = React.useState(false);

  return (
    <SettingsPage
      title={team.name}
      description={`${team.key} · ${membersCount} ${membersCount === 1 ? "member" : "members"}`}
      actions={
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          All teams
        </Button>
      }
    >
      <SettingsSection title="General">
        <SettingRow
          label="Name"
          htmlFor="team-name"
          status={name.status}
          locked={isAdmin ? undefined : ADMIN_ONLY}
        >
          <Input
            id="team-name"
            className="sm:w-64"
            disabled={!isAdmin}
            {...name.inputProps}
          />
        </SettingRow>
        <SettingRow
          label="Key"
          htmlFor="team-key"
          description={`New tasks are numbered ${team.key}-1, ${team.key}-2… Changing the key only affects new tasks; existing tasks keep their IDs, so links to them keep working.`}
          status={key.status}
          locked={isAdmin ? undefined : ADMIN_ONLY}
        >
          <Input
            id="team-key"
            className="font-mono sm:w-32"
            maxLength={8}
            spellCheck={false}
            autoComplete="off"
            disabled={!isAdmin}
            {...key.inputProps}
            onChange={(e) => {
              e.target.value = e.target.value.toUpperCase();
              key.inputProps.onChange(e);
            }}
          />
        </SettingRow>
        <SettingRow
          label="Description"
          htmlFor="team-description"
          description="Shown on the team page. Saved when you leave the field."
          status={description.status}
          locked={isAdmin ? undefined : ADMIN_ONLY}
          stacked
        >
          <Textarea
            id="team-description"
            rows={2}
            placeholder="What this team works on"
            disabled={!isAdmin}
            {...description.inputProps}
          />
        </SettingRow>
      </SettingsSection>

      <SettingsSection
        title="Access"
        description="Workspace admins always see every team."
      >
        <SettingRow
          label="Visibility"
          description={
            VISIBILITY_OPTIONS.find((o) => o.value === visibility.value)
              ?.description
          }
          status={visibility.status}
          locked={isAdmin ? undefined : ADMIN_ONLY}
        >
          <Select
            aria-label="Visibility"
            value={visibility.value}
            disabled={!isAdmin}
            onValueChange={(v) => void visibility.set(v as Visibility)}
            options={VISIBILITY_OPTIONS}
          />
        </SettingRow>
        <SettingRow
          label="Parent team"
          description={
            parentTeam
              ? `A sub-team of ${parentTeam.name}. Its members also belong to ${parentTeam.name}.`
              : "Nest this team under another one to form a department."
          }
          locked={isAdmin ? undefined : ADMIN_ONLY}
        >
          <Select
            aria-label="Parent team"
            value={team.parentTeamId || NO_PARENT}
            disabled={!isAdmin}
            onValueChange={(v) => {
              if (v !== (team.parentTeamId || NO_PARENT)) setPendingParent(v);
            }}
            options={parentOptions}
          />
        </SettingRow>
        <SettingRow
          label="Time zone"
          description="Cycles start and end at midnight here. Set when the team was created; it can't be changed."
        >
          <span className="text-base text-muted-foreground">
            {(team.timezone || "UTC").replace(/_/g, " ")}
          </span>
        </SettingRow>
      </SettingsSection>

      <SettingsSection
        title="Members"
        description="Only team members can be assigned this team's tasks."
        action={
          canManageMembers ? (
            <Button onClick={() => setAdding(true)}>
              <UserPlus className="size-3.5" aria-hidden="true" />
              Add people
            </Button>
          ) : undefined
        }
        bare
      >
        {!canManageMembers && (
          <Notice tone="locked">
            Only workspace admins, or people allowed to invite members, can
            change who is on this team.
          </Notice>
        )}
        <SettingsCard>
          {team.members.length ? (
            team.members.map((m) => (
              <ResourceRow
                key={m.id}
                leading={<PersonAvatar name={m.name} email={m.email} />}
                title={personLabel(m)}
                meta={m.name ? m.email : undefined}
                badges={m.id === viewer.id ? <Badge>You</Badge> : undefined}
                menuLabel={`Actions for ${personLabel(m)}`}
                actions={
                  <>
                    <DropdownMenuItem onSelect={() => openMember(m.id)}>
                      View profile
                    </DropdownMenuItem>
                    {canManageMembers && (
                      <DropdownMenuItem
                        destructive
                        onSelect={() => setRemoving(m)}
                      >
                        Remove from team…
                      </DropdownMenuItem>
                    )}
                  </>
                }
              />
            ))
          ) : (
            <EmptyState
              icon={<Users />}
              title="No one is on this team yet"
              description="Add people so they can be assigned its tasks."
              action={
                canManageMembers ? (
                  <Button onClick={() => setAdding(true)}>Add people</Button>
                ) : undefined
              }
            />
          )}
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title="Cycles">
        <SettingRow
          label="Cycle settings"
          description={`Automatic cycles, start day, length, cooldown and automations for ${team.name}.`}
        >
          <Button
            onClick={() =>
              openTeamCycleSettings({
                id: team.id,
                name: team.name,
                key: team.key,
              })
            }
          >
            <CalendarDays className="size-3.5" aria-hidden="true" />
            Open cycle settings
          </Button>
        </SettingRow>
      </SettingsSection>

      <DangerZone>
        <SettingRow
          label="Archive team"
          description="Hides the team from the sidebar and team lists. Its tasks and projects stay. There is no way to unarchive a team from Hoja yet."
          locked={
            isAdmin ? undefined : "Only workspace admins can archive teams."
          }
        >
          <Button disabled={!isAdmin} onClick={() => setArchiving(true)}>
            Archive…
          </Button>
        </SettingRow>
        <SettingRow
          label="Delete team"
          description="Permanently deletes the team, its cycles, labels and workflow states. Projects stay in the workspace without it."
          locked={
            isAdmin ? undefined : "Only workspace admins can delete teams."
          }
        >
          <Button
            variant="destructive"
            disabled={!isAdmin}
            onClick={() => {
              setDeleteTasks(false);
              setDeleting(true);
            }}
          >
            Delete…
          </Button>
        </SettingRow>
      </DangerZone>

      <AddMembersDialog
        open={adding}
        onOpenChange={setAdding}
        team={team}
        people={people}
        onAdded={onChanged}
      />
      <ConfirmAction
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing ? personLabel(removing) : ""} from ${team.name}?`}
        description={`They stay in the workspace and keep their tasks, but can no longer be assigned ${team.key} tasks. If they are on a sub-team, remove them there first.`}
        confirmLabel="Remove from team"
        onConfirm={async () => {
          if (!removing) return;
          await setTeamMember(team.id, removing.id, false);
          await onChanged();
        }}
      />
      <ConfirmAction
        open={pendingParent !== null}
        onOpenChange={(o) => !o && setPendingParent(null)}
        title={
          pendingParent === NO_PARENT
            ? `Make ${team.name} a top-level team?`
            : `Move ${team.name} under ${pendingParentTeam?.name || "this team"}?`
        }
        description={
          pendingParent === NO_PARENT
            ? `${team.name} leaves ${parentTeam?.name || "its department"}. Labels it used from there become its own copies.`
            : `Everyone on ${team.name} and its sub-teams also joins ${pendingParentTeam?.name}. The running cycle closes now, and upcoming cycles line up with ${pendingParentTeam?.name}'s.`
        }
        confirmLabel={
          pendingParent === NO_PARENT ? "Make top-level" : "Move team"
        }
        destructive={false}
        onConfirm={async () => {
          await updateTeam(team.id, {
            parentTeamId: pendingParent === NO_PARENT ? null : pendingParent,
          });
          await onChanged();
        }}
      />
      <ConfirmAction
        open={archiving}
        onOpenChange={setArchiving}
        title={`Archive ${team.name}?`}
        description="It disappears from the sidebar and team lists for everyone. Its tasks and projects stay. Archiving can't be undone from Hoja yet; ask support if you need it back."
        confirmLabel="Archive team"
        onConfirm={async () => {
          await archiveTeam(team.id);
          onRemoved(`${team.name} archived`);
        }}
      />
      <ConfirmAction
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${team.name}?`}
        typeToConfirm={team.key}
        description={
          <>
            This can't be undone. The team, its cycles, labels and workflow
            states are deleted for everyone. Projects stay in the workspace
            without it. A team that still has tasks, cycles or projects is only
            deleted if you also delete its tasks.
            <span className="mt-3 flex items-start gap-2 text-foreground">
              <Checkbox
                id="team-delete-tasks"
                className="mt-0.5"
                checked={deleteTasks}
                onCheckedChange={(v) => setDeleteTasks(v === true)}
              />
              <label htmlFor="team-delete-tasks" className="cursor-pointer">
                Also delete this team's tasks, with their comments and
                attachments
              </label>
            </span>
          </>
        }
        confirmLabel={deleteTasks ? "Delete team and tasks" : "Delete team"}
        onConfirm={async () => {
          await deleteTeam(team.id, deleteTasks);
          onRemoved(`${team.name} deleted`);
        }}
      />
    </SettingsPage>
  );
}

function AddMembersDialog({
  open,
  onOpenChange,
  team,
  people,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: TeamRow;
  people: Person[];
  onAdded: () => Promise<void>;
}) {
  const [picked, setPicked] = React.useState<string[]>([]);
  React.useEffect(() => {
    if (open) setPicked([]);
  }, [open]);
  const candidates = people.filter(
    (p) => !team.members.some((m) => m.id === p.id),
  );
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Add people to ${team.name}`}
      description={
        team.parentTeamId
          ? "They also join the teams above this one."
          : "They can be assigned this team's tasks right away."
      }
      submitLabel={
        picked.length > 1 ? `Add ${picked.length} people` : "Add to team"
      }
      canSubmit={picked.length > 0}
      onSubmit={async () => {
        const failed: string[] = [];
        let lastError: unknown = null;
        for (const id of picked) {
          try {
            await setTeamMember(team.id, id, true);
          } catch (e) {
            lastError = e;
            failed.push(id);
          }
        }
        await onAdded();
        if (failed.length) {
          setPicked(failed);
          throw lastError;
        }
      }}
    >
      {candidates.length ? (
        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className="sr-only">People to add</legend>
          <div className="grid max-h-72 gap-0.5 overflow-y-auto rounded-lg border border-line p-1">
            {candidates.map((p) => {
              const id = `team-add-${p.id}`;
              return (
                <label
                  key={p.id}
                  htmlFor={id}
                  className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-base hover:bg-subtle pointer-coarse:min-h-11"
                >
                  <Checkbox
                    id={id}
                    checked={picked.includes(p.id)}
                    onCheckedChange={(on) =>
                      setPicked((c) =>
                        on ? [...c, p.id] : c.filter((x) => x !== p.id),
                      )
                    }
                  />
                  <span className="truncate">{personLabel(p)}</span>
                  {p.name && p.email && (
                    <span className="truncate text-sm text-muted-foreground">
                      {p.email}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        </fieldset>
      ) : (
        <Notice>
          Everyone in the workspace is already on this team. Invite someone from
          Members first.
        </Notice>
      )}
    </FormDialog>
  );
}
