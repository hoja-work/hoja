/* Feature flag for the agent surface (HOJA-602-A-16).
 *
 * The screens are complete but the backend is not: WP04 and WP06 have not landed a GraphQL
 * field this surface can read. So it ships dark, and it has to *stay* dark unless somebody
 * turns it on deliberately. Three ways to turn it on, in increasing order of permanence:
 *
 *   - development builds (`import.meta.env.DEV`), so a working tree shows the surface;
 *   - `localStorage.hoja_agents_surface = 'on'`, a per-browser opt-in used by the geometry
 *     test and by a reviewer looking at a deployed preview;
 *   - `VITE_HOJA_AGENTS=on` at build time, which is how a release would enable it.
 *
 * Anything else — including a stray value like `'1'` or `'true'` — is off. Fail closed.
 */

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

export function agentsSurfaceEnabled(): boolean {
  if (fromBuild()) return true;
  if (typeof import.meta.env.DEV !== 'undefined' && import.meta.env.DEV) return true;
  return fromStorage();
}
