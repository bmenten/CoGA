import { QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyAncestryPanel from '../FamilyAncestryPanel';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
const reference = {
  sha256: 'synthetic-reference',
  name: 'Synthetic public reference',
  genome_build: 'GRCh38',
  source_url: 'https://example.org/reference',
  source_revision: 'synthetic-v1',
  method: 'Synthetic reference PCA',
  variant_count: 10000,
  sample_count: 5,
  explained_variance: [0.4, 0.3, 0.2, 0.1],
  populations: {
    AFR: 'African',
    AMR: 'Admixed American',
    EAS: 'East Asian',
    EUR: 'European',
    SAS: 'South Asian',
  },
  points: ['AFR', 'AMR', 'EAS', 'EUR', 'SAS'].map((population, i) => ({
    id: `SYN-${i}`,
    population,
    pcs: [i, i + 1, i + 2, i + 3],
  })),
};
const run = {
  id: 'run1',
  source_id: 'source1',
  reference_sha256: reference.sha256,
  status: 'completed',
  requested_at: '2026-01-01T12:00:00Z',
};
const data = {
  project_id: 'project1',
  source_state: 'ready',
  can_calculate: true,
  active_reference_sha256: reference.sha256,
  reference,
  notices: [],
  runs: [run],
  analysis: {
    run_id: run.id,
    source_id: 'source1',
    reference_sha256: reference.sha256,
    current_source: true,
    current_reference: true,
    samples: [
      {
        sample_id: 'PROBAND',
        status: 'projected',
        pcs: [1, 2, 3, 4],
        call_rate: 0.99,
        qc: {
          total: 10000,
          called: 4000,
          called_unfiltered: 0,
          assumed_ref: 5900,
          no_call: 100,
        },
      },
      {
        sample_id: 'FATHER1',
        status: 'insufficient_data',
        pcs: null,
        call_rate: 0.5,
        qc: {
          total: 10000,
          called: 5000,
          called_unfiltered: 0,
          assumed_ref: 0,
          no_call: 5000,
        },
      },
    ],
    calculation_version: 'coga-ancestry-pca-v1',
    assumptions: {
      full_wgs: true,
      absent_record_is_hom_ref: true,
      min_observed_fraction: 0.2,
      source_limitations: '',
    },
    created_at: '2026-01-01T12:01:00Z',
  },
};
const renderPanel = (familyId = 'F1') =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <FamilyAncestryPanel familyId={familyId} projectId="project1" enabled />
    </QueryClientProvider>
  );

