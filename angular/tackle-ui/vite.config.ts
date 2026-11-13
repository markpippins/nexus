import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // Hosts whose Host header is accepted. Vite's dev server refuses unknown
      // Host values (DNS-rebinding guard) even when bound to 0.0.0.0, which
      // makes LAN access look like an outage. Hosts come from the
      // environment so no machine name is baked into the repo.
      allowedHosts: (process.env.NEXUS_UI_ALLOWED_HOSTS ?? '')
      .split(',')
      .map((h) => h.trim())
      .filter(Boolean),
      // Proxy /api and /config to the real tackle-srv backend.
      // In production (via server.ts), Express-level proxy handles this;
      // during Vite-only dev, these rules forward requests to tackle-srv:3410.
      proxy: {
        '/api': {
          target: 'http://localhost:3410',
          changeOrigin: true,
        },
        '/config': {
          target: 'http://localhost:3410',
          changeOrigin: true,
        },
      },
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify — file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR === 'true' ? false : { port: 24679 },
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
