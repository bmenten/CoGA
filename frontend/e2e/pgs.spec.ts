import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from './fixtures';
import { E2E_EMAIL, E2E_PASSWORD, login } from './helpers';

const weights = '#pgs_id=PGS005091\n#genome_build=GRCh38\nchr_name\tchr_position\teffect_allele\tother_allele\tref_allele\teffect_weight\n1\t10\tG\tA\tA\t0.5\n1\t20\tC\tT\tC\t-1\n';

test('administers PGS, imports full WGS, shows QC and history, and recalculates a new version manually', async ({ page }) => {
  test.setTimeout(120_000);
  const suffix = Date.now().toString(36);
  const family = `PGS_BROWSER_${suffix}`;
  const proband = `PROBAND_${suffix}`;
  const mother = `MOTHER_${suffix}`;
  const father = `FATHER1_${suffix}`;
  const hostRoot = process.env.PGS_E2E_HOST_PACKAGE_ROOT || path.join(tmpdir(), 'coga-pgs-browser-e2e');
  await mkdir(hostRoot, { recursive: true });
  const folder = await mkdtemp(path.join(hostRoot, 'pgs-browser-'));
  const serverFolder = process.env.PGS_E2E_SERVER_PACKAGE_ROOT
    ? path.join(process.env.PGS_E2E_SERVER_PACKAGE_ROOT, path.basename(folder)) : folder;
  await writeFile(path.join(folder, 'family.ped'), `${family} ${proband} ${father} ${mother} 1 2\n${family} ${mother} 0 0 2 1\n${family} ${father} 0 0 1 1\n`);
  await writeFile(path.join(folder, 'family.vcf'), '##fileformat=VCFv4.2\n##reference=GRCh38\n##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\tFATHER1\n1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\t1/1\t./.\n'.replace('PROBAND', proband).replace('MOTHER', mother).replace('FATHER1', father));
  await writeFile(path.join(folder, 'manifest.json'), JSON.stringify({ schema_version: 1, family_id: family, ped: 'family.ped',
    metadata: { pgs: { full_wgs: true, genome_build: 'GRCh38', absent_record_is_hom_ref: true } },
    datasets: { snv: { family_vcf: 'family.vcf', source_format: 'clair3' } } }));

  await login(page);
  const signedIn = await page.request.post('/api/auth/login', { data: { email: E2E_EMAIL, password: E2E_PASSWORD } });
  expect(signedIn.ok()).toBeTruthy();
  const auth = { Authorization: `Bearer ${(await signedIn.json()).access_token}` };
  const projectsResponse = await page.request.get('/api/projects', { headers: auth });
  const projects = await projectsResponse.json();
  const project = projects.find((p: { assembly_name?: string }) => p.assembly_name === 'GRCh38') || projects[0];
  const projectId: string = typeof project._id === 'object' ? project._id.$oid : project._id;

  async function publish(label: string, text: string) {
    await page.goto('/admin/reference/pgs');
    await page.getByLabel('PGS ID').selectOption('PGS005091');
    await page.getByLabel('Version label').fill(label);
    await page.getByLabel('Study citation').fill('Synthetic browser study; not real weights');
    await page.getByLabel('Source URL').fill('https://www.pgscatalog.org/score/PGS005091/');
    await page.getByLabel('Scoring file').setInputFiles({ name: 'synthetic.tsv', mimeType: 'text/tab-separated-values', buffer: Buffer.from(text) });
    await page.getByRole('button', { name: 'Save model version' }).click();
    await expect(page.getByRole('status')).toContainText('Existing families are not recalculated automatically');
  }

  await publish(`browser-v1-${suffix}`, weights);
  const reactivate = page.getByRole('button', { name: 'Reactivate PGS005091' });
  if (await reactivate.isVisible()) await reactivate.click();
  const imported = await page.request.post('/api/family-imports', { headers: auth,
    data: { folder_path: serverFolder, project_id: projectId, dry_run: false, conflict_mode: 'overwrite' } });
  expect(imported.ok()).toBeTruthy();
  const jobBody = await imported.json();
  const jobId = typeof jobBody._id === 'object' ? jobBody._id.$oid : jobBody._id;
  await expect.poll(async () => (await (await page.request.get(`/api/family-imports/${jobId}`, { headers: auth })).json()).status,
    { timeout: 60_000 }).toBe('completed');
  const familyPath = `/families/${family}/pgs?project_id=${encodeURIComponent(projectId)}`;
  await page.goto(familyPath);
  await expect(page.getByText('Incomplete — no raw score')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('-1.500000000', { exact: true })).toBeVisible();
  await expect(page.getByText(/no call: 1/)).toBeVisible();
  await expect(page.getByText(/total: 2; called: 1; assumed ref: 1/).first()).toBeVisible();
  const oldRows = await page.locator('tbody tr').count();

  await publish(`browser-v2-${suffix}`, weights.replace('0.5', '1.0'));
  await page.goto(familyPath);
  await expect(page.getByText(/model version\(s\) await calculation/)).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(oldRows);
  await page.getByRole('button', { name: 'Calculate active models for this family' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(oldRows + 3, { timeout: 30_000 });
  await expect(page.getByText(`PGS005091 / browser-v1-${suffix}`).first()).toBeVisible();
  await expect(page.getByText(`PGS005091 / browser-v2-${suffix}`).first()).toBeVisible();
  await page.getByText(new RegExp(`Provenance: ${proband} / PGS005091 / browser-v2-${suffix}`)).click();
  await expect(page.locator('details[open]').getByText(/Calculation: coga-pgs-gt-v1/)).toBeVisible();

  await page.goto('/admin/reference/pgs');
  await page.getByRole('button', { name: 'Archive PGS005091' }).click();
  await expect(page.getByRole('button', { name: 'Reactivate PGS005091' })).toBeVisible();
  await page.goto(familyPath);
  await expect(page.getByText(`PGS005091 / browser-v1-${suffix} (archived)`).first()).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(oldRows + 3);
  await page.goto('/admin/reference/pgs');
  await page.getByRole('button', { name: 'Reactivate PGS005091' }).click();
  await expect(page.getByRole('button', { name: 'Archive PGS005091' })).toBeVisible();
});
