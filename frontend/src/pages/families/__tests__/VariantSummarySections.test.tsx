// Pins the variant summary's sections other than the structural variants (PROD-1): the small
// variants (per sample, consequences in the filter's groups, per chromosome, call quality, the
// Mendelian table) and the repeat, Paraphase and mtDNA counts. The page's shell and its SV
// section: FamilyVariantSummaryPage.test.tsx.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyVariantSummaryPage from '../FamilyVariantSummaryPage';
import { groupConsequences } from '../VariantSummarySmallVariants';
import api from '../../../lib/api';
import { renderFamilyRoute } from '../../../test/familyRoute';
import {
  failWith,
  sampleStats,
  serveVariantSummary,
  smallStats,
  type SummaryAnswers,
} from '../../../test/variantSummaryRoutes';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

// The page decides which counts each histogram draws, on which labels and scale.
vi.mock('../../../components/visualizations/Histogram', () => ({
  default: ({
    counts,
    binLabels,
    logScale,
    subject,
  }: {
    counts?: number[];
    binLabels?: string[];
    logScale?: boolean;
    subject?: string;
  }) => (
    <div
      data-testid="histogram"
      data-bins={binLabels?.join('|')}
      data-log-scale={String(logScale)}
      data-subject={subject}
    >
      {(counts ?? []).join(' ')}
    </div>
  ),
}));

const mockedGet = vi.mocked(api.get);

const serve = (answers: SummaryAnswers) =>
  serveVariantSummary(mockedGet, 'F1', {
    ...answers,
    has: { structural: false, ...answers.has },
  });

const renderPage = () =>
  renderFamilyRoute(
    '/families/:familyId/variant-summary',
    '/families/F1/variant-summary',
    <FamilyVariantSummaryPage />
  );

const readTable = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .map((row) =>
      Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent)
    );

const section = async (title: string) => {
  const heading = await screen.findByRole('heading', { name: title, level: 2 });
  return heading.closest('section') as HTMLElement;
};

/** The table that follows a sub-heading of the small-variant section. */
const tableAfter = (container: HTMLElement, heading: string) => {
  const title = within(container).getByRole('heading', {
    name: heading,
    level: 3,
  });
  let node: Element | null = title.nextElementSibling;
  while (node && !node.querySelector('table')) node = node.nextElementSibling;
  expect(node).not.toBeNull();
  return (node as Element).querySelector('table') as HTMLElement;
};

const bins = (counts: number[], edges: number[]) =>
  edges.map((low, i) => ({ low, count: counts[i] ?? 0 }));

const TRIO_STATS = smallStats({
  sources: ['deepvariant', 'clair3'],
  imputed_sources: ['glimpse2'],
  total_variants: 1200,
  snv_count: 1000,
  insertion_count: 90,
  deletion_count: 100,
  other_count: 10,
  samples: [
    sampleStats('S1', {
      non_ref_count: 900,
      het_count: 600,
      hom_alt_count: 300,
      snv_count: 750,
      insertion_count: 70,
      deletion_count: 75,
      other_count: 5,
      transition_count: 500,
      transversion_count: 250,
      passing_count: 810,
      consequences: [
        { consequence: 'stop_gained', count: 2 },
        { consequence: 'frameshift_variant', count: 1 },
        { consequence: 'missense_variant', count: 40 },
        { consequence: 'synonymous_variant', count: 30 },
        { consequence: 'intron_variant', count: 500 },
        { consequence: 'feature_truncation', count: 1 },
        { consequence: 'none', count: 326 },
      ],
      chromosomes: [
        { chrom: '1', het_count: 400, hom_alt_count: 100 },
        { chrom: '15', het_count: 0, hom_alt_count: 200 },
        { chrom: 'X', het_count: 200, hom_alt_count: 0 },
      ],
      genotype_quality_histogram: bins([5, 0, 895], [0, 10, 90]),
      genotype_quality_missing: 3,
      depth_histogram: bins([1, 899], [0, 30]),
      allele_balance_histogram: bins([10, 590], [0, 0.45]),
    }),
    sampleStats('S2', {
      non_ref_count: 500,
      het_count: 500,
      hom_alt_count: 0,
      consequences: [{ consequence: 'missense_variant', count: 25 }],
      chromosomes: [{ chrom: '1', het_count: 300, hom_alt_count: 0 }],
    }),
  ],
  trios: [
    {
      child: 'S1',
      father: 'S2',
      mother: 'S3',
      source: 'deepvariant',
      chromosomes: [
        {
          chrom: '1',
          sites: 500,
          not_from_father: 1,
          not_from_mother: 2,
          from_neither: 2,
        },
        {
          chrom: '15',
          sites: 250,
          not_from_father: 180,
          not_from_mother: 0,
          from_neither: 0,
        },
      ],
    },
  ],
});

