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
      // P2-7b: measure frontend coverage (mirrors the backend coverage floor). The global
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
      // Ratchet floors set just below the measured baseline (2026-09-29, #526: lines 80.3 /
      // statements 78.4 / functions 75.7 / branches 66.2; before: 69.3 / 67.7 / 65.1 /
      // 57.2 on 2026-06-28). The visualisations, which draw what the clinician reads off
      // a track, have a floor of their own (lines 84.3 / statements 82.7 / functions
      // 85.5 / branches 68.7).
      thresholds: {
        lines: 78,
        functions: 73,
        branches: 64,
        statements: 76,
        'src/components/visualizations/**': {
          lines: 82,
          functions: 83,
          branches: 66,
          statements: 80
        }
      }
    }
  }
});
