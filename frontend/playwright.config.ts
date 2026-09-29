import { defineConfig } from '@playwright/test';

// Browser end-to-end (M6). Drives a real Chromium against a real running stack:
// a uvicorn backend plus the production frontend server (server.mjs serving the built
// bundle, with its CSP and /api proxy), so the journeys exercise what is deployed (#526).
// Seed the data first (golden trio + a known e2e user):
//   RUN_INTEGRATION=1 python scripts/seed_playwright_e2e.py
// then: npx playwright test   (locally pass E2E_PYTHON=/path/to/python with deps).
// E2E_FRONTEND=dev runs the journeys against the Vite dev server instead (no build, no
// CSP), and E2E_BACKEND_PORT moves the backend off 8000 when that port is taken.

const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT || 8000);
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const DEV_FRONTEND = process.env.E2E_FRONTEND === 'dev';
const FRONTEND_PORT = DEV_FRONTEND ? 5173 : 4173;
const PY = process.env.E2E_PYTHON || 'python';

const frontendServer = DEV_FRONTEND
  ? {
      // Bind IPv4 explicitly: vite defaults to localhost (often ::1), which the
      // 127.0.0.1 readiness probe / baseURL can't reach.
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_DEV_API_PROXY_TARGET: BACKEND },
    }
  : {
      // The production path: build the bundle, then serve it with server.mjs, which sends
      // the enforcing CSP and proxies /api to the backend.
      command: 'npm run build && node server.mjs',
      url: `http://127.0.0.1:${FRONTEND_PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 240_000,
      env: { PORT: String(FRONTEND_PORT), BACKEND_URL: BACKEND },
    };

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    headless: true,
    browserName: 'chromium',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      // Backend reads Postgres/ClickHouse creds from .env; we override APP_ENV +
      // the bootstrap flags so startup stays light (no reference/HPO/gene loads).
      command: `${PY} -m uvicorn backend.app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: '..',
      url: `${BACKEND}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        APP_ENV: 'test',
        REFERENCE_BOOTSTRAP_ENABLED: 'false',
        HPO_BOOTSTRAP_ON_STARTUP: 'false',
        GENE_REFERENCE_BOOTSTRAP_ON_STARTUP: 'false',
      },
    },
    frontendServer,
  ],
});