describe('the small-variant section', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('says what it counted: the totals, the callsets, and that imputed genotypes and chrM are not', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    await within(small).findByRole('heading', { name: 'Per sample', level: 3 });
    expect(small).toHaveTextContent(
      '1,200 variants in the family: 1,000 SNVs, 90 insertions, 100 deletions and 10 other (MNV or complex). ' +
        'Counted from deepvariant, clair3; imputed genotypes (glimpse2) are not. chrM is summarised under mtDNA.'
    );
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/F1/small-variant-statistics',
      { params: undefined }
    );
  });

  it('lays each sample’s counts out with the het/hom and Ti/Tv ratios and the share that passed FILTER', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    await within(small).findByRole('heading', { name: 'Per sample', level: 3 });
    expect(readTable(tableAfter(small, 'Per sample'))).toEqual([
      ['Per sample', 'S1', 'S2'],
      ['Variants', '900', '500'],
      ['Heterozygous', '600', '500'],
      ['Homozygous alt', '300', '0'],
      // A ratio over zero is a dash, not Infinity.
      ['Het / hom ratio', '2.00', '–'],
      ['SNVs', '750', '0'],
      ['Insertions', '70', '0'],
      ['Deletions', '75', '0'],
      ['Other (MNV, complex)', '5', '0'],
      ['Transitions / transversions', '2.00', '–'],
      ['Passed FILTER', '90.0 %', '0.0 %'],
    ]);
  });

  it('groups the most severe consequences as the small-variant filter does, coding first', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    await within(small).findByRole('heading', {
      name: 'Consequences',
      level: 3,
    });
    expect(readTable(tableAfter(small, 'Consequences'))).toEqual([
      ['Most severe consequence', 'S1', 'S2'],
      ['In the coding sequence', '73', '25'],
      ['Nonsense', '2', '0'],
      ['Stop gained', '2', '0'],
      ['Frameshift', '1', '0'],
      ['Frameshift', '1', '0'],
      ['Missense', '40', '25'],
      ['Missense', '40', '25'],
      ['Synonymous', '30', '0'],
      ['Synonymous', '30', '0'],
      // A term no filter group names is listed under Other, by its own name.
      ['Other', '501', '0'],
      ['Intron variant', '500', '0'],
      ['feature truncation', '1', '0'],
      ['Not classified', '326', '0'],
      ['No consequence annotated', '326', '0'],
    ]);
  });

  it('lists each chromosome with every sample’s het, hom and hom share, a chromosome one sample lacks at zero', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    await within(small).findByRole('heading', {
      name: 'Per chromosome',
      level: 3,
    });
    expect(readTable(tableAfter(small, 'Per chromosome'))).toEqual([
      ['Chromosome', 'S1', 'S2'],
      ['Het', 'Hom', 'Hom %', 'Het', 'Hom', 'Hom %'],
      ['1', '400', '100', '20.0 %', '300', '0', '0.0 %'],
      ['15', '0', '200', '100.0 %', '0', '0', '–'],
      ['X', '200', '0', '0.0 %', '0', '0', '–'],
    ]);
  });

  it('draws each sample’s call-quality histogram from the server’s bins, one metric at a time', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    const quality = await within(small).findByRole('heading', {
      name: 'Call quality',
      level: 3,
    });
    const histograms = () =>
      within(quality.parentElement as HTMLElement).getAllByTestId('histogram');
    expect(
      histograms().map((h) => [
        h.dataset.subject,
        h.dataset.bins,
        h.textContent,
      ])
    ).toEqual([
      ["S1's genotype qualities", '0|10|90+', '5 0 895'],
      ["S2's genotype qualities", '', ''],
    ]);
    // Calls without a value are said, not drawn.
    expect(
      within(small).getByText('3 calls without a value are not drawn.')
    ).toBeInTheDocument();

    fireEvent.click(
      within(small).getByRole('button', { name: 'Allele balance (het calls)' })
    );
    expect(
      within(small).getByRole('button', { name: 'Allele balance (het calls)' })
    ).toHaveAttribute('aria-pressed', 'true');
    expect(histograms()[0].dataset.bins).toBe('0.00|0.45+');
    expect(histograms()[0].textContent).toBe('10 590');

    fireEvent.click(within(small).getByRole('button', { name: 'Depth' }));
    expect(histograms()[0].dataset.bins).toBe('0|30+');
    expect(histograms()[0].dataset.logScale).toBe('false');
    fireEvent.click(within(small).getByRole('button', { name: 'Log scale' }));
    expect(histograms()[0].dataset.logScale).toBe('true');
  });

  it('tabulates each trio’s Mendelian inconsistencies per autosome, with the autosomes’ total, and calls no UPD', async () => {
    serve({ has: { small: true }, small: TRIO_STATS });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    const trio = await within(small).findByRole('heading', {
      name: 'S1, child of S2 and S3 (deepvariant)',
      level: 4,
    });
    expect(
      readTable(within(trio.parentElement as HTMLElement).getByRole('table'))
    ).toEqual([
      [
        'Chromosome',
        'Sites',
        'Father’s allele missing',
        'Mother’s allele missing',
        'From neither parent',
        'Inconsistent',
      ],
      ['1', '500', '1', '2', '2', '1.0 %'],
      ['15', '250', '180', '0', '0', '72.0 %'],
      ['Autosomes', '750', '181', '2', '2', '24.7 %'],
    ]);
    expect(small).toHaveTextContent(
      'These counts describe the calls; CoGA does not call UPD.'
    );
  });

  it('says when no child has its parents’ calls, and when the family has imputed genotypes only', async () => {
    serve({
      has: { small: true },
      small: smallStats({ samples: [sampleStats('S1', { non_ref_count: 1 })] }),
    });
    const { unmount } = renderPage();
    expect(
      await within(await section('Small variants (SNV/indel)')).findByText(
        'No child has its own and both parents’ calls in one callset.'
      )
    ).toBeInTheDocument();
    unmount();

    mockedGet.mockReset();
    serve({
      has: { small: true },
      small: smallStats({ sources: [], imputed_sources: ['glimpse2'] }),
    });
    renderPage();
    expect(
      await within(await section('Small variants (SNV/indel)')).findByText(
        'This family has imputed genotypes only (glimpse2); they are not summarised here.'
      )
    ).toBeInTheDocument();
  });

  it('says the statistics could not be loaded, with the reason, and asks again on retry', async () => {
    serve({
      has: { small: true },
      small: failWith({
        response: { status: 500, data: { detail: 'ClickHouse busy' } },
      }),
    });
    renderPage();

    const small = await section('Small variants (SNV/indel)');
    const alert = await within(small).findByRole('alert');
    expect(alert).toHaveTextContent(
      'Could not load the small-variant statistics — this is not an empty result. ClickHouse busy'
    );
    expect(within(small).queryByRole('table')).not.toBeInTheDocument();

    serve({ has: { small: true }, small: TRIO_STATS });
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(within(small).getAllByRole('table').length).toBeGreaterThan(0)
    );
  });
});

