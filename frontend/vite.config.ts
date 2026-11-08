import { defineConfig, type Plugin, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

// The dev/test harness can point the proxy at a disposable backend. This config is
// the only place the browser app learns where the backend is: the app always calls
// same-origin paths (/graphql, /api, /auth, ...) and this proxy, which exists only
// while `npm run dev` runs, forwards them. `npm run build` output has no proxy.
const backend = process.env.HOJA_DEV_BACKEND || 'http://127.0.0.1:8787';
const port = Number(process.env.HOJA_DEV_PORT || 5173);

function isLoopback(target: string): boolean {
  try {
    const { hostname } = new URL(target);
    return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1' || hostname === '[::1]';
  } catch {
    return false;
  }
}

const backendProxy: ProxyOptions = {
  target: backend,
  // Keep Origin and Host identical for the backend's cookie CSRF check.
  changeOrigin: false,
  configure: (proxy) => {
    proxy.on('proxyRes', (response) => {
      // Plain-HTTP development on a Tailscale IP cannot use Secure cookies.
      // This changes only Vite's local development responses, not production.
      const cookies = response.headers['set-cookie'];
      if (cookies) {
        response.headers['set-cookie'] = cookies.map((cookie) =>
          cookie.replace(/;\s*secure(?=;|$)/gi, ''),
        );
      }
    });
  },
};

// The dev-only module src/dev/devBackend.ts reads. It exists so the browser can
// report which backend the proxy targets without the app ever hard-coding an API
// origin: production builds never import it.
const DEV_BACKEND_MODULE = 'virtual:hoja-dev-backend';

function devBackendModule(): Plugin {
  const resolved = '\0' + DEV_BACKEND_MODULE;
  return {
    name: 'hoja-dev-backend',
    resolveId: (id) => (id === DEV_BACKEND_MODULE ? resolved : null),
    load: (id) => (id === resolved ? `export const devBackend = ${JSON.stringify(backend)};` : null),
  };
}

export default defineConfig(({ command }) => {
  // Dev-only guard: `npm run dev` must never forward browser traffic to a remote or
  // production deployment. A deliberate remote test backend has to opt out.
  if (command === 'serve' && !isLoopback(backend) && process.env.HOJA_DEV_ALLOW_REMOTE !== '1') {
    throw new Error(
      `HOJA_DEV_BACKEND must be a loopback address so development cannot reach production (got "${backend}"). ` +
        'Set HOJA_DEV_ALLOW_REMOTE=1 only for a deliberate remote test backend.',
    );
  }
  return {
    plugins: [react(), devBackendModule()],
    server: {
      port,
      proxy: {
        '/graphql': backendProxy,
        '/api': backendProxy,
        '/auth': backendProxy,
        '/onboarding': backendProxy,
        '/sync': backendProxy,
        '/ws': { ...backendProxy, ws: true },
      },
    },
  };
});
