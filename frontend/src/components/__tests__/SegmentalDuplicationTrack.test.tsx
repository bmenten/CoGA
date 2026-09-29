// Segmental-duplication/LCR track: the window request, intervals mapped and clipped to the region with a
// 2-px floor and a label title, the empty and failed (#510) states, and the same-span pan fallback.
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGetMock, useQueryMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));

import SegmentalDuplicationTrack from '../visualizations/SegmentalDuplicationTrack';

type Props = ComponentProps<typeof SegmentalDuplicationTrack>;
type QueryOptions = {
  queryKey: unknown[];
  queryFn: () => Promise<unknown>;
  enabled?: boolean;
};

const THEME: Record<string, string> = {
  '--color-grid': '#e5e7eb',
  '--color-segmental-duplication': '#1f2937',
};

// Synthetic 22q11.2-style LCR blocks.
const LCR22A = { start: 18_600_000, end: 19_000_000, label: 'LCR22-A' };
const LCR22B = { start: 20_300_000, end: 20_700_000, label: 'LCR22-B' };
const LCR22D = { start: 21_450_000, end: 21_600_000, label: 'LCR22-D' };

// 4 Mb on 400 px: 10 kb per px. Height 20 → track row y 4, height 12, baseline at y 10.
const BASE_PROPS: Props = {
  assembly: 'GRCh38',
  chrom: '22',
  width: 400,
  height: 20,
  regionStart: 18_000_000,
  regionEnd: 22_000_000,
};

const track = (overrides: Partial<Props> = {}) => (
  <SegmentalDuplicationTrack {...BASE_PROPS} {...overrides} />
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

// Pixel geometry rounded to µpx, so floating-point noise (229.99999999999997) compares as 230.
const px = (rect: Element, name: string): number =>
  Math.round(Number(rect.getAttribute(name)) * 1e6) / 1e6;

const blocks = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('svg > rect')).map((rect) => ({
    x: px(rect, 'x'),
    width: px(rect, 'width'),
    label: rect.querySelector('title')?.textContent,
  }));

