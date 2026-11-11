import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import ChromosomeViewWorkspace from '../ChromosomeViewWorkspace';
import api from '../../../lib/api';
import type { ApiFamilyMember, ApiFamilyRegionOfInterest } from '../../../lib/apiTypes';
import type { HaplotypePhaseCorrection } from '../../../lib/haplotypePhaseCorrections';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
} from '../../../test/familyPageBanners';

// The props each mocked track was last drawn with, by sample.
const { coverageSegmentsChartMock, trackProps } = vi.hoisted(() => ({
  coverageSegmentsChartMock: vi.fn(),
  trackProps: {
    smallVariants: new Map<string, Record<string, unknown>>(),
    haplotypes: new Map<string, Record<string, unknown>>(),
    svs: new Map<string, Record<string, unknown>>(),
  },
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../components/visualizations/CoverageSegmentsChart', () => ({
  default: (props: unknown) => {
    coverageSegmentsChartMock(props);
    return <div data-testid="coverage-chart" />;
  },
}));

vi.mock('../../../components/visualizations/ApcadChart', () => ({
  default: () => <div data-testid="apcad-chart" />,
}));

const parentalOriginChartMock = vi.fn();
vi.mock('../../../components/visualizations/ParentalOriginChart', () => ({
  default: (props: unknown) => {
    parentalOriginChartMock(props);
    return <div data-testid="parental-origin-chart" />;
  },
}));

vi.mock('../../../components/visualizations/Ideogram', () => ({
  default: () => <div data-testid="ideogram" />,
}));

vi.mock('../../../components/visualizations/ZoomedIdeogram', () => ({
  default: () => <div data-testid="zoomed-ideogram" />,
}));

vi.mock('../../../components/visualizations/VariantTrack', () => ({
  default: (props: { sampleId: string }) => {
    trackProps.svs.set(props.sampleId, props);
    return <div data-testid="variant-track" />;
  },
}));

vi.mock('../../../components/visualizations/HaplotypePhasedTrack', () => ({
  default: (props: { sampleId: string }) => {
    trackProps.haplotypes.set(props.sampleId, props);
    return <div data-testid="haplotype-track" />;
  },
}));

vi.mock('../../../components/visualizations/GeneTrack', () => ({
  default: () => <div data-testid="gene-track" />,
}));

vi.mock('../../../components/visualizations/BlacklistTrack', () => ({
  default: () => <div data-testid="blacklist-track" />,
}));

vi.mock('../../../components/visualizations/CnvTrack', () => ({
  default: () => <div data-testid="cnv-track" />,
}));

vi.mock('../../../components/visualizations/SegmentalDuplicationTrack', () => ({
  default: () => <div data-testid="segmental-duplication-track" />,
}));

vi.mock('../../../components/visualizations/SmallVariantTrack', () => ({
  default: (props: { sampleId: string }) => {
    trackProps.smallVariants.set(props.sampleId, props);
    return <div data-testid="small-variant-track" />;
  },
}));

vi.mock('../../../components/visualizations/RepeatExpansionTrack', () => ({
  default: () => <div data-testid="repeat-expansion-track" />,
}));

vi.mock('../../../components/visualizations/VizLoadingOverlay', () => ({
  default: ({ message }: { message?: string }) => <div>{message || 'Loading'}</div>,
}));

vi.mock('../ViewerMemberSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../ViewerTrackBlock', async (importOriginal) => ({
  // TrackMeta, the filters a variant track's head lists, as drawn.
  ...(await importOriginal<typeof import('../ViewerTrackBlock')>()),
  default: ({ children, label, meta }: { children: React.ReactNode; label: string; meta?: React.ReactNode }) => (
    <section aria-label={label}>
      <h2>{label}</h2>
      {meta}
      {children}
    </section>
  ),
}));

type Props = ComponentProps<typeof ChromosomeViewWorkspace>;

/** The tracks the sidebar shows: none, unless a test turns one on. */
const tracksShown = (fields: Partial<Props['trackVisibility']> = {}): Props['trackVisibility'] => ({
  coverage: false,
  apcad: false,
  variants: false,
  smallVariants: false,
  haplotypes: false,
  phasedMarkers: false,
  repeatExpansions: false,
  parentalOrigin: false,
  ...fields,
});

