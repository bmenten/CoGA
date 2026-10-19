// A signed report version as its frozen snapshot records it, for the report page to render
// that version from the record alone (REQ-TRACE-007, TF-06 H9).
//
// The snapshot is the JSON `report_signout_service.build_report_snapshot` builds and
// `sign_out_report` completes; its shape is not in the API schema (the endpoint serves it
// as stored, so its content hash can be re-checked). Nothing here reads live data. A
// section a record does not hold is `null`, never an empty value, so the page says it is
// not in the record instead of showing "none". An entry that cannot be read is kept and
// marked, never dropped.

import {
  importIncompleteFromMetadata,
  importUnfinishedFromMetadata,
  type FamilyImportIncomplete,
  type FamilyImportUnfinished,
} from '../../components/ImportIncompleteBanner';
import { ACMG_CRITERIA_BY_CODE, STRENGTH_LABELS, type AcmgStrength } from '../../lib/acmg';
import { CNV_CLASS_LABELS, cnvCriterionMap } from '../../lib/cnvAcmg';
import { QC_STATUS_LABEL } from '../../lib/qcStatus';
import type { ApiSmallVariantEvidence, ApiStructuralEvidence, QcStatus } from '../../lib/apiTypes';
import { joinWithAnd } from '../../lib/format';
import { formatAlleleFrequency } from './reportNarrative';
import { formatLocus } from './smallVariantResultUtils';
import {
  getClassificationLabelFromTagKey,
  getClassificationTagKeyFromTags,
  normalizeReviewClassification,
} from './smallVariantSearch';

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value : null;
const number = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const has = (record: Json, key: string): boolean => Object.prototype.hasOwnProperty.call(record, key);

/** An override of a sign-out gate, as frozen: `null` when the record holds none. */
export interface SignedAcknowledgement {
  acknowledged: boolean | null;
  reason: string | null;
}

export interface SignedAcmgCriterion {
  code: string;
  name: string;
  description: string;
  direction: 'pathogenic' | 'benign';
  strengthLabel: string;
  evidence: string | null;
}

/** A frozen frequency or in-silico score, written as the report writes it (null: no value). */
export interface SignedEvidenceValue {
  label: string;
  value: string | null;
}

/** What `build_evidence_snapshot` froze when the variant was classified. */
export interface SignedEvidence {
  clinvar: string | null;
  annotationVersion: string | null;
  capturedAt: string | null;
  /** The primary transcript the frequencies and scores were read from. */
  transcriptId: string | null;
  /** Null when the record holds none (frozen before CoGA froze them). */
  frequencies: SignedEvidenceValue[] | null;
  inSilico: SignedEvidenceValue[] | null;
}

export interface SignedSmallVariant {
  /** Null when the entry cannot be read here (the downloaded record holds it). */
  variantId: string | null;
  classification: string | null;
  pointTotal: number | null;
  /** The accepted criteria, the ones the classification rests on. */
  criteria: SignedAcmgCriterion[];
  /** Criteria in the record that cannot be read here. */
  unreadableCriteria: number;
  tags: string[];
  note: string | null;
  /** Null when no evidence was frozen (the variant was not saved through ACMG classify). */
  evidence: SignedEvidence | null;
}

export interface SignedCnvCriterion {
  code: string;
  name: string;
  points: number | null;
  evidence: string | null;
}

export interface SignedStructuralVariant {
  variantId: string | null;
  variantKey: string | null;
  classification: string | null;
  pointTotal: number | null;
  criteria: SignedCnvCriterion[];
  unreadableCriteria: number;
  tags: string[];
  note: string | null;
}

export interface SignedDriftItem {
  variantId: string;
  status: string;
  clinvarFrom: string | null;
  clinvarTo: string | null;
  /** What moved: 'clinvar', 'transcript_id', a frequency or in-silico field, 'annotations'. */
  changed: string[];
  evidenceFrom: ApiSmallVariantEvidence | null;
  evidenceTo: ApiSmallVariantEvidence | null;
  classifiedBy: string | null;
}

