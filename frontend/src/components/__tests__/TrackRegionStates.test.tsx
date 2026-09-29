// #602 — the chromosome view's region tracks, on a view with no width and on the
// mitochondrion. A zero-width or inverted region asks the API for nothing, so a track over
// it is neither loading, nor empty, nor over its cap: it says there is no region in view.
// And the mitochondrion is chrM in every track's name, as the viewer header writes it.
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));

import BlacklistTrack from '../visualizations/BlacklistTrack';
import CnvTrack from '../visualizations/CnvTrack';
import DgvTrack from '../visualizations/DgvTrack';
import GeneTrack from '../visualizations/GeneTrack';
import HaplotypePhasedTrack from '../visualizations/HaplotypePhasedTrack';
import RepeatExpansionTrack from '../visualizations/RepeatExpansionTrack';
import SegmentalDuplicationTrack from '../visualizations/SegmentalDuplicationTrack';
import SmallVariantTrack from '../visualizations/SmallVariantTrack';
import VariantTrack from '../visualizations/VariantTrack';
import ZoomedIdeogram from '../visualizations/ZoomedIdeogram';

interface Region {
  chrom: string;
  start: number;
  end: number;
}

interface TrackCase {
  track: string;
  /** How the track's name begins, before the region. */
  named: string;
  /** The endpoint of the track's region request; absent when it loads the whole chromosome. */
  endpoint?: string;
  /** The rest of the name after the state, if any (the haplotype risk state). */
  suffix?: string;
  /** Text the track draws for a view with no width. */
  shown?: string;
  render: (region: Region) => ReactElement;
}

const size = { width: 300, height: 40 };

const TRACKS: TrackCase[] = [
  {
    track: 'BlacklistTrack',
    named: 'Blacklist regions on',
    endpoint: '/blacklist/',
    render: (r) => <BlacklistTrack assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />,
  },
  {
    track: 'CnvTrack',
    named: 'Clinical CNVs on',
    endpoint: '/clinical-cnvs',
    render: (r) => <CnvTrack assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />,
  },
  {
    track: 'DgvTrack',
    named: 'DGV variants on',
    endpoint: '/dgv',
    render: (r) => <DgvTrack assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />,
  },
  {
    track: 'SegmentalDuplicationTrack',
    named: 'Segmental duplications on',
    endpoint: '/segmental-duplications',
    render: (r) => (
      <SegmentalDuplicationTrack assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />
    ),
  },
  {
    track: 'GeneTrack',
    named: 'Genes on',
    endpoint: '/genes/',
    render: (r) => <GeneTrack assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} width={300} />,
  },
  {
    track: 'ZoomedIdeogram',
    named: 'Cytobands on',
    render: (r) => <ZoomedIdeogram assembly="GRCh38" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />,
  },
  {
    track: 'SmallVariantTrack',
    named: 'Small variants of S1 on',
    endpoint: '/small-variants',
    shown: 'No region in view',
    render: (r) => (
      <SmallVariantTrack familyId="F1" sampleId="S1" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />
    ),
  },
  {
    track: 'VariantTrack',
    named: 'Structural variants of S1 on',
    endpoint: '/structural-variants',
    shown: 'No region in view',
    render: (r) => (
      <VariantTrack familyId="F1" sampleId="S1" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />
    ),
  },
  {
    track: 'RepeatExpansionTrack',
    named: 'Repeat loci of S1 on',
    endpoint: '/repeat-expansions/',
    render: (r) => (
      <RepeatExpansionTrack familyId="F1" sampleId="S1" chrom={r.chrom} regionStart={r.start} regionEnd={r.end} {...size} />
    ),
  },
  {
    track: 'HaplotypePhasedTrack',
    named: 'Haplotypes of S1 on',
    endpoint: '/haplotypes',
    suffix: '; risk state: not assessed',
    render: (r) => (
      <HaplotypePhasedTrack
        familyId="F1"
        sampleId="S1"
        chrom={r.chrom}
        regionStart={r.start}
        regionEnd={r.end}
        role="child"
        affected={false}
        {...size}
      />
    ),
  },
];

const renderTrack = (ui: ReactElement) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

const trackName = (): string => {
  const images = screen.getAllByRole('img');
  expect(images).toHaveLength(1);
  return images[0].getAttribute('aria-label') ?? '';
};

const requestedUrls = (): string[] => apiGetMock.mock.calls.map(([url]) => String(url));

beforeEach(() => {
  // Nothing answers: a track can only be named from what it asked for, not from data.
  apiGetMock.mockImplementation(() => new Promise(() => {}));
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () =>
      ({
        clearRect: vi.fn(),
        fillRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        fill: vi.fn(),
        setLineDash: vi.fn(),
        fillText: vi.fn(),
        save: vi.fn(),
        restore: vi.fn(),
      }) as unknown as CanvasRenderingContext2D,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  apiGetMock.mockReset();
});

describe.each(TRACKS)('$track', ({ named, endpoint, suffix = '', shown, render: track }) => {
  it.each([
    ['zero-width', { chrom: '1', start: 5_000, end: 5_000 }],
    ['inverted', { chrom: '1', start: 6_000, end: 5_000 }],
  ])('over a %s region asks for nothing and says there is no region in view', (_kind, region) => {
    renderTrack(track(region));

    expect(trackName()).toBe(`${named} chr1: no region in view${suffix}`);
    if (endpoint) {
      expect(requestedUrls().filter((url) => url.includes(endpoint))).toEqual([]);
    }
    // Not "none", not "too many", and no empty-region message.
    expect(screen.queryByText(/^No .* in this region$|too many|for this region/i)).not.toBeInTheDocument();
    if (shown) expect(screen.getByText(shown)).toBeInTheDocument();
  });

  it('names the mitochondrion chrM', () => {
    renderTrack(track({ chrom: 'MT', start: 1, end: 16_569 }));

    expect(trackName().startsWith(`${named} chrM:1–16,569`)).toBe(true);
    expect(trackName()).not.toContain('chrMT');
  });
});
