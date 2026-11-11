import { render, screen, fireEvent, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import GenomeOverviewWorkspace from '../GenomeOverviewWorkspace';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
} from '../../../test/familyPageBanners';

vi.mock('../../../components/visualizations/CoverageSegmentsChart', () => ({
  // The axis it is drawn on, and whether it was given segments to draw.
  default: ({ scale, segmentsUrls }: { scale?: string; segmentsUrls?: string[] }) => (
    <div data-testid="coverage-chart" data-scale={scale ?? 'log2_ratio'} data-segments={segmentsUrls ? 'yes' : 'no'} />
  ),
}));

vi.mock('../../../components/visualizations/ApcadChart', () => ({
  default: () => <div data-testid="apcad-chart" />,
}));

vi.mock('../../../components/visualizations/SvTrack', () => ({
  default: () => <div data-testid="sv-track" />,
}));

vi.mock('../../../components/visualizations/GenomeHaplotypeTrack', () => ({
  default: () => <div data-testid="genome-haplotype-track" />,
}));

vi.mock('../../../components/visualizations/GenomeRepeatExpansionTrack', () => ({
  default: () => <div data-testid="genome-repeat-track" />,
}));

vi.mock('../../../components/visualizations/VizLoadingOverlay', () => ({
  default: ({ message }: { message?: string }) => <div>{message || 'Loading'}</div>,
}));

vi.mock('../../../components/visualizations/Ideogram', () => ({
  default: ({ chrom, onRegionSelect }: { chrom: string; onRegionSelect?: (start: number, end: number) => void }) => (
    <button
      type="button"
      onClick={(event) => {
        if (event.shiftKey) {
          onRegionSelect?.(120, 180);
          return;
        }
      }}
    >
      Ideogram {chrom}
    </button>
  ),
}));

vi.mock('../ViewerMemberSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../ViewerTrackBlock', () => ({
  default: ({ children, label, meta }: { children: React.ReactNode; label: string; meta?: React.ReactNode }) => (
    <section aria-label={label}>
      <h2>{label}</h2>
      {meta}
      {children}
    </section>
  ),
}));

type Props = ComponentProps<typeof GenomeOverviewWorkspace>;

const MEMBER = {
  sample_id: 'PROBAND',
  role: 'proband',
  affected: true,
  sex: 'male',
} as const;

/** The tracks the sidebar shows: coverage only, unless a test turns another on. */
const tracksShown = (fields: Partial<Props['trackVisibility']> = {}): Props['trackVisibility'] => ({
  coverage: true,
  segments: false,
  apcad: false,
  sv: false,
  haplotypes: false,
  repeatExpansions: false,
  parentalOrigin: false,
  ...fields,
});

/** What the track availability says PROBAND has: coverage, the fields a test leaves out absent. */
const probandAvailability = (fields: Partial<Props['availability'][string]> = {}): Props['availability'] => ({
  PROBAND: {
    coverage: true,
    coverageSources: [],
    coverageNormalizations: {},
    coverageContents: {},
    segmentsSources: [],
    segments: false,
    apcadSources: [],
    apcad: false,
    apcadPcf: false,
    haplotypes: false,
    sv: false,
    repeatExpansions: false,
    ...fields,
  },
});

/** Each caller's coverage and segments URLs, named after the caller. */
const urlsBySource = (_sampleId: string, source: string) => ({
  coverageUrls: [`http://test/coverage?source=${source}`],
  segmentsUrls: [`http://test/segments?source=${source}`],
});

const NO_URLS: Props['urlMaps'] = {
  coverageTrackUrls: () => ({ coverageUrls: [], segmentsUrls: [] }),
  apcad: {},
  apcadPcf: {},
  haplotypes: {},
  sv: {},
};

/**
 * The workspace with PROBAND selected. By default no sample has data, so no track is mounted:
 * the empty or failed states. A test gives PROBAND data through `membersWithData` and
 * `availability`.
 */
