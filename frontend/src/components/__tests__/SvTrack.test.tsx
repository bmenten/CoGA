// Genome-wide SV track: the accessible name of its canvas summarises what it draws now —
// the sample's SVs on the displayed chromosomes, by type — and names a load or a failure
// as such, never as zero SVs (#510, #529).
import { render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));

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
      data: [
        sv('DEL', '1', 100_000),
        sv('DUP', 'chr2', 200_000, 'S1', '1/1'),
        sv('DEL', '2', 400_000),
        // Not drawn, so not counted: off the displayed chromosomes, not the sample's,
        // not carried, or not a type the track draws.
        sv('DEL', '3', 100_000),
        sv('INV', '1', 300_000, 'S2'),
        sv('INS', '1', 500_000, 'S1', '0/0'),
        sv('CNV', '1', 600_000),
      ],
    });
    render(track());

    expect(
      screen.getByRole('img', { name: 'Structural variants of S1 in view: 3 (2 DEL, 1 DUP)' }),
    ).toBeInTheDocument();
  });

  it('an empty view is named as empty', () => {
    serve({ data: [] });
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
});
