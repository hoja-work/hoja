// /signup — the user-visible half of the server-side sign-up gate.
//
// The page can show progress; it cannot cause provisioning. Everything it knows
// comes from two public endpoints: GET /api/signup/config (Turnstile site key,
// the current mode, where Google sign-in starts) and GET /api/signup (is this
// browser already google_verified). Submitting posts to the same gate the rest of
// the product uses, and every refusal is rendered from the server's reason code.
//
// Layout and control styling are colocated as Tailwind utilities below.
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { SLUG_MAX_LENGTH } from "./slugPolicy.generated";
import { checkSlug } from "./slugValidation";
import { describeReason } from "./reasonCopy";
import { submitSignup } from "./submitSignup";
import { loadTurnstile } from "./turnstile";
import { fetchWorkspaceStatus } from "./workspaceStatus";
import type { WorkspaceState } from "./workspaceStatus";
import "./signup.css";

/** What GET /api/signup/config may answer with. */
type PublicConfig = {
  mode: "closed" | "invite" | "open";
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
  workspace?: {
    slug: string;
    status: WorkspaceState;
    workspaceUrl: string | null;
    signInUrl: string | null;
    message: string;
  };
  /** Workspaces this verified identity can already open (members and invitations). */
  workspaces?: MemberWorkspace[];
};

/** One workspace the person belongs to, from GET /api/signup. */
type MemberWorkspace = {
  name: string;
  host: string;
  workspaceUrl: string;
  signInUrl: string;
  status: "active" | "invited";
  role?: string | null;
};

type Alert = { title: string; message: string };

type WorkspaceRequest = {
  slug: string;
  message: string;
  alreadyQueued: boolean;
  status: WorkspaceState;
  workspaceUrl: string | null;
  signInUrl: string | null;
};

/** Refusals that mean "the address, not the session" — refocus the field. */
const SLUG_REFUSALS = new Set([
  "slug_required",
  "invalid_slug",
  "reserved_slug",
  "slug_taken",
]);

