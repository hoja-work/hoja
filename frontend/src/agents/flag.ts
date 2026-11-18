/* Feature flag for the agent surface (HOJA-602-A-16).
 *
 * The screens are complete but the backend is not: WP04 and WP06 have not landed a GraphQL
 * field this surface can read. So it ships dark, and it has to *stay* dark unless somebody
 * turns it on deliberately. Three ways to turn it on, in increasing order of permanence:
 *
 *   - development builds (`import.meta.env.DEV`), so a working tree shows the surface;
 *   - `localStorage.hoja_agents_surface = 'on'`, a per-browser opt-in used by the geometry
 *     test and by a reviewer looking at a deployed preview;
 *   - `VITE_HOJA_AGENTS=on` at build time, which is how a release would enable it;
 *   - the workspace switch an admin flips in Settings -> Workspace (`workspaceFeatures`), which is
 *     how production turns it on per organization.
 *
 * Anything else — including a stray value like `'1'` or `'true'` — is off. Fail closed.
 */

import { useSyncExternalStore } from 'react';

export const AGENTS_FLAG_KEY = 'hoja_agents_surface';
const FLAG_TRUE = 'on';

function fromBuild(): boolean {
  const raw = (import.meta.env.VITE_HOJA_AGENTS as string | undefined) || '';
  return raw.trim().toLowerCase() === FLAG_TRUE;
}

function fromStorage(): boolean {
  try {
    return (localStorage.getItem(AGENTS_FLAG_KEY) || '').trim().toLowerCase() === FLAG_TRUE;
  } catch {
    return false;
  }
}

/* The workspace switch (revision 76). The app reads `workspaceFeatures` once per workspace and
 * stores it here; `null` means "not read yet", which is off. Kept outside React so the sidebar and
 * the Agents route read one value. */
let workspaceEnabled: boolean | null = null;
const listeners = new Set<() => void>();

export function setWorkspaceAgentsEnabled(value: boolean | null): void {
  if (workspaceEnabled === value) return;
  workspaceEnabled = value;
  listeners.forEach(notify => notify());
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

/** The workspace's own setting, without the build/localStorage/dev opt-ins. */
export function useWorkspaceAgentsEnabled(): boolean | null {
  return useSyncExternalStore(subscribe, () => workspaceEnabled);
}

export function agentsSurfaceEnabled(): boolean {
  if (fromBuild()) return true;
  if (typeof import.meta.env.DEV !== 'undefined' && import.meta.env.DEV) return true;
  if (workspaceEnabled === true) return true;
  return fromStorage();
}

/** `agentsSurfaceEnabled`, re-rendering when an admin flips the workspace switch. */
export function useAgentsSurfaceEnabled(): boolean {
  useWorkspaceAgentsEnabled();
  return agentsSurfaceEnabled();
}
