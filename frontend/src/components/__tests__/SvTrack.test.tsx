// Genome-wide SV track: the accessible name of its canvas summarises what it draws now —
// the sample's SVs on the displayed chromosomes, by type — and names a load or a failure
// as such, never as zero SVs (#510, #529).
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { useQueryMock, fetchTrackJsonMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
  fetchTrackJsonMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../lib/trackFetch', () => ({ fetchTrackJson: fetchTrackJsonMock }));

import SvTrack from '../visualizations/SvTrack';

type Props = ComponentProps<typeof SvTrack>;

const LAYOUT = {
  offsets: { '1': 0, '2': 1_000_000 },
  lengths: { '1': 1_000_000, '2': 1_000_000 },
  total: 2_000_000,
};

const track = (overrides: Partial<Props> = {}) => (
  <SvTrack
    url="/api/families/F1/structural-variants?sample=S1"
    layout={LAYOUT}
    sampleId="S1"
    width={200}
    height={40}
    {...overrides}
  />
);

const serve = (state: { data?: unknown; isLoading?: boolean; isError?: boolean }) =>
  useQueryMock.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...state,
  });

// What the track's query resolves to: the SVs, and whether the backend's cap cut them.
const page = (variants: unknown[], capped = false) => ({ variants, capped });

const sv = (type: string, chr: string, start: number, sample = 'S1', gt = '0/1') => ({
  chr,
  start,
  end: start + 10_000,
  type,
  genotypes: [{ sample, gt }],
});

describe('SvTrack accessible name (#529)', () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  it('names the drawn SVs by type, counting only what is drawn', () => {
    serve({
      data: page([
        sv('DEL', '1', 100_000),
        sv('DUP', 'chr2', 200_000, 'S1', '1/1'),
        sv('DEL', '2', 400_000),
        // Not drawn, so not counted: off the displayed chromosomes, not the sample's, or
        // not carried.
        sv('DEL', '3', 100_000),
        sv('INV', '1', 300_000, 'S2'),
        sv('INS', '1', 500_000, 'S1', '0/0'),
      ]),
    });
    render(track());

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: 3 (2 DEL, 1 DUP)' }),
    ).toBeInTheDocument();
  });

  it('an empty view is named as empty', () => {
    serve({ data: page([]) });
    render(track());

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: none' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/no SVs for this region \/ sample/)).toBeInTheDocument();
  });

  it('a load is named as loading, not as zero SVs', () => {
    serve({ isLoading: true });
    render(track());

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: loading' }),
    ).toBeInTheDocument();
  });

  it('a failure is named as a failure, never as zero SVs (#510)', () => {
    serve({ isError: true });
    render(track());

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: failed to load' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load structural variants');
    expect(screen.queryByText(/no SVs for this region/)).not.toBeInTheDocument();
  });

  it('past the backend cap, says too many instead of drawing part of the genome (#585)', () => {
    serve({ data: page([sv('DEL', '1', 100_000), sv('DUP', '2', 200_000)], true) });
    render(track());

    // The list stopped part-way: drawn, the last chromosomes would look free of SVs.
    expect(
      screen.getByRole('img', {
        name: 'Structural variants of S1 in view: too many to display; open a chromosome',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Too many SVs to display genome-wide/)).toBeInTheDocument();
    expect(screen.queryByText(/no SVs for this region/)).not.toBeInTheDocument();
  });

  // #602 — a track that asked for nothing found nothing: it is not "none".
  it('before the genome layout is known it asks for nothing and is loading, not empty', () => {
    serve({});
    render(track({ layout: null }));

    expect((useQueryMock.mock.calls.at(-1)?.[0] as { enabled: boolean }).enabled).toBe(false);
    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: loading' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no SVs for this region/)).not.toBeInTheDocument();
  });

  it('without a source it asks for nothing and says there is no SV data, not no SVs', () => {
    serve({});
    render(track({ url: '' }));

    expect((useQueryMock.mock.calls.at(-1)?.[0] as { enabled: boolean }).enabled).toBe(false);
    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: no SV data for this sample' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no SVs for this region/)).not.toBeInTheDocument();
  });
});

describe('SvTrack request (#585)', () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    fetchTrackJsonMock.mockReset();
  });

  it('reads the cap flag from the response', async () => {
    serve({});
    render(track());
    const { queryFn } = useQueryMock.mock.calls[0][0] as {
      queryFn: (context: { signal: AbortSignal }) => Promise<unknown>;
    };
    const signal = new AbortController().signal;

    fetchTrackJsonMock.mockResolvedValueOnce({ variants: [sv('DEL', '1', 1)], total_is_estimated: true });
    await expect(queryFn({ signal })).resolves.toEqual({ variants: [sv('DEL', '1', 1)], capped: true });
    expect(fetchTrackJsonMock).toHaveBeenCalledWith(
      '/api/families/F1/structural-variants?sample=S1',
      { signal },
    );

    fetchTrackJsonMock.mockResolvedValueOnce({ variants: [], total: 0 });
    await expect(queryFn({ signal })).resolves.toEqual({ variants: [], capped: false });
  });
});

