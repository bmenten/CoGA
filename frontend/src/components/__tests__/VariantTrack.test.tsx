import { fireEvent, render, screen } from '@testing-library/react';
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
        // Not carried by S1: not counted.
        sv('DEL', 32_340_000, '0/0'),
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

test('a failed pan draws nothing from the previous window (#586)', () => {
  useQueryMock.mockReturnValue({ data: { variants: [sv('DEL', 32_360_000)] }, isLoading: false });
  const { container, rerender } = render(brca2Track());
  expect(container.querySelectorAll('[data-variant-type]')).toHaveLength(1);

  // Same span, 50 kb right: while the new window loads, the held SV glides along…
  useQueryMock.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
  rerender(brca2Track(32_350_000));
  expect(container.querySelectorAll('[data-variant-type]')).toHaveLength(1);

  // …but once the request fails, the track draws nothing from the previous window.
  useQueryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
  rerender(brca2Track(32_350_000));
  expect(container.querySelectorAll('[data-variant-type]')).toHaveLength(0);
});

// CLIN-54: the track drew DEL, DUP, INV, INS and BND by their exact names only, so a DEL:ME, a
// DUP:TANDEM, an INS:ME, a CNV or a TRA was not drawn, though the SV list showed it.
test('draws every SV the list shows: by its type without subtypes, a CNV by its copy change, a TRA as a BND, any other type in the Other row (CLIN-54)', () => {
  useQueryMock.mockReturnValue({
    data: {
      // As the track payload serves them: the copy change is left out when there is none.
      variants: [
        { ...sv('DEL:ME', 32_310_000), copy_change: 'loss' },
        { ...sv('DUP:TANDEM', 32_320_000), copy_change: 'gain' },
        sv('INS:ME', 32_330_000),
        { ...sv('CNV', 32_340_000), copy_change: 'loss' },
        { ...sv('CNV', 32_350_000), copy_change: 'gain' },
        // A CNV that keeps the sample's copies, and a type no row names: the Other row.
        sv('CNV', 32_360_000),
        sv('TRA', 32_370_000),
        sv('CPX', 32_380_000),
      ],
    },
    isLoading: false,
  });
  const { container } = render(brca2Track());

  expect(
    screen.getByRole('img', {
      name: 'Structural variants of S1 on chr13:32,300,000–32,400,000: 8 (2 DEL, 2 DUP, 1 INS, 1 BND, 2 other)',
    }),
  ).toBeInTheDocument();
  const marks = [...container.querySelectorAll('[data-variant-type]')];
  expect(marks.map((mark) => mark.getAttribute('data-variant-type'))).toEqual([
    'DEL',
    'DUP',
    'INS',
    'DEL',
    'DUP',
    'OTHER',
    'BND',
    'OTHER',
  ]);
  // An insertion is a line, a breakend a triangle; the Other row is the sixth and last.
  expect(marks[2].tagName).toBe('line');
  expect(marks[6].tagName).toBe('path');
  expect(screen.getByText('Other')).toBeInTheDocument();
  expect(Number(marks[7].getAttribute('y'))).toBeGreaterThan((80 * 5) / 6);
});

test('names an SV of another type, and a CNV with its copy change and copy number, in its tooltip (CLIN-54)', () => {
  useQueryMock.mockReturnValue({
    data: {
      variants: [
        { ...sv('CPX', 32_310_000), source: 'caller' },
        { ...sv('CNV', 32_320_000), genotypes: [{ sample: 'S1', gt: '0/1', cn: 3 }], copy_change: 'gain' },
      ],
    },
    isLoading: false,
  });
  const { container } = render(brca2Track());
  const [other, cnv] = [...container.querySelectorAll('[data-variant-type]')];

  fireEvent.mouseMove(other, { clientX: 10, clientY: 70 });
  expect(screen.getByText('chr13:32310000-32315000 CPX [caller]')).toBeInTheDocument();

  fireEvent.mouseMove(cnv, { clientX: 20, clientY: 20 });
  expect(screen.getByText('chr13:32320000-32325000 CNV (copy-number gain)')).toBeInTheDocument();
  expect(screen.getByText('S1: Het CN:3')).toBeInTheDocument();
});

// The tooltip wrote the chromosome as the SV is stored (`M:10-30 DEL`, `1:50-70 DUP`), where
// the viewer names it chrM and chr1 (#602), as the genome overview's SV tooltip does since
// ENG-20 (found while fixing ENG-20, #1030). The mitochondrial SV dataset (mito_sv) comes as `M`.
test.each([
  ['M', 'MT', 'chrM:10-30 DEL'],
  ['chrM', 'MT', 'chrM:10-30 DEL'],
  ['MT', 'MT', 'chrM:10-30 DEL'],
  ['1', '1', 'chr1:10-30 DEL'],
  ['chr1', '1', 'chr1:10-30 DEL'],
])('names an SV stored on %s on the chromosome as the viewer does', (chr, chrom, text) => {
  useQueryMock.mockReturnValue({
    data: { variants: [{ chr, start: 10, end: 30, type: 'DEL', genotypes: [{ sample: 'S1', gt: '0/1' }] }] },
    isLoading: false,
  });
  const { container } = render(
    <VariantTrack familyId="F1" sampleId="S1" chrom={chrom} regionStart={0} regionEnd={100} width={100} height={80} />,
  );

  fireEvent.mouseMove(container.querySelector('[data-variant-type="DEL"]') as Element, { clientX: 15, clientY: 5 });

  expect(screen.getByText(text)).toBeInTheDocument();
});