export interface SignedDrift {
  checked: number | null;
  driftedCount: number;
  drifted: SignedDriftItem[];
}

export interface SignedStructuralDriftItem {
  variantId: string;
  status: string;
  /** What moved: 'source' | 'sv_type' | 'locus' | 'gene_symbols' | 'pli' | 'inheritance' | 'annotations'. */
  changed: string[];
  evidenceFrom: ApiStructuralEvidence | null;
  evidenceTo: ApiStructuralEvidence | null;
  classifiedBy: string | null;
}

export interface SignedStructuralDrift {
  checked: number | null;
  driftedCount: number;
  drifted: SignedStructuralDriftItem[];
}

export interface SignedQcCheck {
  label: string;
  status: string;
  message: string | null;
}

export interface SignedSampleQc {
  overallStatus: string | null;
  applicationLabel: string | null;
  checks: SignedQcCheck[];
  notes: string[];
}

export interface SignedSequencingQcMetric {
  label: string;
  value: number | null;
  unit: string;
  verdict: string;
  warnValue: number | null;
  errorValue: number | null;
}

export interface SignedSequencingQcSample {
  sampleId: string;
  verdict: string | null;
  /** The metrics outside their cut-offs (a warning or a fail). */
  breached: SignedSequencingQcMetric[];
}

export interface SignedSequencingQc {
  profileLabel: string | null;
  /** Why the cut-offs could not be resolved at sign-out, when they could not (#514). */
  unavailable: string | null;
  samples: SignedSequencingQcSample[];
}

/** One annotation or reference module, as the manifest frozen into the record lists it. */
export interface SignedModule {
  key: string | null;
  label: string | null;
  version: string | null;
  detail: string | null;
  byModality: Record<string, string> | null;
}

export interface SignedReport {
  familyId: string | null;
  assembly: string | null;
  modules: SignedModule[] | null;
  software: { version: string; gitSha: string | null } | null;
  drift: SignedDrift | null;
  /** The SV/CNV classifications' evidence drift; null: not in the record. */
  structuralDrift: SignedStructuralDrift | null;
  driftAcknowledgement: SignedAcknowledgement;
  sampleQc: SignedSampleQc | null;
  qcAcknowledgement: SignedAcknowledgement;
  sequencingQc: SignedSequencingQc | null;
  /** `absent`: the record holds no import state; null: the import was complete. */
  importIncomplete: FamilyImportIncomplete | null | 'absent';
  /** The imports that had begun writing the family and not finished; `absent`: not in the record. */
  importUnfinished: FamilyImportUnfinished[] | 'absent';
  importAcknowledgement: SignedAcknowledgement;
  reportedVariants: SignedSmallVariant[] | null;
  /** Null: the record holds no list of reported SVs. */
  reportedStructuralVariants: SignedStructuralVariant[] | null;
}

const acknowledgement = (snapshot: Json, flag: string, reason: string): SignedAcknowledgement => ({
  acknowledged: typeof snapshot[flag] === 'boolean' ? (snapshot[flag] as boolean) : null,
  reason: text(snapshot[reason]),
});

const parseAcmgCriteria = (acmg: Json | null): { criteria: SignedAcmgCriterion[]; unreadable: number } => {
  const entries = Array.isArray(acmg?.criteria) ? (acmg.criteria as unknown[]) : [];
  const criteria: SignedAcmgCriterion[] = [];
  let unreadable = 0;
  entries.forEach((entry) => {
    const code = isObject(entry) ? text(entry.code) : null;
    if (!isObject(entry) || !code) {
      unreadable += 1;
      return;
    }
    if (entry.accepted !== true) return;
    const def = ACMG_CRITERIA_BY_CODE[code as keyof typeof ACMG_CRITERIA_BY_CODE];
    const strength = text(entry.strength) ?? '';
    criteria.push({
      code,
      name: def?.name ?? code,
      description: def?.description ?? '',
      direction: def?.direction ?? 'pathogenic',
      strengthLabel: STRENGTH_LABELS[strength as AcmgStrength] || strength,
      evidence: text(entry.evidence),
    });
  });
  return { criteria, unreadable };
};

