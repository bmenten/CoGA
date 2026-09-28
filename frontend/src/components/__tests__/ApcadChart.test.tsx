import { render, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { type ReactElement, type ReactNode } from 'react';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ApcadChart from '../visualizations/ApcadChart';
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

describe('ApcadChart', () => {
  let canvasContext: CanvasRenderingContext2D;

  beforeEach(() => {
    canvasContext = createCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => canvasContext);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not refetch APCAD data when only width and layout change', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          items: [
            { chr: '1', start: 0, end: 50, value: 0.25, origin: 'paternal' },
            { chr: '1', start: 50, end: 100, value: 0.75, origin: 'maternal' },
          ],
        }),
      }),
    );
    serveTrackFetchFrom(fetchMock);

    const { rerender, container } = renderWithClient(
      <ApcadChart
        apcadUrls={['https://example.test/apcad']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // The chart opts into click telemetry via data-audit-id (the global click
    // logger captures it); guard against a refactor silently dropping the hook.
    expect(container.querySelector('[data-audit-id="apcad-chart"]')).not.toBeNull();

    rerender(
      <ApcadChart
        apcadUrls={['https://example.test/apcad']}
        chroms={['1']}
        width={640}
        height={120}
        layout={{ offsets: { '1': 0 }, lengths: { '1': 100 }, total: 100 }}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it('fetches PCF segment overlays separately from raw APCAD points', async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          items: url.endsWith('/pcf')
            ? [{ chr: '1', start: 0, end: 100, value: 0.5, origin: 'maternal' }]
            : [{ chr: '1', start: 0, end: 50, value: 0.25, origin: 'paternal' }],
        }),
      }),
    );
    serveTrackFetchFrom(fetchMock);

    renderWithClient(
      <ApcadChart
        apcadUrls={['https://example.test/apcad']}
        pcfUrls={['https://example.test/pcf']}
        chroms={['1']}
        width={320}
        height={120}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(canvasContext.arc).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      'https://example.test/pcf',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
