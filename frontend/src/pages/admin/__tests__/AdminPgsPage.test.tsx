import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdminPgsPage from '../AdminPgsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
const version = { id: 'version1', pgs_id: 'PGS005091', version: 'v1', revision: 1, archived: false,
  study: 'Synthetic study', source_url: 'https://example.org/study', genome_build: 'GRCh38',
  weights_sha256: 'synthetic-checksum', resolved_sha256: 'resolved-checksum', reference: { reference: 'explicit ref_allele' },
  variant_count: 2, created_at: '2026-01-01T00:00:00Z' };
const renderPage = () => render(<QueryClientProvider client={createTestQueryClient()}><AdminPgsPage /></QueryClientProvider>);

describe('exploratory PGS administration', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockResolvedValue({ data: { shortlist: ['PGS005091', 'PGS000013'], versions: [version] } });
    api.post.mockResolvedValue({ data: version });
    api.put.mockResolvedValue({ data: {} });
  });
  it('uploads a version with its study and source and explains manual retrospective calculation', async () => {
    renderPage();
    await screen.findByText('PGS005091 — Active');
    fireEvent.change(screen.getByLabelText('Version label'), { target: { value: 'v2' } });
    fireEvent.change(screen.getByLabelText('Study citation'), { target: { value: 'Synthetic study 2' } });
    fireEvent.change(screen.getByLabelText('Source URL'), { target: { value: 'https://example.org/study' } });
    await userEvent.upload(screen.getByLabelText('Scoring file'), new File(['synthetic'], 'weights.tsv', { type: 'text/tab-separated-values' }));
    // Form submission is tested here; the browser journey exercises native file-input
    // validation and the actual click through the production bundle.
    fireEvent.submit(screen.getByRole('button', { name: 'Save model version' }).closest('form')!);
    await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe('/admin/pgs/models/PGS005091/versions');
    expect(body.get('version')).toBe('v2');
    expect(body.get('study')).toBe('Synthetic study 2');
    expect(body.get('file').name).toBe('weights.tsv');
    expect(await screen.findByRole('status')).toHaveTextContent('Existing families are not recalculated automatically');
  });
  it('archives and reactivates a model with all versions retained', async () => {
    renderPage();
    const button = await screen.findByRole('button', { name: 'Archive PGS005091' });
    api.get.mockResolvedValue({ data: { shortlist: ['PGS005091'], versions: [{ ...version, archived: true }] } });
    fireEvent.click(button);
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/pgs/models/PGS005091/archive', { archived: true }));
    const reactivate = await screen.findByRole('button', { name: 'Reactivate PGS005091' });
    api.get.mockResolvedValue({ data: { shortlist: ['PGS005091'], versions: [version] } });
    fireEvent.click(reactivate);
    await waitFor(() => expect(api.put).toHaveBeenLastCalledWith('/admin/pgs/models/PGS005091/archive', { archived: false }));
    expect(screen.getByText(/v1 · GRCh38/)).toBeInTheDocument();
  });
  it('shows a failed publication without claiming success', async () => {
    api.get.mockRejectedValue(new Error('Unavailable'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('Unavailable');
  });
});