describe('SegmentalDuplicationTrack', () => {
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

  it('requests exactly the visible window, keyed per window, from the encoded endpoint', async () => {
    serve({});
    apiGetMock.mockResolvedValue({ data: [LCR22A] });
    render(track({ assembly: 'GRCh38/hg38' }));

    const options = lastQueryOptions();
    expect(options.queryKey).toEqual([
      'segmental-duplications',
      'GRCh38/hg38',
      '22',
      18_000_000,
      22_000_000,
    ]);
    expect(options.enabled).toBe(true);
    await expect(options.queryFn()).resolves.toEqual([LCR22A]);
    // Each identifier is one encoded path segment (#521); the window travels as query params.
    expect(apiGetMock).toHaveBeenCalledWith(
      '/segmental-duplications/GRCh38%2Fhg38/22',
      {
        params: { start: 18_000_000, end: 22_000_000 },
      }
    );
  });

  it.each([
    ['an empty', 20_000_000, 20_000_000],
    ['an inverted', 21_000_000, 20_000_000],
  ])(
    'does not request %s window, and draws nothing for it',
    (_label, regionStart, regionEnd) => {
      // The backend rejects a window without start < end, so the request must not be sent.
      serve({});
      const { container } = render(track({ regionStart, regionEnd }));

      expect(lastQueryOptions().enabled).toBe(false);
      expect(container.querySelector('svg')?.childElementCount).toBe(0);
      expect(
        screen.queryByText(/No segmental duplications/)
      ).not.toBeInTheDocument();
    }
  );

  it('draws nothing (no empty-region claim) while the first window loads', () => {
    serve({});
    const { container } = render(track());

    expect(container.querySelector('svg')?.childElementCount).toBe(0);
    expect(
      screen.queryByText(/No segmental duplications/)
    ).not.toBeInTheDocument();
  });

  it('draws each duplication at its genomic position on the track row', () => {
    serve({ data: [LCR22A, LCR22B, LCR22D] });
    const { container } = render(track());

    expect(blocks(container)).toEqual([
      { x: 60, width: 40, label: 'LCR22-A' },
      { x: 230, width: 40, label: 'LCR22-B' },
      { x: 345, width: 15, label: 'LCR22-D' },
    ]);
    container.querySelectorAll('svg > rect').forEach((rect) => {
      expect(rect).toHaveAttribute('y', '4');
      expect(rect).toHaveAttribute('height', '12');
      expect(rect).toHaveAttribute('fill', '#1f2937');
    });
  });

  it('clips duplications that extend past either region edge', () => {
    serve({
      data: [
        { start: 17_900_000, end: 18_100_000, label: 'crosses start' },
        { start: 21_950_000, end: 22_200_000, label: 'crosses end' },
      ],
    });
    const { container } = render(track());

    expect(blocks(container)).toEqual([
      { x: 0, width: 10, label: 'crosses start' },
      { x: 395, width: 5, label: 'crosses end' },
    ]);
  });

  it('keeps a duplication narrower than a pixel visible at 2 px', () => {
    serve({
      data: [{ start: 19_500_000, end: 19_501_000, label: 'short LCR' }],
    });
    const { container } = render(track());

    expect(blocks(container)).toEqual([
      { x: 150, width: 2, label: 'short LCR' },
    ]);
  });

  it('says so when the region has no segmental duplications, over the baseline', () => {
    serve({ data: [] });
    const { container } = render(track());

    expect(
      screen.getByText('No segmental duplications/LCRs in this region')
    ).toBeInTheDocument();
    expect(blocks(container)).toHaveLength(0);
    const baseline = container.querySelector('svg > line');
    expect(baseline).toHaveAttribute('y1', '10');
    expect(baseline).toHaveAttribute('x2', '400');
    expect(baseline).toHaveAttribute('stroke', '#e5e7eb');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('a failed request shows the failure with retry, never an empty region or old blocks (#510)', () => {
    serve({ data: [LCR22A] });
    const { container, rerender } = render(track());
    expect(blocks(container)).toHaveLength(1);

    // Pan (same span, same chromosome) to a window whose request fails: the last window's
    // blocks, which a pan would otherwise keep on screen, must not stand in for it.
    const refetch = vi.fn();
    serve({ isError: true, refetch });
    rerender(track({ regionStart: 19_000_000, regionEnd: 23_000_000 }));

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not load segmental duplications — this is not an empty result.'
    );
    expect(
      screen.queryByText(/No segmental duplications/)
    ).not.toBeInTheDocument();
    expect(blocks(container)).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('on a pan at the same zoom, keeps the last blocks at their true positions until the new window arrives', () => {
    serve({ data: [LCR22B] });
    const { container, rerender } = render(track());
    expect(blocks(container)).toEqual([
      { x: 230, width: 40, label: 'LCR22-B' },
    ]);

    // Pan 1 Mb right; the new window is still loading.
    serve({});
    rerender(track({ regionStart: 19_000_000, regionEnd: 23_000_000 }));
    expect(blocks(container)).toEqual([
      { x: 130, width: 40, label: 'LCR22-B' },
    ]);

    // The new window's own data replaces the held blocks.
    serve({ data: [LCR22D] });
    rerender(track({ regionStart: 19_000_000, regionEnd: 23_000_000 }));
    expect(blocks(container)).toEqual([
      { x: 245, width: 15, label: 'LCR22-D' },
    ]);
  });

  it('does not draw a held block that lies outside the new window while a pan loads (#526)', () => {
    serve({ data: [LCR22A, LCR22B] });
    const { container, rerender } = render(track());
    expect(blocks(container).map((block) => block.label)).toEqual(['LCR22-A', 'LCR22-B']);

    // Pan to 19.5–23.5 Mb: LCR22-A (18.6–19.0 Mb) is now entirely left of the window.
    serve({});
    rerender(track({ regionStart: 19_500_000, regionEnd: 23_500_000 }));
    // It used to be clamped into a 2 px block at x = 0, labelled LCR22-A at 19.5 Mb.
    expect(blocks(container)).toEqual([{ x: 80, width: 40, label: 'LCR22-B' }]);
    expect(screen.queryByText(/No segmental duplications/)).not.toBeInTheDocument();
  });

  it.each([
    ['a zoom', { regionStart: 18_000_000, regionEnd: 20_000_000 }],
    ['a move to another chromosome', { chrom: '21' }],
    ['a switch to another assembly', { assembly: 'GRCh37' }],
  ])(
    'blanks after %s instead of drawing the previous blocks',
    (_label, change) => {
      serve({ data: [LCR22A, LCR22B] });
      const { container, rerender } = render(track());
      expect(blocks(container)).toHaveLength(2);

      serve({});
      rerender(track(change));

      expect(container.querySelector('svg')?.childElementCount).toBe(0);
      expect(
        screen.queryByText(/No segmental duplications/)
      ).not.toBeInTheDocument();
    }
  );
});
