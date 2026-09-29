import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
  keepPreviousData: (previous: unknown) => previous,
}));

import RepeatExpansionTrack from '../visualizations/RepeatExpansionTrack';

describe('RepeatExpansionTrack', () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  it('renders chromosome-wide loci in chromosome view mode', () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            sample: 'S1',
            locus_id: 'locus-a',
            gene: 'GENE1',
            display_name: 'Locus A',
            disease: 'Disease A',
            chr: '1',
            start: 100,
            end: 110,
            status: 'normal',
            allele_repeat_counts: [20],
            allele_bp_lengths: [60],
          },
          {
            sample: 'S1',
            locus_id: 'locus-b',
            gene: 'GENE2',
            display_name: 'Locus B',
            disease: 'Disease B',
            chr: '1',
            start: 700,
            end: 710,
            status: 'pathogenic',
            allele_repeat_counts: [120],
            allele_bp_lengths: [360],
          },
        ],
      },
      isLoading: false,
    });

    const { container } = render(
      <RepeatExpansionTrack
        familyId="F1"
        sampleId="S1"
        chrom="1"
        regionStart={0}
        regionEnd={200}
        width={100}
        height={20}
        chromosomeSize={1000}
      />,
    );

    const locusA = container.querySelector('[data-repeat-locus-id="locus-a"]');
    const locusB = container.querySelector('[data-repeat-locus-id="locus-b"]');

    expect(locusA).not.toBeNull();
    expect(locusB).not.toBeNull();
    expect(Number(locusA?.getAttribute('x'))).toBeLessThan(Number(locusB?.getAttribute('x')));
  });

  it('renders region message when no loci overlap the visible region', () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            sample: 'S1',
            locus_id: 'locus-a',
            gene: 'GENE1',
            display_name: 'Locus A',
            disease: 'Disease A',
            chr: '1',
            start: 500,
            end: 510,
            status: 'normal',
            allele_repeat_counts: [20],
            allele_bp_lengths: [60],
          },
        ],
      },
      isLoading: false,
    });

    render(
      <RepeatExpansionTrack
        familyId="F1"
        sampleId="S1"
        chrom="1"
        regionStart={0}
        regionEnd={200}
        width={100}
        height={20}
      />,
    );

    expect(screen.getByText(/no repeat loci in this region/i)).toBeInTheDocument();
  });

  // The chart's accessible name summarises what it draws now (#529).
  const locus = (locusId: string, start: number, status: string, chr = '1') => ({
    sample: 'S1',
    locus_id: locusId,
    gene: locusId,
    display_name: locusId,
    disease: `Disease ${locusId}`,
    chr,
    start,
    end: start + 10,
    status,
    allele_repeat_counts: [20],
    allele_bp_lengths: [60],
  });

  const regionTrack = (regionStart = 0, extra: { chromosomeSize?: number } = {}) => (
    <RepeatExpansionTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={regionStart}
      regionEnd={regionStart + 200}
      width={100}
      height={20}
      {...extra}
    />
  );

  it('names the loci of the whole chromosome in chromosome view mode (#529)', () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          locus('ATXN1', 100, 'normal'),
          locus('FMR1', 700, 'pathogenic'),
          locus('AFF2', 800, 'intermediate'),
          // Another chromosome's locus is not drawn, so not counted.
          locus('HTT', 300, 'pathogenic', '4'),
        ],
      },
      isLoading: false,
    });
    render(regionTrack(0, { chromosomeSize: 1000 }));

    expect(
      screen.getByRole('img', {
        name: 'Repeat loci of S1 on chr1: 3, 1 pathogenic (FMR1), 1 intermediate',
      }),
    ).toBeInTheDocument();
  });

  it('names only the loci in the region outside chromosome view mode (#529)', () => {
    useQueryMock.mockReturnValue({
      data: { items: [locus('ATXN1', 100, 'normal'), locus('FMR1', 700, 'pathogenic')] },
      isLoading: false,
    });
    render(regionTrack());

    expect(
      screen.getByRole('img', { name: 'Repeat loci of S1 on chr1:0–200: 1, all normal' }),
    ).toBeInTheDocument();
  });

  it('an empty region is named as empty (#529)', () => {
    useQueryMock.mockReturnValue({ data: { items: [] }, isLoading: false });
    render(regionTrack());

    expect(
      screen.getByRole('img', { name: 'Repeat loci of S1 on chr1:0–200: none' }),
    ).toBeInTheDocument();
  });

  it('a load or a failure is named as such, never as the held window’s loci (#510, #529)', () => {
    useQueryMock.mockReturnValue({
      data: { items: [locus('FMR1', 250, 'pathogenic')] },
      isLoading: false,
    });
    const { rerender } = render(regionTrack(200));
    expect(
      screen.getByRole('img', { name: 'Repeat loci of S1 on chr1:200–400: 1, 1 pathogenic (FMR1)' }),
    ).toBeInTheDocument();

    // A pan keeps the previous window's loci on hand while the next one loads…
    useQueryMock.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
    rerender(regionTrack(100));
    expect(
      screen.getByRole('img', { name: 'Repeat loci of S1 on chr1:100–300: loading' }),
    ).toBeInTheDocument();

    // …and a failed request is a failure, not those loci and not "none".
    useQueryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
    rerender(regionTrack(100));
    expect(
      screen.getByRole('img', { name: 'Repeat loci of S1 on chr1:100–300: failed to load' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no repeat loci in this region/i)).not.toBeInTheDocument();
  });

  it('a failed pan draws nothing from the previous window (#586)', () => {
    useQueryMock.mockReturnValue({ data: { items: [locus('FMR1', 250, 'pathogenic')] }, isLoading: false });
    const { container, rerender } = render(regionTrack(200));
    expect(container.querySelectorAll('[data-repeat-locus-id]')).toHaveLength(1);

    // Same span, 100 bp left: while the new window loads, the held locus stays drawn…
    useQueryMock.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
    rerender(regionTrack(100));
    expect(container.querySelectorAll('[data-repeat-locus-id]')).toHaveLength(1);

    // …but not under the failure.
    useQueryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
    rerender(regionTrack(100));
    expect(container.querySelectorAll('[data-repeat-locus-id]')).toHaveLength(0);
  });
});
