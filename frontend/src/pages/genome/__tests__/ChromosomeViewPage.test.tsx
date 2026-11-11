import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import ChromosomeViewPage from '../ChromosomeViewPage';
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

const GRCH38_REFERENCE = {
  speciesName: 'Homo sapiens',
  assemblyName: 'GRCh38',
  assemblyVersion: 'p14',
  assemblyId: 'asm1',
  projectId: 'p1',
  isLoading: false,
  isError: false,
  retry: () => undefined,
};
const referenceMock = vi.hoisted(() => ({ value: undefined as unknown }));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => referenceMock.value,
}));

vi.mock('../../../lib/useMeasuredWidth', () => ({
  useMeasuredWidth: () => [{ current: null }, 1400] as const,
}));

// What the mocked sidebar and workspace read of their props.
type MemberProp = { sample_id: string };

vi.mock('../ChromosomeViewSidebar', () => ({
  default: (props: { members: MemberProp[] }) => {
    sidebarSpy(props);
    return (
      <div data-testid="chromosome-sidebar">
        {props.members.map((member) => member.sample_id).join(',')}
      </div>
    );
  },
}));

vi.mock('../ChromosomeViewWorkspace', () => ({
  default: (props: {
    region: { start: number; end: number };
    visibleMembers: MemberProp[];
    visibleRoi?: { label?: string } | null;
    onRegionSelect: (start: number, end: number) => void;
  }) => {
    workspaceSpy(props);
    return (
      <>
        <div data-testid="chromosome-workspace">
          {props.region.start}:{props.region.end}|{props.visibleMembers.map((member) => member.sample_id).join(',')}|
          {props.visibleRoi?.label || 'no-roi'}
        </div>
        <button type="button" onClick={() => props.onRegionSelect(120, 180)}>
          Select region
        </button>
      </>
    );
  },
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-search">{location.pathname}{location.search}</div>;
};

const MOTHER = { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' };
const PROBAND = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' };
const FATHER = { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' };

/** What the track availability says a sample has: nothing, but the tracks a test names. */
const tracks = (...present: Array<'coverage' | 'apcad' | 'variants' | 'small_variants' | 'haplotypes'>) => ({
  coverage: present.includes('coverage'),
  apcad: present.includes('apcad'),
  variants: present.includes('variants'),
  small_variants: present.includes('small_variants'),
  haplotypes: present.includes('haplotypes'),
});
const EVERY_TRACK = tracks('coverage', 'apcad', 'variants', 'small_variants', 'haplotypes');

const failure = () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

/**
 * Serves family F1 (project p1) with `members`, the length of `chromosome` on GRCh38, and the
 * tracks each sample has. A test replaces the length or the availability answer to fail it.
 * Any other GET answers an empty object.
 */
const serve = ({
  members,
  roi = null,
  chromosome = { chr: '1', size: 1000 },
  samples = {},
  lengthReply = () => Promise.resolve({ data: chromosome }),
  availabilityReply = () => Promise.resolve({ data: { samples } }),
}: {
  members: Array<typeof PROBAND>;
  roi?: Record<string, unknown> | null;
  chromosome?: { chr: string; size: number };
  samples?: Record<string, ReturnType<typeof tracks>>;
  lengthReply?: () => Promise<unknown>;
  availabilityReply?: () => Promise<unknown>;
}) => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({ data: { family_id: 'F1', members, projects: ['p1'], roi } });
    }
    if (url === `/chromosomes/GRCh38/${chromosome.chr}`) return lengthReply();
    if (url.startsWith('/families/F1/track-availability?')) return availabilityReply();
    return Promise.resolve({ data: {} });
  });
};

