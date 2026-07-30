import React from 'react';

/**
 * Settings of the upstream analysis pipeline, recorded per family at package import
 * (`families.metadata.pipeline`, from the run's `params_*.json`).
 *
 * These are the choices behind every call in the family — reference build, which caller
 * produced which variant class, which annotation cache and repeat catalogue. Tool
 * *versions* live in the annotation manifest and are rendered by
 * `AnnotationProvenanceSummary`; this is the complementary "how was it run".
 */

/** Curated label + group per known parameter, in display order. */
const PARAMETER_GROUPS: { title: string; keys: [string, string][] }[] = [
  {
    title: 'Reference',
    keys: [
      ['genome', 'Genome build'],
      ['fasta', 'Reference FASTA'],
    ],
  },
  {
    title: 'Callers',
    keys: [
      ['snv_caller', 'Small variants'],
      ['sv_caller', 'Structural variants'],
      ['cnv_caller', 'CNV'],
      ['mito_caller', 'Mitochondrial'],
      ['clair3_model_pacbio', 'Clair3 model (PacBio)'],
      ['clair3_model_ont', 'Clair3 model (ONT)'],
    ],
  },
  {
    title: 'Annotation',
    keys: [
      ['vep_cache_version', 'VEP cache'],
      ['vep_genome', 'VEP genome'],
      ['vep_species', 'VEP species'],
      ['trgt_repeats', 'Repeat catalogue'],
      ['qdnaseq_bin_size', 'QDNAseq bin size (kb)'],
    ],
  },
  {
    title: 'Run configuration',
    keys: [
      ['kit', 'Library kit'],
      ['conf_dorado', 'Basecalling config'],
    ],
  },
];

/**
 * Boolean parameters are stage switches. Rendering them as "true/false" rows buries the
 * one fact that matters — which stages ran — so they collapse into a single enabled list.
 */
const STAGE_LABELS: [string, string][] = [
  ['align', 'alignment'],
  ['snv', 'small variants'],
  ['sv', 'structural variants'],
  ['cnv', 'CNV'],
  ['mito', 'mitochondrial'],
  ['repeats', 'repeats'],
  ['paraphase', 'paraphase'],
  ['annotate', 'annotation'],
  ['exomiser', 'exomiser'],
  ['assembly', 'assembly'],
  ['methyl', 'methylation'],
  ['dwell', 'dwell times'],
  ['nanocomp', 'nanocomp'],
];

export interface PipelineSettings {
  [key: string]: unknown;
}

/**
 * The pipeline block from a family's metadata, or undefined when the family was not
 * imported from a package that carried a run record.
 */
export const pipelineSettingsFromMetadata = (
  metadata: Record<string, unknown> | undefined,
): PipelineSettings | undefined => {
  const pipeline = metadata?.pipeline;
  return pipeline && typeof pipeline === 'object' && !Array.isArray(pipeline)
    ? (pipeline as PipelineSettings)
    : undefined;
};

const scalarText = (value: unknown): string | null => {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number') return String(value);
  return null;
};

interface PipelineSettingsPanelProps {
  settings: PipelineSettings | undefined;
  /**
   * Both variants render the same full-width section; only the lead sentence differs,
   * because the report can point at its own "Modules & versions" footer while the
   * workspace has to say where versions live instead.
   */
  variant?: 'workspace' | 'report';
}

const PipelineSettingsPanel: React.FC<PipelineSettingsPanelProps> = ({
  settings,
  variant = 'workspace',
}) => {
  if (!settings) return null;

  const groups = PARAMETER_GROUPS.map((group) => ({
    title: group.title,
    rows: group.keys
      .map(([key, label]) => ({ label, value: scalarText(settings[key]) }))
      .filter((row): row is { label: string; value: string } => row.value !== null),
  })).filter((group) => group.rows.length > 0);

  const stages = STAGE_LABELS.filter(([key]) => settings[key] === true).map(([, label]) => label);

  // Anything the pipeline reported that this component does not know about. Shown
  // rather than dropped: a parameter added upstream still belongs on the record.
  const known = new Set([
    ...PARAMETER_GROUPS.flatMap((group) => group.keys.map(([key]) => key)),
    ...STAGE_LABELS.map(([key]) => key),
  ]);
  const otherRows = Object.entries(settings)
    .filter(([key]) => !known.has(key))
    .map(([key, value]) => ({ label: key, value: scalarText(value) }))
    .filter((row): row is { label: string; value: string } => row.value !== null)
    .sort((left, right) => left.label.localeCompare(right.label));
  if (otherRows.length) {
    groups.push({ title: 'Other', rows: otherRows });
  }

  if (!groups.length && !stages.length) return null;

  const body = (
    <>
      <div className="pipeline-settings-grid">
        {groups.map((group) => (
          <div key={group.title} className="pipeline-settings-group">
            <span className="pipeline-settings-group-title">{group.title}</span>
            <dl className="pipeline-settings-list">
              {group.rows.map((row) => (
                <div key={row.label} className="pipeline-settings-row">
                  <dt>{row.label}</dt>
                  <dd title={row.value}>{row.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      {stages.length > 0 && (
        <p className="pipeline-settings-stages" data-testid="pipeline-stages">
          <span className="pipeline-settings-group-title">Stages run</span>{' '}
          {stages.join(' · ')}
        </p>
      )}
    </>
  );

  return (
    <section
      className={`surface-card report-pipeline${
        variant === 'report' ? ' report-pipeline--report' : ''
      }`}
      data-testid="pipeline-settings"
    >
      <h2 className="report-audit-heading">Analysis pipeline settings</h2>
      <p className="report-paragraph report-audit-lead">
        {variant === 'report'
          ? 'The upstream pipeline configuration these calls were produced with, as recorded at import. Tool versions are listed under Modules & versions below.'
          : "How this family's data was produced, as recorded at import. Tool versions are shown separately in the annotation-provenance footer of the variant pages and on the report."}
      </p>
      {body}
    </section>
  );
};

export default PipelineSettingsPanel;
