/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// `npm run dev` proxies /api to a running backend (`tabrion-studio --port 8765 --no-browser --print-url`). The backend
// answers only its own Host and Origin (STU-003), so the proxy presents both as the backend's; the backend has no
// development exception. Open the printed one-time link with the port changed to 5173 to sign in.
const backend = process.env.TABRION_STUDIO_BACKEND ?? 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (request) => {
            if (request.getHeader('origin') !== undefined) {
              request.setHeader('origin', backend);
            }
          });
        },
      },
    },
  },
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
    // No source maps in the wheel; no inlined assets as data: URLs (the CSP of STU-003 keeps them out).
    sourcemap: false,
    assetsInlineLimit: 0,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
