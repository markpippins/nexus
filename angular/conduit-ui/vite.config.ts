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
      // Host-header allowlist for LAN access. Vite rejects unknown Host values
      // (DNS-rebinding guard) even when bound to 0.0.0.0, so the hosts are read
      // from the environment rather than baked into the repo.
      allowedHosts: (process.env.NEXUS_UI_ALLOWED_HOSTS ?? '')
        .split(',')
        .map((h) => h.trim())
        .filter(Boolean),
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modifyâfile watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