// The frequencies and in-silico scores a small-variant classification's evidence freezes
// (backend `small_variant_review_acmg.small_variant_evidence`), in the order a reviewer
// reads them, each with how the report writes it: an allele frequency as the variant
// description does, a count whole, a score to its digits, a prediction as annotated.
interface EvidenceField {
  key: string;
  label: string;
  format: 'frequency' | 'count' | 'text' | number;
}

const FREQUENCY_FIELDS: EvidenceField[] = [
  { key: 'gnomad_af', label: 'gnomAD AF', format: 'frequency' },
  { key: 'gnomad_exomes_af', label: 'gnomAD exomes AF', format: 'frequency' },
  { key: 'gnomad_genomes_af', label: 'gnomAD genomes AF', format: 'frequency' },
  { key: 'gnomad_popmax_af', label: 'gnomAD popmax AF', format: 'frequency' },
  { key: 'topmed_af', label: 'TOPMed AF', format: 'frequency' },
  { key: 'gnomad_ac', label: 'gnomAD allele count', format: 'count' },
  { key: 'gnomad_hom_count', label: 'gnomAD homozygotes', format: 'count' },
  { key: 'gnomad_hemi_count', label: 'gnomAD hemizygotes', format: 'count' },
];

const IN_SILICO_FIELDS: EvidenceField[] = [
  { key: 'cadd_phred', label: 'CADD', format: 1 },
  { key: 'revel', label: 'REVEL', format: 3 },
  { key: 'spliceai_max', label: 'SpliceAI', format: 2 },
  { key: 'sift', label: 'SIFT', format: 'text' },
  { key: 'polyphen', label: 'PolyPhen', format: 'text' },
  { key: 'alpha_missense_class', label: 'AlphaMissense class', format: 'text' },
  { key: 'alpha_missense_pathogenicity', label: 'AlphaMissense score', format: 3 },
];

// A value of the wrong type for its field is not guessed: it reads as no value.
const formatEvidenceValue = (field: EvidenceField, value: unknown): string | null => {
  if (field.format === 'text') return text(value);
  const numeric = number(value);
  if (numeric === null) return null;
  if (field.format === 'frequency') return formatAlleleFrequency(numeric);
  return typeof field.format === 'number' ? numeric.toFixed(field.format) : String(numeric);
};

const parseEvidenceValues = (value: unknown, fields: EvidenceField[]): SignedEvidenceValue[] | null =>
  isObject(value)
    ? fields
        .filter((field) => has(value, field.key))
        .map((field) => ({ label: field.label, value: formatEvidenceValue(field, value[field.key]) }))
    : null;

const parseSmallVariant = (entry: unknown): SignedSmallVariant => {
  if (!isObject(entry)) {
    return {
      variantId: null,
      classification: null,
      pointTotal: null,
      criteria: [],
      unreadableCriteria: 0,
      tags: [],
      note: null,
      evidence: null,
    };
  }
  const acmg = isObject(entry.acmg) ? entry.acmg : null;
  const tags = strings(entry.tags);
  const { criteria, unreadable } = parseAcmgCriteria(acmg);
  const evidence = isObject(entry.evidence_snapshot) ? entry.evidence_snapshot : null;
  return {
    variantId: text(entry.variant_id),
    // As the live report reads a review: the ACMG classification, else the class the
    // review's classification tag names.
    classification:
      text(acmg?.classification) ??
      (getClassificationLabelFromTagKey(text(entry.acmg_class)) ||
        getClassificationLabelFromTagKey(getClassificationTagKeyFromTags(tags)) ||
        null),
    pointTotal: number(acmg?.point_total),
    criteria,
    unreadableCriteria: unreadable,
    tags,
    note: text(entry.note),
    evidence: evidence
      ? {
          clinvar: text(evidence.clinvar),
          annotationVersion: text(evidence.annotation_version),
          capturedAt: text(evidence.captured_at),
          transcriptId: text(evidence.transcript_id),
          frequencies: parseEvidenceValues(evidence.frequencies, FREQUENCY_FIELDS),
          inSilico: parseEvidenceValues(evidence.in_silico, IN_SILICO_FIELDS),
        }
      : null,
  };
};

