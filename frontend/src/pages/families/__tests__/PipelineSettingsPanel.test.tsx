import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import PipelineSettingsPanel, {
  pipelineSettingsFromMetadata,
} from '../PipelineSettingsPanel';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { modules: [] } }) },
}));

/** The panel fetches the annotation manifest for tool versions, so it needs a client. */
const render = (ui: React.ReactElement) =>
  rtlRender(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

// The shape the long-read import records from the pipeline's params_*.json.
const PACBIO_SETTINGS = {
  genome: 'GRCh38',
  fasta: 'GCA_000001405.15_GRCh38_no_alt_analysis_set.fna',
  snv_caller: 'deepvariant',
  sv_caller: 'sniffles',
  cnv_caller: 'hificnv',
  mito_caller: 'deepvariant',
  vep_cache_version: '115',
  trgt_repeats: 'STRchive-disease-loci.hg38.TRGT.bed',
  kit: 'SQK-NBD114-24',
  snv: true,
  sv: true,
  cnv: true,
  mito: true,
  repeats: true,
  paraphase: true,
  exomiser: true,
  methyl: false,
  assembly: false,
};

describe('pipelineSettingsFromMetadata', () => {
  it('returns the pipeline block from family metadata', () => {
    expect(pipelineSettingsFromMetadata({ pipeline: { genome: 'GRCh38' } })).toEqual({
      genome: 'GRCh38',
    });
  });

  it('returns undefined when the family carries no run record', () => {
    expect(pipelineSettingsFromMetadata(undefined)).toBeUndefined();
    expect(pipelineSettingsFromMetadata({})).toBeUndefined();
    // A non-object value must not be handed on as settings.
    expect(pipelineSettingsFromMetadata({ pipeline: 'GRCh38' })).toBeUndefined();
    expect(pipelineSettingsFromMetadata({ pipeline: ['GRCh38'] })).toBeUndefined();
  });
});

describe('PipelineSettingsPanel', () => {
  it('renders nothing when the family has no pipeline settings', () => {
    const { container } = render(<PipelineSettingsPanel settings={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('groups the settings under readable labels', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} />);

    expect(screen.getByText('Reference')).toBeInTheDocument();
    expect(screen.getByText('Genome build')).toBeInTheDocument();
    expect(screen.getByText('Repeats & paralogues')).toBeInTheDocument();
    expect(screen.getByText('Repeat catalogue')).toBeInTheDocument();
    // Callers appear under the job they did, not as a separate flat list of tools.
    const groups = screen.getByTestId('pipeline-tools');
    expect(groups).toHaveTextContent('Variant calling');
    expect(groups).toHaveTextContent('hificnv');
    expect(groups).toHaveTextContent('CNV');
  });

  it('collapses the boolean stage switches into the list of stages that ran', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} />);

    const stages = screen.getByTestId('pipeline-stages').textContent ?? '';
    expect(stages).toContain('small variants');
    expect(stages).toContain('paraphase');
    // Stages that did not run are omitted rather than shown as "false".
    expect(stages).not.toContain('methylation');
    expect(stages).not.toContain('assembly');
    expect(screen.queryByText('false')).not.toBeInTheDocument();
  });

  it('still shows a parameter it does not have a label for', () => {
    // A setting added upstream belongs on the record even before this UI knows it.
    render(<PipelineSettingsPanel settings={{ genome: 'GRCh38', future_option: 'enabled' }} />);

    expect(screen.getByText('Other settings')).toBeInTheDocument();
    expect(screen.getByText('future_option')).toBeInTheDocument();
    expect(screen.getByText('enabled')).toBeInTheDocument();
  });

  it('stays expanded on the report, where a collapsed section would print blank', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} variant="report" />);

    const section = screen.getByTestId('pipeline-settings');
    expect(section.tagName).toBe('SECTION');
    expect(screen.getByRole('heading', { name: /analysis pipeline settings/i })).toBeInTheDocument();
    expect(screen.getByText('Genome build')).toBeInTheDocument();
  });

  it('collapses on the workspace and starts closed', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} />);

    // Reference material a reader consults occasionally, not something to scroll past
    // on every visit — but the heading stays visible so it can be found.
    const section = screen.getByTestId('pipeline-settings') as HTMLDetailsElement;
    expect(section.tagName).toBe('DETAILS');
    expect(section.open).toBe(false);
    expect(screen.getByRole('heading', { name: /analysis pipeline settings/i })).toBeInTheDocument();
  });

  it('shows the workflow and its engine separately from the tool versions', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockResolvedValue({
      data: {
        modules: [
          { key: 'nf-core/lrsvar', label: 'nf-core/lrsvar', version: 'v1.0.0dev', detail: 'Workflow', layer: 'pipeline', by_modality: null },
          { key: 'nextflow', label: 'Nextflow', version: '26.04.3', detail: 'Workflow', layer: 'pipeline', by_modality: null },
          { key: 'minimap2', label: 'minimap2', version: '2.30-r1287', detail: 'MINIMAP2_ALIGN', layer: 'pipeline', by_modality: null },
          { key: 'samtools', label: 'samtools', version: '1.23.1', detail: 'BAM_SORT', layer: 'pipeline', by_modality: null },
          { key: 'unversioned', label: 'unversioned', version: null, detail: null, layer: 'pipeline', by_modality: null },
        ],
      },
    });

    render(<PipelineSettingsPanel familyId="pacbio" settings={PACBIO_SETTINGS} />);

    // Which pipeline, at which version, on which engine is the headline of the record —
    // it must not be buried among the tools.
    // The section renders straight away from the parameters and fills in when the
    // manifest arrives — so wait for the manifest's content, not for the container.
    await screen.findByText(/nf-core\/lrsvar/);
    const groups = screen.getByTestId('pipeline-tools');
    // Which pipeline, at which version, on which engine is the headline of the record —
    // its own group, first, not a row among 27 tools.
    expect(groups).toHaveTextContent('Workflow');
    expect(groups).toHaveTextContent('v1.0.0dev');
    expect(groups).toHaveTextContent('26.04.3');

    // Alignment tools sit under the job they did, beside the parameters that
    // configured it, rather than alphabetically among everything else.
    expect(groups).toHaveTextContent('Alignment & phasing');
    expect(groups).toHaveTextContent('minimap2');
    expect(groups).toHaveTextContent('2.30-r1287');
    expect(groups).toHaveTextContent('samtools');
    // A module with no version is not a traceability record.
    expect(groups).not.toHaveTextContent('unversioned');
  });

  it('shows the genome build once, not as both a parameter and a module', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockResolvedValue({
      data: {
        modules: [
          { key: 'assembly', label: 'assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference', by_modality: null },
        ],
      },
    });

    render(<PipelineSettingsPanel familyId="pacbio" settings={{ genome: 'GRCh38' }} />);

    await screen.findByText('Genome build');
    // The `assembly` module and the `genome` parameter are the same fact; printing it
    // under one heading twice is the duplication this layout exists to remove.
    expect(screen.getAllByText('GRCh38')).toHaveLength(1);
  });

  it('collapses format libraries onto one line instead of a row each', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockResolvedValue({
      data: {
        modules: [
          { key: 'htslib', label: 'htslib', version: '1.23.1', detail: null, layer: 'pipeline', by_modality: null },
          { key: 'gzip', label: 'gzip', version: '1.14', detail: null, layer: 'pipeline', by_modality: null },
          { key: 'xz', label: 'xz', version: '5.8.3', detail: null, layer: 'pipeline', by_modality: null },
        ],
      },
    });

    render(<PipelineSettingsPanel familyId="pacbio" settings={PACBIO_SETTINGS} />);

    // Real provenance, but a reader looking for which caller made a deletion should
    // not read past six compression libraries to find it.
    const utilities = await screen.findByTestId('pipeline-utilities');
    expect(utilities).toHaveTextContent('gzip 1.14');
    expect(utilities).toHaveTextContent('xz 5.8.3');
    expect(screen.getByTestId('pipeline-tools')).not.toHaveTextContent('gzip');
  });

  it('keeps a caller the pipeline named but reported no version for', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockResolvedValue({ data: { modules: [] } });

    render(<PipelineSettingsPanel familyId="pacbio" settings={PACBIO_SETTINGS} />);

    // Dropping it would silently shorten the record of what produced these calls.
    const tools = await screen.findByTestId('pipeline-tools');
    expect(tools).toHaveTextContent('hificnv');
    expect(tools).toHaveTextContent('not reported');
  });
});