const renderPage = (entry: string) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <LocationProbe />
        <Routes>
          <Route path="/families/:familyId/chromosome/:chrom" element={<ChromosomeViewPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const requested = () => (api.get as unknown as Mock).mock.calls.map(([url]) => String(url));
const availabilityRequests = () => requested().filter((url) => url.startsWith('/families/F1/track-availability?'));
const lastWorkspaceProps = () => workspaceSpy.mock.calls.at(-1)?.[0];
const visibleSamples = () => lastWorkspaceProps()?.visibleMembers.map((member: MemberProp) => member.sample_id);

describe('ChromosomeViewPage', () => {
  beforeEach(() => {
    referenceMock.value = GRCH38_REFERENCE;
    vi.clearAllMocks();
  });

  it('sorts members proband-first, keeps query region, and forwards ROI plus sample filters', async () => {
    serve({
      members: [MOTHER, PROBAND, FATHER],
      roi: {
        query: 'BRCA1',
        label: 'BRCA1',
        source: 'gene',
        assembly_id: 'asm1',
        chr: '1',
        start: 140,
        end: 180,
      },
      samples: { PROBAND: EVERY_TRACK, MOTHER: tracks(), FATHER: tracks('coverage', 'variants') },
    });

    renderPage('/families/F1/chromosome/1?start=100&end=200&origin=small&project_id=p1&sample_filter=PROBAND:het');

    await waitFor(() => expect(screen.getByTestId('chromosome-sidebar')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('100:200'));

    expect(screen.getByTestId('chromosome-sidebar')).toHaveTextContent('PROBAND,FATHER,MOTHER');
    expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('BRCA1');
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=100');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=200');

    const [availabilityCall] = availabilityRequests();
    expect(availabilityCall).toContain('chrom=1');
    expect(availabilityCall).toContain('start=100');
    expect(availabilityCall).toContain('end=200');
    expect(availabilityCall).toContain('sample_filter=PROBAND%3Ahet');

    expect(visibleSamples()).toEqual(['PROBAND', 'FATHER', 'MOTHER']);
    expect(lastWorkspaceProps().backDest).toBe('/families/F1/small-variants?origin=small&sample_filter=PROBAND%3Ahet&project_id=p1');

    fireEvent.click(screen.getByRole('button', { name: 'Select region' }));

    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('120:180'));
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=120');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=180');

    const latestAvailabilityCall = availabilityRequests().at(-1);
    expect(latestAvailabilityCall).toContain('start=120');
    expect(latestAvailabilityCall).toContain('end=180');
  });

  // With the project catalogue failed the reference is unknown, not missing: the page
  // says it could not be loaded, and retries, instead of "Reference not linked".
  it('says the reference could not be loaded, not that it is not linked', async () => {
    const retry = vi.fn();
    referenceMock.value = {
      ...GRCH38_REFERENCE,
      assemblyName: undefined,
      assemblyId: undefined,
      projectId: undefined,
      isError: true,
      retry,
    };
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/families/F1'
        ? Promise.resolve({ data: { family_id: 'F1', members: [], projects: ['p1'], roi: null } })
        : Promise.resolve({ data: {} }),
    );

    renderPage('/families/F1/chromosome/1');

    expect(await screen.findByText('Reference could not be loaded')).toBeInTheDocument();
    expect(screen.queryByText('Reference not linked')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  // The page hands a failed availability request to the workspace as a failure,
  // which it says instead of "No BED data for selected samples".
  it('passes a failed track-availability request on as a failure, and retries it', async () => {
    let availabilityAsked = 0;
    serve({
      members: [PROBAND],
      availabilityReply: () => {
        availabilityAsked += 1;
        return failure();
      },
    });

    renderPage('/families/F1/chromosome/1?start=100&end=200');

    await waitFor(() => expect(lastWorkspaceProps()?.tracksFailure).toBeTruthy());
    const requestsBefore = availabilityAsked;
    act(() => lastWorkspaceProps()?.tracksFailure.retry());
    await waitFor(() => expect(availabilityAsked).toBeGreaterThan(requestsBefore));
  });

  // Without the chromosome's length there is no region to wait for: the failure is handed on
  // to be said in place of the tracks.
  it('passes a failed chromosome-length request on as a failure, not an endless load', async () => {
    let lengthAsked = 0;
    serve({
      members: [PROBAND],
      lengthReply: () => {
        lengthAsked += 1;
        return failure();
      },
      availabilityReply: () => Promise.resolve({ data: {} }),
    });

    renderPage('/families/F1/chromosome/1');

    await waitFor(() => expect(lastWorkspaceProps()?.tracksFailure?.what).toBe('the length of chr1'));
    expect(lastWorkspaceProps()?.showViewerLoading).toBe(false);
    const requestsBefore = lengthAsked;
    act(() => lastWorkspaceProps()?.tracksFailure.retry());
    await waitFor(() => expect(lengthAsked).toBeGreaterThan(requestsBefore));
  });

  it('says the family could not be loaded, not that it was not found', async () => {
    (api.get as unknown as Mock).mockImplementation(failure);

    renderPage('/families/F1/chromosome/1');

    expect(await screen.findByRole('heading', { name: 'Family could not be loaded' })).toBeInTheDocument();
    expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('honors repeated sample params when initializing visible members', async () => {
    serve({
      members: [MOTHER, PROBAND, FATHER],
      samples: { PROBAND: EVERY_TRACK, MOTHER: tracks('coverage'), FATHER: tracks('coverage', 'variants') },
    });

    renderPage('/families/F1/chromosome/1?sample=PROBAND&sample=FATHER&project_id=p1');

    await waitFor(() =>
      expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('0:1000|PROBAND,FATHER|'),
    );

    expect(visibleSamples()).toEqual(['PROBAND', 'FATHER']);
    expect(screen.getByTestId('location-search')).toHaveTextContent('sample=PROBAND');
    expect(screen.getByTestId('location-search')).toHaveTextContent('sample=FATHER');
  });

  it('normalizes mitochondrial chromosome routes for track and IGV links', async () => {
    serve({
      members: [{ ...PROBAND, sex: 'female' }],
      chromosome: { chr: 'MT', size: 16_569 },
      samples: { PROBAND: tracks('coverage', 'small_variants') },
    });

    renderPage('/families/F1/chromosome/chrM?start=3200&end=3300&project_id=p1');

    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('3200:3300'));

    const chromosomeCall = requested().find((url) => url.startsWith('/chromosomes/GRCh38/'));
    expect(chromosomeCall).toBe('/chromosomes/GRCh38/MT');

    // The track-availability request can fire after the workspace first renders
    // (separate query), so poll for it rather than reading mock.calls once, which under
    // load can run before it.
    await waitFor(() => expect(availabilityRequests()[0]).toContain('chrom=MT'));

    expect(lastWorkspaceProps().chrom).toBe('MT');
    expect(decodeURIComponent(lastWorkspaceProps().igvHref)).toContain('locus=chrM:3200-3300');
  });

  it('checks small-variant availability at any zoom (no span gate)', async () => {
    serve({
      members: [PROBAND],
      chromosome: { chr: '1', size: 10_000_000 },
      samples: { PROBAND: tracks('coverage', 'small_variants') },
    });

    renderPage('/families/F1/chromosome/1?project_id=p1');

    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toBeInTheDocument());

    await waitFor(() => expect(availabilityRequests()[0]).toContain('include_small_variants=true'));
    expect(lastWorkspaceProps().availability.PROBAND.smallVariants).toBe(true);
  });

  it('keeps a sample unselected after the region changes', async () => {
    serve({
      members: [MOTHER, PROBAND, FATHER],
      samples: { PROBAND: EVERY_TRACK, MOTHER: EVERY_TRACK, FATHER: EVERY_TRACK },
    });

    renderPage('/families/F1/chromosome/1?project_id=p1');

    // No ?sample= filter → all three members start selected.
    await waitFor(() => expect(visibleSamples()).toEqual(['PROBAND', 'FATHER', 'MOTHER']));

    // Unselect MOTHER via the sidebar's toggle callback.
    const toggleSample = sidebarSpy.mock.calls.at(-1)?.[0].onToggleSample as (id: string) => void;
    await act(async () => {
      toggleSample('MOTHER');
    });
    await waitFor(() => expect(visibleSamples()).toEqual(['PROBAND', 'FATHER']));

    // Change the displayed region — this rebuilds location.search.
    fireEvent.click(screen.getByRole('button', { name: 'Select region' }));
    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('120:180'));

    // MOTHER stays unselected.
    expect(visibleSamples()).toEqual(['PROBAND', 'FATHER']);
  });
});
