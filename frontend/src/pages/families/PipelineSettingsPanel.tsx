import React from 'react';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import type { ApiAnnotationManifest, ApiAnnotationModule } from '../../lib/apiTypes';

/**
 * Settings of the upstream analysis pipeline, recorded per family at package import
 * (`families.metadata.pipeline`, from the run's `params_*.json`) together with the tool
 * versions from the family's annotation manifest.
 *
 * The two belong together and are shown together, grouped by the *job* they describe:
 * which reference, which caller at which version produced which variant class, which
 * annotation cache. Listing 27 tools in one flat table said nothing about what any of
 * them did, and put the parameter that configures a tool in a different block from that
 * tool's version.
 */

/**
 * Display groups. Each pulls its own run parameters *and* its own tools, so a tool sits
 * beside the parameter that configured it — `ensemblvep 116.0` next to the VEP cache
 * version, `trgt 5.0.0` next to the repeat catalogue it genotyped against.
 *
 * `tools` are matched on the manifest's module key. Anything versioned that no group
 * claims still appears under `Other tools`: a module added upstream belongs on the
 * record whether or not this file has been taught about it.
 */
interface SettingsGroup {
  title: string;
  /** Run parameters: `[key, label]`. */
  params?: [string, string][];
  /** Manifest module keys, in display order. */
  tools?: string[];
  /** Parameters that name which tool did a job: `[key, role]`. */
  roleParam?: [string, string][];
}

const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    title: 'Workflow',
    tools: ['nf-core/lrsvar', 'nextflow'],
  },
  {
    title: 'Reference',
    params: [
      ['genome', 'Genome build'],
      ['fasta', 'Reference FASTA'],
    ],
    tools: ['assembly'],
  },
  {
    title: 'Alignment & phasing',
    params: [
      ['kit', 'Library kit'],
      ['conf_dorado', 'Basecalling config'],
    ],
    tools: ['minimap2', 'longphase', 'samtools'],
  },
  {
    title: 'Variant calling',
    params: [
      ['clair3_model_pacbio', 'Clair3 model (PacBio)'],
      ['clair3_model_ont', 'Clair3 model (ONT)'],
    ],
    tools: ['deepvariant', 'glnexus', 'sniffles', 'hificnv', 'deepsomatic', 'mutserve'],
    roleParam: [
      ['snv_caller', 'small variants'],
      ['sv_caller', 'structural variants'],
      ['cnv_caller', 'CNV'],
      ['mito_caller', 'mitochondrial'],
    ],
  },
  {
    title: 'Repeats & paralogues',
    params: [['trgt_repeats', 'Repeat catalogue']],
    tools: ['trgt', 'paraphase'],
  },
  {
    title: 'Copy-number binning',
    params: [['qdnaseq_bin_size', 'QDNAseq bin size (kb)']],
    tools: ['qdnaseq', 'wisecondorx'],
  },
  {
    title: 'Annotation',
    params: [
      ['vep_cache_version', 'VEP cache'],
      ['vep_genome', 'VEP genome'],
      ['vep_species', 'VEP species'],
    ],
    tools: ['ensemblvep', 'exomiser', 'exomiser_data', 'cadd', 'remm', 'monarch'],
  },
  {
    title: 'Quality control',
    tools: ['mosdepth', 'nanoplot'],
  },
];

/**
 * File-format and compression libraries. Real provenance, but a reader scanning for
 * "which caller made this deletion" should not read past six of them, so they collapse
 * onto one line instead of taking a row each.
 */
const UTILITY_TOOLS = new Set(['bcftools', 'htslib', 'tabix', 'gzip', 'xz', 'perl-math-cdf']);

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

const normalizeToolKey = (value: string): string => value.trim().toLowerCase();

