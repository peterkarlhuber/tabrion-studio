import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { configDefaults } from 'vitest/config';

// `npm run dev` proxies /api to a running backend (`tabrion-studio --port 8765 --no-browser --print-url`). Its address
// is a fresh random 127.a.b.c for every start (finding A-01): set TABRION_STUDIO_BACKEND to the printed origin. The
// backend answers only its own Host and Origin (STU-003), so the proxy presents both as the backend's; the backend has
// no development exception. Open the printed one-time link with its address and port changed to 127.0.0.1:5173. Because the proxy
// rewrites Origin, the development setup is not for shared machines (review S-5, docs/TODO.md TD-007).
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
  // Two projects, run one after the other by `npm test`: "unit" (every *.test.ts(x), in parallel) and "perf" (the timed
  // acceptance tests, *.perf.test.ts, alone in one worker so no other test shares the CPU while they measure; STU-023).
  test: {
    globals: true,
    restoreMocks: true,
    projects: [
      {
        extends: true,
        test: {
          name: 'unit',
          environment: 'jsdom',
          setupFiles: ['./src/test/setup.ts'],
          include: ['src/**/*.test.{ts,tsx}'],
          exclude: [...configDefaults.exclude, 'src/**/*.perf.test.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'perf',
          environment: 'node',
          include: ['src/**/*.perf.test.ts'],
          fileParallelism: false,
          maxWorkers: 1,
        },
      },
    ],
  },
});
