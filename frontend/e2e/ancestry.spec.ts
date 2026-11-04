import { expect, test } from './fixtures';
import { E2E_EMAIL, E2E_PASSWORD, login } from './helpers';

// The exploratory ancestry PCA against the bundled public reference, the last section of Sample
// QC: a small-variant upload is calculated automatically (the worker's sweep queues it), and the
// plot is read. The upload is one record, far from a genome or an exome: CoGA reads it as part of
// the markers, so the individual is not plotted, and says why.
test('calculates the exploratory PCA by itself after an upload and plots no individual from a partial VCF', async ({
  page,
}) => {
  // The worker's sweep looks for changed families every half minute.
  test.setTimeout(150_000);
  await login(page);
  const token = await page.request.post('/api/auth/login', {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  });
  const auth = { Authorization: `Bearer ${(await token.json()).access_token}` };
  const projects = await (
    await page.request.get('/api/projects', { headers: auth })
  ).json();
  const project = projects.find(
    (item: { assembly_name?: string }) => item.assembly_name === 'GRCh38'
  );
  const projectId =
    typeof project._id === 'object' ? project._id.$oid : project._id;
  const family = `ANCESTRY_BROWSER_${Date.now().toString(36)}`;
  const sample = `PROBAND_${family}`;
  expect(
    (
      await page.request.post('/api/ped/upload', {
        headers: auth,
        params: { project_id: projectId },
        multipart: {
          file: {
            name: 'synthetic.ped',
            mimeType: 'text/plain',
            buffer: Buffer.from(`${family} ${sample} 0 0 1 1\n`),
          },
        },
      })
    ).ok()
  ).toBeTruthy();
  // Three of the reference's markers (public 1000 Genomes loci), unfiltered: far fewer
  // non-reference calls than a sequenced genome or exome carries there.
  const vcf =
    `##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t${sample}\n` +
    [
      ['965125', 'G', 'C'],
      ['973858', 'G', 'C'],
      ['973862', 'A', 'G'],
    ]
      .map(([pos, ref, alt]) => `1\t${pos}\t.\t${ref}\t${alt}\t50\t.\t.\tGT\t0/1\n`)
      .join('');
  expect(
    (
      await page.request.post(`/api/families/${family}/small-variants/upload`, {
        headers: auth,
        params: { source_format: 'clair3' },
        multipart: {
          file: {
            name: 'synthetic.vcf',
            mimeType: 'text/plain',
            buffer: Buffer.from(vcf),
          },
        },
      })
    ).ok()
  ).toBeTruthy();
  await page.goto(
    `/families/${family}/qc?project_id=${encodeURIComponent(projectId)}`
  );
  const ancestry = page.locator('section#ancestry');
  await expect(
    ancestry.getByRole('heading', { name: 'Ancestry (exploratory)' })
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    ancestry
      .getByRole('note')
      .filter({ hasText: 'Exploratory — not for clinical use.' })
  ).toBeVisible();
  const table = ancestry.getByRole('table', {
    name: "Projections and marker quality of the family’s individuals",
  });
  // Calculated without a request: the section looks again while a run is queued, and the
  // page is reloaded until the sweep has queued one.
  await expect(async () => {
    await page.reload();
    await expect(table).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 120_000, intervals: [5_000] });
  const row = table.getByRole('row', { name: new RegExp(sample) });
  await expect(row).toContainText('Not plotted');
  await expect(row).toContainText('Direct calls · part of the markers');
  await expect(row).toContainText('cannot be read as reference');
  await expect(
    ancestry.getByRole('combobox', { name: 'Projection' })
  ).toContainText('automatic');
  await expect(ancestry.locator('svg [data-sample]')).toHaveCount(0);
  await expect(ancestry.locator('circle[data-population]')).toHaveCount(2504);
  await expect(ancestry.locator('svg .ancestry-exploratory-mark')).toHaveText(
    'Exploratory — not for clinical use'
  );
  await ancestry.getByLabel('Vertical axis').selectOption('2');
  await expect(ancestry.locator('svg desc')).toContainText('vertical PC3');
  await ancestry.getByRole('checkbox', { name: /EUR · European/ }).uncheck();
  await expect(ancestry.locator('circle[data-population="EUR"]')).toHaveCount(
    0
  );
  await ancestry.getByRole('button', { name: 'Reset view' }).click();
  await page.screenshot({
    path: test.info().outputPath('ancestry-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBeTruthy();
  await page.screenshot({
    path: test.info().outputPath('ancestry-mobile.png'),
    fullPage: true,
  });
});
