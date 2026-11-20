import React from 'react';

import type { ApiAnnotationModule } from '../../lib/apiTypes';
import { useAnnotationManifest } from './AnnotationProvenanceSummary';
import { analysisLabel, familyAnalysisTypes } from '../../lib/familyAnalyses';

/**
 * Settings of the upstream analysis pipeline, recorded per family at package import
 * (`families.metadata.pipeline`, from the run's `params_*.json`) together with the tool
 * versions from the family's annotation manifest.
 *
 * The two belong together and are shown together, grouped by the *job* they describe:
 * which reference, which caller at which version produced which variant class, which
 * annotation cache, each tool's version in the block of the parameter that configures it.
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
    tools: ['nf-core/lrsvar', 'nf-cmgg/copgtm', 'nextflow'],
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
      ['shapeit_reference_panel', 'Phasing reference panel'],
    ],
    tools: ['minimap2', 'longphase', 'samtools', 'shapeit5', 'whatshap'],
  },
  {
    title: 'Variant calling',
    params: [
      ['clair3_model_pacbio', 'Clair3 model (PacBio)'],
      ['clair3_model_ont', 'Clair3 model (ONT)'],
      ['cohort_vcf', 'Joint callset'],
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
    params: [
      ['qdnaseq_bin_size', 'QDNAseq bin size (kb)'],
      // The PGT pipeline (nf-cmgg/copgtm) names it bin_size.
      ['bin_size', 'Bin size (kb)'],
    ],
    tools: ['qdnaseq', 'wisecondorx'],
  },
  {
    // What a PGT run traced: the parent whose disease haplotype it followed, and where.
    title: 'PGT analysis',
    params: [
      ['affected_parent', 'Affected parent'],
      ['roi', 'Region of interest'],
    ],
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
    tools: ['mosdepth', 'nanoplot', 'qualimap', 'ngsbits', 'picard', 'rtgtools', 'king', 'plink'],
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
  ['apcad_imputation', 'APCAD on the imputed parents'],
];

export interface PipelineSettings {
  [key: string]: unknown;
}

/**
 * The run parameters of each analysis the family was imported for from a package that carried a
 * run record (`families.metadata.pipeline`, keyed as the family's package records are), sorted by
 * analysis; empty when none did. A family sequenced short-read and then long-read has two.
 */
export const pipelineRunsFromMetadata = (
  metadata: Record<string, unknown> | undefined,
): [string, PipelineSettings][] => {
  const pipeline = metadata?.pipeline;
  if (!pipeline || typeof pipeline !== 'object' || Array.isArray(pipeline)) return [];
  return Object.entries(pipeline as Record<string, unknown>)
    .filter((entry): entry is [string, PipelineSettings] => {
      const settings = entry[1];
      return Boolean(settings) && typeof settings === 'object' && !Array.isArray(settings);
    })
    .sort(([left], [right]) => left.localeCompare(right));
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
  /** The analysis whose run record this is, named in the heading; unset for a family of one analysis. */
  analysis?: string;
  /** Lists the tools and utilities no run parameter names (default): one panel of several does. */
  listOtherTools?: boolean;
}

/**
 * The family's pipeline settings: one panel per analysis whose package carried a run record, the
 * tool versions no parameter names listed once (in the last). Each panel is named with its
 * analysis whenever the family holds several analyses, or holds the run record of more than one,
 * so a family's one run record never reads as another analysis's; one unnamed panel otherwise.
 *
 * `analysis` is the analysis the Report page shows and signs out, of a family that holds several:
 * only its own run record is shown then, and a package's that named none, as its sign-out holds
 * them (backend `signout_scope.holds_record`); its panel is named even when its package carried
 * no run record.
 */