const parseStructuralVariant = (entry: unknown): SignedStructuralVariant => {
  if (!isObject(entry)) {
    return {
      variantId: null,
      variantKey: null,
      classification: null,
      pointTotal: null,
      criteria: [],
      unreadableCriteria: 0,
      tags: [],
      note: null,
    };
  }
  const cnvAcmg = isObject(entry.cnv_acmg) ? entry.cnv_acmg : null;
  const tags = strings(entry.tags);
  const definitions = cnvCriterionMap(cnvAcmg?.kind === 'gain' ? 'gain' : 'loss');
  const criteria: SignedCnvCriterion[] = [];
  let unreadable = 0;
  (Array.isArray(cnvAcmg?.criteria) ? (cnvAcmg.criteria as unknown[]) : []).forEach((criterion) => {
    const code = isObject(criterion) ? text(criterion.code) : null;
    if (!isObject(criterion) || !code) {
      unreadable += 1;
      return;
    }
    if (criterion.accepted !== true) return;
    criteria.push({
      code,
      name: definitions[code]?.name ?? code,
      points: number(criterion.points),
      evidence: text(criterion.evidence),
    });
  });
  const cnvClass = text(entry.cnv_class);
  const variantKey = entry.variant_key;
  return {
    variantId: text(entry.variant_id),
    variantKey:
      typeof variantKey === 'number' || typeof variantKey === 'string' ? String(variantKey) : null,
    // As the live report reads an SV review, then the ClinGen CNV scoring it holds.
    classification:
      normalizeReviewClassification(text(entry.classification), tags) ||
      text(cnvAcmg?.classification) ||
      (cnvClass ? (CNV_CLASS_LABELS[cnvClass] ?? cnvClass) : null),
    pointTotal: number(entry.cnv_point_total) ?? number(cnvAcmg?.point_total),
    criteria,
    unreadableCriteria: unreadable,
    tags,
    note: text(entry.note),
  };
};

const parseDrift = (value: unknown): SignedDrift | null => {
  if (!isObject(value)) return null;
  const drifted = (Array.isArray(value.drifted) ? (value.drifted as unknown[]) : []).map(
    (item): SignedDriftItem =>
      isObject(item)
        ? {
            variantId: text(item.variant_id) ?? 'unnamed variant',
            status: text(item.status) ?? 'unknown',
            clinvarFrom: text(item.clinvar_from),
            clinvarTo: text(item.clinvar_to),
            changed: strings(item.changed),
            evidenceFrom: isObject(item.evidence_from) ? item.evidence_from : null,
            evidenceTo: isObject(item.evidence_to) ? item.evidence_to : null,
            classifiedBy: text(item.classified_by),
          }
        : {
            variantId: 'unnamed variant',
            status: 'unknown',
            clinvarFrom: null,
            clinvarTo: null,
            changed: [],
            evidenceFrom: null,
            evidenceTo: null,
            classifiedBy: null,
          },
  );
  return {
    checked: number(value.checked),
    driftedCount: number(value.drifted_count) ?? drifted.length,
    drifted,
  };
};

// The evidence fields the drift check compares, each kept only when it has the expected type.
const parseStructuralEvidence = (value: unknown): ApiStructuralEvidence | null => {
  if (!isObject(value)) return null;
  const genes = Array.isArray(value.gene_symbols)
    ? (value.gene_symbols as unknown[]).filter((gene): gene is string => typeof gene === 'string')
    : null;
  return {
    source: text(value.source),
    sv_type: text(value.sv_type),
    chrom: text(value.chrom),
    start: number(value.start),
    end: number(value.end),
    gene_symbols: genes,
    pli: number(value.pli),
    inheritance: text(value.inheritance),
  };
};

