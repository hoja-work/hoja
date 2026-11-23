/**
 * Lightweight browser performance & RUM instrumentation (HOJA-754).
 *
 * Captures cold page load (Navigation Timing + LCP), Long Tasks (>50ms),
 * SPA route transitions, and Server-Timing / X-Request-Id attribution for
 * API/GraphQL requests. Never records query strings, issue titles, or PII.
 *
 * Exposes `window.__hojaPerf` for local Playwright / DevTools verification
 * and flushes bounded batches to `POST /api/perf/rum` via `sendBeacon`.
 */

export type RumEvent = {
  kind: "nav" | "route" | "longtask";
  route: string;
  duration_ms?: number;
  ttfb_ms?: number;
  fcp_ms?: number;
  lcp_ms?: number;
  dom_interactive_ms?: number;
  route_ms?: number;
  longtask_ms?: number;
  longtask_count?: number;
  server_app_ms?: number;
  server_db_ms?: number;
  server_pool_ms?: number;
  js_transfer_kb?: number;
  slowest_request_id?: string;
  ts: number;
};

type ApiSample = {
  path: string;
  durationMs: number;
  appMs?: number;
  dbMs?: number;
  poolMs?: number;
  requestId?: string;
  ts: number;
};

const MAX_RING = 50;
const FLUSH_BATCH = 10;
const UUID_RE = /\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;
const ISSUE_RE = /\/[A-Z]{2,10}-\d+/g;

export function normalizeRoute(pathname: string): string {
  const clean = (pathname || "/").split("?")[0].split("#")[0] || "/";
  const norm = clean.replace(UUID_RE, "/:uuid").replace(ISSUE_RE, "/:issue");
  return norm.length > 120 ? norm.slice(0, 120) : norm;
}

export function parseServerTiming(header: string | null): {
  appMs?: number;
  dbMs?: number;
  poolMs?: number;
} {
  if (!header) return {};
  const out: { appMs?: number; dbMs?: number; poolMs?: number } = {};
  for (const part of header.split(",")) {
    const segs = part.trim().split(";");
    const name = segs[0]?.trim().toLowerCase();
    let dur: number | undefined;
    for (const seg of segs.slice(1)) {
      const [k, v] = seg.trim().split("=");
      if (k?.trim().toLowerCase() === "dur" && v !== undefined) {
        const n = Number(v);
        if (Number.isFinite(n) && n >= 0) dur = n;
      }
    }
    if (dur === undefined) continue;
    if (name === "app") out.appMs = dur;
    else if (name === "db") out.dbMs = dur;
    else if (name === "pool") out.poolMs = dur;
  }
  return out;
}

let installed = false;
const ring: RumEvent[] = [];
const pending: RumEvent[] = [];
const recentApi: ApiSample[] = [];
let lcpMs: number | undefined;
let fcpMs: number | undefined;
let longtaskCount = 0;
let longtaskMaxMs = 0;
let navReported = false;

function recordEvent(ev: RumEvent): void {
  ring.push(ev);
  if (ring.length > MAX_RING) ring.shift();
  pending.push(ev);
  if (pending.length >= FLUSH_BATCH) flushBeacon();
}

function slowestApiSince(sinceTs: number): ApiSample | undefined {
  let best: ApiSample | undefined;
  for (let i = recentApi.length - 1; i >= 0; i--) {
    const s = recentApi[i];
    if (s.ts < sinceTs) break;
    if (!best || s.durationMs > best.durationMs) best = s;
  }
  return best;
}

export function flushBeacon(): void {
  if (!pending.length || typeof navigator === "undefined") return;
  const batch = pending.splice(0, 20).map(({ ts: _ts, ...rest }) => rest);
  try {
    const body = JSON.stringify({ events: batch });
    if (typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" });
      navigator.sendBeacon("/api/perf/rum", blob);
    }
  } catch {
    // Telemetry must never break the app.
  }
}

function reportNavigation(): void {
  if (navReported || typeof performance === "undefined") return;
  const nav = performance.getEntriesByType("navigation")[0] as
    | PerformanceNavigationTiming
    | undefined;
  if (!nav) return;
  navReported = true;

  let jsTransferBytes = 0;
  for (const res of performance.getEntriesByType("resource") as PerformanceResourceTiming[]) {
    if (res.initiatorType === "script" || res.name.endsWith(".js")) {
      jsTransferBytes += res.transferSize || 0;
    }
  }
  const slowest = slowestApiSince(0);
  recordEvent({
    kind: "nav",
    route: normalizeRoute(window.location.pathname),
    ttfb_ms: Math.max(0, nav.responseStart - nav.requestStart),
    dom_interactive_ms: Math.max(0, nav.domInteractive - nav.startTime),
    duration_ms: Math.max(0, (nav.loadEventEnd || performance.now()) - nav.startTime),
    fcp_ms: fcpMs,
    lcp_ms: lcpMs,
    longtask_count: longtaskCount,
    longtask_ms: longtaskMaxMs || undefined,
    js_transfer_kb: jsTransferBytes > 0 ? Math.round((jsTransferBytes / 1024) * 10) / 10 : undefined,
    server_app_ms: slowest?.appMs,
    server_db_ms: slowest?.dbMs,
    server_pool_ms: slowest?.poolMs,
    slowest_request_id: slowest?.requestId,
    ts: performance.now(),
  });
}