// CLIN-54: the track drew DEL, DUP, INV, INS and BND by their exact names only, so a DEL:ME, a
// DUP:TANDEM, an INS:ME, a CNV or a TRA was not drawn, though the SV list showed it.
describe('SvTrack draws every SV the list shows (CLIN-54)', () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  // As the track payload serves them: the copy change is left out when there is none.
  const drawn = [
    { ...sv('DEL:ME', '1', 100_000), copy_change: 'loss' },
    { ...sv('DUP:TANDEM', '1', 200_000), copy_change: 'gain' },
    sv('INS:ME', '1', 300_000),
    { ...sv('CNV', '1', 400_000), copy_change: 'loss' },
    { ...sv('CNV', '2', 100_000), copy_change: 'gain' },
    // A CNV that keeps the sample's copies, and a type no row names: the Other row.
    sv('CNV', '2', 200_000),
    sv('TRA', '2', 300_000),
    sv('CPX', '2', 400_000),
  ];

  it('draws an SV by its type without subtypes, a CNV by its copy change, a TRA as a BND and any other type as other', () => {
    serve({ data: page(drawn) });
    render(track());

    expect(
      screen.getByRole('img', {
        name: 'Structural variants of S1 in view: 8 (2 DEL, 2 DUP, 1 INS, 1 BND, 2 other)',
      }),
    ).toBeInTheDocument();
  });

  it('labels the six rows, the Other row last', () => {
    const labels: string[] = [];
    const context = HTMLCanvasElement.prototype.getContext.call(document.createElement('canvas'), '2d');
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation(
        () =>
          ({
            ...context,
            fillText: (text: string) => labels.push(text),
          }) as unknown as CanvasRenderingContext2D,
      );
    serve({ data: page(drawn) });
    render(track({ height: 80 }));

    // The row labels of the last draw.
    expect(labels.slice(-6)).toEqual(['DEL', 'DUP', 'INV', 'INS', 'BND', 'Other']);
    getContext.mockRestore();
  });

  it('names an SV of another type, and a CNV with its copy change and copy number, in its tooltip', () => {
    serve({
      data: page([
        // Far less than a pixel wide on this track: hovered over the pixel it is drawn on.
        { chr: '2', start: 400_000, end: 400_100, type: 'CPX', source: 'caller', genotypes: [{ sample: 'S1', gt: '0/1' }] },
        {
          chr: '1',
          start: 400_000,
          end: 400_100,
          type: 'CNV',
          copy_change: 'loss',
          genotypes: [{ sample: 'S1', gt: '0/1', cn: 1 }],
        },
      ]),
    });
    render(track({ height: 80 }));
    const canvas = screen.getByRole('img');

    // 200 px over 2 Mb: chr2:400,000 is at x 140. Six rows of 80/6 px: the Other row is the
    // last, the DEL row the first.
    fireEvent.mouseMove(canvas, { clientX: 140.5, clientY: 72 });
    expect(screen.getByText('2:400000-400100 CPX [caller]')).toBeInTheDocument();

    fireEvent.mouseMove(canvas, { clientX: 40.5, clientY: 8 });
    expect(screen.getByText('1:400000-400100 CNV (copy-number loss)')).toBeInTheDocument();
    expect(screen.getByText('S1: Het CN:1')).toBeInTheDocument();
  });
});

// Found while fixing the viewer's chrM names (#1023): the track looked an SV up in the layout
// under its stored name, then without `chr`, so an SV stored as `chrM` or `M` never found the
// layout's `MT`, and was neither drawn nor counted. The mitochondrial SV dataset (mito_sv) comes
// as `M`: its VCF's chromosome name without `chr`.
describe('SvTrack places an SV on the mitochondrion however it is stored', () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  // The genome overview's layout, which names the mitochondrion MT: chr1, then MT at 1 Mb.
  const WITH_MT = {
    offsets: { '1': 0, MT: 1_000_000 },
    lengths: { '1': 1_000_000, MT: 16_569 },
    total: 2_000_000,
  };

  it.each(['chrM', 'M', 'MT', 'chrMT'])('draws and counts an SV stored on %s', (chr) => {
    serve({ data: page([sv('DEL', chr, 1_000)]) });
    render(track({ layout: WITH_MT }));

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: 1 (1 DEL)' }),
    ).toBeInTheDocument();
    // On the mitochondrion: 200 px over 2 Mb, MT:1,000 is at x 100.1, in the DEL row (the first).
    fireEvent.mouseMove(screen.getByRole('img'), { clientX: 100.5, clientY: 3 });
    expect(screen.getByText(`${chr}:1000-11000 DEL`)).toBeInTheDocument();
  });
});
