// Pins the variant summary's NIPT-M tab (a monogenic NIPT family's plasma sites by the mother's and
// the fetus's genotype, their allele fractions, transmission per autosome with its marks, the calls
// and the father's genotypes) and the small-variant tab's handling of the NIPT plasma and father,
// whose GT is not a genotype. Synthetic families only.
import { fireEvent, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyVariantSummaryPage from '../FamilyVariantSummaryPage';
import api from '../../../lib/api';
import type {
  NiptStatisticsGroupOut,
  NiptVariantStatisticsOut,
} from '../../../lib/apiSchema.generated';
import { renderFamilyRoute } from '../../../test/familyRoute';
import {
  failWith,
  sampleStats,
  serveVariantSummary,
  smallStats,
  type SummaryAnswers,
} from '../../../test/variantSummaryRoutes';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(api.get);

const NIPT_FAMILY = {
  family_id: 'F1',
  pedigree: 'F1 FETUS FATHER PLASMA 0 2\nF1 FATHER 0 0 1 1\nF1 PLASMA 0 0 2 1',
  metadata: { analysis_type: 'monogenic_nipt' },
  members: [
    { sample_id: 'FETUS', role: 'embryo', affected: false, sex: 'unknown' },
    { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
    { sample_id: 'PLASMA', role: 'mother', affected: false, sex: 'female' },
  ],
};

const histogram = (bin: number, count: number): number[] =>
  Array.from({ length: 50 }, (_, index) => (index === bin ? count : 0));

const group = (
  key: string,
  mother: NiptStatisticsGroupOut['maternal_genotype'],
  fetus: NiptStatisticsGroupOut['fetal_genotype'],
  sites: number,
  overrides: Partial<NiptStatisticsGroupOut> = {}
): NiptStatisticsGroupOut => ({
  key,
  category: key === 'paternal_not_transmitted' ? null : Number(key),
  maternal_genotype: mother,
  fetal_genotype: fetus,
  sites,
  expected_vaf: null,
  observed_median_vaf: null,
  high_confidence: sites,
  ambiguous: 0,
  by_father: {},
  by_variant_class: {},
  vaf_histogram: Array(50).fill(0),
  ...overrides,
});

const STATS: NiptVariantStatisticsOut = {
  family_id: 'F1',
  fetal_fraction: {
    ff: 0.12,
    ff_computed: 0.12,
    ff_median: 0.12,
    ci_low: 0.11,
    ci_high: 0.13,
    n_sites: 40,
    method: 'category7_pooled',
    low_confidence: false,
    vaf_q05: 0.04,
    vaf_q95: 0.08,
  },
  qc: {
    de_novo_window: null,
    paternity: {
      hom_alt_transmitted: 12,
      hom_alt_not_transmitted: 0,
      het_transmitted: 20,
      het_not_transmitted: 21,
      hom_alt_rate: 1,
      het_rate: 0.49,
      status: 'pass',
      message: 'ok',
    },
    fetal_fraction_status: 'pass',
    fetal_sex: {
      call: 'female',
      status: 'pass',
      paternal_x: 'female',
      x_transmitted: 9,
      x_not_transmitted: 1,
      informative_sites: 10,
      chry_profile: null,
      y_ratio: null,
      x_ratio: null,
      chry_fetal_fraction: null,
    },
    plasma_profile_status: 'unknown',
    plasma_profile_message: '',
    target_coverage: null,
    quality_failures: { few_alt_reads: 30, strand_bias: 4 },
    model: {
      reference: 'R NIPT-M v0.5.1',
      overdispersion: 0.0037,
      maternal_het_bias: -0.011,
      min_quality: 20,
      min_alt_reads: 5,
      min_vaf: 0.01,
      vaf_ff_fraction: 0.25,
      max_strand_bias_fs: 20,
      father_het_min_vaf: 0.2,
      father_hom_alt_min_vaf: 0.8,
      min_father_depth: 20,
    },
  },
  filter_counts: { total_in: 200, passed: 160, failed_quality: 34, failed_artifact: 6, paternal_only: 9 },
  caller_filters: { PASS: 110, clustered_events: 60, weak_evidence: 40 },
  classified_sites: 169,
  groups: [
    group('8', 'hom_ref', 'hom_ref', 1),
    group('paternal_not_transmitted', 'hom_ref', 'hom_ref', 8),
    group('7', 'hom_ref', 'het', 40, {
      expected_vaf: 0.06,
      observed_median_vaf: 0.061,
      high_confidence: 30,
      by_father: { het: 28, hom_alt: 12 },
      by_variant_class: { SNV: 38, MNV: 2 },
      vaf_histogram: histogram(3, 40),
    }),
    group('1', 'hom_ref', 'het', 2, { expected_vaf: 0.06, vaf_histogram: histogram(3, 2) }),
    group('2', 'het', 'hom_ref', 30, { expected_vaf: 0.429, vaf_histogram: histogram(21, 30) }),
    group('3', 'het', 'het', 40, { expected_vaf: 0.489, vaf_histogram: histogram(24, 40) }),
    group('4', 'het', 'hom_alt', 10, { expected_vaf: 0.549, vaf_histogram: histogram(27, 10) }),
    group('5', 'hom_alt', 'het', 8, { expected_vaf: 0.94, vaf_histogram: histogram(47, 8) }),
    group('6', 'hom_alt', 'hom_alt', 30, { expected_vaf: 1, vaf_histogram: histogram(49, 30) }),
  ],
  not_classified: { sex_chromosome: 5 },
  vaf_bin_width: 0.02,
  chromosomes: [
    {
      chrom: '1',
      paternal_het_transmitted: 11,
      paternal_het_not_transmitted: 9,
      paternal_hom_alt_transmitted: 6,
      paternal_hom_alt_not_transmitted: 0,
      maternal_het_inherited: 10,
      maternal_het_not_inherited: 10,
      fetal_hom_alt: 20,
    marks: [],
    },
    {
      chrom: '2',
      paternal_het_transmitted: 0,
      paternal_het_not_transmitted: 16,
      paternal_hom_alt_transmitted: 0,
      paternal_hom_alt_not_transmitted: 6,
      maternal_het_inherited: 12,
      maternal_het_not_inherited: 8,
      fetal_hom_alt: 20,
      marks: ['no_paternal_alleles'],
    },
  ],
  chromosome_mark_min_sites: 20,
  father_genotypes: { het: 60, hom_alt: 40, absent: 70, low_vaf: 3 },
};

const serve = (answers: SummaryAnswers = {}) =>
  serveVariantSummary(mockedGet, 'F1', {
    family: NIPT_FAMILY,
    nipt: STATS,
    ...answers,
    has: { structural: false, small: true, ...answers.has },
  });

const renderPage = (url = '/families/F1/variant-summary') =>
  renderFamilyRoute(
    '/families/:familyId/variant-summary',
    url,
    <FamilyVariantSummaryPage />
  );

/** The NIPT-M section, once its statistics are in (its facts shown). */
const niptSection = async () => {
  const heading = await screen.findByRole('heading', { name: 'NIPT-M', level: 2 });
  const section = heading.closest('section') as HTMLElement;
  await within(section).findByText('Sites grouped');
  return section;
};

const rowTexts = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .map((row) => Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent));

describe('the variant summary of a monogenic NIPT family', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('opens on a NIPT-M tab, before the small variants, and asks its statistics', async () => {
    serve();
    renderPage();
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['NIPT-M', 'Small variants (SNV/indel)']);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    await niptSection();
    expect(mockedGet).toHaveBeenCalledWith('/families/F1/nipt/statistics', { params: undefined });
  });

  it('has no NIPT-M tab for a family that is no NIPT family', async () => {
    serve({ family: { ...NIPT_FAMILY, metadata: {} } });
    renderPage();
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Small variants (SNV/indel)']);
  });

  it('heads the tab with the fetal fraction, the grouped sites, the fetal sex and paternity', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const facts = Array.from(section.querySelectorAll('.stat-card')).map((card) => [
      card.querySelector('.stat-label')?.textContent,
      card.querySelector('.stat-value')?.textContent,
      card.querySelector('.table-subtle')?.textContent?.trim(),
    ]);
    expect(facts).toEqual([
      ['Fetal fraction', '12.0%', '95% CI 11.0%–13.0% · 40 category-7 sites'],
      ['Sites grouped', '169', 'of 200 plasma calls and 9 paternal-only sites'],
      ['Fetal sex', 'Female', 'paternal X seen at 9 of 10 sites'],
      ['Paternity', 'Pass', 'hom-alt alleles inherited 12 of 12'],
    ]);
  });

  it('draws the groups as a mother × fetus matrix, the combinations Mendel rules out not modelled', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const matrix = within(section).getByRole('table', {
      name: "Plasma sites by the mother's and the fetus's genotype",
    });
    const rows = within(matrix).getAllByRole('row');
    expect(Array.from(rows[0].querySelectorAll('th')).map((cell) => cell.textContent)).toEqual([
      'Mother ↓ · fetus →',
      'Fetus hom ref',
      'Fetus het',
      'Fetus hom alt',
    ]);
    const cells = (row: number) => Array.from(rows[row].querySelectorAll('td'));
    // The mother hom ref: the father's allele missing or not inherited; his allele or a de novo.
    expect(cells(1)[0]).toHaveTextContent(/8 · Paternal hom-alt, absent \(FN\)\s*1/);
    expect(cells(1)[0]).toHaveTextContent("Father's het allele, not inherited");
    expect(cells(1)[1]).toHaveTextContent('7 · Paternal, transmitted40 23.7%');
    expect(cells(1)[1]).toHaveTextContent('Expected 0.060 · observed 0.061 · 75.0% at confidence ≥ 0.99');
    expect(cells(1)[1]).toHaveTextContent('1 · De novo in fetus');
    expect(cells(1)[2]).toHaveTextContent('Not modelled');
    // The mother het: the fetus hom ref, het or hom alt.
    expect(cells(2).map((cell) => cell.querySelector('.nipt-matrix-count')?.textContent)).toEqual([
      '30 17.8%',
      '40 23.7%',
      '10 5.9%',
    ]);
    // The mother hom alt: no hom-ref fetus.
    expect(cells(3)[0]).toHaveTextContent('Not modelled');
    expect(cells(3)[2]).toHaveTextContent('6 · Maternal & fetal hom30');
  });

  it("splits each group by the father's genotype or the variant class on demand", async () => {
    serve();
    renderPage();
    const section = await niptSection();
    expect(within(section).queryByText(/father het 28/)).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole('button', { name: "Father's genotype" }));
    expect(within(section).getByText('father het 28 · father hom alt 12')).toBeInTheDocument();
    fireEvent.click(within(section).getByRole('button', { name: 'Variant class' }));
    expect(within(section).getByText('SNV 38 · MNV 2')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Variant class' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('stacks the plasma allele fractions by group, with a dashed line where each category is expected', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const chart = within(section).getByRole('img', { name: /Plasma allele fractions of 160 sites/ });
    // One bar per group and bin with sites; the paternal and de novo sites share a bin.
    expect(chart.querySelectorAll('rect')).toHaveLength(7);
    expect(chart.querySelectorAll('line[stroke-dasharray]')).toHaveLength(7);
    const titles = Array.from(chart.querySelectorAll('rect title')).map((title) => title.textContent);
    expect(titles).toContain('7 · Paternal, transmitted: 40 sites at 0.06–0.08');
    fireEvent.click(within(section).getByRole('button', { name: 'Log scale' }));
    expect(within(section).getByRole('button', { name: 'Linear scale' })).toBeInTheDocument();
  });

  it('counts transmission per autosome and marks one without the father’s alleles', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const table = within(section).getByRole('table', { name: 'Transmission by autosome' });
    expect(rowTexts(table)).toEqual([
      [
        'Chromosome',
        'Father’s het alleles inherited',
        'Father’s hom-alt alleles inherited',
        'Mother’s het alleles inherited',
        'Fetus hom alt',
        'Marked',
      ],
      ['1', '55.0% (11 of 20)', '100.0% (6 of 6)', '50.0% (10 of 20)', '20', ''],
      ['2', '0.0% (0 of 16)', '0.0% (0 of 6)', '60.0% (12 of 20)', '20', 'No paternal allele'],
      ['Autosomes', '30.6% (11 of 36)', '50.0% (6 of 12)', '55.0% (22 of 40)', '40', ''],
    ]);
    expect(within(section).getByText(/over at least 20 sites is marked/)).toBeInTheDocument();
  });

  it("lists the plasma calls' funnel, the caller's FILTER and the father's genotypes", async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const plasma = within(section).getByRole('heading', { name: 'Plasma calls', level: 4 }).nextElementSibling as HTMLElement;
    expect(rowTexts(plasma)).toEqual([
      ['Plasma calls read', '200'],
      ['Failed the quality filter', '34'],
      ['too few alt reads', '30'],
      ['strand bias', '4'],
      ['On the artefact list', '6'],
      ['Passed', '160'],
      ["Father's alleles without a plasma call", '9'],
      ['Sites in the groups above', '169'],
      ['Not grouped: on a sex chromosome (the fetal sex reads chrX)', '5'],
    ]);
    const caller = within(section).getByRole('heading', { name: 'Caller FILTER of the plasma calls read', level: 4 })
      .nextElementSibling as HTMLElement;
    expect(rowTexts(caller)).toEqual([
      ['PASS', '110 (55.0%)'],
      ['clustered_events', '60 (30.0%)'],
      ['weak_evidence', '40 (20.0%)'],
    ]);
    const father = within(section).getByRole('heading', { name: 'Father’s genotype at the sites read', level: 4 })
      .nextElementSibling as HTMLElement;
    expect(rowTexts(father)).toEqual([
      ['no call (reference)', '70'],
      ['het', '60'],
      ['hom-alt', '40'],
      ['low-level signal', '3'],
    ]);
  });

  it('says so when the NIPT-M statistics could not be loaded', async () => {
    serve({ nipt: failWith(new Error('boom')) });
    renderPage();
    const heading = await screen.findByRole('heading', { name: 'NIPT-M', level: 2 });
    const section = heading.closest('section') as HTMLElement;
    expect(await within(section).findByRole('alert')).toBeInTheDocument();
    expect(within(section).queryByText('Sites grouped')).not.toBeInTheDocument();
  });

  it("leaves the plasma's and the father's genotype counts out of the small-variant tab", async () => {
    serve({
      small: smallStats({
        sources: ['nipt'],
        total_variants: 300,
        samples: [
          sampleStats('FATHER', {
            non_ref_count: 120,
            het_count: 120,
            passing_count: 60,
            chromosomes: [{ chrom: '1', het_count: 120, hom_alt_count: 0 }],
          }),
          sampleStats('PLASMA', {
            non_ref_count: 200,
            het_count: 200,
            passing_count: 110,
            chromosomes: [{ chrom: '1', het_count: 200, hom_alt_count: 0 }],
          }),
        ],
        nipt_samples: ['PLASMA', 'FATHER'],
      }),
    });
    renderPage('/families/F1/variant-summary#small-variants');
    const heading = await screen.findByRole('heading', { name: 'Small variants (SNV/indel)', level: 2 });
    const section = heading.closest('section') as HTMLElement;
    expect(await within(section).findByRole('note')).toHaveTextContent(
      /PLASMA and FATHER are this monogenic NIPT family’s plasma and father, whose GT is not a genotype/
    );
    const perSample = within(section).getAllByRole('table')[0];
    const rows = rowTexts(perSample);
    expect(rows.find((row) => row[0] === 'Heterozygous')).toEqual(['Heterozygous', '—', '—']);
    expect(rows.find((row) => row[0] === 'Homozygous alt')).toEqual(['Homozygous alt', '—', '—']);
    expect(rows.find((row) => row[0] === 'Het / hom ratio')).toEqual(['Het / hom ratio', '—', '—']);
    expect(rows.find((row) => row[0] === 'Variants')).toEqual(['Variants', '120', '200']);
    expect(rows.find((row) => row[0] === 'Passed FILTER')).toEqual(['Passed FILTER', '50.0 %', '55.0 %']);
    // No sample is left with genotypes of its own for the per-chromosome table.
    expect(within(section).getByText(/No sample here has genotypes of its own to count/)).toBeInTheDocument();
  });
});
