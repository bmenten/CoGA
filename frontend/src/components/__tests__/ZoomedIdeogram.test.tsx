// Zoomed ideogram: bands mapped and clipped to the region (half-open, centromere triangles, telomere caps only
// at a reached chromosome end), stain fills, edges, axis ticks, the ISCN tooltip, loading/failed (#510) states.
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGetMock, useQueryMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));

import ZoomedIdeogram from '../visualizations/ZoomedIdeogram';

type Props = ComponentProps<typeof ZoomedIdeogram>;
type QueryOptions = {
  queryKey: unknown[];
  queryFn: () => Promise<unknown>;
  staleTime?: number;
  gcTime?: number;
};

// The colours the component reads through cssVar(); jsdom has no stylesheet, so set them.
const THEME: Record<string, string> = {
  '--color-black': '#000000',
  '--color-white': '#ffffff',
  '--color-signature-red': '#c61f2d',
  '--color-stain-gneg': '#ffffff',
  '--color-stain-gpos50': '#969696',
  '--color-stain-gpos100': '#000000',
  '--color-stain-acen': '#e60000',
};

// A synthetic 1-Mb chromosome: two p-arm bands, the two centromere (acen) halves, two q-arm bands.
const CHROMOSOME = {
  chr: '1',
  size: 1_000_000,
  bands: [
    { name: 'p13', start: 0, end: 200_000, stain: 'gneg' },
    { name: 'p12', start: 200_000, end: 400_000, stain: 'gpos50' },
    { name: 'p11.1', start: 400_000, end: 500_000, stain: 'acen' },
    { name: 'q11.1', start: 500_000, end: 600_000, stain: 'acen' },
    { name: 'q12', start: 600_000, end: 800_000, stain: 'gpos100' },
    { name: 'q13', start: 800_000, end: 1_000_000, stain: 'gneg' },
  ],
};

// Height 40 = a 20-px band row above the 20-px axis. The whole chromosome at 1 px per kb.
const BASE_PROPS: Props = {
  assembly: 'GRCh38',
  chrom: '1',
  width: 1000,
  height: 40,
  regionStart: 0,
  regionEnd: 1_000_000,
};
const BAND_ROW_HEIGHT = 20;

const ideogram = (overrides: Partial<Props> = {}) => (
  <ZoomedIdeogram {...BASE_PROPS} {...overrides} />
);

const serve = (state: {
  data?: unknown;
  isError?: boolean;
  refetch?: () => void;
}) =>
  useQueryMock.mockReturnValue({
    data: undefined,
    isError: false,
    refetch: vi.fn(),
    ...state,
  });

const lastQueryOptions = (): QueryOptions => {
  const call = useQueryMock.mock.calls.at(-1);
  if (!call) throw new Error('useQuery was not called');
  return call[0] as QueryOptions;
};

// Band shapes are the direct rect/path/polygon children of the svg, in band order.
const bandShapes = (container: HTMLElement): Element[] =>
  Array.from(
    container.querySelectorAll('svg > rect, svg > path, svg > polygon')
  );

const num = (element: Element, name: string): number =>
  Number(element.getAttribute(name));

const numbersIn = (value: string | null): number[] =>
  (value ?? '').match(/-?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi)?.map(Number) ?? [];

const expectNumbers = (value: string | null, expected: number[]) => {
  const actual = numbersIn(value);
  expect(actual).toHaveLength(expected.length);
  actual.forEach((n, index) => expect(n).toBeCloseTo(expected[index], 6));
};

const expectRect = (element: Element, x: number, width: number) => {
  expect(element.tagName).toBe('rect');
  expect(num(element, 'x')).toBeCloseTo(x, 6);
  expect(num(element, 'width')).toBeCloseTo(width, 6);
  expect(num(element, 'y')).toBe(0);
  expect(num(element, 'height')).toBe(BAND_ROW_HEIGHT);
};

const ticks = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('svg > g > text')).map((text) => ({
    x: num(text, 'x'),
    label: text.textContent,
  }));

const tooltip = () => document.body.querySelector('.viz-tooltip');

