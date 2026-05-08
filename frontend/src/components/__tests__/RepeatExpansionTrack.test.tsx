import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
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
});