const parseStructuralDrift = (value: unknown): SignedStructuralDrift | null => {
  if (!isObject(value)) return null;
  const drifted = (Array.isArray(value.drifted) ? (value.drifted as unknown[]) : []).map(
    (item): SignedStructuralDriftItem =>
      isObject(item)
        ? {
            variantId: text(item.variant_id) ?? 'unnamed variant',
            status: text(item.status) ?? 'unknown',
            changed: Array.isArray(item.changed)
              ? (item.changed as unknown[]).filter((field): field is string => typeof field === 'string')
              : [],
            evidenceFrom: parseStructuralEvidence(item.evidence_from),
            evidenceTo: parseStructuralEvidence(item.evidence_to),
            classifiedBy: text(item.classified_by),
          }
        : {
            variantId: 'unnamed variant',
            status: 'unknown',
            changed: [],
            evidenceFrom: null,
            evidenceTo: null,
            classifiedBy: null,
          },
  );
  return {
    checked: number(value.checked),
    driftedCount: number(value.drifted_count) ?? drifted.length,
    drifted,
  };
};

const qcCheck = (check: unknown, label: (value: Json) => string): SignedQcCheck | null =>
  isObject(check)
    ? { label: label(check), status: text(check.status) ?? 'unknown', message: text(check.message) }
    : null;

const parseSampleQc = (value: unknown): SignedSampleQc | null => {
  if (!isObject(value)) return null;
  const list = (key: string, label: (check: Json) => string): SignedQcCheck[] =>
    (Array.isArray(value[key]) ? (value[key] as unknown[]) : [])
      .map((check) => qcCheck(check, label))
      .filter((check): check is SignedQcCheck => check !== null);
  const single = (key: string, label: (check: Json) => string): SignedQcCheck[] => {
    const check = qcCheck(value[key], label);
    return check ? [check] : [];
  };
  return {
    overallStatus: text(value.overall_status),
    applicationLabel: text(value.application_label),
    checks: [
      ...list('sex_checks', (check) => `Sex of ${text(check.sample_id) ?? 'a sample'}`),
      ...list(
        'relatedness_checks',
        (check) =>
          `Relatedness of ${text(check.sample_a) ?? '?'} and ${text(check.sample_b) ?? '?'}${
            text(check.expected_relationship) ? ` (expected ${check.expected_relationship})` : ''
          }`,
      ),
      ...list('mendelian_checks', (check) => `Mendelian consistency of ${text(check.child) ?? 'a child'}`),
      ...single('paternity_check', (check) => `Paternity (cfDNA)${text(check.father) ? ` of ${check.father}` : ''}`),
      ...single('fetal_sex_check', () => 'Fetal sex (cfDNA)'),
      ...single('category_qc_check', () => 'cfDNA category distribution'),
    ],
    notes: strings(value.notes),
  };
};

const parseSequencingQc = (value: unknown): SignedSequencingQc | null => {
  if (!isObject(value)) return null;
  const samples = isObject(value.samples) ? value.samples : {};
  return {
    profileLabel: text(value.profile_label) ?? text(value.profile_key),
    unavailable: text(value.unavailable),
    samples: Object.keys(samples)
      .sort()
      .map((sampleId): SignedSequencingQcSample => {
        const evaluation = isObject(samples[sampleId]) ? (samples[sampleId] as Json) : {};
        const metrics = Array.isArray(evaluation.metrics) ? (evaluation.metrics as unknown[]) : [];
        return {
          sampleId,
          verdict: text(evaluation.verdict),
          breached: metrics
            .filter(isObject)
            .filter((metric) => metric.verdict === 'warn' || metric.verdict === 'fail')
            .map((metric) => ({
              label: text(metric.label) ?? text(metric.metric_key) ?? 'metric',
              value: number(metric.value),
              unit: text(metric.unit) ?? '',
              verdict: text(metric.verdict) ?? 'unknown',
              warnValue: number(metric.warn_value),
              errorValue: number(metric.error_value),
            })),
        };
      }),
  };
};

const parseModules = (value: unknown): SignedModule[] | null => {
  if (!Array.isArray(value)) return null;
  return value.filter(isObject).map((module) => {
    const byModality = isObject(module.by_modality)
      ? Object.fromEntries(
          Object.entries(module.by_modality).filter(
            (entry): entry is [string, string] => typeof entry[1] === 'string',
          ),
        )
      : null;
    return {
      key: text(module.key),
      label: text(module.label),
      version: text(module.version),
      detail: text(module.detail),
      byModality,
    };
  });
};