describe('ZoomedIdeogram', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    useQueryMock.mockReset();
    Object.entries(THEME).forEach(([name, value]) =>
      document.documentElement.style.setProperty(name, value)
    );
  });

  afterEach(() => {
    Object.keys(THEME).forEach((name) =>
      document.documentElement.style.removeProperty(name)
    );
  });

  it('fetches the band set per chromosome from its encoded API path, not per region', async () => {
    serve({});
    apiGetMock.mockResolvedValue({ data: CHROMOSOME });

    const { rerender } = render(ideogram({ chrom: '1/../2' }));
    const options = lastQueryOptions();
    expect(options.queryKey).toEqual(['chromosome', 'GRCh38', '1/../2']);
    expect(options.staleTime).toBe(Infinity);
    await expect(options.queryFn()).resolves.toEqual(CHROMOSOME);
    // The chromosome is one encoded path segment (#521).
    expect(apiGetMock).toHaveBeenCalledWith('/chromosomes/GRCh38/1%2F..%2F2');

    // A pan or zoom keeps the same key, so the bands are not fetched again.
    rerender(
      ideogram({ chrom: '1/../2', regionStart: 100_000, regionEnd: 200_000 })
    );
    expect(lastQueryOptions().queryKey).toEqual(options.queryKey);
  });

  it('draws nothing until the chromosome has loaded', () => {
    serve({});
    const { container } = render(ideogram());

    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('width', '1000');
    expect(svg?.childElementCount).toBe(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it.each([
    ['an empty', 500_000, 500_000],
    ['an inverted', 600_000, 500_000],
  ])('draws nothing for %s region', (_label, regionStart, regionEnd) => {
    serve({ data: CHROMOSOME });
    const { container } = render(ideogram({ regionStart, regionEnd }));

    expect(container.querySelector('svg')?.childElementCount).toBe(0);
    expect(
      screen.getByRole('img', { name: 'Cytobands on chr1: no region in view' })
    ).toBeInTheDocument();
  });

  it('a failed request shows the failure with retry, never a blank or stale ideogram (#510)', () => {
    const refetch = vi.fn();
    // Even with bands cached from an earlier fetch, the failure is what is shown.
    serve({ data: CHROMOSOME, isError: true, refetch });
    const { container } = render(ideogram());

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not load the chromosome ideogram — this is not an empty result.'
    );
    expect(bandShapes(container)).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('maps bands to pixels across the region, clipping the bands cut by its edges', () => {
    serve({ data: CHROMOSOME });
    // 150–650 kb on 500 px: 1 px per kb.
    const { container } = render(
      ideogram({ regionStart: 150_000, regionEnd: 650_000, width: 500 })
    );

    const [p13, p12, p11, q11, q12, ...rest] = bandShapes(container);
    expectRect(p13, 0, 50); // 150–200 kb of a 0–200 kb band
    expectRect(p12, 50, 200);
    expect(p11.tagName).toBe('polygon');
    expect(q11.tagName).toBe('polygon');
    expectRect(q12, 450, 50); // 600–650 kb of a 600–800 kb band
    // q13 (800 kb–1 Mb) lies outside the region.
    expect(rest).toHaveLength(0);
  });

  it('does not draw bands that only touch the region edge (half-open intervals)', () => {
    serve({ data: CHROMOSOME });
    // p13 ends exactly at 200 kb and q12 starts exactly at 600 kb.
    const { container } = render(
      ideogram({ regionStart: 200_000, regionEnd: 600_000, width: 400 })
    );

    const shapes = bandShapes(container);
    expect(shapes.map((shape) => shape.tagName)).toEqual([
      'rect',
      'polygon',
      'polygon',
    ]);
    expectRect(shapes[0], 0, 200);
  });

  it('draws the centromere as two triangles meeting at the p/q boundary', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(
      ideogram({ regionStart: 200_000, regionEnd: 600_000, width: 400 })
    );

    const [, pSide, qSide] = bandShapes(container);
    // p11.1 (400–500 kb) points right, q11.1 (500–600 kb) points left: both apexes at (200, 10).
    expectNumbers(pSide.getAttribute('points'), [200, 0, 300, 10, 200, 20]);
    expectNumbers(qSide.getAttribute('points'), [300, 10, 400, 0, 400, 20]);
  });

  it.each([
    [
      'the whole chromosome',
      0,
      1_000_000,
      ['path', 'rect', 'polygon', 'polygon', 'rect', 'path'],
    ],
    ['only the p-arm end', 0, 500_000, ['path', 'rect', 'polygon']],
    [
      'only the q-arm end',
      100_000,
      1_000_000,
      ['rect', 'rect', 'polygon', 'polygon', 'rect', 'path'],
    ],
    [
      'neither end',
      100_000,
      900_000,
      ['rect', 'rect', 'polygon', 'polygon', 'rect', 'rect'],
    ],
  ])(
    'caps a telomere only where the region reaches it: %s',
    (_label, regionStart, regionEnd, tags) => {
      serve({ data: CHROMOSOME });
      const { container } = render(ideogram({ regionStart, regionEnd }));

      expect(bandShapes(container).map((shape) => shape.tagName)).toEqual(tags);
    }
  );

  it('rounds the pter cap on the left and the qter cap on the right', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(ideogram());

    const shapes = bandShapes(container);
    // p13 (0–200 px): straight right edge, a radius-10 arc closing the left end (sweep 0).
    expect(shapes[0].getAttribute('d')).toMatch(
      /^M \S+ 0 H \S+ A \S+ \S+ 0 0 0 \S+ \S+ H \S+ Z$/
    );
    expectNumbers(
      shapes[0].getAttribute('d'),
      [200, 0, 10, 10, 10, 0, 0, 0, 10, 20, 200]
    );
    // q13 (800–1000 px): straight left edge, the arc closing the right end (sweep 1).
    expectNumbers(
      shapes[5].getAttribute('d'),
      [800, 0, 990, 10, 10, 0, 0, 1, 990, 20, 800]
    );
  });

  it('fills each band with its own gradient whose body is the stain colour', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(ideogram());

    const gradientIds = bandShapes(container).map(
      (shape) => /^url\(#(.+)\)$/.exec(shape.getAttribute('fill') ?? '')?.[1]
    );
    expect(new Set(gradientIds).size).toBe(CHROMOSOME.bands.length);
    const bodyColours = gradientIds.map((id) => {
      const gradient = container.querySelector(`[id="${id}"]`);
      const stops = Array.from(gradient?.querySelectorAll('stop') ?? []);
      return stops
        .find((stop) => stop.getAttribute('offset') === '52%')
        ?.getAttribute('stop-color');
    });
    expect(bodyColours).toEqual([
      '#ffffff',
      '#969696',
      '#e60000',
      '#e60000',
      '#000000',
      '#ffffff',
    ]);
  });

  it('marks both region edges with a line over the band row', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(
      ideogram({ regionStart: 150_000, regionEnd: 650_000, width: 500 })
    );

    const edges = Array.from(container.querySelectorAll('svg > line'));
    expect(
      edges.map((line) =>
        ['x1', 'x2', 'y1', 'y2'].map((name) => num(line, name))
      )
    ).toEqual([
      [0, 0, 0, BAND_ROW_HEIGHT],
      [500, 500, 0, BAND_ROW_HEIGHT],
    ]);
    edges.forEach((line) => expect(line).toHaveAttribute('stroke', '#c61f2d'));
  });

  it('labels the axis at both region edges and at round intervals in between', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(
      ideogram({ regionStart: 150_000, regionEnd: 650_000, width: 500 })
    );

    const axis = ticks(container);
    expect(axis.map((tick) => tick.label)).toEqual([
      '150.00 kb',
      '200.00 kb',
      '300.00 kb',
      '400.00 kb',
      '500.00 kb',
      '600.00 kb',
      '650.00 kb',
    ]);
    [0, 50, 150, 250, 350, 450, 500].forEach((x, index) =>
      expect(axis[index].x).toBeCloseTo(x, 6)
    );
    // Tick marks hang below the band row.
    const tickMark = container.querySelector('svg > g > line');
    expect([
      num(tickMark as Element, 'y1'),
      num(tickMark as Element, 'y2'),
    ]).toEqual([20, 26]);
  });

  it('labels the whole-chromosome axis once per position, switching units at 1 Mb', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(ideogram());

    expect(ticks(container).map((tick) => tick.label)).toEqual([
      '0 bp',
      '100.00 kb',
      '200.00 kb',
      '300.00 kb',
      '400.00 kb',
      '500.00 kb',
      '600.00 kb',
      '700.00 kb',
      '800.00 kb',
      '900.00 kb',
      '1.00 Mb',
    ]);
  });

  it('gives each tick its own label at gene-level zoom (#526)', () => {
    // 10 kb of chr13 over 1,200 px: ticks every 500 bp. At two decimals of a Mb, 19 of the
    // 21 ticks read "32.32 Mb".
    serve({ data: { chr: '13', size: 114_364_328, bands: [] } });
    const { container } = render(
      ideogram({ chrom: '13', width: 1200, regionStart: 32_315_000, regionEnd: 32_325_000 })
    );
    const labels = ticks(container).map((tick) => tick.label);
    expect(labels).toHaveLength(21);
    expect(new Set(labels).size).toBe(21);
    expect(labels.slice(0, 3)).toEqual(['32.3150 Mb', '32.3155 Mb', '32.3160 Mb']);
    expect(labels.at(-1)).toBe('32.3250 Mb');
  });

  it('names the hovered band in ISCN form (1p12) and clears the tooltip on leave', () => {
    serve({ data: CHROMOSOME });
    const { container } = render(ideogram({ chrom: 'chr1' }));
    const [, p12, p11] = bandShapes(container);

    fireEvent.mouseMove(p12, { clientX: 300, clientY: 20 });
    expect(tooltip()?.textContent).toBe('1p12');

    fireEvent.mouseMove(p11, { clientX: 450, clientY: 20 });
    expect(tooltip()?.textContent).toBe('1p11.1');

    fireEvent.mouseLeave(p11);
    expect(tooltip()).toBeNull();
  });

  it('names the cytobands in the region for a screen reader, the first three in ISCN form (#529)', () => {
    serve({ data: CHROMOSOME });
    const { rerender } = render(
      ideogram({ regionStart: 150_000, regionEnd: 650_000, width: 500 })
    );
    expect(
      screen.getByRole('img', {
        name: 'Cytobands on chr1:150,000–650,000: 5 (1p13, 1p12, 1p11.1, …)',
      })
    ).toBeInTheDocument();

    // Half-open, as drawn: p13 and q12 only touch this region.
    rerender(ideogram({ regionStart: 200_000, regionEnd: 600_000, width: 400 }));
    expect(
      screen.getByRole('img', {
        name: 'Cytobands on chr1:200,000–600,000: 3 (1p12, 1p11.1, 1q11.1)',
      })
    ).toBeInTheDocument();
  });

  it.each([
    ['without cytoband data', { data: { ...CHROMOSOME, bands: [] } }, 'none'],
    ['while it loads', {}, 'loading'],
    [
      'after a failure, even with bands cached (#510)',
      { data: CHROMOSOME, isError: true },
      'failed to load',
    ],
  ])('says what it shows %s, and "none" only for no bands (#529)', (_label, state, summary) => {
    serve(state);
    render(ideogram());

    expect(
      screen.getByRole('img', { name: `Cytobands on chr1:0–1,000,000: ${summary}` })
    ).toBeInTheDocument();
  });

  it('still draws the region edges and axis for a chromosome without cytoband data', () => {
    // The backend returns `bands: []` for an assembly without a cytoband file.
    serve({ data: { ...CHROMOSOME, bands: [] } });
    const { container } = render(ideogram());

    expect(bandShapes(container)).toHaveLength(0);
    expect(container.querySelectorAll('svg > line')).toHaveLength(2);
    expect(ticks(container).map((tick) => tick.label)).toContain('1.00 Mb');
  });
});
