import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it, vi, type Mock } from 'vitest';
import GenomeOverviewPage from '../GenomeOverviewPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const sidebarSpy = vi.fn();
const workspaceSpy = vi.fn();

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    speciesName: 'Homo sapiens',
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    assemblyId: 'asm1',
    projectId: 'p1',
    isLoading: false,
  }),
}));

vi.mock('../../../lib/useMeasuredWidth', () => ({
  useMeasuredWidth: () => [{ current: null }, 1400] as const,
}));

// What the mocked sidebar and workspace read of their props.
type MemberProp = { sample_id: string };

vi.mock('../GenomeOverviewSidebar', () => ({
  default: (props: { members: MemberProp[]; chromSelected: Record<string, boolean> }) => {
    sidebarSpy(props);
    const selectedChroms = Object.entries(props.chromSelected)
      .filter(([, enabled]) => Boolean(enabled))
      .map(([chrom]) => chrom)
      .join(',');
    return (
      <div data-testid="genome-sidebar">
        {props.members.map((member) => member.sample_id).join(',')}|{selectedChroms}
      </div>
    );
  },
}));

vi.mock('../GenomeOverviewWorkspace', () => ({
  default: (props: {
    visibleMembers: MemberProp[];
    visibleRoi?: { label?: string } | null;
    navigateToChromosome: (chrom: string, region?: { start: number; end: number }) => void;
  }) => {
    workspaceSpy(props);
    return (
      <>
        <div data-testid="genome-workspace">
          {props.visibleMembers.map((member) => member.sample_id).join(',')}|{props.visibleRoi?.label || 'no-roi'}
        </div>
        <button type="button" onClick={() => props.navigateToChromosome('X')}>
          Open chromosome
        </button>
        <button
          type="button"
          onClick={() => props.navigateToChromosome('X', { start: 120, end: 180 })}
        >
          Open chromosome region
        </button>
      </>
    );
  },
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-search">{location.pathname}{location.search}</div>;
};

// The chromosomes the overview lays out: 1–22, X and Y.
const NUCLEAR = [...Array.from({ length: 22 }, (_, index) => String(index + 1)), 'X', 'Y'];
const PROBAND = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' };

/** What the track availability says a sample has: nothing, but the tracks a test names. */
const tracks = (...present: Array<'coverage' | 'segments' | 'apcad' | 'haplotypes' | 'variants'>) => ({
  coverage: present.includes('coverage'),
  segments: present.includes('segments'),
  apcad: present.includes('apcad'),
  haplotypes: present.includes('haplotypes'),
  variants: present.includes('variants'),
});
const EVERY_TRACK = tracks('coverage', 'segments', 'apcad', 'haplotypes', 'variants');

const failure = () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

/**
 * Serves family F1 (project p1) with `members`, GRCh38's chromosome lengths (1–22, X and Y,
 * 1 kb each, unless a test gives others) and the tracks each sample has. A test replaces the
 * answers of the lengths or the availability to fail them. Any other GET answers an empty object.
 */
const serve = ({
  members,
  roi,
  chromosomes = NUCLEAR.map((chr) => ({ chr, size: 1000 })),
  samples = {},
  lengthsReply = () => Promise.resolve({ data: chromosomes }),
  availabilityReply = () => Promise.resolve({ data: { samples } }),
}: {
  members: Array<typeof PROBAND>;
  roi?: Record<string, unknown> | null;
  chromosomes?: Array<{ chr: string; size: number }>;
  samples?: Record<string, ReturnType<typeof tracks>>;
  lengthsReply?: () => Promise<unknown>;
  availabilityReply?: () => Promise<unknown>;
}) => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({ data: { family_id: 'F1', members, projects: ['p1'], roi } });
    }
    if (url === '/chromosomes/GRCh38') return lengthsReply();
    if (url.startsWith('/families/F1/track-availability?')) return availabilityReply();
    return Promise.resolve({ data: {} });
  });
};