interface SettingsRow {
  label: string;
  value: string;
  /** What the tool was used for, when the parameters say. */
  note?: string;
}

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
  if (!settings && !versioned.length) return null;

  const resolved = settings ?? {};
  const moduleByKey = new Map<string, ApiAnnotationModule>(
    versioned.map((module) => [normalizeToolKey(module.key), module]),
  );

  // Which job each tool did, from the parameters that name it.
  const roleByTool = new Map<string, string>();
  for (const group of SETTINGS_GROUPS) {
    for (const [key, role] of group.roleParam ?? []) {
      const named = scalarText(resolved[key]);
      if (named) roleByTool.set(normalizeToolKey(named), role);
    }
  }

  const claimedParams = new Set<string>();
  const claimedTools = new Set<string>();
  const groups: { title: string; rows: SettingsRow[] }[] = [];

  for (const group of SETTINGS_GROUPS) {
    const rows: SettingsRow[] = [];
    const shownValues = new Set<string>();

    for (const [key, label] of group.params ?? []) {
      claimedParams.add(key);
      const value = scalarText(resolved[key]);
      if (value === null) continue;
      rows.push({ label, value });
      shownValues.add(value);
    }
    for (const key of group.tools ?? []) {
      const module = moduleByKey.get(normalizeToolKey(key));
      // Claim it only once a row exists for it. Claiming every *listed* key would make
      // the fallback below think an unversioned caller had already been shown, and the
      // whole group would disappear when the manifest is empty.
      if (!module?.version) continue;
      claimedTools.add(normalizeToolKey(key));
      // The `assembly` module and the `genome` parameter both say "GRCh38": one row for
      // one fact, rather than the same string twice under one heading.
      if (shownValues.has(module.version)) continue;
      rows.push({
        label: module.label,
        value: module.version,
        note:
          roleByTool.get(normalizeToolKey(module.key)) ??
          roleByTool.get(normalizeToolKey(module.label)),
      });
      shownValues.add(module.version);
    }
    // A caller the parameters name but the manifest has no version for is still part of
    // the record of what produced these calls.
    for (const [key, role] of group.roleParam ?? []) {
      claimedParams.add(key);
      const named = scalarText(resolved[key]);
      if (!named || claimedTools.has(normalizeToolKey(named))) continue;
      rows.push({ label: named, value: 'version not reported', note: role });
      claimedTools.add(normalizeToolKey(named));
    }
    if (rows.length) groups.push({ title: group.title, rows });
  }

  const stages = STAGE_LABELS.filter(([key]) => resolved[key] === true).map(([, label]) => label);
  const stageKeys = new Set(STAGE_LABELS.map(([key]) => key));

  const utilities = versioned
    .filter((module) => UTILITY_TOOLS.has(normalizeToolKey(module.key)))
    .sort((left, right) => left.label.localeCompare(right.label));
  utilities.forEach((module) => claimedTools.add(normalizeToolKey(module.key)));

  // Anything the pipeline reported that this file does not know about, shown rather than
  // dropped: a tool or parameter added upstream still belongs on the record.
  const otherTools = versioned
    .filter((module) => !claimedTools.has(normalizeToolKey(module.key)))
    .sort((left, right) => left.label.localeCompare(right.label))
    .map((module) => ({ label: module.label, value: module.version as string }));
  if (otherTools.length) groups.push({ title: 'Other tools', rows: otherTools });

  const otherParams = Object.entries(resolved)
    .filter(([key]) => !claimedParams.has(key) && !stageKeys.has(key))
    .map(([key, value]) => ({ label: key, value: scalarText(value) }))
    .filter((row): row is SettingsRow => row.value !== null)
    .sort((left, right) => left.label.localeCompare(right.label));
  if (otherParams.length) groups.push({ title: 'Other settings', rows: otherParams });

  if (!groups.length && !stages.length) return null;

  const body = (
    <>
      <div className="pipeline-settings-grid" data-testid="pipeline-tools">
        {groups.map((group) => (
          <div key={group.title} className="pipeline-settings-group">
            <span className="pipeline-settings-group-title">{group.title}</span>
            <dl className="pipeline-settings-list">
              {group.rows.map((row) => (
                <div key={`${row.label}-${row.value}`} className="pipeline-settings-row">
                  <dt>
                    {row.label}
                    {row.note ? <span className="pipeline-settings-note"> {row.note}</span> : null}
                  </dt>
                  <dd title={row.value}>{row.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      {utilities.length > 0 && (
        <p className="pipeline-settings-stages" data-testid="pipeline-utilities">
          <span className="pipeline-settings-group-title">Supporting utilities</span>{' '}
          {utilities.map((module) => `${module.label} ${module.version}`).join(' · ')}
        </p>
      )}
      {stages.length > 0 && (
        <p className="pipeline-settings-stages" data-testid="pipeline-stages">
          <span className="pipeline-settings-group-title">Stages run</span> {stages.join(' · ')}
        </p>
      )}
    </>
  );

  // On the report the section is printed, and a closed <details> prints nothing — so
  // only the workspace collapses it. There it is reference material a reader consults
  // occasionally, not something to scroll past on every visit, so it starts closed.
  if (variant === 'report') {
    return (
      <section className="surface-card report-pipeline report-pipeline--report" data-testid="pipeline-settings">
        <h2 className="report-audit-heading">Analysis pipeline settings</h2>
        {body}
      </section>
    );
  }

  return (
    <details className="surface-card report-pipeline pipeline-settings-details" data-testid="pipeline-settings">
      <summary className="pipeline-settings-summary">
        <h2 className="report-audit-heading">Analysis pipeline settings</h2>
      </summary>
      {body}
    </details>
  );
};

export default PipelineSettingsPanel;
