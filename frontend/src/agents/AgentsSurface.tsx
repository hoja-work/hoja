/* Route shell for the agent screens (HOJA-602-A-16).
 *
 * One entry point owns the four routes, so the shell (`main.tsx`) has a single thing to mount
 * and the flag has a single thing to gate:
 *
 *   /workspace/agents               → the Agents list            (screen 1)
 *   /workspace/agents/<key>         → that agent's detail page   (screen 2)
 *   /workspace/agents/queue         → the workspace run queue    (screen 3)
 *   /workspace/agents/<key>/queue   → that agent's run queue     (screen 3)
 *   /workspace/agents/audit         → the audit log              (screen 6)
 *   /workspace/agents/runners       → the runners list           (screen 4)
 *   /workspace/agents/runners/<id>  → that runner and its Connect exposures (screen 5)
 *
 * `runners` is matched before the `<agentKey>` branch, exactly as `queue` and `audit` are, so a
 * runner id can never be read as an agent key.
 *
 * The router is read from `location.pathname` and re-read on `popstate`, which is how the rest
 * of the app navigates (pushState + a view state). Nothing here fetches: each screen owns its
 * own read through the adapter.
 */

import { useEffect, useState } from "react";
import { agentsSurfaceEnabled } from "./flag";
import { AgentDetailPage } from "./AgentDetail";
import { AgentsPane } from "./AgentsPane";
import { AuditPane } from "./AuditPane";
import { RunQueue } from "./RunQueue";
import { RunnerDetailPage, RunnersPane } from "./RunnersPane";

export const AGENTS_ROOT = "/workspace/agents";

export type AgentsRoute =
  | { kind: "list" }
  | { kind: "queue"; agentKey?: string }
  | { kind: "audit" }
  | { kind: "runners" }
  | { kind: "runner"; runnerId: string }
  | { kind: "detail"; agentKey: string };

export function parseAgentsRoute(pathname: string): AgentsRoute {
  const rest = pathname
    .replace(/^\/workspace\/agents\/?/i, "")
    .split("/")
    .filter(Boolean);
  if (!rest.length) return { kind: "list" };
  if (rest[0].toLowerCase() === "audit") return { kind: "audit" };
  if (rest[0].toLowerCase() === "queue") return { kind: "queue" };
  if (rest[0].toLowerCase() === "runners") {
    return rest[1]
      ? { kind: "runner", runnerId: decodeURIComponent(rest[1]) }
      : { kind: "runners" };
  }
  const agentKey = decodeURIComponent(rest[0]);
  if (rest[1] && rest[1].toLowerCase() === "queue")
    return { kind: "queue", agentKey };
  return { kind: "detail", agentKey };
}

export function AgentsSurface({
  workspaceName,
  viewerName,
}: {
  workspaceName: string;
  viewerName: string;
}) {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = (next: string) => {
    if (next !== window.location.pathname)
      window.history.pushState({}, "", next);
    setPath(next);
  };

  /* Fail closed. The flag is read on every render so the geometry test can flip it with one
   * localStorage write, but the default is off and the page says so rather than 404ing. */
  if (!agentsSurfaceEnabled()) {
    return (
      <section
        className="flex flex-col min-w-0 h-full bg-panel"
        data-surface="agents-disabled"
        aria-label="Agents"
      >
        <header className="flex flex-none items-center border-b border-line gap-2 min-h-12.25 pt-0 pr-5 pb-0 pl-5 border-b-line shell-phone:flex-wrap shell-phone:gap-y-1">
          <nav
            className="flex flex-1 items-center gap-1.75 text-[13px] text-muted min-w-0 shell-phone:min-w-0 shell-phone:flex-wrap shell-phone:gap-y-0.5"
            aria-label="Breadcrumb"
          >
            <span>{workspaceName}</span>
            <span className="text-muted opacity-70">›</span>
            <span className="truncate text-foreground font-semibold overflow-hidden text-ellipsis whitespace-nowrap">
              Agents
            </span>
          </nav>
        </header>
        <div className="flex-1 overflow-y-auto min-h-0">
          <div className="text-center pt-15 pr-5 pb-15 pl-5 text-muted">
            <h2 className="text-foreground text-[15px] mt-3 mr-0 mb-1.5 ml-0">
              Agents are not enabled
            </h2>
            <p>
              This workspace has not turned the agent surface on. Nothing here
              reads or writes until it is enabled.
            </p>
          </div>
        </div>
      </section>
    );
  }

  const route = parseAgentsRoute(path);
  const tab =
    route.kind === "queue"
      ? "queue"
      : route.kind === "audit"
        ? "audit"
        : route.kind === "runners" || route.kind === "runner"
          ? "runners"
          : "agents";
  const onTab = (id: string) =>
    go(
      id === "queue"
        ? `${AGENTS_ROOT}/queue`
        : id === "audit"
          ? `${AGENTS_ROOT}/audit`
          : id === "runners"
            ? `${AGENTS_ROOT}/runners`
            : AGENTS_ROOT,
    );

  if (route.kind === "detail") {
    return (
      <AgentDetailPage
        agentKey={route.agentKey}
        workspaceName={workspaceName}
        viewerName={viewerName}
        onBack={() => go(AGENTS_ROOT)}
        onOpenQueue={(key) =>
          go(`${AGENTS_ROOT}/${encodeURIComponent(key)}/queue`)
        }
      />
    );
  }

  if (route.kind === "queue") {
    return (
      <RunQueue
        workspaceName={workspaceName}
        agentKey={route.agentKey}
        tab={tab}
        onTab={onTab}
        onOpenAgent={(key) => go(`${AGENTS_ROOT}/${encodeURIComponent(key)}`)}
        /* The adapter resolves a session's agent user id to its routable member id. */
        onOpenRun={(run) =>
          go(`${AGENTS_ROOT}/${encodeURIComponent(run.agentId)}/queue`)
        }
      />
    );
  }

  if (route.kind === "audit")
    return <AuditPane workspaceName={workspaceName} tab={tab} onTab={onTab} />;

  if (route.kind === "runner") {
    return (
      <RunnerDetailPage
        runnerId={route.runnerId}
        workspaceName={workspaceName}
        viewerName={viewerName}
        onBack={() => go(`${AGENTS_ROOT}/runners`)}
      />
    );
  }

  if (route.kind === "runners") {
    return (
      <RunnersPane
        workspaceName={workspaceName}
        tab={tab}
        onTab={onTab}
        onOpen={(runner) =>
          go(`${AGENTS_ROOT}/runners/${encodeURIComponent(runner.id)}`)
        }
      />
    );
  }

  return (
    <AgentsPane
      workspaceName={workspaceName}
      tab={tab}
      onTab={onTab}
      onOpen={(agent) => go(`${AGENTS_ROOT}/${encodeURIComponent(agent.id)}`)}
    />
  );
}
