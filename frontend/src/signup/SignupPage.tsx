// /signup — the user-visible half of the server-side sign-up gate.
//
// The page can show progress; it cannot cause provisioning. Everything it knows
// comes from two public endpoints: GET /api/signup/config (Turnstile site key,
// the current mode, where Google sign-in starts) and GET /api/signup (is this
// browser already google_verified). Submitting posts to the same gate the rest of
// the product uses, and every refusal is rendered from the server's reason code.
//
// Layout is the existing auth layout, not a new one: .onboarding-page /
// .onboarding-card (onboarding.css) hold the card, .primary / .error / .auth-switch
// (styles.css) style the controls. signup.css adds only the gate's own pieces.
import {useCallback, useEffect, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import {SLUG_MAX_LENGTH} from './slugPolicy.generated';
import {checkSlug} from './slugValidation';
import {describeReason} from './reasonCopy';
import {submitSignup} from './submitSignup';
import {loadTurnstile} from './turnstile';
import './signup.css';

/** What GET /api/signup/config may answer with. */
type PublicConfig = {
  mode: 'closed' | 'invite' | 'open';
  available: boolean;
  sitekey: string | null;
  signInUrl: string | null;
  widgetScriptUrl: string;
};

/** What GET /api/signup reports about this browser. */
type SessionInfo = {
  state?: string;
  googleVerified?: boolean;
  email?: string | null;
};

type Alert = {title: string; message: string};

/** Refusals that mean "the address, not the session" — refocus the field. */
const SLUG_REFUSALS = new Set(['slug_required', 'invalid_slug', 'reserved_slug', 'slug_taken']);

export function SignupPage() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState('');
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [slug, setSlug] = useState('');
  const [token, setToken] = useState('');
  const [challengeError, setChallengeError] = useState('');
  const [alert, setAlert] = useState<Alert | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [queued, setQueued] = useState<{slug: string; message: string; alreadyQueued: boolean} | null>(null);
  const widgetRef = useRef<HTMLDivElement | null>(null);
  const widgetId = useRef<string | null>(null);
  const slugInput = useRef<HTMLInputElement | null>(null);

  // 1. Public configuration and the session's sign-in state. Both are public,
  //    read-only endpoints; neither can create anything.
  useEffect(() => {
    let live = true;
    fetch('/api/signup/config')
      .then(response => (response.ok ? response.json() as Promise<PublicConfig> : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((body) => { if (live) setConfig(body) })
      .catch(() => { if (live) setConfigError('Hoja could not load the sign-up settings. Reload the page to try again.') });
    fetch('/api/signup', {credentials: 'include'})
      .then(response => (response.ok ? response.json() as Promise<SessionInfo> : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((body) => { if (live) setSession(body) })
      .catch(() => { if (live) setConfigError('Hoja could not tell whether you are signed in with Google. Reload the page.') });
    return () => { live = false };
  }, []);

  const closed = config?.mode === 'closed';
  // The form only exists when the deployment can verify a widget AND the browser
  // has a Google session: a missing site key is a fail-closed "unavailable", never
  // a widget that produces tokens nothing can spend.
  const ready = Boolean(config && !closed && config.available && config.signInUrl);
  const showForm = ready && Boolean(session?.googleVerified) && !queued;

  // 2. The widget. Explicit render into this component's node, one callback that
  //    stores the token; the effect removes it on unmount (or on success).
  useEffect(() => {
    const sitekey = config?.sitekey;
    const container = widgetRef.current;
    if (!showForm || !sitekey || !container) return;
    let cancelled = false;
    loadTurnstile(config?.widgetScriptUrl)
      .then((api) => {
        if (cancelled) return;
        const id = api.render(container, {
          sitekey,
          action: 'signup',
          size: 'flexible',
          theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
          callback: (value: string) => { setToken(value); setChallengeError('') },
          'expired-callback': () => setToken(''),
          'error-callback': () => {
            setToken('');
            setChallengeError('The human check could not run. Reload the page to try again.');
          },
        });
        widgetId.current = id ?? null;
      })
      .catch(() => {
        if (!cancelled) setChallengeError('The human check could not load. Reload the page to try again.');
      });
    return () => {
      cancelled = true;
      const id = widgetId.current;
      widgetId.current = null;
      if (id && window.turnstile) window.turnstile.remove(id);
    };
  }, [showForm, config]);

  const resetChallenge = useCallback(() => {
    setToken('');
    const id = widgetId.current;
    if (id && window.turnstile) window.turnstile.reset(id);
  }, []);

  // 3. Submit. The gate decides; this only sends and reports.
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setAlert(null);
    const checked = checkSlug(slug);
    if (!checked.ok) {
      setAlert(describeReason(checked.reason));
      slugInput.current?.focus();
      return;
    }
    if (!token) {
      setAlert(describeReason('turnstile_missing'));
      return;
    }
    setSubmitting(true);
    try {
      const result = await submitSignup({slug: checked.slug, token});
      if (result.kind === 'queued') {
        setQueued({slug: result.slug, message: result.message, alreadyQueued: result.alreadyQueued});
        return;
      }
      if (result.kind === 'unreachable') {
        setAlert({title: 'Could not reach Hoja', message: result.message});
        return;
      }
      setAlert({title: result.title, message: result.message});
      if (result.retryChallenge) resetChallenge();
      if (result.retrySignIn) setSession({googleVerified: false});
      if (SLUG_REFUSALS.has(result.reason)) slugInput.current?.focus();
    } finally {
      setSubmitting(false);
    }
  };

  const signOut = async () => {
    try {
      await fetch('/auth/google/logout', {method: 'POST', credentials: 'include'});
    } catch {
      /* the state below is what the page shows either way */
    }
    setSession({googleVerified: false});
    setToken('');
  };

  // Live validation, from the generated server rule: only complain once there is
  // something to complain about, and never while the field is untouched.
  const typed = slug.trim();
  const liveCheck = typed ? checkSlug(slug) : null;
  const liveIssue = liveCheck && !liveCheck.ok ? describeReason(liveCheck.reason).message : '';
  const preview = typed && (!liveCheck || !liveCheck.ok) ? typed.toLowerCase() : 'your-workspace';

  return (
    <main className="onboarding-page signup-page">
      <section className="onboarding-card signup-card" aria-labelledby="signup-title">
        <div className="onboarding-brand">
          <img className="brand-mark" src="/hoja-clover.8371400e.svg" alt=""/>
          <span>Hoja</span>
        </div>
        <p className="onboarding-eyebrow">Sign up</p>
        <h1 id="signup-title">
          {queued
            ? (queued.alreadyQueued ? 'You already asked for this address' : 'Request received')
            : 'Create your Hoja workspace'}
        </h1>

        {!queued && (
          <p>
            Sign in with Google, then choose the address your workspace will live at — your
            team reaches it at <code>{preview}.hoja.work</code>. We also check that a person,
            not a script, is asking before the request is queued.
          </p>
        )}

        {queued && (
          <div className="signup-notice" role="status">
            <h2><code>{queued.slug}.hoja.work</code></h2>
            <p>{queued.message}</p>
            <p>
              Creating it is an operator step, so nothing else is needed from you right now.
            </p>
          </div>
        )}

        {configError && <p className="error" role="alert">{configError}</p>}
        {!config && !configError && <p className="signup-loading" role="status">Loading sign-up…</p>}

        {config && closed && (
          <div className="signup-notice" role="note">
            <h2>Sign-ups are invite-only</h2>
            <p>
              Every workspace gets its own database and address, so Hoja is opening them by
              invitation only. If someone invited you, open the link in that email.
            </p>
          </div>
        )}

        {config && !closed && !config.available && (
          <div className="signup-notice" role="note">
            <h2>Sign-up is unavailable</h2>
            <p>
              This deployment cannot run the verification challenge, so no request can be
              accepted. Nothing is wrong with your browser or your Google account — try again
              later.
            </p>
          </div>
        )}

        {config && !closed && config.available && !queued && (
          session === null
            ? <p className="signup-loading" role="status">Checking your Google sign-in…</p>
            : !session.googleVerified
              ? (
                <>
                  {config.mode === 'invite' && (
                    <p className="signup-note">
                      Hoja is invite-only right now: sign in with the Google address that was
                      invited.
                    </p>
                  )}
                  <a className="primary full signup-google" href={config.signInUrl ?? undefined}>
                    Continue with Google
                  </a>
                </>
              )
              : (
                <form className="signup-form" onSubmit={onSubmit} noValidate>
                  <p className="signup-identity">
                    Signed in as <b>{session.email || 'your Google account'}</b>.{' '}
                    <button type="button" className="signup-link" onClick={signOut}>
                      Use a different account
                    </button>
                  </p>
                  {config.mode === 'invite' && (
                    <p className="signup-note">
                      Invite-only: this address needs an open invitation before the request is
                      queued.
                    </p>
                  )}

                  <label htmlFor="signup-slug">Workspace address</label>
                  <input
                    id="signup-slug"
                    ref={slugInput}
                    name="slug"
                    value={slug}
                    onChange={(event) => { setSlug(event.target.value); setAlert(null) }}
                    placeholder="acme"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    maxLength={SLUG_MAX_LENGTH + 16}
                    aria-invalid={liveIssue ? true : undefined}
                    aria-describedby={liveIssue ? 'signup-slug-help signup-slug-error' : 'signup-slug-help'}
                  />
                  <p className="signup-hint" id="signup-slug-help">
                    Lowercase letters, digits and single hyphens, up to {SLUG_MAX_LENGTH}{' '}
                    characters. Names such as <code>www</code> are reserved.
                  </p>
                  <div aria-live="polite" className="signup-hint-error" id="signup-slug-error">
                    {liveIssue || null}
                  </div>

                  <div className="signup-challenge" ref={widgetRef}/>
                  {challengeError && <p className="error" role="alert">{challengeError}</p>}

                  <div className="signup-alert" role="alert" aria-live="assertive" aria-atomic="true">
                    {alert && (
                      <>
                        <strong>{alert.title}</strong>
                        <span>{alert.message}</span>
                      </>
                    )}
                  </div>

                  <button type="submit" className="primary full" disabled={submitting}>
                    {submitting ? 'Sending your request…' : 'Request workspace'}
                  </button>
                </form>
              )
        )}

        {!queued && (
          <p className="login-help">
            Turnstile verifies a human; the token is checked on the server, never here.
          </p>
        )}
      </section>
    </main>
  );
}