const renderWorkspace = (overrides: Partial<Props> = {}) =>
  render(
    <MemoryRouter>
      <GenomeOverviewWorkspace
        familyId="F1"
        familyDisplayId="F1"
        speciesName="Homo sapiens"
        assemblyVersion="p14"
        assembly="GRCh38"
        projectId="p1"
        trackAreaRef={{ current: null }}
        backDest="/families/F1/structural-variants"
        visibleRoi={null}
        genomeRoiRange={null}
        navigateToChromosome={vi.fn()}
        familyMembers={[MEMBER]}
        visibleMembers={[MEMBER]}
        membersWithData={[]}
        trackVisibility={tracksShown()}
        availability={{}}
        variantFilters={{}}
        sampleFilterMap={{}}
        urlMaps={NO_URLS}
        layout={{ chroms: ['1'], offsets: { '1': 0 }, lengths: { '1': 1000 }, total: 1000 }}
        trackWidth={1200}
        trackHeight={120}
        svTrackHeight={80}
        showViewerLoading={false}
        {...overrides}
      />
    </MemoryRouter>,
  );

/** Gives a selection surface jsdom's zero-sized box the track's width, so x maps to the layout. */
const sizeSurface = (surface: HTMLElement, width = 1200) =>
  Object.defineProperty(surface, 'getBoundingClientRect', {
    value: () => ({ left: 0, top: 0, width, height: 120, right: width, bottom: 120, x: 0, y: 0, toJSON: () => ({}) }),
  });

const drag = (surface: HTMLElement, fromX: number, toX: number) => {
  fireEvent.mouseDown(surface, { clientX: fromX });
  fireEvent.mouseMove(surface, { clientX: toX });
  fireEvent.mouseUp(surface, { clientX: toX });
};

