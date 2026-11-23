import { gql } from "../../ui";

export type SlaState = { id: string; name: string; type: string };
export type SlaTeam = {
  id: string;
  name: string;
  key: string;
  states: { nodes: SlaState[] };
};
export type SlaPolicy = {
  id: string;
  name: string;
  team: { id: string; key: string; name: string } | null;
  appliesPriorities: number[];
  firstResponseMinutes: number | null;
  resolutionMinutes: number | null;
  pauseStates: { id: string; name: string }[];
  enabled: boolean;
  cycleBound: boolean;
  stopsOn: string;
  businessDaysOnly: boolean;
};
export type SlaInput = {
  name: string;
  teamId: string | null;
  appliesPriorities: number[];
  firstResponseMinutes: number | null;
  resolutionMinutes: number | null;
  pauseStateIds: string[];
  cycleBound: boolean;
  stopsOn: string;
  businessDaysOnly: boolean;
  enabled: boolean;
  applyToOpen?: boolean;
};

const POLICY_FIELDS =
  "id name team{id key name} appliesPriorities firstResponseMinutes resolutionMinutes pauseStates{id name} enabled cycleBound stopsOn businessDaysOnly";

export const PRIORITIES = [
  { value: 1, label: "Urgent" },
  { value: 2, label: "High" },
  { value: 3, label: "Medium" },
  { value: 4, label: "Low" },
  { value: 0, label: "No priority" },
];

export const STOPS_ON = [
  {
    value: "completed",
    label: "The task is completed",
    short: "on completion",
  },
  { value: "started", label: "Work starts", short: "when work starts" },
  {
    value: "prioritized",
    label: "Someone sets a priority",
    short: "when a priority is set",
  },
];

export const MAX_MINUTES = 525600;

export async function loadSlas() {
  const d = await gql<{
    slaPolicies: SlaPolicy[];
    teams: { nodes: SlaTeam[] };
  }>(
    `query SlaSettings{slaPolicies{${POLICY_FIELDS}} teams(first:100){nodes{id name key states{nodes{id name type}}}}}`,
  );
  return { policies: d.slaPolicies, teams: d.teams.nodes };
}

export function toInput(p: SlaPolicy): SlaInput {
  return {
    name: p.name,
    teamId: p.team?.id || null,
    appliesPriorities: p.appliesPriorities,
    firstResponseMinutes: p.firstResponseMinutes,
    resolutionMinutes: p.resolutionMinutes,
    pauseStateIds: p.pauseStates.map((s) => s.id),
    cycleBound: p.cycleBound,
    stopsOn: p.stopsOn || "completed",
    businessDaysOnly: p.businessDaysOnly,
    enabled: p.enabled,
  };
}

type Payload = {
  success: boolean;
  slaPolicy: SlaPolicy | null;
  startedClocks: number | null;
};

export async function savePolicy(id: string | null, input: SlaInput) {
  const d = id
    ? await gql<{ slaPolicyUpdate: Payload }>(
        `mutation SlaUpdate($id:String!,$input:SlaPolicyInput!){slaPolicyUpdate(id:$id,input:$input){success startedClocks slaPolicy{${POLICY_FIELDS}}}}`,
        { id, input },
      )
    : await gql<{ slaPolicyCreate: Payload }>(
        `mutation SlaCreate($input:SlaPolicyInput!){slaPolicyCreate(input:$input){success startedClocks slaPolicy{${POLICY_FIELDS}}}}`,
        { input },
      );
  const out = "slaPolicyUpdate" in d ? d.slaPolicyUpdate : d.slaPolicyCreate;
  if (!out?.success || !out.slaPolicy)
    throw new Error("The policy wasn't saved. Try again.");
  return { policy: out.slaPolicy, started: out.startedClocks || 0 };
}

export function formatMinutes(m: number | null | undefined): string {
  if (!m) return "—";
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  if (m % 1440 === 0) return plural(m / 1440, "day");
  if (m % 60 === 0) return plural(m / 60, "hour");
  if (m > 60) return `${Math.floor(m / 60)} h ${m % 60} min`;
  return `${m} min`;
}

export function priorityList(values: number[]): string {
  if (!values.length) return "Covers every task";
  const names = PRIORITIES.filter((p) => values.includes(p.value)).map(
    (p) => p.label,
  );
  if (values.length === 1 && values[0] === 0)
    return "Covers tasks with no priority";
  return `Covers priority: ${names.join(", ")}`;
}

/* HOJA-704's cycle preset: one workspace policy per priority plus a triage clock, each
 * pausing in every Backlog state. "Cycle-bound, else N days" keeps a target for tasks that
 * sit outside a cycle, which is why the fallback minutes are always sent. */
export function cyclePreset(teams: SlaTeam[]): SlaInput[] {
  const backlog = [
    ...new Set(
      teams.flatMap((t) =>
        t.states.nodes.filter((s) => s.type === "backlog").map((s) => s.id),
      ),
    ),
  ];
  const day = 24 * 60;
  const base = {
    teamId: null,
    pauseStateIds: backlog,
    businessDaysOnly: false,
    applyToOpen: true,
    stopsOn: "completed",
    enabled: true,
  };
  return [
    {
      ...base,
      name: "Urgent",
      appliesPriorities: [1],
      firstResponseMinutes: 120,
      resolutionMinutes: day,
      cycleBound: false,
    },
    {
      ...base,
      name: "High",
      appliesPriorities: [2],
      firstResponseMinutes: day,
      resolutionMinutes: 7 * day,
      cycleBound: true,
    },
    {
      ...base,
      name: "Medium",
      appliesPriorities: [3],
      firstResponseMinutes: null,
      resolutionMinutes: 14 * day,
      cycleBound: true,
    },
    {
      ...base,
      name: "Low",
      appliesPriorities: [4],
      firstResponseMinutes: null,
      resolutionMinutes: 28 * day,
      cycleBound: true,
    },
    {
      ...base,
      name: "No priority",
      appliesPriorities: [0],
      firstResponseMinutes: null,
      resolutionMinutes: 2 * day,
      cycleBound: false,
      stopsOn: "prioritized",
    },
  ];
}