export function SignupPage() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [slug, setSlug] = useState("");
  const [token, setToken] = useState("");
  const [challengeError, setChallengeError] = useState("");
  const [alert, setAlert] = useState<Alert | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [request, setRequest] = useState<WorkspaceRequest | null>(null);
  // An existing member sees their workspaces first; the create form is the
  // secondary action, opened on purpose.
  const [wantsCreate, setWantsCreate] = useState(false);
  const widgetRef = useRef<HTMLDivElement | null>(null);
  const widgetId = useRef<string | null>(null);
  const slugInput = useRef<HTMLInputElement | null>(null);

  // 1. Public configuration and the session's sign-in state. Both are public,
  //    read-only endpoints; neither can create anything.
  useEffect(() => {
    let live = true;
    fetch("/api/signup/config")
      .then((response) =>
        response.ok
          ? (response.json() as Promise<PublicConfig>)
          : Promise.reject(new Error(`HTTP ${response.status}`)),
      )
      .then((body) => {
        if (live) setConfig(body);
      })
      .catch(() => {
        if (live)
          setConfigError(
            "Hoja could not load the sign-up settings. Reload the page to try again.",
          );
      });
    fetch("/api/signup", { credentials: "include" })
      .then((response) =>
        response.ok
          ? (response.json() as Promise<SessionInfo>)
          : Promise.reject(new Error(`HTTP ${response.status}`)),
      )
      .then((body) => {
        if (!live) return;
        setSession(body);
        // A returning person must not be asked to name a second workspace.
        // The server attaches only this identity's own latest request.
        if (body.workspace) {
          setRequest({
            slug: body.workspace.slug,
            message: body.workspace.message,
            alreadyQueued: true,
            status: body.workspace.status,
            workspaceUrl: body.workspace.workspaceUrl,
            signInUrl: body.workspace.signInUrl,
          });
        }
      })
      .catch(() => {
        if (live)
          setConfigError(
            "Hoja could not tell whether you are signed in with Google. Reload the page.",
          );
      });
    return () => {
      live = false;
    };
  }, []);

  const closed = config?.mode === "closed";
  // The form only exists when the deployment can verify a widget AND the browser
  // has a Google session: a missing site key is a fail-closed "unavailable", never
  // a widget that produces tokens nothing can spend.
  const ready = Boolean(
    config && !closed && config.available && config.signInUrl,
  );
  const memberships = session?.googleVerified ? session.workspaces || [] : [];
  const showMemberships = memberships.length > 0 && !request;
  const showForm =
    ready &&
    Boolean(session?.googleVerified) &&
    !request &&
    (!showMemberships || wantsCreate);

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
          action: "signup",
          size: "flexible",
          theme:
            document.documentElement.dataset.theme === "dark"
              ? "dark"
              : "light",
          callback: (value: string) => {
            setToken(value);
            setChallengeError("");
          },
          "expired-callback": () => setToken(""),
          "error-callback": () => {
            setToken("");
            setChallengeError(
              "The human check could not run. Reload the page to try again.",
            );
          },
        });
        widgetId.current = id ?? null;
      })
      .catch(() => {
        if (!cancelled)
          setChallengeError(
            "The human check could not load. Reload the page to try again.",
          );
      });
    return () => {
      cancelled = true;
      const id = widgetId.current;
      widgetId.current = null;
      if (id && window.turnstile) window.turnstile.remove(id);
    };
  }, [showForm, config]);

  const resetChallenge = useCallback(() => {
    setToken("");
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
      setAlert(describeReason("turnstile_missing"));
      return;
    }
    setSubmitting(true);
    try {
      const result = await submitSignup({ slug: checked.slug, token });
      if (result.kind === "queued") {
        setRequest({
          slug: result.slug,
          message: result.message,
          alreadyQueued: result.alreadyQueued,
          status: result.status,
          workspaceUrl: null,
          signInUrl: null,
        });
        return;
      }
      if (result.kind === "unreachable") {
        setAlert({ title: "Could not reach Hoja", message: result.message });
        return;
      }
      setAlert({ title: result.title, message: result.message });
      if (result.retryChallenge) resetChallenge();
      if (result.retrySignIn) setSession({ googleVerified: false });
      if (SLUG_REFUSALS.has(result.reason)) slugInput.current?.focus();
    } finally {
      setSubmitting(false);
    }
  };

  // 4. The accepted request is asynchronous. Poll only while it can still
  //    change; a transient failure is retried, never rendered as a rejection.
  const requestSlug = request?.slug;
  const requestStatus = request?.status;
  useEffect(() => {
    if (
      !requestSlug ||
      requestStatus === "active" ||
      requestStatus === "failed"
    )
      return;
    let live = true;
    let timer: number | undefined;
    const poll = async () => {
      const result = await fetchWorkspaceStatus({ slug: requestSlug });
      if (!live) return;
      if (result.kind === "unauthenticated") {
        setSession({ googleVerified: false });
        setRequest(null);
        return;
      }
      if (result.kind === "state") {
        setRequest((previous) =>
          previous && previous.slug === result.slug
            ? {
                ...previous,
                status: result.status,
                workspaceUrl: result.workspaceUrl,
                signInUrl: result.signInUrl,
                message:
                  result.status === "eligible"
                    ? previous.message
                    : result.message,
              }
            : previous,
        );
        if (result.status === "active" || result.status === "failed") return;
      }
      timer = window.setTimeout(poll, 1500);
    };
    timer = window.setTimeout(poll, 300);
    return () => {
      live = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [requestSlug, requestStatus]);

  // 5. A ready workspace opens itself. The tenant still asks Google to sign in;
  //    the control-plane session is intentionally not a cross-origin cookie.
  useEffect(() => {
    const target = request?.signInUrl || request?.workspaceUrl;
    if (request?.status !== "active" || !target) return;
    const timer = window.setTimeout(() => {
      window.location.replace(target);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [request?.status, request?.signInUrl, request?.workspaceUrl]);

  const signOut = async () => {
    try {
      await fetch("/auth/google/logout", {
        method: "POST",
        credentials: "include",
      });
    } catch {
      /* the state below is what the page shows either way */
    }
    setSession({ googleVerified: false });
    setToken("");
  };

  // Live validation, from the generated server rule: only complain once there is
  // something to complain about, and never while the field is untouched.
  const typed = slug.trim();
  const liveCheck = typed ? checkSlug(slug) : null;
  const liveIssue =
    liveCheck && !liveCheck.ok ? describeReason(liveCheck.reason).message : "";
  const preview =
    typed && (!liveCheck || !liveCheck.ok)
      ? typed.toLowerCase()
      : "your-workspace";

  const signedIn = Boolean(session?.googleVerified);
  const heading = request
    ? request.status === "active"
      ? "Your workspace is ready"
      : request.status === "failed"
        ? "We could not finish your workspace"
        : request.status === "provisioning"
          ? "Building your workspace"
          : request.alreadyQueued
            ? "You already asked for this address"
            : "Request received"
    : showMemberships && !wantsCreate
      ? memberships.length === 1
        ? "You already have a workspace"
        : "You already have workspaces"
      : signedIn
        ? "Name your workspace"
        : "Sign up with your identity provider";
  const sub = request
    ? request.status === "active"
      ? "Opening it now. You will sign in there with the same Google account."
      : request.status === "failed"
        ? "Your request is still recorded, but setting it up did not finish."
        : request.status === "provisioning"
          ? "The database, application and address are being prepared. This page updates itself."
          : "Your request is accepted. Creation starts automatically; this page updates itself."
    : showMemberships && !wantsCreate
      ? "Signed in as " +
        (session?.email || "your Google account") +
        ". Open your workspace below."
      : signedIn
        ? "Signed in with Google. Your team will reach this workspace at " +
          preview +
          ".hoja.work."
        : "You'll use this provider to sign in to your workspace.";

  return (
    <main
      className='min-h-dvh pt-6 px-0 pb-16 bg-white text-[#242424] text-[16px] font-[Inter,-apple-system,BlinkMacSystemFont,"avenir_next",avenir,\n____helvetica,"helvetica_neue",ubuntu,roboto,noto,"segoe_ui",arial,sans-serif] tracking-[-0.0015em] leading-[1.45] text-center [-webkit-font-smoothing:antialiased] [-moz-osx-font-smoothing:grayscale]'
      data-fidelity-state="signin-page"
    >
      <div
        className="w-[94%] max-w-140 my-0 mx-auto"
        data-fidelity-state="signin-shell"
      >
        <div
          className="flex items-center justify-center gap-[6.4px] mb-12 text-[#242424] text-[18.4px] font-semibold tracking-[-0.03em]"
          data-fidelity-state="signin-brand"
        >
          <img
            className="w-[21.6px] h-[21.6px]"
            src="/hoja-clover.8371400e.svg"
            alt=""
          />
          <span>hoja</span>
        </div>

        <header className="mb-12" data-fidelity-state="signin-header">
          <h1
            className="m-0 text-[#242424] text-[20px] font-semibold tracking-[-0.03em] leading-[1.45]"
            id="signup-title"
          >
            {heading}
          </h1>
          <p
            className="mt-3 mx-auto mb-0 max-w-140 text-[#343433] text-[16px] font-normal"
            data-fidelity-state="signin-sub"
          >
            {sub}
          </p>
        </header>

        {configError && (
          <p
            className="max-w-[320px] mt-4 mx-auto mb-0 text-[#c62f2f] text-[12.8px]"
            data-fidelity-state="signin-error"
            role="alert"
          >
            {configError}
          </p>
        )}
        {!config && !configError && (
          <p
            className="my-4 mx-auto text-[#666] text-[12.8px]"
            data-fidelity-state="signin-loading"
            role="status"
          >
            Loading sign-up…
          </p>
        )}

        {showMemberships && (
          <section
            className="w-full max-w-sm mx-auto mb-8 text-left"
            data-fidelity-state="signin-memberships"
            aria-label="Your workspaces"
          >
            <ul className="grid gap-2 m-0 p-0 list-none">
              {memberships.map((entry) => (
                <li key={entry.host}>
                  <a
                    className="flex items-center justify-between gap-4 p-4 border border-solid border-neutral-800 rounded-sm bg-neutral-800 text-white no-underline hover:bg-black"
                    data-fidelity-state="signin-workspace-link"
                    href={entry.signInUrl}
                  >
                    <span className="grid gap-1 min-w-0">
                      <span className="font-semibold truncate">
                        Open {entry.name}
                      </span>
                      <span className="text-sm text-neutral-300 truncate">
                        {entry.host}
                        {entry.status === "invited" ? " · invitation" : ""}
                      </span>
                    </span>
                    <span aria-hidden="true">→</span>
                  </a>
                </li>
              ))}
            </ul>
            {ready && !wantsCreate && (
              <button
                type="button"
                className="w-full mt-4 p-4 border border-solid border-neutral-300 rounded-sm bg-white text-neutral-800 text-base cursor-pointer hover:border-neutral-500"
                data-fidelity-state="signin-create-another"
                onClick={() => setWantsCreate(true)}
              >
                Create another workspace
              </button>
            )}
          </section>
        )}

        {config && closed && !showMemberships && (
          <div
            className="w-[94%] max-w-[320px] mt-0 mx-auto mb-8 p-4 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-left"
            data-fidelity-state="signin-notice"
            role="note"
          >
            <h2 className="mt-0 mx-0 mb-2 text-[#242424] text-[17.92px] font-semibold tracking-[-0.03em]">
              Sign-ups are invite-only
            </h2>
            <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
              Every workspace gets its own database and address, so Hoja is
              opening them by invitation only. If someone invited you, open the
              link in that email.
            </p>
          </div>
        )}

        {config && !closed && !config.available && (
          <div
            className="w-[94%] max-w-[320px] mt-0 mx-auto mb-8 p-4 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-left"
            data-fidelity-state="signin-notice"
            role="note"
          >
            <h2 className="mt-0 mx-0 mb-2 text-[#242424] text-[17.92px] font-semibold tracking-[-0.03em]">
              Sign-up is unavailable
            </h2>
            <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
              This deployment cannot run the verification challenge, so no
              request can be accepted. Nothing is wrong with your browser or
              your Google account — try again later.
            </p>
          </div>
        )}

        {config &&
          !closed &&
          config.available &&
          !request &&
          (!showMemberships || wantsCreate) &&
          (session === null ? (
            <p
              className="my-4 mx-auto text-[#666] text-[12.8px]"
              data-fidelity-state="signin-loading"
              role="status"
            >
              Checking your Google sign-in…
            </p>
          ) : !session.googleVerified ? (
            <>
              {config.mode === "invite" && (
                <p
                  className="max-w-md mt-0 mx-auto mb-4 text-[#666] text-[12.8px]"
                  data-fidelity-state="signin-inline-note"
                >
                  Invite-only right now: use the Google address that was
                  invited.
                </p>
              )}
              <p
                className="max-w-md mt-2 mx-auto mb-8 text-[#666] text-[12.8px] tracking-[-0.01em] leading-[1.45]"
                data-fidelity-state="signin-legal"
              >
                By continuing you agree to the Hoja terms. We use Google to
                verify who you are and Cloudflare Turnstile to check that a
                person is asking — your Google password is never shared with us.
              </p>
              <div
                className="grid gap-2.5 w-[94%] max-w-[320px] my-0 mx-auto"
                data-fidelity-state="signin-providers"
              >
                <a
                  className="flex items-center justify-center w-full py-2 px-3 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-[#242424] font-[inherit] text-[16px] font-normal leading-[inherit] no-underline cursor-pointer appearance-none transition-[color,border-color,background-color,box-shadow] duration-200 ease-[ease] [&:hover]:border-[#a9a8a6] [&:hover]:shadow-[0_1px_2px_rgba(0,0,0,0.08)]"
                  data-fidelity-state="signin-provider"
                  href={config.signInUrl ?? undefined}
                >
                  <span className="mr-3 shrink-0">
                    <GoogleMark />
                  </span>
                  <span>Sign up with Google</span>
                </a>
                <button
                  type="button"
                  className="flex items-center justify-center w-full py-2 px-3 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-[#242424] font-[inherit] text-[16px] font-normal leading-[inherit] no-underline cursor-pointer appearance-none transition-[color,border-color,background-color,box-shadow] duration-200 ease-[ease] [&:hover]:border-[#a9a8a6] [&:hover]:shadow-[0_1px_2px_rgba(0,0,0,0.08)] data-[fidelity-state~=is-pending]:cursor-not-allowed data-[fidelity-state~=is-pending]:text-[#6b6b6b] [&[data-fidelity-state~=is-pending]:hover]:border-[#d8d6d4] [&[data-fidelity-state~=is-pending]:hover]:shadow-[0_2px_4px_rgba(0,0,0,0.05)] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="signin-provider is-pending"
                  disabled
                  aria-disabled="true"
                >
                  <span className="mr-3 shrink-0 opacity-50">
                    <MicrosoftMark />
                  </span>
                  <span>Sign up with Microsoft</span>
                  <em
                    className="ml-[8.8px] text-[#8a8a8a] text-[9.92px] not-italic font-medium tracking-[0.08em] uppercase"
                    data-fidelity-state="signin-pending"
                  >
                    Pending
                  </em>
                </button>
                <button
                  type="button"
                  className="flex items-center justify-center w-full py-2 px-3 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-[#242424] font-[inherit] text-[16px] font-normal leading-[inherit] no-underline cursor-pointer appearance-none transition-[color,border-color,background-color,box-shadow] duration-200 ease-[ease] [&:hover]:border-[#a9a8a6] [&:hover]:shadow-[0_1px_2px_rgba(0,0,0,0.08)] data-[fidelity-state~=is-pending]:cursor-not-allowed data-[fidelity-state~=is-pending]:text-[#6b6b6b] [&[data-fidelity-state~=is-pending]:hover]:border-[#d8d6d4] [&[data-fidelity-state~=is-pending]:hover]:shadow-[0_2px_4px_rgba(0,0,0,0.05)] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="signin-provider is-pending"
                  disabled
                  aria-disabled="true"
                >
                  <span className="mr-3 shrink-0 opacity-50">
                    <GitHubMark />
                  </span>
                  <span>Sign up with GitHub</span>
                  <em
                    className="ml-[8.8px] text-[#8a8a8a] text-[9.92px] not-italic font-medium tracking-[0.08em] uppercase"
                    data-fidelity-state="signin-pending"
                  >
                    Pending
                  </em>
                </button>
                <button
                  type="button"
                  className="flex items-center justify-center w-full py-2 px-3 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-[#242424] font-[inherit] text-[16px] font-normal leading-[inherit] no-underline cursor-pointer appearance-none transition-[color,border-color,background-color,box-shadow] duration-200 ease-[ease] [&:hover]:border-[#a9a8a6] [&:hover]:shadow-[0_1px_2px_rgba(0,0,0,0.08)] data-[fidelity-state~=is-pending]:cursor-not-allowed data-[fidelity-state~=is-pending]:text-[#6b6b6b] [&[data-fidelity-state~=is-pending]:hover]:border-[#d8d6d4] [&[data-fidelity-state~=is-pending]:hover]:shadow-[0_2px_4px_rgba(0,0,0,0.05)] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="signin-provider is-pending"
                  disabled
                  aria-disabled="true"
                >
                  <span className="mr-3 shrink-0 opacity-50">
                    <AppleMark />
                  </span>
                  <span>Sign up with Apple</span>
                  <em
                    className="ml-[8.8px] text-[#8a8a8a] text-[9.92px] not-italic font-medium tracking-[0.08em] uppercase"
                    data-fidelity-state="signin-pending"
                  >
                    Pending
                  </em>
                </button>
                <button
                  type="button"
                  className="flex items-center justify-center w-full py-2 px-3 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-[#242424] font-[inherit] text-[16px] font-normal leading-[inherit] no-underline cursor-pointer appearance-none transition-[color,border-color,background-color,box-shadow] duration-200 ease-[ease] [&:hover]:border-[#a9a8a6] [&:hover]:shadow-[0_1px_2px_rgba(0,0,0,0.08)] data-[fidelity-state~=is-pending]:cursor-not-allowed data-[fidelity-state~=is-pending]:text-[#6b6b6b] [&[data-fidelity-state~=is-pending]:hover]:border-[#d8d6d4] [&[data-fidelity-state~=is-pending]:hover]:shadow-[0_2px_4px_rgba(0,0,0,0.05)] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="signin-provider is-pending"
                  disabled
                  aria-disabled="true"
                >
                  <span className="mr-3 shrink-0 opacity-50">
                    <OidcMark />
                  </span>
                  <span>Sign up with OIDC</span>
                  <em
                    className="ml-[8.8px] text-[#8a8a8a] text-[9.92px] not-italic font-medium tracking-[0.08em] uppercase"
                    data-fidelity-state="signin-pending"
                  >
                    Pending
                  </em>
                </button>
              </div>
            </>
          ) : (
            <form
              className="w-[94%] max-w-[320px] my-0 mx-auto text-left"
              data-fidelity-state="signin-form"
              onSubmit={onSubmit}
              noValidate
            >
              <p
                className="mt-0 mx-0 mb-4 text-[#666] text-[12.8px] tracking-[-0.01em] text-center"
                data-fidelity-state="signin-identity"
              >
                Signed in as{" "}
                <b className="text-[#242424] font-semibold">
                  {session.email || "your Google account"}
                </b>{" "}
                ·{" "}
                <button
                  type="button"
                  className="p-0 border-0 [background:none] text-[#666] font-[inherit] text-[12.8px] underline underline-offset-[0.1em] cursor-pointer [&:hover]:text-[#242424] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="signin-link"
                  onClick={signOut}
                >
                  use a different account
                </button>
              </p>
              {config.mode === "invite" && (
                <p
                  className="max-w-md mt-0 mx-auto mb-4 text-[#666] text-[12.8px]"
                  data-fidelity-state="signin-inline-note"
                >
                  Invite-only: this address needs an open invitation before the
                  request is queued.
                </p>
              )}

              <label
                className="block mb-[5.6px] text-[#242424] text-[12.8px] font-semibold"
                htmlFor="signup-slug"
              >
                Workspace address
              </label>
              <div
                className="flex items-center border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] focus-within:border-[#a9a8a6]"
                data-fidelity-state="signin-field"
              >
                <input
                  className="flex-1 min-w-0 pt-2 pr-0 pb-2 pl-3 border-0 [outline:0] bg-transparent text-[#242424] font-[inherit] text-[16px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                  id="signup-slug"
                  ref={slugInput}
                  name="slug"
                  value={slug}
                  onChange={(event) => {
                    setSlug(event.target.value);
                    setAlert(null);
                  }}
                  placeholder="acme"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={SLUG_MAX_LENGTH + 16}
                  aria-invalid={liveIssue ? true : undefined}
                  aria-describedby={
                    liveIssue
                      ? "signup-slug-help signup-slug-error"
                      : "signup-slug-help"
                  }
                />
                <span
                  className="flex-none pr-3 text-[#8a8a8a] text-[14px]"
                  data-fidelity-state="signin-suffix"
                >
                  .hoja.work
                </span>
              </div>
              <p
                className="mt-[6.4px] mx-0 mb-0 text-[#666] text-[12px] tracking-[-0.01em]"
                data-fidelity-state="signin-hint"
                id="signup-slug-help"
              >
                Lowercase letters, digits and single hyphens, up to{" "}
                {SLUG_MAX_LENGTH} characters. Names such as{" "}
                <code className="text-[11.52px]">www</code> are reserved.
              </p>
              <div
                aria-live="polite"
                className="min-h-[16.8px] mt-[3.2px] mx-0 mb-0 text-[#c62f2f] text-[12px]"
                data-fidelity-state="signin-hint-error"
                id="signup-slug-error"
              >
                {liveIssue || null}
              </div>

              <div
                className="flex justify-center mt-3.5 mx-0 mb-1"
                data-fidelity-state="signin-challenge"
                ref={widgetRef}
              />
              {challengeError && (
                <p
                  className="max-w-[320px] mt-4 mx-auto mb-0 text-[#c62f2f] text-[12.8px]"
                  data-fidelity-state="signin-error"
                  role="alert"
                >
                  {challengeError}
                </p>
              )}

              <div
                className="grid gap-[2.4px] mt-[9.6px] mx-0 mb-0 text-[#c62f2f] text-[12.8px] leading-[1.45]"
                data-fidelity-state="signin-alert"
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
              >
                {alert && (
                  <>
                    <strong className="font-semibold">{alert.title}</strong>
                    <span>{alert.message}</span>
                  </>
                )}
              </div>

              <button
                type="submit"
                className="w-full mt-3.5 py-2 px-3 border border-solid border-[#242424] rounded-sm bg-[#242424] text-white font-[inherit] text-[16px] font-medium cursor-pointer [transition:background-color_200ms_ease,border-color_200ms_ease] [&:hover:not(:disabled)]:bg-[#343433] [&:hover:not(:disabled)]:border-[#343433] disabled:opacity-70 disabled:cursor-progress shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="signin-submit"
                disabled={submitting}
              >
                {submitting ? "Sending your request…" : "Create workspace"}
              </button>
            </form>
          ))}

        {request && (
          <div
            className="w-[94%] max-w-[320px] mt-0 mx-auto mb-8 p-4 border border-solid border-[#d8d6d4] rounded-sm bg-white shadow-[0_2px_4px_rgba(0,0,0,0.05)] text-left"
            data-fidelity-state="signin-notice"
            role="status"
            aria-live="polite"
          >
            <h2 className="mt-0 mx-0 mb-2 text-[#242424] text-[17.92px] font-semibold tracking-[-0.03em]">
              <code className="text-[16.8px]">{request.slug}.hoja.work</code>
            </h2>
            <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
              {request.message}
            </p>
            {request.status === "active" &&
            (request.signInUrl || request.workspaceUrl) ? (
              <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
                <a
                  className="inline-flex items-center min-h-9 py-0 px-3 border border-solid border-[#242424] rounded-sm bg-[#242424] text-white text-[12.8px] font-medium no-underline [&:hover]:bg-black"
                  data-fidelity-state="signin-workspace-link"
                  href={request.signInUrl || request.workspaceUrl || undefined}
                >
                  Open {request.slug}.hoja.work
                </a>
              </p>
            ) : request.status === "failed" ? (
              <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
                Contact Hoja support and quote this workspace address so it can
                be retried.
              </p>
            ) : (
              <p className="mt-0 mx-0 mb-2 text-[#666] text-[12.8px] leading-[1.45] last:mb-0">
                You can leave this page open; it will update when the workspace
                is ready.
              </p>
            )}
          </div>
        )}
      </div>
    </main>
  );
}

function GoogleMark() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}

function MicrosoftMark() {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 17 17"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="1" y="1" width="7" height="7" fill="#f25022" />
      <rect x="9" y="1" width="7" height="7" fill="#7fba00" />
      <rect x="1" y="9" width="7" height="7" fill="#00a4ef" />
      <rect x="9" y="9" width="7" height="7" fill="#ffb900" />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
      />
    </svg>
  );
}

function AppleMark() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z"
      />
    </svg>
  );
}

function OidcMark() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M14 6a5 5 0 1 0-4.9 6.5L11 14v2h2v2h2v2h4v-3.2l-4.1-4.1A5 5 0 0 0 14 6zm-6 4a2 2 0 1 1 0-4 2 2 0 0 1 0 4z"
      />
    </svg>
  );
}
