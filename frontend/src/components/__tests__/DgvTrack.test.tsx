import { fireEvent, render } from '@testing-library/react';
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
