import { QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyAncestryPanel from '../FamilyAncestryPanel';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
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
  trigger: 'automatic',
  reference_sha256: reference.sha256,
  status: 'completed',
  requested_at: '2026-01-01T12:00:00Z',
};
const scan = (callset: string, label: string, kind: string, extra = {}) => ({
  callset,
  label,
  kind,
  carriers: 3800,
  expected_carriers: 3960.4,
  coverage_ratio: 0.96,
  exome_ratio: 0.97,
  outside_exome_ratio: 0.95,
  explicit_calls: 4100,
  accepts_unfiltered: false,
  covered_markers: 10000,
  usable_markers: 9900,
  call_rate: 0.99,
  qc: { called: 4000, assumed_ref: 5900, no_call: 100 },
  projectable: true,
  reason: null,
  ...extra,
});
const partialReason =
  'The callset holds fewer non-reference calls than a sequenced genome or exome has at these markers.';
const data = {
  project_id: 'project1',
  state: 'ready',
  can_calculate: true,
  active_reference_sha256: reference.sha256,
  reference,
  notices: [],
  runs: [run],
  analysis: {
    run_id: run.id,
    reference_sha256: reference.sha256,
    storage_version: '4:123',
    current_genotypes: true,
    current_reference: true,
    samples: [
      {
        sample_id: 'PROBAND',
        status: 'projected',
        pcs: [1, 2, 3, 4],
        pcs_se: [0.05, 0.1, 0.2, 0.4],
        call_rate: 0.99,
        qc: { called: 4000, assumed_ref: 5900, no_call: 100 },
        callset: 'clair3',
        kind: 'genome',
        usable_markers: 9900,
        reason: null,
        candidates: [scan('clair3', 'Direct calls', 'genome')],
      },
      {
        sample_id: 'MOTHER',
        status: 'projected',
        pcs: [2, 1, 0, 1],
        call_rate: 0.98,
        qc: { called: 3500, assumed_ref: 4700, outside_covered: 1100 },
        callset: 'clair3',
        kind: 'exome',
        usable_markers: 8200,
        reason: null,
        candidates: [
          scan('clair3', 'Direct calls', 'exome', {
            covered_markers: 8398,
            usable_markers: 8200,
            call_rate: 0.976,
            exome_ratio: 0.93,
            outside_exome_ratio: 0.08,
          }),
        ],
      },
      {
        sample_id: 'FATHER1',
        status: 'insufficient_data',
        pcs: null,
        call_rate: 0,
        qc: { called: 900 },
        callset: 'glimpse2',
        kind: 'partial',
        usable_markers: 900,
        reason: partialReason,
        candidates: [
          scan('glimpse2', 'Imputed genotypes', 'partial', {
            carriers: 1750,
            coverage_ratio: 0.44,
            covered_markers: 0,
            usable_markers: 900,
            call_rate: 0,
            projectable: false,
            reason: partialReason,
          }),
        ],
      },
      {
        sample_id: 'EMB1',
        status: 'excluded',
        pcs: null,
        call_rate: 0,
        qc: {},
        callset: null,
        kind: null,
        usable_markers: null,
        reason: 'An embryo is left out: its genotypes come from a few amplified cells and are its parents’ alleles.',
        candidates: [],
      },
    ],
    calculation_version: 'coga-ancestry-pca-v2',
    assumptions: {
      genotypes: 'the family’s stored callsets at the reference markers',
      min_coverage_ratio: 0.75,
      exome_mask: 'CDS of protein-coding transcripts, GENCODE v50 basic annotation, ±20 bp',
      exome_mask_sha256: '',
      min_call_rate: 0.95,
    },
    created_at: '2026-01-01T12:01:00Z',
  },
};
const renderPanel = (familyId = 'F1') =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <FamilyAncestryPanel familyId={familyId} projectId="project1" enabled />
      </MemoryRouter>
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
        name: /not for clinical use\): 2 family individual/,
      })
    ).toBeInTheDocument();
    // The section and the plot carry the mark, so a screenshot or a print of it does too.
    expect(screen.getByRole('note')).toHaveTextContent('Exploratory — not for clinical use.');
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
    const table = screen.getByRole('table', { name: /Projections and marker quality/ });
    const proband = within(within(table).getByRole('row', { name: /PROBAND/ }));
    expect(proband.getByText('Projected')).toBeInTheDocument();
    // Which callset, read how, and how much of a sequenced genome it covers.
    expect(proband.getByText('Direct calls · genome')).toBeInTheDocument();
    expect(proband.getByText('96%')).toBeInTheDocument();
    expect(proband.getByText('9,900 of 10,000 (99.0%)')).toBeInTheDocument();
    expect(proband.getByText('no call: 100')).toBeInTheDocument();
    // Each coordinate with its standard error, and an ellipse of two around the point.
    expect(proband.getByText('± 0.050')).toBeInTheDocument();
    expect(container.querySelector('[data-uncertainty="PROBAND"]')).toBeInTheDocument();
    expect(container.querySelector('[data-uncertainty="MOTHER"]')).not.toBeInTheDocument();
    const mother = within(within(table).getByRole('row', { name: /MOTHER/ }));
    expect(mother.getByText('Direct calls · exome')).toBeInTheDocument();
    expect(mother.getByText('93% in the exome')).toBeInTheDocument();
    expect(mother.getByText('outside what the callset covers: 1,100')).toBeInTheDocument();
    const father = within(within(table).getByRole('row', { name: /FATHER1/ }));
    expect(father.getByText('Not plotted')).toBeInTheDocument();
    expect(father.getByText('Imputed genotypes · part of the markers')).toBeInTheDocument();
    expect(father.getByText(partialReason)).toBeInTheDocument();
    // A callset that covers none of the markers has its calls only.
    expect(father.getByText('900 called')).toBeInTheDocument();
    const embryo = within(within(table).getByRole('row', { name: /EMB1/ }));
    expect(embryo.getByText('Left out')).toBeInTheDocument();
    expect(embryo.getByText(/An embryo is left out/)).toBeInTheDocument();
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
  it('lists every callset of each individual with how it was read and whether it was used', async () => {
    renderPanel();
    await screen.findByRole('img');
    const table = screen.getByRole('table', { name: /stored callsets at the reference markers/ });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((row) => row.firstChild?.textContent)).toEqual(['PROBAND', 'MOTHER', 'FATHER1']);
    const father = within(rows[2]);
    expect(father.getByText('1,750 (44%)')).toBeInTheDocument();
    expect(father.getByText('Not usable')).toBeInTheDocument();
    const mother = within(rows[1]);
    expect(mother.getByText('8.0%')).toBeInTheDocument();
    expect(mother.getByText('8,200 of 8,398 (97.6%)')).toBeInTheDocument();
    expect(mother.getByText('Projected')).toBeInTheDocument();
    expect(screen.getByText(/carries the ALT allele at about 3,960 of the markers/)).toBeInTheDocument();
  });
  it('lets a user choose how an individual is read, with a panel or a BED file, and saves it', async () => {
    const panels = [{ _id: 'panel-1', name: 'Synthetic panel' }];
    api.get.mockImplementation((url: string) =>
      Promise.resolve({
        data:
          url === '/panels'
            ? panels
            : {
                ...data,
                can_choose: true,
                choices: [
                  {
                    sample_id: 'MOTHER',
                    mode: 'exclude',
                    callset: null,
                    regions: null,
                    chosen_at: '2026-01-02T12:00:00Z',
                  },
                ],
              },
      })
    );
    api.put.mockResolvedValue({ data: {} });
    renderPanel();
    await screen.findByRole('img');
    const table = screen.getByRole('table', { name: 'Choices per individual' });
    // Every individual with a callset to choose.
    expect(within(table).getAllByRole('row').slice(1).map((row) => row.firstChild?.textContent)).toEqual([
      'PROBAND',
      'MOTHER',
      'FATHER1',
    ]);
    expect(within(within(table).getByRole('row', { name: /MOTHER/ })).getByText('Left out')).toBeInTheDocument();
    const father = within(within(table).getByRole('row', { name: /FATHER1/ }));
    const regions = father.getByLabelText("Regions FATHER1's callset covers");
    expect(regions).toBeDisabled();
    fireEvent.change(father.getByLabelText('How FATHER1 is read'), { target: { value: 'callset:glimpse2' } });
    await waitFor(() => expect(father.getByRole('option', { name: 'Gene panel: Synthetic panel' })).toBeInTheDocument());
    fireEvent.change(regions, { target: { value: 'panel:panel-1' } });
    fireEvent.click(father.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/families/F1/ancestry/choices/FATHER1',
        { mode: 'callset', callset: 'glimpse2', panel_id: 'panel-1', keep_regions: false },
        { params: { project_id: 'project1' } }
      )
    );
    // A BED file is sent as a form with the callset, not kept by the page.
    fireEvent.change(regions, { target: { value: 'bed' } });
    expect(father.getByRole('button', { name: 'Save' })).toBeDisabled();
    const file = new File(['chr1\t0\t10\n'], 'capture.bed', { type: 'text/plain' });
    fireEvent.change(father.getByLabelText('BED file for FATHER1'), { target: { files: [file] } });
    fireEvent.click(father.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(2));
    const [url, form] = api.put.mock.calls[1];
    expect(url).toBe('/families/F1/ancestry/choices/FATHER1/bed');
    expect((form as FormData).get('callset')).toBe('glimpse2');
    expect(((form as FormData).get('file') as File).name).toBe('capture.bed');
    // Leaving an individual out.
    const proband = within(within(table).getByRole('row', { name: /PROBAND/ }));
    fireEvent.change(proband.getByLabelText('How PROBAND is read'), { target: { value: 'exclude' } });
    fireEvent.click(proband.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.put).toHaveBeenLastCalledWith(
        '/families/F1/ancestry/choices/PROBAND',
        { mode: 'exclude', callset: null, panel_id: null, keep_regions: false },
        { params: { project_id: 'project1' } }
      )
    );
  });
  it('lists the declared VCF files and lets a user read one', async () => {
    const fileCallset = 'file:0f8fad5b-d9cb-469f-a165-70867728950e';
    const withFile = {
      ...data,
      can_choose: true,
      choices: [],
      files: [
        {
          id: '0f8fad5b-d9cb-469f-a165-70867728950e',
          label: 'Father gVCF',
          relative_path: 'ancestry/FATHER1.g.vcf.gz',
          declared_at: '2026-01-01T12:00:00Z',
          status: 'scanned',
          sha256: 'ab'.repeat(32),
          scanned_at: '2026-01-01T12:05:00Z',
          columns: ['FATHER1'],
        },
        {
          id: '1f8fad5b-d9cb-469f-a165-70867728950e',
          label: 'Old gVCF',
          relative_path: 'old.g.vcf.gz',
          declared_at: '2026-01-01T12:00:00Z',
          status: 'failed',
          error: 'The VCF file could not be read.',
          columns: [],
        },
      ],
      analysis: {
        ...data.analysis,
        samples: data.analysis.samples.map((sample) =>
          sample.sample_id === 'FATHER1'
            ? {
                ...sample,
                candidates: [
                  ...sample.candidates,
                  scan(fileCallset, 'VCF file: Father gVCF', 'complete'),
                ],
              }
            : sample
        ),
      },
    };
    api.get.mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/panels' ? [] : withFile })
    );
    api.put.mockResolvedValue({ data: {} });
    renderPanel();
    await screen.findByRole('img');
    expect(screen.getByText('Father gVCF')).toBeInTheDocument();
    expect(screen.getByText(/ancestry\/FATHER1\.g\.vcf\.gz/).closest('li')).toHaveTextContent(`SHA-256 ${'ab'.repeat(32)}`);
    expect(screen.getByText(/not read: The VCF file could not be read\./)).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'Choices per individual' });
    const father = within(within(table).getByRole('row', { name: /FATHER1/ }));
    fireEvent.change(father.getByLabelText('How FATHER1 is read'), { target: { value: `callset:${fileCallset}` } });
    fireEvent.click(father.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/families/F1/ancestry/choices/FATHER1',
        { mode: 'callset', callset: fileCallset, panel_id: null, keep_regions: false },
        { params: { project_id: 'project1' } }
      )
    );
  });
  it('offers no choice to a reader', async () => {
    renderPanel();
    await screen.findByRole('img');
    expect(screen.queryByRole('table', { name: 'Choices per individual' })).not.toBeInTheDocument();
  });
  it('names the policies of the projection shown, in words', async () => {
    renderPanel();
    await screen.findByRole('img');
    expect(
      screen.getByText('Minimum coverage ratio').nextSibling
    ).toHaveTextContent('0.75');
    expect(screen.getByText('Exome mask').nextSibling).toHaveTextContent(
      'CDS of protein-coding transcripts'
    );
    // An empty value is not listed.
    expect(screen.queryByText('exome_mask_sha256')).not.toBeInTheDocument();
    expect(screen.getByText(/current genotypes · calculation/)).toBeInTheDocument();
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
        state: 'import_in_progress',
        notices: [
          'An import of this family is running; ancestry PCA is calculated once it ends.',
        ],
      },
    });
    const { container } = renderPanel();
    expect(
      await screen.findByText(/No PCA projection has been calculated/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/An import of this family is running/)
    ).toBeInTheDocument();
    expect(screen.getByText(/calculated automatically once its genotypes are imported/)).toBeInTheDocument();
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
      error: 'Genotypes were written during calculation; no results were published.',
    };
    api.get.mockResolvedValue({ data: { ...data, runs: [failed, run] } });
    renderPanel();
    expect(
      await screen.findByText(/: failed — Genotypes were written during calculation/)
    ).toHaveTextContent(new Date(failed.requested_at).toLocaleString());
    expect(screen.getByText(/Automatic calculation/)).toBeInTheDocument();
  });
  it('keeps earlier axes and projections identifiable when the genotypes or the reference change', async () => {
    api.get.mockResolvedValue({
      data: {
        ...data,
        analysis: {
          ...data.analysis,
          current_genotypes: false,
          current_reference: false,
        },
      },
    });
    renderPanel();
    expect(await screen.findByText(/Earlier projection:/)).toHaveTextContent(
      'the family’s genotypes changed since it was calculated'
    );
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
