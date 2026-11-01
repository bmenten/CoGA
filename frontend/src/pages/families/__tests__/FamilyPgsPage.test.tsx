import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyPgsPage from '../FamilyPgsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
vi.mock('../FamilyCaseHeader', () => ({ default: () => <div>Family header</div> }));
vi.mock('../../../lib/reference', () => ({ useFamilyReference: () => ({ projectId: 'project1', assemblyName: 'GRCh38', isLoading: false, isError: false, retry: vi.fn() }) }));
const data = {
  project_id: 'project1', source_state: 'ready', can_calculate: true, pending_version_ids: ['v2'],
  notices: ['New or uncalculated model versions are available. Existing families are not recalculated automatically.'],
  runs: [],
  sources: [{ id: 'source1', package_path: '/synthetic', relative_vcf: 'family.vcf', sha256: 'source-checksum', storage_version: '1:2', import_job_id: 'import1' }],
  versions: [{ id: 'v1', pgs_id: 'PGS005091', version: 'v1', archived: true, study: 'Synthetic study', genome_build: 'GRCh38', source_url: 'https://example.org/study', weights_sha256: 'weights-checksum', resolved_sha256: 'resolved-checksum' }],
  results: [{ id: 'r1', run_id: 'run1', source_id: 'source1', version_id: 'v1', sample_id: 'PROBAND', raw_score: -1.5,
    qc: { total: 2, called: 1, assumed_ref: 1 }, current_source: true, created_at: '2026-01-01', calculation_version: 'coga-pgs-gt-v1', assumptions: { absent_record_is_hom_ref: true } }],
};
const renderPage = () => render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/families/F1/pgs?project_id=project1']}>
  <Routes><Route path="/families/:familyId/pgs" element={<FamilyPgsPage />} /></Routes>
</MemoryRouter></QueryClientProvider>);

describe('family exploratory PGS', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/pgs') ? data : { family_id: 'F1', projects: ['project1'] } }));
    api.post.mockResolvedValue({ data: { status: 'queued' } });
  });
  it('keeps archived scores, QC and complete provenance visible and manually calculates the family', async () => {
    renderPage();
    expect(await screen.findByText('PGS005091 / v1 (archived)')).toBeInTheDocument();
    expect(screen.getByText('-1.500000000')).toBeInTheDocument();
    expect(screen.getByText(/assumed ref: 1/)).toBeInTheDocument();
    expect(screen.getByText(/weights-checksum/)).toBeInTheDocument();
    expect(screen.getByText(/coga-pgs-gt-v1/)).toBeInTheDocument();
    expect(screen.getByText(/Existing families are not recalculated automatically/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Calculate active models for this family' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/families/F1/pgs/calculate', null, { params: { project_id: 'project1' } }));
  });
  it('shows incomplete QC instead of a fabricated raw score and blocks unavailable sources', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/pgs') ? {
      ...data, source_state: 'genotype_storage_changed', can_calculate: false,
      results: [{ ...data.results[0], raw_score: null, qc: { total: 2, no_call: 1, assumed_ref: 1 }, current_source: false }],
    } : { family_id: 'F1' } }));
    renderPage();
    expect(await screen.findByText('Incomplete — no raw score')).toBeInTheDocument();
    expect(screen.getByText(/no call: 1/)).toBeInTheDocument();
    expect(screen.getByText('Historical import')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Calculate active models for this family' })).toBeDisabled();
  });
  it('shows failed loading and an explicit retry', async () => {
    api.get.mockRejectedValue(new Error('Unavailable'));
    renderPage();
    expect(await screen.findByText('Family could not be loaded')).toBeInTheDocument();
    expect(screen.getByText(/Unavailable/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
