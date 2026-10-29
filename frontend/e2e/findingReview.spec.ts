import { expect, test } from './fixtures';

import { GOLDEN_FAMILY, login } from './helpers';

/**
 * The review of a repeat locus from its card (docs/repeat-paraphase-review-design.md): the
 * dialog offers the repeat scale and no ACMG class tags, the save shows on the card at once,
 * and it is the stored review, read back after a reload.
 */
test('reviews a repeat locus from its card and reads the review back', async ({ page }) => {
  await login(page);
  await page.goto(`/families/${GOLDEN_FAMILY}/repeat-expansions`);

  const card = page.locator('.family-repeat-list article').first();
  await expect(card).toBeVisible({ timeout: 20_000 });
  const name = await card.getAttribute('aria-label');

  await card.getByRole('button', { name: 'Tags & notes' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Repeat review')).toBeVisible();
  await expect(dialog.getByLabel('Pathogenic expansion')).toBeVisible();
  await expect(dialog.getByLabel(/VUS/)).toHaveCount(0);

  const checkbox = dialog.getByLabel('Uncertain significance');
  if (!(await checkbox.isChecked())) await checkbox.check();
  await dialog.getByPlaceholder('Note or rationale').fill('e2e repeat review');
  await dialog.getByRole('button', { name: 'Save review' }).click();
  await expect(dialog).toBeHidden({ timeout: 20_000 });

  await expect(card.getByText('Review: Uncertain significance')).toBeVisible();
  await expect(card.getByText('e2e repeat review')).toBeVisible();

  await page.reload();
  const reloaded = page.locator(`.family-repeat-list article[aria-label="${name}"]`);
  await expect(reloaded.getByText('Review: Uncertain significance')).toBeVisible({ timeout: 20_000 });
  await expect(reloaded.getByText('e2e repeat review')).toBeVisible();
});
