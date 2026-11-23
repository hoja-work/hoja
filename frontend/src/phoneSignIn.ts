/**
 * "Sign in on your phone" (HOJA-733): the computer side of the QR handoff, as a state machine
 * with no React in it. `PhoneSignIn.tsx` renders it; `scripts/test-phone-signin.mjs` drives it
 * with a fake `fetch` and fake timers. The wire contract is `docs/mobile-signin.md` section 1.
 *
 *   const c = new PhoneSignInController();
 *   c.subscribe(() => render(c.state));
 *   c.start();          // POST /auth/handoff, then poll and rotate
 *   c.approve();        // once the state is "claimed"
 *   c.close();          // cancels a pending or claimed handoff (best effort)
 */

export type HandoffDevice = {
  name: string;
  platform: string;
  ip?: string | null;
  userAgent?: string | null;
};

type Handoff = {
  id: string;
  code: string;
  qrUrl: string;
  expiresAt: string;
  rotateAfter: number;
};

export type PhoneSignInState =
  | { kind: "loading" }
  | ({ kind: "pending" } & Handoff)
  | ({
      kind: "claimed";
      device: HandoffDevice;
      busy: boolean;
      notice?: string;
    } & Handoff)
  | { kind: "approved"; device: HandoffDevice | null }
  | { kind: "consumed"; device: HandoffDevice | null }
  | { kind: "burned" }
  | { kind: "expired" }
  | { kind: "error"; reason: string; message: string }
  | { kind: "closed" };

export type Timers = {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
};

export type ControllerOptions = {
  fetch?: typeof fetch;
  timers?: Timers;
  pollMs?: number;
  successCloseMs?: number;
};

const MESSAGES: Record<string, string> = {
  signin_method_disabled:
    "Your workspace does not allow signing in with a QR code. An admin can turn it on in security settings.",
  ip_not_allowed:
    "Your workspace does not allow sign-in from the network that phone is on.",
  rate_limited:
    "Too many codes in a short time. Wait a minute, then try again.",
  browser_session_required:
    "Open Hoja in a signed-in browser to show a code. API keys and app tokens cannot do this.",
};

export function messageFor(reason: string, fallback?: string): string {
  return (
    MESSAGES[reason] ||
    fallback ||
    "Could not reach Hoja. Check your connection, then try again."
  );
}

/** `BCDFGHJK` → `BCDF-GHJK`; anything else is shown as given. */
export function formatCode(code: string): string {
  const c = code.replace(/[^A-Za-z]/g, "").toUpperCase();
  return c.length === 8 ? `${c.slice(0, 4)}-${c.slice(4)}` : code;
}

/** A readable browser/OS name from a user agent, for the approval card. */
export function shortUserAgent(ua?: string | null): string {
  if (!ua) return "";
  const app = /Hoja\/[\d.]+/.exec(ua)?.[0];
  const os =
    /Android [\d.]+/.exec(ua)?.[0] ||
    /(?:iPhone|CPU) OS ([\d_]+)/
      .exec(ua)?.[1]
      ?.replace(/_/g, ".")
      .replace(/^/, "iOS ") ||
    (/iPhone|iPad/.test(ua) ? "iOS" : "");
  const parts = [app, os].filter(Boolean);
  if (parts.length) return parts.join(" · ");
  return ua.length > 60 ? `${ua.slice(0, 57)}…` : ua;
}

class HttpError extends Error {
  status: number;
  reason: string;
  constructor(status: number, reason: string, message: string) {
    super(message);
    this.status = status;
    this.reason = reason;
  }
}

const defaultTimers: Timers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (h) => globalThis.clearTimeout(h as number),
};

export class PhoneSignInController {
  state: PhoneSignInState = { kind: "loading" };
  private listeners = new Set<() => void>();
  private fetchImpl: typeof fetch;
  private timers: Timers;
  private pollMs: number;
  private successCloseMs: number;
  private handoff: Handoff | null = null;
  private device: HandoffDevice | null = null;
  private pollTimer: unknown = null;
  private rotateTimer: unknown = null;
  private closeTimer: unknown = null;
  private done = false;
  /** Bumped whenever the handoff in play changes, so late responses for an old one are ignored. */
  private generation = 0;

