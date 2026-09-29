import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';

const { apiGetMock, trackFetchMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  trackFetchMock: vi.fn(),
}));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));
vi.mock('../../lib/trackFetch', () => ({ fetchTrackJson: trackFetchMock }));

import BlacklistTrack from '../visualizations/BlacklistTrack';
import GenomeHaplotypeTrack from '../visualizations/GenomeHaplotypeTrack';
import SvTrack from '../visualizations/SvTrack';

// #510 — a failed track request must say so, never read as "nothing in this region",
// and never feed partial data into what the track computes.

const renderWithClient = (ui: ReactElement) =>
  render(<QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>);

const LAYOUT = {
  offsets: { '1': 0, '2': 1000 },
  lengths: { '1': 1000, '2': 1000 },
  total: 2000,
  chroms: ['1', '2'],
};

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
});

afterEach(() => {
  vi.restoreAllMocks();
  apiGetMock.mockReset();
  trackFetchMock.mockReset();
});

describe('GenomeHaplotypeTrack', () => {
  it('fails as a whole when any source fails, instead of computing risk from the rest', async () => {
    trackFetchMock.mockImplementation(async (url: string) => {
      if (url.includes('chr=2')) throw Object.assign(new Error('HTTP 500'), { response: { status: 500 } });
      return { samples: [{ sample: 'E1', segments: [{ start: 0, end: 500, hap1: 'A', hap2: 'B' }] }] };
    });
    const { container } = renderWithClient(
      <GenomeHaplotypeTrack
        urls={['/api/families/F1/haplotypes/batch?chr=1', '/api/families/F1/haplotypes/batch?chr=2']}
        sampleId="E1"
        role="child"
        affected={false}
        layout={LAYOUT}
        chroms={['1', '2']}
      />,
    );

    expect(await screen.findByText(/Could not load haplotypes — this is not an empty result/)).toBeInTheDocument();
    // No risk state is claimed from the one chromosome that did load.
    expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('unavailable');
    expect(screen.queryByText('No haplotype data')).not.toBeInTheDocument();
  });

  it('retries on request', async () => {
    trackFetchMock.mockRejectedValueOnce(new Error('Network Error'));
    trackFetchMock.mockResolvedValue({ samples: [] });
    renderWithClient(
      <GenomeHaplotypeTrack
        urls={['/api/families/F1/haplotypes/batch?chr=1']}
        sampleId="E1"
        role="child"
        affected={false}
        layout={LAYOUT}
        chroms={['1']}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No haplotype data')).toBeInTheDocument();
    expect(trackFetchMock).toHaveBeenCalledTimes(2);
  });

  // #529: the canvas is a named image — whose haplotypes, where, and the risk state its
  // border shows, with where that state was assessed.
  it('is named with whose haplotypes, where, and where the risk state was assessed (#529)', async () => {
    trackFetchMock.mockResolvedValue({
      samples: [{ sample: 'E1', segments: [{ chr: '1', start: 0, end: 500, hap1: 'A', hap2: 'B' }] }],
    });
    const props = {
      urls: ['/api/families/F1/haplotypes/batch?chr=1', '/api/families/F1/haplotypes/batch?chr=2'],
      sampleId: 'E1',
      role: 'child',
      affected: false,
      layout: LAYOUT,
      chroms: ['1', '2'],
    };
    const { unmount } = renderWithClient(
      <GenomeHaplotypeTrack {...props} riskRegion={{ chr: '2', start: 100, end: 200 }} />,
    );
    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of E1 across 2 chromosomes; risk state at chr2:100–200: uninformative',
      }),
    ).toBeInTheDocument();
    unmount();

    // Without an ROI the state is assessed over the first chromosome shown (analysisRegion).
    renderWithClient(<GenomeHaplotypeTrack {...props} />);
    expect(
      await screen.findByRole('img', {
        name: 'Haplotypes of E1 across 2 chromosomes; risk state on chr1: uninformative',
      }),
    ).toBeInTheDocument();
  });

  it('never names a failed or empty track by a risk state it does not have (#529, #510)', async () => {
    trackFetchMock.mockRejectedValueOnce(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    trackFetchMock.mockResolvedValue({ samples: [] });
    renderWithClient(
      <GenomeHaplotypeTrack
        urls={['/api/families/F1/haplotypes/batch?chr=1']}
        sampleId="E1"
        role="child"
        affected={false}
        layout={LAYOUT}
        chroms={['1']}
      />,
    );

    expect(
      await screen.findByRole('img', { name: 'Haplotypes of E1 on chr1: failed to load; risk state: unavailable' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(
      await screen.findByRole('img', { name: 'Haplotypes of E1 on chr1: no data; risk state: uninformative' }),
    ).toBeInTheDocument();
  });
});

describe('SvTrack', () => {
  it('shows a failure instead of "no SVs"', async () => {
    trackFetchMock.mockRejectedValue(Object.assign(new Error('HTTP 502'), { response: { status: 502 } }));
    renderWithClient(<SvTrack url="/api/families/F1/structural-variants" layout={LAYOUT} sampleId="S1" />);

    expect(await screen.findByText(/Could not load structural variants/)).toBeInTheDocument();
    expect(screen.queryByText(/no SVs for this region/i)).not.toBeInTheDocument();
  });
});

describe('BlacklistTrack', () => {
  it('shows a failure instead of "no blacklist regions"', async () => {
    apiGetMock.mockRejectedValue(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    renderWithClient(
      <BlacklistTrack assembly="GRCh38" chrom="1" width={200} height={20} regionStart={0} regionEnd={1000} />,
    );

    expect(await screen.findByText(/Could not load blacklist regions/)).toBeInTheDocument();
    expect(screen.queryByText(/No blacklist regions in this region/)).not.toBeInTheDocument();
    // Nor does its name for a screen reader say "none" (#529).
    expect(
      screen.getByRole('img', { name: 'Blacklist regions on chr1:0–1,000: failed to load' }),
    ).toBeInTheDocument();
  });

  it('still shows a genuinely empty region as empty', async () => {
    apiGetMock.mockResolvedValue({ data: [] });
    renderWithClient(
      <BlacklistTrack assembly="GRCh38" chrom="1" width={200} height={20} regionStart={0} regionEnd={1000} />,
    );

    await waitFor(() => expect(screen.getByText(/No blacklist regions in this region/)).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Blacklist regions on chr1:0–1,000: none' })).toBeInTheDocument();
  });

  it('names how many regions it draws for a screen reader, and says so while loading (#529)', async () => {
    apiGetMock.mockResolvedValue({
      data: [
        { start: 100, end: 200, label: 'High Signal Region' },
        { start: 400, end: 500, label: 'Low Mappability' },
      ],
    });
    renderWithClient(
      <BlacklistTrack assembly="GRCh38" chrom="1" width={200} height={20} regionStart={0} regionEnd={1000} />,
    );

    expect(screen.getByRole('img', { name: 'Blacklist regions on chr1:0–1,000: loading' })).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: 'Blacklist regions on chr1:0–1,000: 2' })).toBeInTheDocument();
  });
});
