import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PipelineSettingsPanel, {
  pipelineSettingsFromMetadata,
} from '../PipelineSettingsPanel';

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

  it('renders as a report section when asked, with the versions cross-reference', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} variant="report" />);

    expect(
      screen.getByRole('heading', { name: /analysis pipeline settings/i }),
    ).toBeInTheDocument();
    // Settings and versions are complementary records; the report says where to find both.
    expect(screen.getByText(/Modules & versions/i)).toBeInTheDocument();
  });

  it('points at where versions live when rendered outside the report', () => {
    render(<PipelineSettingsPanel settings={PACBIO_SETTINGS} />);

    expect(
      screen.getByRole('heading', { name: /analysis pipeline settings/i }),
    ).toBeInTheDocument();
    // Off the report there is no "Modules & versions" footer to point at.
    expect(screen.queryByText(/Modules & versions/i)).not.toBeInTheDocument();
    expect(screen.getByText(/annotation-provenance footer/i)).toBeInTheDocument();
  });
});
