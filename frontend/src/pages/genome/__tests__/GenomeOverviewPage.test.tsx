import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { useLocation } from 'react-router';
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

describe('GenomeOverviewPage', () => {
  it('keeps proband-first member order, respects sample and chromosome query state, and forwards ROI', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'SIB', role: 'sibling', affected: false, sex: 'female' },
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: ['p1'],
            roi: {
              query: 'GENE-X',
              label: 'GENE-X',
              source: 'gene',
              assembly_id: 'asm1',
              chr: 'X',
              start: 250,
              end: 400,
            },
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        return Promise.resolve({
          data: [
            ...Array.from({ length: 22 }, (_, index) => ({
              chr: String(index + 1),
              size: 1000,
            })),
            { chr: 'X', size: 1000 },
            { chr: 'Y', size: 1000 },
          ],
        });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                segments: true,
                apcad: true,
                haplotypes: true,
                variants: true,
              },
              SIB: {
                coverage: true,
                segments: false,
                apcad: false,
                haplotypes: false,
                variants: false,
              },
              DAD: {
                coverage: false,
                segments: false,
                apcad: false,
                haplotypes: false,
                variants: false,
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={['/families/F1/genome?chrom=2&chrom=X&sample=SIB&type=DEL&sample_filter=PROBAND:hom']}
        >
          <LocationProbe />
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
            <Route path="/families/:familyId/chromosome/:chrom" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('genome-sidebar')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toHaveTextContent('SIB|GENE-X'));

    expect(screen.getByTestId('genome-sidebar')).toHaveTextContent('PROBAND,DAD,SIB|2,X');

    const availabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .find((url) => url.startsWith('/families/F1/track-availability?'));
    expect(availabilityCall).toContain('chrom=2');
    expect(availabilityCall).toContain('chrom=X');
    expect(availabilityCall).toContain('type=DEL');
    expect(availabilityCall).toContain('sample_filter=PROBAND%3Ahom');

    const lastWorkspaceProps = workspaceSpy.mock.calls.at(-1)?.[0];
    expect(lastWorkspaceProps.visibleMembers.map((member: MemberProp) => member.sample_id)).toEqual([
      'SIB',
    ]);
    expect(lastWorkspaceProps.backDest).toBe(
      '/families/F1/structural-variants?type=DEL&sample_filter=PROBAND%3Ahom&project_id=p1',
    );
  });

  it('sends, and lists as the tracks’ filters, only the SV filters the routes take (ENG-7)', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
            projects: ['p1'],
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        return Promise.resolve({ data: [{ chr: '1', size: 1000 }] });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: { samples: { PROBAND: { coverage: false, segments: false, apcad: false, haplotypes: false, variants: true } } },
        });
      }
      return Promise.resolve({ data: {} });
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/genome?type=DEL&qual=30&read_support=5&filter=PASS']}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toHaveTextContent('PROBAND'));
    // `qual`, `read_support` and `filter` were sent, and listed among each track's filters,
    // though neither the SV route nor the track availability takes them.
    const availabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .find((url) => url.startsWith('/families/F1/track-availability?'));
    expect(availabilityCall).toContain('type=DEL');
    for (const unused of ['qual=30', 'read_support=5', 'filter=PASS']) {
      expect(availabilityCall).not.toContain(unused);
    }
    expect(workspaceSpy.mock.calls.at(-1)?.[0].variantFilters).toEqual({ type: 'DEL' });
  });

  it('navigates to a chromosome region when genome workspace requests a regional jump', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
            ],
            projects: ['p1'],
            roi: null,
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        return Promise.resolve({
          data: [
            ...Array.from({ length: 22 }, (_, index) => ({
              chr: String(index + 1),
              size: 1000,
            })),
            { chr: 'X', size: 1000 },
            { chr: 'Y', size: 1000 },
          ],
        });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                segments: true,
                apcad: true,
                haplotypes: true,
                variants: true,
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/genome?chrom=2&project_id=p1&type=DEL']}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
            <Route path="/families/:familyId/chromosome/:chrom" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

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
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
            projects: ['p1'],
            roi: null,
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        lengthRequests += 1;
        return Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/genome']}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(workspaceSpy.mock.calls.at(-1)?.[0].tracksFailure?.what).toBe('the chromosome lengths'),
    );
    expect(workspaceSpy.mock.calls.at(-1)?.[0].showViewerLoading).toBe(false);
    const requestsBefore = lengthRequests;
    act(() => workspaceSpy.mock.calls.at(-1)?.[0].tracksFailure.retry());
    await waitFor(() => expect(lengthRequests).toBeGreaterThan(requestsBefore));
  });

  it('says the family could not be loaded, not that it was not found', async () => {
    (api.get as unknown as Mock).mockImplementation(() =>
      Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } })),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/genome']}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

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
  const NUCLEAR = [...Array.from({ length: 22 }, (_, index) => String(index + 1)), 'X', 'Y'];

  const renderOverview = (search: string) => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
            projects: ['p1'],
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        // The assembly has the mitochondrion.
        return Promise.resolve({
          data: [...NUCLEAR.map((chr) => ({ chr, size: 1000 })), { chr: 'MT', size: 16_569 }],
        });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: { coverage: true, segments: true, apcad: true, haplotypes: true, variants: true },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={[`/families/F1/genome${search}`]}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  // What the overview lays out and offers once it has the chromosome lengths.
  const laidOut = async () => {
    await waitFor(() => expect(workspaceSpy.mock.calls.at(-1)?.[0].layout).toBeTruthy());
    const workspace = workspaceSpy.mock.calls.at(-1)?.[0];
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
    const availabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .find((url) => url.startsWith('/families/F1/track-availability?'));
    expect(new URLSearchParams(availabilityCall?.split('?')[1]).getAll('chrom')).toEqual(NUCLEAR);
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
