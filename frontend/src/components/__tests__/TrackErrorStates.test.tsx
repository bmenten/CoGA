import { render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';

const { apiGetMock, trackFetchMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  trackFetchMock: vi.fn(),
}));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));
vi.mock('../../lib/trackFetch', () => ({ fetchTrackJson: trackFetchMock }));

import BlacklistTrack from '../visualizations/BlacklistTrack';
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

afterEach(() => {
  apiGetMock.mockReset();
  trackFetchMock.mockReset();
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
