/* Screen 2 — Agent detail (HOJA-602-A-16).
 *
 * The issue page's layout with the issue page's own pieces: `.lin-head` breadcrumb, the right-aligned
 * `.lin-toolbar`, then `.lin-detail` split into `.lin-detail-main` and the `.lin-props` sidebar. The
 * editable rows are the exported `PropDropdown` and the sidebar's own input shape, so a property row
 * here is the same height and has the same keyboard behaviour as an issue property.
 *
 * Everything on this page is a server fact: the identity and policy are WP04's `agentMember`, and
 * the sessions and their transcript are WP06's session rows, read through the workspace session
 * connection filtered to this agent. Stop calls `agentSessionStop`; the audit event it writes is
 * read back on the audit screen, not invented here.
 *
 * The screen never edits its own copy of a value: it renders the policy the adapter returned, so what
 * you see is what the server stored. `updatePolicy` reports the audit row the server produced, or
 * `null` when it keeps none, and the notice under the form follows that instead of promising a trail.
 *
 * The verifier half (WP10) is the *Claims* section: `verifierClaims` narrowed to this agent, so the
 * page shows what each run claimed, whether it is still waiting, and the verdict rows with the
 * checks a separate principal re-read. Signing is `agentSessionVerify` and belongs to that
 * principal, not to this page: a Verify button here would let the worker's own session sign itself.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy } from "lucide-react";
import { confirmDialog } from "../dialogs";
import { Avatar, IconBtn, Tip } from "../graph/shared";
import { PropDropdown } from "../graph/tasks";
import { getAgentsAdapter } from "./adapter";
import { statusGlyph } from "./AgentsPane";
import {
  sessionStatusName,
  agentStatus,
  verdictColor,
  verdictLabel,
} from "./types";
import type {
  AgentDetail,
  AgentEvent,
  AgentPolicy,
  AgentSession,
  Claim,
} from "./types";

const EVENT_LABELS: Record<AgentEvent["kind"], string> = {
  thought: "Thought",
  action: "Action",
  elicitation: "Needs input",
  response: "Response",
  error: "Error",
  prompt: "Prompt",
};

function relTime(iso?: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function stamp(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

/* One editable sidebar row: the issue page's `.lin-prop` shape, saved on blur or Enter. Uncontrolled
 * inputs are re-keyed by the stored value, so a save redraws the field with what came back rather
 * than keeping the draft.
 *
 * The field states its own outcome. A policy save is a round trip, and the page's notice line sits
 * at the bottom of the main column — below the fold of a long agent page and in a different column
 * from the control that was just edited. So the row that was edited says "Saving…", "Saved" or
 * "Not saved" itself, in `aria-live="polite"` text, and the page notice stays for the longer story
 * (which audit row the server wrote). Nothing here claims a save the promise refused. */
function PolicyField({
  label,
  icon,
  value,
  placeholder,
  type = "text",
  min,
  disabled,
  onSave,
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  placeholder?: string;
  type?: "text" | "number";
  min?: number;
  disabled?: boolean;
  onSave: (next: string) => void | Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">(
    "idle",
  );
  const timer = useRef<number | undefined>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );
  const commit = async (next: string) => {
    /* A "Saved" from the previous edit may still be ticking on this row; clear it first, or it
     * fires mid-save and paints the new outcome away — including a refusal. */
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = undefined;
    }
    setState("saving");
    try {
      await onSave(next);
      setState("saved");
      timer.current = window.setTimeout(() => setState("idle"), 1600);
    } catch {
      setState("failed");
      /* The server kept its value, so the field shows it again: a draft that was refused must not
         sit there looking saved. */
      if (inputRef.current) inputRef.current.value = value;
    }
  };
  return (
    <>
      <div
        className="relative flex items-center gap-2.5 min-h-8.5 text-[13px]"
        data-style-hooks="lin-prop"
      >
        <span className="flex flex-none items-center gap-2 text-muted w-24">
          {icon}
          {label}
        </span>
        <span className="flex flex-1 items-center min-w-0 gap-1.75">
          <input
            ref={inputRef}
            key={value}
            type={type}
            min={min}
            step={type === "number" ? 1 : undefined}
            defaultValue={value}
            placeholder={placeholder}
            aria-label={label}
            disabled={disabled}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                (e.target as HTMLInputElement).blur();
              }
            }}
            onBlur={(e) => {
              const next = e.target.value.trim();
              if (next !== value.trim()) void commit(next);
            }}
            className="border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 w-full min-w-0 border-solid rounded-md pt-1.25 pr-2 pb-1.25 pl-2 text-[13px]"
          />
        </span>
      </div>
      {/* The outcome sits under the row it belongs to, in the value column (the label column is
          6rem plus the row's 0.625rem gap), and disappears on its own; it adds no permanent gap to
          the sidebar. */}
      {state !== "idle" && (
        <p
          className="text-[12px] text-muted mt-0 mr-0 mb-1.5 pl-[calc(6rem+0.625rem)]"
          role="status"
          aria-live="polite"
          data-field-state={state}
        >
          {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : "Not saved"}
        </p>
      )}
    </>
  );
}

