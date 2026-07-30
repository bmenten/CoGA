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
    expect(screen.getByText('Small variants')).toBeInTheDocument();
    expect(screen.getByText('Repeat catalogue')).toBeInTheDocument();
    expect(screen.getByText('hificnv')).toBeInTheDocument();
    // deepvariant called both the nuclear small variants and chrM, so it appears twice.
    expect(screen.getAllByText('deepvariant')).toHaveLength(2);
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

    expect(screen.getByText('Other')).toBeInTheDocument();
    expect(screen.getByText('future_option')).toBeInTheDocument();
    expect(screen.getByText('enabled')).toBeInTheDocument();
  });

  it('renders as a report section when asked', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} variant="report" />);

    expect(
      screen.getByRole('heading', { name: /analysis pipeline settings/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/every tool version these calls were produced with/i)).toBeInTheDocument();
  });

  it('renders as a workspace section by default', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} />);

    expect(
      screen.getByRole('heading', { name: /analysis pipeline settings/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/the run configuration and the version of every tool/i),
    ).toBeInTheDocument();
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
    const workflow = await screen.findByTestId('pipeline-workflow');
    expect(workflow).toHaveTextContent('nf-core/lrsvar');
    expect(workflow).toHaveTextContent('v1.0.0dev');
    expect(workflow).toHaveTextContent('26.04.3');

    const tools = screen.getByTestId('pipeline-tools');
    expect(tools).toHaveTextContent('minimap2');
    expect(tools).toHaveTextContent('samtools');
    // A module with no version is not a traceability record; it is left out of the count.
    expect(tools).toHaveTextContent('(2)');
    expect(tools).not.toHaveTextContent('unversioned');
  });
});
