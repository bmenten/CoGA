// The conventional NIPT page: the plasma's automatic result per target, read first for
// chromosomes 21, 18 and 13, the sex chromosomes and segmental CNVs; the plasma's fetal fractions,
// fetal sex and resolution; each finding with a link to the chromosome view; and the bounds the
// rule applied, marked provisional until QA sets them. All values are synthetic.
import { screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import FamilyCniptPage from '../FamilyCniptPage';
import { renderFamilyRoute } from '../../../test/familyRoute';
import type { CniptSampleResultOut } from '../../../lib/apiSchema.generated';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../../../lib/api', () => ({ default: apiMock }));

vi.mock('../../../lib/reference', () => ({
  useProjectCatalog: () => ({ data: [] }),
  useFamilyReference: () => ({ assemblyName: 'GRCh38', projectId: undefined, isLoading: false }),
  formatResolvedReferenceLabel: () => 'GRCh38',
}));

const lowRisk = (target: string) => ({
  target,
  verdict: 'low_risk' as const,
  reasons: [],
  claimed: ['21', '18', '13'].includes(target),
});

const sample = (overrides: Partial<CniptSampleResultOut> = {}): CniptSampleResultOut => ({
  sample_id: 'CFDNA1',
  qc_file: 'CFDNA1_qc.json',
  status: 'result',
  no_result_reasons: [],
  fetal_fraction: { effective: 0.1, active: 0.085, active_se: 0.005, active_source: 'FUSED', ffy: null, ffx: 0.09, ffa: 0.088, ff_size: 0.07 },
  fetal_sex: 'F',
  sca: { call: 'XX', is_aneuploid: false, confidence: 0.999 },
  gestation: null,
  limits_of_detection: { lod_ff_35mb: 0.03, lod_ff_3mb: 0.08, segmental_assessed_from_mb: 3, statement: 'Segmental CNVs are assessed from 3 Mb.' },
  read_metrics: { effective_reads_kept: 12000000, usable_bins: 28000 },
  warnings: ['ccnv fragmentomics: WARN_HIGH_LEUKOCYTE_DNA'],
  targets: [
    ...Array.from({ length: 22 }, (_, index) => String(index + 1)).map((target) =>
      target === '21'
        ? { target, verdict: 'high_risk' as const, reasons: ['|z| 9.0 ≥ 3, concordant with the fetal fraction, origin FETAL_CONSTITUTIONAL'], claimed: true }
        : target === '7'
          ? { target, verdict: 'inconclusive' as const, reasons: ['|z| 2.7 is in the grey zone (2.5 to 3)'], claimed: false }
          : lowRisk(target),
    ),
    { target: 'sex_chromosomes', verdict: 'low_risk', reasons: ['ccnv calls XX (confidence 99.9%)'], claimed: true },
    { target: 'segmental', verdict: 'low_risk', reasons: ['Segmental CNVs are assessed from 3 Mb.'], claimed: false },
  ],
  findings: [
    {
      chrom: '21',
      start: 0,
      end: 46700000,
      size_mb: 46.7,
      direction: 'gain',
      ratio: 0.0702,
      zscore: 9.0,
      bin_count: 430,
      ccnv_call: 'gain',
      expected_ratio: 0.0704,
      implied_ff: 0.098,
      implied_ff_ratio: 0.98,
      discordant: false,
      discordance_reason: null,
      origin_call: 'FETAL_CONSTITUTIONAL',
      kind: 'chromosome',
      verdict: 'high_risk',
      reasons: ['|z| 9.0 ≥ 3, concordant with the fetal fraction, origin FETAL_CONSTITUTIONAL'],
    },
  ],
  bounds: [
    { key: 'cnipt.fetal_fraction', label: 'cNIPT: fetal fraction', unit: 'fraction', warn_value: null, error_value: 0.04, provisional: true },
    { key: 'cnipt.call_zscore', label: 'cNIPT: segment |z-score|', unit: 'z', warn_value: 2.5, error_value: 3, provisional: false },
  ],
  rule: { threshold_profile: 'nipt_conventional', whole_chromosome_share: 0.5, maternal_or_mosaic_origins: ['MATERNAL_MOSAIC'] },
  ...overrides,
});

const serve = (result: CniptSampleResultOut) =>
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          members: [{ sample_id: 'CFDNA1', role: 'mother', affected: false, sex: 'female' }],
          projects: [],
          metadata: { analyses: ['conventional_nipt'] },
        },
      });
    }
    if (url === '/families/F1/cnipt') {
      return Promise.resolve({ data: { family_id: 'F1', samples: [result] } });
    }
    return Promise.resolve({ data: {} });
  });

