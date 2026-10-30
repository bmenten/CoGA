// The parental-origin view (an embryo's segregation table, `apcad_segregation`), drawn as the PGT
// pipeline's plots draw it: one panel per heterozygous parent, each site at the embryo's own
// allele fraction, coloured by the other parent's homozygous allele. Synthetic sites only.
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import ParentalOriginChart, {
  parentalOriginChartHeight,
  segregationPoint,
} from '../visualizations/ParentalOriginChart';
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
    setLineDash: vi.fn(),
    fillText: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
  }) as unknown as CanvasRenderingContext2D;

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
);

const SITES = [
  // The mother heterozygous: the father homozygous reference (2B) and alternate (2A).
  { chr: 'chr9', start: 99, end: 100, value: 0.34, origin: 'maternal', group: '2B' },
  { chr: '9', start: 199, end: 200, value: 0.3, origin: 'maternal', group: '2A' },
  // The father heterozygous.
  { chr: '9', start: 299, end: 300, value: 0, origin: 'paternal', group: '3B' },
  // Another chromosome, no group, no origin or no value: not drawn.
  { chr: '8', start: 300, end: 301, value: 0.5, origin: 'paternal', group: '3B' },
  { chr: '9', start: 400, end: 401, value: 0.5, origin: 'maternal', group: null },
  { chr: '9', start: 450, end: 451, value: 0.5, origin: 'und', group: '2B' },
  { chr: '9', start: 500, end: 501, value: null, origin: 'maternal', group: '2B' },
];

describe('ParentalOriginChart', () => {
  let ctx: CanvasRenderingContext2D;
  beforeEach(() => {
    ctx = context();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx);
  });
  afterEach(() => vi.restoreAllMocks());

  it('draws each heterozygous parent in its own panel, titled and keyed, at the embryo\'s allele fraction', async () => {
    serveTrackFetchFrom(async () => ({ ok: true, json: async () => ({ bed_type: 'apcad_segregation', items: SITES }) }));
    render(
      <ParentalOriginChart
        urls={['https://example.test/segregation']}
        sampleId="EMB1"
        chroms={['9']}
        regionStart={0}
        regionEnd={1000}
        width={400}
        panelHeight={100}
      />,
      { wrapper },
    );

    expect(
      await screen.findByRole('img', {
        name: 'Parental origin of EMB1 on chr9:0–1,000: 2 sites where the mother is heterozygous, 1 where the father is',
      }),
    ).toBeInTheDocument();
    // Each panel says whose sites it holds and what its two colours are, in the pipeline's groups.
    expect(screen.getByText('Mother heterozygous')).toBeInTheDocument();
    expect(screen.getByText('Father heterozygous')).toBeInTheDocument();
    expect(screen.getByText('father homozygous alternate (2A)')).toBeInTheDocument();
    expect(screen.getByText('mother homozygous reference (3B)')).toBeInTheDocument();
    // How to read the bands, beside the plot.
    expect(screen.getByText(/only ⅓ and ⅔, nothing at 0 or 1: both of its homologs/)).toBeInTheDocument();
    const labels = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls.map(([text]) => text);
    expect(labels).toEqual(expect.arrayContaining(['0', '⅓', '½', '⅔', '1']));
    expect((ctx.arc as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(3);
  });

  it('draws every chromosome on the genome view\'s axis, each named', async () => {
    serveTrackFetchFrom(async () => ({ ok: true, json: async () => ({ bed_type: 'apcad_segregation', items: SITES }) }));
    const { container } = render(
      <ParentalOriginChart
        urls={['https://example.test/segregation/batch']}
        sampleId="EMB1"
        chroms={['8', '9']}
        layout={{ offsets: { '8': 0, '9': 1000 }, lengths: { '8': 1000, '9': 1000 }, total: 2000 }}
        width={400}
        panelHeight={80}
      />,
      { wrapper },
    );

    expect(
      await screen.findByRole('img', {
        name: 'Parental origin of EMB1 across 2 chromosomes: 2 sites where the mother is heterozygous, 2 where the father is',
      }),
    ).toBeInTheDocument();
    expect([...container.querySelectorAll('.parental-origin-chroms span')].map((node) => node.textContent)).toEqual(['8', '9']);
    // chr9's sites sit in the right half of the axis.
    const xs = (ctx.arc as ReturnType<typeof vi.fn>).mock.calls.map(([x]) => x as number);
    expect(xs.filter((x) => x >= 200)).toHaveLength(3);
  });

  it('says a view without sites has none, not that the table failed', async () => {
    serveTrackFetchFrom(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    render(
      <ParentalOriginChart urls={['https://example.test/segregation']} sampleId="EMB1" chroms={['9']} regionStart={0} regionEnd={1000} />,
      { wrapper },
    );

    expect(await screen.findByText('No segregation sites in this view')).toBeInTheDocument();
  });
});

describe('segregationPoint', () => {
  it('reads the embryo\'s allele fraction back from the stored value and the group', () => {
    // B: the value is the alternate-allele fraction; A: one minus it.
    expect(segregationPoint({ chr: 'chr9', start: 9, end: 10, value: 0.3, origin: 'maternal', group: '2B' })).toEqual({
      chr: '9', pos: 10, fraction: 0.3, origin: 'maternal', allele: 'B',
    });
    expect(segregationPoint({ chr: '9', start: 9, end: 10, value: 0.3, origin: 'paternal', group: '3A' })?.fraction).toBeCloseTo(0.7);
    expect(segregationPoint({ chr: '9', start: 9, end: 10, value: 0.3, origin: 'paternal', group: '1A' })).toBeNull();
  });

  it('gives the height the chart takes', () => {
    expect(parentalOriginChartHeight(100, false)).toBe(2 * (18 + 100) + 18);
    expect(parentalOriginChartHeight(80, true)).toBe(2 * (18 + 80) + 14 + 18);
  });
});
