import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';
import { fireMouseAt } from '../../test/pointer';
import { serveTrackFetchFrom } from '../../test/trackFetchMock';
import ApcadChart from '../visualizations/ApcadChart';

// The charts fetch through lib/trackFetch (the shared API client); serve it from the
// fetch-shaped fixtures below.
vi.mock('../../lib/trackFetch', async () => {
  const { fetchTrackJsonMock } = await import('../../test/trackFetchMock');
  return { fetchTrackJson: fetchTrackJsonMock };
});

/**
 * The APCAD track normally shows parent-of-origin markers, but it is also where a
 * caller's minor-allele-fraction signal lands. bigWig has nowhere to record a
 * parental origin, so every one of those points is `und` — and this chart used to
 * drop them, showing "No APCAD data in this region" over a track holding 4.2M
 * points.
 *
 * The server decides which markers to send (phased ones where they exist, unphased
 * otherwise); the chart's job is to draw what arrived.
 */

/**
 * Count the dots actually painted. A negative assertion ("the empty message is
 * absent") is useless here: it is satisfied on the first render, before the fetch
 * resolves, so it passes just as happily against the code that dropped every point.
 */
const arcSpy = vi.fn();

beforeEach(() => {
  arcSpy.mockReset();
  // The shared canvas stub (setupTests.ts), with its dots counted.
  const context = HTMLCanvasElement.prototype.getContext.call(
    document.createElement('canvas'),
    '2d',
  ) as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    ...context,
    arc: arcSpy,
  } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  vi.restoreAllMocks();
});

const chart = (props: Partial<ComponentProps<typeof ApcadChart>> = {}) => (
  <ApcadChart apcadUrls={['http://test/apcad']} width={400} height={120} chroms={['1']} {...props} />
);

const renderChart = (props: Partial<ComponentProps<typeof ApcadChart>> = {}) => {
  const client = createTestQueryClient();
  const view = render(<QueryClientProvider client={client}>{chart(props)}</QueryClientProvider>);
  return {
    ...view,
    rerenderChart: (next: Partial<ComponentProps<typeof ApcadChart>>) =>
      view.rerender(<QueryClientProvider client={client}>{chart(next)}</QueryClientProvider>),
  };
};

const respondWith = (items: unknown[]) => {
  const fetchLike = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ items }),
  });
  serveTrackFetchFrom(fetchLike);
  return fetchLike;
};