  constructor(options: ControllerOptions = {}) {
    this.fetchImpl = options.fetch || ((...a) => globalThis.fetch(...a));
    this.timers = options.timers || defaultTimers;
    this.pollMs = options.pollMs ?? 1500;
    this.successCloseMs = options.successCloseMs ?? 2000;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private set(state: PhoneSignInState) {
    this.state = state;
    this.listeners.forEach((l) => l());
  }

  private async request(
    path: string,
    body?: unknown,
  ): Promise<Record<string, unknown>> {
    const response = await this.fetchImpl(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers:
        body === undefined
          ? { accept: "application/json" }
          : { "content-type": "application/json", accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data: Record<string, unknown> = {};
    try {
      data = (await response.json()) || {};
    } catch {
      /* an empty or non-JSON body is handled by the status check below */
    }
    if (!response.ok) {
      const reason =
        typeof data.reason === "string"
          ? data.reason
          : `http_${response.status}`;
      const message = typeof data.error === "string" ? data.error : "";
      throw new HttpError(
        response.status,
        reason,
        messageFor(reason, message || undefined),
      );
    }
    return data;
  }

  private stopTimers() {
    for (const t of [this.pollTimer, this.rotateTimer, this.closeTimer])
      if (t !== null) this.timers.clearTimeout(t);
    this.pollTimer = this.rotateTimer = this.closeTimer = null;
  }

  /** Create a handoff (or a replacement for the current pending one) and start polling. */
  async start(replaces?: string): Promise<void> {
    if (this.done) return;
    const generation = ++this.generation;
    const previous = this.handoff;
    this.stopTimers();
    if (!replaces) {
      this.handoff = null;
      this.device = null;
      this.set({ kind: "loading" });
    }
    let created: Handoff;
    try {
      const d = await this.request(
        "/auth/handoff",
        replaces ? { replaces } : {},
      );
      created = {
        id: String(d.id),
        code: String(d.code || ""),
        qrUrl: String(d.qrUrl || ""),
        expiresAt: String(d.expiresAt || ""),
        rotateAfter: Number(d.rotateAfter) > 0 ? Number(d.rotateAfter) : 30,
      };
    } catch (e) {
      if (this.done || generation !== this.generation) return;
      const err = e as HttpError;
      this.set({
        kind: "error",
        reason: err.reason || "network",
        message: err.reason ? err.message : messageFor("network"),
      });
      return;
    }
    if (this.done) {
      // Closed while the request was in flight: do not leave a live code behind.
      void this.decide(created.id, "cancel").catch(() => undefined);
      return;
    }
    if (generation !== this.generation) {
      if (replaces)
        void this.decide(created.id, "cancel").catch(() => undefined);
      return;
    }
    if (replaces && previous) {
      // The server only cancels a replaced code while it is still pending. If a phone claimed
      // it just before the rotation, keep that one (and drop the new code) so the claim is not
      // left waiting on a code nobody is watching.
      const old = await this.request(
        `/auth/handoff/${encodeURIComponent(replaces)}`,
      ).catch(() => null);
      if (
        this.done ||
        generation !== this.generation ||
        old?.status === "claimed"
      ) {
        void this.decide(created.id, "cancel").catch(() => undefined);
        if (this.done || generation !== this.generation) return;
        if (old?.device && typeof old.device === "object")
          this.device = old.device as HandoffDevice;
        this.apply("claimed");
        return;
      }
    }
    this.handoff = created;
    this.set({ kind: "pending", ...created });
    this.schedulePoll();
    this.scheduleRotate();
  }

  /** "Show a new code" after expired, burned or an error. */
  retry(): Promise<void> {
    return this.start();
  }

  private scheduleRotate() {
    const h = this.handoff;
    if (!h) return;
    this.rotateTimer = this.timers.setTimeout(() => {
      this.rotateTimer = null;
      if (
        !this.done &&
        this.state.kind === "pending" &&
        this.handoff?.id === h.id
      )
        void this.start(h.id);
    }, h.rotateAfter * 1000);
  }

  private schedulePoll() {
    this.pollTimer = this.timers.setTimeout(() => {
      this.pollTimer = null;
      void this.poll();
    }, this.pollMs);
  }

  /** One poll of `GET /auth/handoff/<id>`; schedules the next one while the handoff is live. */
  async poll(): Promise<void> {
    const h = this.handoff;
    if (this.done || !h) return;
    const generation = this.generation;
    let d: Record<string, unknown>;
    try {
      d = await this.request(`/auth/handoff/${encodeURIComponent(h.id)}`);
    } catch (e) {
      if (this.done || generation !== this.generation) return;
      if ((e as HttpError).status === 404) {
        this.finish({ kind: "expired" });
        return;
      }
      this.schedulePoll(); // a dropped request is not a state change; try again
      return;
    }
    if (this.done || generation !== this.generation) return;
    if (d.device && typeof d.device === "object")
      this.device = d.device as HandoffDevice;
    this.apply(String(d.status || ""));
  }

  private apply(status: string) {
    const h = this.handoff;
    if (!h) return;
    switch (status) {
      case "pending":
        if (this.state.kind !== "pending") this.set({ kind: "pending", ...h });
        this.schedulePoll();
        return;
      case "claimed": {
        if (this.rotateTimer !== null) {
          this.timers.clearTimeout(this.rotateTimer);
          this.rotateTimer = null;
        }
        const prev = this.state.kind === "claimed" ? this.state : null;
        this.set({
          kind: "claimed",
          ...h,
          device: this.device || { name: "A phone", platform: "" },
          busy: prev?.busy ?? false,
          notice: prev?.notice,
        });
        this.schedulePoll();
        return;
      }
      case "approved":
        if (this.state.kind !== "approved")
          this.set({ kind: "approved", device: this.device });
        this.schedulePoll();
        return;
      case "consumed":
        this.finish({ kind: "consumed", device: this.device });
        this.closeTimer = this.timers.setTimeout(() => {
          this.closeTimer = null;
          this.set({ kind: "closed" });
        }, this.successCloseMs);
        return;
      case "denied":
      case "cancelled":
        this.finish({ kind: "closed" });
        return;
      case "burned":
        this.finish({ kind: "burned" });
        return;
      case "expired":
        this.finish({ kind: "expired" });
        return;
      default:
        this.schedulePoll();
    }
  }

  /** Stop the live handoff: no more polls or rotations. */
  private finish(state: PhoneSignInState) {
    this.stopTimers();
    this.handoff = null;
    this.generation++;
    this.set(state);
  }

  private decide(id: string, decision: "approve" | "deny" | "cancel") {
    return this.request(`/auth/handoff/${encodeURIComponent(id)}/decision`, {
      decision,
    });
  }

  async approve(): Promise<void> {
    await this.decideFromClaimed("approve");
  }

  async deny(): Promise<void> {
    await this.decideFromClaimed("deny");
  }

  private async decideFromClaimed(decision: "approve" | "deny") {
    const s = this.state;
    if (s.kind !== "claimed" || s.busy) return;
    this.set({ ...s, busy: true, notice: undefined });
    try {
      const d = await this.decide(s.id, decision);
      if (this.done || this.handoff?.id !== s.id) return;
      this.apply(
        String(d.status || (decision === "approve" ? "approved" : "denied")),
      );
    } catch (e) {
      if (this.done || this.handoff?.id !== s.id) return;
      const err = e as HttpError;
      if (err.reason === "wrong_state") {
        // The phone moved on (burned, expired…); the next poll shows where it is.
        if (this.state.kind === "claimed")
          this.set({ ...this.state, busy: false });
        return;
      }
      const message = err.reason ? err.message : messageFor("network");
      if (this.state.kind === "claimed")
        this.set({ ...this.state, busy: false, notice: message });
    }
  }

  /** The dialog is closing. Cancels a pending or claimed handoff; the request is best effort. */
  close(): void {
    if (this.done) return;
    const live =
      this.state.kind === "pending" || this.state.kind === "claimed"
        ? this.handoff
        : null;
    this.done = true;
    this.stopTimers();
    this.handoff = null;
    this.generation++;
    if (live) void this.decide(live.id, "cancel").catch(() => undefined);
    this.state = { kind: "closed" };
    this.listeners.clear();
  }
}