describe('family ancestry PCA', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockResolvedValue({ data });
    api.post.mockResolvedValue({ data: { status: 'queued' } });
  });
  it('plots reference and valid family individuals, exposes QC and changes the axes and population visibility', async () => {
    const { container } = renderPanel();
    expect(
      await screen.findByRole('img', {
        name: /not for clinical use\): 1 family individual/,
      })
    ).toBeInTheDocument();
    // The plot carries the mark itself, so a screenshot or a print of it does too.
    expect(
      container.querySelector('.ancestry-exploratory-mark')
    ).toHaveTextContent('Exploratory — not for clinical use');
    expect(container.querySelectorAll('circle[data-population]')).toHaveLength(
      5
    );
    expect(
      container.querySelector('[data-sample="PROBAND"]')
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-sample="FATHER1"]')
    ).not.toBeInTheDocument();
    const proband = within(screen.getByRole('row', { name: /PROBAND/ }));
    expect(proband.getByText('Projected')).toBeInTheDocument();
    // Observed calls and assumed reference calls are told apart; the share counts both.
    expect(proband.getByText('4,000')).toBeInTheDocument();
    expect(proband.getByText('5,900')).toBeInTheDocument();
    expect(proband.getByText('99.0%')).toBeInTheDocument();
    expect(proband.getByText('no call: 100')).toBeInTheDocument();
    expect(
      screen.getByText('Insufficient data — not plotted')
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Vertical axis'), {
      target: { value: '2' },
    });
    expect(container.querySelector('desc')).toHaveTextContent(
      'vertical PC3 (20.00%)'
    );
    const legend = screen.getByRole('group', {
      name: 'Reference populations shown',
    });
    fireEvent.click(
      within(legend).getByRole('checkbox', { name: /AFR · African/ })
    );
    expect(container.querySelectorAll('circle[data-population]')).toHaveLength(
      4
    );
    fireEvent.change(screen.getByLabelText('Center view'), {
      target: { value: 'PROBAND' },
    });
    expect(screen.getByLabelText('Zoom 3.0×')).toHaveValue('3');
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(screen.getByLabelText('Center view')).toHaveValue('');
    expect(screen.getByLabelText('Zoom 1.0×')).toHaveValue('1');
  });
  it('names the policies of the projection shown, in words', async () => {
    renderPanel();
    await screen.findByRole('img');
    expect(
      screen.getByText('Absent records taken as homozygous reference')
        .nextSibling
    ).toHaveTextContent('Yes');
    expect(
      screen.getByText('Minimum share of observed calls').nextSibling
    ).toHaveTextContent('0.2');
    // An empty limitation is not listed.
    expect(screen.queryByText('Source limitations')).not.toBeInTheDocument();
  });
  it('queues a scoped family calculation and reads a selected earlier result', async () => {
    renderPanel('SYN/F1');
    await screen.findByRole('img');
    fireEvent.click(
      screen.getByRole('button', { name: 'Calculate PCA projection' })
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/families/SYN%2FF1/ancestry/calculate',
        null,
        {
          params: { project_id: 'project1' },
        }
      )
    );
    fireEvent.change(screen.getByLabelText('Projection'), {
      target: { value: 'run1' },
    });
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/families/SYN%2FF1/ancestry', {
        params: { project_id: 'project1', run_id: 'run1' },
      })
    );
  });
  it('shows reference-only and read-only states, and why, without inventing a family point', async () => {
    api.get.mockResolvedValue({
      data: {
        ...data,
        can_calculate: false,
        analysis: null,
        runs: [],
        source_state: 'no_full_wgs_source',
        notices: [
          'No WGS source is registered for this family: register a retained whole-genome VCF below.',
        ],
      },
    });
    const { container } = renderPanel();
    expect(
      await screen.findByText(/No PCA projection has been calculated/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No WGS source is registered for this family/)
    ).toBeInTheDocument();
    expect(container.querySelectorAll('[data-sample]')).toHaveLength(0);
    expect(screen.queryByLabelText('Center view')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Calculate PCA projection' })
    ).toBeDisabled();
  });
  it('says when the latest calculation failed, and when it was requested', async () => {
    const failed = {
      ...run,
      id: 'run2',
      status: 'failed',
      requested_at: '2026-01-02T12:00:00Z',
      error: 'A newer WGS source exists; request a new calculation.',
    };
    api.get.mockResolvedValue({ data: { ...data, runs: [failed, run] } });
    renderPanel();
    expect(
      await screen.findByText(/: failed — A newer WGS source exists/)
    ).toHaveTextContent(new Date(failed.requested_at).toLocaleString());
  });
  it('keeps earlier axes and projections identifiable when a source or reference changes', async () => {
    api.get.mockResolvedValue({
      data: {
        ...data,
        can_calculate: false,
        source_state: 'source_changed',
        analysis: {
          ...data.analysis,
          current_source: false,
          current_reference: false,
        },
      },
    });
    renderPanel();
    expect(await screen.findByText(/Earlier projection:/)).toBeInTheDocument();
    expect(
      screen.getByText(/calculated with an earlier reference model/)
    ).toBeInTheDocument();
    expect(screen.getByRole('img')).toBeInTheDocument();
  });
  it('reports loading failures with a retry and locks a pending calculation', async () => {
    api.get.mockRejectedValueOnce(new Error('Synthetic failure'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Synthetic failure'
    );
    api.get.mockResolvedValue({
      data: { ...data, runs: [{ ...run, status: 'running' }] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry ancestry' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'PCA projection queued or running' })
      ).toBeDisabled()
    );
  });
});
