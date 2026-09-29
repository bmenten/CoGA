// The genome-wide haplotype track (#588): the risk haplotype is inferred at the ROI and
// drawn only on the ROI's chromosome, where its homolog label means something; without
// an ROI no risk haplotype is drawn and no risk state is claimed.
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';

const { trackFetchMock, overlayMock } = vi.hoisted(() => ({
  trackFetchMock: vi.fn(),
  overlayMock: vi.fn(),
}));
vi.mock('../../lib/trackFetch', () => ({ fetchTrackJson: trackFetchMock }));
vi.mock('../../lib/haplotypeCanvas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/haplotypeCanvas')>()),
  drawHaplotypeRiskOverlay: overlayMock,
}));

import GenomeHaplotypeTrack from '../visualizations/GenomeHaplotypeTrack';

// Two 1,000 bp chromosomes on a 200 px track: chr1 is x 0–100, chr2 is x 100–200.
const LAYOUT = {
  offsets: { '1': 0, '2': 1000 },
  lengths: { '1': 1000, '2': 1000 },
  total: 2000,
  chroms: ['1', '2'],
};

// An affected father and an affected child who share his first homolog ('0') at the ROI:
// a dominant risk haplotype, paternal '0'. The child carries a paternal '0' on chr2 too,
// but chr2's '0' is another homolog altogether.
const TRIO = [
  { sample_id: 'FATHER', role: 'father', affected: true },
  { sample_id: 'MOTHER', role: 'mother', affected: false },
  { sample_id: 'CHILD', role: 'proband', affected: true },
];
const segments = (chr: string) => [{ chr, start: 0, end: 1000, hap1: '0', hap2: '1', ps: 1 }];

const renderWithClient = (ui: ReactElement) =>
  render(<QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>);

const track = (riskRegion: { chr: string; start: number; end: number } | null) => (
  <GenomeHaplotypeTrack
    urls={['/api/families/F1/haplotypes/batch?chr=1', '/api/families/F1/haplotypes/batch?chr=2']}
    sampleId="CHILD"
    role="proband"
    affected
    sex="male"
    highlightRiskHaplotype
    inheritanceModel="AD"
    familyMembers={TRIO}
    riskRegion={riskRegion}
    layout={LAYOUT}
    width={200}
    height={20}
    chroms={['1', '2']}
  />
);

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () =>
      ({
        clearRect: vi.fn(),
        fillRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        setLineDash: vi.fn(),
        fillText: vi.fn(),
      }) as unknown as CanvasRenderingContext2D,
  );
  trackFetchMock.mockImplementation(async (url: string) => {
    const chr = url.endsWith('chr=2') ? '2' : '1';
    return {
      samples: ['FATHER', 'CHILD'].map((sample) => ({ sample, segments: segments(chr) })),
    };
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  trackFetchMock.mockReset();
  overlayMock.mockReset();
});

// The x of every risk overlay drawn, in track pixels.
const overlayXs = () => overlayMock.mock.calls.map(([, x]) => x as number);

describe('GenomeHaplotypeTrack risk haplotype (#588)', () => {
  it('draws the risk haplotype only on the ROI chromosome', async () => {
    renderWithClient(track({ chr: '1', start: 400, end: 600 }));

    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of CHILD across 2 chromosomes; risk state at chr1:400–600: affected / at risk',
      }),
    ).toBeInTheDocument();
    // The child's paternal '0' on chr1 is the risk haplotype; the one on chr2 is not,
    // and was drawn as such before.
    expect(overlayXs().length).toBeGreaterThan(0);
    expect(overlayXs().every((x) => x < 100)).toBe(true);
  });

  it('follows the ROI to its chromosome', async () => {
    renderWithClient(track({ chr: 'chr2', start: 400, end: 600 }));

    expect(
      await screen.findByRole('img', { name: /; risk state at chr2:400–600: affected \/ at risk$/ }),
    ).toBeInTheDocument();
    expect(overlayXs().length).toBeGreaterThan(0);
    expect(overlayXs().every((x) => x >= 100)).toBe(true);
  });

  it('without an ROI draws no risk haplotype and claims no risk state', async () => {
    const { container } = renderWithClient(track(null));

    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of CHILD across 2 chromosomes; risk state: not assessed, no region of interest',
      }),
    ).toBeInTheDocument();
    // It used to infer the risk haplotype over the whole of chr1 and draw it genome-wide.
    expect(overlayMock).not.toHaveBeenCalled();
    const track_ = container.querySelector('[data-risk-state]');
    expect(track_?.getAttribute('data-risk-state')).toBe('not_assessed');
    expect(track_?.className).not.toMatch(/haplotype-track--affected_or_at_risk|haplotype-track--carrier/);
  });
});
