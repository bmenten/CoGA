// Genome-wide SV track: the accessible name of its canvas summarises what it draws now —
// the sample's SVs on the displayed chromosomes, by type — and names a load or a failure
// as such, never as zero SVs (#510, #529).
import { render, screen } from '@testing-library/react';
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
        // Not drawn, so not counted: off the displayed chromosomes, not the sample's,
        // not carried, or not a type the track draws.
        sv('DEL', '3', 100_000),
        sv('INV', '1', 300_000, 'S2'),
        sv('INS', '1', 500_000, 'S1', '0/0'),
        sv('CNV', '1', 600_000),
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