/** The signed version's frozen snapshot, read; null when there is no readable snapshot. */
export const parseSignedReport = (snapshot: unknown): SignedReport | null => {
  if (!isObject(snapshot)) return null;
  const software = isObject(snapshot.software) ? snapshot.software : null;
  return {
    familyId: text(snapshot.family_id),
    assembly: text(snapshot.assembly),
    modules: parseModules(snapshot.modules),
    software: software && text(software.version)
      ? { version: text(software.version) as string, gitSha: text(software.git_sha) }
      : null,
    drift: parseDrift(snapshot.drift),
    structuralDrift: parseStructuralDrift(snapshot.structural_drift),
    driftAcknowledgement: acknowledgement(snapshot, 'acknowledged_drift', 'drift_acknowledgement_reason'),
    sampleQc: parseSampleQc(snapshot.sample_qc),
    qcAcknowledgement: acknowledgement(snapshot, 'acknowledged_qc', 'qc_acknowledgement_reason'),
    sequencingQc: parseSequencingQc(snapshot.sequencing_qc),
    importIncomplete: has(snapshot, 'import_incomplete')
      ? importIncompleteFromMetadata(snapshot)
      : 'absent',
    importUnfinished: has(snapshot, 'import_unfinished')
      ? importUnfinishedFromMetadata(snapshot)
      : 'absent',
    importAcknowledgement: acknowledgement(
      snapshot,
      'acknowledged_import_incomplete',
      'import_incomplete_acknowledgement_reason',
    ),
    reportedVariants: Array.isArray(snapshot.reported_variants)
      ? (snapshot.reported_variants as unknown[]).map(parseSmallVariant)
      : null,
    reportedStructuralVariants: Array.isArray(snapshot.reported_structural_variants)
      ? (snapshot.reported_structural_variants as unknown[]).map(parseStructuralVariant)
      : null,
  };
};

// --- Wording shared by the live and the signed report ----------------------

/** `2026-06-25 10:00 UTC` from an ISO timestamp, as the report writes times. */
export const formatReportTime = (iso: string): string => `${iso.replace('T', ' ').slice(0, 16)} UTC`;

/**
 * One module and its version, as the report footer lists it. A database cited at
 * different releases by different pipelines lists each (issue #294): "GENCODE 49 (snv),
 * 45 (sv)".
 */
export const describeModuleVersion = (module: {
  key?: string | null;
  label?: string | null;
  version?: string | null;
  detail?: string | null;
  by_modality?: Record<string, string> | null;
  byModality?: Record<string, string> | null;
}): string => {
  const label = module.label || module.key || 'module';
  const byModality = module.byModality ?? module.by_modality ?? null;
  if (byModality && new Set(Object.values(byModality)).size > 1) {
    return `${label} ${Object.entries(byModality)
      .map(([modality, version]) => `${version} (${modality})`)
      .join(', ')}`;
  }
  return `${label} ${module.version}${module.detail ? ` (${module.detail})` : ''}`;
};

/** The modules that carry a version, joined as the footer lists them; null when none do. */
export const describeModuleVersions = (
  modules: Parameters<typeof describeModuleVersion>[0][],
): string | null => {
  const versioned = modules.filter((module) => module.version);
  return versioned.length ? versioned.map(describeModuleVersion).join(' · ') : null;
};

// Snapshot sections the sign-out check can report as changed (report_signout_service
// REPORT_CONTENT_SECTIONS), in words a reviewer reads.
const REPORT_SECTION_LABELS: Record<string, string> = {
  assembly: 'reference assembly',
  modules: 'annotation and pipeline versions',
  reported_variants: 'reported small variants',
  reported_structural_variants: 'reported structural variants',
  drift: 'evidence drift',
  structural_drift: 'structural-variant evidence drift',
  sample_qc: 'sample-integrity QC',
  sequencing_qc: 'sequencing QC cut-offs',
  import_incomplete: 'import completeness',
  import_unfinished: 'unfinished imports',
};

