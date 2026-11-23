// The shared setup experience.
//
// One shell (step navigation, focused form, live preview) with edition-specific
// server calls behind `SetupAdapter`. The component owns no product rules that
// belong to the server: it validates only what the API refuses, and every state
// it shows as "created" comes from a response, not from a click.
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import './setup.css';
import {buildSteps, canSkip, canVisit, nextStep, previousStep, progress, STEP_TITLES} from './flow';
import type {FlowMode, StepId} from './flow';
import {buildPreview} from './preview';
import {clearDraft, freshDraft, loadDraft, saveDraft} from './draft';
import type {SetupDraft} from './draft';
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
} from './validation';
import {errorMessage} from './api';
import type {InviteResult, SetupAdapter} from './types';

type Props = {
  adapter: SetupAdapter;
  mode: FlowMode;
  initial?: {workspaceId?: string; workspaceUrlKey?: string; teamId?: string};
  onFinish?: () => void;
};

type Boot = {workspaceId?: string; workspaceUrlKey?: string; teamId?: string; projectId?: string};

const memory = new Map<string, string>();
const fallbackStorage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => { memory.set(key, value) },
  removeItem: (key: string) => { memory.delete(key) },
};

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  try {
    return window.localStorage;
  } catch {
    return fallbackStorage;
  }
}

