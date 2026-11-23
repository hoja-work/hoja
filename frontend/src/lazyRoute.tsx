import * as React from "react";

/* Route-level code splitting (HOJA-747).
 *
 * The web app used to ship as one ~2.1 MB entry chunk, so every first paint parsed and ran
 * the code for every page — agents, settings, cycles, the setup wizard, profiles — before
 * the page the person asked for could render. `lazyRoute` moves a page into its own chunk
 * while keeping its call sites untouched: it returns a component with the same props that
 * renders a skeleton while the chunk downloads.
 *
 * A tab that outlived a deploy asks for chunk hashes the server no longer has. That
 * failure is recovered with one reload (capped by a session-storage guard so a genuinely
 * broken build cannot loop); a second failure inside the guard window renders an inline
 * error with a Reload button instead of a blank page. The guard key is shared with
 * Vite's `vite:preloadError` recovery so the two never reload twice for one failure.
 *
 * Every registered page is also fetched once the browser is idle after the first paint,
 * so navigating to it later does not wait on the network.
 */

const RELOAD_KEY = "hoja:chunk-reload-at";
const GUARD_MS = 60_000;

function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS|ChunkLoadError/i.test(
    message,
  );
}

/** Reload once per guard window. Returns false when a reload already happened recently. */
function reloadForCurrentBuild(): boolean {
  const now = Date.now();
  try {
    const last = Number(window.sessionStorage.getItem(RELOAD_KEY) ?? 0);
    if (Number.isFinite(last) && last > 0 && now - last < GUARD_MS) return false;
    window.sessionStorage.setItem(RELOAD_KEY, String(now));
  } catch {
    // Storage disabled: still try one reload; the guard is then per page load.
  }
  window.location.reload();
  return true;
}

const pending = new Promise<never>(() => {});

type AnyProps = Record<string, unknown>;
type Loader<P> = () => Promise<React.ComponentType<P> | undefined>;

const registered: Array<() => Promise<unknown>> = [];

/** Load a chunk, recovering a stale tab with a single guarded reload. */
function recoverable<P>(
  load: Loader<P>,
  onLoaded: (component: React.ComponentType<P>) => void,
): () => Promise<{ default: React.ComponentType<P> }> {
  let inflight: Promise<{ default: React.ComponentType<P> }> | null = null;
  return () =>
    (inflight ??= load().then(
      (component) => {
        // A `vite:preloadError` listener that already called preventDefault() resolves the
        // import with nothing while the page reloads: wait for the reload.
        if (!component) return pending;
        onLoaded(component);
        return { default: component };
      },
      (error: unknown) => {
        inflight = null;
        if (isChunkLoadError(error) && reloadForCurrentBuild()) return pending;
        throw error;
      },
    ));
}

class ChunkBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    // Only a failed download is ours to explain; anything else keeps propagating.
    if (!isChunkLoadError(error)) throw error;
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 p-6 text-sm text-muted-foreground"
      >
        <p className="m-0">
          This page could not be loaded. The app may have been updated since this tab
          was opened.
        </p>
        <button
          type="button"
          className="rounded-md border border-line px-3 py-1.5 text-foreground hover:bg-subtle"
          onClick={() => window.location.reload()}
        >
          Reload
        </button>
      </div>
    );
  }
}

/** Skeleton shown while a page chunk downloads. `data-route-loading` lets probes wait it out. */
export function RouteLoading() {
  return (
    <div
      role="status"
      aria-label="Loading"
      data-route-loading=""
      className="flex flex-col gap-3 p-6"
    >
      {[40, 70, 55, 65].map((w) => (
        <div
          key={w}
          className="h-3 animate-pulse rounded bg-subtle motion-reduce:animate-none"
          style={{ width: `${w}%` }}
        />
      ))}
    </div>
  );
}

/**
 * Wrap a page module in its own chunk. `load` resolves the named export, e.g.
 * `lazyRoute(() => import("./profiles/LeaderboardPage").then((m) => m.LeaderboardPage))`.
 * `fallback: null` suits dialogs and inline panels that should simply appear when ready.
 */
export function lazyRoute<P extends object = AnyProps>(
  load: Loader<P>,
  options: { fallback?: React.ReactNode } = {},
): React.ComponentType<P> & { preload: () => Promise<unknown> } {
  let loaded: React.ComponentType<P> | null = null;
  const loader = recoverable(load, (component) => {
    loaded = component;
  });
  const Lazy = React.lazy(loader);
  const fallback = options.fallback === undefined ? <RouteLoading /> : options.fallback;
  function LazyRoute(props: P) {
    // Once the chunk is in (warmed or visited before), render it directly so navigating
    // there never flashes the skeleton. Chosen once per mount: switching the element type
    // later would remount the page.
    const [Ready] = React.useState(() => loaded);
    if (Ready) return <Ready {...(props as P & React.JSX.IntrinsicAttributes)} />;
    return (
      <ChunkBoundary>
        <React.Suspense fallback={fallback}>
          <Lazy {...(props as P & React.JSX.IntrinsicAttributes)} />
        </React.Suspense>
      </ChunkBoundary>
    );
  }
  const preload = () => loader().catch(() => undefined);
  registered.push(preload);
  return Object.assign(LazyRoute, { preload });
}

/* Warm the page chunks after the first paint, one at a time on idle callbacks, so a later
 * navigation does not wait on the network and the warm-up never competes with the data the
 * current page is fetching. */
function warmRegisteredRoutes() {
  if (typeof window === "undefined") return;
  const idle: (cb: () => void) => void =
    "requestIdleCallback" in window
      ? (cb) => window.requestIdleCallback(cb, { timeout: 5000 })
      : (cb) => window.setTimeout(cb, 200);
  let i = 0;
  const next = () => {
    const preload = registered[i++];
    if (!preload) return;
    void preload().finally(() => idle(next));
  };
  const start = () => window.setTimeout(() => idle(next), 3000);
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true });
}

warmRegisteredRoutes();
