import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

// The dev/test harness can point the proxy at a disposable backend.
const backend = process.env.HOJA_DEV_BACKEND || 'http://127.0.0.1:8787';
const port = Number(process.env.HOJA_DEV_PORT || 5173);
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

export default defineConfig({
  plugins: [react()],
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
});