export function SetupWizard({adapter, mode, initial, onFinish}: Props) {
  const store = storage();
  const scope = useMemo(() => ({edition: adapter.edition} as const), [adapter.edition]);
  // A saved draft wins over the caller's starting mode: a first run that has
  // already created its workspace is resumed on the same path, not restarted as
  // an "existing workspace" flow with an empty form.
  const [draft, setDraft] = useState<SetupDraft>(() => {
    const saved = loadDraft(store, {...scope, workspaceId: initial?.workspaceId});
    if (!saved) return freshDraft(mode);
    // A draft for a different workspace must not leak into this one; a pre-create
    // draft only belongs here when a Google sign-in was started from it.
    if (initial?.workspaceId && !saved.workspaceId && !saved.googleStarted) return freshDraft(mode);
    return saved;
  });
  const [step, setStep] = useState<StepId>(() => draft.step);
  const [direction, setDirection] = useState<'forward' | 'back'>('forward');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteResults, setInviteResults] = useState<InviteResult[]>([]);
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldError, setFieldError] = useState<{field: string; message: string} | null>(null);
  const [status, setStatus] = useState<{initialized: boolean; ownerBound: boolean; googleSignIn: string | null} | null>(null);
  const [connectors, setConnectors] = useState<{googleWorkspace: boolean}>({googleWorkspace: false});
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
  const busyRef = useRef(false);
  const addressTouched = useRef(Boolean(draft.workspaceAddress && draft.workspaceName && draft.workspaceAddress !== deriveAddress(draft.workspaceName)));

  const flowMode = draft.mode;
  const steps = useMemo(
    () => buildSteps({
      mode: flowMode,
      googleSignIn: Boolean(status?.googleSignIn),
      ownerBound: Boolean(status?.ownerBound),
      workspaceCreated: Boolean(boot.workspaceId),
      teamExists: Boolean(boot.teamId),
      googleWorkspaceAvailable: connectors.googleWorkspace,
    }),
    [flowMode, status?.googleSignIn, status?.ownerBound, boot.workspaceId, boot.teamId, connectors.googleWorkspace],
  );

  const persist = useCallback((patch: Partial<SetupDraft>) => {
    setDraft(previous => {
      const next = {...previous, ...patch};
      saveDraft(store, {...scope, workspaceId: next.workspaceId}, next);
      return next;
    });
  }, [scope, store]);

  // Public, read-only facts: is this instance already set up, can it sign in with
  // Google, and can it connect anything? A failure here never blocks a password
  // setup, it only hides the optional move.
  useEffect(() => {
    let live = true;
    Promise.allSettled([
      adapter.status()
        .then((value) => { if (live) setStatus(value) })
        .catch(() => { if (live) setStatus({initialized: false, ownerBound: false, googleSignIn: null}) }),
      adapter.connectorConfig()
        .then((value) => { if (live) setConnectors(value) })
        .catch(() => { if (live) setConnectors({googleWorkspace: false}) }),
    ]).then(() => { if (live) setConfigReady(true) });
    return () => { live = false };
  }, [adapter]);

  // Connected accounts are tenant data, so they are only asked for once this
  // browser has a workspace session — never as an anonymous probe.
  useEffect(() => {
    if (!boot.workspaceId) return;
    let live = true;
    adapter.connectorAccounts()
      .then((providers) => { if (live) setConnected(providers) })
      .catch(() => { if (live) setConnected([]) });
    return () => { live = false };
  }, [adapter, boot.workspaceId]);

  // A returning person with a draft resumes where they stopped; a step that is no
  // longer part of the flow (team removed after creation) resolves to the first
  // unfinished step instead of a blank screen.
  useEffect(() => {
    if (!configReady || steps.includes(step)) return;
    const next = steps.find((candidate) => !draft.completed.includes(candidate) && !draft.skipped.includes(candidate)) ?? 'ready';
    setStep(next);
    persist({step: next});
  }, [configReady, steps, step, draft.completed, draft.skipped, persist]);

  useEffect(() => {
    if (!initial?.workspaceId) return;
    setBoot(previous => previous.workspaceId
      ? previous
      : {workspaceId: initial.workspaceId, workspaceUrlKey: initial.workspaceUrlKey, teamId: initial.teamId});
  }, [initial?.workspaceId, initial?.workspaceUrlKey, initial?.teamId]);

  useEffect(() => {
    headingRef.current?.focus();
  }, [step]);

  useEffect(() => {
    if (!draft.invites.length) return;
    let live = true;
    adapter.pendingInvites()
      .then((rows) => { if (live) setPendingCount(rows.length) })
      .catch(() => { if (live) setPendingCount(0) });
    return () => { live = false };
  }, [adapter, draft.invites.length]);

  const preview = buildPreview({draft, addressDomain: adapter.addressDomain, googleWorkspaceAvailable: connectors.googleWorkspace});
  const position = progress(steps, step);
  const liveSummary = [
    draft.workspaceName.trim() ? `Workspace ${draft.workspaceName.trim()}` : 'Workspace not named',
    draft.workspaceAddress ? `address ${normaliseAddress(draft.workspaceAddress)}` : 'no address yet',
    draft.teamName.trim() ? `team ${draft.teamName.trim()} (${normaliseTeamKey(draft.teamKey) || '—'})` : 'no team yet',
  ].join(', ');

  const focusFirstInvalid = () => {
    window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>('[aria-invalid="true"]');
      (target ?? headingRef.current)?.focus();
    }, 0);
  };

  const go = (target: StepId | null, movement: 'forward' | 'back') => {
    if (!target) return;
    setDirection(movement);
    setError('');
    setFieldError(null);
    setStep(target);
    persist({step: target});
  };

  const advance = () => go(nextStep(steps, step), 'forward');
  const back = () => go(previousStep(steps, step), 'back');

  const markDone = (id: StepId) => {
    const completed = draft.completed.includes(id) ? draft.completed : [...draft.completed, id];
    persist({completed});
  };

  const skip = () => {
    if (!canSkip(step)) return;
    const skipped = draft.skipped.includes(step) ? draft.skipped : [...draft.skipped, step];
    persist({skipped});
    go(nextStep(steps, step), 'forward');
  };

  async function run<T>(work: () => Promise<T>): Promise<T | null> {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    setError('');
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
    const checks: {field: string; message: string}[] = [
      {field: 'name', message: checkName(name)},
      {field: 'email', message: checkEmail(email)},
      {field: 'password', message: checkPassword(password)},
    ];
    const first = checks.find((entry) => entry.message);
    if (first) {
      setFieldError(first);
      focusFirstInvalid();
      return;
    }
    markDone('account');
    go(nextStep(steps, step), 'forward');
  };

  const submitWorkspace = (event: FormEvent) => {
    event.preventDefault();
    const nameIssue = checkWorkspaceName(draft.workspaceName);
    const addressIssue = checkAddress(draft.workspaceAddress);
    if (nameIssue || addressIssue) {
      setFieldError({field: nameIssue ? 'workspaceName' : 'workspaceAddress', message: nameIssue || addressIssue});
      focusFirstInvalid();
      return;
    }
    markDone('workspace');
    go(nextStep(steps, step), 'forward');
  };

  const submitTeam = async (event: FormEvent) => {
    event.preventDefault();
    const teamIssue = checkTeamName(draft.teamName) || checkTeamKey(draft.teamKey);
    const workspaceIssue = boot.workspaceId ? '' : checkWorkspaceName(draft.workspaceName) || checkAddress(draft.workspaceAddress);
    const accountIssue = flowMode === 'first-run' && !boot.workspaceId
      ? (checkName(name) || checkEmail(email) || checkPassword(password))
      : '';
    if (teamIssue || workspaceIssue || accountIssue) {
      const pair = teamIssue
        ? {field: checkTeamName(draft.teamName) ? 'teamName' : 'teamKey', message: teamIssue}
        : workspaceIssue
          ? {field: 'workspaceAddress', message: workspaceIssue}
          : {field: checkName(name) ? 'name' : checkEmail(email) ? 'email' : 'password', message: accountIssue};
      setFieldError(pair);
      focusFirstInvalid();
      return;
    }

    if (flowMode === 'first-run' && !boot.workspaceId) {
      const result = await run(() => adapter.bootstrap({
        name,
        email,
        password,
        workspaceName: draft.workspaceName,
        workspaceAddress: normaliseAddress(draft.workspaceAddress),
        teamName: draft.teamName,
        teamKey: normaliseTeamKey(draft.teamKey),
      }));
      if (!result) {
        // A lost response can mean the server did commit. Ask before suggesting a retry.
        const settled = await adapter.status().catch(() => null);
        if (settled?.initialized) {
          setError('This instance is already set up, so Hoja will not create it again. Reload to continue with the account that was created.');
        }
        return;
      }
      setApiKey(result.apiKey ?? '');
      setPassword('');
      const nextBoot: Boot = {workspaceId: result.workspaceId, workspaceUrlKey: result.workspaceUrlKey, teamId: result.teamId};
      setBoot(nextBoot);
      persist({workspaceId: result.workspaceId, workspaceUrlKey: result.workspaceUrlKey, teamId: result.teamId, completed: [...draft.completed, 'workspace', 'team']});
      go('project', 'forward');
      return;
    }

    if (!boot.teamId) {
      const created = await run(() => adapter.createTeam({name: draft.teamName, key: normaliseTeamKey(draft.teamKey)}));
      if (!created) return;
      setBoot(previous => ({...previous, teamId: created.id}));
      persist({teamId: created.id, completed: [...draft.completed, 'team']});
      go('project', 'forward');
      return;
    }
    go('project', 'forward');
  };

  const submitProject = async (event: FormEvent) => {
    event.preventDefault();
    const issue = checkProjectName(draft.projectName);
    if (!draft.projectName.trim()) {
      skip();
      return;
    }
    if (issue) {
      setFieldError({field: 'projectName', message: issue});
      focusFirstInvalid();
      return;
    }
    if (!boot.teamId) {
      setError('A project needs a team. Go back and create the team first.');
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
      return adapter.createProject({name: draft.projectName, teamId: boot.teamId as string});
    });
    if (!created) return;
    setBoot(previous => ({...previous, projectId: created.id}));
    persist({projectId: created.id, completed: [...draft.completed, 'project']});
    go(nextStep(steps, step), 'forward');
  };

  const submitInvite = async (event: FormEvent) => {
    event.preventDefault();
    const issue = checkInvitee(inviteName, inviteEmail);
    if (issue) {
      setFieldError({field: 'inviteEmail', message: issue});
      focusFirstInvalid();
      return;
    }
    if (!boot.teamId) {
      setError('Add a team before inviting people to it.');
      return;
    }
    const result = await run(() => adapter.invite({name: inviteName, email: inviteEmail, teamId: boot.teamId as string}));
    if (!result) return;
    setInviteResults(previous => [...previous, result]);
    const invites = [...draft.invites, {name: inviteName.trim(), email: inviteEmail.trim().toLowerCase()}];
    persist({invites});
    setInviteName('');
    setInviteEmail('');
    setPendingCount(invites.length);
  };

  const copy = async (value: string) => {
    try {
      if (!navigator.clipboard) throw new Error('no clipboard');
      await navigator.clipboard.writeText(value);
      setCopied(value);
    } catch {
      setError('Copying is blocked in this browser. Select the value and copy it manually.');
    }
  };

  const recover = () => {
    window.location.reload();
  };

  const finish = () => {
    clearDraft(store, {...scope, workspaceId: boot.workspaceId});
    if (onFinish) onFinish();
    else adapter.openWorkspace();
  };

  const fieldProps = (field: string) => {
    const invalid = fieldError?.field === field;
    return {
      'aria-invalid': invalid ? true : undefined,
      'aria-describedby': invalid ? `setup-${field}-error` : undefined,
    };
  };

  if (status?.initialized && flowMode === 'first-run' && !boot.workspaceId) {
    return (
      <main className="setup-page">
        <section className="setup-card setup-finished">
          <h1>This workspace is already set up</h1>
          <p>Setup was completed in another tab or by another person. Sign in to continue.</p>
          <a className="primary full" href="/login">Go to sign in</a>
        </section>
      </main>
    );
  }

  return (
    <main className="setup-page">
      <div className="setup-shell">
        <nav className="setup-steps" aria-label="Setup steps">
          <div className="setup-brand">
            <img className="brand-mark" src="/hoja-clover.8371400e.svg" alt=""/>
            <span>Hoja</span>
          </div>
          <p className="setup-progress-label">{position.label}</p>
          <ol>
            {steps.map((id, index) => {
              const state = id === step ? 'current' : draft.completed.includes(id) ? 'done' : draft.skipped.includes(id) ? 'skipped' : 'upcoming';
              const visitable = canVisit(steps, step, id);
              return (
                <li key={id} className={`setup-step-item is-${state}`}>
                  {visitable ? (
                    <button type="button" onClick={() => go(id, 'back')} aria-current={undefined}>
                      <span className="setup-step-dot" aria-hidden="true">{index + 1}</span>
                      <span>{STEP_TITLES[id]}</span>
                    </button>
                  ) : (
                    <span aria-current={state === 'current' ? 'step' : undefined}>
                      <span className="setup-step-dot" aria-hidden="true">{state === 'done' ? '✓' : index + 1}</span>
                      <span>{STEP_TITLES[id]}</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <p className="setup-steps-help">You can go back without creating anything twice.</p>
        </nav>

        <section className="setup-form-panel">
          <div key={step} className={`setup-step is-${direction}`}>
            <p className="setup-eyebrow">{STEP_TITLES[step]}</p>
            <h1 ref={headingRef} tabIndex={-1}>{headingFor(step, {mode: flowMode, boot, draft})}</h1>
            <p className="setup-intro">{introFor(step, {mode: flowMode, boot, draft, connectors})}</p>

            {step === 'account' && (boot.workspaceId ? (
              <div>
                <p className="setup-created" role="status"><span aria-hidden="true">✓</span> Your admin account was created and this browser is signed in.</p>
                <div className="setup-actions"><button type="button" className="primary" onClick={advance}>Continue</button></div>
              </div>
            ) : (
              <form onSubmit={submitAccount} noValidate>
                {status?.googleSignIn && (
                  <>
                    <a
                      className="primary full"
                      href={status.googleSignIn}
                      onClick={() => persist({googleStarted: true, step: 'account'})}
                    >
                      Continue with Google
                    </a>
                    <p className="setup-hint">Google verifies who you are; Hoja never sees your Google password.</p>
                    <p className="setup-divider"><span>or set up a password</span></p>
                  </>
                )}
                <label htmlFor="setup-name">Your name<input id="setup-name" autoComplete="name" required value={name} onChange={(event) => { setName(event.target.value); setFieldError(null) }} placeholder="Alex Morgan" {...fieldProps("name")}/></label>
                <label htmlFor="setup-email">Work email<input id="setup-email" type="email" autoComplete="email" required value={email} onChange={(event) => { setEmail(event.target.value); setFieldError(null) }} placeholder="alex@example.com" {...fieldProps('email')}/></label>
                {status?.ownerBound && <p className="setup-hint">This workspace was provisioned for one owner. Use the address it was created for.</p>}
                <label htmlFor="setup-password">Create a password<input id="setup-password" type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(event) => { setPassword(event.target.value); setFieldError(null) }} placeholder="At least 12 characters" {...fieldProps('password')}/></label>
                <p className="setup-hint">Twelve characters or more. The browser session itself is an HttpOnly cookie.</p>
                {fieldError && <p className="setup-field-error" id={`setup-${fieldError.field}-error`} role="alert">{fieldError.message}</p>}
                <button type="submit" className="primary full" disabled={busy}>Continue</button>
                {status?.googleSignIn && <a className="auth-switch" href="/login">I already have an account</a>}
              </form>
            ))}

            {step === 'workspace' && (boot.workspaceId ? (
              <div>
                <p className="setup-created" role="status"><span aria-hidden="true">✓</span> Workspace <b>{boot.workspaceUrlKey || draft.workspaceAddress}</b> exists. Its name and address cannot be changed from this step.</p>
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="button" className="primary" onClick={advance}>Continue</button>
                </div>
              </div>
            ) : (
              <form onSubmit={submitWorkspace} noValidate>
                <label htmlFor="setup-workspace-name">Workspace name
                  <input
                    id="setup-workspace-name"
                    required
                    value={draft.workspaceName}
                    onChange={(event) => {
                      const value = event.target.value;
                      persist({
                        workspaceName: value,
                        workspaceAddress: addressOnNameChange(value, draft.workspaceAddress, addressTouched.current),
                      });
                    }}
                    placeholder="Acme Studio"
                    {...fieldProps('workspaceName')}
                  />
                </label>
                {fieldError?.field === 'workspaceName' && <p className="setup-field-error" id="setup-workspaceName-error" role="alert">{fieldError.message}</p>}

                <label htmlFor="setup-workspace-address">Workspace address
                  <span className="setup-address">
                    <input
                      id="setup-workspace-address"
                      required
                      autoCapitalize="none"
                      spellCheck={false}
                      value={draft.workspaceAddress}
                      onChange={(event) => {
                        addressTouched.current = true;
                        persist({workspaceAddress: event.target.value.toLowerCase()});
                      }}
                      placeholder="acme-studio"
                      {...fieldProps('workspaceAddress')}
                    />
                    {adapter.addressDomain && <span className="setup-address-suffix">{adapter.addressDomain}</span>}
                  </span>
                </label>
                <p className="setup-hint">Lowercase letters, numbers and hyphens. Changing the name later will not overwrite an address you edit here.</p>
                {fieldError?.field === 'workspaceAddress' && <p className="setup-field-error" id="setup-workspaceAddress-error" role="alert">{fieldError.message}</p>}

                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="submit" className="primary">Continue</button>
                </div>
              </form>
            ))}

            {step === 'team' && (boot.teamId ? (
              <div>
                <p className="setup-created" role="status"><span aria-hidden="true">✓</span> Team <b>{draft.teamName} ({normaliseTeamKey(draft.teamKey)})</b> exists.</p>
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="button" className="primary" onClick={advance}>Continue</button>
                </div>
              </div>
            ) : (
              <form onSubmit={submitTeam} noValidate>
                <label htmlFor="setup-team-name">Team name<input id="setup-team-name" required value={draft.teamName} onChange={(event) => persist({teamName: event.target.value})} placeholder="Engineering" {...fieldProps('teamName')}/></label>
                {fieldError?.field === 'teamName' && <p className="setup-field-error" id="setup-teamName-error" role="alert">{fieldError.message}</p>}
                <label htmlFor="setup-team-key">Team key<input id="setup-team-key" required maxLength={8} value={draft.teamKey} onChange={(event) => persist({teamKey: normaliseTeamKey(event.target.value)})} placeholder="ENG" {...fieldProps('teamKey')}/></label>
                <p className="setup-hint">Tasks get identifiers like <code>{preview.issueExample}</code>. A team is required because tasks and projects belong to one.</p>
                {fieldError?.field === 'teamKey' && <p className="setup-field-error" id="setup-teamKey-error" role="alert">{fieldError.message}</p>}
                {flowMode === 'first-run' && !boot.workspaceId && (
                  <p className="setup-hint">Creating the workspace also creates your admin account, this team, its default workflow states and your browser session — in one step. It cannot be half-created.</p>
                )}
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="submit" className="primary" disabled={busy}>
                    {busy ? 'Creating…' : boot.workspaceId ? 'Create team' : 'Create workspace'}
                  </button>
                </div>
              </form>
            ))}

            {step === 'project' && (
              <form onSubmit={submitProject} noValidate>
                {boot.projectId ? (
                  <p className="setup-created" role="status"><span aria-hidden="true">✓</span> Project <b>{draft.projectName}</b> exists.</p>
                ) : (
                  <>
                    <label htmlFor="setup-project-name">Project name<input id="setup-project-name" value={draft.projectName} onChange={(event) => persist({projectName: event.target.value})} placeholder="Website refresh" {...fieldProps('projectName')}/></label>
                    <p className="setup-hint">Optional. A project groups tasks across teams; you can add one any time from Projects.</p>
                    {fieldError && <p className="setup-field-error" id={`setup-${fieldError.field}-error`} role="alert">{fieldError.message}</p>}
                  </>
                )}
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  {!boot.projectId && <button type="button" className="auth-switch" onClick={skip}>Skip this step</button>}
                  <button type="submit" className="primary" disabled={busy}>{busy ? 'Creating…' : boot.projectId ? 'Continue' : 'Create project'}</button>
                </div>
              </form>
            )}

            {step === 'invitations' && (
              <div>
                <ul className="setup-results" aria-live="polite">
                  {inviteResults.map((result) => (
                    <li key={result.userId}>
                      <p className="setup-result-head">Invite link ready</p>
                      <p className="setup-hint">Hoja does not send invitation email, so share this single-use link yourself.</p>
                      <span className="setup-address">
                        <input aria-label="Invite link" readOnly value={result.link} onFocus={(event) => event.currentTarget.select()}/>
                        <button type="button" className="outline" onClick={() => void copy(result.link)}>{copied === result.link ? 'Copied' : 'Copy'}</button>
                      </span>
                    </li>
                  ))}
                </ul>
                {draft.invites.length > 0 && inviteResults.length === 0 && (
                  <p className="setup-created" role="status"><span aria-hidden="true">✓</span> {pendingCount || draft.invites.length} invitation{(pendingCount || draft.invites.length) === 1 ? '' : 's'} pending. Links are not stored in this browser — resend from Members if needed.</p>
                )}
                <form onSubmit={submitInvite} noValidate>
                  <label htmlFor="setup-invite-name">Name<input id="setup-invite-name" value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="Sam Rivera"/></label>
                  <label htmlFor="setup-invite-email">Email<input id="setup-invite-email" type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="sam@example.com" {...fieldProps('inviteEmail')}/></label>
                  {fieldError && <p className="setup-field-error" id={`setup-${fieldError.field}-error`} role="alert">{fieldError.message}</p>}
                  <p className="setup-hint">Optional. Invites are single use and expire after 72 hours.</p>
                  <div className="setup-actions">
                    <button type="button" className="outline" onClick={back}>Back</button>
                    <button type="button" className="auth-switch" onClick={skip}>Skip this step</button>
                    <button type="submit" className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add invitation'}</button>
                  </div>
                  {inviteResults.length > 0 && <button type="button" className="primary full setup-next" onClick={advance}>Continue</button>}
                </form>
              </div>
            )}

            {step === 'integrations' && (
              <div>
                <p className="setup-hint">
                  Read-only access to Gmail, Drive, Calendar and Docs. An organization connection is
                  available to everyone in this workspace; a personal connection is only yours. You can
                  disconnect it later in Settings → Connectors.
                </p>
                {connected.includes('google_workspace') ? (
                  <p className="setup-created" role="status"><span aria-hidden="true">✓</span> Google Workspace is connected.</p>
                ) : (
                  <a className="primary full" href={adapter.googleConnectUrl()} onClick={() => persist({step: 'integrations'})}>Connect Google Workspace</a>
                )}
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="button" className="auth-switch" onClick={skip}>Skip this step</button>
                  {connected.includes('google_workspace') && <button type="button" className="primary" onClick={advance}>Continue</button>}
                </div>
              </div>
            )}

            {step === 'ready' && (
              <div>
                <ul className="setup-summary">
                  <li><span>Workspace</span><b>{boot.workspaceUrlKey || normaliseAddress(draft.workspaceAddress)}</b></li>
                  <li><span>Team</span><b>{draft.teamName} ({normaliseTeamKey(draft.teamKey)})</b></li>
                  <li><span>Project</span><b>{boot.projectId ? draft.projectName : 'Skipped — add one later'}</b></li>
                  <li><span>Invitations</span><b>{inviteResults.length ? `${inviteResults.length} sent` : draft.invites.length ? `${draft.invites.length} pending` : 'Skipped — add people later'}</b></li>
                  <li><span>Google Workspace</span><b>{connected.includes('google_workspace') ? 'Connected' : connectors.googleWorkspace ? 'Skipped' : 'Not configured here'}</b></li>
                </ul>
                {apiKey && (
                  <details className="setup-key">
                    <summary>Admin API key for CLI setup</summary>
                    <code>{apiKey}</code>
                    <button type="button" className="outline" onClick={() => void copy(apiKey)}>{copied === apiKey ? 'Copied' : 'Copy API key'}</button>
                    <p className="setup-hint">Shown once. It is not saved in this browser.</p>
                  </details>
                )}
                <div className="setup-actions">
                  <button type="button" className="outline" onClick={back}>Back</button>
                  <button type="button" className="primary" onClick={finish}>Open my workspace</button>
                </div>
              </div>
            )}

            {error && (
              <div className="setup-error" role="alert">
                <b>That did not finish</b>
                <span>{error}</span>
                {/already set up/i.test(error) && <button type="button" className="outline" onClick={recover}>Reload Hoja</button>}
              </div>
            )}
          </div>
        </section>

        <aside className="setup-preview" aria-label="Workspace preview">
          <p className="setup-eyebrow">Preview</p>
          <p className="setup-preview-name">{preview.workspaceName}</p>
          <p className="setup-preview-address">{preview.address}{adapter.addressDomain}</p>
          <p className="setup-preview-live" aria-live="polite">{liveSummary}</p>
          <ul>
            {preview.rows.map((row) => (
              <li key={row.label} className={`is-${row.state}`}>
                <span>{row.label}</span>
                <b>{row.value}</b>
              </li>
            ))}
            <li><span>First task</span><b>{preview.issueExample}</b></li>
          </ul>
          <p className="setup-preview-note">This preview is built from what you type and from the server’s confirmations. It never shows a resource that has not been created.</p>
        </aside>
      </div>
    </main>
  );
}

function headingFor(step: StepId, input: {mode: FlowMode; boot: Boot; draft: SetupDraft}): string {
  switch (step) {
    case 'account': return 'Let’s make this yours.';
    case 'workspace': return input.mode === 'existing' ? 'Name your new workspace.' : 'Give your workspace a home.';
    case 'team': return input.boot.workspaceId ? 'Add your first team.' : 'Create your workspace and first team.';
    case 'project': return 'Start with a project?';
    case 'invitations': return 'Bring your teammates in.';
    case 'integrations': return 'Connect Google Workspace.';
    case 'ready': return 'You’re all set.';
  }
}

function introFor(step: StepId, input: {mode: FlowMode; boot: Boot; draft: SetupDraft; connectors: {googleWorkspace: boolean}}): string {
  switch (step) {
    case 'account': return 'You’ll be the workspace admin. This account signs you in and owns the workspace.';
    case 'workspace': return 'The name is for people; the address is what they type.';
    case 'team': return 'Teams own tasks and give them their identifier prefix.';
    case 'project': return 'Optional. Projects group tasks across teams.';
    case 'invitations': return 'Optional. Add one person at a time; skip and do it later from Members.';
    case 'integrations': return 'Optional, and only shown because this deployment is configured for it.';
    case 'ready': return 'Here is exactly what exists now.';
  }
}
