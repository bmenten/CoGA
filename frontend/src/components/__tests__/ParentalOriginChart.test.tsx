// The parental-origin view (an embryo's segregation table, `apcad_segregation`): one panel per
// parent, the sites where that parent is heterozygous, with guide lines at 0, 1/3, 1/2 and 2/3
// and a strip of each window's share of sites at 0. Synthetic sites only.
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import ParentalOriginChart, { ZERO_BAND, zeroShareByWindow } from '../visualizations/ParentalOriginChart';
import { serveTrackFetchFrom } from '../../test/trackFetchMock';

vi.mock('../../lib/trackFetch', async () => {
  const { fetchTrackJsonMock } = await import('../../test/trackFetchMock');
  return { fetchTrackJson: fetchTrackJsonMock };
});

const context = () =>
  ({
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fillText: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
  }) as unknown as CanvasRenderingContext2D;

const renderChart = () => {
  const client = createTestQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <ParentalOriginChart
      url="https://example.test/segregation"
      sampleId="EMB1"
      chrom="9"
      regionStart={0}
      regionEnd={1000}
      width={400}
      height={160}
    />,
    { wrapper: Wrapper },
  );
};

describe('ParentalOriginChart', () => {
  let ctx: CanvasRenderingContext2D;
  beforeEach(() => {
    ctx = context();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx);
  });
  afterEach(() => vi.restoreAllMocks());

  it("draws each parent's heterozygous sites in its own panel, with the guide lines", async () => {
    serveTrackFetchFrom(async () => ({
      ok: true,
      json: async () => ({
        bed_type: 'apcad_segregation',
        items: [
          { chr: 'chr9', start: 100, end: 101, value: 0.34, origin: 'maternal' },
          { chr: '9', start: 200, end: 201, value: 0.3, origin: 'maternal' },
          { chr: '9', start: 300, end: 301, value: 0, origin: 'paternal' },
          // Another chromosome, an unknown origin, or no value: not drawn.
          { chr: '8', start: 300, end: 301, value: 0.5, origin: 'paternal' },
          { chr: '9', start: 400, end: 401, value: 0.5, origin: 'und' },
          { chr: '9', start: 500, end: 501, value: null, origin: 'maternal' },
        ],
      }),
    }));
    renderChart();

    expect(
      await screen.findByRole('img', {
        name: 'Parental origin of EMB1 on chr9:0–1,000: 2 sites where the mother is heterozygous, 1 where the father is',
      }),
    ).toBeInTheDocument();
    const labels = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls.map(([text]) => text);
    expect(labels).toEqual(expect.arrayContaining(['⅓', '½', '⅔', 'Mother heterozygous', 'Father heterozygous']));
    expect((ctx.arc as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(3);
  });

  it('says a region without sites has none, not that the table failed', async () => {
    serveTrackFetchFrom(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    renderChart();

    expect(await screen.findByText('No segregation sites in this region')).toBeInTheDocument();
  });
});

describe('zeroShareByWindow', () => {
  it("gives each window's share of sites at 0, and none for a window without sites", () => {
    const points = [
      { start: 10, value: 0 },
      { start: 20, value: 0.5 },
      { start: 30, value: ZERO_BAND - 0.01 },
      { start: 60, value: 0.34 },
      { start: 70, value: 0.3 },
    ];
    // Both of a parent's homologs (heterodisomy) in the second half: no site at 0 there.
    // Windows of 25: 10 and 20; 30; 60 and 70; none.
    expect(zeroShareByWindow(points, 0, 100, 4)).toEqual([1 / 2, 1, 0, null]);
  });
});
