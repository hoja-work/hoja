import * as React from "react";
import {
  Checkbox,
  RadioGroup,
  RadioGroupItem,
} from "../../../components/ui/switch";
import { FormDialog, Notice } from "../../ui";
import { setTeamMember, type TeamRow } from "../teams/api";
import { memberLabel, setRole, type Member } from "./api";

export function RoleDialog({
  member,
  onOpenChange,
  isSelf,
  lastActiveAdmin,
  onSaved,
}: {
  member: Member | null;
  onOpenChange: (open: boolean) => void;
  isSelf: boolean;
  /** The server's own guard also counts deactivated admins, so the UI checks active ones. */
  lastActiveAdmin: boolean;
  onSaved: () => Promise<void>;
}) {
  const current = member?.admin ? "admin" : "member";
  const [role, setRoleValue] = React.useState(current);
  React.useEffect(() => setRoleValue(current), [current, member?.id]);
  const demotingLast =
    lastActiveAdmin && current === "admin" && role === "member";
  const who = member ? memberLabel(member) : "";
  return (
    <FormDialog
      open={!!member}
      onOpenChange={onOpenChange}
      title={`Change role for ${who}`}
      submitLabel="Change role"
      canSubmit={role !== current && !demotingLast}
      onSubmit={async () => {
        if (!member) return;
        await setRole(member.id, role === "admin");
        await onSaved();
      }}
    >
      <RadioGroup
        aria-label="Role"
        value={role}
        onValueChange={setRoleValue}
        className="grid gap-2"
      >
        {(
          [
            [
              "member",
              "Member",
              "Works in the teams they belong to. Can't change workspace settings.",
            ],
            [
              "admin",
              "Admin",
              "Full access: workspace settings, members, roles and every team, including private ones.",
            ],
          ] as const
        ).map(([value, label, help]) => (
          <label
            key={value}
            htmlFor={`role-${value}`}
            className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-3 has-data-[state=checked]:border-primary"
          >
            <RadioGroupItem
              id={`role-${value}`}
              value={value}
              className="mt-0.5"
            />
            <span className="grid gap-0.5">
              <span className="text-base font-medium">{label}</span>
              <span className="text-sm text-muted-foreground">{help}</span>
            </span>
          </label>
        ))}
      </RadioGroup>
      {demotingLast && (
        <Notice tone="warning">
          {who} is the only active admin. Make someone else an admin first, so
          the workspace is never left without one.
        </Notice>
      )}
      {!demotingLast && isSelf && current === "admin" && role === "member" && (
        <Notice tone="warning">
          You will lose access to admin settings as soon as you save.
        </Notice>
      )}
    </FormDialog>
  );
}

export function TeamsDialog({
  member,
  onOpenChange,
  teams,
  onSaved,
}: {
  member: Member | null;
  onOpenChange: (open: boolean) => void;
  teams: TeamRow[];
  onSaved: () => Promise<void>;
}) {
  const initial = React.useMemo(
    () =>
      member
        ? teams
            .filter((t) => t.members.some((m) => m.id === member.id))
            .map((t) => t.id)
        : [],
    [member, teams],
  );
  const [picked, setPicked] = React.useState<string[]>(initial);
  React.useEffect(() => setPicked(initial), [member?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const added = picked.filter((id) => !initial.includes(id));
  const removed = initial.filter((id) => !picked.includes(id));
  const keyOf = (id: string) => teams.find((t) => t.id === id)?.key || id;
  const who = member ? memberLabel(member) : "";
  return (
    <FormDialog
      open={!!member}
      onOpenChange={onOpenChange}
      title={`Teams for ${who}`}
      description="Only team members can be assigned a team's tasks. Joining a sub-team also joins the team above it."
      submitLabel="Save teams"
      canSubmit={added.length + removed.length > 0}
      onSubmit={async () => {
        if (!member) return;
        let failure: unknown = null;
        for (const id of added)
          await setTeamMember(id, member.id, true).catch((e) => (failure = e));
        for (const id of removed)
          await setTeamMember(id, member.id, false).catch((e) => (failure = e));
        await onSaved();
        if (failure) throw failure;
      }}
    >
      {teams.length ? (
        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className="sr-only">Teams</legend>
          <div className="grid max-h-72 gap-0.5 overflow-y-auto rounded-lg border border-line p-1">
            {teams.map((t) => {
              const id = `member-team-${t.id}`;
              return (
                <label
                  key={t.id}
                  htmlFor={id}
                  className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-base hover:bg-subtle pointer-coarse:min-h-11"
                >
                  <Checkbox
                    id={id}
                    checked={picked.includes(t.id)}
                    onCheckedChange={(on) =>
                      setPicked((c) =>
                        on ? [...c, t.id] : c.filter((x) => x !== t.id),
                      )
                    }
                  />
                  <span className="truncate">{t.name}</span>
                  <span className="text-sm text-muted-foreground">{t.key}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ) : (
        <Notice>There are no teams yet. Create one under Teams.</Notice>
      )}
      {removed.length > 0 && (
        <Notice tone="warning">
          {who} will be removed from {removed.map(keyOf).join(", ")} and can no
          longer be assigned {removed.length === 1 ? "its" : "their"} tasks.
          Their existing tasks stay assigned.
        </Notice>
      )}
    </FormDialog>
  );
}
