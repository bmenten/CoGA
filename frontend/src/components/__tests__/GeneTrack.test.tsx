// The gene track (#526: it had no tests): where each gene is drawn in the region, how
// overlapping genes stack, the exon/intron and strand drawing, and the panel tooltip.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock, refetchMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
  refetchMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
  keepPreviousData: (previous: unknown) => previous,
}));

vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import GeneTrack from '../visualizations/GeneTrack';

type TestGene = {
  gene_id: string;
  hgnc_symbol: string;
  start: number;
  end: number;
  strand: number;
  exons: { start: number; end: number; name: string }[];
};

const gene = (overrides: Partial<TestGene>): TestGene => ({
  gene_id: 'ENSG0',
  hgnc_symbol: 'GENE',
  start: 0,
  end: 100,
  strand: 1,
  exons: [],
  ...overrides,
});

const answer = ({
  genes,
  panels = [],
  isError = false,
}: {
  genes: TestGene[] | undefined;
  panels?: { name: string; genes: string[] }[];
  isError?: boolean;
}) => {
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'gene-panels'
      ? { data: panels }
      : { data: isError ? undefined : genes, isError, refetch: refetchMock },
  );
};

const renderTrack = (regionStart = 0, regionEnd = 1000) =>
  render(
    <GeneTrack assembly="GRCh38" chrom="1" width={1000} regionStart={regionStart} regionEnd={regionEnd} />,
  );

const geneGroups = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<SVGGElement>('svg > g')).map((group) => {
    const [, x, y] = /translate\(([-\d.]+),([-\d.]+)\)/.exec(group.getAttribute('transform') || '') ?? [];
    return { group, x: Number(x), y: Number(y) };
  });

beforeEach(() => {
  useQueryMock.mockReset();
  refetchMock.mockReset();
  document.body.querySelectorAll('.viz-tooltip').forEach((node) => node.remove());
});

test('asks for the genes of the displayed region', () => {
  answer({ genes: [] });
  renderTrack(200, 800);
  const geneQuery = useQueryMock.mock.calls
    .map(([options]) => options as { queryKey: unknown[]; enabled?: boolean })
    .find((options) => options.queryKey[0] === 'genes');
  expect(geneQuery?.queryKey).toEqual(['genes', 'GRCh38', '1', 200, 800]);
  expect(geneQuery?.enabled).toBe(true);
});

test('stacks overlapping genes on separate lines and reuses a line once it is free', async () => {
  answer({
    genes: [
      gene({ hgnc_symbol: 'A', start: 100, end: 400 }),
      gene({ hgnc_symbol: 'B', start: 300, end: 600 }),
      gene({ hgnc_symbol: 'C', start: 500, end: 900 }),
    ],
  });
  const { container } = renderTrack();
  await waitFor(() => expect(geneGroups(container)).toHaveLength(3));
  // Region 0–1000 over 1000 px: 1 px per bp. Lines are 12 px apart, starting at y = 2.
  expect(geneGroups(container).map(({ x, y }) => [x, y])).toEqual([
    [100, 2], // A on line 0
    [300, 14], // B overlaps A → line 1
    [500, 2], // C starts after A ends → back on line 0
  ]);
  expect(container.querySelector('svg')?.getAttribute('height')).toBe('28'); // 2 lines × 12 + 4
});

test('clips a gene that runs past the region edges', async () => {
  answer({ genes: [gene({ start: -500, end: 1500 })] });
  const { container } = renderTrack();
  await waitFor(() => expect(geneGroups(container)).toHaveLength(1));
  expect(geneGroups(container)[0].x).toBe(0);
  expect(container.querySelector('line.gene-body')?.getAttribute('x2')).toBe('1000');
});

test('still shows the gene when the view falls inside one of its introns', async () => {
  // Exons at 0–100 and 900–1000; the view is 300–600, inside the intron.
  answer({
    genes: [
      gene({
        start: 0,
        end: 1000,
        exons: [
          { start: 0, end: 100, name: 'e1' },
          { start: 900, end: 1000, name: 'e2' },
        ],
      }),
    ],
  });
  const { container } = renderTrack(300, 600);
  await waitFor(() => expect(geneGroups(container)).toHaveLength(1));
  expect(container.querySelectorAll('rect.exon')).toHaveLength(0);
  // Before, nothing but the strand arrow was drawn: the view looked intergenic.
  const body = container.querySelector('line.gene-body');
  expect(body?.getAttribute('x1')).toBe('0');
  expect(body?.getAttribute('x2')).toBe('1000');
});

