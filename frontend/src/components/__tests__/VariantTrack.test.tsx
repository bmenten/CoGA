import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
  keepPreviousData: (previous: unknown) => previous,
}));

import VariantTrack from '../visualizations/VariantTrack';

test('renders structural variant types on separate vertical rows', () => {
  useQueryMock.mockReturnValue({
    data: {
      variants: [
        {
          chr: '1',
          start: 10,
          end: 30,
          type: 'DEL',
          genotypes: [{ sample: 'S1', gt: '0/1' }],
        },
        {
          chr: '1',
          start: 12,
          end: 32,
          type: 'DUP',
          genotypes: [{ sample: 'S1', gt: '1/1' }],
        },
      ],
    },
    isLoading: false,
  });

  const { container } = render(
    <VariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={80}
    />
  );

  expect(screen.getByText('DEL')).toBeInTheDocument();
  expect(screen.getByText('DUP')).toBeInTheDocument();
  expect(screen.getByText('INV')).toBeInTheDocument();
  expect(screen.getByText('INS')).toBeInTheDocument();
  expect(screen.getByText('BND')).toBeInTheDocument();

  const delVariant = container.querySelector('[data-variant-type="DEL"]');
  const dupVariant = container.querySelector('[data-variant-type="DUP"]');

  expect(delVariant).not.toBeNull();
  expect(dupVariant).not.toBeNull();
  expect(Number(delVariant?.getAttribute('y'))).toBeLessThan(Number(dupVariant?.getAttribute('y')));
});

test('renders message when no structural variants are available', () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });

  render(
    <VariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={80}
    />
  );

  expect(screen.getByText(/no svs for this region \/ sample/i)).toBeInTheDocument();
});

// The chart's accessible name summarises what it draws now (#529).
const sv = (type: string, start: number, gt = '0/1') => ({
  chr: '13',
  start,
  end: start + 5_000,
  type,
  genotypes: [{ sample: 'S1', gt }],
});

const brca2Track = (regionStart = 32_300_000) => (
  <VariantTrack
    familyId="F1"
    sampleId="S1"
    chrom="13"
    regionStart={regionStart}
    regionEnd={regionStart + 100_000}
    width={100}
    height={80}
  />
);

test('names the drawn SVs by type (#529)', () => {
  useQueryMock.mockReturnValue({
    data: {
      variants: [
        sv('DEL', 32_310_000),
        sv('DUP', 32_320_000, '1/1'),
        sv('del', 32_330_000),
        // Not carried by S1, or not a type the track draws: not counted.
        sv('DEL', 32_340_000, '0/0'),
        sv('CNV', 32_350_000),
      ],
    },
    isLoading: false,
  });
  render(brca2Track());

  expect(
    screen.getByRole('img', {
      name: 'Structural variants of S1 on chr13:32,300,000–32,400,000: 3 (2 DEL, 1 DUP)',
    }),
  ).toBeInTheDocument();
});

test('an empty region is named as empty (#529)', () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  render(brca2Track());

  expect(
    screen.getByRole('img', { name: 'Structural variants of S1 on chr13:32,300,000–32,400,000: none' }),
  ).toBeInTheDocument();
});

test('a load or a failure is named as such, never as the held window’s count (#510, #529)', () => {
  useQueryMock.mockReturnValue({ data: { variants: [sv('DEL', 32_310_000)] }, isLoading: false });
  const { rerender } = render(brca2Track());
  expect(screen.getByRole('img', { name: /: 1 \(1 DEL\)$/ })).toBeInTheDocument();

  // A pan keeps the previous window's SVs on hand while the next one loads…
  useQueryMock.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
  rerender(brca2Track(32_350_000));
  expect(
    screen.getByRole('img', { name: 'Structural variants of S1 on chr13:32,350,000–32,450,000: loading' }),
  ).toBeInTheDocument();

  // …and a failed request is a failure, not those SVs and not "none".
  useQueryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
  rerender(brca2Track(32_350_000));
  expect(
    screen.getByRole('img', {
      name: 'Structural variants of S1 on chr13:32,350,000–32,450,000: failed to load',
    }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Could not load structural variants/)).toBeInTheDocument();
});

test('a view holding more SVs than one page says too many instead of drawing the page (#585)', () => {
  // The page is the first two by position of five in view: drawn, the right of the view
  // would look free of SVs.
  useQueryMock.mockReturnValue({
    data: { total: 5, variants: [sv('DEL', 32_310_000), sv('DUP', 32_320_000)] },
    isLoading: false,
  });
  const { container } = render(brca2Track());

  expect(
    screen.getByRole('img', {
      name: 'Structural variants of S1 on chr13:32,300,000–32,400,000: too many to display; zoom in or apply filters',
    }),
  ).toBeInTheDocument();
  expect(screen.getByText('Too many SVs to display. Zoom in or apply filters.')).toBeInTheDocument();
  expect(container.querySelector('[data-variant-type]')).toBeNull();
});

test('past the backend cap the track says too many as well (#585)', () => {
  useQueryMock.mockReturnValue({
    data: { total: 2, total_is_estimated: true, count_limit: 2, variants: [sv('DEL', 32_310_000), sv('DEL', 32_320_000)] },
    isLoading: false,
  });
  render(brca2Track());

  expect(screen.getByText('Too many SVs to display. Zoom in or apply filters.')).toBeInTheDocument();
});

test('a complete page is drawn', () => {
  useQueryMock.mockReturnValue({
    data: { total: 2, variants: [sv('DEL', 32_310_000), sv('DUP', 32_320_000)] },
    isLoading: false,
  });
  const { container } = render(brca2Track());

  expect(container.querySelectorAll('[data-variant-type]')).toHaveLength(2);
  expect(screen.queryByText(/Too many SVs/)).not.toBeInTheDocument();
});
