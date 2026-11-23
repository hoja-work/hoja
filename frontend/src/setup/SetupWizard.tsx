// The shared setup experience.
//
// One shell (step navigation, focused form, live preview) with edition-specific
// server calls behind `SetupAdapter`. The component owns no product rules that
// belong to the server: it validates only what the API refuses, and every state
// it shows as "created" comes from a response, not from a click.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import "./setup.css";
import {
  buildSteps,
  canSkip,
  canVisit,
  nextStep,
  previousStep,
  progress,
  STEP_TITLES,
} from "./flow";
import type { FlowMode, StepId } from "./flow";
import { buildPreview } from "./preview";
import { clearDraft, freshDraft, loadDraft, saveDraft } from "./draft";
import type { SetupDraft } from "./draft";
import {
  checkAddress,
  checkEmail,
  checkInvitee,
  checkName,
  checkPassword,
  checkProjectName,
  checkTeamKey,
  checkTeamName,
  checkWorkspaceName,
  addressOnNameChange,
  deriveAddress,
  normaliseAddress,
  normaliseTeamKey,
} from "./validation";
import { errorMessage } from "./api";
import type { InviteResult, SetupAdapter } from "./types";

type Props = {
  adapter: SetupAdapter;
  mode: FlowMode;
  initial?: { workspaceId?: string; workspaceUrlKey?: string; teamId?: string };
  onFinish?: () => void;
};

type Boot = {
  workspaceId?: string;
  workspaceUrlKey?: string;
  teamId?: string;
  projectId?: string;
};

// Fields of the account step whose message renders under the field itself.
const ACCOUNT_FIELDS = ["name", "email", "password"];
const memory = new Map<string, string>();
const fallbackStorage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => {
    memory.set(key, value);
  },
  removeItem: (key: string) => {
    memory.delete(key);
  },
};

function storage(): Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  try {
    return window.localStorage;
  } catch {
    return fallbackStorage;
  }
}

