import * as React from "react";
import { Archive, Clock, PenLine, Play, Plus, Sparkles } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { Switch } from "../../components/ui/switch";
import { toast } from "../../components/ui/toast";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  ResourceRow,
  SaveStatusLine,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  errorMessage,
  gql,
  useResource,
  useSetting,
} from "../ui";
import {
  STOPS_ON,
  cyclePreset,
  formatMinutes,
  loadSlas,
  priorityList,
  savePolicy,
  toInput,
  type SlaPolicy,
  type SlaTeam,
} from "./slas/model";
import { PolicyDialog } from "./slas/PolicyDialog";

const clocks = (n: number) => `${n} ${n === 1 ? "clock" : "clocks"}`;

/* SLAs (HOJA-491, HOJA-681). Each policy is one card: what it covers, the two targets side
 * by side, and the rules that move its clock. Enabled is an instant switch; everything else
 * is edited together in one dialog because the targets only make sense as a set. */
export default function SlaSettings() {
  const data = useResource(loadSlas, []);
  const [creating, setCreating] = React.useState(false);
  const [presetOpen, setPresetOpen] = React.useState(false);

  const upsert = (p: SlaPolicy) =>
    data.setData((d) =>
      d
        ? {
            ...d,
            policies: d.policies.some((x) => x.id === p.id)
              ? d.policies.map((x) => (x.id === p.id ? p : x))
              : [...d.policies, p],
          }
        : d,
    );
  const remove = (id: string) =>
    data.setData((d) =>
      d ? { ...d, policies: d.policies.filter((x) => x.id !== id) } : d,
    );
  const announceSaved = (p: SlaPolicy, started: number) => {
    upsert(p);
    if (started)
      toast({
        title: `Started ${clocks(started)} on open tasks for “${p.name}”`,
      });
  };

  return (
    <SettingsPage
      title="SLAs"
      description="Give tasks a deadline for a first response and for a resolution. A task's clock starts when it is created, pauses in the states you choose and stops when the work is done. Breaches show on the task."
      actions={
        data.data && data.data.policies.length > 0 ? (
          <>
            <Button onClick={() => setPresetOpen(true)}>
              <Sparkles className="size-3.5" aria-hidden="true" />
              Cycle preset…
            </Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus className="size-3.5" aria-hidden="true" />
              New policy
            </Button>
          </>
        ) : undefined
      }
    >
      <Loadable
        resource={data}
        rows={3}
        isEmpty={(d) => d.policies.length === 0}
        empty={
          <EmptyState
            icon={<Clock />}
            title="No SLA policies yet"
            description="Nothing is being measured. Create a policy for the work that has a deadline, or start from the cycle preset: one policy per priority, paused while tasks sit in Backlog."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Button onClick={() => setPresetOpen(true)}>
                  <Sparkles className="size-3.5" aria-hidden="true" />
                  Use the cycle preset…
                </Button>
                <Button variant="primary" onClick={() => setCreating(true)}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  Create a policy
                </Button>
              </div>
            }
          />
        }
      >
        {(d) => (
          <SettingsSection
            bare
            title="Policies"
            description="When several policies cover a task, a team's own policy wins over a workspace one, then the first by name. Turned-off policies are skipped."
          >
            <div className="grid gap-3">
              {sortPolicies(d.policies).map((p) => (
                <PolicyCard
                  key={p.id}
                  policy={p}
                  teams={d.teams}
                  onSaved={announceSaved}
                  onArchived={() => remove(p.id)}
                />
              ))}
            </div>
          </SettingsSection>
        )}
      </Loadable>
      <PolicyDialog
        open={creating}
        onOpenChange={setCreating}
        teams={data.data?.teams || []}
        onSaved={announceSaved}
      />
      <PresetConfirm
        open={presetOpen}
        onOpenChange={setPresetOpen}
        teams={data.data?.teams || []}
        existing={data.data?.policies || []}
        onDone={() => void data.reload()}
      />
    </SettingsPage>
  );
}

function sortPolicies(list: SlaPolicy[]) {
  return [...list].sort(
    (a, b) =>
      (a.team?.name || "").localeCompare(b.team?.name || "") ||
      Math.min(...(a.appliesPriorities.length ? a.appliesPriorities : [9])) -
        Math.min(...(b.appliesPriorities.length ? b.appliesPriorities : [9])) ||
      a.name.localeCompare(b.name),
  );
}

