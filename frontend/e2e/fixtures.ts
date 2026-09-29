import { expect, test as base } from '@playwright/test';

/**
 * The production server sends an enforcing Content-Security-Policy (securityHeaders.mjs).
 * A script, style, font or request it blocks shows up only as a console error, so every
 * journey fails if the browser reports a CSP violation (#526). Specs import `test` and
 * `expect` from here rather than from '@playwright/test'.
 */
export const test = base.extend<{ cspViolations: string[] }>({
  cspViolations: [
    async ({ page }, use) => {
      const violations: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error' && /Content Security Policy/i.test(message.text())) {
          violations.push(message.text());
        }
      });
      await use(violations);
      expect(violations, 'the page reported Content-Security-Policy violations').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
