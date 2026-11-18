import React, { useEffect, useState } from "react";
import { authHeaders, IssueChip } from "./markdown";
import { sessionStatus } from "./agents/adapter";
import { SESSION_TYPE, sessionStatusName } from "./agents/types";

/* One task the chat handed to an agent. The server appends a fenced `hoja-delegation` block per
 * task to the reply (agent_chat.delegation_blocks); this card reads the session it names and keeps
 * reading it until the session stops moving, so the chat shows progress without the model polling. */
export type Delegation = {
  issueId: string;
  identifier: string | null;
  title: string | null;
  agentName: string | null;
  sessionId: string | null;
};

const DELEGATION_BLOCK = /```hoja-delegation\n([\s\S]*?)\n```/g;

export function splitDelegations(content: string): {
  text: string;
  delegations: Delegation[];
} {
  const delegations: Delegation[] = [];
  for (const match of content.matchAll(DELEGATION_BLOCK)) {
    try {
      const row = JSON.parse(match[1]);
      if (row && typeof row.issueId === "string") delegations.push(row);
    } catch {
      /* A block cut off mid-stream parses on the next chunk. */
    }
  }
  return { text: content.replace(DELEGATION_BLOCK, "").trim(), delegations };
}

/* agent_schedule.REASON_ORDER, in words. */
const REASON_TEXT: Record<string, string> = {
  capacity: "the agent is at capacity",
  budget: "the monthly budget is spent",
  window: "outside the agent's working hours",
  blocked: "the task is blocked",
  approval: "waiting for approval",
  lease_held: "another run holds the task",
};

const SETTLED = new Set(["in_review", "done", "failed", "cancelled"]);
const POLL_MS = 5000;
const SESSION_QUERY =
  "query ChatDelegation($id:String!){agentSession(id:$id){state status queueReason}}";

type Live = { state: string; status: string; queueReason: string[] };

function useSession(sessionId: string | null): Live | null | "unavailable" {
  const [live, setLive] = useState<Live | null | "unavailable">(null);
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => {
      fetch("/graphql", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          query: SESSION_QUERY,
          variables: { id: sessionId },
        }),
      })
        .then((r) => r.json())
        .then((d) => {
          if (cancelled) return;
          const row = d?.data?.agentSession as Live | null | undefined;
          if (!row) {
            setLive("unavailable");
            return;
          }
          setLive(row);
          if (!SETTLED.has(row.state)) timer = setTimeout(read, POLL_MS);
        })
        .catch(() => {
          if (!cancelled) timer = setTimeout(read, POLL_MS);
        });
    };
    read();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId]);
  return live;
}

export function DelegationCard({ delegation }: { delegation: Delegation }) {
  const live = useSession(delegation.sessionId);
  const agent = delegation.agentName || "The agent";
  const known = live && live !== "unavailable" ? live : null;
  const group = known
    ? sessionStatus(known.state, known.status as never)
    : "queued";
  const reasons = known?.queueReason?.length
    ? known.queueReason.map((r) => REASON_TEXT[r] || r).join(", ")
    : "";
  const label =
    live === "unavailable" || !delegation.sessionId
      ? "Status unavailable"
      : known
        ? sessionStatusName(group)
        : "Checking…";
  return (
    <div
      className="my-2 flex flex-col gap-2 rounded-xl border border-line bg-panel p-4"
      data-delegation-card={delegation.issueId}
      data-session-state={known?.state ?? ""}
    >
      {/* The chip names the task and links to it; the title is only spelled out when there is no
          identifier to link. */}
      <div className="flex min-w-0">
        {delegation.identifier ? (
          <IssueChip identifier={delegation.identifier} />
        ) : (
          <span className="min-w-0 truncate">
            {delegation.title || "This task"}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm text-muted">
        <span
          className="inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-full border border-line bg-panel px-2 text-xs font-medium"
          data-session-chip={known ? group : "unknown"}
          title={known ? `${agent} · ${label} · ${known.state}` : label}
        >
          <i
            className="size-2 shrink-0 rounded-full not-italic"
            style={{
              background: known ? SESSION_TYPE[group].color : "#a1a1aa",
            }}
          />
          {agent} · {label}
        </span>
        {reasons && <span className="min-w-0">Queued: {reasons}</span>}
      </div>
    </div>
  );
}
