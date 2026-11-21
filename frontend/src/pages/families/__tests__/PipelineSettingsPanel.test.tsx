import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import PipelineSettingsPanel, { PipelineRunsPanels, pipelineRunsFromMetadata } from '../PipelineSettingsPanel';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { analyses: [{ analysis_type: null, modules: [] }] } }) },
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

describe('pipelineRunsFromMetadata', () => {
  it('returns the run record of each analysis, sorted', () => {
    // Keyed as the family's package records are: short-read and then long-read WGS of one family.
    expect(
      pipelineRunsFromMetadata({
        pipeline: { short_read_wgs: { genome: 'GRCh38' }, long_read_wgs: { snv_caller: 'deepvariant' } },
      }),
    ).toEqual([
      ['long_read_wgs', { snv_caller: 'deepvariant' }],
      ['short_read_wgs', { genome: 'GRCh38' }],
    ]);
  });

  it('returns none when the family carries no run record', () => {
    expect(pipelineRunsFromMetadata(undefined)).toEqual([]);
    expect(pipelineRunsFromMetadata({})).toEqual([]);
    // A non-object value must not be handed on as settings.
    expect(pipelineRunsFromMetadata({ pipeline: 'GRCh38' })).toEqual([]);
    expect(pipelineRunsFromMetadata({ pipeline: ['GRCh38'] })).toEqual([]);
    expect(pipelineRunsFromMetadata({ pipeline: { long_read_wgs: 'GRCh38' } })).toEqual([]);
  });
});

