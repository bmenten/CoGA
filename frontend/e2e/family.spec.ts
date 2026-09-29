import { expect, test } from './fixtures';

import { GOLDEN_FAMILY, login } from './helpers';

test.beforeEach(async ({ page }) => {
  await login(page);
});

test('opens the golden family workspace', async ({ page }) => {
  await page.goto(`/families/${GOLDEN_FAMILY}`);
  // Stayed authenticated on the family route (not bounced to /login)...
  await expect(page).toHaveURL(new RegExp(`/families/${GOLDEN_FAMILY}`));
  await expect(page).not.toHaveURL(/\/login/);
  // ...and the family identifier is rendered (breadcrumb / workspace header).
  await expect(page.getByText(GOLDEN_FAMILY).first()).toBeVisible({ timeout: 15_000 });
});

test('renders the genome overview for the golden family', async ({ page }) => {
  await page.goto(`/families/${GOLDEN_FAMILY}/genome`);
  await expect(page).toHaveURL(new RegExp(`/families/${GOLDEN_FAMILY}/genome`));
  await expect(page.getByText(GOLDEN_FAMILY).first()).toBeVisible({ timeout: 15_000 });
  // The genome view draws its tracks/ideogram as SVG/canvas — assert it renders
  // (data-driven, non-pixel) rather than asserting on brittle chart internals.
  await expect(page.locator('canvas, svg').first()).toBeVisible({ timeout: 30_000 });
});
