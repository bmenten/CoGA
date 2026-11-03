import { expect, test } from './fixtures';
import { E2E_EMAIL, E2E_PASSWORD, login } from './helpers';

test('registers ancestry input and projects a family without any PGS module', async ({ page }) => {
  await login(page);
  const token = await page.request.post('/api/auth/login', { data: { email: E2E_EMAIL, password: E2E_PASSWORD } });
  const auth = { Authorization: `Bearer ${(await token.json()).access_token}` };
  const projects = await (await page.request.get('/api/projects', { headers: auth })).json();
  const project = projects.find((item: { assembly_name?: string }) => item.assembly_name === 'GRCh38');
  const projectId = typeof project._id === 'object' ? project._id.$oid : project._id;
  const family = `ANCESTRY_BROWSER_${Date.now().toString(36)}`;
  const sample = `PROBAND_${family}`;
  expect((await page.request.post('/api/ped/upload', { headers: auth, params: { project_id: projectId },
    multipart: { file: { name: 'synthetic.ped', mimeType: 'text/plain', buffer: Buffer.from(`${family} ${sample} 0 0 1 1\n`) } } })).ok()).toBeTruthy();
  const vcf = `##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t${sample}\n1\t10\t.\tA\tG\t50\t.\t.\tGT\t0/1\n`;
  expect((await page.request.post(`/api/families/${family}/small-variants/upload`, { headers: auth,
    params: { source_format: 'clair3' }, multipart: { file: { name: 'synthetic.vcf', mimeType: 'text/plain', buffer: Buffer.from(vcf) } } })).ok()).toBeTruthy();
  await page.goto(`/families/${family}/ancestry?project_id=${encodeURIComponent(projectId)}`);
  await expect(page.getByRole('heading', { name: 'Ancestry', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'PGS', exact: true })).toHaveCount(0);
  const source = page.getByRole('region', { name: 'WGS source', exact: true });
  const register = source.getByRole('button', { name: 'Verify and register WGS source' });
  await expect(register).toBeDisabled();
  await source.getByRole('checkbox', { name: /This is a complete whole-genome callset/ }).check();
  await source.getByLabel('Absent positions').selectOption('reference');
  await source.getByRole('checkbox', { name: /Allow unfiltered/ }).check();
  await register.click();
  const calculate = page.getByRole('button', { name: 'Calculate ancestry for this family' });
  await expect(calculate).toBeEnabled();
  await calculate.click();
  const ancestry = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Genetic ancestry · PCA' }) });
  await expect(ancestry.locator('svg [data-sample]')).toHaveCount(1, { timeout: 30_000 });
  await expect(ancestry.locator('circle[data-population]')).toHaveCount(2504);
  await expect(ancestry.getByRole('table', { name: 'Individual ancestry projections and marker quality' })).toBeVisible();
  await ancestry.getByLabel('Vertical axis').selectOption('2');
  await expect(ancestry.locator('svg desc')).toContainText('vertical PC3');
  await ancestry.getByRole('checkbox', { name: /EUR · European/ }).uncheck();
  await expect(ancestry.locator('circle[data-population="EUR"]')).toHaveCount(0);
  await ancestry.getByLabel('Center view').selectOption(sample);
  await expect(ancestry.getByLabel('Zoom 3.0×')).toHaveValue('3');
  await ancestry.getByRole('button', { name: 'Reset view' }).click();
  await page.screenshot({ path: test.info().outputPath('ancestry-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: test.info().outputPath('ancestry-mobile.png'), fullPage: true });
  for (const path of ['/api/pgs/models', `/api/families/${family}/pgs`]) {
    expect((await page.request.get(path, { headers: auth })).status()).toBe(404);
  }
});
