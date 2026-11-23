import * as React from "react";
import { ArrowUpRight, CalendarDays, Users } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Select } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import { useSettings } from "../context";
import {
  Badge,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../ui";

const DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

type Cadence = { enabled: boolean; weekday: number | null; timezone: string };
type AutoKey = "autoAddStarted" | "autoAddCompleted" | "autoAddBacklog";
type CycleTeam = {
  id: string;
  name: string;
  key: string;
  timezone: string | null;
} & Record<AutoKey, boolean | null>;
type Row = { team: CycleTeam; cadence: Cadence | null; error: string | null };

async function loadCycles(): Promise<Row[]> {
  const d = await gql<{ teams: { nodes: CycleTeam[] } }>(
    "query CycleTeams{teams(first:100){nodes{id name key timezone autoAddStarted autoAddCompleted autoAddBacklog}}}",
  );
  const teams = d.teams.nodes;
  // One request per team, so one team's failure does not blank the whole page.
  const cadences = await Promise.allSettled(
    teams.map((t) =>
      gql<{ teamCycleCadence: Cadence }>(
        "query TeamCadence($id:String!){teamCycleCadence(teamId:$id){enabled weekday timezone}}",
        { id: t.id },
      ),
    ),
  );
  return teams.map((team, i) => {
    const r = cadences[i];
    return r.status === "fulfilled"
      ? { team, cadence: r.value.teamCycleCadence, error: null }
      : {
          team,
          cadence: null,
          error:
            r.reason instanceof Error
              ? r.reason.message
              : "Couldn't load this team's schedule.",
        };
  });
}

const AUTOMATIONS: { key: AutoKey; label: string; description: string }[] = [
  {
    key: "autoAddStarted",
    label: "Add started tasks",
    description:
      "A task with no cycle joins the current cycle when work on it starts.",
  },
  {
    key: "autoAddCompleted",
    label: "Add completed tasks",
    description:
      "A task with no cycle joins the current cycle when it is completed, so finished work is counted.",
  },
  {
    key: "autoAddBacklog",
    label: "Add tasks moved to Backlog",
    description:
      "A task with no cycle joins the current cycle when it moves to a Backlog state.",
  },
];

/* Cycles are per team. This page shows every team's real schedule and the switches the
 * server actually stores. Length, cooldown and auto-complete are fixed by the server
 * (one week, no cooldown, close on the end date), so they are stated, not offered. */
export default function CycleSettings() {
  const { isAdmin, openSection } = useSettings();
  const data = useResource(loadCycles, []);
  const patch = (teamId: string, next: Partial<Row>) =>
    data.setData((rows) =>
      rows?.map((r) => (r.team.id === teamId ? { ...r, ...next } : r)),
    );
  return (
    <SettingsPage
      title="Cycles"
      description="Cycles are short, repeating stretches of work, set up per team. Hoja creates one-week cycles for a team, keeps the next two ready, and closes each one on its end date."
    >
      {!isAdmin && (
        <Notice tone="locked" title="Only workspace admins can change cycles">
          You can see how each team plans its cycles. Ask a workspace admin to
          turn cycles on or change what joins them.
        </Notice>
      )}
      <Loadable
        resource={data}
        rows={4}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState
            icon={<Users />}
            title="No teams yet"
            description="Cycles belong to a team. Create a team first, then turn on its cycles here."
            action={
              <Button onClick={() => openSection("teams")}>Open Teams</Button>
            }
          />
        }
      >
        {(rows) => (
          <>
            {rows.map((r) => (
              <TeamCycles
                key={r.team.id}
                row={r}
                canEdit={isAdmin}
                onPatch={(next) => patch(r.team.id, next)}
              />
            ))}
          </>
        )}
      </Loadable>
    </SettingsPage>
  );
}