const renderPage = () => renderFamilyRoute('/families/:familyId/cnipt', '/families/F1/cnipt', <FamilyCniptPage />);

describe('FamilyCniptPage', () => {
  it('reads the result for chromosomes 21, 18, 13, the sex chromosomes and segmental CNVs first', async () => {
    serve(sample());
    renderPage();

    const result = await screen.findByRole('region', { name: 'Result of CFDNA1' });
    const [targets] = within(result).getAllByRole('table');
    const rows = within(targets).getAllByRole('row').slice(1);
    // The headline targets, then the one other autosome that is not low risk; each target the
    // intended purpose does not claim says so under its name.
    expect(rows.map((row) => within(row).getByRole('rowheader').firstChild?.textContent)).toEqual([
      'Chromosome 21 (T21)',
      'Chromosome 18 (T18)',
      'Chromosome 13 (T13)',
      'Sex chromosomes',
      'Segmental CNVs',
      'Chromosome 7',
    ]);
    expect(rows.map((row) => within(row).queryByText('outside the validated claim') !== null)).toEqual([
      false,
      false,
      false,
      false,
      true,
      true,
    ]);
    expect(within(result).getByText(/The validated claim covers trisomies 21, 18 and 13/)).toBeInTheDocument();
    expect(within(rows[0]).getByText('High risk')).toBeInTheDocument();
    expect(within(rows[0]).getByText(/concordant with the fetal fraction/)).toBeInTheDocument();
    expect(within(rows[5]).getByText('Inconclusive')).toBeInTheDocument();
    // The other autosomes, folded.
    expect(
      within(result).getByText(/Every other autosome \(18 low risk, 1 listed above; outside the validated claim\)/),
    ).toBeInTheDocument();
  });

  it('shows the plasma’s fetal fractions, sex, resolution and ccnv’s warnings', async () => {
    serve(sample());
    renderPage();

    const plasma = await screen.findByRole('region', { name: 'Sample CFDNA1' });
    expect(within(plasma).getByText('10.0%')).toBeInTheDocument();
    expect(within(plasma).getByText(/active 8\.5% ± 0\.5% \(FUSED\)/)).toBeInTheDocument();
    expect(within(plasma).getByText(/F · sex-chromosome call XX \(confidence 99\.90%\)/)).toBeInTheDocument();
    expect(within(plasma).getByText('Segmental CNVs are assessed from 3 Mb.')).toBeInTheDocument();
    expect(within(plasma).getByText('ccnv fragmentomics: WARN_HIGH_LEUKOCYTE_DNA')).toBeInTheDocument();
  });

  it('links each finding to the chromosome view, and marks provisional bounds', async () => {
    serve(sample());
    renderPage();

    const findings = await screen.findByRole('region', { name: 'Findings of CFDNA1' });
    expect(within(findings).getByRole('link', { name: 'chr21:0–46,700,000' })).toHaveAttribute(
      'href',
      '/families/F1/chromosome/21?start=0&end=46700000',
    );
    expect(within(findings).getByText('(0.98 × FF)')).toBeInTheDocument();
    const bounds = screen.getByRole('region', { name: 'Bounds of the rule' });
    expect(within(bounds).getByText(/Provisional bounds apply/)).toBeInTheDocument();
    const [fetal, zscore] = within(bounds).getAllByRole('row').slice(1);
    expect(within(fetal).getByText('provisional')).toBeInTheDocument();
    expect(within(fetal).getByText('4.0%')).toBeInTheDocument();
    expect(within(zscore).getByText('QA')).toBeInTheDocument();
  });

  it('says why there is no result', async () => {
    serve(
      sample({
        status: 'no_result',
        no_result_reasons: ['the fetal fraction (3.0%) is below 4.0%'],
        targets: [{ target: '21', verdict: 'no_result', reasons: ['the fetal fraction (3.0%) is below 4.0%'], claimed: true }],
        findings: [],
      }),
    );
    renderPage();

    const result = await screen.findByRole('region', { name: 'Result of CFDNA1' });
    const status = within(result).getByRole('status');
    expect(within(status).getByText('No result')).toBeInTheDocument();
    expect(within(status).getByText('the fetal fraction (3.0%) is below 4.0%')).toBeInTheDocument();
    expect(screen.getByText('No segment reaches the grey zone, and ccnv called none.')).toBeInTheDocument();
  });
});
