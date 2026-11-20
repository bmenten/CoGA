import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AnnotationProvenanceSummary, { analysisManifest } from '../AnnotationProvenanceSummary';
import type { ApiAnnotationModule } from '../../../lib/apiTypes';
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
): ApiAnnotationModule => ({ key, label, version, detail, layer, by_modality });

/** One analysis's manifest (`analysis_type` null: a family that holds none). */
const ANALYSIS = (
  analysis_type: string | null,
  modules: ApiAnnotationModule[],
  source: string | null = 'vcf_header',
) => ({ analysis_type, source, recorded_at: null, recorded_by: null, modules });

/** The family's manifests, as GET /families/{id}/annotation-manifest serves them. */
const serve = (...analyses: ReturnType<typeof ANALYSIS>[]) =>
  apiMock.get.mockResolvedValue({ data: { family_id: 'FAM1', assembly: 'GRCh38', analyses } });

describe('AnnotationProvenanceSummary', () => {
  it('shows tool/db versions, the source, and a working show-more toggle', async () => {
    serve(
      ANALYSIS(null, [
        MODULE('vep', 'VEP', '110', '110_GRCh38'),
        MODULE('gnomad', 'gnomAD', 'r2.1.1'),
        MODULE('clinvar', 'ClinVar', '202301'),
        MODULE('deepvariant', 'DeepVariant', '1.6.0', 'snv caller'),
        MODULE('sniffles', 'Sniffles', '2.2', 'sv caller'),
        MODULE('trgt', 'TRGT', '0.7.0', 'repeats caller'),
        MODULE('dbnsfp', 'dbNSFP', '4.3a'),
        MODULE('spliceai', 'SpliceAI', '1.3'),
      ]),
    );

    renderSummary();

    await waitFor(() => expect(screen.getByText('110')).toBeInTheDocument());
    // Versions from across modalities are present (SNV caller + SV caller + repeats).
    expect(screen.getByText('1.6.0')).toBeInTheDocument();
    expect(screen.getByText('2.2')).toBeInTheDocument();
    expect(screen.getByText('from VCF headers')).toBeInTheDocument();
    // One analysis (or none): the footer names none.
    expect(screen.getByText('Annotation versions')).toBeInTheDocument();

    // 8 modules collapse to 6; the last two are hidden until expanded.
    expect(screen.queryByText('1.3')).toBeNull();
    fireEvent.click(screen.getByText('+2 more'));
    expect(screen.getByText('1.3')).toBeInTheDocument();
  });

  it('renders nothing when no versioned modules are recorded', async () => {
    serve(ANALYSIS(null, [], null));

    const { container } = renderSummary();
    await waitFor(() => expect(apiMock.get).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="annotation-provenance"]')).toBeNull();
  });

  it('shows the per-modality version and hides other modalities', async () => {
    serve(
      ANALYSIS(null, [
        MODULE('assembly', 'Reference assembly', 'GRCh38.p14', null, null, 'reference'),
        MODULE('gencode', 'GENCODE', '45', null, { snv: '49', sv: '45' }),
        MODULE('deepvariant', 'DeepVariant', '1.10.0', null, { snv: '1.10.0' }),
        MODULE('omim', 'OMIM', '20260129', null, { sv: '20260129' }),
      ]),
    );

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

  // A family sequenced short-read and then long-read keeps each analysis's versions: the page
  // shows both analyses' data, each line its own analysis's versions, never one over the other.
  it('shows a line of each analysis’s own versions when the family holds several', async () => {
    serve(
      ANALYSIS('long_read_wgs', [
        MODULE('vep', 'VEP', '112', null, { snv: '112' }),
        MODULE('deepvariant', 'DeepVariant', '1.6.1', null, { snv: '1.6.1' }),
      ], 'manifest'),
      ANALYSIS('short_read_wgs', [
        MODULE('vep', 'VEP', '110', null, { snv: '110' }),
        MODULE('gatk', 'GATK', '4.5.0.0', null, { snv: '4.5.0.0' }),
      ]),
    );

    renderSummary('snv');

    await waitFor(() => expect(screen.getAllByTestId('annotation-provenance')).toHaveLength(2));
    const [longRead, shortRead] = screen.getAllByTestId('annotation-provenance');
    expect(within(longRead).getByText('Annotation versions — long-read WGS')).toBeInTheDocument();
    expect(longRead).toHaveTextContent('VEP 112');
    expect(longRead).toHaveTextContent('DeepVariant 1.6.1');
    expect(longRead).not.toHaveTextContent('GATK');
    expect(longRead).toHaveTextContent('from import manifest');
    expect(within(shortRead).getByText('Annotation versions — short-read WGS')).toBeInTheDocument();
    expect(shortRead).toHaveTextContent('VEP 110');
    expect(shortRead).toHaveTextContent('GATK 4.5.0.0');
    expect(shortRead).not.toHaveTextContent('DeepVariant');
  });

  // A failed manifest is said as such, never as a family with no versions recorded.
  it('says the annotation versions could not be loaded, and retries', async () => {
    apiMock.get.mockRejectedValue(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    renderSummary();

    const footer = await screen.findByTestId('annotation-provenance');
    expect(footer).toHaveTextContent('Annotation versions could not be loaded.');

    serve(ANALYSIS(null, [MODULE('vep', 'VEP', '115')], null));
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByTestId('annotation-provenance')).toHaveTextContent('VEP'));
    expect(screen.getByTestId('annotation-provenance')).not.toHaveTextContent('could not be loaded');
  });
});

describe('analysisManifest', () => {
  const manifest = {
    family_id: 'FAM1',
    assembly: 'GRCh38',
    analyses: [ANALYSIS('long_read_wgs', []), ANALYSIS('short_read_wgs', [])],
  };

  it('gives the manifest of the analysis named', () => {
    expect(analysisManifest(manifest, 'short_read_wgs')?.analysis_type).toBe('short_read_wgs');
    expect(analysisManifest(manifest, 'long_read_wgs')?.analysis_type).toBe('long_read_wgs');
  });

  it('gives none for an analysis of several that is not named, or one the family does not hold', () => {
    expect(analysisManifest(manifest, null)).toBeNull();
    expect(analysisManifest(manifest, 'pgt')).toBeNull();
    expect(analysisManifest(undefined, 'pgt')).toBeNull();
  });

  it('gives the only manifest of a family of one analysis or none', () => {
    const single = { ...manifest, analyses: [ANALYSIS('short_read_wgs', [])] };
    expect(analysisManifest(single, null)?.analysis_type).toBe('short_read_wgs');
    // A run record of a package that named no analysis reads its analysis's manifest.
    expect(analysisManifest(single, 'unspecified')?.analysis_type).toBe('short_read_wgs');
  });
});