function TeamCycles({
  row,
  canEdit,
  onPatch,
}: {
  row: Row;
  canEdit: boolean;
  onPatch: (next: Partial<Row>) => void;
}) {
  const { openTeamCycleSettings } = useSettings();
  const { team, cadence } = row;
  const [enabling, setEnabling] = React.useState(false);
  const on = !!cadence?.enabled;
  const locked = canEdit ? undefined : "Only workspace admins can change this.";
  return (
    <SettingsSection
      title={team.name}
      description={
        on
          ? `Cycles start every ${DAYS[cadence?.weekday ?? 0]}, ${cadence?.timezone || "UTC"} time.`
          : "No automatic cycles."
      }
      action={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => openTeamCycleSettings(team)}
        >
          {team.key} cycle page
          <ArrowUpRight className="size-3.5" aria-hidden="true" />
        </Button>
      }
    >
      <SettingRow
        label="Automatic cycles"
        description={
          row.error
            ? row.error
            : on
              ? `One-week cycles starting every ${DAYS[cadence?.weekday ?? 0]}. The current cycle and the next two always exist.`
              : `Hoja creates one-week cycles for ${team.name} on the day you choose: the current one and the next two, rolling forward every week.`
        }
        locked={
          on
            ? "Changing the start day or turning cycles off isn't available yet."
            : locked
        }
      >
        {on ? (
          <Badge tone="strong">On</Badge>
        ) : canEdit && !row.error ? (
          <Button onClick={() => setEnabling(true)}>
            <CalendarDays className="size-3.5" aria-hidden="true" />
            Turn on…
          </Button>
        ) : (
          <Badge>Off</Badge>
        )}
      </SettingRow>
      {AUTOMATIONS.map((a) => (
        <AutomationRow
          key={a.key}
          team={team}
          automation={a}
          canEdit={canEdit}
          locked={locked}
          onSaved={(v) => onPatch({ team: { ...team, [a.key]: v } })}
        />
      ))}
      <EnableDialog
        open={enabling}
        onOpenChange={setEnabling}
        team={team}
        onEnabled={(c) => onPatch({ cadence: c })}
      />
    </SettingsSection>
  );
}

function AutomationRow({
  team,
  automation,
  canEdit,
  locked,
  onSaved,
}: {
  team: CycleTeam;
  automation: (typeof AUTOMATIONS)[number];
  canEdit: boolean;
  locked?: string;
  onSaved: (v: boolean) => void;
}) {
  const id = React.useId();
  const setting = useSetting<boolean>({
    value: team[automation.key] !== false,
    save: async (next) => {
      const d = await gql<{ teamUpdate: { success: boolean } | null }>(
        "mutation CycleAutomation($id:String!,$input:TeamUpdateInput!){teamUpdate(id:$id,input:$input){success team{id autoAddStarted autoAddCompleted autoAddBacklog}}}",
        { id: team.id, input: { [automation.key]: next } },
      );
      if (!d.teamUpdate?.success)
        throw new Error("The change wasn't saved. Try again.");
      onSaved(next);
    },
  });
  return (
    <SettingRow
      label={automation.label}
      htmlFor={id}
      description={automation.description}
      status={setting.status}
      locked={locked}
    >
      <Switch
        id={id}
        aria-label={`${automation.label} to ${team.name}'s current cycle`}
        checked={setting.value}
        disabled={!canEdit}
        onCheckedChange={(v) => void setting.set(v)}
      />
    </SettingRow>
  );
}

function EnableDialog({
  open,
  onOpenChange,
  team,
  onEnabled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: CycleTeam;
  onEnabled: (c: Cadence) => void;
}) {
  const id = React.useId();
  const [weekday, setWeekday] = React.useState("0");
  React.useEffect(() => {
    if (open) setWeekday("0");
  }, [open]);
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Turn on cycles for ${team.name}`}
      description={`Hoja creates the current one-week cycle and the next two right away, in ${team.timezone || "UTC"} time.`}
      submitLabel="Turn on cycles"
      onSubmit={async () => {
        const d = await gql<{ teamCycleCadenceEnable: Cadence }>(
          "mutation CadenceEnable($id:String!,$weekday:Int!){teamCycleCadenceEnable(teamId:$id,weekday:$weekday){enabled weekday timezone}}",
          { id: team.id, weekday: Number(weekday) },
        );
        onEnabled(d.teamCycleCadenceEnable);
      }}
    >
      <Field label="Cycles start on" htmlFor={id}>
        <Select
          id={id}
          className="sm:w-full"
          value={weekday}
          onValueChange={setWeekday}
          options={DAYS.map((d, i) => ({ value: String(i), label: d }))}
        />
      </Field>
      <Notice tone="warning" title="This can't be undone yet">
        Once cycles are on, the start day can't be changed and cycles can't be
        turned off.
      </Notice>
    </FormDialog>
  );
}
