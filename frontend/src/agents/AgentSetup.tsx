/* "Get this agent working": one checklist from a new agent to a finished test run (HOJA-602).
 *
 * Before this, running an agent meant knowing six facts the screens never put together: that the
 * policy must name a team (or every lease is refused), which harness to pick, that the model is a
 * member field, that the provider key is a workspace secret *and* a policy grant, that a machine
 * must enroll and then start the daemon, and whether any of it worked. Each step here reads the
 * server's own state, says in one line whether it is done, and opens only the first step that is
 * not. Every change is one click or one paste, saved as it is made.
 *
 * Nothing is inferred: "a machine is ready" is an online runner that declared this agent's harness
 * at enrollment, and "it works" is a real task that reached In Review. */
import {
  Check,
  ChevronDown,
  Cpu,
  KeyRound,
  Loader2,
  Play,
  Server,
  Sparkles,
  TriangleAlert,
  Users,
  Workflow,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getAgentsAdapter } from "./adapter";
import { ConnectMachine } from "./ConnectMachine";
import type {
  Agent,
  HarnessPolicy,
  RunAccess,
  Runner,
  SetupProbe,
} from "./types";

/** The harness that can ask a person and report a typed result (runner/hoja_runnerd/coder.py). */
export const RECOMMENDED_HARNESS = "hoja-coder";

const CLI_NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  gemini: "Gemini CLI",
  opencode: "OpenCode",
  pi: "pi",
  generic: "Any command",
};

/** The key each provider (by model prefix) or CLI harness reads, and its optional endpoint. */
const PROVIDERS: Record<string, { label: string; key: string; base?: string }> =
  {
    anthropic: {
      label: "Anthropic",
      key: "ANTHROPIC_API_KEY",
      base: "ANTHROPIC_BASE_URL",
    },
    openai: {
      label: "OpenAI or a compatible gateway",
      key: "OPENAI_API_KEY",
      base: "OPENAI_BASE_URL",
    },
    "google-gla": { label: "Google", key: "GEMINI_API_KEY" },
    gemini: { label: "Google", key: "GEMINI_API_KEY" },
    groq: { label: "Groq", key: "GROQ_API_KEY" },
    mistral: { label: "Mistral", key: "MISTRAL_API_KEY" },
    openrouter: { label: "OpenRouter", key: "OPENROUTER_API_KEY" },
  };
const CLI_KEYS: Record<string, string> = {
  "claude-code": "ANTHROPIC_API_KEY",
  codex: "OPENAI_API_KEY",
  gemini: "GEMINI_API_KEY",
  pi: "PI_API_KEY",
  opencode: "OPENCODE_API_KEY",
};

const MODEL_PRESETS = [
  {
    id: "anthropic:claude-sonnet-5-5",
    label: "Claude Sonnet 5.5",
    hint: "Balanced",
  },
  {
    id: "anthropic:claude-opus-5-5",
    label: "Claude Opus 5.5",
    hint: "Most capable",
  },
  {
    id: "anthropic:claude-haiku-5-5",
    label: "Claude Haiku 5.5",
    hint: "Fastest",
  },
];

const RUNNER_POLL_MS = 3000;
const PROBE_POLL_MS = 2000;

type StepId = "teams" | "harness" | "model" | "key" | "machine" | "try";

const BUTTON =
  "inline-flex items-center gap-1.5 rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-foreground hover:bg-subtle disabled:opacity-50";
const PRIMARY =
  "inline-flex items-center gap-1.5 rounded-md border border-line bg-accent px-3 py-1.5 text-sm font-medium text-foreground disabled:bg-panel disabled:text-muted";
const FIELD =
  "min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1.5 text-sm text-foreground";

function providerOf(model: string | null): string | null {
  if (!model || !model.includes(":")) return null;
  return model.split(":", 1)[0];
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => undefined);
}

function StatusIcon({
  state,
}: {
  state: "done" | "todo" | "working" | "warn";
}) {
  if (state === "done")
    return (
      <span className="flex size-5 flex-none items-center justify-center rounded-full bg-emerald-600 text-white">
        <Check size={12} strokeWidth={3} aria-hidden="true" />
      </span>
    );
  if (state === "working")
    return (
      <Loader2
        size={20}
        className="flex-none animate-spin text-muted"
        aria-hidden="true"
      />
    );
  if (state === "warn")
    return (
      <TriangleAlert
        size={20}
        className="flex-none text-amber-600"
        aria-hidden="true"
      />
    );
  return (
    <span
      className="size-5 flex-none rounded-full border-2 border-line"
      aria-hidden="true"
    />
  );
}