function PolicyCard({
  policy,
  teams,
  onSaved,
  onArchived,
}: {
  policy: SlaPolicy;
  teams: SlaTeam[];
  onSaved: (p: SlaPolicy, started: number) => void;
  onArchived: () => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [archiving, setArchiving] = React.useState(false);
  const [applying, setApplying] = React.useState(false);
  const enabled = useSetting<boolean>({
    value: policy.enabled,
    save: async (next) => {
      const { policy: saved } = await savePolicy(policy.id, {
        ...toInput(policy),
        enabled: next,
      });
      onSaved(saved, 0);
    },
  });
  const stop =
    STOPS_ON.find((s) => s.value === (policy.stopsOn || "completed")) ||
    STOPS_ON[0];
  const resolution = policy.cycleBound
    ? policy.resolutionMinutes
      ? `End of cycle, else ${formatMinutes(policy.resolutionMinutes)}`
      : "End of cycle"
    : formatMinutes(policy.resolutionMinutes);
  return (
    <SettingsCard>
      <div data-sla-policy={policy.id}>
        <ResourceRow
          title={policy.name}
          badges={
            <>
              <Badge>{policy.team ? policy.team.key : "All teams"}</Badge>
              {!enabled.value && <Badge>Off</Badge>}
            </>
          }
          meta={
            enabled.status.state === "idle" ? (
              priorityList(policy.appliesPriorities)
            ) : (
              <SaveStatusLine status={enabled.status} />
            )
          }
          menuLabel={`Actions for ${policy.name}`}
          actions={
            <>
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <PenLine aria-hidden="true" />
                Edit…
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!policy.enabled}
                onSelect={() => setApplying(true)}
              >
                <Play aria-hidden="true" />
                Start clocks on open tasks…
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setArchiving(true)}>
                <Archive aria-hidden="true" />
                Archive…
              </DropdownMenuItem>
            </>
          }
        >
          <Switch
            aria-label={`${policy.name} enabled`}
            checked={enabled.value}
            disabled={enabled.saving}
            onCheckedChange={(v) => void enabled.set(v)}
          />
        </ResourceRow>
        <dl
          className={
            "m-0 grid grid-cols-2 border-t border-line" +
            (enabled.value ? "" : " opacity-60")
          }
        >
          <div className="grid gap-0.5 px-(--ds-settings-row-padding) py-3">
            <dt className="text-sm text-muted-foreground">First response</dt>
            <dd className="m-0 text-base font-medium">
              {formatMinutes(policy.firstResponseMinutes)}
            </dd>
          </div>
          <div className="grid gap-0.5 border-l border-line px-(--ds-settings-row-padding) py-3">
            <dt className="text-sm text-muted-foreground">Resolution</dt>
            <dd className="m-0 text-base font-medium">{resolution}</dd>
          </div>
        </dl>
        <p className="m-0 border-t border-line px-(--ds-settings-row-padding) py-2.5 text-sm text-muted-foreground">
          Stops {stop.short}
          {" · "}
          {policy.pauseStates.length
            ? `Pauses in ${policy.pauseStates.map((s) => s.name).join(", ")}`
            : "Never pauses"}
          {" · "}
          {policy.businessDaysOnly ? "Weekdays only" : "Counts every day"}
        </p>
      </div>
      <PolicyDialog
        open={editing}
        onOpenChange={setEditing}
        policy={policy}
        teams={teams}
        onSaved={onSaved}
      />
      <ConfirmAction
        open={applying}
        onOpenChange={setApplying}
        destructive={false}
        title={`Start clocks for “${policy.name}”?`}
        description="Every open task this policy covers that has no clock yet gets one, starting now. Tasks that already have a clock keep it."
        confirmLabel="Start clocks"
        onConfirm={async () => {
          const d = await gql<{
            slaPolicyApply: { startedClocks: number | null } | null;
          }>(
            "mutation SlaApply($id:String!){slaPolicyApply(id:$id){success startedClocks}}",
            { id: policy.id },
          );
          const n = d.slaPolicyApply?.startedClocks || 0;
          toast({
            title: n
              ? `Started ${clocks(n)} for “${policy.name}”`
              : `Every open task “${policy.name}” covers already has a clock`,
          });
        }}
      />
      <ConfirmAction
        open={archiving}
        onOpenChange={setArchiving}
        title={`Archive “${policy.name}”?`}
        description="New tasks stop getting this clock. Tasks that already have one keep counting against it."
        confirmLabel="Archive policy"
        onConfirm={async () => {
          const d = await gql<{ slaPolicyArchive: boolean }>(
            "mutation SlaArchive($id:String!){slaPolicyArchive(id:$id)}",
            { id: policy.id },
          );
          if (!d.slaPolicyArchive)
            throw new Error(
              "This policy was already archived. Reload to see the current list.",
            );
          onArchived();
        }}
      />
    </SettingsCard>
  );
}

function PresetConfirm({
  open,
  onOpenChange,
  teams,
  existing,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: SlaTeam[];
  existing: SlaPolicy[];
  onDone: () => void;
}) {
  const taken = new Set(
    existing.filter((p) => !p.team).map((p) => p.name.toLowerCase()),
  );
  const preset = cyclePreset(teams);
  const todo = preset.filter((p) => !taken.has(p.name.toLowerCase()));
  const skipped = preset.length - todo.length;
  return (
    <ConfirmAction
      open={open}
      onOpenChange={onOpenChange}
      destructive={false}
      title={
        todo.length
          ? `Create ${todo.length} SLA ${todo.length === 1 ? "policy" : "policies"}?`
          : "The cycle preset is already set up"
      }
      description={
        todo.length ? (
          <>
            Adds {todo.map((p) => p.name).join(", ")} for every team, each
            paused while a task is in Backlog. Clocks start right away on every
            open task they cover, so some may already be late.
            {skipped > 0 &&
              ` ${skipped} ${skipped === 1 ? "policy" : "policies"} with the same name already exist and are left as they are.`}
          </>
        ) : (
          "Workspace policies named Urgent, High, Medium, Low and No priority already exist. Edit them instead."
        )
      }
      confirmLabel={todo.length ? "Create policies" : "Close"}
      onConfirm={async () => {
        if (!todo.length) return;
        let made = 0;
        let started = 0;
        const failures: string[] = [];
        for (const input of todo) {
          try {
            const r = await savePolicy(null, input);
            made += 1;
            started += r.started;
          } catch (e) {
            failures.push(`${input.name}: ${errorMessage(e)}`);
          }
        }
        onDone();
        if (!made) throw new Error(failures.join(" "));
        toast({
          title: `Created ${made} ${made === 1 ? "policy" : "policies"} and started ${clocks(started)}`,
          description: failures.length
            ? `Not created: ${failures.join(" ")}`
            : undefined,
        });
      }}
    />
  );
}
