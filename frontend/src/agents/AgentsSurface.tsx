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

import {useEffect, useState} from 'react';
import {agentsSurfaceEnabled} from './flag';
import {AgentDetailPage} from './AgentDetail';
import {AgentsPane} from './AgentsPane';
import {AuditPane} from './AuditPane';
import {RunQueue} from './RunQueue';
import {RunnerDetailPage, RunnersPane} from './RunnersPane';

export const AGENTS_ROOT = '/workspace/agents';

export type AgentsRoute =
  | {kind: 'list'}
  | {kind: 'queue'; agentKey?: string}
  | {kind: 'audit'}
  | {kind: 'runners'}
  | {kind: 'runner'; runnerId: string}
  | {kind: 'detail'; agentKey: string};

export function parseAgentsRoute(pathname: string): AgentsRoute {
  const rest = pathname.replace(/^\/workspace\/agents\/?/i, '').split('/').filter(Boolean);
  if (!rest.length) return {kind: 'list'};
  if (rest[0].toLowerCase() === 'audit') return {kind: 'audit'};
  if (rest[0].toLowerCase() === 'queue') return {kind: 'queue'};
  if (rest[0].toLowerCase() === 'runners') {
    return rest[1] ? {kind: 'runner', runnerId: decodeURIComponent(rest[1])} : {kind: 'runners'};
  }
  const agentKey = decodeURIComponent(rest[0]);
  if (rest[1] && rest[1].toLowerCase() === 'queue') return {kind: 'queue', agentKey};
  return {kind: 'detail', agentKey};
}

export function AgentsSurface({workspaceName, viewerName}: {workspaceName: string; viewerName: string}) {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const go = (next: string) => {
    if (next !== window.location.pathname) window.history.pushState({}, '', next);
    setPath(next);
  };

  /* Fail closed. The flag is read on every render so the geometry test can flip it with one
   * localStorage write, but the default is off and the page says so rather than 404ing. */
  if (!agentsSurfaceEnabled()) {
    return (
      <section className="lin-page" data-surface="agents-disabled" aria-label="Agents">
        <header className="lin-head">
          <nav className="lin-crumb" aria-label="Breadcrumb">
            <span>{workspaceName}</span><span className="sep">›</span><span className="current">Agents</span>
          </nav>
        </header>
        <div className="lin-scroll">
          <div className="lin-empty">
            <h2>Agents are not enabled</h2>
            <p>This workspace has not turned the agent surface on. Nothing here reads or writes until it is enabled.</p>
          </div>
        </div>
      </section>
    );
  }

  const route = parseAgentsRoute(path);
  const tab = route.kind === 'queue' ? 'queue'
    : route.kind === 'audit' ? 'audit'
      : route.kind === 'runners' || route.kind === 'runner' ? 'runners' : 'agents';
  const onTab = (id: string) => go(id === 'queue' ? `${AGENTS_ROOT}/queue`
    : id === 'audit' ? `${AGENTS_ROOT}/audit`
      : id === 'runners' ? `${AGENTS_ROOT}/runners` : AGENTS_ROOT);

  if (route.kind === 'detail') {
    return (
      <AgentDetailPage
        agentKey={route.agentKey}
        workspaceName={workspaceName}
        viewerName={viewerName}
        onBack={() => go(AGENTS_ROOT)}
        onOpenQueue={key => go(`${AGENTS_ROOT}/${encodeURIComponent(key)}/queue`)}
      />
    );
  }

  if (route.kind === 'queue') {
    return (
      <RunQueue
        workspaceName={workspaceName}
        agentKey={route.agentKey}
        tab={tab}
        onTab={onTab}
        onOpenAgent={key => go(`${AGENTS_ROOT}/${encodeURIComponent(key)}`)}
        /* A delegated task's own page (screen 4) is out of this slice, so a queue row opens the
           agent that ran it — the only page that exists for the run today. */
        onOpenRun={run => go(`${AGENTS_ROOT}/${encodeURIComponent(run.agentKey)}`)}
      />
    );
  }

  if (route.kind === 'audit') return <AuditPane workspaceName={workspaceName} tab={tab} onTab={onTab} />;

  if (route.kind === 'runner') {
    return (
      <RunnerDetailPage
        runnerId={route.runnerId}
        workspaceName={workspaceName}
        viewerName={viewerName}
        onBack={() => go(`${AGENTS_ROOT}/runners`)}
      />
    );
  }

  if (route.kind === 'runners') {
    return (
      <RunnersPane
        workspaceName={workspaceName}
        tab={tab}
        onTab={onTab}
        onOpen={runner => go(`${AGENTS_ROOT}/runners/${encodeURIComponent(runner.id)}`)}
      />
    );
  }

  return (
    <AgentsPane
      workspaceName={workspaceName}
      tab={tab}
      onTab={onTab}
      onOpen={agent => go(`${AGENTS_ROOT}/${encodeURIComponent(agent.key)}`)}
    />
  );
}
