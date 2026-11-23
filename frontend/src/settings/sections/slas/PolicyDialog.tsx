import * as React from "react";
import { Input } from "../../../components/ui/input";
import { Select } from "../../../components/ui/select";
import { Checkbox } from "../../../components/ui/switch";
import { Field, FormDialog, Notice } from "../../ui";
import {
  MAX_MINUTES,
  PRIORITIES,
  STOPS_ON,
  savePolicy,
  toInput,
  type SlaInput,
  type SlaPolicy,
  type SlaTeam,
} from "./model";

type Unit = "minutes" | "hours" | "days";
const UNIT_MINUTES: Record<Unit, number> = {
  minutes: 1,
  hours: 60,
  days: 1440,
};
type Duration = { amount: string; unit: Unit };

function fromMinutes(m: number | null | undefined): Duration {
  if (!m) return { amount: "", unit: "hours" };
  if (m % 1440 === 0) return { amount: String(m / 1440), unit: "days" };
  if (m % 60 === 0) return { amount: String(m / 60), unit: "hours" };
  return { amount: String(m), unit: "minutes" };
}
function toMinutes(d: Duration): number | null {
  if (!d.amount.trim()) return null;
  return Math.round(Number(d.amount) * UNIT_MINUTES[d.unit]);
}
function durationError(d: Duration): string | null {
  if (!d.amount.trim()) return null;
  const n = Number(d.amount);
  if (!Number.isFinite(n) || n <= 0) return "Enter a number above zero.";
  const m = toMinutes(d)!;
  if (m < 1) return "The shortest target is one minute.";
  if (m > MAX_MINUTES) return "The longest target is 365 days.";
  return null;
}

function DurationInput({
  id,
  label,
  value,
  onChange,
  invalid,
}: {
  id: string;
  label: string;
  value: Duration;
  onChange: (d: Duration) => void;
  invalid?: boolean;
}) {
  return (
    <div className="flex min-w-0 gap-2">
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        min={0}
        step="any"
        placeholder="None"
        className="min-w-0 flex-1"
        value={value.amount}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange({ ...value, amount: e.target.value })}
      />
      <Select
        aria-label={`${label} unit`}
        value={value.unit}
        onValueChange={(u) => onChange({ ...value, unit: u as Unit })}
        className="w-28 flex-none sm:w-28"
        options={[
          { value: "minutes", label: "Minutes" },
          { value: "hours", label: "Hours" },
          { value: "days", label: "Days" },
        ]}
      />
    </div>
  );
}

function CheckRow({
  checked,
  onChange,
  children,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: React.ReactNode;
  hint?: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <div className="flex items-start gap-2.5">
      <Checkbox
        id={id}
        className="mt-0.5"
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
      />
      <label htmlFor={id} className="grid gap-0.5 text-base">
        {children}
        {hint && <span className="text-sm text-muted-foreground">{hint}</span>}
      </label>
    </div>
  );
}

function ChipGroup<T extends string | number>({
  legend,
  options,
  selected,
  onToggle,
  empty,
  hint,
}: {
  hint?: React.ReactNode;
  legend: string;
  options: { value: T; label: string }[];
  selected: T[];
  onToggle: (v: T) => void;
  empty?: string;
}) {
  return (
    <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
      <legend className="mb-1.5 p-0 text-sm font-medium">{legend}</legend>
      {options.length ? (
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {options.map((o) => (
            <CheckRow
              key={String(o.value)}
              checked={selected.includes(o.value)}
              onChange={() => onToggle(o.value)}
            >
              {o.label}
            </CheckRow>
          ))}
        </div>
      ) : (
        <p className="m-0 text-sm text-muted-foreground">{empty}</p>
      )}
      {hint && <p className="m-0 text-sm text-muted-foreground">{hint}</p>}
    </fieldset>
  );
}

const WORKSPACE = "workspace";