test('draws the exons of a wide gene over its body, only where they fall in the region', async () => {
  answer({
    genes: [
      gene({
        start: 100,
        end: 700,
        exons: [
          { start: 500, end: 700, name: 'e3' },
          { start: 100, end: 200, name: 'e1' },
          { start: 300, end: 400, name: 'e2' },
          { start: 5000, end: 6000, name: 'outside' },
        ],
      }),
    ],
  });
  const { container } = renderTrack();
  await waitFor(() => expect(container.querySelectorAll('rect.exon')).toHaveLength(3));
  // Exon x is relative to the gene start (100), sorted by position.
  expect(
    Array.from(container.querySelectorAll('rect.exon')).map((exon) => [
      exon.getAttribute('x'),
      exon.getAttribute('width'),
    ]),
  ).toEqual([
    ['0', '100'],
    ['200', '100'],
    ['400', '200'],
  ]);
  // The introns are the gene body between them: one line over the gene's extent.
  const body = container.querySelector('line.gene-body');
  expect([body?.getAttribute('x1'), body?.getAttribute('x2')]).toEqual(['0', '600']);
});

test('points the strand arrow the way the gene is read', async () => {
  answer({
    genes: [
      gene({ hgnc_symbol: 'PLUS', start: 100, end: 110, strand: 1 }),
      gene({ hgnc_symbol: 'MINUS', start: 300, end: 310, strand: -1 }),
    ],
  });
  const { container } = renderTrack();
  await waitFor(() => expect(container.querySelectorAll('svg > g > path')).toHaveLength(2));
  const [plus, minus] = Array.from(container.querySelectorAll('svg > g > path')).map((path) =>
    path.getAttribute('d'),
  );
  // A 10 px gene: the + arrow's tip is at the right end, the − arrow's at x = 0.
  expect(plus).toBe('M 6 0 L 10 4 L 6 8');
  expect(minus).toBe('M 4 0 L 0 4 L 4 8');
});

test('keeps a gene narrower than 6 px visible as a 6 px block', async () => {
  answer({ genes: [gene({ start: 100, end: 102 })] });
  const { container } = renderTrack();
  await waitFor(() => expect(container.querySelectorAll('svg > g > rect')).toHaveLength(1));
  expect(container.querySelector('svg > g > rect')?.getAttribute('width')).toBe('6');
  expect(container.querySelector('svg > g > path')).toBeNull();
});

test('names the gene and its panels on hover, with the symbol escaped', async () => {
  answer({
    genes: [gene({ hgnc_symbol: 'BRCA2<img src=x onerror=alert(1)>', start: 100, end: 400 })],
    panels: [
      { name: 'Hereditary cancer', genes: ['BRCA2<img src=x onerror=alert(1)>'] },
      { name: 'Unrelated', genes: ['TP53'] },
    ],
  });
  const { container } = renderTrack();
  await waitFor(() => expect(geneGroups(container)).toHaveLength(1));
  fireEvent.mouseMove(geneGroups(container)[0].group, { clientX: 50, clientY: 20 });
  const tooltip = document.body.querySelector<HTMLElement>('.viz-tooltip');
  expect(tooltip?.style.display).toBe('block');
  expect(tooltip?.textContent).toBe('BRCA2<img src=x onerror=alert(1)>Panels: Hereditary cancer');
  expect(tooltip?.querySelector('img')).toBeNull();
  fireEvent.mouseOut(geneGroups(container)[0].group);
  expect(tooltip?.style.display).toBe('none');
});

test('says so when the region has no genes', () => {
  answer({ genes: [] });
  renderTrack();
  expect(screen.getByText('No genes in this region')).toBeInTheDocument();
});

test('shows a failed request as a failure with a retry, never as "no genes"', () => {
  answer({ genes: undefined, isError: true });
  renderTrack();
  expect(screen.queryByText('No genes in this region')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetchMock).toHaveBeenCalledTimes(1);
});

