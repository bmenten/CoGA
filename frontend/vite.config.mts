import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiProxyTarget =
  process.env.VITE_DEV_API_PROXY_TARGET || process.env.BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (_error, _req, res) => {
            if (!res || !('writeHead' in res) || res.headersSent) {
              return;
            }
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ detail: `Unable to reach backend API at ${apiProxyTarget}.` }));
          });
        },
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return undefined;
          }
          if (
            id.includes('/node_modules/react/') ||
            id.includes('/node_modules/react-dom/') ||
            id.includes('/node_modules/scheduler/')
          ) {
            return 'react-vendor';
          }
          if (id.includes('/node_modules/react-router')) {
            return 'router-vendor';
          }
          if (id.includes('/node_modules/@tanstack/react-query/')) {
            return 'query-vendor';
          }
          if (id.includes('/node_modules/d3') || id.includes('/node_modules/internmap')) {
            return 'd3-vendor';
          }
          return 'vendor';
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    // Unit tests live under src/. The Playwright browser e2e (frontend/e2e/*.spec.ts)
    // is a separate runner (npm run test:e2e) — keep vitest from collecting it.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      // Measure frontend coverage (#273) (mirrors the backend coverage floor). The global
      // threshold ratchets against regression; `npm run test:coverage` fails below it.
      provider: 'v8',
      reporter: ['text-summary', 'json-summary', 'json'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/__tests__/**',
        'src/**/*.d.ts',
        // The entry point: the route table and bootstrap, driven end to end by the
        // Playwright journeys (TF-09d). The exclude named a main.tsx that does not exist.
        'src/index.tsx',
        'src/setupTests.ts',
        'src/vite-env.d.ts'
      ],
      // Ratchet floors set just below the measured baseline (2026-09-29, #526 close-out:
      // lines 81.9 / statements 80.1 / functions 77.5 / branches 69.5; before: 80.3 /
      // 78.4 / 75.7 / 66.2 earlier that day, and 69.3 / 67.7 / 65.1 / 57.2 on
      // 2026-06-28). The visualisations, which draw what the clinician reads off a
      // track, have a floor of their own (lines 89.9 / statements 88.5 / functions 90.9 /
      // branches 78.2).
      thresholds: {
        lines: 80,
        functions: 75,
        branches: 67,
        statements: 78,
        'src/components/visualizations/**': {
          lines: 88,
          functions: 89,
          branches: 76,
          statements: 86
        }
      }
    }
  }
});
