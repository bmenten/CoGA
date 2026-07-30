import React from 'react';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import type { ApiAnnotationManifest, ApiAnnotationModule } from '../../lib/apiTypes';

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
    title: 'Caller models',
    keys: [
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

/**
 * The workflow itself comes first: which pipeline, at which version, on which engine is
 * the single most important line of the record, and it was previously buried among the
 * tools in an alphabetical footer.
 */
const WORKFLOW_KEYS = ['nf-core/lrsvar', 'nextflow'];

const isWorkflowModule = (module: ApiAnnotationModule): boolean =>
  WORKFLOW_KEYS.includes(module.key) ||
  module.detail?.toLowerCase() === 'workflow' ||
  module.key.startsWith('nf-core/');

/**
 * Pipeline parameters that name the tool used for a job.
 *
 * These used to be rendered as their own "Callers" rows — `Small variants: clair3` —
 * while a chip footer listed `clair3 1.1.2` a screen further down. Same tool, named
 * twice, with the version only in the place you had to scroll to. The role is folded
 * into the versions table instead, so each tool appears once, with what it did and
 * what version it was.
 */
const CALLER_ROLES: [string, string][] = [
  ['snv_caller', 'Small variants'],
  ['sv_caller', 'Structural variants'],
  ['cnv_caller', 'CNV'],
  ['mito_caller', 'Mitochondrial'],
];

const normalizeToolKey = (value: string): string => value.trim().toLowerCase();

interface PipelineSettingsPanelProps {
  /** Family whose recorded tool versions to show alongside the run parameters. */
  familyId?: string;
  settings: PipelineSettings | undefined;
  /**
   * Both variants render the same full-width section; only the lead sentence differs,
   * because the report can point at its own "Modules & versions" footer while the
   * workspace has to say where versions live instead.
   */
  variant?: 'workspace' | 'report';
}

const PipelineSettingsPanel: React.FC<PipelineSettingsPanelProps> = ({
  familyId,
  settings,
  variant = 'workspace',
}) => {
  // Tool versions live in the family's annotation manifest, separately from the run
  // parameters — a run is only traceable with both, so show them together.
  const { data: manifest } = useQuery<ApiAnnotationManifest>({
    queryKey: ['family', familyId, 'annotation-manifest'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(`/families/${familyId}/annotation-manifest`)).data as ApiAnnotationManifest,
  });

  const versioned = (manifest?.modules ?? []).filter((module) => module.version);
  const workflowModules = versioned.filter(isWorkflowModule);

  if (!settings && !versioned.length) return null;

  const resolved = settings ?? {};
  const groups = PARAMETER_GROUPS.map((group) => ({
    title: group.title,
    rows: group.keys
      .map(([key, label]) => ({ label, value: scalarText(resolved[key]) }))
      .filter((row): row is { label: string; value: string } => row.value !== null),
  })).filter((group) => group.rows.length > 0);

  const stages = STAGE_LABELS.filter(([key]) => resolved[key] === true).map(([, label]) => label);

  // Anything the pipeline reported that this component does not know about. Shown
  // rather than dropped: a parameter added upstream still belongs on the record.
  const known = new Set([
    ...PARAMETER_GROUPS.flatMap((group) => group.keys.map(([key]) => key)),
    ...STAGE_LABELS.map(([key]) => key),
  ]);
  const otherRows = Object.entries(resolved)
    .filter(([key]) => !known.has(key))
    .map(([key, value]) => ({ label: key, value: scalarText(value) }))
    .filter((row): row is { label: string; value: string } => row.value !== null)
    .sort((left, right) => left.label.localeCompare(right.label));
  if (otherRows.length) {
    groups.push({ title: 'Other', rows: otherRows });
  }

  if (!groups.length && !stages.length && !versioned.length) return null;

  // Which job each tool did, keyed by the tool name the pipeline recorded.
  const roleByTool = new Map<string, string>();
  for (const [key, role] of CALLER_ROLES) {
    const named = scalarText(resolved[key]);
    if (named) roleByTool.set(normalizeToolKey(named), role);
  }

  // One row per tool: the versioned modules, plus any caller the parameters named
  // that reported no version — dropping those would silently shorten the record of
  // what produced these calls.
  const toolRows: {
    key: string;
    label: string;
    version: string;
    role?: string;
    detail?: string | null;
  }[] = versioned
    .filter((module) => !isWorkflowModule(module))
    .map((module) => ({
      key: module.key,
      label: module.label,
      version: module.version as string,
      role: roleByTool.get(normalizeToolKey(module.key)) ?? roleByTool.get(normalizeToolKey(module.label)),
      detail: module.detail,
    }));
  const namedTools = new Set(toolRows.flatMap((row) => [normalizeToolKey(row.key), normalizeToolKey(row.label)]));
  for (const [key, role] of CALLER_ROLES) {
    const named = scalarText(resolved[key]);
    if (named && !namedTools.has(normalizeToolKey(named))) {
      toolRows.push({ key: `param:${key}`, label: named, version: '', role });
    }
  }
  toolRows.sort((left, right) => {
    // Callers first — they produced the variants being read — then the rest by name.
    if (Boolean(left.role) !== Boolean(right.role)) return left.role ? -1 : 1;
    return left.label.localeCompare(right.label);
  });

  const body = (
    <>
      {(workflowModules.length > 0 || toolRows.length > 0) && (
        <div className="pipeline-settings-versions" data-testid="pipeline-workflow">
          {workflowModules.length > 0 && (
            <p className="pipeline-settings-workflow">
              {workflowModules.map((module, index) => (
                <span key={module.key}>
                  {index > 0 ? ' on ' : ''}
                  <strong>{module.label}</strong> {module.version}
                </span>
              ))}
            </p>
          )}
          {toolRows.length > 0 && (
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table pipeline-tools-table" data-testid="pipeline-tools">
                <thead>
                  <tr>
                    <th>Tool / database</th>
                    <th>Version</th>
                    <th>Used for</th>
                  </tr>
                </thead>
                <tbody>
                  {toolRows.map((row) => (
                    <tr key={row.key}>
                      <td title={row.detail ? `reported by ${row.detail}` : undefined}>{row.label}</td>
                      {/* A tool the pipeline named but reported no version for is shown
                          as such, rather than omitted or left looking versioned. */}
                      <td>{row.version || <span className="table-subtle">not reported</span>}</td>
                      <td className="table-subtle">{row.role ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
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
          {stages.join(' \u00b7 ')}
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
          ? 'The upstream pipeline configuration and every tool version these calls were produced with, as recorded at import.'
          : "How this family's data was produced: the run configuration and the version of every tool and database behind it, as recorded at import."}
      </p>
      {body}
    </section>
  );
};

export default PipelineSettingsPanel;
