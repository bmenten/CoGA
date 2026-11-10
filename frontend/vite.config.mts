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
          // MSAL (Microsoft sign-in): its own chunk, loaded only where it is used.
          if (id.includes('/node_modules/@azure/')) {
            return 'msal-vendor';
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
      // Measure frontend coverage (mirrors the backend coverage floor). The global
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
        // Playwright journeys (TF-09d).
        'src/index.tsx',
        'src/setupTests.ts',
        'src/vite-env.d.ts'
      ],
      // Ratchet floors about 3 points under what the suite measures (lines 93.2 / statements
      // 92.0 / functions 90.8 / branches 82.4). The visualisations, which draw what the
      // clinician reads off a track, have a floor of their own (measured 95.7 / 94.3 / 95.5 /
      // 86.2).
      thresholds: {
        lines: 90,
        functions: 87,
        branches: 79,
        statements: 89,
        'src/components/visualizations/**': {
          lines: 92,
          functions: 92,
          branches: 83,
          statements: 91
        }
      }
    }
  }
});
