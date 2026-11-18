/* Screen 1 — Agents (HOJA-602-A-16).
 *
 * One row per agent, in the issue list's layout: the same `.lin-row`, the same leading slot order
 * (status glyph, identifier, title, chips, meta), the same `.lin-group` headers. Filtering and
 * grouping are the issue list's own components — `FilterMenu` is handed the agent statuses in its
 * `FilterTeam` shape, and `DisplayPanel` is handed the two groupings this surface can produce — so
 * no control here is a copy.
 *
 * The rows are real: `agentMembers` from WP04 (app/hoja_server/agent_members.py). Three columns the
 * server does not have — running sessions, harness, owner — render as "not reported" rather than as
 * a plausible number, which is why the row's capacity ring is empty and its tooltip says so. The
 * create form is the only write here, and it is `agentMemberCreate`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ListFilter, Plus, SlidersHorizontal } from "lucide-react";
import { GraphStatusIcon, IconBtn, Ring, StatusAgeIcon } from "../graph/shared";
import {
  DisplayPanel,
  FilterMenu,
  defaultDisplay,
  emptyFilters,
  type FilterTeam,
  type LinDisplay,
  type LinFilters,
} from "../graph/tasks";
import { stateKey } from "../main";
import { getAgentsAdapter } from "./adapter";
import { AGENTS_TABS, AgentsListShell, SearchField } from "./chrome";
import { AGENT_STATUSES, agentStatus } from "./types";
import type { Agent, AgentStatus } from "./types";

export type AgentPropKey =
  "id" | "status" | "model" | "capacity" | "budget" | "pool" | "updated";

const AGENT_PROPS: { key: AgentPropKey; label: string }[] = [
  { key: "id", label: "ID" },
  { key: "status", label: "Status" },
  { key: "model", label: "Model" },
  { key: "capacity", label: "Running / capacity" },
  { key: "budget", label: "Monthly budget" },
  { key: "pool", label: "Runner pool" },
  { key: "updated", label: "Updated" },
];

function defaultAgentProps(): Record<AgentPropKey, boolean> {
  return {
    id: true,
    status: true,
    model: true,
    capacity: true,
    budget: true,
    pool: true,
    updated: false,
  };
}

export const AGENT_DISPLAY: LinDisplay & {
  agentProps: Record<AgentPropKey, boolean>;
} = {
  ...defaultDisplay,
  grouping: "status",
  agentProps: defaultAgentProps(),
};

export function statusGlyph(status: AgentStatus) {
  const s = agentStatus(status);
  return (
    <GraphStatusIcon
      state={{ name: s.name, type: s.type, color: s.color }}
      size={14}
    />
  );
}

/* What the row shows for a column the server does not report. One spelling, so "not reported"
 * cannot drift into a zero somewhere. */
function NotReported({ what }: { what: string }) {
  return (
    <span
      className="whitespace-nowrap text-xs text-muted"
      title={`${what}: the server does not report this yet`}
    >
      —
    </span>
  );
}

/* One agent, drawn with the issue row's grid and the issue row's own leading glyphs. The two 14 px
 * columns before the identifier are the same two the issue row has: the status symbol (with its age
 * on hover, like an issue's) and a second symbol. On an issue the second is priority; an agent has
 * no priority, so it carries the capacity ring, which is the agent's own scarce resource. */