function Step({
  id,
  title,
  summary,
  state,
  open,
  onToggle,
  icon,
  children,
}: {
  id: StepId;
  title: string;
  summary: string;
  state: "done" | "todo" | "working" | "warn";
  open: boolean;
  onToggle: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <li
      className="border-b border-line last:border-b-0"
      data-setup-step={id}
      data-setup-state={state}
    >
      <button
        type="button"
        className="flex w-full items-center gap-3 py-3 text-left"
        aria-expanded={open}
        onClick={onToggle}
      >
        <StatusIcon state={state} />
        <span className="flex flex-none text-muted">{icon}</span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-medium text-foreground">{title}</span>
          <span className="truncate text-xs text-muted">{summary}</span>
        </span>
        <ChevronDown
          size={16}
          aria-hidden="true"
          className={`flex-none text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && <div className="flex flex-col gap-3 pb-4 pl-8">{children}</div>}
    </li>
  );
}

export function AgentSetup({
  agent,
  harnesses,
  onSaveModel,
  onSaveHarness,
  onRunStarted,
}: {
  agent: Agent;
  harnesses: HarnessPolicy;
  onSaveModel: (model: string) => Promise<void>;
  onSaveHarness: (harness: string) => Promise<void>;
  /** Called when the checklist started a run, so the page re-reads and follows it live. */
  onRunStarted?: () => void;
}) {
  const adapter = getAgentsAdapter();
  const [access, setAccess] = useState<RunAccess | null>(null);
  const [runners, setRunners] = useState<Runner[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<StepId | "">("");
  /* `null` follows the checklist (the first unfinished step is open); "none" is a person who
   * closed everything; a step id is a person who chose one. */
  const [opened, setOpened] = useState<StepId | "none" | null>(null);
  const [collapsed, setCollapsed] = useState<boolean | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [baseDraft, setBaseDraft] = useState("");
  const [showBase, setShowBase] = useState(false);
  const [repoDraft, setRepoDraft] = useState("");
  const [prefixDraft, setPrefixDraft] = useState("agent/");
  const [probe, setProbe] = useState<SetupProbe | null>(null);

  const harness = harnesses.defaultHarness || "";
  const isCoder = harness === RECOMMENDED_HARNESS;
  const provider = isCoder ? providerOf(agent.model) : null;
  const keyName = isCoder
    ? (provider && PROVIDERS[provider]?.key) || null
    : CLI_KEYS[harness] || null;
  const baseName = isCoder && provider ? PROVIDERS[provider]?.base : undefined;

  const readAccess = useCallback(
    () =>
      adapter
        .getRunAccess(agent.id)
        .then(setAccess)
        .catch((e: Error) => setError(e.message)),
    [adapter, agent.id],
  );
  const readRunners = useCallback(
    () =>
      adapter
        .listRunners()
        .then((page) => setRunners(page.runners))
        .catch((e: Error) => setError(e.message)),
    [adapter],
  );
  useEffect(() => {
    void readAccess();
    void readRunners();
  }, [readAccess, readRunners]);

  /* The harness a machine must offer: the chosen one, else the recommended one, so a machine
   * that can run Hoja Coder counts before anyone has picked how the agent runs. */
  const runsOn = harness || RECOMMENDED_HARNESS;

  /* --- what is true right now ------------------------------------------------------------ */
  const ready = useMemo(
    () =>
      (runners || []).filter(
        (r) =>
          r.status === "online" &&
          r.enabled &&
          !r.revoked &&
          r.harnesses.includes(runsOn),
      ),
    [runners, runsOn],
  );
  const lacking = useMemo(
    () =>
      (runners || []).filter(
        (r) =>
          r.status === "online" &&
          r.enabled &&
          !r.revoked &&
          !r.harnesses.includes(runsOn),
      ),
    [runners, runsOn],
  );
  const teamsDone =
    !!access && (access.teams.length > 0 || access.projects.length > 0);
  const harnessDone = !!harness;
  // Model and key depend on the harness: nothing is "done" for a harness nobody chose yet.
  const modelDone = harnessDone && (!isCoder || !!agent.model);
  const keyStored = !!keyName && !!access?.workspaceSecrets.includes(keyName);
  const keyGranted = !!keyName && !!access?.secrets.includes(keyName);
  const keyDone = !keyName ? modelDone : keyStored && keyGranted;
  const machineDone = ready.length > 0;
  const tryDone = probe?.stateName === "In Review";

  const steps: { id: StepId; done: boolean }[] = [
    { id: "machine", done: machineDone },
    { id: "teams", done: teamsDone },
    { id: "harness", done: harnessDone },
    { id: "model", done: modelDone },
    { id: "key", done: keyDone },
    { id: "try", done: tryDone },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const allSetUp = steps.slice(0, 5).every((s) => s.done);
  const next = steps.find((s) => !s.done)?.id ?? null;
  const openStep = opened ?? next;
  /* Folded only when the agent was already set up when the page opened: someone finishing
   * setup right now keeps the checklist, with "Try it" open next, until they hide it. */
  const startedReady = useRef<boolean | null>(null);
  if (startedReady.current === null && access !== null && runners !== null) {
    startedReady.current = allSetUp;
  }
  const isCollapsed = collapsed ?? (startedReady.current === true && !probe);
  const toggle = (id: StepId) =>
    setOpened((cur) => ((cur ?? next) === id ? "none" : id));

  /* --- live: a machine coming online, a test task moving -------------------------------- */

  const probing =
    !!probe &&
    probe.stateName !== "In Review" &&
    !["failed", "cancelled"].includes(probe.sessionState || "");
  const probeRef = useRef(probe);
  probeRef.current = probe;
  useEffect(() => {
    if (!probing) return;
    const id = window.setInterval(() => {
      const current = probeRef.current;
      if (!current || document.hidden) return;
      adapter
        .getSetupProbe(current.issueId)
        .then(setProbe)
        .catch(() => undefined);
    }, PROBE_POLL_MS);
    return () => window.clearInterval(id);
  }, [probing, adapter]);

  /* --- actions ---------------------------------------------------------------------------- */
  const run = async (step: StepId, action: () => Promise<unknown>) => {
    setBusy(step);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const toggleTeam = (teamId: string) =>
    run("teams", async () => {
      if (!access) return;
      const teams = access.teams.includes(teamId)
        ? access.teams.filter((t) => t !== teamId)
        : [...access.teams, teamId];
      setAccess({ ...access, teams });
      await adapter.updateRunAccess(agent.id, { teams });
      await readAccess();
    });

  const saveRepos = (repos: { repo: string; branches: string[] }[]) =>
    run("teams", async () => {
      if (!access) return;
      setAccess({ ...access, repos });
      await adapter.updateRunAccess(agent.id, { repos });
      await readAccess();
    });

  const addRepo = () => {
    if (!access) return;
    const repo = repoDraft.trim();
    const prefix = prefixDraft.trim() || "agent/";
    if (!repo) return;
    setRepoDraft("");
    void saveRepos([
      ...access.repos.filter((g) => g.repo !== repo),
      { repo, branches: [prefix] },
    ]);
  };

  const saveKey = () =>
    run("key", async () => {
      if (!keyName || !access) return;
      const names = [keyName];
      if (keyDraft.trim()) await adapter.setSecret(keyName, keyDraft.trim());
      if (baseName && baseDraft.trim()) {
        await adapter.setSecret(baseName, baseDraft.trim());
        names.push(baseName);
      } else if (
        baseName &&
        access.workspaceSecrets.includes(baseName) &&
        showBase
      ) {
        names.push(baseName);
      }
      const secrets = Array.from(new Set([...access.secrets, ...names]));
      await adapter.updateRunAccess(agent.id, { secrets });
      setKeyDraft("");
      setBaseDraft("");
      await readAccess();
    });

  const sendTest = () =>
    run("try", async () => {
      if (!access?.teams.length)
        throw new Error("Choose a team first: the test task is created there.");
      const created = await adapter.createSetupTask(
        agent.userId,
        access.teams[0],
      );
      setProbe(created);
      onRunStarted?.();
      setCollapsed(false);
      setOpened("try");
    });

  /* --- view -------------------------------------------------------------------------------- */
  if (access === null || runners === null) {
    return (
      <section
        className="mb-4 flex items-center gap-2 rounded-lg border border-line p-4 text-sm text-muted"
        aria-label="Setup"
      >
        <Loader2 size={16} className="animate-spin" aria-hidden="true" />{" "}
        Checking what this agent needs…
      </section>
    );
  }

  if (isCollapsed) {
    return (
      <section
        className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line p-4"
        aria-label="Setup"
        data-setup="ready"
      >
        <StatusIcon state="done" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-medium">
            {agent.name} is ready to take work
          </span>
          <span className="text-xs text-muted">
            {CLI_NAMES[harness] ?? "Hoja Coder"}
            {isCoder && agent.model ? ` · ${agent.model}` : ""} · {ready.length}{" "}
            {ready.length === 1 ? "machine" : "machines"} online
          </span>
        </span>
        <button
          type="button"
          className={BUTTON}
          onClick={() => void sendTest()}
          disabled={busy === "try"}
        >
          <Play size={14} aria-hidden="true" /> Send a test task
        </button>
        <button
          type="button"
          className={BUTTON}
          onClick={() => setCollapsed(false)}
        >
          Change setup
        </button>
      </section>
    );
  }

  return (
    <section
      className="mb-4 rounded-lg border border-line p-4"
      aria-label="Setup"
      data-setup="open"
    >
      <header className="flex items-center gap-3">
        <Sparkles size={16} className="text-muted" aria-hidden="true" />
        <span className="flex-1 text-sm font-medium">
          Get {agent.name} working
        </span>
        <span className="text-xs text-muted" data-setup-progress={doneCount}>
          {doneCount} of {steps.length}
        </span>
        {allSetUp && !probing && (
          <button
            type="button"
            className="text-xs text-muted hover:text-foreground"
            onClick={() => setCollapsed(true)}
          >
            Hide
          </button>
        )}
      </header>
      <div
        className="mt-3 h-1 overflow-hidden rounded-full bg-subtle"
        aria-hidden="true"
      >
        <div
          className="h-full rounded-full bg-emerald-600 transition-all"
          style={{ width: `${(doneCount / steps.length) * 100}%` }}
        />
      </div>
      {error && (
        <p className="mt-3 mb-0 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <ol className="mt-2 flex list-none flex-col p-0">
        <Step
          id="machine"
          title="A machine to run on"
          summary={
            machineDone
              ? `${ready.map((r) => r.name).join(", ")} ${ready.length === 1 ? "is" : "are"} online and can run it`
              : lacking.length && harness
                ? `${lacking.map((r) => r.name).join(", ")} ${lacking.length === 1 ? "is" : "are"} online but cannot run ${CLI_NAMES[harness] ?? "Hoja Coder"}`
                : "Connect a laptop, server or CI box with one command. It only dials out to Hoja."
          }
          state={
            busy === "machine"
              ? "working"
              : machineDone
                ? "done"
                : lacking.length && harness
                  ? "warn"
                  : "todo"
          }
          open={openStep === "machine"}
          onToggle={() => toggle("machine")}
          icon={<Server size={16} />}
        >
          {machineDone ? (
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {ready.map((r) => (
                <li key={r.id} className="flex items-center gap-2 text-sm">
                  <span
                    className="size-2 rounded-full bg-emerald-600"
                    aria-hidden="true"
                  />
                  {r.name}
                  <span className="text-xs text-muted">
                    {r.lease ? "busy with a run" : "idle"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <>
              {lacking.length > 0 && harness && (
                <p className="m-0 text-sm text-muted">
                  Run the command below on that machine to upgrade it, or
                  connect another one.
                </p>
              )}
              <ConnectMachine
                harness={harness || RECOMMENDED_HARNESS}
                onConnected={() => void readRunners()}
              />
            </>
          )}
        </Step>

        <Step
          id="teams"
          title="Where it can work"
          summary={
            teamsDone
              ? `Takes tasks in ${
                  access.allTeams
                    .filter((t) => access.teams.includes(t.id))
                    .map((t) => t.key)
                    .join(", ") || "its projects"
                }`
              : "Pick at least one team. Until then every task delegated to it is refused."
          }
          state={busy === "teams" ? "working" : teamsDone ? "done" : "todo"}
          open={openStep === "teams"}
          onToggle={() => toggle("teams")}
          icon={<Users size={16} />}
        >
          <div className="flex flex-wrap gap-2" role="group" aria-label="Teams">
            {access.allTeams.map((team) => {
              const on = access.teams.includes(team.id);
              return (
                <button
                  key={team.id}
                  type="button"
                  aria-pressed={on}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm ${on ? "border-emerald-600 bg-emerald-600 text-white" : "border-line bg-panel text-foreground hover:bg-subtle"}`}
                  onClick={() => void toggleTeam(team.id)}
                >
                  {on && <Check size={12} aria-hidden="true" />}
                  {team.name}{" "}
                  <span className={on ? "text-white" : "text-muted"}>
                    {team.key}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="flex flex-col gap-2" data-setup-repos>
            <span className="text-sm">
              Repositories <span className="text-muted">(optional)</span>
            </span>
            <span className="text-xs text-muted">
              The machine clones each one into its work folder once; every task
              gets its own worktree and its own branch under the prefix, e.g.
              agent/hoja-12.
            </span>
            {access.repos.map((g) => (
              <span
                key={g.repo}
                className="flex items-center gap-2 text-sm"
                data-repo-grant={g.repo}
              >
                <code className="text-xs">{g.repo}</code>
                <span className="text-xs text-muted">
                  branches {g.branches.join(", ")}
                </span>
                <button
                  type="button"
                  className="ml-auto text-xs text-muted hover:text-foreground"
                  onClick={() =>
                    void saveRepos(
                      access.repos.filter((x) => x.repo !== g.repo),
                    )
                  }
                >
                  Remove
                </button>
              </span>
            ))}
            <form
              className="flex flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                addRepo();
              }}
            >
              <input
                className={FIELD}
                aria-label="Repository"
                placeholder="owner/name, a git URL, or a path on the machine"
                value={repoDraft}
                onChange={(e) => setRepoDraft(e.target.value)}
              />
              <input
                className="w-28 flex-none rounded-md border border-line bg-panel px-2 py-1.5 text-sm text-foreground"
                aria-label="Branch prefix"
                value={prefixDraft}
                onChange={(e) => setPrefixDraft(e.target.value)}
              />
              <button
                type="submit"
                className={BUTTON}
                disabled={!repoDraft.trim()}
              >
                Add
              </button>
            </form>
          </div>
        </Step>

        <Step
          id="harness"
          title="How it runs"
          summary={
            harnessDone
              ? isCoder
                ? "Hoja Coder: asks you when it needs to, reports what it did"
                : `${CLI_NAMES[harness] ?? harness}, in a terminal on your machine`
              : "Choose the program that does the work."
          }
          state={busy === "harness" ? "working" : harnessDone ? "done" : "todo"}
          open={openStep === "harness"}
          onToggle={() => toggle("harness")}
          icon={<Workflow size={16} />}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              aria-pressed={isCoder}
              className={`flex flex-col gap-1 rounded-lg border p-4 text-left ${isCoder ? "border-emerald-600 bg-subtle" : "border-line bg-panel hover:bg-subtle"}`}
              onClick={() =>
                void run("harness", () => onSaveHarness(RECOMMENDED_HARNESS))
              }
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                Hoja Coder
                <span className="rounded-full bg-emerald-600 px-2 text-xs text-white">
                  Recommended
                </span>
              </span>
              <span className="text-xs text-muted">
                Asks you questions right here when it is stuck, answers every
                acceptance item, and reports exactly which model ran and what it
                cost. Works with any model.
              </span>
            </button>
            <div
              className={`flex flex-col gap-2 rounded-lg border p-4 ${harnessDone && !isCoder ? "border-emerald-600 bg-subtle" : "border-line bg-panel"}`}
            >
              <span className="text-sm font-medium">
                A coding CLI you already use
              </span>
              <span className="text-xs text-muted">
                Runs the vendor's terminal app on your machine. It cannot ask
                you questions; a run that needs input stops and says so.
              </span>
              <select
                aria-label="Coding CLI"
                className={FIELD}
                value={!isCoder ? harness : ""}
                onChange={(e) =>
                  e.target.value &&
                  void run("harness", () => onSaveHarness(e.target.value))
                }
              >
                <option value="">Choose a CLI…</option>
                {harnesses.known
                  .filter((name) => name !== RECOMMENDED_HARNESS)
                  .map((name) => (
                    <option key={name} value={name}>
                      {CLI_NAMES[name] ?? name}
                    </option>
                  ))}
              </select>
            </div>
          </div>
        </Step>

        <Step
          id="model"
          title="Which model"
          summary={
            !harnessDone
              ? "Choose how it runs first."
              : !isCoder
                ? "The CLI uses its own model setting"
                : agent.model
                  ? agent.model
                  : "Pick the model it thinks with."
          }
          state={busy === "model" ? "working" : modelDone ? "done" : "todo"}
          open={openStep === "model"}
          onToggle={() => toggle("model")}
          icon={<Cpu size={16} />}
        >
          {!isCoder ? (
            <p className="m-0 text-sm text-muted">
              {CLI_NAMES[harness] ?? "The CLI"} reads its model from its own
              configuration on the machine, so there is nothing to choose here.
            </p>
          ) : (
            <>
              <div
                className="flex flex-wrap gap-2"
                role="group"
                aria-label="Suggested models"
              >
                {MODEL_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    aria-pressed={agent.model === preset.id}
                    className={`flex flex-col rounded-lg border px-3 py-2 text-left ${agent.model === preset.id ? "border-emerald-600 bg-subtle" : "border-line bg-panel hover:bg-subtle"}`}
                    onClick={() =>
                      void run("model", () => onSaveModel(preset.id))
                    }
                  >
                    <span className="text-sm">{preset.label}</span>
                    <span className="text-xs text-muted">{preset.hint}</span>
                  </button>
                ))}
              </div>
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const value = modelDraft.trim();
                  if (value)
                    void run("model", () => onSaveModel(value)).then(() =>
                      setModelDraft(""),
                    );
                }}
              >
                <input
                  className={FIELD}
                  aria-label="Any other model"
                  placeholder="Any other model, as provider:model (e.g. openai:gpt-…, openrouter:…)"
                  value={modelDraft}
                  onChange={(e) => setModelDraft(e.target.value)}
                />
                <button
                  type="submit"
                  className={BUTTON}
                  disabled={!modelDraft.includes(":")}
                >
                  Use
                </button>
              </form>
            </>
          )}
        </Step>

        <Step
          id="key"
          title="Model key"
          summary={
            !keyName
              ? !harnessDone
                ? "Choose how it runs first."
                : isCoder && !agent.model
                  ? "Pick a model first"
                  : "No key needed, or the CLI is signed in on the machine"
              : keyDone
                ? `Uses the workspace's ${keyName}${baseName && access.secrets.includes(baseName) ? ` and ${baseName}` : ""}`
                : keyStored
                  ? `The workspace has ${keyName}; let this agent use it`
                  : `Paste the ${provider ? (PROVIDERS[provider]?.label ?? provider) : CLI_NAMES[harness]} key once; it is sealed and never shown again`
          }
          state={busy === "key" ? "working" : keyDone ? "done" : "todo"}
          open={openStep === "key"}
          onToggle={() => toggle("key")}
          icon={<KeyRound size={16} />}
        >
          {keyName && (
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void saveKey();
              }}
            >
              {keyStored ? (
                <p className="m-0 text-sm text-muted">
                  {keyName} is already stored in this workspace.{" "}
                  {keyGranted
                    ? "This agent's runs receive it."
                    : "Grant it to this agent, or paste a new value to replace it."}
                </p>
              ) : null}
              <div className="flex gap-2">
                <input
                  className={FIELD}
                  type="password"
                  autoComplete="off"
                  aria-label={keyName}
                  placeholder={
                    keyStored
                      ? `Replace ${keyName} (optional)`
                      : `Paste ${keyName}`
                  }
                  value={keyDraft}
                  onChange={(e) => setKeyDraft(e.target.value)}
                />
                <button
                  type="submit"
                  className={PRIMARY}
                  disabled={
                    busy === "key" ||
                    (!keyStored && !keyDraft.trim()) ||
                    (keyGranted && !keyDraft.trim() && !baseDraft.trim())
                  }
                >
                  {keyStored && !keyDraft.trim()
                    ? keyGranted
                      ? "Saved"
                      : "Use it"
                    : "Save key"}
                </button>
              </div>
              {baseName && (
                <>
                  <label className="flex items-center gap-2 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={showBase}
                      onChange={(e) => setShowBase(e.target.checked)}
                    />
                    Through a gateway or proxy
                  </label>
                  {showBase && (
                    <input
                      className={FIELD}
                      aria-label={baseName}
                      placeholder={`${baseName}, e.g. https://gateway.example.com/v1`}
                      value={baseDraft}
                      onChange={(e) => setBaseDraft(e.target.value)}
                    />
                  )}
                </>
              )}
              <p className="m-0 text-xs text-muted">
                Keys reach only this agent's runs, as {keyName}
                {baseName ? ` (and ${baseName})` : ""} in the run's environment.
                They never appear in transcripts or logs.
              </p>
            </form>
          )}
        </Step>

        <Step
          id="try"
          title="Try it"
          summary={
            !probe
              ? "Send a one-line test task and watch it come back."
              : tryDone
                ? `${probe.identifier} came back and is In Review`
                : probe.sessionState === "failed"
                  ? `${probe.identifier} failed. Open it to see why.`
                  : `${probe.identifier}: ${describe(probe)}`
          }
          state={
            busy === "try" || probing
              ? "working"
              : tryDone
                ? "done"
                : probe?.sessionState === "failed"
                  ? "warn"
                  : "todo"
          }
          open={openStep === "try"}
          onToggle={() => toggle("try")}
          icon={<Play size={16} />}
        >
          {probe ? (
            <ProbeTrack probe={probe} />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={PRIMARY}
                disabled={!allSetUp || busy === "try"}
                onClick={() => void sendTest()}
              >
                <Play size={14} aria-hidden="true" /> Send a test task
              </button>
              {!allSetUp && (
                <span className="text-xs text-muted">
                  Finish the steps above first.
                </span>
              )}
            </div>
          )}
        </Step>
      </ol>
    </section>
  );
}