test('names the genes in the region for a screen reader: how many, and the first three as drawn (#529)', () => {
  answer({
    genes: [
      gene({ hgnc_symbol: 'C', start: 500, end: 900 }),
      gene({ hgnc_symbol: 'A', start: 100, end: 400 }),
      gene({ hgnc_symbol: 'B', start: 300, end: 600 }),
      gene({ hgnc_symbol: 'D', start: 950, end: 990 }),
    ],
  });
  const { rerender } = renderTrack();
  expect(screen.getByRole('img', { name: 'Genes on chr1:0–1,000: 4 (A, B, C, …)' })).toBeInTheDocument();

  answer({ genes: [gene({ hgnc_symbol: 'BRCA2', start: 100, end: 400 })] });
  rerender(<GeneTrack assembly="GRCh38" chrom="chr13" width={1000} regionStart={0} regionEnd={1000} />);
  expect(screen.getByRole('img', { name: 'Genes on chr13:0–1,000: 1 (BRCA2)' })).toBeInTheDocument();
});

test.each([
  ['an empty region', { genes: [] }, 'none'],
  ['a request in flight', { genes: undefined }, 'loading'],
  ['a failed request (#510)', { genes: undefined, isError: true }, 'failed to load'],
])('names %s as what it is, and only an empty region as "none" (#529)', (_label, state, summary) => {
  answer(state);
  renderTrack();
  expect(screen.getByRole('img', { name: `Genes on chr1:0–1,000: ${summary}` })).toBeInTheDocument();
});

test('while a pan loads, names only the held genes in the new region, never "none" (#529)', () => {
  const region = (regionStart: number, regionEnd: number) => (
    <GeneTrack assembly="GRCh38" chrom="1" width={1000} regionStart={regionStart} regionEnd={regionEnd} />
  );
  answer({
    genes: [
      gene({ hgnc_symbol: 'A', start: 100, end: 400 }),
      gene({ hgnc_symbol: 'B', start: 600, end: 900 }),
    ],
  });
  const { rerender } = render(region(0, 1000));
  expect(screen.getByRole('img', { name: 'Genes on chr1:0–1,000: 2 (A, B)' })).toBeInTheDocument();

  // Same span, 500 bp right, the new window still loading: A is now left of the region.
  answer({ genes: undefined });
  rerender(region(500, 1500));
  expect(screen.getByRole('img', { name: 'Genes on chr1:500–1,500: 1 (B)' })).toBeInTheDocument();

  // No held gene lies in this window, but its genes have not arrived: not "none".
  rerender(region(1000, 2000));
  expect(screen.getByRole('img', { name: 'Genes on chr1:1,000–2,000: loading' })).toBeInTheDocument();

  answer({ genes: [] });
  rerender(region(1000, 2000));
  expect(screen.getByRole('img', { name: 'Genes on chr1:1,000–2,000: none' })).toBeInTheDocument();
});

test('while a pan loads, a held gene left of the new window is not drawn at its edge (#586)', async () => {
  const region = (regionStart: number, regionEnd: number) => (
    <GeneTrack assembly="GRCh38" chrom="1" width={1000} regionStart={regionStart} regionEnd={regionEnd} />
  );
  answer({
    genes: [
      gene({ hgnc_symbol: 'A', start: 100, end: 400 }),
      gene({ hgnc_symbol: 'B', start: 600, end: 900 }),
    ],
  });
  const { container, rerender } = render(region(0, 1000));
  await waitFor(() => expect(geneGroups(container)).toHaveLength(2));

  // Same span, 500 bp right, the new window still loading: A now lies wholly left of it.
  // It was drawn as a 6 px block at x = 0, as if a gene started there.
  answer({ genes: undefined });
  rerender(region(500, 1500));
  await waitFor(() => expect(geneGroups(container)).toHaveLength(1));
  expect(geneGroups(container)[0].x).toBe(100); // B, 100 px into the new window
});

test('a failed pan draws nothing from the previous window (#586)', async () => {
  const region = (regionStart: number, regionEnd: number) => (
    <GeneTrack assembly="GRCh38" chrom="1" width={1000} regionStart={regionStart} regionEnd={regionEnd} />
  );
  answer({ genes: [gene({ hgnc_symbol: 'B', start: 600, end: 900 })] });
  const { container, rerender } = render(region(0, 1000));
  await waitFor(() => expect(geneGroups(container)).toHaveLength(1));

  answer({ genes: undefined, isError: true });
  rerender(region(500, 1500));
  await waitFor(() => expect(geneGroups(container)).toHaveLength(0));
  expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
});
