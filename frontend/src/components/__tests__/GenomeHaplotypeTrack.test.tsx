// The genome-wide haplotype track: the risk haplotype is inferred at the ROI and
// drawn only on the ROI's chromosome, where its homolog label means something; without
// an ROI no risk haplotype is drawn and no risk state is claimed.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import React, { type ReactElement } from 'react';
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

describe('GenomeHaplotypeTrack risk haplotype', () => {
  it('draws the risk haplotype only on the ROI chromosome', async () => {
    renderWithClient(track({ chr: '1', start: 400, end: 600 }));

    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of CHILD across 2 chromosomes; risk state at chr1:400–600: affected / at risk',
      }),
    ).toBeInTheDocument();
    // The child's paternal '0' on chr1 is the risk haplotype; the one on chr2 is not, and is
    // not drawn as such.
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

  // Blocks built for another pedigree or other sexes resolve no disease haplotype.
  it('reads no risk state from outdated blocks, and says why', async () => {
    trackFetchMock.mockImplementation(async (url: string) => {
      const chr = url.endsWith('chr=2') ? '2' : '1';
      return {
        blocks_outdated: true,
        samples: ['FATHER', 'CHILD'].map((sample) => ({ sample, segments: segments(chr) })),
      };
    });
    const { container } = renderWithClient(track({ chr: '1', start: 400, end: 600 }));

    const warning =
      "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.";
    expect(
      await screen.findByRole('img', {
        name: `Haplotypes of CHILD across 2 chromosomes; risk state at chr1:400–600: uninformative; ${warning}`,
      }),
    ).toBeInTheDocument();
    // No risk haplotype is drawn, and the child does not read affected / at risk.
    expect(overlayMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('uninformative');
    expect(screen.getByRole('note')).toHaveTextContent('Outdated blocks: no risk state');
    expect(screen.getByRole('note')).toHaveAttribute('title', warning);
  });

  // The PGT analysis leaves an embryo uncalled whose markers at the ROI mix both of a parent's
  // homologs: its blocks show one, so the track reads no risk state from them either.
  it("reads no risk state for an embryo whose markers mix both of a parent's homologs, and says why", async () => {
    const warning = "This embryo's markers at the ROI mix both of the father's homologs: no call is made.";
    const { container } = renderWithClient(
      React.cloneElement(track({ chr: '1', start: 400, end: 600 }), { mixedHomologsWarning: warning }),
    );

    expect(
      await screen.findByRole('img', {
        name: `Haplotypes of CHILD across 2 chromosomes; risk state at chr1:400–600: uninformative; ${warning}`,
      }),
    ).toBeInTheDocument();
    // Read from the blocks alone, the child is affected / at risk.
    expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('uninformative');
    expect(screen.getByRole('note')).toHaveTextContent('Both homologs of a parent: no risk state');
    expect(screen.getByRole('note')).toHaveAttribute('title', warning);
  });

  it('without an ROI draws no risk haplotype and claims no risk state', async () => {
    const { container } = renderWithClient(track(null));

    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of CHILD across 2 chromosomes; risk state: not assessed, no region of interest',
      }),
    ).toBeInTheDocument();
    // No risk haplotype is inferred over a stand-in region and drawn genome-wide.
    expect(overlayMock).not.toHaveBeenCalled();
    const track_ = container.querySelector('[data-risk-state]');
    expect(track_?.getAttribute('data-risk-state')).toBe('not_assessed');
    expect(track_?.className).not.toMatch(/haplotype-track--affected_or_at_risk|haplotype-track--carrier/);
  });
});

// A failed request must say so, never read as "no haplotype data", and never feed
// the chromosomes that did load into a risk state. The canvas is a named image:
// whose haplotypes, where, and the risk state its border shows, with where that state was
// assessed.
describe('GenomeHaplotypeTrack failures and its name', () => {
  const unaffectedChild = (urls: string[], chroms: string[]) => (
    <GenomeHaplotypeTrack
      urls={urls}
      sampleId="E1"
      role="child"
      affected={false}
      layout={LAYOUT}
      chroms={chroms}
    />
  );
  const CHR1 = '/api/families/F1/haplotypes/batch?chr=1';
  const CHR2 = '/api/families/F1/haplotypes/batch?chr=2';

  it('fails as a whole when any source fails, instead of computing risk from the rest', async () => {
    trackFetchMock.mockImplementation(async (url: string) => {
      if (url.includes('chr=2')) throw Object.assign(new Error('HTTP 500'), { response: { status: 500 } });
      return { samples: [{ sample: 'E1', segments: [{ start: 0, end: 500, hap1: 'A', hap2: 'B' }] }] };
    });
    const { container } = renderWithClient(unaffectedChild([CHR1, CHR2], ['1', '2']));

    expect(await screen.findByText(/Could not load haplotypes — this is not an empty result/)).toBeInTheDocument();
    // No risk state is claimed from the one chromosome that did load.
    expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('unavailable');
    expect(screen.queryByText('No haplotype data')).not.toBeInTheDocument();
  });

  it('retries on request', async () => {
    trackFetchMock.mockRejectedValueOnce(new Error('Network Error'));
    trackFetchMock.mockResolvedValue({ samples: [] });
    renderWithClient(unaffectedChild([CHR1], ['1']));

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No haplotype data')).toBeInTheDocument();
    expect(trackFetchMock).toHaveBeenCalledTimes(2);
  });

  // Without an ROI no risk state is assessed: `without an ROI draws no risk haplotype and
  // claims no risk state` above: no chromosome stands in for the disorder's locus.
  it('is named with whose haplotypes, where, and where the risk state was assessed', async () => {
    trackFetchMock.mockResolvedValue({
      samples: [{ sample: 'E1', segments: [{ chr: '1', start: 0, end: 500, hap1: 'A', hap2: 'B' }] }],
    });
    renderWithClient(
      React.cloneElement(unaffectedChild([CHR1, CHR2], ['1', '2']), {
        riskRegion: { chr: '2', start: 100, end: 200 },
      }),
    );
    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of E1 across 2 chromosomes; risk state at chr2:100–200: uninformative',
      }),
    ).toBeInTheDocument();
  });

  it('never names a failed or empty track by a risk state it does not have', async () => {
    trackFetchMock.mockRejectedValueOnce(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    trackFetchMock.mockResolvedValue({ samples: [] });
    renderWithClient(unaffectedChild([CHR1], ['1']));

    expect(
      await screen.findByRole('img', { name: 'Haplotypes of E1 on chr1: failed to load; risk state: unavailable' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of E1 on chr1: no data; risk state: not assessed, no region of interest',
      }),
    ).toBeInTheDocument();
  });
});