export const PipelineRunsPanels: React.FC<{
  familyId?: string;
  metadata: Record<string, unknown> | undefined;
  variant?: 'workspace' | 'report';
  analysis?: string | null;
}> = ({ familyId, metadata, variant, analysis = null }) => {
  const held = familyAnalysisTypes(metadata);
  const recorded = pipelineRunsFromMetadata(metadata);
  const runs = analysis ? recorded.filter(([key]) => key === analysis || !held.includes(key)) : recorded;
  if (analysis && !runs.length) {
    return <PipelineSettingsPanel familyId={familyId} settings={undefined} variant={variant} analysis={analysis} />;
  }
  if (runs.length === 0 || (runs.length === 1 && held.length <= 1)) {
    return <PipelineSettingsPanel familyId={familyId} settings={runs[0]?.[1]} variant={variant} />;
  }
  return (
    <>
      {runs.map(([analysis, settings], index) => (
        <PipelineSettingsPanel
          key={analysis}
          familyId={familyId}
          settings={settings}
          variant={variant}
          analysis={analysis}
          listOtherTools={index === runs.length - 1}
        />
      ))}
    </>
  );
};

const PipelineSettingsPanel: React.FC<PipelineSettingsPanelProps> = ({
  familyId,
  settings,
  variant = 'workspace',
  analysis,
  listOtherTools = true,
}) => {
  // Tool versions live in the family's annotation manifest, separately from the run
  // parameters — a run is only traceable with both, so show them together.
  const { data: manifest, isError: manifestFailed, refetch: refetchManifest } =
    useAnnotationManifest(familyId);

  const versioned = (manifest?.modules ?? []).filter((module) => module.version);
  // A failed manifest is not a run without tool versions: the panel says so rather than
  // vanishing, and it is printed on the report.
  if (!settings && !versioned.length && !manifestFailed) return null;

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
      // The manifest that holds the versions may have failed to load: then the version is
      // unknown, not unreported, and the panel is printed on the report.
      rows.push({
        label: named,
        value: manifestFailed ? 'version could not be loaded' : 'version not reported',
        note: role,
      });
      claimedTools.add(normalizeToolKey(named));
    }
    if (rows.length) groups.push({ title: group.title, rows });
  }

  const stages = STAGE_LABELS.filter(([key]) => resolved[key] === true).map(([, label]) => label);
  const stageKeys = new Set(STAGE_LABELS.map(([key]) => key));

  // The family's tool versions are one record (its annotation manifest): of several analyses'
  // panels, only one lists the utilities and the tools no run parameter names.
  const utilities = versioned
    .filter(() => listOtherTools)
    .filter((module) => UTILITY_TOOLS.has(normalizeToolKey(module.key)))
    .sort((left, right) => left.label.localeCompare(right.label));
  utilities.forEach((module) => claimedTools.add(normalizeToolKey(module.key)));

  // Anything the pipeline reported that this file does not know about, shown rather than
  // dropped: a tool or parameter added upstream still belongs on the record.
  const otherTools = versioned
    .filter(() => listOtherTools)
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

  if (!groups.length && !stages.length && !manifestFailed) return null;

  const body = (
    <>
      {manifestFailed ? (
        <p className="pipeline-settings-stages" role="alert" data-testid="pipeline-versions-failed">
          The tool versions could not be loaded, so only the run parameters are shown.{' '}
          <button type="button" className="button-link no-print" onClick={() => void refetchManifest()}>
            Retry
          </button>
        </p>
      ) : null}
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

  // Named with its analysis when the family holds several analyses or several run records.
  const heading = analysis
    ? `Analysis pipeline settings — ${analysisLabel(analysis) ?? analysis}`
    : 'Analysis pipeline settings';

  // On the report the section is printed, and a closed <details> prints nothing — so
  // only the workspace collapses it. There it is reference material a reader consults
  // occasionally, not something to scroll past on every visit, so it starts closed.
  if (variant === 'report') {
    return (
      <section className="surface-card report-pipeline report-pipeline--report" data-testid="pipeline-settings">
        <h2 className="report-audit-heading">{heading}</h2>
        {body}
      </section>
    );
  }

  return (
    <details className="surface-card report-pipeline pipeline-settings-details" data-testid="pipeline-settings">
      <summary className="pipeline-settings-summary">
        <h2 className="report-audit-heading">{heading}</h2>
      </summary>
      {body}
    </details>
  );
};

export default PipelineSettingsPanel;
