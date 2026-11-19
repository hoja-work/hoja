/* Screens 4 and 5 — Runners list and Runner detail (HOJA-602-A-16, WP13 T059).
 *
 * Both screens are the issue list's and the issue page's own pieces:
 *
 *   - the list is `IssueRow` (graph/tasks.tsx), the way `RunQueue.tsx` uses it, inside the shared
 *     `.lin-*` shell — so the identifier column, the 42 px rhythm and the meta chips are the
 *     issue list's, not a lookalike;
 *   - the detail is the `.lin-detail` + `.lin-props` split with the exported `PropDropdown` for
 *     property rows, the shape `AgentDetail.tsx` already proves.
 *
 * Runners and their leases are server rows. Enrollment tokens are shown only once, and the
 * runner detail offers distinct machine and Connect exposure revocation controls. The server
 * omits bearer secrets from these reads; Connect-disabled workspaces cannot revoke exposures.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import { Copy, Lock, LockOpen, Plus, SlidersHorizontal } from "lucide-react";
import { confirmDialog } from "../dialogs";
import { GraphStatusIcon, IconBtn, LabelChip, Tip } from "../graph/shared";
import {
  DisplayPanel,
  IssueRow,
  PropDropdown,
  defaultDisplay,
  type IssueRowModel,
  type LinDisplay,
} from "../graph/tasks";
import { getAgentsAdapter } from "./adapter";
import { AGENTS_TABS, AgentsListShell, SearchField } from "./chrome";
import {
  RUNNER_STATUSES,
  runnerStatus,
  ttlLabel,
  ttlSecondsLeft,
} from "./types";
import type {
  ConnectExposure,
  Runner,
  RunnerEnrollment,
  RunnerEnrollmentGrant,
  RunnerLease,
  RunnerStatus,
} from "./types";

/* A runner row is an `IssueRowModel`: the runner's id is the identifier column, its name is the
 * title, online/offline is the status glyph and `lastSeenAt` is the age the glyph explains. The
 * labels ride the model's own `labels`, so they render through the issue row's `LabelChip`. */
export function runnerRow(r: Runner): IssueRowModel {
  const tone = runnerStatus(r.status);
  return {
    id: r.id,
    identifier: r.id,
    title: r.name,
    state: { name: tone.name, type: tone.type, color: tone.color },
    stateChangedAt: r.lastSeenAt,
    updatedAt: r.lastSeenAt,
    labels: { nodes: r.labels.map((name) => ({ id: name, name })) },
  };
}

/* The row's second 14 px column is the one an issue spends on priority. A runner has no
 * priority; it has an enrollment, which is what FR-018 makes scarce. Filling the column keeps the
 * identifier where the issue list puts it instead of shifting it 23 px left. */
function credentialGlyph(r: Runner) {
  const label = r.revoked
    ? `Revoked${r.revokedAt ? ` ${r.revokedAt.slice(0, 10)}` : ""}: this runner's token no longer authenticates`
    : r.enabled
      ? "Enrolled: a one-time lease token was presented and accepted"
      : "Disabled: this runner is refused at the lease route";
  return (
    <span
      className="relative inline-grid place-items-center"
      data-tip={label}
      role="img"
      aria-label={label}
      data-credential={r.revoked ? "revoked" : "enrolled"}
    >
      {r.revoked || !r.enabled ? (
        <LockOpen size={14} aria-hidden="true" />
      ) : (
        <Lock size={14} aria-hidden="true" />
      )}
    </span>
  );
}

/* The lease a runner holds, as the row's trailing chip: the task it took and the harness it runs.
 * A runner holding nothing says nothing — an empty chip would be a second status column. */
function leaseChip(r: Runner) {
  if (!r.lease) return null;
  const task = r.lease.taskIdentifier || r.lease.taskId.slice(0, 8);
  return (
    <span
      className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] rounded-[48px] px-2 text-muted bg-panel"
      data-lease={r.lease.id}
      data-lease-state={r.lease.state}
      title={`Holding ${task} · ${r.lease.harness} · fence ${r.lease.fence}${r.lease.leaseUntil ? ` · until ${r.lease.leaseUntil}` : ""}`}
    >
      <i style={{ background: "#5e6ad2" }} />
      {task} · {r.lease.harness}
    </span>
  );
}

/* A labelled action inside `.lin-toolbar`. The toolbar's height is its content — a 28 px
 * `.lin-icon-btn` inside 8 px of padding — and both the issue list and the issue page are measured
 * against it, so a `.lin-btn` at its default ~34 px would break that parity on this screen alone.
 * Pinning the height keeps the shared `.lin-btn` styling and the shared toolbar geometry. */
const TOOLBAR_ACTION = { height: 28, padding: "0 12px" } as const;

const RUNNER_DISPLAY: LinDisplay = {
  ...defaultDisplay,
  grouping: "status",
  ordering: "updated",
  props: {
    ...defaultDisplay.props,
    // An issue row's priority column is spent on enrollment here, and assignee/project/cycle/
    // dueDate/created have no runner counterpart.
    priority: false,
    assignee: false,
    project: false,
    cycle: false,
    dueDate: false,
    created: false,
  },
};