const PROBE_STAGES = [
  { key: "queued", label: "Queued" },
  { key: "running", label: "Running" },
  { key: "verifying", label: "Checking" },
  { key: "in_review", label: "In Review" },
];

function stageIndex(probe: SetupProbe): number {
  const state = probe.sessionState || "queued";
  if (
    probe.stateName === "In Review" ||
    state === "in_review" ||
    state === "done"
  )
    return 3;
  if (state === "verifying") return 2;
  if (["leased", "running", "awaiting_input"].includes(state)) return 1;
  return 0;
}

function describe(probe: SetupProbe): string {
  if (probe.sessionState === "awaiting_input")
    return "the agent is asking you something below";
  if (probe.sessionState === "verifying" && probe.verdict === "uncertain")
    return "the check could not confirm the result; a person can accept it on the task";
  return PROBE_STAGES[stageIndex(probe)].label.toLowerCase() + "…";
}

function ProbeTrack({ probe }: { probe: SetupProbe }) {
  const at = stageIndex(probe);
  const failed =
    probe.sessionState === "failed" || probe.sessionState === "cancelled";
  return (
    <div className="flex flex-col gap-3">
      <ol
        className="m-0 flex list-none flex-wrap items-center gap-2 p-0"
        aria-label="Test task progress"
      >
        {PROBE_STAGES.map((stage, i) => (
          <li
            key={stage.key}
            className="flex items-center gap-2 text-xs"
            data-probe-stage={stage.key}
            data-probe-reached={i <= at}
          >
            <span
              className={`flex size-5 items-center justify-center rounded-full ${failed && i === at ? "bg-red-600 text-white" : i < at || (i === at && at === 3) ? "bg-emerald-600 text-white" : i === at ? "border-2 border-emerald-600" : "border-2 border-line"}`}
            >
              {i < at || (i === at && at === 3) ? (
                <Check size={12} strokeWidth={3} aria-hidden="true" />
              ) : null}
            </span>
            <span className={i <= at ? "text-foreground" : "text-muted"}>
              {stage.label}
            </span>
            {i < PROBE_STAGES.length - 1 && (
              <span className="h-px w-6 bg-line" aria-hidden="true" />
            )}
          </li>
        ))}
      </ol>
      <a
        className="text-sm text-foreground underline"
        href={`/workspace/task/${probe.identifier}`}
      >
        Open {probe.identifier}
      </a>
    </div>
  );
}