export function AgentDetailPage({
  agentKey,
  workspaceName,
  viewerName,
  onBack,
  onOpenQueue,
}: {
  /** `AgentMember.id`. */
  agentKey: string;
  workspaceName: string;
  viewerName: string;
  onBack: () => void;
  onOpenQueue: (agentKey: string) => void;
}) {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [claimsError, setClaimsError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState(false);

  /* Returns the promise so a failed write can re-read the row and *then* state the refusal: the
   * other order lets the successful reload clear the error that explains what happened. */
  const load = useCallback(
    () =>
      Promise.all([
        getAgentsAdapter().getAgent(agentKey),
        // A refused claims read is not an empty list; show the refusal beside the claims.
        getAgentsAdapter()
          .listClaims({ agentId: agentKey })
          .then((page) => ({ rows: page.claims, error: "" }))
          .catch((e: Error) => ({ rows: [] as Claim[], error: e.message })),
      ])
        .then(([d, claimed]) => {
          setDetail(d);
          setClaims(claimed.rows);
          setClaimsError(claimed.error);
          setError(
            d
              ? ""
              : `No agent member with the id ${agentKey} exists in this workspace.`,
          );
        })
        .catch((e: Error) => {
          setDetail(null);
          setClaims([]);
          setClaimsError("");
          setError(e.message);
        }),
    [agentKey],
  );

  useEffect(() => {
    load();
  }, [load]);

  const active = useMemo(
    () =>
      detail?.sessions.find(
        (s) => s.status !== "done" && s.status !== "failed",
      ) || null,
    [detail],
  );
  const events = useMemo(
    () => [...(detail?.events || [])].sort((a, b) => (a.at < b.at ? -1 : 1)),
    [detail],
  );

  const applyPolicy = (policy: AgentPolicy, auditNotice: string) => {
    setDetail((d) => (d ? { ...d, policy } : d));
    setNotice(auditNotice);
  };

  const savePolicy = async (patch: Partial<AgentPolicy>) => {
    setBusy("policy");
    setNotice("");
    setError("");
    try {
      const { policy, audit } = await getAgentsAdapter().updatePolicy(
        agentKey,
        patch,
        viewerName,
      );
      applyPolicy(
        policy,
        audit
          ? `Saved. The server recorded ${audit.action} by ${audit.actor}.`
          : "Saved. Agent edits land in the workspace change log; the audit ledger records session stops and the other actions it names.",
      );
      load();
    } catch (e) {
      const message = (e as Error).message;
      await load();
      setError(message);
      /* Re-thrown so the field that was edited can say "Not saved": the page error carries the
       * server's sentence, the field confirms which row it belongs to. */
      throw e;
    } finally {
      setBusy("");
    }
  };

  /* HOJA-602-A-22: the harness half of WP12's policy is its own row and its own mutation, so a
   * refusal is the server's sentence (unknown name, alias, default outside the allowlist) rather
   * than a message this screen composes. A failed write re-reads the row first, so the field shows
   * what the server actually stored. */
  const saveHarness = async (patch: {
    defaultHarness?: string | null;
    harnessAllowlist?: string[];
  }) => {
    setBusy("harness");
    setNotice("");
    setError("");
    try {
      const { harnesses } = await getAgentsAdapter().updateHarnessPolicy(
        agentKey,
        patch,
        viewerName,
      );
      setDetail((d) => (d ? { ...d, harnesses } : d));
      setNotice(
        harnesses.defaultHarness
          ? `Saved. Runs delegated to this agent queue for ${harnesses.defaultHarness}.`
          : "Saved. No harness chosen, so runs queue for pi.",
      );
      load();
    } catch (e) {
      const message = (e as Error).message;
      await load();
      setError(message);
      throw e;
    } finally {
      setBusy("");
    }
  };

  const stop = async (session: AgentSession) => {
    if (
      !(await confirmDialog(`Stop the session on ${session.issueIdentifier}?`, {
        danger: true,
      }))
    )
      return;
    setBusy("stop");
    try {
      const { session: next } = await getAgentsAdapter().stopSession(
        session.id,
      );
      setDetail((d) =>
        d
          ? {
              ...d,
              sessions: d.sessions.map((s) => (s.id === next.id ? next : s)),
            }
          : d,
      );
      setNotice(
        `Session on ${next.issueIdentifier} stopped. The server recorded agent.session.stop for it — see the audit log.`,
      );
      void load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  if (!detail) {
    return (
      <section
        className="flex flex-col min-w-0 h-full bg-panel"
        data-surface="agent-detail"
        aria-label={`${agentKey}: agent`}
      >
        <header className="flex flex-none items-center border-b border-line gap-2 min-h-12.25 pt-0 pr-5 pb-0 pl-5 border-b-line shell-phone:flex-wrap shell-phone:gap-y-1">
          <nav
            className="flex flex-1 items-center gap-1.75 text-[13px] text-muted min-w-0 shell-phone:min-w-0 shell-phone:flex-wrap shell-phone:gap-y-0.5"
            aria-label="Breadcrumb"
          >
            <button
              type="button"
              className="inline-flex cursor-pointer items-center whitespace-nowrap border-0 hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [background:none] pt-0 pr-0 pb-0 pl-0 text-muted [font:inherit] gap-1.5 shell-phone:min-w-11! shell-phone:min-h-11! shell-phone:whitespace-normal shell-phone:max-w-full"
              onClick={onBack}
            >
              ← Agents
            </button>
            <span className="text-muted opacity-70">›</span>
            <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
              {agentKey}
            </span>
          </nav>
        </header>
        {error ? (
          <div
            className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
            role="alert"
            data-state="error"
          >
            <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
              This agent could not be opened
            </h2>
            <p>{error}</p>
            <div className="flex justify-center gap-2">
              <button
                type="button"
                className="inline-flex items-center border border-line hover:bg-subtle disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 border-solid bg-panel text-foreground rounded-[7px] pt-2 pr-3.5 pb-2 pl-3.5 text-[13px] font-[550] shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={() => {
                  setError("");
                  load();
                }}
              >
                Retry
              </button>
              <button
                type="button"
                className="inline-flex items-center border border-line hover:bg-subtle disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 border-solid bg-panel text-foreground rounded-[7px] pt-2 pr-3.5 pb-2 pl-3.5 text-[13px] font-[550] shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={onBack}
              >
                ← Agents
              </button>
            </div>
          </div>
        ) : (
          <p
            className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
            role="status"
          >
            Loading agent…
          </p>
        )}
      </section>
    );
  }

  const { agent, policy, sessions } = detail;
  const status = agentStatus(agent.status);
  const running = sessions.filter((s) => s.status === "active").length;
  const caps = `${agent.capacity} ${agent.capacity === 1 ? "slot" : "slots"}`;

  return (
    <section
      className="flex flex-col min-w-0 h-full bg-panel"
      data-surface="agent-detail"
      data-style-hooks="lin-page"
      aria-label={`${agent.key}: ${agent.name}`}
    >
      <header
        className="flex flex-none items-center border-b border-line gap-2 min-h-12.25 pt-0 pr-5 pb-0 pl-5 border-b-line shell-phone:flex-wrap shell-phone:gap-y-1"
        data-style-hooks="lin-head"
      >
        <nav
          className="flex flex-1 items-center gap-1.75 text-[13px] text-muted min-w-0 shell-phone:min-w-0 shell-phone:flex-wrap shell-phone:gap-y-0.5"
          aria-label="Breadcrumb"
        >
          <button
            type="button"
            className="inline-flex cursor-pointer items-center whitespace-nowrap border-0 hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [background:none] pt-0 pr-0 pb-0 pl-0 text-muted [font:inherit] gap-1.5 shell-phone:min-w-11! shell-phone:min-h-11! shell-phone:whitespace-normal shell-phone:max-w-full"
            onClick={onBack}
          >
            ← Agents
          </button>
          <span className="text-muted opacity-70">›</span>
          <span>{workspaceName}</span>
          <span className="text-muted opacity-70">›</span>
          <span className="font-semibold text-muted">{agent.key}</span>
          <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
            {agent.name}
          </span>
        </nav>
        <div className="flex flex-none items-center gap-0.5 ml-auto">
          <IconBtn
            label="Open run queue"
            title="Open run queue"
            onClick={() => onOpenQueue(agent.id)}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path d="M2 4h12M2 8h12M2 12h8" strokeLinecap="round" />
            </svg>
          </IconBtn>
        </div>
      </header>
      <div
        className="flex flex-none items-center border-b border-line justify-end gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line"
        data-style-hooks="lin-toolbar"
      >
        <Tip label={copied ? "Copied" : `Copy ${agent.key}`}>
          <span>
            <IconBtn
              label="Copy agent id"
              title="Copy agent id"
              onClick={() => {
                void navigator.clipboard
                  .writeText(agent.id)
                  .then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1500);
                  })
                  .catch(() => setCopied(false));
              }}
            >
              <Copy size={15} />
            </IconBtn>
          </span>
        </Tip>
      </div>

      <div
        className="flex flex-1 flex-col lg:flex-row min-h-0 [@media_(max-width:900px)]:flex-col"
        data-style-hooks="lin-detail"
      >
        <main
          className="flex-1 overflow-y-auto min-w-0 pt-6.5 pr-10 pb-20 pl-10 max-w-205 [@media_(max-width:900px)]:max-w-none [@media_(max-width:900px)]:pt-5 [@media_(max-width:900px)]:pr-5 [@media_(max-width:900px)]:pb-5 [@media_(max-width:900px)]:pl-5"
          data-style-hooks="lin-detail-main"
        >
          <h1 className="overflow-hidden border-0 focus:outline-none flex items-center w-full bg-transparent text-foreground text-[21px] font-[650] tracking-[-.015em] leading-[1.3] pt-0 pr-0 pb-0 pl-0 mt-0 mr-0 mb-3.5 ml-0 resize-none font-[inherit] focus:[outline:none] gap-2.5">
            {statusGlyph(agent.status)} {agent.name}
          </h1>
          <p
            className="relative wrap-break-word text-[14px] leading-[1.6] text-foreground [word-break:break-word] mt-0 mr-0 mb-2 ml-0 min-h-6"
            data-style-hooks="lin-detail-desc"
          >
            {status.name} · {agent.model || "no model set"} · {caps} · {running}{" "}
            running ·{" "}
            {agent.monthlyBudgetCredits === null
              ? "workspace budget default"
              : `${agent.monthlyBudgetCredits} credits/month`}{" "}
            · {agent.runnerPool || "any runner pool"} · created{" "}
            {stamp(agent.createdAt)}
          </p>

          <section
            className="border-t border-line border-t-line pt-4 mt-1.5"
            data-style-hooks="lin-activity"
            aria-label="Session activity"
          >
            <header className="flex items-center mb-3">
              <b className="text-[14px]">Session activity</b>
              <span className="flex-1" />
              {active ? (
                <button
                  type="button"
                  className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 [background:none] text-muted text-[12px] font-medium gap-1.75 pt-1.25 pr-2 pb-1.25 pl-2 rounded-md"
                  data-style-hooks="lin-subscribe-btn"
                  disabled={busy === "stop"}
                  onClick={() => void stop(active)}
                >
                  {busy === "stop"
                    ? "Stopping…"
                    : `Stop ${active.issueIdentifier}`}
                </button>
              ) : (
                /* Not "no session is running": the server reports no run state at all yet, and
                 * the difference matters to a reader deciding whether to delegate to this agent. */
                <span className="text-[12px] text-muted">
                  No run is active right now.
                </span>
              )}
            </header>
            {events.map((e) => (
              <div
                key={e.id}
                className="flex items-start gap-2.5 text-[13px] text-muted pt-1.75 pr-0 pb-1.75 pl-0"
                data-style-hooks="lin-event"
                data-kind={e.kind}
              >
                <Avatar name={agent.name} size={18} />
                <span>
                  <b
                    className={
                      (e.kind === "error" ? "text-danger" : "text-foreground") +
                      " block mt-3 mr-0 mb-3 ml-0 font-semibold"
                    }
                  >
                    {EVENT_LABELS[e.kind]}
                  </b>
                  {e.tool ? (
                    <>
                      {" "}
                      <code>{e.tool}</code>
                    </>
                  ) : null}{" "}
                  — {e.text}
                </span>
                <time className="flex-none ml-auto text-[12px]">
                  {relTime(e.at)}
                </time>
              </div>
            ))}
            {!events.length && (
              <p className="text-[12px] text-muted">
                No run has posted an activity for this agent yet.
              </p>
            )}
          </section>

          <section
            className="border-t border-line border-t-line pt-4 mt-1.5"
            aria-label="Sessions"
          >
            <header className="flex items-center mb-3">
              <b className="text-[14px]">Sessions</b>
            </header>
            {sessions.map((s) => (
              <div
                key={s.id}
                className="flex items-start gap-2.5 text-[13px] text-muted pt-1.75 pr-0 pb-1.75 pl-0"
                data-session-status={s.status}
                data-session-state={s.state}
                data-session-id={s.id}
              >
                <span
                  className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
                  title={`state: ${s.state}${s.admitted ? " · admitted" : ""}`}
                >
                  {sessionStatusName(s.status)}
                </span>
                <span>
                  <b className="text-foreground font-semibold">
                    {s.issueIdentifier}
                  </b>{" "}
                  {s.issueTitle}
                  {s.reason ? (
                    <span className="text-[12px] text-muted">
                      {" "}
                      — {s.reason}
                    </span>
                  ) : null}
                </span>
                <time className="flex-none ml-auto text-[12px]">
                  {relTime(s.endedAt || s.startedAt)}
                </time>
              </div>
            ))}
            {!sessions.length && (
              <p className="text-[12px] text-muted">
                No run is stored for this agent. Setting the Delegate field on a
                task opens one; the{" "}
                <button
                  type="button"
                  className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 [background:none] text-muted text-[13px] pt-1.25 pr-1.5 pb-1.25 pl-1.5 rounded-md"
                  onClick={() => onOpenQueue(agent.id)}
                >
                  run queue
                </button>{" "}
                to see the queue as it stands.
              </p>
            )}
          </section>

          <section
            className="border-t border-line border-t-line pt-4 mt-1.5"
            aria-label="Claims"
          >
            <header className="flex items-center mb-3">
              <b className="text-sm">Claims</b>
              <span className="flex-1" />
              <span className="text-xs text-muted">
                {claims.some((c) => c.awaiting)
                  ? "A claim is waiting on a verifier"
                  : "Nothing is waiting on a verifier"}
              </span>
            </header>
            {claimsError && (
              <p className="text-danger text-sm" role="alert" data-claims-error>
                {claimsError}
              </p>
            )}
            {claims.map((claim) => (
              <div
                key={claim.sessionId}
                className="flex items-start gap-2.5 text-sm text-muted pt-1.75 pr-0 pb-1.75 pl-0"
                data-claim-row={claim.sessionId}
                data-verdict={claim.verdict || "awaiting"}
              >
                <Avatar name={agent.name} size={18} />
                <span className="min-w-0 wrap-break-word">
                  <b className="text-foreground font-semibold">
                    {claim.issueIdentifier}
                  </b>{" "}
                  {claim.issueTitle}
                  {" — "}
                  {claim.summary || "no summary"}{" "}
                  <span
                    className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-xs rounded-full pt-0 pr-2 pb-0 pl-2 text-muted bg-panel"
                    data-verdict-chip={claim.verdict || "awaiting"}
                    title={`claimer model ${claim.workerModel || "unknown"} · claimed ${claim.claimAt}`}
                  >
                    <i
                      className="inline-block size-2 rounded-full"
                      style={{ background: verdictColor(claim.verdict) }}
                    />
                    {claim.awaiting && !claim.verdict
                      ? "Awaiting verifier"
                      : verdictLabel(claim.verdict)}
                  </span>
                  {claim.verifications.length > 0 && (
                    <span
                      data-verification-checks={claim.verifications[0].id}
                      className="block mt-1"
                    >
                      {claim.verifications[0].checks.map((check) => (
                        <span
                          key={check.name}
                          className="block text-xs text-muted"
                          data-check={check.name}
                          data-outcome={check.outcome}
                        >
                          {check.name}: {check.outcome}
                          {check.detail ? ` — ${check.detail}` : ""}
                        </span>
                      ))}
                      {claim.verifications[0].rationale && (
                        <span className="block text-xs text-muted">
                          “{claim.verifications[0].rationale}” —{" "}
                          {claim.verifications[0].verifierKind} verifier
                          {claim.verifications[0].verifierModel
                            ? ` (${claim.verifications[0].verifierModel})`
                            : ""}
                        </span>
                      )}
                    </span>
                  )}
                </span>
                <time className="flex-none ml-auto text-xs">
                  {relTime(claim.verifiedAt || claim.claimAt)}
                </time>
              </div>
            ))}
            {!claims.length && !claimsError && (
              <p className="text-xs text-muted">
                No run of this agent has finished with a claim. A run submits
                one at finish (<code>/runner/finish</code>), and until a
                separate principal signs it the session stays awaiting
                verification.
              </p>
            )}
          </section>

          {notice && (
            <p className="text-[12px] text-muted mt-3" role="status">
              {notice}
            </p>
          )}
          {error && (
            <p
              className="border border-danger/40 mt-4 mr-6 mb-4 ml-6 pt-3 pr-4 pb-3 pl-4 border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
              role="alert"
            >
              {error}
            </p>
          )}
        </main>

        <aside
          className="flex-none overflow-y-auto border-t border-line lg:w-72 lg:border-t-0 lg:border-l w-72.5 border-l border-l-line pt-5.5 pr-5 pb-15 pl-5 [@media_(max-width:1100px)]:w-62.5 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:border-l-0 [@media_(max-width:900px)]:border-t [@media_(max-width:900px)]:border-t-line"
          data-style-hooks="lin-props"
          aria-label="Agent settings"
        >
          <h3 className="text-[12px] font-semibold text-muted mt-0 mr-0 mb-2.5 ml-0">
            Agent
          </h3>
          <PolicyField
            label="Name"
            icon={<span aria-hidden="true">◈</span>}
            value={policy.name}
            disabled={busy === "policy"}
            onSave={(name) => savePolicy({ name })}
          />
          <PolicyField
            label="Display name"
            icon={<span aria-hidden="true">◍</span>}
            value={policy.displayName || ""}
            placeholder="Same as the name"
            disabled={busy === "policy"}
            onSave={(displayName) =>
              savePolicy({ displayName: displayName || null })
            }
          />
          <PolicyField
            label="Capacity"
            icon={<span aria-hidden="true">▦</span>}
            type="number"
            min={1}
            value={String(policy.capacity)}
            disabled={busy === "policy"}
            onSave={(next) => {
              const n = Number(next);
              /* A value the policy cannot hold is refused here, before the round trip, and the
                 field says "Not saved" like any other refusal rather than reporting a save. */
              if (!Number.isFinite(n) || n < 1)
                return Promise.reject(
                  new Error("Capacity must be at least 1"),
                );
              return savePolicy({ capacity: n });
            }}
          />
          <PolicyField
            label="Budget"
            icon={<span aria-hidden="true">▮</span>}
            type="number"
            min={0}
            value={
              policy.monthlyBudgetCredits === null
                ? ""
                : String(policy.monthlyBudgetCredits)
            }
            placeholder="Workspace default"
            disabled={busy === "policy"}
            onSave={(next) =>
              savePolicy({
                monthlyBudgetCredits: next === "" ? null : Number(next),
              })
            }
          />
          <PolicyField
            label="Model"
            icon={<span aria-hidden="true">◇</span>}
            value={policy.model || ""}
            placeholder="Not set"
            disabled={busy === "policy"}
            onSave={(next) => savePolicy({ model: next || null })}
          />
          <PolicyField
            label="Runner pool"
            icon={<span aria-hidden="true">▤</span>}
            value={policy.runnerPool || ""}
            placeholder="Any pool"
            disabled={busy === "policy"}
            onSave={(next) => savePolicy({ runnerPool: next || null })}
          />
          <PolicyField
            label="Timezone"
            icon={<span aria-hidden="true">◷</span>}
            value={policy.timezone || ""}
            placeholder="UTC"
            disabled={busy === "policy"}
            onSave={(next) => savePolicy({ timezone: next || null })}
          />
          <PolicyField
            label="Hours"
            icon={<span aria-hidden="true">◔</span>}
            value={policy.workingHours || ""}
            placeholder='{"days":["mon","tue","wed","thu","fri"],"from":"08:00","to":"19:00"}'
            disabled={busy === "policy"}
            onSave={(next) => savePolicy({ workingHours: next || null })}
          />
          <PropDropdown
            label="State"
            icon={<span aria-hidden="true">◎</span>}
            value={policy.archived ? "Archived" : "Active"}
            onPick={(id) => {
              if (!id || id === (policy.archived ? "archived" : "active"))
                return;
              if (id !== "archived") {
                void savePolicy({ archived: false });
                return;
              }
              /* Archiving is one click away from Active and nothing on this page undoes it by
                 accident: the agent drops out of the Delegate picker and stops being offered work.
                 So the pick asks first, in the same two-step this surface uses for stopping a run
                 and revoking a runner. Restoring stays one step. */
              void confirmDialog(
                `Archive ${agent.name}? It stops being offered delegated work and leaves the Delegate picker. ` +
                  "Existing runs and their history stay, and State can set it back to Active.",
                { danger: true },
              ).then((ok) => {
                if (ok) void savePolicy({ archived: true });
              });
            }}
            disabled={busy === "policy"}
            options={[
              { id: "active", selected: !policy.archived, label: "Active" },
              { id: "archived", selected: policy.archived, label: "Archived" },
            ]}
          />

          <div className="mt-4 mb-1 text-xs font-semibold text-muted">
            {/* "Run policy", not "Policy (WP12)": the work-package number is our bookkeeping, not
                something a workspace admin can act on. */}
            Run policy
          </div>
          <PolicyField
            label="Harness"
            icon={<span aria-hidden="true">⌬</span>}
            value={detail.harnesses.defaultHarness || ""}
            placeholder={`pi (default) — one of ${detail.harnesses.known.join(", ")}`}
            disabled={busy === "harness"}
            onSave={(next) => saveHarness({ defaultHarness: next || null })}
          />
          <PolicyField
            label="Allowed harnesses"
            icon={<span aria-hidden="true">⌸</span>}
            value={detail.harnesses.harnessAllowlist.join(", ")}
            placeholder="Any profile"
            disabled={busy === "harness"}
            onSave={(next) =>
              saveHarness({
                harnessAllowlist: next
                  .split(",")
                  .map((name) => name.trim())
                  .filter(Boolean),
              })
            }
          />
          <div className="relative flex items-start gap-2.5 min-h-8 text-sm">
            <span className="flex flex-none items-center gap-2 text-muted w-24">
              <span aria-hidden="true">⌗</span>Runs on
            </span>
            <span
              className="flex-1 min-w-0 text-xs text-muted"
              data-harness-effective={
                detail.harnesses.defaultHarness ||
                detail.harnesses.harnessAllowlist[0] ||
                "pi"
              }
            >
              {detail.harnesses.defaultHarness
                ? `queues for ${detail.harnesses.defaultHarness}`
                : detail.harnesses.harnessAllowlist.length
                  ? `queues for the first allowed profile (${[...detail.harnesses.harnessAllowlist].sort()[0]})`
                  : "queues for pi until a policy chooses"}{" "}
              · a run is only handed to a runner that declares it
            </span>
          </div>
          <p className="text-xs text-muted">
            The harness decides which CLI a delegated run executes under, and
            the queue will not hand it to a runner that did not declare it. An
            unknown profile, an alias (claude is claude-code) and a default the
            allowlist forbids are all refused by the server, not saved and
            corrected here. Tools, repos, secrets, network and approvers have
            their own columns in the policy row and no control on this page yet.
          </p>
        </aside>
      </div>
    </section>
  );
}
