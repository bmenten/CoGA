import { expect, test } from './fixtures';

// The production server's page-level security headers (securityHeaders.mjs, #521). The
// journeys run against that server (#526), so a policy that breaks the app fails them
// through the CSP fixture; this checks the headers are actually sent.
test('the frontend server sends its security headers', async ({ page }) => {
  test.skip(process.env.E2E_FRONTEND === 'dev', 'the Vite dev server sends no CSP');
  const response = await page.goto('/login');
  expect(response, 'no response for /login').not.toBeNull();
  const headers = response!.headers();
  const csp = headers['content-security-policy'] ?? '';
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(headers['x-content-type-options']).toBe('nosniff');
  expect(headers['permissions-policy'] ?? '').toContain('camera=()');
});
