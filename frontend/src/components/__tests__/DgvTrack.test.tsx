import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock, keepPreviousData: (previous: unknown) => previous }));
vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import DgvTrack from '../visualizations/DgvTrack';

const renderTrack = () =>
  render(
    <DgvTrack
      assembly="GRCh38"
      chrom="1"
      width={100}
      height={48}
      regionStart={0}
      regionEnd={300}
    />,
  );

beforeEach(() => {
  useQueryMock.mockReset();
});

test('lines mode draws variants with a hover tooltip', () => {
  useQueryMock.mockReturnValue({
    data: {
      total: 1,
      mode: 'lines',
      bin_size: 0,
      variants: [
        {
          chr: '1',
          start: 100,
          end: 200,
          accession: 'nsv6138160',
          variant_subtype: 'duplication',
          variant_class: 'gain',
          frequency: 0.05,
        },
      ],
      bins: [],
    },
  });

  const { container } = renderTrack();

  const rect = container.querySelector('rect[aria-label]');
  expect(rect).not.toBeNull();
  fireEvent.mouseMove(rect as Element, { clientX: 30, clientY: 10 });

  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip).not.toBeNull();
  expect(tooltip).toHaveClass('viz-tooltip--floating');
  expect(tooltip?.textContent).toContain('nsv6138160');
  expect(tooltip?.textContent).toContain('duplication');
});

test('lines mode places gains above and losses below the baseline', () => {
  useQueryMock.mockReturnValue({
    data: {
      total: 2,
      mode: 'lines',
      bin_size: 0,
      variants: [
        { chr: '1', start: 50, end: 150, accession: 'dup1', variant_class: 'gain' },
        { chr: '1', start: 50, end: 150, accession: 'del1', variant_class: 'loss' },
      ],
      bins: [],
    },
  });

  const { container } = renderTrack(); // height 48 -> baseline 24
  const dup = container.querySelector('rect[aria-label="dup1"]') as SVGRectElement;
  const del = container.querySelector('rect[aria-label="del1"]') as SVGRectElement;
  expect(dup).not.toBeNull();
  expect(del).not.toBeNull();
  expect(Number(dup.getAttribute('y'))).toBeLessThan(24);
  expect(Number(del.getAttribute('y'))).toBeGreaterThanOrEqual(24);
});

test('density mode draws stacked bars with a per-bin tooltip', () => {
  useQueryMock.mockReturnValue({
    data: {
      total: 50000,
      mode: 'density',
      bin_size: 100,
      variants: [],
      bins: [
        { start: 0, end: 100, gain: 10, loss: 5, mixed: 2, other: 0 },
        { start: 100, end: 200, gain: 0, loss: 0, mixed: 0, other: 0 },
        { start: 200, end: 300, gain: 3, loss: 20, mixed: 1, other: 0 },
      ],
    },
  });

  const { container } = renderTrack();

  // density note + stacked segment rects are rendered
  expect(container.textContent).toContain('density');
  expect(container.textContent).toContain('50,000');
  expect(container.querySelectorAll('rect').length).toBeGreaterThan(0);

  // hovering the first bin's overlay surfaces its per-class breakdown
  const overlay = container.querySelector('rect[fill="transparent"]');
  expect(overlay).not.toBeNull();
  fireEvent.mouseMove(overlay as Element, { clientX: 10, clientY: 10 });

  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip?.textContent).toContain('17 DGV variants');
  expect(tooltip?.textContent).toContain('gain 10');
});

test('names the variants it draws for a screen reader, by class, and keeps each one\'s name (#529)', () => {
  useQueryMock.mockReturnValue({
    data: {
      total: 5,
      mode: 'lines',
      bin_size: 0,
      variants: [
        { chr: '1', start: 10, end: 60, accession: 'dup1', variant_class: 'gain' },
        { chr: '1', start: 50, end: 150, accession: 'del1', variant_class: 'loss' },
        { chr: '1', start: 160, end: 220, accession: 'del2', variant_class: 'loss' },
        { chr: '1', start: 200, end: 250, accession: 'cx1', variant_class: 'mixed' },
        { chr: '1', start: 260, end: 290, accession: 'ot1', variant_class: 'other' },
      ],
      bins: [],
    },
  });

  const { container } = renderTrack();

  expect(
    screen.getByRole('img', { name: 'DGV variants on chr1:0–300: 5 (1 gain, 2 loss, 1 mixed, 1 other)' }),
  ).toBeInTheDocument();
  expect(container.querySelectorAll('rect[aria-label]')).toHaveLength(5);
});

test('names a density profile by its total, as its note does (#529)', () => {
  useQueryMock.mockReturnValue({
    data: {
      total: 50000,
      mode: 'density',
      bin_size: 100,
      variants: [],
      bins: [{ start: 0, end: 100, gain: 10, loss: 5, mixed: 2, other: 0 }],
    },
  });

  renderTrack();

  expect(
    screen.getByRole('img', { name: 'DGV variants on chr1:0–300: 50,000, shown as density' }),
  ).toBeInTheDocument();
});

test.each([
  ['an empty region', { data: { total: 0, mode: 'lines', bin_size: 0, variants: [], bins: [] } }, 'none'],
  ['a request in flight', { data: undefined }, 'loading'],
  ['a failed request (#510)', { data: undefined, isError: true, refetch: vi.fn() }, 'failed to load'],
])('names %s as what it is, and only an empty region as "none" (#529)', (_label, state, summary) => {
  useQueryMock.mockReturnValue(state);

  renderTrack();

  expect(screen.getByRole('img', { name: `DGV variants on chr1:0–300: ${summary}` })).toBeInTheDocument();
});

test('while a pan loads, a held variant left of the new window is not drawn at its edge (#586)', () => {
  const region = (regionStart: number) => (
    <DgvTrack assembly="GRCh38" chrom="1" width={100} height={48} regionStart={regionStart} regionEnd={regionStart + 300} />
  );
  const variant = (accession: string, start: number) => ({
    chr: '1',
    start,
    end: start + 50,
    accession,
    variant_subtype: 'duplication',
    variant_class: 'gain',
    frequency: 0.05,
  });
  useQueryMock.mockReturnValue({
    data: { total: 2, mode: 'lines', bin_size: 0, variants: [variant('left', 50), variant('right', 250)], bins: [] },
  });
  const { container, rerender } = render(region(0));
  expect(container.querySelectorAll('rect[aria-label]')).toHaveLength(2);

  // Same span, 200 bp right, still loading: "left" (50–100) now lies left of the window.
  // It was drawn as a 1 px bar at x = 0 under its own name.
  useQueryMock.mockReturnValue({ data: undefined, isLoading: true });
  rerender(region(200));
  const drawn = Array.from(container.querySelectorAll('rect[aria-label]')).map((rect) =>
    rect.getAttribute('aria-label'),
  );
  expect(drawn).toEqual(['right']);
});