/** The overview at `entry`; a chromosome it opens shows where it went. */
const renderPage = (entry: string) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
          <Route path="/families/:familyId/chromosome/:chrom" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const availabilityRequest = () =>
  (api.get as unknown as Mock).mock.calls
    .map(([url]) => String(url))
    .find((url) => url.startsWith('/families/F1/track-availability?'));
const lastWorkspaceProps = () => workspaceSpy.mock.calls.at(-1)?.[0];

describe('GenomeOverviewPage', () => {
  it('keeps proband-first member order, respects sample and chromosome query state, and forwards ROI', async () => {
    serve({
      members: [
        { sample_id: 'SIB', role: 'sibling', affected: false, sex: 'female' },
        PROBAND,
        { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
      ],
      roi: {
        query: 'GENE-X',
        label: 'GENE-X',
        source: 'gene',
        assembly_id: 'asm1',
        chr: 'X',
        start: 250,
        end: 400,
      },
      samples: { PROBAND: EVERY_TRACK, SIB: tracks('coverage'), DAD: tracks() },
    });

    renderPage('/families/F1/genome?chrom=2&chrom=X&sample=SIB&type=DEL&sample_filter=PROBAND:hom');

    await waitFor(() => expect(screen.getByTestId('genome-sidebar')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toHaveTextContent('SIB|GENE-X'));

    expect(screen.getByTestId('genome-sidebar')).toHaveTextContent('PROBAND,DAD,SIB|2,X');

    const availabilityCall = availabilityRequest();
    expect(availabilityCall).toContain('chrom=2');
    expect(availabilityCall).toContain('chrom=X');
    expect(availabilityCall).toContain('type=DEL');
    expect(availabilityCall).toContain('sample_filter=PROBAND%3Ahom');

    expect(lastWorkspaceProps().visibleMembers.map((member: MemberProp) => member.sample_id)).toEqual(['SIB']);
    expect(lastWorkspaceProps().backDest).toBe(
      '/families/F1/structural-variants?type=DEL&sample_filter=PROBAND%3Ahom&project_id=p1',
    );
  });

  it('sends, and lists as the tracks’ filters, only the SV filters the routes take (ENG-7)', async () => {
    serve({ members: [PROBAND], chromosomes: [{ chr: '1', size: 1000 }], samples: { PROBAND: tracks('variants') } });
    renderPage('/families/F1/genome?type=DEL&qual=30&read_support=5&filter=PASS');

    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toHaveTextContent('PROBAND'));
    // `qual`, `read_support` and `filter` were sent, and listed among each track's filters,
    // though neither the SV route nor the track availability takes them.
    const availabilityCall = availabilityRequest();
    expect(availabilityCall).toContain('type=DEL');
    for (const unused of ['qual=30', 'read_support=5', 'filter=PASS']) {
      expect(availabilityCall).not.toContain(unused);
    }
    expect(lastWorkspaceProps().variantFilters).toEqual({ type: 'DEL' });
  });

  it('navigates to a chromosome region when genome workspace requests a regional jump', async () => {
    serve({ members: [PROBAND], roi: null, samples: { PROBAND: EVERY_TRACK } });
    renderPage('/families/F1/genome?chrom=2&project_id=p1&type=DEL');

    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Open chromosome region' }));

    await waitFor(() =>
      expect(screen.getByTestId('location-search')).toHaveTextContent('/families/F1/chromosome/X'),
    );
    expect(screen.getByTestId('location-search')).toHaveTextContent('chrom=2');
    expect(screen.getByTestId('location-search')).toHaveTextContent('project_id=p1');
    expect(screen.getByTestId('location-search')).toHaveTextContent('type=DEL');
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=120');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=180');
  });

  // #610 — without the chromosome lengths the viewer waited for good on a layout that
  // never came; the failure is handed on to be said in place of the tracks.
  it('passes a failed chromosome-lengths request on as a failure, not an endless load', async () => {
    let lengthRequests = 0;
    serve({
      members: [PROBAND],
      roi: null,
      lengthsReply: () => {
        lengthRequests += 1;
        return failure();
      },
      availabilityReply: () => Promise.resolve({ data: {} }),
    });

    renderPage('/families/F1/genome');

    await waitFor(() => expect(lastWorkspaceProps()?.tracksFailure?.what).toBe('the chromosome lengths'));
    expect(lastWorkspaceProps()?.showViewerLoading).toBe(false);
    const requestsBefore = lengthRequests;
    act(() => lastWorkspaceProps()?.tracksFailure.retry());
    await waitFor(() => expect(lengthRequests).toBeGreaterThan(requestsBefore));
  });

  it('says the family could not be loaded, not that it was not found', async () => {
    (api.get as unknown as Mock).mockImplementation(failure);

    renderPage('/families/F1/genome');

    expect(await screen.findByRole('heading', { name: 'Family could not be loaded' })).toBeInTheDocument();
    expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

// ENG-20, the owner's decision of 2026-10-28: on the overview the mitochondrion, 16.6 kb of a
// genome of 3.1 Gb, is about 0.006 px wide. It is left out, as the Circos plot leaves it out: not
// laid out, so not in the chromosome strip, not offered in the sidebar and asked of no track.
// The chromosome view's list and the mtDNA page show it.
describe('GenomeOverviewPage leaves the mitochondrion out (ENG-20)', () => {
  const renderOverview = (search: string) => {
    serve({
      members: [PROBAND],
      // The assembly has the mitochondrion.
      chromosomes: [...NUCLEAR.map((chr) => ({ chr, size: 1000 })), { chr: 'MT', size: 16_569 }],
      samples: { PROBAND: EVERY_TRACK },
    });
    renderPage(`/families/F1/genome${search}`);
  };

  // What the overview lays out and offers once it has the chromosome lengths.
  const laidOut = async () => {
    await waitFor(() => expect(lastWorkspaceProps()?.layout).toBeTruthy());
    const workspace = lastWorkspaceProps();
    const sidebar = sidebarSpy.mock.calls.at(-1)?.[0];
    return {
      layout: workspace.layout as { chroms: string[]; offsets: Record<string, number> },
      urlMaps: workspace.urlMaps,
      offered: Object.keys(sidebar.chromSelected),
      ticked: Object.keys(sidebar.chromSelected).filter((chrom) => sidebar.chromSelected[chrom]),
    };
  };

  it('lays out, offers and asks the tracks for 1–22, X and Y only', async () => {
    renderOverview('');
    const { layout, urlMaps, offered, ticked } = await laidOut();

    // The chromosome strip draws the layout's chromosomes: no "Open chromosome M".
    expect(layout.chroms).toEqual(NUCLEAR);
    expect(layout.offsets).not.toHaveProperty('MT');
    expect(offered).toEqual(NUCLEAR);
    expect(ticked).toEqual(NUCLEAR);
    expect(new URLSearchParams(availabilityRequest()?.split('?')[1]).getAll('chrom')).toEqual(NUCLEAR);
    expect(new URL(urlMaps.haplotypes.PROBAND[0]).searchParams.getAll('chr')).toEqual(NUCLEAR);
    const coverage = urlMaps.coverageTrackUrls('PROBAND', 'hificnv');
    expect(new URL(coverage.coverageUrls[0]).searchParams.getAll('chrom')).toEqual(NUCLEAR);
    expect(new URL(coverage.segmentsUrls[0]).searchParams.getAll('chrom')).toEqual(NUCLEAR);
    expect(new URL(urlMaps.apcad.PROBAND[0]).searchParams.getAll('chrom')).toEqual(NUCLEAR);
  });

  // A breakend's two chromosomes open on the overview from the Circos plot; the mitochondrion
  // named by a link is not shown.
  it.each(['M', 'MT', 'chrM'])('leaves out the mitochondrion a link names as %s', async (name) => {
    renderOverview(`?chrom=2&chrom=${name}`);
    const { layout, ticked } = await laidOut();

    expect(layout.chroms).toEqual(['2']);
    expect(ticked).toEqual(['2']);
  });
});