export function PolicyDialog({
  open,
  onOpenChange,
  policy,
  teams,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  policy?: SlaPolicy;
  teams: SlaTeam[];
  onSaved: (p: SlaPolicy, started: number) => void;
}) {
  const ids = {
    name: React.useId(),
    scope: React.useId(),
    first: React.useId(),
    resolution: React.useId(),
    stops: React.useId(),
  };
  const [name, setName] = React.useState("");
  const [scope, setScope] = React.useState(WORKSPACE);
  const [first, setFirst] = React.useState<Duration>(fromMinutes(null));
  const [resolution, setResolution] = React.useState<Duration>(
    fromMinutes(null),
  );
  const [priorities, setPriorities] = React.useState<number[]>([]);
  const [pauses, setPauses] = React.useState<string[]>([]);
  const [cycleBound, setCycleBound] = React.useState(false);
  const [weekdays, setWeekdays] = React.useState(false);
  const [stopsOn, setStopsOn] = React.useState("completed");
  const [applyToOpen, setApplyToOpen] = React.useState(true);
  const [touched, setTouched] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    const i = policy ? toInput(policy) : null;
    setName(i?.name || "");
    setScope(i?.teamId || WORKSPACE);
    setFirst(fromMinutes(i?.firstResponseMinutes));
    setResolution(fromMinutes(i?.resolutionMinutes));
    setPriorities(i?.appliesPriorities || []);
    setPauses(i?.pauseStateIds || []);
    setCycleBound(!!i?.cycleBound);
    setWeekdays(!!i?.businessDaysOnly);
    setStopsOn(i?.stopsOn || "completed");
    // Editing is not consent to start clocks on every open task; creating defaults to it.
    setApplyToOpen(!policy);
    setTouched(false);
  }, [open, policy]);

  const scopeTeams = teams.filter((t) => scope === WORKSPACE || t.id === scope);
  const stateOptions = scopeTeams.flatMap((t) =>
    t.states.nodes.map((s) => ({
      value: s.id,
      label:
        scope === WORKSPACE && teams.length > 1
          ? `${s.name} (${t.key})`
          : s.name,
    })),
  );
  const firstError = durationError(first);
  const resolutionError = durationError(resolution);
  const noTarget = !first.amount.trim() && !resolution.amount.trim();
  const nameError = touched && !name.trim() ? "Give the policy a name." : null;
  const toggle = <T,>(list: T[], v: T) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  return (
    <FormDialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title={policy ? `Edit “${policy.name}”` : "New SLA policy"}
      description="Set how quickly covered tasks need a first response and a resolution. Leave a target empty to skip it."
      submitLabel={policy ? "Save policy" : "Create policy"}
      canSubmit={!!name.trim() && !noTarget && !firstError && !resolutionError}
      onSubmit={async () => {
        const input: SlaInput = {
          name: name.trim(),
          teamId: scope === WORKSPACE ? null : scope,
          appliesPriorities: priorities,
          firstResponseMinutes: toMinutes(first),
          resolutionMinutes: toMinutes(resolution),
          pauseStateIds: pauses,
          cycleBound,
          stopsOn,
          businessDaysOnly: weekdays,
          // Send the current state so a save never flips it (HOJA-734).
          enabled: policy ? policy.enabled : true,
          applyToOpen,
        };
        const { policy: saved, started } = await savePolicy(
          policy?.id || null,
          input,
        );
        onSaved(saved, started);
      }}
    >
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor={ids.name} error={nameError}>
          <Input
            id={ids.name}
            autoFocus
            autoComplete="off"
            maxLength={80}
            value={name}
            placeholder="e.g. Customer bugs"
            aria-invalid={!!nameError || undefined}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched(true)}
          />
        </Field>
        <Field label="Applies to" htmlFor={ids.scope}>
          <Select
            id={ids.scope}
            value={scope}
            className="sm:w-full"
            onValueChange={(v) => {
              setScope(v);
              // States belong to teams; a narrower scope cannot keep another team's.
              const keep = new Set(
                teams
                  .filter((t) => v === WORKSPACE || t.id === v)
                  .flatMap((t) => t.states.nodes.map((s) => s.id)),
              );
              setPauses((p) => p.filter((id) => keep.has(id)));
            }}
            options={[
              { value: WORKSPACE, label: "Every team" },
              ...teams.map((t) => ({ value: t.id, label: `${t.name} only` })),
            ]}
          />
        </Field>
      </div>

      <div
        className="grid items-start gap-4 sm:grid-cols-2"
        role="group"
        aria-label="Targets"
      >
        <Field
          label="First response within"
          htmlFor={ids.first}
          error={firstError}
          help="Until someone other than the author comments."
        >
          <DurationInput
            id={ids.first}
            label="First response"
            value={first}
            onChange={setFirst}
            invalid={!!firstError}
          />
        </Field>
        <Field
          label={
            cycleBound ? "Resolve within (outside a cycle)" : "Resolve within"
          }
          htmlFor={ids.resolution}
          error={resolutionError}
          help={
            cycleBound
              ? "Tasks in a cycle are due when the cycle ends."
              : "Until the clock stops."
          }
        >
          <DurationInput
            id={ids.resolution}
            label="Resolution"
            value={resolution}
            onChange={setResolution}
            invalid={!!resolutionError}
          />
        </Field>
      </div>
      {noTarget && touched && (
        <p role="alert" className="m-0 text-sm text-destructive">
          Set at least one target.
        </p>
      )}

      <ChipGroup
        legend="Priorities"
        options={PRIORITIES}
        selected={priorities}
        onToggle={(v) => setPriorities((p) => toggle(p, v))}
        hint={
          priorities.length
            ? "Only tasks with these priorities get a clock."
            : "None ticked: every task gets a clock, whatever its priority."
        }
      />

      <Field label="The clock stops when" htmlFor={ids.stops}>
        <Select
          id={ids.stops}
          value={stopsOn}
          className="sm:w-full"
          onValueChange={setStopsOn}
          options={STOPS_ON.map((s) => ({ value: s.value, label: s.label }))}
        />
      </Field>

      <ChipGroup
        legend="Pause while the task is in"
        options={stateOptions}
        selected={pauses}
        onToggle={(v) => setPauses((p) => toggle(p, v))}
        empty="This scope has no workflow states yet."
      />

      <div className="grid gap-3">
        <CheckRow
          checked={cycleBound}
          onChange={setCycleBound}
          hint="A task in a cycle is due when its cycle ends."
        >
          Follow the task’s cycle
        </CheckRow>
        <CheckRow
          checked={weekdays}
          onChange={setWeekdays}
          hint="Weekends don’t count, in the team’s timezone."
        >
          Count weekdays only
        </CheckRow>
        <CheckRow
          checked={applyToOpen}
          onChange={setApplyToOpen}
          hint={
            policy
              ? "Starts a clock now for open tasks this policy covers that don’t have one."
              : "Without this, only tasks created from now on get a clock."
          }
        >
          Start clocks for tasks that are already open
        </CheckRow>
      </div>
      {policy && !policy.enabled && (
        <Notice>
          This policy is turned off. Saving keeps it off; turn it on from the
          list when you are ready.
        </Notice>
      )}
    </FormDialog>
  );
}