function AgentRow({
  agent,
  props,
  onOpen,
}: {
  agent: Agent;
  props: Record<AgentPropKey, boolean>;
  onOpen: (a: Agent) => void;
}) {
  const tone = agentStatus(agent.status);
  const slots =
    agent.running === null
      ? `${agent.capacity} ${agent.capacity === 1 ? "slot" : "slots"}; running sessions are not reported yet`
      : `${agent.running} of ${agent.capacity} slots in use`;
  return (
    <div
      className="group relative flex items-center border-b border-line hover:bg-subtle gap-2.25 min-h-10.5 pt-1.25 pr-2.5 pb-1.25 pl-2.5 border-b-line text-[13px]"
      data-style-hooks="lin-row"
      data-agent={agent.id}
      data-archived={agent.archivedAt ? "true" : undefined}
    >
      {props.status && (
        <StatusAgeIcon
          issue={{
            state: { name: tone.name, type: tone.type, color: tone.color },
            stateChangedAt: agent.statusChangedAt,
            updatedAt: agent.updatedAt,
          }}
        />
      )}
      {props.capacity && (
        <span
          className="relative inline-grid place-items-center"
          data-tip={slots}
          role="img"
          aria-label={`Capacity: ${slots}`}
        >
          <Ring
            frac={
              agent.running === null
                ? 0
                : agent.running / Math.max(1, agent.capacity)
            }
            size={14}
          />
        </span>
      )}
      {props.id && (
        <span
          className="whitespace-nowrap font-semibold text-foreground"
          data-style-hooks="ident"
        >
          {agent.key}
        </span>
      )}
      <button
        type="button"
        className="flex-1 cursor-pointer truncate border-0 text-left min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-foreground [background:none] [font:inherit] pt-0 pr-0 pb-0 pl-0"
        onClick={() => onOpen(agent)}
        title={agent.name}
        data-style-hooks="title"
      >
        {agent.name}
      </button>
      <span
        className="flex flex-none items-center gap-2 ml-auto"
        data-style-hooks="meta"
      >
        {/* Narrow screens keep the row readable instead of clipping the identifier: the extra
            columns return as space allows, widest last. */}
        {props.model && (
          <span className="hidden sm:inline-flex">
            {agent.model ? (
              <span
                className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                title="Model"
              >
                {agent.model}
              </span>
            ) : (
              <NotReported what="Model" />
            )}
          </span>
        )}
        {props.budget && (
          <span className="hidden md:inline-flex">
            {agent.monthlyBudgetCredits !== null ? (
              <span
                className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                title="Credits per calendar month"
              >
                {agent.monthlyBudgetCredits} credits/mo
              </span>
            ) : (
              <NotReported what="Monthly budget" />
            )}
          </span>
        )}
        {props.pool && (
          <span className="hidden lg:inline-flex">
            {agent.runnerPool ? (
              <span
                className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                title="Runner pool"
              >
                {agent.runnerPool}
              </span>
            ) : (
              <NotReported what="Runner pool" />
            )}
          </span>
        )}
        {props.updated && (
          <span className="whitespace-nowrap text-muted text-[12px]">
            {agent.updatedAt.slice(0, 10)}
          </span>
        )}
      </span>
    </div>
  );
}

/* `agentMemberCreate`. The form asks for the columns WP04 stores and nothing else — no harness, no
 * owner, no status, because a create that accepted them would be inventing a write the server
 * drops. Errors are the server's own text (name collisions are a real BAD_USER_INPUT). */
function NewAgentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (agent: Agent) => void;
}) {
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("1");
  const [credits, setCredits] = useState("");
  const [model, setModel] = useState("");
  const [pool, setPool] = useState("");
  const [timezone, setTimezone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const agent = await getAgentsAdapter().createAgent({
        name: trimmed,
        capacity: Number(capacity) || 1,
        monthlyBudgetCredits: credits.trim() === "" ? null : Number(credits),
        model: model.trim() || null,
        runnerPool: pool.trim() || null,
        timezone: timezone.trim() || null,
      });
      onCreated(agent);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 overflow-y-auto p-4 z-10 bg-[#0008]"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form
        className="relative mx-auto mt-12 max-w-xl border border-line absolute top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(570px,calc(100%-32px))] pt-6.25 pr-6.25 pb-6.25 pl-6.25 border-solid rounded-[10px] bg-panel shadow-(--shadow)"
        role="dialog"
        aria-modal="true"
        aria-label="New agent"
        onSubmit={submit}
      >
        <h2 className="mt-0 mr-0 mb-1 ml-0 text-[15px]">New agent</h2>
        <p className="text-[12px] text-muted mt-0 mr-0 mb-3 ml-0">
          An agent is a workspace principal, not a seat: it gets a name and the
          four limits below.
        </p>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-2.5">
          <span className="text-[12px] text-muted">Name</span>
          <input
            ref={nameRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            aria-label="Agent name"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-2.5">
          <span className="text-[12px] text-muted">
            Capacity (concurrent turns)
          </span>
          <input
            type="number"
            min={1}
            step={1}
            value={capacity}
            onChange={(e) => setCapacity(e.target.value)}
            aria-label="Capacity"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-2.5">
          <span className="text-[12px] text-muted">
            Monthly budget (credits, blank for the workspace default)
          </span>
          <input
            type="number"
            min={0}
            step={1}
            value={credits}
            onChange={(e) => setCredits(e.target.value)}
            aria-label="Monthly budget credits"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-2.5">
          <span className="text-[12px] text-muted">Model</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            aria-label="Model"
            placeholder="claude-sonnet-4"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-2.5">
          <span className="text-[12px] text-muted">Runner pool</span>
          <input
            value={pool}
            onChange={(e) => setPool(e.target.value)}
            aria-label="Runner pool"
            placeholder="linux-x64"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        <label className="grid block gap-1.75 mt-3.75 text-[12px] font-semibold mb-3">
          <span className="text-[12px] text-muted">Timezone</span>
          <input
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            aria-label="Timezone"
            placeholder="UTC"
            className="resize-y border border-line text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-1.5 pr-2 pb-1.5 pl-2"
          />
        </label>
        {error && (
          <p
            className="border border-danger/40 mt-4 mr-6 mb-4 ml-6 pt-3 pr-4 pb-3 pl-4 border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
            role="alert"
          >
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="inline-flex items-center border border-line hover:bg-subtle disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 border-solid bg-panel text-foreground rounded-[7px] pt-2 pr-3.5 pb-2 pl-3.5 text-[13px] font-[550] shell-phone:min-w-11! shell-phone:min-h-11!"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="inline-flex items-center border border-foreground hover:bg-foreground/90 disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 justify-center gap-2 rounded-none bg-[#1b1b1f] text-white pt-2 pr-3.5 pb-2 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719] border-solid border-line text-[13px] hover:bg-[#333] border-[#1b1b1f] shell-phone:min-w-11! shell-phone:min-h-11!"
            disabled={busy || !name.trim()}
          >
            {busy ? "Creating…" : "Create agent"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function AgentsPane({
  workspaceName,
  onOpen,
  tab,
  onTab,
}: {
  workspaceName: string;
  onOpen: (agent: Agent) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<LinFilters>({ ...emptyFilters });
  const [display, setDisplay] = useState<
    LinDisplay & { agentProps: Record<AgentPropKey, boolean> }
  >(() => ({ ...AGENT_DISPLAY }));
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    getAgentsAdapter()
      .listAgents()
      .then((rows) => {
        if (live) {
          setAgents(rows);
          setError("");
        }
      })
      .catch((e: Error) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [reloadKey]);

  /* The filter menu speaks in LinFilters, and so does this list: `status` holds
   * `stateKey(status)`. There is no assignee facet: `agent_member` has no owner column, so an owner
   * filter would offer a value that does not exist. */
  const filterTeam = useMemo<FilterTeam>(
    () => ({
      id: "agents",
      states: {
        nodes: AGENT_STATUSES.map((s) => ({
          id: s.id,
          name: s.name,
          color: s.color,
          type: s.type,
        })),
      },
      members: { nodes: [] },
    }),
    [],
  );

  const visible = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    const rows = agents.filter((a) => {
      if (
        q &&
        !`${a.key} ${a.name} ${a.model || ""} ${a.runnerPool || ""} ${a.timezone || ""}`
          .toLowerCase()
          .includes(q)
      )
        return false;
      if (
        filters.status.length &&
        !filters.status.includes(stateKey(agentStatus(a.status)))
      )
        return false;
      return true;
    });
    const dir = display.direction === "desc" ? -1 : 1;
    rows.sort((a, b) =>
      display.ordering === "priority"
        ? a.name.localeCompare(b.name) * dir
        : a.updatedAt < b.updatedAt
          ? dir
          : -dir,
    );
    return rows;
  }, [agents, filters, display.direction, display.ordering]);

  const groups = useMemo(() => {
    if (display.grouping === "none")
      return [
        {
          key: "all",
          name: "",
          status: undefined as AgentStatus | undefined,
          items: visible,
        },
      ];
    const map = new Map<
      string,
      { key: string; name: string; status?: AgentStatus; items: Agent[] }
    >();
    for (const a of visible) {
      const key =
        display.grouping === "runner" ? a.runnerPool || "—" : a.status;
      const name =
        display.grouping === "runner"
          ? a.runnerPool || "No runner pool"
          : agentStatus(a.status).name;
      if (!map.has(key))
        map.set(key, {
          key,
          name,
          status: display.grouping === "status" ? a.status : undefined,
          items: [],
        });
      map.get(key)!.items.push(a);
    }
    const out = [...map.values()];
    if (display.grouping === "status") {
      const order = new Map<string, number>(
        AGENT_STATUSES.map((s, i) => [s.id as string, i]),
      );
      /* `??`, not `||`: Active's order is 0, and `0 || 999` sorted the live agents into the fallback
       * bucket, which put Archived above Active on every workspace that had one. */
      out.sort((a, b) => (order.get(a.key) ?? 999) - (order.get(b.key) ?? 999));
    } else out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [visible, display.grouping]);

  const collapsedSet = new Set(collapsed);

  const filterChips: { label: string; clear: () => void }[] = [];
  if (filters.q)
    filterChips.push({
      label: `“${filters.q}”`,
      clear: () => setFilters({ ...filters, q: "" }),
    });
  filters.status.forEach((s) =>
    filterChips.push({
      label: AGENT_STATUSES.find((x) => stateKey(x) === s)?.name || s,
      clear: () =>
        setFilters({
          ...filters,
          status: filters.status.filter((x) => x !== s),
        }),
    }),
  );

  /* Three states that must not look alike: a refused read (401/403/other), a workspace with no
   * agents, and agents that the current filters exclude. Before WP04 all three drew "No agents
   * match", which is how a 403 passed for an empty workspace. */
  const refused = error !== "";
  const forbidden = error !== "" && /admin|forbidden|permission/i.test(error);
  const nothingYet = !loading && !refused && agents.length === 0;

  return (
    <AgentsListShell
      ariaLabel="Agents"
      surface="agents-list"
      workspaceName={workspaceName}
      trail="Agents"
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={
        <>
          <SearchField
            value={filters.q}
            onChange={(q) => setFilters({ ...filters, q })}
            placeholder="Search agents…"
            label="Search agents"
          />
          <span className="flex-1" />
          {/* The issue list's toolbar vocabulary is icon buttons at 28 px, which is what keeps this
            strip the same height as the issue list's. The primary, labelled button lives in the
            empty state, where there is room for it. */}
          {/* A member is refused this write, so the control is not offered: a button that can only
              produce a 403 is a dead end dressed as an action. */}
          {!forbidden && (
            <IconBtn
              label="New agent"
              title="New agent"
              onClick={() => setCreating(true)}
            >
              <Plus size={15} />
            </IconBtn>
          )}
          <span className="relative">
            <IconBtn
              label="Add filter"
              title="Add filter"
              active={filterOpen}
              onClick={() => {
                setFilterOpen((v) => !v);
                setDisplayOpen(false);
              }}
            >
              <ListFilter size={15} />
            </IconBtn>
            {filterOpen && (
              <FilterMenu
                filters={filters}
                setFilters={setFilters}
                team={filterTeam}
                teams={[]}
                projects={[]}
                labels={[]}
                cycles={[]}
                onClose={() => setFilterOpen(false)}
                anchorClass=""
                only={["status", "content"]}
              />
            )}
          </span>
          <span className="relative">
            <IconBtn
              label="Display options"
              title="Display options"
              active={displayOpen}
              onClick={() => {
                setDisplayOpen((v) => !v);
                setFilterOpen(false);
              }}
            >
              <SlidersHorizontal size={15} />
            </IconBtn>
            {displayOpen && (
              <DisplayPanel
                display={display}
                setDisplay={(d) => setDisplay((prev) => ({ ...prev, ...d }))}
                onClose={() => setDisplayOpen(false)}
                groupingOptions={[
                  ["none", "No grouping"],
                  ["status", "Status"],
                  ["runner", "Runner pool"],
                ]}
                groupingLabel="Group agents by"
                entityToggles={false}
                propertyRows={AGENT_PROPS.map((p) => ({
                  key: p.key,
                  label: p.label,
                  checked: display.agentProps[p.key],
                  onToggle: () =>
                    setDisplay((prev) => ({
                      ...prev,
                      agentProps: {
                        ...prev.agentProps,
                        [p.key]: !prev.agentProps[p.key],
                      },
                    })),
                }))}
              />
            )}
          </span>
        </>
      }
      chips={
        filterChips.length ? (
          <>
            {filterChips.map((c, ix) => (
              <span
                key={ix}
                className="inline-flex items-center border border-line gap-1.5 text-[12px] border-solid bg-subtle rounded-[99px] pt-0.75 pr-1.5 pb-0.75 pl-2.5 text-foreground"
              >
                {c.label}
                <button
                  className="border-0 hover:text-foreground [background:none] text-muted text-[13px] leading-none pt-0.5 pr-1 pb-0.5 pl-1 rounded-sm"
                  type="button"
                  aria-label={`Remove filter ${c.label}`}
                  onClick={c.clear}
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground gap-2 [background:none] text-muted text-[13px] pt-1.25 pr-1.5 pb-1.25 pl-1.5 rounded-md"
              onClick={() => setFilters({ ...emptyFilters })}
            >
              Clear all
            </button>
          </>
        ) : null
      }
    >
      {loading && (
        <p
          className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
          role="status"
        >
          Loading agents…
        </p>
      )}
      {refused && (
        <div
          className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
          role="alert"
          data-state="error"
          data-refusal={forbidden ? "forbidden" : "failed"}
        >
          <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
            {forbidden
              ? "You cannot see this workspace’s agents"
              : "Agents could not be loaded"}
          </h2>
          <p>{error}</p>
          {/* A permission refusal does not become a success on retry; a dropped request does. */}
          {!forbidden && (
            <button
              type="button"
              className="inline-flex items-center border border-line hover:bg-subtle disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 border-solid bg-panel text-foreground rounded-[7px] pt-2 pr-3.5 pb-2 pl-3.5 text-[13px] font-[550] shell-phone:min-w-11! shell-phone:min-h-11!"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              Retry
            </button>
          )}
        </div>
      )}
      {nothingYet && (
        <div
          className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
          data-state="empty"
        >
          <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
            No agents yet
          </h2>
          <p>
            A workspace with no agent members has nothing to delegate. Create
            one to give the workspace an identity that can act.
          </p>
          <button
            type="button"
            className="inline-flex items-center border border-foreground hover:bg-foreground/90 disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 justify-center gap-2 rounded-none bg-[#1b1b1f] text-white pt-2 pr-3.5 pb-2 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719] border-solid border-line text-[13px] hover:bg-[#333] border-[#1b1b1f] shell-phone:min-w-11! shell-phone:min-h-11!"
            onClick={() => setCreating(true)}
          >
            <Plus size={14} aria-hidden="true" /> New agent
          </button>
        </div>
      )}
      {!loading && !refused && !nothingYet && (
        <div className="pt-1.5 pr-3 pb-10 pl-3" data-style-hooks="lin-list">
          {groups.map((g) => {
            const isCollapsed = collapsedSet.has(g.key);
            return (
              <section
                key={g.key}
                className="mt-2 mr-2 mb-1 ml-2"
                data-style-hooks="lin-group"
              >
                {g.name && (
                  <header
                    className="flex items-center gap-2 bg-subtle rounded-[7px] min-h-9 pt-0 pr-2.5 pb-0 pl-2.5 text-[13px]"
                    data-style-hooks="lin-group-head"
                  >
                    <button
                      type="button"
                      className="grid place-items-center border-0 hover:text-foreground [background:none] text-muted pt-1 pr-1 pb-1 pl-1 rounded-sm"
                      data-style-hooks="chev"
                      aria-expanded={!isCollapsed}
                      aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${g.name}`}
                      onClick={() =>
                        setCollapsed((c) =>
                          c.includes(g.key)
                            ? c.filter((x) => x !== g.key)
                            : [...c, g.key],
                        )
                      }
                    >
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 16 16"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        style={{
                          transform: isCollapsed ? "rotate(-90deg)" : undefined,
                        }}
                        aria-hidden="true"
                      >
                        <path
                          d="M4 6.5 8 10.5 12 6.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </button>
                    {g.status && statusGlyph(g.status)}
                    <b className="font-semibold">{g.name}</b>
                    <span className="text-muted font-medium text-[12px]">
                      {g.items.length}
                    </span>
                    <span className="flex-1" />
                  </header>
                )}
                {!isCollapsed &&
                  g.items.map((a) => (
                    <AgentRow
                      key={a.id}
                      agent={a}
                      props={display.agentProps}
                      onOpen={onOpen}
                    />
                  ))}
              </section>
            );
          })}
          {!visible.length && (
            <div
              className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
              data-state="no-match"
            >
              <ListFilter size={30} strokeWidth={1.4} aria-hidden="true" />
              <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
                No agents match
              </h2>
              <p>
                Clear the filters to see all {agents.length} agent
                {agents.length === 1 ? "" : "s"} in this workspace.
              </p>
            </div>
          )}
        </div>
      )}
      {creating && (
        <NewAgentDialog
          onClose={() => setCreating(false)}
          onCreated={(agent) => {
            setCreating(false);
            onOpen(agent);
          }}
        />
      )}
    </AgentsListShell>
  );
}
