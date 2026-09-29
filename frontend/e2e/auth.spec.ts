import { expect, test } from './fixtures';

import { login } from './helpers';

test('rejects bad credentials and stays on the login page', async ({ page }) => {
  await page.goto('/login');
  await page.locator('input[type="email"]').fill('nobody@example.com');
  await page.locator('input[type="password"]').fill('wrong-password');
  await page.getByRole('button', { name: /login|signing in/i }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('input[type="password"]')).toBeVisible();
});

test('logs in with the seeded e2e user and lands authenticated', async ({ page }) => {
  await login(page);
  // Left the login page for the default landing route...
  await expect(page).toHaveURL(/\/dashboard/);
  // ...and the login form is gone (we are authenticated).
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
});