/** What the track availability says a sample has: nothing, but the fields a test sets. */
const sampleAvailability = (fields: Partial<Props['availability'][string]> = {}): Props['availability'][string] => ({
  coverage: false,
  coverageSources: [],
  coverageNormalizations: {},
  coverageContents: {},
  apcadSources: [],
  apcad: false,
  apcadPcf: false,
  variants: false,
  smallVariants: false,
  haplotypes: false,
  repeatExpansions: false,
  ...fields,
});

/** Shows `members`, each with data, on the tracks the test turns on. */
const withMembers = (members: ApiFamilyMember[]): Partial<Props> => ({
  familyMembers: members,
  visibleMembers: members,
  membersWithData: members,
});

const renderWorkspace = (overrides: Partial<Props> = {}) => {
  const queryClient = createTestQueryClient();

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChromosomeViewWorkspace
          familyId="F1"
          familyDisplayId="F1"
          chrom="1"
          speciesName="Homo sapiens"
          assemblyVersion="p14"
          assembly="GRCh38"
          assemblyId="asm1"
          projectId="p1"
          trackAreaRef={{ current: null }}
          region={{ start: 0, end: 1_000_000 }}
          trackWidth={1200}
          backDest="/families/F1/structural-variants"
          genomeViewHref="/families/F1/genome"
          igvHref="/families/F1/igv"
          chromInfoSize={248_956_422}
          visibleRoi={null}
          chromosomeRoiRange={null}
          regionRoiRange={null}
          onChromChange={vi.fn()}
          onRegionStartChange={vi.fn()}
          onRegionEndChange={vi.fn()}
          onResetRange={vi.fn()}
          onPan={vi.fn()}
          onZoom={vi.fn()}
          onRegionSelect={vi.fn()}
          onRoiZoom={vi.fn()}
          onJumpToRegion={vi.fn()}
          familyMembers={[]}
          visibleMembers={[]}
          membersWithData={[]}
          availability={{}}
          trackVisibility={tracksShown()}
          variantFilters={{}}
          sampleFilterMap={{}}
          detailWindow={5000}
          binLimit={500}
          apcadPointLimit={2000}
          segmentLimit={500}
          showViewerLoading={false}
          {...overrides}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const PROBAND_S1: ApiFamilyMember = { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' };
const EMBRYO: ApiFamilyMember = { sample_id: 'EMB1', role: 'embryo', affected: false, sex: 'female' };

describe('ChromosomeViewWorkspace', () => {
  beforeEach(() => {
    Object.values(trackProps).forEach((drawn) => drawn.clear());
  });

  // Which tracks a sample has decides which are mounted: a failed availability
  // request is not a sample without data.
  it('says the track availability failed, not that the samples have no data', () => {
    const retry = vi.fn();
    renderWorkspace({
      visibleMembers: [PROBAND_S1],
      tracksFailure: {
        what: 'which tracks each sample has',
        error: new Error('Request failed with status code 500'),
        retry,
      },
    });

    expect(screen.getByRole('alert')).toHaveTextContent(
      "Could not load which tracks each sample has — this is not an empty result. Request failed with status code 500 The samples' tracks are not shown until it loads.",
    );
    expect(screen.queryByText('No BED data for selected samples.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('says a sample without tracks has no data once availability is known', () => {
    renderWorkspace({ visibleMembers: [PROBAND_S1] });

    expect(screen.getByText('No BED data for selected samples.')).toBeInTheDocument();
  });

  // The view draws the family's data like any family page: a partly imported family, or one
  // off the validated scope, is said so in its header.
  it('warns in its header that the import is incomplete and the assembly is not validated', async () => {
    renderWorkspace({
      assembly: 'GRCh37',
      assemblyValidated: false,
      familyMetadata: INCOMPLETE_IMPORT_METADATA,
    });

    const header = screen.getByRole('heading', { name: 'Chromosome view for family F1' }).closest('.page-top-card');
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
  });

  it('shows neither warning for a complete family inside the validated scope', () => {
    renderWorkspace({ assemblyValidated: true, familyMetadata: {} });

    expect(screen.queryByText('Import incomplete.')).not.toBeInTheDocument();
    expect(screen.queryByText('Not validated for clinical use.')).not.toBeInTheDocument();
  });

  // The list names each chromosome as the header, the tracks and the genome overview do (`chr1`,
  // `chrX`, the mitochondrion chrM). Its value stays the route's key, `MT`, which the API reads
  // as M, chrM and chrMT alike.
  it('names each chromosome in its list as the header does, the mitochondrion chrM', () => {
    const onChromChange = vi.fn();
    renderWorkspace({ chrom: 'MT', region: { start: 0, end: 16_569 }, onChromChange });

    const list = screen.getByRole('combobox', { name: 'Chromosome' });
    expect(within(list).getAllByRole('option').map((option) => option.textContent)).toEqual([
      ...Array.from({ length: 22 }, (_, index) => `chr${index + 1}`),
      'chrX',
      'chrY',
      'chrM',
    ]);
    expect(list).toHaveDisplayValue('chrM');
    expect(screen.getByText(/GRCh38.* • chrM • /)).toBeInTheDocument();

    fireEvent.change(list, { target: { value: '7' } });
    expect(onChromChange).toHaveBeenLastCalledWith('7');
  });

  it('requests coverage and segments for the active chromosome window', () => {
    renderWorkspace({
      region: { start: 1_000_000, end: 2_000_000 },
      inheritanceModel: null,
      ...withMembers([PROBAND_S1]),
      availability: {
        S1: sampleAvailability({
          coverage: true,
          coverageSources: ['hificnv'],
          coverageNormalizations: { hificnv: ['log2_ratio_to_autosomal_median'] },
          coverageContents: { hificnv: 'log2_ratio' },
        }),
      },
      trackVisibility: tracksShown({ coverage: true }),
    });

    const props = coverageSegmentsChartMock.mock.calls[0][0] as {
      coverageUrls: string[];
      segmentsUrls?: string[];
    };

    expect(props.coverageUrls[0]).toContain('start=1000000');
    expect(props.coverageUrls[0]).toContain('end=2000000');
    expect(props.segmentsUrls?.[0]).toContain('start=1000000');
    expect(props.segmentsUrls?.[0]).toContain('end=2000000');
  });

  // Each coverage track is drawn on the axis of what it holds: HiFiCNV's
  // depths stored as read on a depth axis; a coverage BED that
  // does not declare what it holds on its own range, without an axis, and never with the
  // segments, which are log2 ratios.
  it('draws each coverage track on the axis of what it holds, and says why where that is not a log2 ratio', () => {
    renderWorkspace({
      ...withMembers([PROBAND_S1]),
      availability: {
        S1: sampleAvailability({
          coverage: true,
          coverageSources: ['web', 'wisecondorx', 'hificnv'],
          coverageNormalizations: { hificnv: ['none'] },
          coverageContents: { hificnv: 'read_depth', web: 'unknown', wisecondorx: 'log2_ratio' },
        }),
      },
      trackVisibility: tracksShown({ coverage: true }),
    });

    // Every caller keeps its row, in display order, and is drawn ...
    const labels = screen
      .getAllByRole('heading')
      .map((node) => node.textContent)
      .filter((text) => text?.startsWith('Coverage'));
    expect(labels).toEqual(['Coverage · HiFiCNV', 'Coverage · WisecondorX', 'Coverage · web']);
    expect(screen.getAllByTestId('coverage-chart')).toHaveLength(3);
    // ... each on the axis of what it holds, the segments beside the log2 ratio only.
    const drawn = Object.fromEntries(
      coverageSegmentsChartMock.mock.calls.map(([props]) => {
        const { coverageUrls, scale, segmentsUrls } = props as {
          coverageUrls: string[];
          scale?: string;
          segmentsUrls?: string[];
        };
        const source = new URL(coverageUrls[0], 'http://test').searchParams.get('source');
        return [source, { scale, segments: segmentsUrls !== undefined }];
      }),
    );
    expect(drawn).toEqual({
      hificnv: { scale: 'read_depth', segments: false },
      wisecondorx: { scale: 'log2_ratio', segments: true },
      web: { scale: 'unknown', segments: false },
    });
    // Each head says why, where the track is not on the log2-ratio axis.
    expect(within(screen.getByRole('region', { name: 'Coverage · WisecondorX' })).queryByRole('note')).toBeNull();
    expect(within(screen.getByRole('region', { name: 'Coverage · HiFiCNV' })).getByRole('note')).toHaveTextContent(
      'Read depth, stored as read: the sample had no usable autosomal median to normalise it by.',
    );
    expect(within(screen.getByRole('region', { name: 'Coverage · web' })).getByRole('note')).toHaveTextContent(
      'This coverage BED does not declare whether it holds read depth or a log2 ratio',
    );
  });

  it("draws an embryo's parental origin from its segregation table only when asked for", () => {
    renderWorkspace({
      ...withMembers([EMBRYO]),
      availability: { EMB1: sampleAvailability({ apcadSegregation: true }) },
      trackVisibility: tracksShown({ parentalOrigin: true }),
    });

    expect(screen.getByTestId('parental-origin-chart')).toBeInTheDocument();
    const { urls, chroms } = parentalOriginChartMock.mock.calls[0][0] as { urls: string[]; chroms: string[] };
    expect(chroms).toEqual(['1']);
    const [url] = urls;
    expect(new URL(url, 'http://test').pathname).toMatch(/\/bed\/EMB1\/apcad_segregation$/);
    expect(new URL(url, 'http://test').searchParams.get('chrom')).toBe('1');
  });

  it('draws no parental origin while the toggle is off', () => {
    renderWorkspace({
      ...withMembers([EMBRYO]),
      availability: { EMB1: sampleAvailability({ apcadSegregation: true }) },
    });

    expect(screen.queryByTestId('parental-origin-chart')).toBeNull();
  });

  it('encodes the caller it asks the coverage and segments for', () => {
    // A caller's name is encoded in the BED URLs: an `&` or a `#` in it would otherwise end
    // the parameter there.
    renderWorkspace({
      ...withMembers([PROBAND_S1]),
      availability: {
        S1: sampleAvailability({
          coverage: true,
          coverageSources: ['cnv&kit#2'],
          coverageContents: { 'cnv&kit#2': 'log2_ratio' },
        }),
      },
      trackVisibility: tracksShown({ coverage: true }),
    });

    const [[props]] = coverageSegmentsChartMock.mock.calls as [[{ coverageUrls: string[]; segmentsUrls?: string[] }]];
    for (const url of [props.coverageUrls[0], props.segmentsUrls?.[0] ?? '']) {
      const params = new URL(url, 'http://test').searchParams;
      expect(params.get('source')).toBe('cnv&kit#2');
    }
  });

  // A child's small variants are drawn on three rows: inherited from the father, from the
  // mother, or not determined. A parent's, with no parent of its own in the family, on one.
  // The SV and small-variant tracks read the search's filters, and each sample's own genotype
  // filter.
  it("splits a child's small variants by parental origin, a parent's not, with each sample's own filter", () => {
    const father: ApiFamilyMember = { sample_id: 'FATHER1', role: 'father', affected: false, sex: 'male' };
    const mother: ApiFamilyMember = { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' };
    const proband: ApiFamilyMember = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' };
    const available = sampleAvailability({ smallVariants: true, variants: true });
    renderWorkspace({
      ...withMembers([proband, father, mother]),
      availability: { PROBAND: available, FATHER1: available, MOTHER: available },
      trackVisibility: tracksShown({ smallVariants: true, variants: true }),
      variantFilters: { impact: 'HIGH' },
      sampleFilterMap: { PROBAND: 'het' },
    });

    expect(trackProps.smallVariants.get('PROBAND')).toMatchObject({
      paternalSampleId: 'FATHER1',
      maternalSampleId: 'MOTHER',
      height: 45,
      filters: { impact: 'HIGH', sample_filter: 'het' },
    });
    for (const parent of ['FATHER1', 'MOTHER']) {
      expect(trackProps.smallVariants.get(parent)).toMatchObject({
        paternalSampleId: null,
        maternalSampleId: null,
        height: 20,
      });
      expect(trackProps.smallVariants.get(parent)?.filters).toEqual({ impact: 'HIGH' });
      expect(trackProps.svs.get(parent)?.filters).toEqual({ impact: 'HIGH' });
    }
    expect(trackProps.svs.get('PROBAND')?.filters).toEqual({ impact: 'HIGH', sample_filter: 'het' });
  });

  // A parent's phase switches the haplotype import undid are marked on that parent's track
  // only, and only on their chromosome. The ROI is the risk region of a chromosome it lies on,
  // and an embryo the PGT analysis leaves uncalled for markers that mix both of a parent's
  // homologs is warned of it on its track, as on the family page.
  describe('the haplotype tracks', () => {
    const father: ApiFamilyMember = { sample_id: 'FATHER1', role: 'father', affected: true, sex: 'male' };
    const mother: ApiFamilyMember = { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' };
    const correction = (parent: string, side: 'father' | 'mother', chr: string): HaplotypePhaseCorrection => ({
      parent,
      side,
      chr,
      position: 30_400_000,
      end: 30_450_000,
      children_switching: 3,
      children: 3,
    });
    const ROI: ApiFamilyRegionOfInterest = {
      query: 'GENE1',
      label: 'GENE1',
      source: 'gene',
      chr: 'chr1',
      start: 30_500_000,
      end: 30_600_000,
    };
    // EMB1's markers at the ROI mix both of the father's homologs.
    const PGT_ANALYSIS = {
      disease_haplotype_resolved: true,
      phase_corrections: [],
      inputs: { markers_truncated: false },
      rules: { qc_thresholds: { max_marker_discordance: 0.1 } },
      embryos: [
        {
          sample: 'EMB1',
          call: 'uninformative',
          recombinations: [],
          roi_not_covered: false,
          sex_dependent: null,
          qc_verdict: null,
          mixed_homologs: ['paternal'],
          qc: { paternal_discordance: 0.3, paternal_markers: 40 },
        },
      ],
    };
    const renderHaplotypes = (members: ApiFamilyMember[], overrides: Partial<Props> = {}) => {
      // The reference tracks (genes, DGV, …) ask for their own data: none here.
      (api.get as unknown as Mock).mockImplementation((url: string) =>
        Promise.resolve({ data: url === '/families/F1/pgt-analysis' ? PGT_ANALYSIS : [] }),
      );
      renderWorkspace({
        ...withMembers(members),
        availability: Object.fromEntries(
          members.map((member) => [member.sample_id, sampleAvailability({ haplotypes: true })]),
        ),
        trackVisibility: tracksShown({ haplotypes: true, phasedMarkers: true }),
        phaseCorrections: [
          correction('FATHER1', 'father', 'chr1'),
          correction('FATHER1', 'father', '2'),
          correction('MOTHER', 'mother', '1'),
        ],
        ...overrides,
      });
    };

    it('marks each parent’s own phase corrections on this chromosome, and warns of an embryo’s mixed homologs at the ROI', async () => {
      renderHaplotypes([father, mother, EMBRYO], { visibleRoi: ROI });

      expect(trackProps.haplotypes.get('FATHER1')?.phaseCorrections).toEqual([correction('FATHER1', 'father', 'chr1')]);
      expect(trackProps.haplotypes.get('MOTHER')?.phaseCorrections).toEqual([correction('MOTHER', 'mother', '1')]);
      expect(trackProps.haplotypes.get('EMB1')?.phaseCorrections).toEqual([]);
      for (const sample of ['FATHER1', 'MOTHER', 'EMB1']) {
        // Both parents are present, so the phased markers are drawn on every track.
        expect(trackProps.haplotypes.get(sample)).toMatchObject({ riskRegion: ROI, showMarkers: true, height: 40 });
      }
      await waitFor(() =>
        expect(trackProps.haplotypes.get('EMB1')?.mixedHomologsWarning).toMatch(
          /^This embryo's markers at the ROI mix both of the father's homologs/,
        ),
      );
      expect(trackProps.haplotypes.get('FATHER1')?.mixedHomologsWarning).toBeNull();
      expect(api.get).toHaveBeenCalledWith('/families/F1/pgt-analysis');
    });

    it('reads no risk region from an ROI on another chromosome, nor the PGT analysis', () => {
      renderHaplotypes([father, mother, EMBRYO], { visibleRoi: { ...ROI, chr: '7' } });

      expect(trackProps.haplotypes.get('EMB1')).toMatchObject({ riskRegion: null, mixedHomologsWarning: null });
      expect(api.get).not.toHaveBeenCalledWith('/families/F1/pgt-analysis');
    });

    it('draws no phased markers without both parents', () => {
      renderHaplotypes([father, EMBRYO]);

      expect(trackProps.haplotypes.get('EMB1')).toMatchObject({ showMarkers: false, height: 20, riskRegion: null });
    });
  });

  it('jumps to a resolved gene window', async () => {
    const onJumpToRegion = vi.fn();
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/genes/search') {
        return Promise.resolve({
          data: [
            {
              symbol: 'BRCA1',
              gene_id: 'gene1',
              chr: '17',
              start: 43044295,
              end: 43125482,
              transcript_count: 1,
              assembly_count: 1,
            },
          ],
        });
      }
      if (url === '/genes/profile') {
        return Promise.resolve({
          data: {
            symbol: 'BRCA1',
            chr: '17',
            start: 10000,
            end: 11000,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWorkspace({ onJumpToRegion });

    fireEvent.change(screen.getByLabelText('Jump to gene or locus'), {
      target: { value: 'BRCA1' },
    });

    await waitFor(() =>
      expect(api.get as unknown as Mock).toHaveBeenCalledWith('/genes/search', {
        params: { q: 'BRCA1' },
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(api.get as unknown as Mock).toHaveBeenCalledWith('/genes/profile', {
        params: {
          symbol: 'BRCA1',
          assembly_id: 'asm1',
          family_id: 'F1',
          project_id: 'p1',
        },
      }),
    );

    await waitFor(() =>
      expect(onJumpToRegion).toHaveBeenCalledWith('17', {
        start: 5000,
        end: 16000,
      }),
    );
  });

  it('parses a direct locus jump without calling the gene profile endpoint', async () => {
    const onJumpToRegion = vi.fn();
    (api.get as unknown as Mock).mockResolvedValue({ data: [] });

    renderWorkspace({ onJumpToRegion });

    fireEvent.change(screen.getByLabelText('Jump to gene or locus'), {
      target: { value: 'chr5:10,000-20,000' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(onJumpToRegion).toHaveBeenCalledWith('5', {
        start: 10000,
        end: 20000,
      }),
    );

    expect(api.get as unknown as Mock).not.toHaveBeenCalledWith('/genes/profile', expect.anything());
  });

  // A locus whose end lies before its start is no locus: it is looked up as a gene, and a
  // lookup that fails says why, and jumps nowhere.
  it('says why a gene or locus could not be resolved, and stays where it is', async () => {
    const onJumpToRegion = vi.fn();
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/genes/profile'
        ? Promise.reject({ response: { status: 404, data: { detail: 'No gene chr5:20000-10000 in GRCh38' } } })
        : Promise.resolve({ data: [] }),
    );
    renderWorkspace({ onJumpToRegion });

    fireEvent.change(screen.getByLabelText('Jump to gene or locus'), { target: { value: 'chr5:20000-10000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    expect(await screen.findByText('No gene chr5:20000-10000 in GRCh38')).toHaveClass('status-note--error');
    expect(onJumpToRegion).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Go' })).toBeEnabled();
  });

  it('pans and zooms from its buttons once the chromosome length is known, and switches the drag mode', () => {
    const onPan = vi.fn();
    const onZoom = vi.fn();
    renderWorkspace({ onPan, onZoom });

    fireEvent.click(screen.getByRole('button', { name: 'Pan left' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Pan right' }));
    expect(onPan.mock.calls).toEqual([[-1], [1]]);
    expect(onZoom.mock.calls).toEqual([[2], [0.5]]);

    const pan = screen.getByRole('button', { name: 'Pan' });
    const zoom = screen.getByRole('button', { name: 'Zoom' });
    expect(pan).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Drag to pan')).toBeInTheDocument();
    fireEvent.click(zoom);
    expect(zoom).toHaveAttribute('aria-pressed', 'true');
    expect(pan).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('Drag to select region')).toBeInTheDocument();
  });

  it('offers no pan or zoom before the chromosome length is known', () => {
    renderWorkspace({ chromInfoSize: undefined });

    for (const name of ['Pan left', 'Zoom out', 'Zoom in', 'Pan right']) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
  });
});
