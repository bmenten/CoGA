// The chromosome ideogram (#526: it had no tests): cytobands drawn to scale, the
// centromere, the highlighted region, the axis, drag-to-select and the band tooltip.

import { createEvent, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock, refetchMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
  refetchMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import Ideogram from '../visualizations/Ideogram';

// A 1,000 bp chromosome drawn 1,000 px wide: 1 px per bp.
const CHROMOSOME = {
  chr: '1',
  size: 1000,
  bands: [
    { name: 'p2', start: 0, end: 300, stain: 'gneg' },
    { name: 'p1', start: 300, end: 450, stain: 'gpos50' },
    { name: 'p11', start: 450, end: 500, stain: 'acen' },
    { name: 'q11', start: 500, end: 550, stain: 'acen' },
    { name: 'q2', start: 550, end: 1000, stain: 'gneg' },
  ],
};

const answer = (data: unknown, isError = false) =>
  useQueryMock.mockReturnValue({ data, isError, refetch: refetchMock });

const renderIdeogram = (props: Partial<React.ComponentProps<typeof Ideogram>> = {}) =>
  render(
    <Ideogram
      assembly="GRCh38"
      chrom="chr1"
      width={1000}
      height={40}
      regionStart={0}
      regionEnd={1000}
      {...props}
    />,
  );

// Fire a mouse event with an offsetX, which jsdom does not compute.
const mouse = (type: 'mouseDown' | 'mouseMove' | 'mouseUp', target: Element, offsetX: number) => {
  const event = createEvent[type](target);
  Object.defineProperty(event, 'offsetX', { value: offsetX });
  fireEvent(target, event);
};

beforeEach(() => {
  useQueryMock.mockReset();
  refetchMock.mockReset();
});

test('asks for the chromosome of the assembly', () => {
  answer(CHROMOSOME);
  renderIdeogram();
  expect(useQueryMock.mock.calls[0][0].queryKey).toEqual(['chromosome', 'GRCh38', 'chr1']);
});

test('draws each cytoband to scale, and the centromere as two triangles', () => {
  answer(CHROMOSOME);
  const { container } = renderIdeogram();
  const bands = Array.from(container.querySelectorAll('g[clip-path] rect')).map((band) => [
    band.getAttribute('x'),
    band.getAttribute('width'),
  ]);
  expect(bands).toEqual([
    ['0', '300'],
    ['300', '150'],
    ['550', '450'],
  ]);
  // Two acen bands, each a triangle pointing at the centromere (x = 500).
  const [p, q] = Array.from(container.querySelectorAll('g[clip-path] polygon')).map((polygon) =>
    polygon.getAttribute('points'),
  );
  expect(p).toMatch(/^450,[\d.]+ 500,[\d.]+ 450,/);
  expect(q).toMatch(/^500,[\d.]+ 550,[\d.]+ 550,/);
  // With a p and a q acen band, the outline is pinched at the centromere.
  expect(container.querySelector('path[fill="none"]')?.getAttribute('d')).toContain('L 500');
});

test('falls back to a plain capsule outline without a centromere', () => {
  answer({ ...CHROMOSOME, bands: CHROMOSOME.bands.filter((band) => band.stain !== 'acen') });
  const { container } = renderIdeogram();
  expect(container.querySelector('path[fill="none"]')).toBeNull();
  expect(container.querySelector('svg > rect[fill="none"]')).not.toBeNull();
});

test('highlights the displayed region, and not the whole chromosome', () => {
  answer(CHROMOSOME);
  const { container, rerender } = renderIdeogram({ regionStart: 200, regionEnd: 400 });
  const highlight = container.querySelector('svg > rect[pointer-events="none"]');
  expect([highlight?.getAttribute('x'), highlight?.getAttribute('width')]).toEqual(['200', '200']);

  rerender(
    <Ideogram assembly="GRCh38" chrom="chr1" width={1000} height={40} regionStart={0} regionEnd={1000} />,
  );
  expect(container.querySelector('svg > rect[pointer-events="none"]')).toBeNull();
});

test('clamps a region that runs past the chromosome end', () => {
  answer(CHROMOSOME);
  const { container } = renderIdeogram({ regionStart: 900, regionEnd: 5000 });
  const highlight = container.querySelector('svg > rect[pointer-events="none"]');
  expect([highlight?.getAttribute('x'), highlight?.getAttribute('width')]).toEqual(['900', '100']);
});

test('labels the axis in bp, kb or Mb, and can leave it out', () => {
  answer({ chr: '1', size: 248_956_422, bands: [] });
  const { container, rerender } = renderIdeogram();
  const labels = Array.from(container.querySelectorAll('svg text')).map((text) => text.textContent);
  expect(labels[0]).toBe('0 bp');
  expect(labels.slice(1).every((label) => /^\d+ Mb$/.test(label ?? ''))).toBe(true);

  rerender(
    <Ideogram
      assembly="GRCh38"
      chrom="chr1"
      width={1000}
      height={40}
      regionStart={0}
      regionEnd={1000}
      showAxis={false}
    />,
  );
  expect(container.querySelectorAll('svg text')).toHaveLength(0);
});

test('turns a drag into a region, in either direction, and ignores a click', () => {
  answer(CHROMOSOME);
  const onRegionSelect = vi.fn();
  const { container } = renderIdeogram({ onRegionSelect });
  const svg = container.querySelector('svg') as SVGSVGElement;

  mouse('mouseDown', svg, 600.4);
  mouse('mouseMove', svg, 300);
  // The drag is shown while it lasts.
  expect(container.querySelector('rect[stroke-dasharray="4"]')?.getAttribute('x')).toBe('300');
  mouse('mouseUp', svg, 250.6);
  // Start rounds down and end up, so the selection covers what was dragged over.
  expect(onRegionSelect).toHaveBeenCalledWith(250, 601);
  expect(container.querySelector('rect[stroke-dasharray="4"]')).toBeNull();

  // Less than 5 px is a click, not a selection.
  mouse('mouseDown', svg, 100);
  mouse('mouseUp', svg, 103);
  expect(onRegionSelect).toHaveBeenCalledTimes(1);
});

test('does not start a selection without a handler', () => {
  answer(CHROMOSOME);
  const { container } = renderIdeogram();
  const svg = container.querySelector('svg') as SVGSVGElement;
  mouse('mouseDown', svg, 100);
  mouse('mouseMove', svg, 300);
  expect(container.querySelector('rect[stroke-dasharray="4"]')).toBeNull();
});

test('names the cytoband under the pointer, without the chr prefix', () => {
  answer(CHROMOSOME);
  const { container } = renderIdeogram();
  const band = container.querySelectorAll('g[clip-path] rect')[1];
  fireEvent.mouseMove(band, { clientX: 10, clientY: 10 });
  expect(screen.getByText('1p1')).toBeInTheDocument();
  fireEvent.mouseLeave(band);
  expect(screen.queryByText('1p1')).not.toBeInTheDocument();
});

test('names the chromosome and the region highlighted on it for a screen reader (#529)', () => {
  answer(CHROMOSOME);
  const region = (regionStart: number, regionEnd: number) => (
    <Ideogram assembly="GRCh38" chrom="chr1" width={1000} height={40} regionStart={regionStart} regionEnd={regionEnd} />
  );
  const { rerender } = render(region(200, 400));
  expect(screen.getByRole('img', { name: 'Chromosome 1 ideogram, 200–400 highlighted' })).toBeInTheDocument();

  // Clamped to the chromosome, as the highlight is.
  rerender(region(900, 5000));
  expect(screen.getByRole('img', { name: 'Chromosome 1 ideogram, 900–1,000 highlighted' })).toBeInTheDocument();

  // Nothing is highlighted when the whole chromosome is in view.
  rerender(region(0, 1000));
  expect(screen.getByRole('img', { name: 'Chromosome 1 ideogram, whole chromosome in view' })).toBeInTheDocument();
});

test('names a loading or failed ideogram as such, not as a region (#529)', () => {
  answer(undefined);
  const { rerender } = renderIdeogram({ regionStart: 200, regionEnd: 400 });
  expect(screen.getByRole('img', { name: 'Chromosome 1 ideogram: loading' })).toBeInTheDocument();

  answer(undefined, true);
  rerender(
    <Ideogram assembly="GRCh38" chrom="chr1" width={1000} height={40} regionStart={200} regionEnd={400} />,
  );
  expect(screen.getByRole('img', { name: 'Chromosome 1 ideogram: failed to load' })).toBeInTheDocument();
});

test('draws nothing until the chromosome arrives, and a failure with a retry', () => {
  answer(undefined);
  const { container, rerender } = renderIdeogram();
  expect(container.querySelector('svg')?.childElementCount).toBe(0);

  answer(undefined, true);
  rerender(
    <Ideogram assembly="GRCh38" chrom="chr1" width={1000} height={40} regionStart={0} regionEnd={1000} />,
  );
  fireEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetchMock).toHaveBeenCalledTimes(1);
});