describe('ApcadChart', () => {
  it('does not refetch APCAD data when only width and layout change', async () => {
    const fetchLike = respondWith([
      { chr: '1', start: 0, end: 50, value: 0.25, origin: 'paternal' },
      { chr: '1', start: 50, end: 100, value: 0.75, origin: 'maternal' },
    ]);

    const { container, rerenderChart } = renderChart({ width: 320 });

    await waitFor(() => expect(fetchLike).toHaveBeenCalledTimes(1));

    // The chart opts into click telemetry via data-audit-id (the global click
    // logger captures it); guard against a refactor silently dropping the hook.
    expect(container.querySelector('[data-audit-id="apcad-chart"]')).not.toBeNull();

    rerenderChart({ width: 640, layout: { offsets: { '1': 0 }, lengths: { '1': 100 }, total: 100 } });

    await waitFor(() => expect(fetchLike).toHaveBeenCalledTimes(1));
  });

  it('draws unphased points instead of reporting an empty region', async () => {
    respondWith([
      { chr: '1', start: 11564, end: 11565, value: 0.0, origin: 'und' },
      { chr: '1', start: 11771, end: 11772, value: 0.5, origin: 'und' },
      { chr: '1', start: 11862, end: 11863, value: 0.375, origin: 'und' },
    ]);

    renderChart();

    // Three points in, three dots painted.
    await waitFor(() => expect(arcSpy).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(/No APCAD data in this region/i)).not.toBeInTheDocument();
  });

  it('reads a chromosome however the data spells it (#684)', async () => {
    // Lower case and a leading zero used to miss the chromosome the chart draws.
    respondWith([
      { chr: 'chrx', start: 100, end: 101, value: 0.5, origin: 'und' },
      { chr: 'chr01', start: 200, end: 201, value: 0.5, origin: 'und' },
    ]);

    renderChart({ chroms: ['1', 'X'] });

    await waitFor(() => expect(arcSpy).toHaveBeenCalledTimes(2));
  });

  it('still draws phased points', async () => {
    respondWith([
      { chr: '1', start: 100, end: 101, value: 0.5, origin: 'paternal' },
      { chr: '1', start: 200, end: 201, value: 0.5, origin: 'maternal' },
    ]);

    renderChart();

    await waitFor(() => expect(arcSpy).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/No APCAD data in this region/i)).not.toBeInTheDocument();
  });

  it('reports an empty region when nothing came back', async () => {
    respondWith([]);

    renderChart();

    // The message has to survive: a genuinely empty window on a phased track is the
    // autozygosity signal, and must not be confused with a track that failed to draw.
    await waitFor(() =>
      expect(screen.getByText(/No APCAD data in this region/i)).toBeInTheDocument(),
    );
    expect(arcSpy).not.toHaveBeenCalled();
  });

  it('drops points with a non-finite value', async () => {
    respondWith([
      { chr: '1', start: 100, end: 101, value: null, origin: 'und' },
      { chr: '1', start: 200, end: 201, value: 'nan', origin: 'und' },
    ]);

    renderChart();

    await waitFor(() =>
      expect(screen.getByText(/No APCAD data in this region/i)).toBeInTheDocument(),
    );
    expect(arcSpy).not.toHaveBeenCalled();
  });

  // #529: the canvas is a named image, and its name says what is drawn on it now.
  it('is named after the sample, the window and the sites drawn in it (#529)', async () => {
    respondWith([
      { chr: '1', start: 11564, end: 11565, value: 0.0, origin: 'und' },
      { chr: '1', start: 11771, end: 11772, value: 0.5, origin: 'und' },
      // Outside the window: not drawn, so not counted either.
      { chr: '1', start: 30000, end: 30001, value: 0.5, origin: 'und' },
    ]);

    renderChart({ sampleId: 'S1', regionStart: 10000, regionEnd: 20000 });

    expect(
      await screen.findByRole('img', {
        name: 'APCAD B-allele frequency of S1 on chr1:10,000–20,000: 2 sites',
      }),
    ).toBeInTheDocument();
    expect(arcSpy).toHaveBeenCalledTimes(2);
  });

  it('fetches the PCF segment overlays apart from the raw points, and counts the segments drawn over the sites (#529)', async () => {
    const fetchLike = vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          items: url.endsWith('/pcf')
            ? [
                { chr: '1', start: 0, end: 100, value: 0.5, origin: 'maternal' },
                // A PCF segment without a parental origin is not an overlay: not drawn, not counted.
                { chr: '1', start: 100, end: 200, value: 0.5, origin: 'und' },
              ]
            : [{ chr: '1', start: 10, end: 11, value: 0.5, origin: 'paternal' }],
        }),
      }),
    );
    serveTrackFetchFrom(fetchLike);

    renderChart({ pcfUrls: ['http://test/pcf'] });

    expect(
      await screen.findByRole('img', { name: 'APCAD B-allele frequency on chr1: 1 site, 1 PCF segment' }),
    ).toBeInTheDocument();
    expect(fetchLike).toHaveBeenCalledTimes(2);
    expect(fetchLike).toHaveBeenCalledWith(
      'http://test/pcf',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    // One dot for the one site: a segment is drawn as a line, not as a dot.
    expect(arcSpy).toHaveBeenCalledTimes(1);
  });

  it('opens the chromosome under a click on the genome-wide chart', async () => {
    respondWith([
      { chr: '1', start: 10, end: 11, value: 0.5, origin: 'und' },
      { chr: '2', start: 10, end: 11, value: 0.5, origin: 'und' },
    ]);
    const onChromosomeClick = vi.fn();
    renderChart({
      chroms: ['1', '2'],
      layout: { offsets: { '1': 0, '2': 100 }, lengths: { '1': 100, '2': 100 }, total: 200 },
      onChromosomeClick,
    });
    await waitFor(() => expect(arcSpy).toHaveBeenCalledTimes(2));

    // 400 px over 200 bp: x 300 is 50 bp into chr2, x 100 is in chr1.
    fireMouseAt('click', screen.getByRole('img'), 300);
    fireMouseAt('click', screen.getByRole('img'), 100);
    expect(onChromosomeClick.mock.calls).toEqual([['2'], ['1']]);
  });

  it('names a PCF segment under the pointer, and nothing once the pointer leaves it', async () => {
    serveTrackFetchFrom(
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            items: url.endsWith('/pcf')
              ? [{ chr: '1', start: 0, end: 100, value: 0.5, origin: 'maternal' }]
              : [{ chr: '1', start: 10, end: 11, value: 0.5, origin: 'paternal' }],
          }),
        }),
      ),
    );
    renderChart({ pcfUrls: ['http://test/pcf'] });
    const canvas = await screen.findByRole('img', { name: /1 site, 1 PCF segment$/ });
    const tooltip = () => document.body.querySelector('.viz-tooltip');

    // The segment runs across the 400 px chart at value 0.5, the middle of the 120 px axis.
    fireMouseAt('mouseMove', canvas, 200, 60);
    expect(tooltip()).toHaveTextContent('PCF maternal');
    expect(tooltip()).toHaveTextContent('value 0.5000');

    fireMouseAt('mouseMove', canvas, 200, 10);
    expect(tooltip()).toBeNull();

    fireMouseAt('mouseMove', canvas, 200, 60);
    expect(tooltip()).not.toBeNull();
    fireEvent.mouseLeave(canvas);
    expect(tooltip()).toBeNull();
  });

  it('names an empty region as empty, on the folded axis’s own terms (#529)', async () => {
    respondWith([]);

    renderChart({ maxValue: 0.5 });

    expect(
      await screen.findByRole('img', { name: 'APCAD minor allele fraction on chr1: no data' }),
    ).toBeInTheDocument();
  });

  it('never names a load in flight or a failed load as an empty region (#529, #510)', async () => {
    serveTrackFetchFrom(vi.fn(() => new Promise<never>(() => undefined)));
    const pending = renderChart({ sampleId: 'S1' });
    expect(
      screen.getByRole('img', { name: 'APCAD B-allele frequency of S1 on chr1: loading' }),
    ).toBeInTheDocument();
    pending.unmount();

    serveTrackFetchFrom(vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }));
    renderChart({ sampleId: 'S1' });

    expect(
      await screen.findByRole('img', { name: 'APCAD B-allele frequency of S1 on chr1: failed to load' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /no data/i })).not.toBeInTheDocument();
  });
});
