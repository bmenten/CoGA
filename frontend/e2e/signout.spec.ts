import { type Page } from '@playwright/test';

import { expect, test } from './fixtures';

import { GOLDEN_FAMILY, login } from './helpers';

/**
 * The signed version the report names (0 if none yet): the one the live report compares
 * itself with, or the one the page renders from its record.
 */
async function currentVersion(page: Page): Promise<number> {
  const line = page.locator('.report-signout-line').first();
  if (!(await line.isVisible().catch(() => false))) return 0;
  const text = await line.innerText();
  const match = text.match(/version\s+(\d+)/i);
  return match ? Number.parseInt(match[1], 10) : 0;
}

/**
 * Wait for whichever comes next after a sign-out attempt: the evidence-drift override
 * dialog, the Sample-QC override dialog, or the signed version advancing past `before`.
 */
async function nextStep(page: Page, before: number): Promise<'drift' | 'qc' | 'done'> {
  const drift = page.locator('#drift-ack-reason');
  const qc = page.locator('#qc-ack-reason');
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (await drift.isVisible().catch(() => false)) return 'drift';
    if (await qc.isVisible().catch(() => false)) return 'qc';
    if ((await currentVersion(page)) > before) return 'done';
    await page.waitForTimeout(250);
  }
  return 'done';
}

test('signs out the family report from the browser (with gate handling)', async ({ page }) => {
  await login(page);
  // A case is signed out from the live report; a signed case opens on its signed version.
  await page.goto(`/families/${GOLDEN_FAMILY}/report?view=live`);

  // The seed tagged a variant for reporting, so the report is non-empty...
  await expect(page.getByText(/No variants are currently tagged for reporting/i)).toHaveCount(0);
  const signOutButton = page.getByRole('button', { name: /sign out report|amend sign-out/i });
  // ...and sign-out is offered once the page knows whether the case is signed.
  await expect(signOutButton).toBeEnabled({ timeout: 20_000 });

  const before = await currentVersion(page);
  await signOutButton.click();

  // Each gate is an acknowledge-with-reason dialog (#508 made the evidence-drift gate one
  // too; it used to be a native confirm). Evidence drift comes first, then the
  // sample-integrity gate (#330) — the seeded trio's sparse genotypes leave an asserted
  // relationship unverifiable. Either may be absent on a different seed, so each is
  // handled only when it appears.
  let step = await nextStep(page, before);
  if (step === 'drift') {
    await page.locator('#drift-ack-reason').fill('e2e drift override — seeded review has no evidence snapshot');
    const overrideButton = page.getByRole('button', { name: /sign out anyway/i });
    await expect(overrideButton).toBeEnabled();
    await overrideButton.click();
    step = await nextStep(page, before);
  }
  if (step === 'qc') {
    await page.locator('#qc-ack-reason').fill('e2e QC override — relatedness not assessable on seed data');
    const overrideButton = page.getByRole('button', { name: /sign out anyway/i });
    await expect(overrideButton).toBeEnabled();
    await overrideButton.click();
  }

  // The page then shows the version just signed, rendered from its record, and — straight
  // after signing, through the real JSONB round-trip — the family's data still matches it.
  await expect(page.getByText(/^Signed version \d+ — signed out by/)).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => currentVersion(page), { timeout: 20_000 }).toBeGreaterThan(before);
  await expect(page.getByText(/The family’s current data still matches this signed version/)).toBeVisible({
    timeout: 20_000,
  });
  // What it shows comes from the record, which holds no variant description.
  await expect(page.getByRole('heading', { name: 'Not in the signed record' })).toBeVisible();
});