const COMMAND_BOX: CSSProperties = {
  margin: "4px 0 6px",
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid var(--line)",
  background: "var(--subtle)",
  overflowX: "auto",
  fontSize: 12,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

/* One step of the hand-off: a heading, the line to paste, its copy button and the one thing
 * that has to be said about it. All three steps share this shape so the block reads as a
 * sequence (HOJA-712) rather than as three unrelated panels. */
function CommandStep({
  step,
  title,
  command,
  note,
  copied,
  onCopy,
}: {
  step: string;
  title: string;
  command: string | null;
  note: string;
  copied: boolean;
  onCopy: () => void;
}) {
  /* The three boxes keep the attribute names the checks already select (`data-enroll-command`
   * is the enroll line, the one a test can prove carries this token), spelled out rather than
   * composed so a rename cannot quietly break them. */
  const attribute =
    step === "install"
      ? { "data-install-command": true }
      : step === "start"
        ? { "data-start-command": true }
        : step === "connect"
          ? { "data-connect-command": true }
          : { "data-enroll-command": true };
  return (
    <li data-enroll-step={step}>
      <b>{title}</b>
      {command ? (
        <pre {...attribute} style={COMMAND_BOX}>
          {command}
        </pre>
      ) : null}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        {command && (
          <button
            type="button"
            className="inline-flex items-center rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] text-foreground hover:bg-subtle disabled:opacity-50"
            onClick={onCopy}
          >
            {copied ? "Copied" : `Copy ${step} command`}
          </button>
        )}
        <span className="text-[12px] text-muted">{note}</span>
      </div>
    </li>
  );
}

/* Enroll (HOJA-635) — the one control on this screen that hands out a credential.
 *
 * Three states, and the middle one is the whole point:
 *
 *   form   → name + lifetime, then `runnerEnrollmentCreate`
 *   token  → the plaintext, shown once, with the three commands that use it: install,
 *            enroll, start (the last two are separate because enrolling exits — HOJA-712)
 *   list   → every token in the workspace and whether it can still be used
 *
 * The plaintext lives in this component's state and nowhere else: it is not in a URL, not in
 * localStorage and not refetched — the server stores only its sha256, so there is nothing to
 * refetch. Closing the panel drops it, and the block says so instead of letting someone assume
 * they can come back for it.
 */
function EnrollPanel({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [ttl, setTtl] = useState("1440");
  const [grant, setGrant] = useState<RunnerEnrollmentGrant | null>(null);
  const [enrollments, setEnrollments] = useState<RunnerEnrollment[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");

  const refresh = useCallback(() => {
    getAgentsAdapter()
      .listRunnerEnrollments()
      .then(setEnrollments)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const copy = (what: string, value: string) => {
    void navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(what);
        window.setTimeout(() => setCopied(""), 1500);
      })
      .catch(() => setCopied(""));
  };

  const create = async () => {
    const label = name.trim();
    if (!label || busy) return;
    setBusy("create");
    setError("");
    try {
      const minutes = ttl.trim() === "" ? undefined : Number(ttl);
      const created = await getAgentsAdapter().createRunnerEnrollment(
        label,
        Number.isFinite(minutes) ? minutes : undefined,
      );
      setGrant(created);
      setName("");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const revoke = async (enrollment: RunnerEnrollment) => {
    if (
      !(await confirmDialog(
        `Withdraw the enrollment token "${enrollment.label || enrollment.id}"? A machine that has not ` +
          "used it yet will be refused at /runner/enroll, and the token cannot be restored.",
        { danger: true },
      ))
    )
      return;
    setBusy(enrollment.id);
    setError("");
    try {
      await getAgentsAdapter().revokeRunnerEnrollment(enrollment.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <section
      className="text-[12px] text-muted"
      data-enroll-panel
      aria-label="Enroll a runner"
      style={{
        display: "block",
        padding: "14px 20px",
        borderBottom: "1px solid var(--line)",
      }}
    >
      {!grant && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            alignItems: "center",
          }}
        >
          <b>Enroll a runner</b>
          <input
            aria-label="Runner name"
            placeholder="Machine name, e.g. mac-mini-01"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void create();
            }}
            style={{
              minWidth: 240,
              padding: "6px 10px",
              borderRadius: 7,
              border: "1px solid var(--line)",
              background: "var(--panel)",
              color: "var(--text)",
            }}
          />
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            Expires in
            <input
              aria-label="Token lifetime in minutes"
              value={ttl}
              inputMode="numeric"
              onChange={(e) => setTtl(e.target.value)}
              style={{
                width: 80,
                padding: "6px 8px",
                borderRadius: 7,
                border: "1px solid var(--line)",
                background: "var(--panel)",
                color: "var(--text)",
              }}
            />
            min
          </label>
          <button
            type="button"
            className="inline-flex items-center rounded-[7px] border border-line bg-accent px-3 py-1.5 text-[13px] text-foreground disabled:opacity-50"
            data-style-hooks="lin-btn primary"
            disabled={!name.trim() || busy === "create"}
            onClick={() => void create()}
          >
            {busy === "create" ? "Minting…" : "Create token"}
          </button>
          <button
            type="button"
            className="inline-flex items-center rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] text-foreground hover:bg-subtle disabled:opacity-50"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      )}

      {grant && (
        <div data-enroll-token style={{ display: "block" }}>
          <p style={{ margin: "0 0 8px" }}>
            <b>Copy this now.</b> Hoja keeps only a hash of the token, so this
            is the one and only time it can be shown — closing this panel
            discards it for good.
          </p>
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
              marginBottom: 8,
            }}
          >
            <code
              data-enroll-secret
              style={{
                fontFamily: "var(--mono, monospace)",
                fontSize: 12,
                padding: "6px 10px",
                borderRadius: 6,
                border: "1px solid var(--line)",
                background: "var(--subtle)",
                wordBreak: "break-all",
                maxWidth: "100%",
              }}
            >
              {grant.token}
            </code>
            <button
              type="button"
              className="inline-flex items-center rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] text-foreground hover:bg-subtle disabled:opacity-50"
              onClick={() => copy("token", grant.token)}
            >
              {copied === "token" ? "Copied" : "Copy token"}
            </button>
          </div>
          {/* Three steps, in the order they are run (HOJA-712). `--enroll` registers and
           * exits, so a screen that showed only that line left the machine idle with a
           * spent token; and it assumed a `hoja-runnerd` that no machine had, because the
           * package is published nowhere. The install step is the deployment's own wheel
           * (`/downloads`), and the start step carries no harness flag: the default is the
           * one the server named at enroll and the daemon reads from the state directory. */}
          {grant.connectCommand && (
            <ol
              data-enroll-connect
              style={{ listStyle: "none", margin: "0 0 12px", padding: 0 }}
            >
              <CommandStep
                step="connect"
                title="Run this one line on the machine"
                command={grant.connectCommand}
                copied={copied === "connect"}
                onCopy={() => copy("connect", grant.connectCommand || "")}
                note="Installs the runner into ~/.hoja/runner, connects it and keeps it running as a service. The machine shows up in this list by itself. Or do it step by step:"
              />
            </ol>
          )}
          <ol
            data-enroll-steps
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "grid",
              gap: 10,
            }}
          >
            <CommandStep
              step="install"
              title="1. Install the daemon"
              command={grant.installCommand}
              copied={copied === "install"}
              onCopy={() => copy("install", grant.installCommand || "")}
              note={
                grant.installCommand
                  ? "Python 3.11 or newer, and pipx on PATH (python3 -m pip install --user pipx)."
                  : "This deployment publishes no daemon wheel. From a checkout of the repository: pip install ./runner"
              }
            />
            <CommandStep
              step="enroll"
              title="2. Enroll this machine"
              command={grant.command}
              copied={copied === "command"}
              onCopy={() => copy("command", grant.command)}
              note="The token works once: this is the only time it is shown, and this step exits when it succeeds."
            />
            <CommandStep
              step="start"
              title="3. Start the daemon"
              command={grant.startCommand}
              copied={copied === "start"}
              onCopy={() => copy("start", grant.startCommand)}
              note={`Keep it running under a supervisor; it heartbeats every ${grant.heartbeatIntervalS} s.`}
            />
          </ol>
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
              marginTop: 10,
            }}
          >
            <span className="grow" />
            <button
              type="button"
              className="inline-flex items-center rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] text-foreground hover:bg-subtle disabled:opacity-50"
              onClick={() => {
                setGrant(null);
                onClose();
              }}
            >
              Done
            </button>
          </div>
        </div>
      )}

      {error && (
        <p
          className="text-danger text-[13px]"
          role="alert"
          style={{ margin: "8px 0 0" }}
        >
          {error}
        </p>
      )}

      {enrollments.length > 0 && (
        <div data-enroll-list style={{ marginTop: 12 }}>
          {enrollments.map((enrollment) => (
            <div
              key={enrollment.id}
              className="flex flex-wrap items-center gap-2 border-b border-line py-2 text-[13px]"
              data-enrollment={enrollment.id}
              data-enrollment-status={enrollment.status}
            >
              <span
                className="inline-flex items-center rounded-[48px] border border-line bg-panel px-2 py-1 text-[12px]"
                title={`status: ${enrollment.status}`}
              >
                {enrollment.status}
              </span>
              <span>
                <b>{enrollment.label || "unnamed"}</b>
                {" · "}
                {enrollmentExpiry(enrollment)}
                {enrollment.usedByRunner
                  ? ` · used by ${enrollment.usedByRunner.slice(0, 8)}`
                  : ""}
              </span>
              <span
                style={{
                  marginLeft: "auto",
                  display: "inline-flex",
                  gap: 8,
                  alignItems: "center",
                }}
              >
                {enrollment.usable ? (
                  <button
                    type="button"
                    className="inline-flex items-center rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] text-foreground hover:bg-subtle disabled:opacity-50"
                    data-style-hooks="lin-btn"
                    disabled={busy === enrollment.id}
                    onClick={() => void revoke(enrollment)}
                  >
                    {busy === enrollment.id ? "Withdrawing…" : "Withdraw"}
                  </button>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* When a token stops working, said in the list's own vocabulary: an age for one that already
 * expired, a deadline for one that still works, and "used" for one that did its job. */
function enrollmentExpiry(enrollment: RunnerEnrollment): string {
  if (enrollment.status === "spent")
    return enrollment.usedAt ? `used ${lastSeen(enrollment.usedAt)}` : "used";
  if (!enrollment.expiresAt) return "never expires";
  if (enrollment.status === "expired")
    return `expired ${lastSeen(enrollment.expiresAt)}`;
  const ms = Date.parse(enrollment.expiresAt) - Date.now();
  if (Number.isNaN(ms)) return enrollment.expiresAt;
  const m = Math.round(ms / 60000);
  if (m <= 0) return "expires now";
  if (m < 60) return `expires in ${m}m`;
  return `expires in ${Math.round(m / 60)}h`;
}

type RunnerGrouping = "status" | "labels" | "none";

export function RunnersPane({
  workspaceName,
  onOpen,
  tab,
  onTab,
}: {
  workspaceName: string;
  onOpen: (runner: Runner) => void;
  tab: string;
  onTab: (id: string) => void;
}) {
  const [runners, setRunners] = useState<Runner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [moreError, setMoreError] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [q, setQ] = useState("");
  const [grouping, setGrouping] = useState<RunnerGrouping>("status");
  const [labels, setLabels] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [connectOn, setConnectOn] = useState(true);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([
      getAgentsAdapter().listRunners(),
      getAgentsAdapter().connectEnabled(),
    ])
      .then(([page, enabled]) => {
        if (!live) return;
        setRunners(page.runners);
        setCursor(page.endCursor);
        setHasMore(page.hasNextPage);
        setConnectOn(enabled);
        setError("");
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

  /* A refused read is the state of the screen, not a red banner above an empty list — the same
   * three-state split AgentsPane and AuditPane use. A member cannot retry a permission refusal into
   * success, so that state names the reason and offers no button; every other failure does offer
   * one, because a dropped request can be repeated. */
  const refused = error !== "";
  const forbidden = error !== "" && /admin|forbidden|permission/i.test(error);

  const loadMore = useCallback(async () => {
    if (!cursor || paging) return;
    setPaging(true);
    setMoreError("");
    try {
      const page = await getAgentsAdapter().listRunners({ after: cursor });
      setRunners((current) => [...current, ...page.runners]);
      setCursor(page.endCursor);
      setHasMore(page.hasNextPage);
      setError("");
    } catch (e) {
      setMoreError((e as Error).message);
    } finally {
      setPaging(false);
    }
  }, [cursor, paging]);

  const allLabels = useMemo(
    () => [...new Set(runners.flatMap((r) => r.labels))].sort(),
    [runners],
  );

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return runners.filter((r) => {
      if (
        needle &&
        !`${r.id} ${r.name} ${r.labels.join(" ")} ${r.harnesses.join(" ")}`
          .toLowerCase()
          .includes(needle)
      )
        return false;
      if (labels.length && !labels.some((l) => r.labels.includes(l)))
        return false;
      return true;
    });
  }, [runners, q, labels]);

  const groups = useMemo(() => {
    if (grouping === "none")
      return [
        {
          key: "all",
          name: "",
          status: undefined as RunnerStatus | undefined,
          items: visible,
        },
      ];
    const map = new Map<
      string,
      { key: string; name: string; status?: RunnerStatus; items: Runner[] }
    >();
    for (const r of visible) {
      const key =
        grouping === "status" ? r.status : r.labels.join(" · ") || "unlabelled";
      if (!map.has(key))
        map.set(key, {
          key,
          name: grouping === "status" ? runnerStatus(r.status).name : key,
          status: grouping === "status" ? r.status : undefined,
          items: [],
        });
      map.get(key)!.items.push(r);
    }
    const out = [...map.values()];
    if (grouping === "status") {
      const order = new Map<string, number>(
        RUNNER_STATUSES.map((s, i) => [s.id as string, i]),
      );
      out.sort((a, b) => (order.get(a.key) ?? 999) - (order.get(b.key) ?? 999));
    } else out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [visible, grouping]);

  const collapsedSet = new Set(collapsed);

  return (
    <AgentsListShell
      ariaLabel="Runners"
      surface="runners-list"
      workspaceName={workspaceName}
      trail="Runners"
      tabs={AGENTS_TABS}
      activeTab={tab}
      onTab={onTab}
      toolbar={
        <>
          <SearchField
            value={q}
            onChange={setQ}
            placeholder="Search runners…"
            label="Search runners"
          />
          <span className="flex-1" />
          {allLabels.map((l) => {
            const on = labels.includes(l);
            return (
              <button
                key={l}
                type="button"
                className="inline-flex items-center border border-line hover:text-foreground hover:border-accent aria-pressed:bg-subtle aria-pressed:text-foreground aria-pressed:font-semibold gap-1.5 pt-1.25 pr-2.75 pb-1.25 pl-2.75 border-solid rounded-[99px] bg-panel text-[12px] text-muted font-medium aria-pressed:border-line"
                aria-pressed={on}
                onClick={() =>
                  setLabels((cur) =>
                    on ? cur.filter((x) => x !== l) : [...cur, l],
                  )
                }
              >
                {l}
              </button>
            );
          })}
          {/* A refused read does not offer a credential-minting action: the button could only
              produce a 403. On a phone the same control is one icon, so the strip still fits the
              shell instead of ending in a cut-off word. */}
          {!forbidden && (
            <button
              type="button"
              className="inline-flex shrink-0 items-center gap-1.5 rounded-[7px] border border-line bg-panel px-3 py-1.5 text-[13px] hover:bg-subtle whitespace-nowrap"
              data-action="enroll-runner"
              aria-label="Enroll runner"
              title="Enroll runner"
              aria-expanded={enrollOpen}
              onClick={() => setEnrollOpen((v) => !v)}
            >
              <Plus size={14} aria-hidden="true" />
              <span className="shell-phone:hidden">Connect a machine</span>
            </button>
          )}
          <span className="relative">
            <IconBtn
              label="Display options"
              title="Display options"
              active={displayOpen}
              onClick={() => setDisplayOpen((v) => !v)}
            >
              <SlidersHorizontal size={15} />
            </IconBtn>
            {displayOpen && (
              <DisplayPanel
                display={{ ...RUNNER_DISPLAY, grouping }}
                setDisplay={(d) => {
                  if (d.grouping) setGrouping(d.grouping as RunnerGrouping);
                }}
                onClose={() => setDisplayOpen(false)}
                groupingOptions={[
                  ["none", "No grouping"],
                  ["status", "Online or offline"],
                  ["labels", "Label"],
                ]}
                groupingLabel="Group runners by"
                entityToggles={false}
                propertyRows={[]}
              />
            )}
          </span>
        </>
      }
      chips={
        labels.length ? (
          <>
            {labels.map((l) => (
              <span
                key={l}
                className="inline-flex items-center border border-line gap-1.5 text-[12px] border-solid bg-subtle rounded-[99px] pt-0.75 pr-1.5 pb-0.75 pl-2.5 text-foreground"
              >
                {l}
                <button
                  className="border-0 hover:text-foreground [background:none] text-muted text-[13px] leading-none pt-0.5 pr-1 pb-0.5 pl-1 rounded-sm"
                  type="button"
                  aria-label={`Remove filter ${l}`}
                  onClick={() => setLabels((cur) => cur.filter((x) => x !== l))}
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="inline-flex items-center border-0 hover:bg-subtle hover:text-foreground gap-2 [background:none] text-muted text-[13px] pt-1.25 pr-1.5 pb-1.25 pl-1.5 rounded-md"
              onClick={() => setLabels([])}
            >
              Clear all
            </button>
          </>
        ) : null
      }
    >
      {enrollOpen && <EnrollPanel onClose={() => setEnrollOpen(false)} />}
      {loading && (
        <p
          className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
          role="status"
        >
          Loading runners…
        </p>
      )}
      {!loading && refused && (
        <div
          className="text-center pt-15 pr-5 pb-15 pl-5 text-muted"
          data-state="error"
          data-refusal={forbidden ? "forbidden" : "failed"}
          data-style-hooks="lin-error"
          role="alert"
        >
          <h2 className="text-foreground text-base mt-3 mr-0 mb-1.5 ml-0">
            {forbidden
              ? "Only workspace admins can see this workspace’s runners"
              : "Runners could not be loaded"}
          </h2>
          <p>{error}</p>
          {!forbidden && (
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-[7px] border border-line bg-panel px-3.5 py-2 text-[13px] font-[550] text-foreground border-solid hover:bg-subtle shell-phone:min-h-11 pointer-coarse:min-h-11"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              Retry
            </button>
          )}
        </div>
      )}
      {!loading && !error && (
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
                    {g.status && (
                      <GraphStatusIcon
                        state={{
                          name: runnerStatus(g.status).name,
                          type: runnerStatus(g.status).type,
                          color: runnerStatus(g.status).color,
                        }}
                        size={13}
                      />
                    )}
                    <b className="font-semibold">{g.name}</b>
                    <span className="text-muted font-medium text-[12px]">
                      {g.items.length}
                    </span>
                    <span className="flex-1" />
                  </header>
                )}
                {!isCollapsed &&
                  g.items.map((r) => (
                    <IssueRow
                      key={r.id}
                      issue={runnerRow(r)}
                      display={RUNNER_DISPLAY}
                      selected={false}
                      showCheckbox={false}
                      onSelect={() => undefined}
                      onOpen={() => onOpen(r)}
                      childCount={0}
                      leading={credentialGlyph(r)}
                      trailing={leaseChip(r)}
                    />
                  ))}
              </section>
            );
          })}
          {!visible.length && (
            <div className="text-center pt-15 pr-5 pb-15 pl-5 text-muted">
              <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
                {runners.length
                  ? "No runner matches these filters"
                  : "No machine is enrolled"}
              </h2>
              <p>
                {runners.length
                  ? "The search and label chips narrow the runners loaded so far; clear them to see them all."
                  : "A runner appears here once hoja-runnerd has presented its one-time lease token."}
              </p>
            </div>
          )}
          {(hasMore || runners.length > 0) && (
            <div className="flex items-center gap-2 py-2 text-[12px] text-muted">
              <span>
                {runners.length} machine{runners.length === 1 ? "" : "s"} loaded
              </span>
              {hasMore && (
                <button
                  type="button"
                  className="rounded-[7px] border border-line px-3 py-1.5 hover:bg-subtle disabled:opacity-50"
                  data-action="load-more"
                  disabled={paging}
                  onClick={() => void loadMore()}
                >
                  {paging ? "Loading…" : "Load more runners"}
                </button>
              )}
              {moreError && (
                <span className="text-danger" role="alert">
                  {moreError}
                </span>
              )}
            </div>
          )}
        </div>
      )}
      {!connectOn && (
        <p className="text-[12px] text-muted pt-0 pr-5 pb-6 pl-5" role="status">
          Connect is turned off for this workspace. Runners still lease work; no
          tunnel can be opened until an admin turns it back on.
        </p>
      )}
    </AgentsListShell>
  );
}

/* Last seen, in the shape the row's age glyph uses. Kept local so the detail page does not import
 * from AgentDetail (which owns a different vocabulary). */
function lastSeen(iso: string | null): string {
  if (!iso) return "no heartbeat yet";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function leaseLine(lease: RunnerLease): string {
  const task = lease.taskIdentifier || lease.taskId.slice(0, 8);
  return `${task} · ${lease.harness} · fence ${lease.fence} · ${lease.state}`;
}

function ExposureRow({
  exposure,
  connectOn,
  busy,
  onRevoke,
  now,
}: {
  exposure: ConnectExposure;
  connectOn: boolean;
  busy: boolean;
  onRevoke: (e: ConnectExposure) => void;
  now: number;
}) {
  const left = ttlSecondsLeft(exposure.expiresAt, now);
  const tone =
    left > 300
      ? { name: "Live", type: "started", color: "#2f9e6f" }
      : left > 0
        ? { name: "Expiring", type: "unstarted", color: "#c98a1b" }
        : { name: "Expired", type: "completed", color: "#95a2b3" };
  return (
    <div
      className="group relative flex items-center border-b border-line hover:bg-subtle gap-2.25 min-h-10.5 pt-1.25 pr-2.5 pb-1.25 pl-2.5 border-b-line text-[13px]"
      data-exposure={exposure.id}
    >
      <GraphStatusIcon state={tone} size={14} />
      {/* The second 14 px slot: an exposure has no priority, and the TTL column below carries the
          number that matters, so the slot stays a fixed spacer rather than a blank glyph. */}
      <span aria-hidden="true" className="inline-block w-3.5 h-3.5" />
      <span className="whitespace-nowrap font-semibold text-foreground">
        {exposure.id}
      </span>
      <span
        className="flex-1 cursor-pointer truncate border-0 text-left min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-foreground [background:none] [font:inherit] pt-0 pr-0 pb-0 pl-0"
        title={`Ports ${exposure.ports.join(", ")}`}
      >
        {exposure.ports.length === 1
          ? `port ${exposure.ports[0]}`
          : `ports ${exposure.ports.join(", ")}`}
      </span>
      <span className="flex flex-none items-center gap-2 ml-auto">
        {/* The pinned peer is the whole ACL (`--allow=nodekey:…`), so it is shown shortened and
            in full on hover: it is an identifier, not a secret. */}
        <span className="hidden md:inline-flex">
          <span
            className="inline-flex items-center whitespace-nowrap border border-line gap-1 h-6 text-[12px] font-[450] border-solid border-(--card-line) rounded-[48px] pt-0 pr-2 pb-0 pl-2 text-(--card-meta) bg-panel"
            title={`Pinned peer ${exposure.pinnedPeer}`}
          >
            {exposure.pinnedPeer.slice(0, 15)}…
          </span>
        </span>
        <span
          className="whitespace-nowrap text-muted text-[12px]"
          title={`Expires in ${left}s`}
          data-ttl={left}
        >
          {ttlLabel(exposure.expiresAt, now)}
        </span>
        <button
          type="button"
          className="inline-flex items-center border border-line hover:bg-subtle disabled:cursor-default disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-2 border-solid bg-panel text-foreground rounded-[7px] pt-2 pr-3.5 pb-2 pl-3.5 text-[13px] font-[550] shell-phone:min-w-11! shell-phone:min-h-11!"
          data-style-hooks="lin-btn"
          disabled={!connectOn || busy}
          title={
            connectOn
              ? "Revoke this exposure"
              : "Connect is disabled for this workspace"
          }
          onClick={() => onRevoke(exposure)}
        >
          {busy ? "Revoking…" : "Revoke"}
        </button>
      </span>
    </div>
  );
}

export function RunnerDetailPage({
  runnerId,
  workspaceName,
  viewerName,
  onBack,
}: {
  runnerId: string;
  workspaceName: string;
  viewerName: string;
  onBack: () => void;
}) {
  const [runner, setRunner] = useState<Runner | null>(null);
  const [leases, setLeases] = useState<RunnerLease[]>([]);
  const [exposures, setExposures] = useState<ConnectExposure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState(false);
  const [connectOn, setConnectOn] = useState(true);
  const [revoking, setRevoking] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    getAgentsAdapter()
      .getRunner(runnerId)
      .then(async (d) => {
        const enabled = await getAgentsAdapter().connectEnabled();
        setConnectOn(enabled);
        setRunner(d ? d.runner : null);
        setExposures(d ? d.exposures : []);
        setError(d ? "" : `Runner ${runnerId} was not found.`);
        setLeases(d ? await getAgentsAdapter().listLeases(runnerId) : []);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runnerId]);

  useEffect(() => {
    load();
  }, [load]);

  /* Revoke the machine itself, which is a different act from revoking one of its tunnels: this
   * one turns the runner's bearer token off, so /runner/claim refuses it on its next poll. */
  /* Takes the row as an argument rather than closing over `runner`: this hook list runs before the
   * "not found" early return, so the component's `runner` is still nullable here, and the narrowed
   * value at the button is the one that exists. */
  const revokeRunner = async (target: Runner) => {
    if (
      !(await confirmDialog(
        `Revoke ${target.name}? Its lease token stops authenticating immediately, so it is refused at ` +
          "/runner/claim and any lease it holds is lost on the next heartbeat. Work is re-queued; the " +
          "machine is not deleted.",
        { danger: true },
      ))
    )
      return;
    setRevoking(true);
    setNotice("");
    setError("");
    try {
      await getAgentsAdapter().revokeRunner(target.id);
      load();
      setNotice(
        `${target.name} revoked. Its token no longer authenticates at /runner/claim.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRevoking(false);
    }
  };

  /* A TTL that does not count down is a lie about a tunnel that will die on its own. */
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  /* Two steps: the shared `confirmDialog` (never `window.confirm`), then a state transition that
   * also writes the audit row. The row only disappears when the adapter says it is gone. */
  const revoke = async (exposure: ConnectExposure) => {
    if (
      !(await confirmDialog(
        `Revoke the Connect exposure ${exposure.id} on ${exposure.ports.join(", ")}? The tunnel is killed, not hidden.`,
        { danger: true },
      ))
    )
      return;
    setBusy(exposure.id);
    setNotice("");
    try {
      await getAgentsAdapter().revokeExposure(exposure.id, viewerName);
      setExposures((cur) => cur.filter((e) => e.id !== exposure.id));
      setNotice(
        `Revoked and audited: connect.exposure.close on ${exposure.id}.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  if (loading) {
    return (
      <section
        className="flex flex-col min-w-0 h-full bg-panel"
        data-surface="runner-detail"
        aria-label={`${runnerId}: runner`}
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
              ← Runners
            </button>
            <span className="text-muted opacity-70">›</span>
            <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
              {runnerId}
            </span>
          </nav>
        </header>
        <p
          className="text-center pt-10 pr-10 pb-10 pl-10 text-muted"
          role="status"
        >
          Loading runner…
        </p>
      </section>
    );
  }

  if (!runner) {
    return (
      <section
        className="flex flex-col min-w-0 h-full bg-panel"
        data-surface="runner-detail"
        aria-label={`${runnerId}: runner`}
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
              ← Runners
            </button>
            <span className="text-muted opacity-70">›</span>
            <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
              {runnerId}
            </span>
          </nav>
        </header>
        <p
          className="border border-danger/40 mt-4 mr-6 mb-4 ml-6 pt-3 pr-4 pb-3 pl-4 border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
          role="alert"
        >
          {error || `Runner ${runnerId} was not found.`}{" "}
          <button
            type="button"
            onClick={() => {
              setError("");
              load();
            }}
            className="shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          >
            Retry
          </button>
        </p>
      </section>
    );
  }

  const tone = runnerStatus(runner.status);

  return (
    <section
      className="flex flex-col min-w-0 h-full bg-panel"
      data-surface="runner-detail"
      data-style-hooks="lin-page"
      data-connect={connectOn ? "on" : "off"}
      data-runner-status={runner.status}
      aria-label={`${runner.id}: ${runner.name}`}
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
            data-style-hooks="crumb-link"
            onClick={onBack}
          >
            ← Runners
          </button>
          <span className="text-muted opacity-70">›</span>
          <span>{workspaceName}</span>
          <span className="text-muted opacity-70">›</span>
          <span className="font-semibold text-muted">{runner.id}</span>
          <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
            {runner.name}
          </span>
        </nav>
      </header>
      <div
        className="flex flex-none items-center border-b border-line justify-end gap-1.5 pt-2 pr-5 pb-2 pl-5 border-b-line"
        data-style-hooks="lin-toolbar"
      >
        {!runner.revoked && runner.enabled && (
          <button
            type="button"
            className="rounded-[7px] border border-line px-3 py-1.5 text-[13px] hover:bg-subtle disabled:opacity-50"
            data-style-hooks="lin-btn"
            data-action="revoke-runner"
            disabled={revoking}
            onClick={() => void revokeRunner(runner)}
          >
            {revoking ? "Revoking…" : "Revoke runner"}
          </button>
        )}
        <Tip label={copied ? "Copied" : `Copy ${runner.id}`}>
          <span>
            <IconBtn
              label="Copy runner id"
              title="Copy runner id"
              onClick={() => {
                void navigator.clipboard
                  .writeText(runner.id)
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
            <GraphStatusIcon
              state={{ name: tone.name, type: tone.type, color: tone.color }}
              size={16}
            />{" "}
            {runner.name}
          </h1>
          <p className="relative wrap-break-word text-[14px] leading-[1.6] text-foreground [word-break:break-word] mt-0 mr-0 mb-2 ml-0 min-h-6">
            {tone.name} · last seen {lastSeen(runner.lastSeenAt)} ·{" "}
            {runner.revoked
              ? "revoked"
              : runner.enabled
                ? "enrolled"
                : "disabled"}
            {runner.harnesses.length ? ` · ${runner.harnesses.join(", ")}` : ""}
            {runner.labels.length ? ` · ${runner.labels.join(", ")}` : ""}
          </p>

          <section
            className="border-t border-line pt-4 mt-1.5"
            aria-label="Leases"
          >
            <header className="flex items-center gap-2 mb-3">
              <b className="text-[14px]">Leases</b>
              <span className="flex-1" />
              <span className="text-[12px] text-muted">
                {runner.lease
                  ? `Holding ${leaseLine(runner.lease)}`
                  : "Holding no lease"}
              </span>
            </header>
            <div data-leases>
              {leases.map((lease) => (
                <div
                  key={lease.id}
                  className="flex flex-wrap items-center gap-2 border-b border-line py-2 text-[13px]"
                  data-lease-row={lease.id}
                  data-lease-state={lease.state}
                >
                  <span
                    className="rounded-[48px] border border-line px-2 py-1 text-[12px]"
                    title={`state: ${lease.state}`}
                  >
                    {lease.state}
                  </span>
                  <span>
                    <b>{lease.taskIdentifier || lease.taskId.slice(0, 8)}</b> ·{" "}
                    {lease.harness} · fence {lease.fence}
                  </span>
                  <time className="ml-auto text-muted">
                    {lastSeen(lease.grantedAt || lease.finishedAt)}
                  </time>
                </div>
              ))}
            </div>
            {!leases.length && (
              <p className="text-[12px] text-muted">
                This machine has never been granted work.
              </p>
            )}
          </section>
          <section
            className="border-t border-line border-t-line pt-4 mt-1.5"
            aria-label="Connect exposures"
          >
            <header className="flex items-center mb-3">
              <b className="text-[14px]">Connect exposures</b>
              <span className="flex-1" />
              <span className="text-[12px] text-muted">
                {connectOn
                  ? "Each tunnel is one process, one ephemeral key."
                  : "Connect is disabled for this workspace."}
              </span>
            </header>
            {!connectOn && (
              <p className="text-[12px] text-muted" data-connect-notice="off">
                An admin turned Connect off for this workspace, so no exposure
                can be opened and none can be revoked. This is a switch, not an
                empty list.
              </p>
            )}
            {/* `data-exposures` identifies this list for geometry and behavior probes. */}
            <div data-exposures>
              {exposures.map((e) => (
                <ExposureRow
                  key={e.id}
                  exposure={e}
                  connectOn={connectOn}
                  busy={busy === e.id}
                  onRevoke={revoke}
                  now={now}
                />
              ))}
            </div>
            {!exposures.length && connectOn && (
              <p className="text-[12px] text-muted">
                This runner holds no live exposure. One opens per leased job
                that asks for a port.
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
          aria-label="Runner details"
        >
          <h3 className="text-[12px] font-semibold text-muted mt-0 mr-0 mb-2.5 ml-0">
            Runner
          </h3>
          <PropDropdown
            label="Credential"
            icon={<Lock size={13} aria-hidden="true" />}
            value={
              runner.revoked
                ? "Revoked"
                : runner.enabled
                  ? "Enrolled"
                  : "Disabled"
            }
            disabled
            onPick={() => undefined}
            options={[
              {
                id: "enrolled",
                selected: !runner.revoked && runner.enabled,
                label: "Enrolled",
              },
              { id: "revoked", selected: runner.revoked, label: "Revoked" },
              {
                id: "disabled",
                selected: !runner.revoked && !runner.enabled,
                label: "Disabled",
              },
            ]}
          />
          <div className="relative flex items-center gap-2.5 min-h-8.5 text-[13px]">
            <span className="flex flex-none items-center gap-2 text-muted w-24">
              ◷ Last seen
            </span>
            <span className="flex flex-1 items-center min-w-0 gap-1.75">
              {lastSeen(runner.lastSeenAt)}
            </span>
          </div>
          <div className="relative flex items-center gap-2.5 min-h-8.5 text-[13px]">
            <span className="flex flex-none items-center gap-2 text-muted w-24">
              ▮ Current lease
            </span>
            <span
              className="flex flex-1 items-center min-w-0 gap-1.75"
              data-current-lease={runner.lease?.id || ""}
            >
              {runner.lease ? leaseLine(runner.lease) : "None"}
            </span>
          </div>
          <div className="mt-4.5 mr-0 mb-1 ml-0 text-[12px] font-semibold text-muted">
            Harnesses
          </div>
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            {runner.harnesses.map((name) => (
              <LabelChip key={name} label={{ id: name, name }} />
            ))}
            {!runner.harnesses.length && (
              <span className="text-[12px] text-muted">None declared</span>
            )}
          </div>
          <div className="mt-4.5 mr-0 mb-1 ml-0 text-[12px] font-semibold text-muted">
            Labels
          </div>
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            {runner.labels.map((name) => (
              <LabelChip key={name} label={{ id: name, name }} />
            ))}
            {!runner.labels.length && (
              <span className="text-[12px] text-muted">None</span>
            )}
          </div>
          <div className="mt-4.5 mr-0 mb-1 ml-0 text-[12px] font-semibold text-muted">
            Connect
          </div>
          <div className="relative flex items-center gap-2.5 min-h-8.5 text-[13px]">
            <span className="flex flex-none items-center gap-2 text-muted w-24">
              ▮ Workspace
            </span>
            <span
              className="flex flex-1 items-center min-w-0 gap-1.75"
              data-connect-state={connectOn ? "on" : "off"}
            >
              {connectOn ? "Enabled" : "Disabled by an admin"}
            </span>
          </div>
          <p className="text-[12px] text-muted mt-4">
            A runner leases work outbound-only. Revoking an exposure kills the
            tunnel process, so its key and its address die with it; the revoke
            is written to the audit log with your name.
          </p>
        </aside>
      </div>
    </section>
  );
}