export function SetupWizard({ adapter, mode, initial, onFinish }: Props) {
  const store = storage();
  const scope = useMemo(
    () => ({ edition: adapter.edition }) as const,
    [adapter.edition],
  );
  // A saved draft wins over the caller's starting mode: a first run that has
  // already created its workspace is resumed on the same path, not restarted as
  // an "existing workspace" flow with an empty form.
  const [draft, setDraft] = useState<SetupDraft>(() => {
    const saved = loadDraft(store, {
      ...scope,
      workspaceId: initial?.workspaceId,
    });
    if (!saved) return freshDraft(mode);
    // A draft for a different workspace must not leak into this one; a pre-create
    // draft only belongs here when a Google sign-in was started from it.
    if (initial?.workspaceId && !saved.workspaceId && !saved.googleStarted)
      return freshDraft(mode);
    return saved;
  });
  const [step, setStep] = useState<StepId>(() => draft.step);
  const [direction, setDirection] = useState<"forward" | "back">("forward");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteResults, setInviteResults] = useState<InviteResult[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const [status, setStatus] = useState<{
    initialized: boolean;
    ownerBound: boolean;
    googleSignIn: string | null;
  } | null>(null);
  const [connectors, setConnectors] = useState<{ googleWorkspace: boolean }>({
    googleWorkspace: false,
  });
  const [connected, setConnected] = useState<string[]>([]);
  const [configReady, setConfigReady] = useState(false);
  const [boot, setBoot] = useState<Boot>({
    workspaceId: initial?.workspaceId ?? draft.workspaceId,
    workspaceUrlKey: initial?.workspaceUrlKey ?? draft.workspaceUrlKey,
    teamId: initial?.teamId ?? draft.teamId,
    projectId: draft.projectId,
  });
  const [pendingCount, setPendingCount] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const stepListRef = useRef<HTMLOListElement | null>(null);
  const busyRef = useRef(false);
  const addressTouched = useRef(
    Boolean(
      draft.workspaceAddress &&
      draft.workspaceName &&
      draft.workspaceAddress !== deriveAddress(draft.workspaceName),
    ),
  );

  const flowMode = draft.mode;
  const steps = useMemo(
    () =>
      buildSteps({
        mode: flowMode,
        googleSignIn: Boolean(status?.googleSignIn),
        ownerBound: Boolean(status?.ownerBound),
        workspaceCreated: Boolean(boot.workspaceId),
        teamExists: Boolean(boot.teamId),
        googleWorkspaceAvailable: connectors.googleWorkspace,
      }),
    [
      flowMode,
      status?.googleSignIn,
      status?.ownerBound,
      boot.workspaceId,
      boot.teamId,
      connectors.googleWorkspace,
    ],
  );

  const persist = useCallback(
    (patch: Partial<SetupDraft>) => {
      setDraft((previous) => {
        const next = { ...previous, ...patch };
        saveDraft(store, { ...scope, workspaceId: next.workspaceId }, next);
        return next;
      });
    },
    [scope, store],
  );

  // Public, read-only facts: is this instance already set up, can it sign in with
  // Google, and can it connect anything? A failure here never blocks a password
  // setup, it only hides the optional move.
  useEffect(() => {
    let live = true;
    Promise.allSettled([
      adapter
        .status()
        .then((value) => {
          if (live) setStatus(value);
        })
        .catch(() => {
          if (live)
            setStatus({
              initialized: false,
              ownerBound: false,
              googleSignIn: null,
            });
        }),
      adapter
        .connectorConfig()
        .then((value) => {
          if (live) setConnectors(value);
        })
        .catch(() => {
          if (live) setConnectors({ googleWorkspace: false });
        }),
    ]).then(() => {
      if (live) setConfigReady(true);
    });
    return () => {
      live = false;
    };
  }, [adapter]);

  // Connected accounts are tenant data, so they are only asked for once this
  // browser has a workspace session — never as an anonymous probe.
  useEffect(() => {
    if (!boot.workspaceId) return;
    let live = true;
    adapter
      .connectorAccounts()
      .then((providers) => {
        if (live) setConnected(providers);
      })
      .catch(() => {
        if (live) setConnected([]);
      });
    return () => {
      live = false;
    };
  }, [adapter, boot.workspaceId]);

  // A returning person with a draft resumes where they stopped; a step that is no
  // longer part of the flow (team removed after creation) resolves to the first
  // unfinished step instead of a blank screen.
  useEffect(() => {
    if (!configReady || steps.includes(step)) return;
    const next =
      steps.find(
        (candidate) =>
          !draft.completed.includes(candidate) &&
          !draft.skipped.includes(candidate),
      ) ?? "ready";
    setStep(next);
    persist({ step: next });
  }, [configReady, steps, step, draft.completed, draft.skipped, persist]);

  useEffect(() => {
    if (!initial?.workspaceId) return;
    setBoot((previous) =>
      previous.workspaceId
        ? previous
        : {
            workspaceId: initial.workspaceId,
            workspaceUrlKey: initial.workspaceUrlKey,
            teamId: initial.teamId,
          },
    );
  }, [initial?.workspaceId, initial?.workspaceUrlKey, initial?.teamId]);

  useEffect(() => {
    headingRef.current?.focus();
    // On phones the steps are a sideways strip: keep the current one visible.
    const list = stepListRef.current;
    const current = list?.querySelector<HTMLElement>('[aria-current="step"]');
    if (list && current && list.scrollWidth > list.clientWidth)
      list.scrollLeft = current.offsetLeft - list.offsetLeft - 8;
  }, [step]);

  useEffect(() => {
    if (!draft.invites.length) return;
    let live = true;
    adapter
      .pendingInvites()
      .then((rows) => {
        if (live) setPendingCount(rows.length);
      })
      .catch(() => {
        if (live) setPendingCount(0);
      });
    return () => {
      live = false;
    };
  }, [adapter, draft.invites.length]);

  const preview = buildPreview({
    draft,
    addressDomain: adapter.addressDomain,
    googleWorkspaceAvailable: connectors.googleWorkspace,
  });
  const position = progress(steps, step);
  const liveSummary = [
    draft.workspaceName.trim()
      ? `Workspace ${draft.workspaceName.trim()}`
      : "Workspace not named",
    draft.workspaceAddress
      ? `address ${normaliseAddress(draft.workspaceAddress)}`
      : "no address yet",
    draft.teamName.trim()
      ? `team ${draft.teamName.trim()} (${normaliseTeamKey(draft.teamKey) || "—"})`
      : "no team yet",
  ].join(", ");

  const focusFirstInvalid = () => {
    window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>(
        '[aria-invalid="true"]',
      );
      (target ?? headingRef.current)?.focus();
    }, 0);
  };

  const go = (target: StepId | null, movement: "forward" | "back") => {
    if (!target) return;
    setDirection(movement);
    setError("");
    setFieldError(null);
    setStep(target);
    persist({ step: target });
  };

  const advance = () => go(nextStep(steps, step), "forward");
  const back = () => go(previousStep(steps, step), "back");

  const markDone = (id: StepId) => {
    const completed = draft.completed.includes(id)
      ? draft.completed
      : [...draft.completed, id];
    persist({ completed });
  };

  const skip = () => {
    if (!canSkip(step)) return;
    const skipped = draft.skipped.includes(step)
      ? draft.skipped
      : [...draft.skipped, step];
    persist({ skipped });
    go(nextStep(steps, step), "forward");
  };

  async function run<T>(work: () => Promise<T>): Promise<T | null> {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      return await work();
    } catch (failure) {
      setError(errorMessage(failure));
      return null;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const submitAccount = (event: FormEvent) => {
    event.preventDefault();
    const checks: { field: string; message: string }[] = [
      { field: "name", message: checkName(name) },
      { field: "email", message: checkEmail(email) },
      { field: "password", message: checkPassword(password) },
    ];
    const first = checks.find((entry) => entry.message);
    if (first) {
      setFieldError(first);
      focusFirstInvalid();
      return;
    }
    markDone("account");
    go(nextStep(steps, step), "forward");
  };

  const submitWorkspace = (event: FormEvent) => {
    event.preventDefault();
    const nameIssue = checkWorkspaceName(draft.workspaceName);
    const addressIssue = checkAddress(draft.workspaceAddress);
    if (nameIssue || addressIssue) {
      setFieldError({
        field: nameIssue ? "workspaceName" : "workspaceAddress",
        message: nameIssue || addressIssue,
      });
      focusFirstInvalid();
      return;
    }
    markDone("workspace");
    go(nextStep(steps, step), "forward");
  };

  const submitTeam = async (event: FormEvent) => {
    event.preventDefault();
    const teamIssue =
      checkTeamName(draft.teamName) || checkTeamKey(draft.teamKey);
    const workspaceIssue = boot.workspaceId
      ? ""
      : checkWorkspaceName(draft.workspaceName) ||
        checkAddress(draft.workspaceAddress);
    const accountIssue =
      flowMode === "first-run" && !boot.workspaceId
        ? checkName(name) || checkEmail(email) || checkPassword(password)
        : "";
    if (teamIssue || workspaceIssue || accountIssue) {
      const pair = teamIssue
        ? {
            field: checkTeamName(draft.teamName) ? "teamName" : "teamKey",
            message: teamIssue,
          }
        : workspaceIssue
          ? { field: "workspaceAddress", message: workspaceIssue }
          : {
              field: checkName(name)
                ? "name"
                : checkEmail(email)
                  ? "email"
                  : "password",
              message: accountIssue,
            };
      setFieldError(pair);
      focusFirstInvalid();
      return;
    }

    if (flowMode === "first-run" && !boot.workspaceId) {
      const result = await run(() =>
        adapter.bootstrap({
          name,
          email,
          password,
          workspaceName: draft.workspaceName,
          workspaceAddress: normaliseAddress(draft.workspaceAddress),
          teamName: draft.teamName,
          teamKey: normaliseTeamKey(draft.teamKey),
        }),
      );
      if (!result) {
        // A lost response can mean the server did commit. Ask before suggesting a retry.
        const settled = await adapter.status().catch(() => null);
        if (settled?.initialized) {
          setError(
            "This instance is already set up, so Hoja will not create it again. Reload to continue with the account that was created.",
          );
        }
        return;
      }
      setApiKey(result.apiKey ?? "");
      setPassword("");
      const nextBoot: Boot = {
        workspaceId: result.workspaceId,
        workspaceUrlKey: result.workspaceUrlKey,
        teamId: result.teamId,
      };
      setBoot(nextBoot);
      persist({
        workspaceId: result.workspaceId,
        workspaceUrlKey: result.workspaceUrlKey,
        teamId: result.teamId,
        completed: [...draft.completed, "workspace", "team"],
      });
      go("project", "forward");
      return;
    }

    if (!boot.teamId) {
      const created = await run(() =>
        adapter.createTeam({
          name: draft.teamName,
          key: normaliseTeamKey(draft.teamKey),
        }),
      );
      if (!created) return;
      setBoot((previous) => ({ ...previous, teamId: created.id }));
      persist({ teamId: created.id, completed: [...draft.completed, "team"] });
      go("project", "forward");
      return;
    }
    go("project", "forward");
  };

  const submitProject = async (event: FormEvent) => {
    event.preventDefault();
    const issue = checkProjectName(draft.projectName);
    if (!draft.projectName.trim()) {
      skip();
      return;
    }
    if (issue) {
      setFieldError({ field: "projectName", message: issue });
      focusFirstInvalid();
      return;
    }
    if (!boot.teamId) {
      setError("A project needs a team. Go back and create the team first.");
      return;
    }
    if (draft.projectId) {
      advance();
      return;
    }
    const created = await run(async () => {
      // Never blind-retry a create whose response may have been lost: look first.
      const existing = await adapter.findProject(draft.projectName);
      if (existing) return existing;
      return adapter.createProject({
        name: draft.projectName,
        teamId: boot.teamId as string,
      });
    });
    if (!created) return;
    setBoot((previous) => ({ ...previous, projectId: created.id }));
    persist({
      projectId: created.id,
      completed: [...draft.completed, "project"],
    });
    go(nextStep(steps, step), "forward");
  };

  const submitInvite = async (event: FormEvent) => {
    event.preventDefault();
    const issue = checkInvitee(inviteName, inviteEmail);
    if (issue) {
      setFieldError({ field: "inviteEmail", message: issue });
      focusFirstInvalid();
      return;
    }
    if (!boot.teamId) {
      setError("Add a team before inviting people to it.");
      return;
    }
    const result = await run(() =>
      adapter.invite({
        name: inviteName,
        email: inviteEmail,
        teamId: boot.teamId as string,
      }),
    );
    if (!result) return;
    setInviteResults((previous) => [...previous, result]);
    const invites = [
      ...draft.invites,
      { name: inviteName.trim(), email: inviteEmail.trim().toLowerCase() },
    ];
    persist({ invites });
    setInviteName("");
    setInviteEmail("");
    setPendingCount(invites.length);
  };

  const copy = async (value: string) => {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(value);
      setCopied(value);
    } catch {
      setError(
        "Copying is blocked in this browser. Select the value and copy it manually.",
      );
    }
  };

  const recover = () => {
    window.location.reload();
  };

  const finish = () => {
    clearDraft(store, { ...scope, workspaceId: boot.workspaceId });
    if (onFinish) onFinish();
    else adapter.openWorkspace();
  };

  // The account step shows each message under the field it is about, so on a phone the
  // message sits next to the focused input instead of two fields away (under the keyboard).
  const accountFieldError = (field: string) =>
    fieldError?.field === field ? (
      <p
        className="mt-2 mx-0 mb-0 text-danger text-[12px] shell-phone:text-[13px] pointer-coarse:text-[13px]"
        data-fidelity-state="setup-field-error"
        id={`setup-${field}-error`}
        role="alert"
      >
        {fieldError.message}
      </p>
    ) : null;

  const fieldProps = (field: string) => {
    const invalid = fieldError?.field === field;
    return {
      "aria-invalid": invalid ? true : undefined,
      "aria-describedby": invalid ? `setup-${field}-error` : undefined,
    };
  };

  if (status?.initialized && flowMode === "first-run" && !boot.workspaceId) {
    return (
      <main
        data-setup-page=""
        className="min-h-dvh p-[clamp(20px,4vw,48px)_clamp(16px,3vw,40px)] bg-bg text-foreground [@media(max-height:_700px)]:pt-4 [@media(max-height:_700px)]:pb-4 [@media(max-width:_760px)]:pt-4.5 [@media(max-width:_760px)]:px-3.5 [@media(max-width:_760px)]:pb-8"
        data-fidelity-state="setup-page"
      >
        <section
          className="max-w-110 mt-[10vh] mx-auto mb-0 p-8 border border-solid border-line bg-panel"
          data-fidelity-state="setup-card setup-finished"
        >
          <h1 className="mt-0 mx-0 mb-2 text-[22px] tracking-[-0.03em]">
            This workspace is already set up
          </h1>
          <p className="mt-0 mx-0 mb-5.5 text-muted text-[13px]">
            Setup was completed in another tab or by another person. Sign in to
            continue.
          </p>
          <a
            className="inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-[0_1px_2px_#0002] [&:hover]:filter-[brightness(1.08)] w-full min-h-11"
            data-control-primary=""
            href="/login"
          >
            Go to sign in
          </a>
        </section>
      </main>
    );
  }

  return (
    <main
      data-setup-page=""
      className="min-h-dvh p-[clamp(20px,4vw,48px)_clamp(16px,3vw,40px)] bg-bg text-foreground [@media(max-height:_700px)]:pt-4 [@media(max-height:_700px)]:pb-4 [@media(max-width:_760px)]:pt-4.5 [@media(max-width:_760px)]:px-3.5 [@media(max-width:_760px)]:pb-8"
      data-fidelity-state="setup-page"
    >
      <div
        data-setup-shell=""
        className="grid grid-cols-[210px_minmax(0,1fr)_300px] gap-[clamp(20px,3vw,40px)] [align-items:start] w-[min(1180px,100%)] my-0 mx-auto [@media(max-width:_1040px)]:grid-cols-[190px_minmax(0,1fr)] [@media(max-width:_760px)]:grid-cols-[minmax(0,1fr)] [@media(max-width:_760px)]:gap-4"
        data-fidelity-state="setup-shell"
      >
        <nav aria-label="Setup steps">
          <div
            className="flex items-center gap-2.25 mb-6.5 text-[17px] font-[650] tracking-[-0.02em] [@media(max-width:_760px)]:mb-3.5"
            data-fidelity-state="setup-brand"
          >
            <img
              className="w-5.5 h-5.5"
              src="/hoja-clover.8371400e.svg"
              alt=""
            />
            <span>Hoja</span>
          </div>
          <p
            data-setup-progress=""
            className="mt-0 mx-0 mb-2.5 text-muted text-[11px] font-[650] tracking-widest uppercase"
            data-fidelity-state="setup-progress-label"
          >
            {position.label}
          </p>
          <ol
            ref={stepListRef}
            className="grid gap-0.5 m-0 p-0 list-none [@media(max-width:_760px)]:flex [@media(max-width:_760px)]:gap-1.5 [@media(max-width:_760px)]:overflow-x-auto [@media(max-width:_760px)]:pb-1 [@media(max-width:_760px)]:scrollbar-thin"
          >
            {steps.map((id, index) => {
              const state =
                id === step
                  ? "current"
                  : draft.completed.includes(id)
                    ? "done"
                    : draft.skipped.includes(id)
                      ? "skipped"
                      : "upcoming";
              const visitable = canVisit(steps, step, id);
              return (
                <li
                  key={id}
                  data-fidelity-state={`setup-step-item is-${state}`}
                >
                  {visitable ? (
                    <button
                      className="flex items-center gap-2.5 w-full min-h-8.5 py-1 px-1.5 border-0 bg-transparent text-muted text-[12px] text-left [&:hover]:bg-subtle [&:hover]:text-foreground [@media(max-width:_760px)]:w-auto [@media(max-width:_760px)]:min-w-max [@media(max-width:_760px)]:pt-1 [@media(max-width:_760px)]:pr-2 [@media(max-width:_760px)]:pb-1 [@media(max-width:_760px)]:pl-1 [@media(max-width:_760px)]:border [@media(max-width:_760px)]:border-solid [@media(max-width:_760px)]:border-line shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      type="button"
                      onClick={() => go(id, "back")}
                      aria-current={undefined}
                    >
                      <span
                        className="grid place-items-center w-5.5 h-5.5 flex-none border border-solid border-line bg-panel text-muted text-[10px] font-bold in-data-[fidelity-state~=is-current]:border-accent in-data-[fidelity-state~=is-current]:bg-accent in-data-[fidelity-state~=is-current]:text-panel in-data-[fidelity-state~=is-done]:border-accent in-data-[fidelity-state~=is-done]:bg-accent-soft in-data-[fidelity-state~=is-done]:text-accent in-data-[fidelity-state~=is-skipped]:border-dashed"
                        data-fidelity-state="setup-step-dot"
                        aria-hidden="true"
                      >
                        {index + 1}
                      </span>
                      <span>{STEP_TITLES[id]}</span>
                    </button>
                  ) : (
                    <span
                      className="flex items-center gap-2.5 w-full min-h-8.5 py-1 px-1.5 border-0 bg-transparent text-muted text-[12px] text-left in-data-[fidelity-state~=is-current]:text-foreground in-data-[fidelity-state~=is-current]:font-[650] in-data-[fidelity-state~=is-skipped]:opacity-65 [@media(max-width:_760px)]:w-auto [@media(max-width:_760px)]:min-w-max [@media(max-width:_760px)]:pt-1 [@media(max-width:_760px)]:pr-2 [@media(max-width:_760px)]:pb-1 [@media(max-width:_760px)]:pl-1 [@media(max-width:_760px)]:border [@media(max-width:_760px)]:border-solid [@media(max-width:_760px)]:border-line [@media(max-width:_760px)]:in-data-[fidelity-state~=is-current]:bg-accent-soft shell-phone:min-h-11 pointer-coarse:min-h-11"
                      aria-current={state === "current" ? "step" : undefined}
                    >
                      <span
                        className="grid place-items-center w-5.5 h-5.5 flex-none border border-solid border-line bg-panel text-muted text-[10px] font-bold in-data-[fidelity-state~=is-current]:border-accent in-data-[fidelity-state~=is-current]:bg-accent in-data-[fidelity-state~=is-current]:text-panel in-data-[fidelity-state~=is-done]:border-accent in-data-[fidelity-state~=is-done]:bg-accent-soft in-data-[fidelity-state~=is-done]:text-accent in-data-[fidelity-state~=is-skipped]:border-dashed"
                        data-fidelity-state="setup-step-dot"
                        aria-hidden="true"
                      >
                        {state === "done" ? "✓" : index + 1}
                      </span>
                      <span>{STEP_TITLES[id]}</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <p
            className="mt-5 mx-1.5 mb-0 text-muted text-[11px] leading-normal [@media(max-width:_760px)]:hidden"
            data-fidelity-state="setup-steps-help"
          >
            You can go back without creating anything twice.
          </p>
        </nav>

        <section
          className="min-w-0 p-[clamp(24px,3vw,36px)] border border-solid border-line bg-panel [@media(max-width:_760px)]:py-5.5 [@media(max-width:_760px)]:px-4.5"
          data-fidelity-state="setup-form-panel"
          data-setup-form-panel=""
        >
          <div
            data-setup-step=""
            key={step}
            data-direction={direction}
            className="data-[fidelity-state~=is-forward]:animate-[setup-enter-forward_160ms_ease-out_both] data-[fidelity-state~=is-back]:animate-[setup-enter-back_160ms_ease-out_both] motion-reduce:data-[fidelity-state~=is-forward]:animate-none motion-reduce:data-[fidelity-state~=is-back]:animate-none"
            data-fidelity-state={`setup-step is-${direction}`}
          >
            <p
              className="mt-0 mx-0 mb-2.5 text-muted text-[11px] font-[650] tracking-widest uppercase"
              data-fidelity-state="setup-eyebrow"
            >
              {STEP_TITLES[step]}
            </p>
            <h1
              className="mt-0 mx-0 mb-2 text-[clamp(24px,3vw,30px)] leading-[1.15] tracking-[-0.04em] focus:[outline:none] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-4 pointer-coarse:focus-visible:outline-none"
              ref={headingRef}
              tabIndex={-1}
            >
              {headingFor(step, { mode: flowMode, boot, draft })}
            </h1>
            <p
              className="mt-0 mx-0 mb-6 text-muted text-[13px] leading-[1.55]"
              data-fidelity-state="setup-intro"
            >
              {introFor(step, { mode: flowMode, boot, draft, connectors })}
            </p>

            {step === "account" &&
              (boot.workspaceId ? (
                <div>
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    Your admin account was created and this browser is signed
                    in.
                  </p>
                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                      onClick={advance}
                    >
                      Continue
                    </button>
                  </div>
                </div>
              ) : (
                <form onSubmit={submitAccount} noValidate>
                  {status?.googleSignIn && (
                    <>
                      <a
                        className="mt-4.5 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-[0_1px_2px_#0002] [&:hover]:filter-[brightness(1.08)] w-full min-h-11"
                        data-control-primary=""
                        href={status.googleSignIn}
                        onClick={() =>
                          persist({ googleStarted: true, step: "account" })
                        }
                      >
                        Continue with Google
                      </a>
                      <p
                        className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                        data-fidelity-state="setup-hint"
                        data-setup-hint=""
                      >
                        Google verifies who you are; Hoja never sees your Google
                        password.
                      </p>
                      <p
                        className="flex items-center gap-2.5 mt-5.5 mx-0 mb-1 text-muted text-[10px] uppercase tracking-[0.08em] [&::before]:content-[''] [&::before]:flex-1 [&::before]:h-px [&::before]:bg-line [&::after]:content-[''] [&::after]:flex-1 [&::after]:h-px [&::after]:bg-line"
                        data-fidelity-state="setup-divider"
                      >
                        <span>or set up a password</span>
                      </p>
                    </>
                  )}
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-name"
                  >
                    Your name
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-name"
                      autoComplete="name"
                      required
                      value={name}
                      onChange={(event) => {
                        setName(event.target.value);
                        setFieldError(null);
                      }}
                      placeholder="Alex Morgan"
                      {...fieldProps("name")}
                    />
                  </label>
                  {accountFieldError("name")}
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-email"
                  >
                    Work email
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-email"
                      type="email"
                      autoComplete="email"
                      required
                      value={email}
                      onChange={(event) => {
                        setEmail(event.target.value);
                        setFieldError(null);
                      }}
                      placeholder="alex@example.com"
                      {...fieldProps("email")}
                    />
                  </label>
                  {accountFieldError("email")}
                  {status?.ownerBound && (
                    <p
                      className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                      data-fidelity-state="setup-hint"
                      data-setup-hint=""
                    >
                      This workspace was provisioned for one owner. Use the
                      address it was created for.
                    </p>
                  )}
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-password"
                  >
                    Create a password
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-password"
                      type="password"
                      autoComplete="new-password"
                      required
                      minLength={12}
                      value={password}
                      onChange={(event) => {
                        setPassword(event.target.value);
                        setFieldError(null);
                      }}
                      placeholder="At least 12 characters"
                      {...fieldProps("password")}
                    />
                  </label>
                  {accountFieldError("password")}
                  <p
                    className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                    data-fidelity-state="setup-hint"
                    data-setup-hint=""
                  >
                    Twelve characters or more. The browser session itself is an
                    HttpOnly cookie.
                  </p>
                  {fieldError &&
                    !ACCOUNT_FIELDS.includes(fieldError.field) &&
                    accountFieldError(fieldError.field)}
                  <button
                    type="submit"
                    className="mt-4.5 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] w-full min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-primary=""
                    disabled={busy}
                  >
                    Continue
                  </button>
                  {status?.googleSignIn && (
                    <a
                      className="mt-3.5 mx-auto mb-0 block bg-transparent text-accent text-[12px] shell-phone:flex shell-phone:min-h-11 shell-phone:items-center shell-phone:justify-center pointer-coarse:flex pointer-coarse:min-h-11 pointer-coarse:items-center pointer-coarse:justify-center"
                      href="/login"
                    >
                      I already have an account
                    </a>
                  )}
                </form>
              ))}

            {step === "workspace" &&
              (boot.workspaceId ? (
                <div>
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    Workspace{" "}
                    <b>{boot.workspaceUrlKey || draft.workspaceAddress}</b>{" "}
                    exists. Its name and address cannot be changed from this
                    step.
                  </p>
                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={back}
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                      onClick={advance}
                    >
                      Continue
                    </button>
                  </div>
                </div>
              ) : (
                <form onSubmit={submitWorkspace} noValidate>
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-workspace-name"
                  >
                    Workspace name
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-workspace-name"
                      required
                      value={draft.workspaceName}
                      onChange={(event) => {
                        const value = event.target.value;
                        persist({
                          workspaceName: value,
                          workspaceAddress: addressOnNameChange(
                            value,
                            draft.workspaceAddress,
                            addressTouched.current,
                          ),
                        });
                      }}
                      placeholder="Acme Studio"
                      {...fieldProps("workspaceName")}
                    />
                  </label>
                  {fieldError?.field === "workspaceName" && (
                    <p
                      className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                      data-fidelity-state="setup-field-error"
                      id="setup-workspaceName-error"
                      role="alert"
                    >
                      {fieldError.message}
                    </p>
                  )}

                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-workspace-address"
                  >
                    Workspace address
                    <span
                      className="flex items-stretch mt-1.75 border border-solid border-line bg-bg focus-within:outline-solid focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2"
                      data-fidelity-state="setup-address"
                      data-setup-address=""
                    >
                      <input
                        className="block w-full min-h-10.5 py-2.25 px-2.75 border-0 rounded-none bg-transparent text-foreground text-sm font-normal focus:[outline:none] focus:outline-offset-2 aria-invalid:border-danger m-0 shell-phone:min-h-11 pointer-coarse:min-h-11"
                        id="setup-workspace-address"
                        required
                        autoCapitalize="none"
                        spellCheck={false}
                        value={draft.workspaceAddress}
                        onChange={(event) => {
                          addressTouched.current = true;
                          persist({
                            workspaceAddress: event.target.value.toLowerCase(),
                          });
                        }}
                        placeholder="acme-studio"
                        {...fieldProps("workspaceAddress")}
                      />
                      {adapter.addressDomain && (
                        <span
                          className="flex items-center py-0 px-2.75 border-l border-l-line text-muted text-[12px] whitespace-nowrap"
                          data-fidelity-state="setup-address-suffix"
                        >
                          {adapter.addressDomain}
                        </span>
                      )}
                    </span>
                  </label>
                  <p
                    className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                    data-fidelity-state="setup-hint"
                    data-setup-hint=""
                  >
                    Lowercase letters, numbers and hyphens. Changing the name
                    later will not overwrite an address you edit here.
                  </p>
                  {fieldError?.field === "workspaceAddress" && (
                    <p
                      className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                      data-fidelity-state="setup-field-error"
                      id="setup-workspaceAddress-error"
                      role="alert"
                    >
                      {fieldError.message}
                    </p>
                  )}

                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={back}
                    >
                      Back
                    </button>
                    <button
                      type="submit"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                    >
                      Continue
                    </button>
                  </div>
                </form>
              ))}

            {step === "team" &&
              (boot.teamId ? (
                <div>
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    Team{" "}
                    <b>
                      {draft.teamName} ({normaliseTeamKey(draft.teamKey)})
                    </b>{" "}
                    exists.
                  </p>
                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={back}
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                      onClick={advance}
                    >
                      Continue
                    </button>
                  </div>
                </div>
              ) : (
                <form onSubmit={submitTeam} noValidate>
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-team-name"
                  >
                    Team name
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-team-name"
                      required
                      value={draft.teamName}
                      onChange={(event) =>
                        persist({ teamName: event.target.value })
                      }
                      placeholder="Engineering"
                      {...fieldProps("teamName")}
                    />
                  </label>
                  {fieldError?.field === "teamName" && (
                    <p
                      className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                      data-fidelity-state="setup-field-error"
                      id="setup-teamName-error"
                      role="alert"
                    >
                      {fieldError.message}
                    </p>
                  )}
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-team-key"
                  >
                    Team key
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-team-key"
                      required
                      maxLength={8}
                      value={draft.teamKey}
                      onChange={(event) =>
                        persist({
                          teamKey: normaliseTeamKey(event.target.value),
                        })
                      }
                      placeholder="ENG"
                      {...fieldProps("teamKey")}
                    />
                  </label>
                  <p
                    className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                    data-fidelity-state="setup-hint"
                    data-setup-hint=""
                  >
                    Tasks get identifiers like{" "}
                    <code className="py-px px-1.25 border border-solid border-line bg-subtle font-[ui-monospace,monospace] text-[11px]">
                      {preview.issueExample}
                    </code>
                    . A team is required because tasks and projects belong to
                    one.
                  </p>
                  {fieldError?.field === "teamKey" && (
                    <p
                      className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                      data-fidelity-state="setup-field-error"
                      id="setup-teamKey-error"
                      role="alert"
                    >
                      {fieldError.message}
                    </p>
                  )}
                  {flowMode === "first-run" && !boot.workspaceId && (
                    <p
                      className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                      data-fidelity-state="setup-hint"
                      data-setup-hint=""
                    >
                      Creating the workspace also creates your admin account,
                      this team, its default workflow states and your browser
                      session — in one step. It cannot be half-created.
                    </p>
                  )}
                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={back}
                    >
                      Back
                    </button>
                    <button
                      type="submit"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                      disabled={busy}
                    >
                      {busy
                        ? "Creating…"
                        : boot.workspaceId
                          ? "Create team"
                          : "Create workspace"}
                    </button>
                  </div>
                </form>
              ))}

            {step === "project" && (
              <form onSubmit={submitProject} noValidate>
                {boot.projectId ? (
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    Project <b>{draft.projectName}</b> exists.
                  </p>
                ) : (
                  <>
                    <label
                      className="block mt-4 text-[12px] font-[650]"
                      htmlFor="setup-project-name"
                    >
                      Project name
                      <input
                        className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                        id="setup-project-name"
                        value={draft.projectName}
                        onChange={(event) =>
                          persist({ projectName: event.target.value })
                        }
                        placeholder="Website refresh"
                        {...fieldProps("projectName")}
                      />
                    </label>
                    <p
                      className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                      data-fidelity-state="setup-hint"
                      data-setup-hint=""
                    >
                      Optional. A project groups tasks across teams; you can add
                      one any time from Projects.
                    </p>
                    {fieldError && (
                      <p
                        className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                        data-fidelity-state="setup-field-error"
                        id={`setup-${fieldError.field}-error`}
                        role="alert"
                      >
                        {fieldError.message}
                      </p>
                    )}
                  </>
                )}
                <div
                  className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                  data-fidelity-state="setup-actions"
                  data-setup-actions=""
                >
                  <button
                    type="button"
                    className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-outline=""
                    onClick={back}
                  >
                    Back
                  </button>
                  {!boot.projectId && (
                    <button
                      type="button"
                      className="mt-3.5 mx-auto mb-0 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [@media(max-width:_760px)]:order-3 block bg-transparent text-accent text-[12px] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      onClick={skip}
                    >
                      Skip this step
                    </button>
                  )}
                  <button
                    type="submit"
                    className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-primary=""
                    disabled={busy}
                  >
                    {busy
                      ? "Creating…"
                      : boot.projectId
                        ? "Continue"
                        : "Create project"}
                  </button>
                </div>
              </form>
            )}

            {step === "invitations" && (
              <div>
                <ul
                  className="grid gap-3 mt-0 mx-0 mb-5.5 p-0 list-none"
                  data-fidelity-state="setup-results"
                  data-setup-results=""
                  aria-live="polite"
                >
                  {inviteResults.map((result) => (
                    <li
                      key={result.userId}
                      className="py-3 px-3.5 border border-solid border-line bg-bg"
                    >
                      <p
                        data-setup-invite-result=""
                        className="m-0 text-[12px] font-[650]"
                        data-fidelity-state="setup-result-head"
                      >
                        Invite link ready
                      </p>
                      <p
                        className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                        data-fidelity-state="setup-hint"
                        data-setup-hint=""
                      >
                        Hoja does not send invitation email, so share this
                        single-use link yourself.
                      </p>
                      <span
                        className="flex items-stretch mt-1.75 border border-solid border-line bg-bg focus-within:outline-solid focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 max-sm:flex-col"
                        data-fidelity-state="setup-address"
                        data-setup-address=""
                      >
                        <input
                          className="block w-full min-h-10.5 py-2.25 px-2.75 border-0 rounded-none bg-transparent text-foreground text-sm font-normal focus:[outline:none] focus:outline-offset-2 aria-invalid:border-danger m-0 shell-phone:min-h-11 pointer-coarse:min-h-11"
                          aria-label="Invite link"
                          readOnly
                          value={result.link}
                          onFocus={(event) => event.currentTarget.select()}
                        />
                        <button
                          type="button"
                          className="flex items-center py-0 px-2.75 border-l border-l-line text-muted text-[12px] whitespace-nowrap min-h-10.5 rounded-none [&:hover]:bg-subtle gap-1.75 bg-panel shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 max-sm:justify-center max-sm:border-l-0 max-sm:border-t max-sm:border-t-line max-sm:text-[13px] max-sm:font-[550] max-sm:text-foreground"
                          data-control-outline=""
                          onClick={() => void copy(result.link)}
                        >
                          {copied === result.link ? "Copied" : "Copy"}
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
                {draft.invites.length > 0 && inviteResults.length === 0 && (
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    {pendingCount || draft.invites.length} invitation
                    {(pendingCount || draft.invites.length) === 1
                      ? ""
                      : "s"}{" "}
                    pending. Links are not stored in this browser — resend from
                    Members if needed.
                  </p>
                )}
                <form onSubmit={submitInvite} noValidate>
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-invite-name"
                  >
                    Name
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-invite-name"
                      value={inviteName}
                      onChange={(event) => setInviteName(event.target.value)}
                      placeholder="Sam Rivera"
                    />
                  </label>
                  <label
                    className="block mt-4 text-[12px] font-[650]"
                    htmlFor="setup-invite-email"
                  >
                    Email
                    <input
                      className="block w-full min-h-10.5 mt-1.75 py-2.25 px-2.75 border border-solid border-line rounded-none bg-bg text-foreground text-sm font-normal focus:outline-solid focus:outline-2 focus:outline-accent focus:outline-offset-2 aria-invalid:border-danger shell-phone:min-h-11 pointer-coarse:min-h-11"
                      id="setup-invite-email"
                      type="email"
                      value={inviteEmail}
                      onChange={(event) => setInviteEmail(event.target.value)}
                      placeholder="sam@example.com"
                      {...fieldProps("inviteEmail")}
                    />
                  </label>
                  {fieldError && (
                    <p
                      className="mt-2 mx-0 mb-0 text-danger text-[12px]"
                      data-fidelity-state="setup-field-error"
                      id={`setup-${fieldError.field}-error`}
                      role="alert"
                    >
                      {fieldError.message}
                    </p>
                  )}
                  <p
                    className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                    data-fidelity-state="setup-hint"
                    data-setup-hint=""
                  >
                    Optional. Invites are single use and expire after 72 hours.
                  </p>
                  <div
                    className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                    data-fidelity-state="setup-actions"
                    data-setup-actions=""
                  >
                    <button
                      type="button"
                      className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={back}
                    >
                      Back
                    </button>
                    {inviteResults.length === 0 && (
                      <button
                        type="button"
                        className="mt-3.5 mx-auto mb-0 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [@media(max-width:_760px)]:order-3 block bg-transparent text-accent text-[12px] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                        onClick={skip}
                      >
                        Skip this step
                      </button>
                    )}
                    {inviteResults.length === 0 ? (
                      <button
                        type="submit"
                        className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                        data-control-primary=""
                        disabled={busy}
                      >
                        {busy ? "Adding…" : "Add invitation"}
                      </button>
                    ) : (
                      // Once a link exists, Continue below is the one primary action; adding
                      // another person is secondary, so the two are not identical dark buttons.
                      <button
                        type="submit"
                        className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 min-h-11 rounded-none [&:hover]:bg-subtle flex items-center justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 font-[550] disabled:cursor-wait disabled:opacity-65 shell-phone:min-w-11 pointer-coarse:min-w-11"
                        data-control-outline=""
                        disabled={busy}
                      >
                        {busy ? "Adding…" : "Add another invitation"}
                      </button>
                    )}
                  </div>
                  {inviteResults.length > 0 && (
                    <button
                      type="button"
                      className="mt-4.5 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] w-full min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-fidelity-state="primary full setup-next"
                      data-control-primary=""
                      onClick={advance}
                    >
                      Continue
                    </button>
                  )}
                </form>
              </div>
            )}

            {step === "integrations" && (
              <div>
                <p
                  className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                  data-fidelity-state="setup-hint"
                  data-setup-hint=""
                >
                  Read-only access to Gmail, Drive, Calendar and Docs. An
                  organization connection is available to everyone in this
                  workspace; a personal connection is only yours. You can
                  disconnect it later in Settings → Connectors.
                </p>
                {connected.includes("google_workspace") ? (
                  <p
                    className="flex items-center gap-2.25 mt-0 mx-0 mb-3.5 py-3 px-3.5 border border-solid border-line bg-accent-soft text-[12px]"
                    data-fidelity-state="setup-created"
                    data-setup-created=""
                    role="status"
                  >
                    <span className="text-accent font-bold" aria-hidden="true">
                      ✓
                    </span>{" "}
                    Google Workspace is connected.
                  </p>
                ) : (
                  <a
                    className="mt-4.5 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-[0_1px_2px_#0002] [&:hover]:filter-[brightness(1.08)] w-full min-h-11"
                    data-control-primary=""
                    href={adapter.googleConnectUrl()}
                    onClick={() => persist({ step: "integrations" })}
                  >
                    Connect Google Workspace
                  </a>
                )}
                <div
                  className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                  data-fidelity-state="setup-actions"
                  data-setup-actions=""
                >
                  <button
                    type="button"
                    className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-outline=""
                    onClick={back}
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="mt-3.5 mx-auto mb-0 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [@media(max-width:_760px)]:order-3 block bg-transparent text-accent text-[12px] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    onClick={skip}
                  >
                    Skip this step
                  </button>
                  {connected.includes("google_workspace") && (
                    <button
                      type="button"
                      className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-primary=""
                      onClick={advance}
                    >
                      Continue
                    </button>
                  )}
                </div>
              </div>
            )}

            {step === "ready" && (
              <div>
                <ul
                  className="grid mt-0 mx-0 mb-5 p-0 border border-solid border-line list-none"
                  data-fidelity-state="setup-summary"
                  data-setup-summary=""
                >
                  <li className="flex justify-between gap-4 py-3 px-3.5 border-b border-b-line text-[12px] last:border-b-0">
                    <span className="text-muted">Workspace</span>
                    <b className="text-right wrap-anywhere">
                      {boot.workspaceUrlKey ||
                        normaliseAddress(draft.workspaceAddress)}
                    </b>
                  </li>
                  <li className="flex justify-between gap-4 py-3 px-3.5 border-b border-b-line text-[12px] last:border-b-0">
                    <span className="text-muted">Team</span>
                    <b className="text-right wrap-anywhere">
                      {draft.teamName} ({normaliseTeamKey(draft.teamKey)})
                    </b>
                  </li>
                  <li className="flex justify-between gap-4 py-3 px-3.5 border-b border-b-line text-[12px] last:border-b-0">
                    <span className="text-muted">Project</span>
                    <b className="text-right wrap-anywhere">
                      {boot.projectId
                        ? draft.projectName
                        : "Skipped — add one later"}
                    </b>
                  </li>
                  <li className="flex justify-between gap-4 py-3 px-3.5 border-b border-b-line text-[12px] last:border-b-0">
                    <span className="text-muted">Invitations</span>
                    <b className="text-right wrap-anywhere">
                      {inviteResults.length
                        ? `${inviteResults.length} sent`
                        : draft.invites.length
                          ? `${draft.invites.length} pending`
                          : "Skipped — add people later"}
                    </b>
                  </li>
                  <li className="flex justify-between gap-4 py-3 px-3.5 border-b border-b-line text-[12px] last:border-b-0">
                    <span className="text-muted">Google Workspace</span>
                    <b className="text-right wrap-anywhere">
                      {connected.includes("google_workspace")
                        ? "Connected"
                        : connectors.googleWorkspace
                          ? "Skipped"
                          : "Not configured here"}
                    </b>
                  </li>
                </ul>
                {apiKey && (
                  <details
                    className="mt-4.5 mx-0 mb-0"
                    data-fidelity-state="setup-key"
                    data-setup-key=""
                  >
                    <summary className="cursor-pointer text-[12px] font-[650] shell-phone:py-3 pointer-coarse:py-3 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:text-[13px] pointer-coarse:text-[13px] shell-phone:leading-5 pointer-coarse:leading-5">
                      Admin API key for CLI setup
                    </summary>
                    <code className="p-2.75 border border-solid border-line bg-bg font-[ui-monospace,monospace] text-[11px] block my-2.5 mx-0 wrap-anywhere">
                      {apiKey}
                    </code>
                    <button
                      type="button"
                      className="[&:hover]:bg-subtle flex items-center gap-1.75 border border-solid border-line rounded-md bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                      data-control-outline=""
                      onClick={() => void copy(apiKey)}
                    >
                      {copied === apiKey ? "Copied" : "Copy API key"}
                    </button>
                    <p
                      className="mt-2.5 mx-0 mb-0 text-muted text-[11.5px] leading-[1.55]"
                      data-fidelity-state="setup-hint"
                      data-setup-hint=""
                    >
                      Shown once. It is not saved in this browser.
                    </p>
                  </details>
                )}
                <div
                  className="flex items-center gap-2.5 flex-wrap mt-6.5 [@media(max-width:_760px)]:items-stretch [@media(max-width:_760px)]:flex-col"
                  data-fidelity-state="setup-actions"
                  data-setup-actions=""
                >
                  <button
                    type="button"
                    className="min-h-10.5 rounded-none [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 [&:hover]:bg-subtle flex items-center [@media(max-width:_760px)]:justify-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-outline=""
                    onClick={back}
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="min-w-37.5 [@media(max-width:_760px)]:w-full [@media(max-width:_760px)]:m-0 inline-flex items-center justify-center gap-1.75 rounded-lg bg-accent text-white py-2.25 px-3.5 font-[650] shadow-none [&:hover]:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] [&:hover:not(:disabled)]:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] [&:hover:not(:disabled)]:filter-none [&:active:not(:disabled)]:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-primary=""
                    onClick={finish}
                  >
                    Open my workspace
                  </button>
                </div>
              </div>
            )}

            {error && (
              <div
                data-setup-error
                className="grid gap-1 mt-5.5 py-3 px-3.5 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))] text-[12px]"
                data-fidelity-state="setup-error"
                role="alert"
              >
                <b>That did not finish</b>
                <span className="text-foreground">{error}</span>
                {/already set up/i.test(error) && (
                  <button
                    type="button"
                    className="justify-self-start mt-1.5 rounded-none [&:hover]:bg-subtle flex items-center gap-1.75 border border-solid border-line bg-panel py-2 px-2.75 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-control-outline=""
                    onClick={recover}
                  >
                    Reload Hoja
                  </button>
                )}
              </div>
            )}
          </div>
        </section>

        <aside
          className="sticky top-8 min-w-0 p-5.5 border border-solid border-line bg-panel [@media(max-height:_700px)]:static [@media(max-width:_1040px)]:col-span-full [@media(max-width:_1040px)]:static"
          data-fidelity-state="setup-preview"
          data-setup-preview=""
          aria-label="Workspace preview"
        >
          <p
            className="mt-0 mx-0 mb-2.5 text-muted text-[11px] font-[650] tracking-widest uppercase"
            data-fidelity-state="setup-eyebrow"
          >
            Preview
          </p>
          <p
            className="mt-0 mx-0 mb-0.5 text-[16px] font-[650] tracking-[-0.02em]"
            data-fidelity-state="setup-preview-name"
          >
            {preview.workspaceName}
          </p>
          <p
            data-setup-preview-address=""
            className="mt-0 mx-0 mb-3.5 text-muted text-[12px] wrap-anywhere"
            data-fidelity-state="setup-preview-address"
          >
            {preview.address}
            {adapter.addressDomain}
          </p>
          <p
            className="mt-0 mx-0 mb-3.5 text-muted text-[11px] leading-normal"
            data-fidelity-state="setup-preview-live"
            aria-live="polite"
          >
            {liveSummary}
          </p>
          <ul className="grid gap-px m-0 p-0 list-none bg-line [@media(max-width:_1040px)]:gap-2 [@media(max-width:_1040px)]:bg-transparent [@media(max-width:_1040px)]:grid-cols-[repeat(auto-fit,minmax(150px,1fr))] [@media(max-width:_760px)]:grid-cols-2">
            {preview.rows.map((row) => (
              <li
                key={row.label}
                data-fidelity-state={`is-${row.state}`}
                className="grid gap-0.75 py-2.25 px-0.5 bg-panel text-[11px] [@media(max-width:_1040px)]:py-2.25 [@media(max-width:_1040px)]:px-2.5 [@media(max-width:_1040px)]:border [@media(max-width:_1040px)]:border-solid [@media(max-width:_1040px)]:border-line"
              >
                <span className="text-muted">{row.label}</span>
                <b className="wrap-anywhere font-semibold in-data-[fidelity-state~=is-empty]:text-muted in-data-[fidelity-state~=is-empty]:font-normal in-data-[fidelity-state~=is-unavailable]:text-muted in-data-[fidelity-state~=is-unavailable]:font-normal in-data-[fidelity-state~=is-typed]:italic">
                  {row.value}
                </b>
              </li>
            ))}
            <li className="grid gap-0.75 py-2.25 px-0.5 bg-panel text-[11px] [@media(max-width:_1040px)]:py-2.25 [@media(max-width:_1040px)]:px-2.5 [@media(max-width:_1040px)]:border [@media(max-width:_1040px)]:border-solid [@media(max-width:_1040px)]:border-line">
              <span className="text-muted">First task</span>
              <b className="wrap-anywhere font-semibold">
                {preview.issueExample}
              </b>
            </li>
          </ul>
          <p
            className="mt-4 mx-0 mb-0 text-muted text-[10.5px] leading-normal"
            data-fidelity-state="setup-preview-note"
          >
            This preview is built from what you type and from the server’s
            confirmations. It never shows a resource that has not been created.
          </p>
        </aside>
      </div>
    </main>
  );
}

function headingFor(
  step: StepId,
  input: { mode: FlowMode; boot: Boot; draft: SetupDraft },
): string {
  switch (step) {
    case "account":
      return "Let’s make this yours.";
    case "workspace":
      return input.mode === "existing"
        ? "Name your new workspace."
        : "Give your workspace a home.";
    case "team":
      return input.boot.workspaceId
        ? "Add your first team."
        : "Create your workspace and first team.";
    case "project":
      return "Start with a project?";
    case "invitations":
      return "Bring your teammates in.";
    case "integrations":
      return "Connect Google Workspace.";
    case "ready":
      return "You’re all set.";
  }
}

function introFor(
  step: StepId,
  input: {
    mode: FlowMode;
    boot: Boot;
    draft: SetupDraft;
    connectors: { googleWorkspace: boolean };
  },
): string {
  switch (step) {
    case "account":
      return "You’ll be the workspace admin. This account signs you in and owns the workspace.";
    case "workspace":
      return "The name is for people; the address is what they type.";
    case "team":
      return "Teams own tasks and give them their identifier prefix.";
    case "project":
      return "Optional. Projects group tasks across teams.";
    case "invitations":
      return "Optional. Add one person at a time; skip and do it later from Members.";
    case "integrations":
      return "Optional, and only shown because this deployment is configured for it.";
    case "ready":
      return "Here is exactly what exists now.";
  }
}
