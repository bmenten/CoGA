import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CoverageSegmentsChart from '../visualizations/CoverageSegmentsChart';

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

describe('CoverageSegmentsChart', () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => createCanvasContext());
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
    vi.stubGlobal('fetch', fetchMock);

    const { rerender } = render(
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
});
