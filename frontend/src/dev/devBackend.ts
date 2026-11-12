/**
 * Which backend the Vite dev server proxies to.
 *
 * `vite.config.ts` serves the `virtual:hoja-dev-backend` module. Only the
 * `import.meta.env.DEV` branch in `main.tsx` imports this file, and that flag is
 * a compile-time `false` in `npm run build`, so production never bundles it.
 */
import { devBackend } from 'virtual:hoja-dev-backend';

export function devBackendOrigin(): string | null {
  if (!import.meta.env.DEV) return null;
  return devBackend || null;
}