/** The sections the sign-out check names as changed: "reported small variants and …". */
export const describeReportSections = (sections: string[]): string =>
  joinWithAnd(sections.map((section) => REPORT_SECTION_LABELS[section] ?? section));

/** A QC verdict in the words the Sample QC page uses. */
export const qcStatusLabel = (status: string | null): string =>
  status ? (QC_STATUS_LABEL[status as QcStatus] ?? status) : 'not recorded';

/**
 * The frozen frequencies and in-silico scores of a classification, as the signed view writes
 * them: "Read from its primary transcript ENST…: population frequencies gnomAD AF 3.10e-5, …;
 * in silico scores CADD 24.3, …". Null when the record holds none (frozen before CoGA froze
 * them).
 */
export const describeFrozenEvidenceValues = (evidence: SignedEvidence): string | null => {
  const list = (values: SignedEvidenceValue[]) =>
    joinWithAnd(values.map((entry) => `${entry.label} ${entry.value ?? 'not annotated'}`));
  const groups = [
    evidence.frequencies?.length ? `population frequencies ${list(evidence.frequencies)}` : null,
    evidence.inSilico?.length ? `in silico scores ${list(evidence.inSilico)}` : null,
  ].filter((group): group is string => group !== null);
  if (!groups.length) return null;
  return `Read from its primary transcript${evidence.transcriptId ? ` ${evidence.transcriptId}` : ''}: ${groups.join('; ')}.`;
};

/**
 * What moved in a small-variant classification's frozen frequencies and in-silico scores
 * ("REVEL 0.412 → 0.951; gnomAD AF 3.10e-5 → 0.012"); null when none of them moved. The
 * live report and the signed version both describe drift this way.
 */
export const describeSmallVariantEvidenceChange = (
  changed: string[],
  from: ApiSmallVariantEvidence | null,
  to: ApiSmallVariantEvidence | null,
): string | null => {
  const parts: string[] = [];
  if (changed.includes('transcript_id')) {
    parts.push(`primary transcript ${text(from?.transcript_id) ?? 'n/a'} → ${text(to?.transcript_id) ?? 'n/a'}`);
  }
  [...FREQUENCY_FIELDS, ...IN_SILICO_FIELDS]
    .filter((field) => changed.includes(field.key))
    .forEach((field) => {
      let before = formatEvidenceValue(field, from?.[field.key]);
      let after = formatEvidenceValue(field, to?.[field.key]);
      // Two values that differ past the digits the report writes are written in full.
      if (before !== null && before === after) {
        before = String(from?.[field.key]);
        after = String(to?.[field.key]);
      }
      parts.push(`${field.label} ${before ?? 'n/a'} → ${after ?? 'n/a'}`);
    });
  return parts.length ? parts.join('; ') : null;
};

/** What moved in a small-variant classification's evidence: its ClinVar significance, then its values. */
export const describeSmallVariantDrift = (item: {
  clinvarFrom: string | null;
  clinvarTo: string | null;
  changed: string[];
  evidenceFrom: ApiSmallVariantEvidence | null;
  evidenceTo: ApiSmallVariantEvidence | null;
}): string | null => {
  const parts = [
    item.clinvarFrom !== item.clinvarTo ? `ClinVar ${item.clinvarFrom || 'n/a'} → ${item.clinvarTo || 'n/a'}` : null,
    describeSmallVariantEvidenceChange(item.changed, item.evidenceFrom, item.evidenceTo),
  ].filter((part): part is string => part !== null);
  return parts.length ? parts.join('; ') : null;
};

/** Why a classification counted as drift when the version was signed. */
export const describeSignedDrift = (item: SignedDriftItem): string => {
  if (item.status === 'variant_missing') return 'no longer present in the data';
  if (item.status === 'no_snapshot') return 'no frozen evidence (not saved through ACMG classify)';
  if (item.status === 'unknown') return 'its evidence could not be compared';
  return describeSmallVariantDrift(item) ?? 'annotation set changed';
};

