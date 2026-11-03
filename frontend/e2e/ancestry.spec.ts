import { expect, test } from './fixtures';
import { E2E_EMAIL, E2E_PASSWORD, login } from './helpers';

// The exploratory ancestry PCA against the bundled public reference: register a retained upload
// as the WGS source, calculate, and read the plot. The upload is one record, far from a whole
// genome: with absent records taken as reference it would otherwise project as all-reference,
// so the individual must come out "Insufficient data", plotted nowhere.
test('registers a WGS source, calculates the exploratory PCA and plots no individual from an incomplete VCF', async ({
  page,
}) => {
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
  const vcf = `##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t${sample}\n1\t10\t.\tA\tG\t50\t.\t.\tGT\t0/1\n`;
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
    `/families/${family}/ancestry?project_id=${encodeURIComponent(projectId)}`
  );
  await expect(
    page
      .getByRole('note')
      .filter({ hasText: 'Exploratory — not for clinical use.' })
  ).toBeVisible();
  const source = page.getByRole('region', { name: 'WGS source', exact: true });
  const register = source.getByRole('button', {
    name: 'Check and register the WGS source',
  });
  await expect(register).toBeDisabled();
  await source
    .getByRole('checkbox', { name: /This is a complete whole-genome callset/ })
    .check();
  await source.getByLabel('Absent positions').selectOption('reference');
  await source
    .getByRole('checkbox', { name: /Accept unfiltered calls/ })
    .check();
  await register.click();
  await expect(source.getByText('WGS source registered.')).toBeVisible();
  const calculate = page.getByRole('button', {
    name: 'Calculate PCA projection',
  });
  await expect(calculate).toBeEnabled();
  await calculate.click();
  const ancestry = page
    .locator('section')
    .filter({
      has: page.getByRole('heading', { name: 'Genetic similarity (PCA)' }),
    });
  const table = ancestry.getByRole('table', {
    name: "Projections and marker quality of the family’s individuals",
  });
  await expect(table).toBeVisible({ timeout: 30_000 });
  await expect(
    table.getByRole('row', { name: new RegExp(sample) })
  ).toContainText('Insufficient data — not plotted');
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
