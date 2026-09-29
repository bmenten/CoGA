import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AnnotationProvenanceSummary from '../AnnotationProvenanceSummary';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

function renderSummary(modality?: string) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AnnotationProvenanceSummary familyId="FAM1" modality={modality} />
    </QueryClientProvider>,
  );
}

const MODULE = (
  key: string,
  label: string,
  version: string,
  detail: string | null = null,
  by_modality: Record<string, string> | null = null,
  layer = 'pipeline',
) => ({ key, label, version, detail, layer, by_modality });

describe('AnnotationProvenanceSummary', () => {
  it('shows tool/db versions, the source, and a working show-more toggle', async () => {
    apiMock.get.mockResolvedValue({
      data: {
        family_id: 'FAM1',
        assembly: 'GRCh38',
        source: 'vcf_header',
        recorded_at: null,
        recorded_by: null,
        modules: [
          MODULE('vep', 'VEP', '110', '110_GRCh38'),
          MODULE('gnomad', 'gnomAD', 'r2.1.1'),
          MODULE('clinvar', 'ClinVar', '202301'),
          MODULE('deepvariant', 'DeepVariant', '1.6.0', 'snv caller'),
          MODULE('sniffles', 'Sniffles', '2.2', 'sv caller'),
          MODULE('trgt', 'TRGT', '0.7.0', 'repeats caller'),
          MODULE('dbnsfp', 'dbNSFP', '4.3a'),
          MODULE('spliceai', 'SpliceAI', '1.3'),
        ],
      },
    });

    renderSummary();

    await waitFor(() => expect(screen.getByText('110')).toBeInTheDocument());
    // Versions from across modalities are present (SNV caller + SV caller + repeats).
    expect(screen.getByText('1.6.0')).toBeInTheDocument();
    expect(screen.getByText('2.2')).toBeInTheDocument();
    expect(screen.getByText('from VCF headers')).toBeInTheDocument();

    // 8 modules collapse to 6; the last two are hidden until expanded.
    expect(screen.queryByText('1.3')).toBeNull();
    fireEvent.click(screen.getByText('+2 more'));
    expect(screen.getByText('1.3')).toBeInTheDocument();
  });

  it('renders nothing when no versioned modules are recorded', async () => {
    apiMock.get.mockResolvedValue({
      data: {
        family_id: 'FAM1',
        assembly: null,
        source: null,
        recorded_at: null,
        recorded_by: null,
        modules: [],
      },
    });

    const { container } = renderSummary();
    await waitFor(() => expect(apiMock.get).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="annotation-provenance"]')).toBeNull();
  });

  it('shows the per-modality version and hides other modalities (#294)', async () => {
    apiMock.get.mockResolvedValue({
      data: {
        family_id: 'FAM1',
        assembly: 'GRCh38',
        source: 'vcf_header',
        recorded_at: null,
        recorded_by: null,
        modules: [
          MODULE('assembly', 'Reference assembly', 'GRCh38.p14', null, null, 'reference'),
          MODULE('gencode', 'GENCODE', '45', null, { snv: '49', sv: '45' }),
          MODULE('deepvariant', 'DeepVariant', '1.10.0', null, { snv: '1.10.0' }),
          MODULE('omim', 'OMIM', '20260129', null, { sv: '20260129' }),
        ],
      },
    });

    // SNV page: GENCODE shows 49 (not 45), DeepVariant present, OMIM (SV-only) hidden.
    const { unmount } = renderSummary('snv');
    await waitFor(() => expect(screen.getByText('49')).toBeInTheDocument());
    expect(screen.getByText('DeepVariant')).toBeInTheDocument();
    expect(screen.getByText('Reference assembly')).toBeInTheDocument(); // shared
    expect(screen.queryByText('OMIM')).toBeNull();
    expect(screen.queryByText('45')).toBeNull();
    unmount();

    // SV page: GENCODE shows 45, OMIM present, DeepVariant (SNV-only) hidden.
    renderSummary('sv');
    await waitFor(() => expect(screen.getByText('45')).toBeInTheDocument());
    expect(screen.getByText('OMIM')).toBeInTheDocument();
    expect(screen.queryByText('DeepVariant')).toBeNull();
    expect(screen.queryByText('49')).toBeNull();
  });

  // #610 — a failed manifest made the footer vanish, as for a family with no versions
  // recorded.
  it('says the annotation versions could not be loaded, and retries', async () => {
    apiMock.get.mockRejectedValue(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    renderSummary();

    const footer = await screen.findByTestId('annotation-provenance');
    expect(footer).toHaveTextContent('Annotation versions could not be loaded.');

    apiMock.get.mockResolvedValue({
      data: {
        family_id: 'FAM1',
        assembly: null,
        source: null,
        recorded_at: null,
        recorded_by: null,
        modules: [MODULE('vep', 'VEP', '115')],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByTestId('annotation-provenance')).toHaveTextContent('VEP'));
    expect(screen.getByTestId('annotation-provenance')).not.toHaveTextContent('could not be loaded');
  });
});