// The track looks a block up in the layout by its chromosome as normalizeChrom names it
// (`1`, `X`), as the layout does, so a block stored as `x` or `chrX` is drawn too.
describe('GenomeHaplotypeTrack places a block however its chromosome is stored', () => {
  // The genome overview's layout: chr1 and chrX, 1,000 bp each, on a 200 px track: X is x 100–200.
  const LAYOUT_1_X = {
    offsets: { '1': 0, X: 1000 },
    lengths: { '1': 1000, X: 1000 },
    total: 2000,
    chroms: ['1', 'X'],
  };

  // The x of each rectangle the last draw filled: one per lane of a block.
  let filled: number[] = [];

  beforeEach(() => {
    filled = [];
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(
      () =>
        ({
          clearRect: () => {
            filled = [];
          },
          fillRect: (x: number) => {
            filled.push(x);
          },
          beginPath: vi.fn(),
          moveTo: vi.fn(),
          lineTo: vi.fn(),
          stroke: vi.fn(),
          setLineDash: vi.fn(),
          fillText: vi.fn(),
        }) as unknown as CanvasRenderingContext2D,
    );
  });

  it.each(['X', 'x', 'chrX', 'chrx'])('draws a block stored on %s on chrX', async (chr) => {
    trackFetchMock.mockResolvedValue({ samples: [{ sample: 'CHILD', segments: segments(chr) }] });
    renderWithClient(
      <GenomeHaplotypeTrack
        urls={['/api/families/F1/haplotypes/batch?chr=1&chr=X']}
        sampleId="CHILD"
        role="proband"
        affected={false}
        sex="female"
        layout={LAYOUT_1_X}
        width={200}
        height={20}
        chroms={LAYOUT_1_X.chroms}
      />,
    );

    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of CHILD across 2 chromosomes; risk state: not assessed, no region of interest',
      }),
    ).toBeInTheDocument();
    // Both of her lanes, over the whole of chrX.
    await waitFor(() => expect(filled).toEqual([100, 100]));
  });
});

// Where a child's haplotypes change between two blocks of a chromosome the track draws a dashed
// line (a recombination), and a deleted haplotype ('.') is struck through: neither ran in a test.
describe('GenomeHaplotypeTrack marks recombinations and deleted haplotypes', () => {
  // Each line the last draw stroked, from its moveTo to its lineTo, and whether it was dashed.
  let strokes: { from: [number, number]; to: [number, number]; dashed: boolean }[] = [];

  beforeEach(() => {
    strokes = [];
    let dash: number[] = [];
    let from: [number, number] = [0, 0];
    let to: [number, number] = [0, 0];
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(
      () =>
        ({
          clearRect: () => {
            strokes = [];
          },
          fillRect: vi.fn(),
          beginPath: vi.fn(),
          moveTo: (x: number, y: number) => {
            from = [x, y];
          },
          lineTo: (x: number, y: number) => {
            to = [x, y];
          },
          stroke: () => {
            strokes.push({ from, to, dashed: dash.length > 0 });
          },
          setLineDash: (segments: number[]) => {
            dash = segments;
          },
          fillText: vi.fn(),
        }) as unknown as CanvasRenderingContext2D,
    );
  });

  it('draws a dashed line where the haplotypes change, and strikes a deleted haplotype through', async () => {
    // chr1 is x 0–100 on the 200 px track: the second block starts at x 50, its hap2 deleted.
    trackFetchMock.mockResolvedValue({
      samples: [
        {
          sample: 'CHILD',
          segments: [
            { chr: '1', start: 0, end: 500, hap1: '0', hap2: '1', ps: 1 },
            { chr: '1', start: 500, end: 1000, hap1: '1', hap2: '.', ps: 1 },
          ],
        },
      ],
    });
    renderWithClient(
      <GenomeHaplotypeTrack
        urls={['/api/families/F1/haplotypes/batch?chr=1']}
        sampleId="CHILD"
        role="proband"
        affected={false}
        sex="female"
        layout={LAYOUT}
        width={200}
        height={20}
        chroms={['1']}
      />,
    );

    await waitFor(() => expect(strokes.length).toBeGreaterThan(0));
    // The recombination: dashed, the full height of the track at x 50.
    expect(strokes.filter(({ dashed }) => dashed)).toEqual([{ from: [50, 0], to: [50, 20], dashed: true }]);
    // The deleted hap2 of the second block, on the lower lane (y 11–20), struck corner to corner.
    expect(strokes.filter(({ dashed }) => !dashed)).toEqual([{ from: [50.75, 12], to: [99.25, 19], dashed: false }]);
  });
});