let activeRoute: { route: string; startTs: number; timer?: number } | null = null;

function startRouteTimer(pathname: string): void {
  const route = normalizeRoute(pathname);
  if (activeRoute?.timer !== undefined) window.clearTimeout(activeRoute.timer);
  const startTs = performance.now();
  const entry = { route, startTs, timer: undefined as number | undefined };
  activeRoute = entry;
  // Measure time until two animation frames + microtask settlement after route change.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      entry.timer = window.setTimeout(() => {
        if (activeRoute !== entry) return;
        const routeMs = Math.max(0, performance.now() - startTs);
        const slowest = slowestApiSince(startTs);
        recordEvent({
          kind: "route",
          route,
          route_ms: routeMs,
          duration_ms: routeMs,
          server_app_ms: slowest?.appMs,
          server_db_ms: slowest?.dbMs,
          server_pool_ms: slowest?.poolMs,
          slowest_request_id: slowest?.requestId,
          ts: performance.now(),
        });
        activeRoute = null;
      }, 120);
    });
  });
}

export function initPerfTracking(): void {
  if (installed || typeof window === "undefined" || typeof performance === "undefined") return;
  installed = true;

  // Expose read-only inspection hook for DevTools & Playwright benchmark harness.
  (window as unknown as Record<string, unknown>).__hojaPerf = {
    events: () => ring.slice(),
    apiSamples: () => recentApi.slice(),
    flush: flushBeacon,
  };

  // Capture X-Request-Id + Server-Timing from same-origin API/GraphQL fetches.
  if (typeof window.fetch === "function") {
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const urlStr = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const isTracked =
        urlStr.startsWith("/graphql") ||
        urlStr.startsWith("/sync/") ||
        (urlStr.startsWith("/api/") && !urlStr.startsWith("/api/perf/"));
      if (!isTracked) return origFetch(input, init);
      const t0 = performance.now();
      const resp = await origFetch(input, init);
      const durationMs = performance.now() - t0;
      const st = parseServerTiming(resp.headers.get("server-timing"));
      const reqId = resp.headers.get("x-request-id") || undefined;
      recentApi.push({
        path: urlStr.split("?")[0],
        durationMs,
        appMs: st.appMs,
        dbMs: st.dbMs,
        poolMs: st.poolMs,
        requestId: reqId,
        ts: performance.now(),
      });
      if (recentApi.length > 100) recentApi.shift();
      return resp;
    };
  }

  // PerformanceObserver for paint, LCP, and Long Tasks.
  if (typeof PerformanceObserver !== "undefined") {
    const observe = (type: string, cb: (entries: PerformanceEntryList) => void) => {
      try {
        const obs = new PerformanceObserver((list) => cb(list.getEntries()));
        obs.observe({ type, buffered: true } as PerformanceObserverInit);
      } catch {
        // Unsupported entryType in this browser.
      }
    };
    observe("paint", (entries) => {
      for (const e of entries) {
        if (e.name === "first-contentful-paint") fcpMs = e.startTime;
      }
    });
    observe("largest-contentful-paint", (entries) => {
      const last = entries[entries.length - 1];
      if (last) lcpMs = last.startTime;
    });
    observe("longtask", (entries) => {
      for (const e of entries) {
        longtaskCount += 1;
        if (e.duration > longtaskMaxMs) longtaskMaxMs = e.duration;
      }
    });
  }

  // Track SPA route transitions via history.pushState and popstate.
  const origPushState = window.history.pushState.bind(window.history);
  window.history.pushState = function (
    data: unknown,
    unused: string,
    url?: string | URL | null,
  ) {
    const prevPath = window.location.pathname;
    origPushState(data, unused, url);
    if (window.location.pathname !== prevPath) {
      startRouteTimer(window.location.pathname);
    }
  };
  window.addEventListener("popstate", () => {
    startRouteTimer(window.location.pathname);
  });

  // Report cold navigation once settled, and flush on page hide.
  if (document.readyState === "complete") {
    window.setTimeout(reportNavigation, 800);
  } else {
    window.addEventListener("load", () => window.setTimeout(reportNavigation, 800), { once: true });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      reportNavigation();
      flushBeacon();
    }
  });
}
