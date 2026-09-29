import { render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { type ReactElement, type ReactNode } from 'react';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CoverageSegmentsChart from '../visualizations/CoverageSegmentsChart';
import { storage } from '../../lib/storage';
import { serveTrackFetchFrom } from '../../test/trackFetchMock';

// The charts fetch through lib/trackFetch (the shared API client); serve it from the
// fetch-shaped fixtures below.
vi.mock('../../lib/trackFetch', async () => {
  const { fetchTrackJsonMock } = await import('../../test/trackFetchMock');
  return { fetchTrackJson: fetchTrackJsonMock };
});

const createCanvasContext = (): CanvasRenderingContext2D =>
  ({
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fillText: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
  }) as unknown as CanvasRenderingContext2D;

const renderWithClient = (ui: ReactElement) => {
  const client = createTestQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(ui, { wrapper: Wrapper });
};

describe('CoverageSegmentsChart', () => {
  let canvasContext: CanvasRenderingContext2D;

  beforeEach(() => {
    canvasContext = createCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => canvasContext);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not refetch coverage data when only width and layout change', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('segments')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{ chr: '1', start: 0, end: 100, value: 0.2 }],
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          items: [
            { chr: '1', start: 0, end: 50, value: 0.1 },
            { chr: '1', start: 50, end: 100, value: -0.1 },
          ],
        }),
      });
    });
    serveTrackFetchFrom(fetchMock);

    const { rerender } = renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        chroms={['1']}
        width={640}
        height={120}
        layout={{ offsets: { '1': 0 }, lengths: { '1': 100 }, total: 100 }}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('renders coverage from raw array BED payloads', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [{ chr: '1', start: 0, end: 100, value: 0.2 }],
      }),
    );
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalled());
  });

  it('shows the gain/loss thresholds in use, and marks this browser’s own (#529)', async () => {
    serveTrackFetchFrom(
      vi.fn(() => Promise.resolve({ ok: true, json: async () => [{ chr: '1', start: 0, end: 100, value: 0.2 }] })),
    );
    storage.removeItem('coverageUpperThreshold');
    storage.removeItem('coverageLowerThreshold');
    const { unmount } = renderWithClient(
      <CoverageSegmentsChart coverageUrls={['https://example.test/coverage']} chroms={['1']} width={320} height={120} />,
    );
    expect(screen.getByText('gain > +0.35 · loss < -0.35 (log2)')).toBeInTheDocument();
    // Drawn, the name carries no state: only the thresholds that colour it.
    expect(
      await screen.findByRole('img', { name: 'Coverage log2 ratio; gain > +0.35 · loss < -0.35 (log2)' }),
    ).toBeInTheDocument();
    unmount();

    storage.setItem('coverageUpperThreshold', '0.5');
    try {
      renderWithClient(
        <CoverageSegmentsChart coverageUrls={['https://example.test/coverage']} chroms={['1']} width={320} height={120} />,
      );
      expect(screen.getByText(/gain > \+0\.5 · loss < -0\.35 \(log2\) · custom \(this browser\)/)).toBeInTheDocument();
    } finally {
      storage.removeItem('coverageUpperThreshold');
    }
  });

  // Regression: with no stored coverageRange, getCoverageRange() used to return 0,
  // collapsing the y-scale so every coverage point was drawn at a non-finite
  // coordinate (invisible) even though the data loaded correctly.
  it('draws coverage at finite coordinates when coverageRange is unset', async () => {
    storage.clear();
    const ys: number[] = [];
    canvasContext.arc = vi.fn((_x: number, y: number) => {
      ys.push(y);
    }) as unknown as CanvasRenderingContext2D['arc'];

    serveTrackFetchFrom(vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ chr: '1', start: 0, end: 100, value: 0.1 }] }),
        }),
      ),
    );

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(ys.length).toBeGreaterThan(0));
    expect(ys.every((y) => Number.isFinite(y))).toBe(true);
  });

  // A missing/empty coverage response must not blank a track that still has
  // segment data to show.
  it('renders segments even when coverage is empty', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('segments')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ chr: '1', start: 0, end: 100, value: 0.2 }] }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: [] }) });
    });
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        chroms={['1']}
        regionStart={0}
        regionEnd={100}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    // Segment lines are stroked even though there are no coverage bins.
    await waitFor(() => expect(canvasContext.stroke).toHaveBeenCalled());
  });

  // #510 — the BED endpoints answer "no data" with a 404; that is an empty source, while
  // any other failure must fail the chart rather than draw what did load.
  it('treats a "no data" 404 as an empty source and still draws the rest', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('segments')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ chr: '1', start: 0, end: 100, value: 0.2 }] }),
        });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    });
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        chroms={['1']}
        regionStart={0}
        regionEnd={100}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(canvasContext.stroke).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a failure instead of a partial or empty chart when a source errors', async () => {
    serveTrackFetchFrom(
      vi.fn((input: RequestInfo | URL) =>
        String(input).includes('segments')
          ? Promise.resolve({ ok: false, status: 500, json: async () => ({}) })
          : Promise.resolve({
              ok: true,
              json: async () => ({ items: [{ chr: '1', start: 0, end: 100, value: 0.1 }] }),
            }),
      ),
    );

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    expect(await screen.findByText(/Could not load coverage — this is not an empty result/)).toBeInTheDocument();
    expect(screen.queryByText(/No coverage data in this region/)).not.toBeInTheDocument();
    // The name says so too: it used to name only the thresholds, as if drawn (#602).
    expect(screen.getByRole('img', { name: /^Coverage log2 ratio: failed to load; gain > / })).toBeInTheDocument();
  });

  // #602 — like every other chart's, the name says when the chart is loading or has
  // nothing to show, not only which thresholds colour it.
  it('is named as loading while its sources load, and as empty when they hold nothing', async () => {
    serveTrackFetchFrom(vi.fn(() => new Promise<never>(() => {})));
    const { unmount } = renderWithClient(
      <CoverageSegmentsChart coverageUrls={['https://example.test/coverage']} chroms={['1']} width={320} height={120} />,
    );
    expect(screen.getByRole('img', { name: /^Coverage log2 ratio: loading; gain > / })).toBeInTheDocument();
    unmount();

    serveTrackFetchFrom(vi.fn(() => Promise.resolve({ ok: false, status: 404, json: async () => ({}) })));
    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage-404']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );
    expect(await screen.findByRole('img', { name: /^Coverage log2 ratio: no data; gain > / })).toBeInTheDocument();
    expect(screen.getByText('No coverage data in this region')).toBeInTheDocument();
  });
});
