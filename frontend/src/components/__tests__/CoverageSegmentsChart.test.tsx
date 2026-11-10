import { render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { type ReactElement, type ReactNode } from 'react';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CoverageSegmentsChart from '../visualizations/CoverageSegmentsChart';
import { storage } from '../../lib/storage';
import { fireMouseAt } from '../../test/pointer';
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

  // The chart reads the BED endpoint's JSON through its generated type, `BedTrackOut`,
  // whose `value` is null where the stored row holds none. Such a bin is left out, never drawn
  // as 0, a normal log2 ratio.
  it('draws each bin the BED endpoint serves with a value, and leaves out one without', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          bed_type: 'coverage',
          items: [
            { chr: '1', start: 0, end: 100, value: 0.2 },
            { chr: '1', start: 100, end: 200, value: null },
          ],
        }),
      }),
    );
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        chroms={['1']}
        regionStart={0}
        regionEnd={200}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalledTimes(1));
  });

  it('shows the gain/loss thresholds in use, and marks this browser’s own', async () => {
    serveTrackFetchFrom(
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({ bed_type: 'coverage', items: [{ chr: '1', start: 0, end: 100, value: 0.2 }] }),
        }),
      ),
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

  // With no stored coverageRange, getCoverageRange() returns the default: a range of 0
  // would collapse the y-scale and draw every coverage point at a non-finite coordinate
  // (invisible) although the data loaded.
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

  // The BED endpoints answer "no data" with a 404; that is an empty source, while
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
    // The name says so too, not only the thresholds, as if drawn.
    expect(screen.getByRole('img', { name: /^Coverage log2 ratio: failed to load; gain > / })).toBeInTheDocument();
  });

  // Like every other chart's, the name says when the chart is loading or has
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

  // A coverage track is drawn on the axis of what it holds. Read depth on a depth axis
  // from 0, without the gain/loss colouring (its thresholds are log2 ratios) and without the
  // segments (log2 ratios).
  it('draws read depth on a depth axis from 0, without the log2 thresholds or the segments', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      String(input).includes('segments')
        ? Promise.resolve({ ok: true, json: async () => ({ items: [{ chr: '1', start: 0, end: 300, value: 0.9 }] }) })
        : Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { chr: '1', start: 0, end: 100, value: 28 },
                { chr: '1', start: 100, end: 200, value: 31 },
                { chr: '1', start: 200, end: 300, value: 60 },
              ],
            }),
          }),
    );
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        segmentsUrls={['https://example.test/segments']}
        scale="read_depth"
        chroms={['1']}
        regionStart={0}
        regionEnd={300}
        width={320}
        height={120}
      />,
    );

    // The axis: 0 to a round number at or above the values, labelled in ×.
    expect(await screen.findByRole('img', { name: 'Coverage read depth; read depth · axis 0–60×' })).toBeInTheDocument();
    expect(screen.getByText('read depth · axis 0–60×')).toBeInTheDocument();
    expect(screen.queryByText(/gain >/)).not.toBeInTheDocument();
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalledTimes(3));
    const labels = (canvasContext.fillText as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([text]) => text);
    expect(labels).toEqual(['0×', '20×', '40×', '60×']);
    // No segment line beside depths: only the four gridlines are stroked.
    expect(canvasContext.stroke).toHaveBeenCalledTimes(4);
  });

  // A coverage BED that does not declare what it holds: its values on their own range, with no
  // gridline or label that would give them a unit.
  it('draws values of unknown kind on their own range, without an axis', async () => {
    serveTrackFetchFrom(
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              { chr: '1', start: 0, end: 100, value: 0.2 },
              { chr: '1', start: 100, end: 200, value: 1.4 },
            ],
          }),
        }),
      ),
    );

    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        scale="unknown"
        chroms={['1']}
        regionStart={0}
        regionEnd={200}
        width={320}
        height={120}
      />,
    );

    expect(
      await screen.findByRole('img', {
        name: 'Coverage of unknown kind; drawn on its own range, without an axis',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('values of unknown kind (0.2–1.4) · no axis')).toBeInTheDocument();
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalledTimes(2));
    expect(canvasContext.fillText).not.toHaveBeenCalled();
    expect(canvasContext.stroke).not.toHaveBeenCalled();
  });

  // Dragging over the chart of a region zooms to what was dragged over; a click on the
  // genome-wide chart opens the chromosome under it. Neither ran in a test.
  it('turns a drag over a region into the region dragged over, and ignores a click-sized drag', async () => {
    serveTrackFetchFrom(
      vi.fn(() => Promise.resolve({ ok: true, json: async () => ({ items: [{ chr: '1', start: 1000, end: 1100, value: 0.2 }] }) })),
    );
    const onRegionSelect = vi.fn();
    const { container } = renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        chroms={['1']}
        regionStart={1000}
        regionEnd={2000}
        width={400}
        height={120}
        onRegionSelect={onRegionSelect}
      />,
    );
    const canvas = await screen.findByRole('img', { name: /^Coverage log2 ratio;/ });
    // The drag's outline, the chart's last element.
    const selection = () => (container.firstElementChild?.lastElementChild as HTMLElement).style;

    expect(selection().display).toBe('none');

    // 400 px over 1,000 bp: x 300 → 100 is 1,250–1,750, whichever way it is dragged.
    fireMouseAt('mouseDown', canvas, 300);
    fireMouseAt('mouseMove', canvas, 200);
    // The drag is shown while it lasts.
    expect(selection().display).not.toBe('none');
    expect(selection().left).toBe('200px');
    expect(selection().width).toBe('100px');
    fireMouseAt('mouseUp', canvas, 100);
    expect(onRegionSelect).toHaveBeenCalledWith(1250, 1750);
    expect(selection().display).toBe('none');

    // Less than 5 px is a click, not a selection.
    fireMouseAt('mouseDown', canvas, 100);
    fireMouseAt('mouseUp', canvas, 104);
    expect(onRegionSelect).toHaveBeenCalledTimes(1);
  });

  it('does not select a region on the genome-wide chart, and opens the chromosome under a click', async () => {
    serveTrackFetchFrom(
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              { chr: '1', start: 0, end: 100, value: 0.2 },
              { chr: '2', start: 0, end: 100, value: -0.2 },
            ],
          }),
        }),
      ),
    );
    const onRegionSelect = vi.fn();
    const onChromosomeClick = vi.fn();
    renderWithClient(
      <CoverageSegmentsChart
        coverageUrls={['https://example.test/coverage']}
        chroms={['1', '2']}
        layout={{ offsets: { '1': 0, '2': 100 }, lengths: { '1': 100, '2': 100 }, total: 200 }}
        width={400}
        height={120}
        onRegionSelect={onRegionSelect}
        onChromosomeClick={onChromosomeClick}
      />,
    );
    const canvas = await screen.findByRole('img', { name: /^Coverage log2 ratio;/ });
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalledTimes(2));

    fireMouseAt('mouseDown', canvas, 100);
    fireMouseAt('mouseUp', canvas, 300);
    expect(onRegionSelect).not.toHaveBeenCalled();

    // 400 px over 200 bp: x 300 is 50 bp into chr2, x 100 is in chr1.
    fireMouseAt('click', canvas, 300);
    fireMouseAt('click', canvas, 100);
    expect(onChromosomeClick.mock.calls).toEqual([['2'], ['1']]);
  });
});
