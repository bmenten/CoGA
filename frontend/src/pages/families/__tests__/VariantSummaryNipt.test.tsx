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
  Array.from({ length: 100 }, (_, index) => (index === bin ? count : 0));

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
  vaf_histogram: Array(100).fill(0),
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
      vaf_histogram: histogram(6, 40),
    }),
    group('1', 'hom_ref', 'het', 2, { expected_vaf: 0.06, vaf_histogram: histogram(6, 2) }),
    group('2', 'het', 'hom_ref', 30, { expected_vaf: 0.429, vaf_histogram: histogram(42, 30) }),
    group('3', 'het', 'het', 40, { expected_vaf: 0.489, vaf_histogram: histogram(48, 40) }),
    group('4', 'het', 'hom_alt', 10, { expected_vaf: 0.549, vaf_histogram: histogram(54, 10) }),
    group('5', 'hom_alt', 'het', 8, { expected_vaf: 0.94, vaf_histogram: histogram(94, 8) }),
    group('6', 'hom_alt', 'hom_alt', 30, { expected_vaf: 1, vaf_histogram: histogram(99, 30) }),
  ],
  not_classified: { sex_chromosome: 5 },
  vaf_bin_width: 0.01,
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
  artifacts: { listed: 120, in_family: 9, removed: 7, protected: { clinvar: 1, common: 1 } },
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
      [
        'Artefact list',
        '7 removed',
        '9 of the 120 alleles the assay lists are in this family; 2 kept in (ClinVar P/LP 1, common 1)',
      ],
    ]);
  });

  it('says so when the assay’s artefact list is empty', async () => {
    serve({ nipt: { ...STATS, artifacts: { listed: 0, in_family: 0, removed: 0, protected: {} } } });
    renderPage();
    const section = await niptSection();
    const card = within(section).getByText('Artefact list').closest('.stat-card') as HTMLElement;
    expect(card).toHaveTextContent('Empty');
    expect(card).toHaveTextContent('The assay’s list holds no allele: nothing was removed.');
  });

  it('lists the groups one under another, by the mother’s genotype and then the fetus’s', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const table = within(section).getByRole('table', {
      name: "Plasma sites by the mother's and the fetus's genotype",
    });
    expect(rowTexts(table)).toEqual([
      ['Fetus', 'Group', 'Sites', 'Share', 'Expected VAF', 'Observed VAF', 'Confidence ≥ 0.99'],
      ['Mother hom ref', '51', '30.2%', ''],
      ['Hom ref', '8 · Paternal hom-alt, absent (FN)', '1', '0.6%', '—', '—', '100.0%'],
      ['Hom ref', "Father's het allele, not inherited", '8', '4.7%', '—', '—', '100.0%'],
      ['Het', '7 · Paternal, transmitted', '40', '23.7%', '0.060', '0.061', '75.0%'],
      ['Het', '1 · De novo in fetus', '2', '1.2%', '0.060', '—', '100.0%'],
      ['Mother het', '80', '47.3%', ''],
      ['Hom ref', '2 · Maternal het, not inherited', '30', '17.8%', '0.429', '—', '100.0%'],
      ['Het', '3 · Maternal het, inherited', '40', '23.7%', '0.489', '—', '100.0%'],
      ['Hom alt', '4 · Maternal het → hom fetus', '10', '5.9%', '0.549', '—', '100.0%'],
      ['Mother hom alt', '38', '22.5%', ''],
      ['Het', '5 · Maternal hom, het fetus', '8', '4.7%', '0.940', '—', '100.0%'],
      ['Hom alt', '6 · Maternal & fetal hom', '30', '17.8%', '1.000', '—', '100.0%'],
      ['All groups', '169', ''],
    ]);
    // Each share is drawn as a bar of its group's colour.
    const bars = Array.from(table.querySelectorAll<HTMLElement>('.nipt-share-fill')).map((bar) => bar.style.width);
    expect(bars.slice(0, 2)).toEqual([`${(51 / 169) * 100}%`, `${(1 / 169) * 100}%`]);
  });

  it("splits each group by the father's genotype or the variant class on demand", async () => {
    serve();
    renderPage();
    const section = await niptSection();
    expect(within(section).queryByText(/father het 28/)).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole('button', { name: "Father's genotype" }));
    expect(within(section).getByRole('columnheader', { name: 'By the father’s genotype' })).toBeInTheDocument();
    expect(within(section).getByText('father het 28 · father hom alt 12')).toBeInTheDocument();
    fireEvent.click(within(section).getByRole('button', { name: 'Variant class' }));
    expect(within(section).getByText('SNV 38 · MNV 2')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Variant class' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('draws each group with a plasma call in a row of its own scale, in the order its category expects', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const chart = within(section).getByRole('img', { name: /one row per group, each scaled to its own tallest bin/ });
    // The father's missing alleles have no plasma call and no row.
    const rows = Array.from(chart.querySelectorAll('[data-row]'));
    expect(rows.map((row) => row.getAttribute('data-row'))).toEqual(['7', '1', '2', '3', '4', '5', '6']);
    expect(rows[0]).toHaveTextContent('7 · Paternal, transmitted');
    expect(rows[0]).toHaveTextContent('40 sites · peak 40');
    // Each row's tallest bar fills the row, whatever the group's size.
    const height = (row: Element) => Number(row.querySelector('rect[data-bin]')?.getAttribute('height'));
    expect(height(rows[1])).toBe(height(rows[0]));
    // Its expected fraction (dashed) and its observed median (triangle).
    expect(rows[0].querySelectorAll('.nipt-vaf-expected line')).toHaveLength(1);
    expect(rows[0].querySelectorAll('.nipt-row-median')).toHaveLength(1);
    // The scale toggle belongs to the stacked view only.
    expect(within(section).queryByRole('button', { name: 'Log scale' })).not.toBeInTheDocument();
  });

  it('lists a hovered row bin’s sites with its group’s expected and observed fraction', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    const chart = within(section).getByRole('img', { name: /one row per group/ });
    const row = chart.querySelector('[data-row="7"]') as Element;
    fireEvent.mouseMove(row.querySelectorAll('.nipt-vaf-hit')[6], { clientX: 100, clientY: 100 });
    const tip = document.body.querySelector('.viz-tooltip') as HTMLElement;
    expect(tip).toHaveTextContent('0.06–0.07 · 40 sites');
    expect(tip).toHaveTextContent('7 · Paternal, transmitted40');
    expect(tip).not.toHaveTextContent('De novo');
    expect(tip).toHaveTextContent('Expected 0.060 · observed median 0.061');
    fireEvent.mouseLeave(chart);
    expect(document.body.querySelector('.viz-tooltip')).toBeNull();
  });

  it('stacks all sites by group in bins of 0.01, with a numbered line where each category is expected', async () => {
    serve();
    renderPage();
    const section = await niptSection();
    fireEvent.click(within(section).getByRole('button', { name: 'All sites' }));
    const chart = within(section).getByRole('img', { name: /Plasma allele fractions of 160 sites in bins of 0.01/ });
    // One bar per group and bin with sites; the paternal and de novo sites share a bin.
    const bars = Array.from(chart.querySelectorAll('rect[data-group]')).map((bar) => [
      bar.getAttribute('data-bin'),
      bar.getAttribute('data-group'),
    ]);
    expect(bars).toEqual([
      ['6', '7'],
      ['6', '1'],
      ['42', '2'],
      ['48', '3'],
      ['54', '4'],
      ['94', '5'],
      ['99', '6'],
    ]);
    // De novo and paternal are expected at the same fraction (FF/2): one line names both.
    const labels = Array.from(chart.querySelectorAll('.nipt-vaf-expected-label')).map((label) => label.textContent);
    expect(labels).toEqual(['7/1', '2', '3', '4', '5', '6']);
    // No bin stands far above the others here: nothing is cut.
    expect(chart.querySelectorAll('.nipt-vaf-cut')).toHaveLength(0);
    fireEvent.mouseMove(chart.querySelectorAll('.nipt-vaf-hit')[6], { clientX: 100, clientY: 100 });
    const tip = document.body.querySelector('.viz-tooltip') as HTMLElement;
    expect(tip).toHaveTextContent('0.06–0.07 · 42 sites');
    expect(Array.from(tip.querySelectorAll('.nipt-tip-row')).map((row) => row.textContent)).toEqual([
      '7 · Paternal, transmitted40',
      '1 · De novo in fetus2',
    ]);
    fireEvent.click(within(section).getByRole('button', { name: 'Log scale' }));
    expect(within(chart).getByText('Sites (log scale)')).toBeInTheDocument();
  });

  it('cuts a hom/hom peak far above the other bins, and gives its full count', async () => {
    const tall = STATS.groups.map((group) =>
      group.key === '6' ? { ...group, sites: 900, vaf_histogram: histogram(99, 900) } : group
    );
    serve({ nipt: { ...STATS, groups: tall } });
    renderPage();
    const section = await niptSection();
    fireEvent.click(within(section).getByRole('button', { name: 'All sites' }));
    const chart = within(section).getByRole('img', { name: /stacked by group/ });
    // The axis reaches a little above the tallest other bin (42), not to 900.
    const ticks = Array.from(chart.querySelectorAll('text[text-anchor="end"]'))
      .map((text) => text.textContent ?? '')
      .filter((text) => /^\d+$/.test(text));
    expect(ticks).toEqual(['0', '10', '20', '30', '40', '50', '60']);
    expect(chart.querySelectorAll('.nipt-vaf-cut')).toHaveLength(1);
    expect(chart.querySelector('.nipt-vaf-cut-label')).toHaveTextContent('900 ↑');
    expect(within(section).getByRole('note')).toHaveTextContent(
      'The hom/hom peak is cut at 60 sites so that the other bins can be read: its tallest bin, 0.99–1.00, holds 900. The log scale shows it whole.'
    );
    // The cut bar reaches the top of the axis and no further.
    const bar = chart.querySelector('rect[data-group="6"]') as Element;
    expect(Number(bar.getAttribute('y'))).toBe(30);
    // On the log scale nothing is cut.
    fireEvent.click(within(section).getByRole('button', { name: 'Log scale' }));
    expect(chart.querySelectorAll('.nipt-vaf-cut')).toHaveLength(0);
    expect(within(section).queryByRole('note')).not.toBeInTheDocument();
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
      ['Removed by the artefact list, after the quality filter', '6'],
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