const genesOf = (evidence: ApiStructuralEvidence | null): string =>
  evidence?.gene_symbols?.length ? evidence.gene_symbols.join(', ') : 'none';

const pliOf = (evidence: ApiStructuralEvidence | null): string =>
  typeof evidence?.pli === 'number' ? evidence.pli.toFixed(3) : 'n/a';

const locusOf = (evidence: ApiStructuralEvidence | null): string =>
  evidence && typeof evidence.start === 'number' && typeof evidence.end === 'number'
    ? formatLocus({ chr: String(evidence.chrom ?? ''), start: evidence.start, end: evidence.end })
    : 'n/a';

/**
 * What moved in an SV/CNV classification's evidence, in the words of its inputs ("genes A, B →
 * A; pLI 0.990 → 0.410"); null when none of the named inputs moved. The live report and the
 * signed version both describe drift this way.
 */
export const describeStructuralEvidenceChange = (
  changed: string[],
  from: ApiStructuralEvidence | null,
  to: ApiStructuralEvidence | null,
): string | null => {
  const parts: string[] = [];
  if (changed.includes('gene_symbols')) parts.push(`genes ${genesOf(from)} → ${genesOf(to)}`);
  if (changed.includes('pli')) parts.push(`pLI ${pliOf(from)} → ${pliOf(to)}`);
  if (changed.includes('inheritance')) {
    parts.push(`inheritance ${from?.inheritance || 'n/a'} → ${to?.inheritance || 'n/a'}`);
  }
  if (changed.includes('sv_type')) parts.push(`type ${from?.sv_type || 'n/a'} → ${to?.sv_type || 'n/a'}`);
  if (changed.includes('locus')) parts.push(`locus ${locusOf(from)} → ${locusOf(to)}`);
  if (changed.includes('source')) parts.push(`caller ${from?.source || 'n/a'} → ${to?.source || 'n/a'}`);
  return parts.length ? parts.join('; ') : null;
};

/** Why an SV/CNV classification counted as drift when the version was signed. */
export const describeSignedStructuralDrift = (item: SignedStructuralDriftItem): string => {
  if (item.status === 'variant_missing') return 'no longer present in the data';
  if (item.status === 'no_snapshot') {
    return 'no frozen evidence (no CNV scoring saved)';
  }
  if (item.status === 'unknown') return 'its evidence could not be compared';
  return (
    describeStructuralEvidenceChange(item.changed, item.evidenceFrom, item.evidenceTo) ??
    (item.changed.includes('annotations') ? 'annotation changed' : 'evidence changed')
  );
};

/** A sequencing-QC metric outside its cut-offs: "Mean coverage 18 × (Warning; warning 20, fail 10)". */
export const describeBreachedMetric = (metric: SignedSequencingQcMetric): string => {
  const limits = [
    metric.warnValue !== null ? `warning ${metric.warnValue}` : null,
    metric.errorValue !== null ? `fail ${metric.errorValue}` : null,
  ].filter((part): part is string => Boolean(part));
  const value = metric.value !== null ? ` ${metric.value}${metric.unit ? ` ${metric.unit}` : ''}` : '';
  return `${metric.label}${value} (${qcStatusLabel(metric.verdict)}${limits.length ? `; ${limits.join(', ')}` : ''})`;
};

// --- The report page's views ------------------------------------------------

/** A `?version=` value that names a signed version (a positive whole number), else null. */
export const parseSignedVersionParam = (value: string | null): number | null =>
  value !== null && /^[1-9][0-9]*$/.test(value.trim()) ? Number(value.trim()) : null;

/**
 * The report page's search string for a view, keeping the other parameters (the project):
 * a signed version (`?version=N`) or the live report (`?view=live`).
 */
export const reportViewSearch = (search: string, view: { version: number } | 'live'): string => {
  const params = new URLSearchParams(search);
  params.delete('version');
  params.delete('view');
  if (view === 'live') params.set('view', 'live');
  else params.set('version', String(view.version));
  return `?${params.toString()}`;
};
