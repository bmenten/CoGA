import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ApcadChart from '../ApcadChart';

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

const renderChart = () => {
  const context = HTMLCanvasElement.prototype.getContext.call(
    document.createElement('canvas'),
    '2d',
  ) as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    ...context,
    arc: arcSpy,
  } as unknown as CanvasRenderingContext2D);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApcadChart apcadUrls={['http://test/apcad']} width={400} height={120} chroms={['1']} />
    </QueryClientProvider>,
  );
};

const respondWith = (items: unknown[]) => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items }),
    }),
  );
};

describe('ApcadChart', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    arcSpy.mockClear();
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
});
