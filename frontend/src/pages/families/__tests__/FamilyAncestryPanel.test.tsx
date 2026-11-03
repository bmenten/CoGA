import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyAncestryPanel from '../FamilyAncestryPanel';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
const reference = {
  sha256: 'synthetic-reference', name: 'Synthetic public reference', genome_build: 'GRCh38', source_url: 'https://example.org/reference',
  source_revision: 'synthetic-v1', method: 'Synthetic reference PCA', variant_count: 10000, sample_count: 5,
  explained_variance: [0.4, 0.3, 0.2, 0.1],
  populations: { AFR: 'African', AMR: 'Admixed American', EAS: 'East Asian', EUR: 'European', SAS: 'South Asian' },
  points: ['AFR', 'AMR', 'EAS', 'EUR', 'SAS'].map((population, i) => ({ id: `SYN-${i}`, population, pcs: [i, i + 1, i + 2, i + 3] })),
};
const run = { id: 'run1', source_id: 'source1', reference_sha256: reference.sha256, status: 'completed', requested_at: '2026-01-01T12:00:00Z' };
const data = {
  project_id: 'project1', source_state: 'ready', can_calculate: true, active_reference_sha256: reference.sha256,
  reference, notices: [], runs: [run],
  analysis: { run_id: run.id, source_id: 'source1', reference_sha256: reference.sha256, current_source: true, current_reference: true,
    samples: [{ sample_id: 'PROBAND', status: 'projected', pcs: [1, 2, 3, 4], call_rate: 0.99, qc: { total: 10000, called: 9900, no_call: 100 } },
      { sample_id: 'FATHER1', status: 'insufficient_data', pcs: null, call_rate: 0.5, qc: { total: 10000, called: 5000, no_call: 5000 } }],
    calculation_version: 'coga-ancestry-pca-v1', assumptions: { full_wgs: true }, created_at: '2026-01-01T12:01:00Z' },
};
const renderPanel = (familyId = 'F1') => render(<QueryClientProvider client={createTestQueryClient()}>
  <FamilyAncestryPanel familyId={familyId} projectId="project1" enabled />
</QueryClientProvider>);

describe('family ancestry PCA', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockResolvedValue({ data });
    api.post.mockResolvedValue({ data: { status: 'queued' } });
  });
  it('plots reference and valid family individuals, exposes QC and changes the axes and population visibility', async () => {
    const { container } = renderPanel();
    expect(await screen.findByRole('img', { name: /Ancestry PCA: 1 family individual/ })).toBeInTheDocument();
    expect(container.querySelectorAll('circle[data-population]')).toHaveLength(5);
    expect(container.querySelector('[data-sample="PROBAND"]')).toBeInTheDocument();
    expect(container.querySelector('[data-sample="FATHER1"]')).not.toBeInTheDocument();
    expect(screen.getByText('Insufficient data — not plotted')).toBeInTheDocument();
    expect(screen.getByText('99.0%')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Vertical axis'), { target: { value: '2' } });
    expect(container.querySelector('desc')).toHaveTextContent('vertical PC3 (20.00%)');
    fireEvent.click(screen.getByRole('checkbox', { name: /AFR · African/ }));
    expect(container.querySelectorAll('circle[data-population]')).toHaveLength(4);
    fireEvent.change(screen.getByLabelText('Center view'), { target: { value: 'PROBAND' } });
    expect(screen.getByLabelText('Zoom 3.0×')).toHaveValue('3');
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(screen.getByLabelText('Center view')).toHaveValue('');
    expect(screen.getByLabelText('Zoom 1.0×')).toHaveValue('1');
  });
  it('queues a scoped family calculation and reads a selected historical result', async () => {
    renderPanel('SYN/F1');
    await screen.findByRole('img');
    fireEvent.click(screen.getByRole('button', { name: 'Calculate ancestry for this family' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/families/SYN%2FF1/ancestry/calculate', null, { params: { project_id: 'project1' } }));
    fireEvent.change(screen.getByLabelText('Projection history'), { target: { value: 'run1' } });
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/families/SYN%2FF1/ancestry', { params: { project_id: 'project1', run_id: 'run1' } }));
  });
  it('shows reference-only and read-only states without inventing a family point', async () => {
    api.get.mockResolvedValue({ data: { ...data, can_calculate: false, analysis: null, runs: [] } });
    const { container } = renderPanel();
    expect(await screen.findByText(/No ancestry projection has been calculated/)).toBeInTheDocument();
    expect(container.querySelectorAll('[data-sample]')).toHaveLength(0);
    expect(screen.queryByLabelText('Center view')).not.toBeInTheDocument();
    const help = screen.getByRole('button', { name: 'About genetic ancestry PCA' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.focus(help);
    expect(screen.getByRole('tooltip')).toHaveTextContent('1,000 usable markers and 95% usable calls');
    expect(help).toHaveAccessibleDescription(/does not measure ancestry percentages/);
    fireEvent.blur(help);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Calculate ancestry for this family' })).toBeDisabled();
  });
  it('keeps historical axes and projections identifiable when a source or reference changes', async () => {
    api.get.mockResolvedValue({ data: { ...data, can_calculate: false, source_state: 'source_changed',
      analysis: { ...data.analysis, current_source: false, current_reference: false } } });
    renderPanel();
    expect(await screen.findByText(/Historical projection:/)).toBeInTheDocument();
    expect(screen.getByText(/A previous reference model is shown/)).toBeInTheDocument();
    expect(screen.getByRole('img')).toBeInTheDocument();
  });
  it('reports loading failures with a retry and locks a pending calculation', async () => {
    api.get.mockRejectedValueOnce(new Error('Synthetic failure'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Synthetic failure');
    api.get.mockResolvedValue({ data: { ...data, runs: [{ ...run, status: 'running' }] } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry ancestry' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ancestry calculation queued or running' })).toBeDisabled());
  });
});