describe('PipelineRunsPanels', () => {
  // A family sequenced short-read and then long-read: each run's panel lists the tool versions of
  // its own analysis's annotation manifest, never the other's.
  it("lists each analysis's own tool versions in its panel", async () => {
    const api = (await import('../../../lib/api')).default;
    const tool = (key: string, label: string, version: string) => ({
      key,
      label,
      version,
      detail: null,
      layer: 'pipeline',
      by_modality: null,
    });
    vi.mocked(api.get).mockResolvedValue({
      data: {
        analyses: [
          {
            analysis_type: 'long_read_wgs',
            modules: [tool('minimap2', 'minimap2', '2.30-r1287'), tool('sniffles', 'Sniffles', '2.2'), tool('xz', 'xz', '5.8.3')],
          },
          { analysis_type: 'short_read_wgs', modules: [tool('gatk', 'GATK', '4.5.0.0'), tool('gzip', 'gzip', '1.14')] },
        ],
      },
    });

    render(
      <PipelineRunsPanels
        familyId="FAM1"
        metadata={{ pipeline: { short_read_wgs: { genome: 'GRCh38' }, long_read_wgs: PACBIO_SETTINGS } }}
        variant="report"
      />,
    );

    await screen.findByText('2.30-r1287');
    const [longRead, shortRead] = screen.getAllByTestId('pipeline-settings');
    expect(longRead).toHaveTextContent('minimap2');
    expect(longRead).toHaveTextContent('xz 5.8.3');
    expect(longRead).not.toHaveTextContent('GATK');
    expect(shortRead).toHaveTextContent('GATK');
    expect(shortRead).toHaveTextContent('4.5.0.0');
    expect(shortRead).toHaveTextContent('gzip 1.14');
    expect(shortRead).not.toHaveTextContent('minimap2');
    expect(shortRead).not.toHaveTextContent('Sniffles');
    vi.mocked(api.get).mockResolvedValue({ data: { analyses: [{ analysis_type: null, modules: [] }] } });
  });

  it('shows a panel per analysis, named, when the family holds the run record of several', async () => {
    render(
      <PipelineRunsPanels
        metadata={{ pipeline: { short_read_wgs: { genome: 'GRCh38' }, long_read_wgs: PACBIO_SETTINGS } }}
        variant="report"
      />,
    );
    expect(
      (await screen.findAllByRole('heading')).map((heading) => heading.textContent),
    ).toEqual(['Analysis pipeline settings — long-read WGS', 'Analysis pipeline settings — short-read WGS']);
  });

  it('shows the tool versions of an analysis whose package carried no run record', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockResolvedValue({
      data: {
        analyses: [
          { analysis_type: 'long_read_wgs', modules: [] },
          {
            analysis_type: 'short_read_wgs',
            modules: [{ key: 'gatk', label: 'GATK', version: '4.5.0.0', detail: null, layer: 'pipeline', by_modality: null }],
          },
        ],
      },
    });

    render(
      <PipelineRunsPanels
        familyId="FAM1"
        metadata={{ analyses: ['long_read_wgs', 'short_read_wgs'], pipeline: { long_read_wgs: PACBIO_SETTINGS } }}
        variant="report"
      />,
    );

    await screen.findByText('4.5.0.0');
    expect((await screen.findAllByRole('heading')).map((heading) => heading.textContent)).toEqual([
      'Analysis pipeline settings — long-read WGS',
      'Analysis pipeline settings — short-read WGS',
    ]);
    vi.mocked(api.get).mockResolvedValue({ data: { analyses: [{ analysis_type: null, modules: [] }] } });
  });

  it('shows one unnamed panel for a family of one run record', async () => {
    render(<PipelineRunsPanels metadata={{ pipeline: { long_read_wgs: PACBIO_SETTINGS } }} variant="report" />);
    expect((await screen.findAllByRole('heading')).map((heading) => heading.textContent)).toEqual([
      'Analysis pipeline settings',
    ]);
  });

  it('names the one run record of a family that holds several analyses', async () => {
    // The short-read package carried no run record: the long-read one is not the family's whole.
    render(
      <PipelineRunsPanels
        metadata={{ analyses: ['long_read_wgs', 'short_read_wgs'], pipeline: { long_read_wgs: PACBIO_SETTINGS } }}
        variant="workspace"
      />,
    );
    expect((await screen.findAllByRole('heading')).map((heading) => heading.textContent)).toEqual([
      'Analysis pipeline settings — long-read WGS',
    ]);
  });

  it('shows on the report the run record of the analysis it signs out alone', async () => {
    const metadata = {
      analyses: ['long_read_wgs', 'short_read_wgs'],
      pipeline: { short_read_wgs: { genome: 'GRCh38' }, long_read_wgs: PACBIO_SETTINGS },
    };
    const { unmount } = render(<PipelineRunsPanels metadata={metadata} variant="report" analysis="short_read_wgs" />);
    expect((await screen.findAllByRole('heading')).map((heading) => heading.textContent)).toEqual([
      'Analysis pipeline settings — short-read WGS',
    ]);
    expect(screen.queryByText('sniffles')).not.toBeInTheDocument();
    unmount();

    // An analysis whose package carried none: its panel is named, and shows no other analysis's
    // run record.
    render(
      <PipelineRunsPanels
        metadata={{ analyses: ['long_read_wgs', 'short_read_wgs'], pipeline: { long_read_wgs: PACBIO_SETTINGS } }}
        variant="report"
        analysis="short_read_wgs"
      />,
    );
    expect(screen.queryByText('sniffles')).not.toBeInTheDocument();
    expect(screen.queryByText('Analysis pipeline settings — long-read WGS')).not.toBeInTheDocument();
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

  it("groups a PGT run's parameters under the job they configured", () => {
    // The shape the PGT pipeline (nf-cmgg/copgtm) records from its params_*.json.
    render(
      <PipelineSettingsPanel
        settings={{
          fasta: 'GRCh38_analysis_set.fna',
          affected_parent: 'MOTHER1',
          roi: 'chr7:1000-2000',
          bin_size: 500,
          apcad_imputation: true,
          cohort_vcf: 'joint_germline.vcf.gz',
          shapeit_reference_panel: 'panel_samplesheet.csv',
        }}
      />,
    );

    const groups = screen.getByTestId('pipeline-tools');
    expect(groups).toHaveTextContent('PGT analysis');
    expect(groups).toHaveTextContent('Affected parent');
    expect(groups).toHaveTextContent('MOTHER1');
    expect(groups).toHaveTextContent('Region of interest');
    expect(groups).toHaveTextContent('chr7:1000-2000');
    expect(groups).toHaveTextContent('Bin size (kb)');
    expect(groups).toHaveTextContent('Phasing reference panel');
    expect(groups).toHaveTextContent('Joint callset');
    // Every parameter has its place: nothing is left for "Other settings".
    expect(screen.queryByText('Other settings')).not.toBeInTheDocument();
    // A switch is a stage that ran, not a "true" row.
    expect(screen.getByTestId('pipeline-stages')).toHaveTextContent('APCAD on the imputed parents');
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
        analyses: [
          {
            analysis_type: null,
            modules: [
              { key: 'nf-core/lrsvar', label: 'nf-core/lrsvar', version: 'v1.0.0dev', detail: 'Workflow', layer: 'pipeline', by_modality: null },
              { key: 'nextflow', label: 'Nextflow', version: '26.04.3', detail: 'Workflow', layer: 'pipeline', by_modality: null },
              { key: 'minimap2', label: 'minimap2', version: '2.30-r1287', detail: 'MINIMAP2_ALIGN', layer: 'pipeline', by_modality: null },
              { key: 'samtools', label: 'samtools', version: '1.23.1', detail: 'BAM_SORT', layer: 'pipeline', by_modality: null },
              { key: 'unversioned', label: 'unversioned', version: null, detail: null, layer: 'pipeline', by_modality: null },
            ],
          },
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
        analyses: [
          {
            analysis_type: null,
            modules: [
              { key: 'assembly', label: 'assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference', by_modality: null },
            ],
          },
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
        analyses: [
          {
            analysis_type: null,
            modules: [
              { key: 'htslib', label: 'htslib', version: '1.23.1', detail: null, layer: 'pipeline', by_modality: null },
              { key: 'gzip', label: 'gzip', version: '1.14', detail: null, layer: 'pipeline', by_modality: null },
              { key: 'xz', label: 'xz', version: '5.8.3', detail: null, layer: 'pipeline', by_modality: null },
            ],
          },
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
    vi.mocked(api.get).mockResolvedValue({ data: { analyses: [{ analysis_type: null, modules: [] }] } });

    render(<PipelineSettingsPanel familyId="pacbio" settings={PACBIO_SETTINGS} />);

    // Dropping it would silently shorten the record of what produced these calls.
    const tools = await screen.findByTestId('pipeline-tools');
    expect(tools).toHaveTextContent('hificnv');
    expect(tools).toHaveTextContent('not reported');
  });

  // A failed manifest never reads as a run whose tools reported no version, on a panel
  // that is printed on the report.
  it('says the tool versions could not be loaded, not that none were reported', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockRejectedValue(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    render(<PipelineSettingsPanel familyId="pacbio" settings={PACBIO_SETTINGS} variant="report" />);

    expect(await screen.findByTestId('pipeline-versions-failed')).toHaveTextContent(
      'The tool versions could not be loaded, so only the run parameters are shown.',
    );
    const tools = screen.getByTestId('pipeline-tools');
    expect(tools).toHaveTextContent('hificnv');
    expect(tools).toHaveTextContent('version could not be loaded');
    expect(tools).not.toHaveTextContent('not reported');

    vi.mocked(api.get).mockResolvedValue({ data: { analyses: [{ analysis_type: null, modules: [] }] } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByTestId('pipeline-versions-failed')).not.toBeInTheDocument());
    expect(screen.getByTestId('pipeline-tools')).toHaveTextContent('not reported');
  });

  it('says the tool versions could not be loaded for a family without a run record', async () => {
    const api = (await import('../../../lib/api')).default;
    vi.mocked(api.get).mockRejectedValue(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    render(<PipelineSettingsPanel familyId="pacbio" settings={undefined} />);

    expect(await screen.findByTestId('pipeline-versions-failed')).toBeInTheDocument();
  });
});