describe('GenomeOverviewWorkspace', () => {
  // Which tracks a sample has decides which are mounted: a failed availability
  // request is not a sample without data.
  it('says the track availability failed, not that the samples have no data', () => {
    const retry = vi.fn();
    renderWorkspace({
      tracksFailure: { what: 'which tracks each sample has', error: new Error('Network Error'), retry },
    });

    expect(screen.getByText(/Could not load which tracks each sample has — this is not an empty result/)).toBeInTheDocument();
    expect(screen.queryByText('No data for selected samples')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  // The chromosome lengths the view is laid out on failed: said by name, in place
  // of the tracks, instead of loading for good.
  it('names the request the tracks are waiting on when it is not the availability', () => {
    renderWorkspace({
      tracksFailure: { what: 'the chromosome lengths', error: new Error('HTTP 500'), retry: vi.fn() },
    });

    expect(screen.getByRole('alert')).toHaveTextContent(
      "Could not load the chromosome lengths — this is not an empty result. HTTP 500 The samples' tracks are not shown until it loads.",
    );
  });

  // The overview draws the family's data like any family page: a partly imported family, or
  // one off the validated scope, is said so in its header.
  it('warns in its header that the import is incomplete and the assembly is not validated', async () => {
    renderWorkspace({
      assembly: 'GRCh37',
      assemblyValidated: false,
      familyMetadata: INCOMPLETE_IMPORT_METADATA,
    });

    const header = screen.getByRole('heading', { name: 'Genome overview for family F1' }).closest('.page-top-card');
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
  });

  it('shows neither warning for a complete family inside the validated scope', () => {
    renderWorkspace({ assemblyValidated: true, familyMetadata: {} });

    expect(screen.queryByText('Import incomplete.')).not.toBeInTheDocument();
    expect(screen.queryByText('Not validated for clinical use.')).not.toBeInTheDocument();
  });

  it('says a sample without tracks has no data once availability is known', () => {
    renderWorkspace();

    expect(screen.getByText('No data for selected samples')).toBeInTheDocument();
  });

  it('keeps whole-chromosome clicks and supports region jumps from chromosome ideograms', () => {
    const navigateToChromosome = vi.fn();
    renderWorkspace({
      navigateToChromosome,
      membersWithData: [MEMBER],
      availability: probandAvailability({
        coverageSources: ['hificnv'],
        coverageNormalizations: { hificnv: ['log2_ratio_to_autosomal_median'] },
        coverageContents: { hificnv: 'log2_ratio' },
      }),
      urlMaps: {
        ...NO_URLS,
        coverageTrackUrls: urlsBySource,
        haplotypes: { PROBAND: ['http://test/haplotype'] },
        sv: { PROBAND: 'http://test/sv' },
      },
    });

    // The test id carries the caller: a sample can have three coverage tracks and
    // each selection surface has to be addressable on its own.
    const coverageSurface = screen.getByTestId('genome-region-select-coverage-PROBAND-hificnv');
    sizeSurface(coverageSurface);

    drag(coverageSurface, 120, 240);
    expect(navigateToChromosome).toHaveBeenCalledWith('1', {
      start: 100,
      end: 200,
    });

    fireEvent.click(screen.getByText('Ideogram 1'), { shiftKey: true });
    expect(navigateToChromosome).toHaveBeenCalledWith('1', {
      start: 120,
      end: 180,
    });

    fireEvent.click(screen.getByText('1'));
    expect(navigateToChromosome).toHaveBeenCalledWith('1');

    // The keyboard opens a chromosome too.
    navigateToChromosome.mockClear();
    const chromosome = screen.getByRole('button', { name: 'Open chromosome 1' });
    expect(chromosome).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(chromosome, { key: 'Enter' });
    fireEvent.keyDown(chromosome, { key: ' ' });
    fireEvent.keyDown(chromosome, { key: 'a' });
    expect(navigateToChromosome.mock.calls).toEqual([['1'], ['1']]);
  });

  // A region opens on one chromosome: a drag too short to be one, or across the boundary of
  // two chromosomes, opens none.
  it('opens no region for a drag shorter than 5 px or one that spans two chromosomes', () => {
    const navigateToChromosome = vi.fn();
    renderWorkspace({
      navigateToChromosome,
      membersWithData: [MEMBER],
      availability: probandAvailability({ coverageSources: ['hificnv'], coverageContents: { hificnv: 'log2_ratio' } }),
      urlMaps: { ...NO_URLS, coverageTrackUrls: urlsBySource },
      layout: { chroms: ['1', '2'], offsets: { '1': 0, '2': 1000 }, lengths: { '1': 1000, '2': 1000 }, total: 2000 },
    });
    const surface = screen.getByTestId('genome-region-select-coverage-PROBAND-hificnv');
    sizeSurface(surface);

    drag(surface, 100, 103);
    drag(surface, 500, 700);
    expect(navigateToChromosome).not.toHaveBeenCalled();

    // Within the second chromosome, a region of it.
    drag(surface, 720, 840);
    expect(navigateToChromosome).toHaveBeenCalledWith('2', { start: 200, end: 400 });
  });

  // The strip writes each chromosome as the sidebar, the chromosome view and the tracks name it,
  // without its chr, which its narrow slots have no room for (1, X), and opens the chromosome
  // view's route. The overview leaves the mitochondrion out (the owner's decision of
  // 2026-10-28), so no layout the page builds holds it.
  it('names each chromosome in the strip without its chr, and opens it by its route', () => {
    const navigateToChromosome = vi.fn();
    renderWorkspace({
      navigateToChromosome,
      layout: {
        chroms: ['X', 'Y'],
        offsets: { X: 0, Y: 1000 },
        lengths: { X: 1000, Y: 500 },
        total: 1500,
      },
    });

    const strip = screen.getByRole('region', { name: 'Chromosomes' });
    expect(
      within(strip)
        .getAllByRole('button', { name: /^Open chromosome / })
        .map((chromosome) => chromosome.getAttribute('aria-label')),
    ).toEqual(['Open chromosome X', 'Open chromosome Y']);
    expect(within(strip).queryByText('chrY')).not.toBeInTheDocument();

    fireEvent.click(within(strip).getByText('Y'));
    expect(navigateToChromosome).toHaveBeenCalledWith('Y');
  });

  it('draws one labelled coverage track per CNV caller', () => {
    renderWorkspace({
      membersWithData: [MEMBER],
      inheritanceModel: 'dominant',
      trackVisibility: tracksShown({ segments: true }),
      availability: probandAvailability({
        // Deliberately out of display order: the view must not depend on
        // what order the availability query returned.
        coverageSources: ['qdnaseq', 'hificnv', 'wisecondorx'],
        coverageNormalizations: { hificnv: ['log2_ratio_to_autosomal_median'] },
        coverageContents: { hificnv: 'log2_ratio', qdnaseq: 'log2_ratio', wisecondorx: 'log2_ratio' },
        segmentsSources: ['hificnv'],
        segments: true,
      }),
      urlMaps: { ...NO_URLS, coverageTrackUrls: urlsBySource },
    });

    // Three independent measurements, three tracks -- merging them would average
    // three answers into one that is none of them.
    const labels = screen
      .getAllByRole('heading')
      .map((node) => node.textContent)
      .filter((text) => text?.startsWith('Coverage'));
    // Display order, not the order the availability query returned: the same caller
    // must sit in the same row for every family member.
    expect(labels).toEqual([
      'Coverage · HiFiCNV',
      'Coverage · WisecondorX',
      'Coverage · QDNAseq',
    ]);
    expect(screen.getAllByTestId('coverage-chart')).toHaveLength(3);
    expect(screen.getByTestId('genome-region-select-coverage-PROBAND-qdnaseq')).toBeInTheDocument();
  });

  // Each coverage track is drawn on the axis of what it holds, as the track
  // availability serves it: a log2 ratio on the log2-ratio axis, read depth on a depth axis
  // (HiFiCNV's depths stored as read), and a coverage BED that does not declare what it holds
  // on its own range without an axis: it is not read as depth either.
  it('draws each coverage track on the axis of what it holds, and says why where that is not a log2 ratio', () => {
    const coverageTrackUrls = vi.fn(urlsBySource);
    renderWorkspace({
      membersWithData: [MEMBER],
      trackVisibility: tracksShown({ segments: true }),
      availability: probandAvailability({
        coverageSources: ['web', 'wisecondorx', 'hificnv'],
        coverageNormalizations: { hificnv: ['none'] },
        coverageContents: { hificnv: 'read_depth', web: 'unknown', wisecondorx: 'log2_ratio' },
        segmentsSources: ['hificnv', 'web', 'wisecondorx'],
        segments: true,
      }),
      urlMaps: { ...NO_URLS, coverageTrackUrls },
    });

    const track = (label: string) => screen.getByRole('region', { name: label });
    const chart = (label: string) => within(track(label)).getByTestId('coverage-chart');
    // Every caller keeps its row, in display order, and is drawn ...
    expect(
      screen
        .getAllByRole('heading')
        .map((node) => node.textContent)
        .filter((text) => text?.startsWith('Coverage')),
    ).toEqual(['Coverage · HiFiCNV', 'Coverage · WisecondorX', 'Coverage · web']);
    // ... each on the axis of what it holds; the segments, log2 ratios, beside a log2 ratio only.
    expect(chart('Coverage · WisecondorX')).toHaveAttribute('data-scale', 'log2_ratio');
    expect(chart('Coverage · WisecondorX')).toHaveAttribute('data-segments', 'yes');
    expect(chart('Coverage · HiFiCNV')).toHaveAttribute('data-scale', 'read_depth');
    expect(chart('Coverage · HiFiCNV')).toHaveAttribute('data-segments', 'no');
    expect(chart('Coverage · web')).toHaveAttribute('data-scale', 'unknown');
    expect(chart('Coverage · web')).toHaveAttribute('data-segments', 'no');
    // The HiFiCNV depths are fetched and drawn.
    expect(coverageTrackUrls).toHaveBeenCalledWith('PROBAND', 'hificnv');
    // Each head says why, where the track is not on the log2-ratio axis.
    expect(within(track('Coverage · WisecondorX')).queryByRole('note')).not.toBeInTheDocument();
    expect(within(track('Coverage · HiFiCNV')).getByRole('note')).toHaveTextContent(
      'Read depth, stored as read: the sample had no usable autosomal median to normalise it by.',
    );
    const undeclared = within(track('Coverage · web')).getByRole('note');
    expect(undeclared).toHaveTextContent(
      'This coverage BED does not declare whether it holds read depth or a log2 ratio: drawn on its own range without an axis, and not read as depth. Load it again with what it holds.',
    );
    expect(undeclared).toHaveClass('viz-scale-note--warning');
  });

  it.each([
    ['read_depth', 'no'],
    ['log2_ratio', 'yes'],
  ])('draws a coverage BED declared as %s on that axis, without a note (segments: %s)', (content, segments) => {
    renderWorkspace({
      // A header without the species, version or project: the reference label reads the assembly alone.
      speciesName: undefined,
      assemblyVersion: undefined,
      projectId: undefined,
      membersWithData: [MEMBER],
      trackVisibility: tracksShown({ segments: true }),
      availability: probandAvailability({
        coverageSources: ['web'],
        coverageContents: { web: content },
        segmentsSources: ['web'],
        segments: true,
      }),
      urlMaps: { ...NO_URLS, coverageTrackUrls: urlsBySource },
    });

    expect(screen.getByTestId('coverage-chart')).toHaveAttribute('data-scale', content);
    expect(screen.getByTestId('coverage-chart')).toHaveAttribute('data-segments', segments);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });
});