describe('groupConsequences', () => {
  it('leaves out a group no sample has a term of, and adds each group’s terms per sample', () => {
    const groups = groupConsequences([
      sampleStats('A', {
        consequences: [
          { consequence: 'splice_donor_variant', count: 2 },
          { consequence: 'splice_acceptor_variant', count: 3 },
        ],
      }),
      sampleStats('B', {
        consequences: [{ consequence: 'splice_acceptor_variant', count: 1 }],
      }),
    ]);
    expect(
      groups.map((group) => [
        group.label,
        group.totals,
        group.rows.map((row) => [row.label, row.counts]),
      ])
    ).toEqual([
      [
        'Essential splice site',
        [5, 1],
        [
          ['Splice donor variant', [2, 0]],
          ['Splice acceptor variant', [3, 1]],
        ],
      ],
    ]);
  });
});

describe('the other calls’ sections', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  const member = (sampleId: string) => ({ sample_id: sampleId });

  it('counts each member’s repeat calls by the repeat page’s statuses', async () => {
    serve({
      has: { repeats: true },
      repeats: {
        samples: [member('S1'), member('S2')] as never,
        loci: [
          {
            locus_id: 'HTT',
            calls: { S1: { status: 'pathogenic' }, S2: { status: 'normal' } },
          },
          {
            locus_id: 'FMR1',
            calls: {
              S1: { status: 'premutation' },
              S2: { status: 'intermediate' },
            },
          },
          { locus_id: 'ATXN1', calls: { S1: { status: 'normal' } } },
        ] as never,
      },
    });
    renderPage();

    const repeats = await section('Repeat expansions');
    expect(
      readTable(
        await within(repeats).findByRole('table', {
          name: 'Repeat calls per member and status',
        })
      )
    ).toEqual([
      ['Call status', 'S1', 'S2'],
      ['Loci called', '3', '2'],
      ['Pathogenic', '1', '0'],
      ['Premutation', '1', '0'],
      ['Grey zone', '0', '1'],
      ['Normal', '1', '1'],
    ]);
    expect(repeats).toHaveTextContent('3 repeat loci with calls.');
  });

  it('counts each member’s Paraphase results by the Paraphase page’s statuses', async () => {
    serve({
      has: { paraphase: true },
      paraphase: {
        samples: [member('S1'), member('S2')] as never,
        genes: [
          {
            gene_symbol: 'SMN1',
            has_copy_number_signal: true,
            samples: {
              S1: { clinical_status: 'carrier' },
              S2: { clinical_status: 'normal' },
            },
          },
          {
            gene_symbol: 'GBA',
            has_copy_number_signal: false,
            samples: { S1: { clinical_status: null } },
          },
        ] as never,
      },
    });
    renderPage();

    const paraphase = await section('Paraphase');
    expect(
      readTable(
        await within(paraphase).findByRole('table', {
          name: 'Paraphase results per member and status',
        })
      )
    ).toEqual([
      ['Status', 'S1', 'S2'],
      ['Genes with a result', '2', '1'],
      ['Carrier', '1', '0'],
      ['Normal', '0', '1'],
      ['No status', '1', '0'],
    ]);
    expect(paraphase).toHaveTextContent(
      '2 genes or regions with results; of them 1 with a copy-number change.'
    );
  });

  it('counts each member’s mtDNA calls by heteroplasmy, with the haplogroup and depth', async () => {
    serve({
      has: { mito: true },
      mito: {
        samples: [
          {
            sample_id: 'S1',
            haplogroup: 'H1',
            coverage: { mean_depth: 2100.4 },
          },
          { sample_id: 'S2', haplogroup: null, coverage: { mean_depth: null } },
        ] as never,
        variants: [
          {
            variant_id: 'v1',
            calls: {
              S1: { zygosity: 'homoplasmic' },
              S2: { zygosity: 'homoplasmic' },
            },
          },
          {
            variant_id: 'v2',
            calls: {
              S1: { zygosity: 'heteroplasmic' },
              S2: { zygosity: 'reference' },
            },
          },
          { variant_id: 'v3', calls: { S1: { zygosity: 'low_level' } } },
        ] as never,
        structural_variants: [
          { variant_id: 'sv1', calls: { S1: { genotype: '1' } } },
        ] as never,
        heteroplasmy_threshold: 0.02,
        homoplasmy_threshold: 0.95,
      },
    });
    renderPage();

    const mito = await section('Mitochondrial DNA');
    expect(
      readTable(
        await within(mito).findByRole('table', {
          name: 'mtDNA calls per member',
        })
      )
    ).toEqual([
      ['Per member', 'S1', 'S2'],
      ['Haplogroup', 'H1', '–'],
      ['Mean depth', '2100×', '–'],
      ['Homoplasmic', '1', '1'],
      ['Heteroplasmic', '1', '0'],
      ['Low-level', '1', '0'],
      ['Deletions or duplications', '1', '0'],
    ]);
    expect(mito).toHaveTextContent(
      'Homoplasmic from 95 % heteroplasmy, low-level below 2 %'
    );
  });
});
