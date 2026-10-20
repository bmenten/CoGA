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
import type { PgtEmbryoQcFindingOut } from '../../lib/apiSchema.generated';
import {
  describeQcFindings,
  describeQcSides,
  formatQcRate,
  segregationStateLabel,
  sexUnknownWarning,
} from '../../lib/embryoSegregation';
import { formatCount, joinWithAnd } from '../../lib/format';
import type { HaplotypeRiskState } from '../../lib/haplotypeRisk';
import type { HaplotypePhaseCorrection } from '../../lib/haplotypePhaseCorrections';
import { FATHER_STATE_LABELS, depth, niptEvidenceSummary, pct, readsText } from './niptClassification';
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

/** A frozen value (a frequency, a score, the consequence), written as the report writes it (null: no value). */
export interface SignedEvidenceValue {
  label: string;
  value: string | null;
}

/** What `build_evidence_snapshot` froze when the variant was classified. */
export interface SignedEvidence {
  clinvar: string | null;
  annotationVersion: string | null;
  capturedAt: string | null;
  /** The primary transcript the consequence, frequencies and scores were read from. */
  transcriptId: string | null;
  /** Null when the record holds none (frozen before CoGA froze them). */
  consequence: SignedEvidenceValue[] | null;
  frequencies: SignedEvidenceValue[] | null;
  inSilico: SignedEvidenceValue[] | null;
  /**
   * The gene's missense Z, from its gene profile, which is frozen for a missense variant only;
   * null when the record holds none.
   */
  geneConstraint: SignedEvidenceValue[] | null;
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
  /**
   * What moved: 'clinvar', 'transcript_id', a consequence, frequency or in-silico field, the
   * gene's 'gene_missense_z', 'annotations'.
   */
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
  /** The application the Sample QC read the family as ('pgt', 'nipt', 'wgs', ...). */
  application: string | null;
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

/**
 * What a signed record is (owner decision of 2026-10-14). Frozen into each record since
 * (`record_statement`); said on the live report, and on a record signed before CoGA froze it.
 */
export const ANALYTICAL_SIGN_OFF_STATEMENT =
  'This record is the analytical sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient report happens in the EPD, which refers to this record by its report code.';

/** The same, on the live report, where the case is signed out. */
export const LIVE_REPORT_SIGN_OFF_STATEMENT =
  'Signing out is the analytical sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient report happens in the EPD, which refers to the signed record by its report code.';

/**
 * A section of what the analysis looked at (`signed_record_sections.py`): `absent` from a
 * record signed before CoGA recorded it, `unavailable` when it could not be read at sign-out
 * (the reason frozen in its place), else `recorded`. Adding a section: parse it with
 * `parseRecordSection` and render it with `RecordSectionBlock` (SignedFamilyReport.tsx).
 */
export type SignedRecordSection<T> =
  | { state: 'absent' }
  | { state: 'unavailable'; reason: string }
  | { state: 'recorded'; value: T };

export interface SignedGenotype {
  sample: string;
  gt: string | null;
  dp: number | null;
  gq: number | null;
  ad: number[] | null;
  af: number[] | null;
  /** Structural variants: the copy number and the reads that support the call. */
  cn: number | null;
  readSupport: number | null;
}

/** A reported small variant as the analyst reviewed it, one per callset that calls it. */
export interface SignedSmallVariantDescription {
  variantId: string | null;
  source: string | null;
  chrom: string | null;
  start: number | null;
  ref: string | null;
  alt: string | null;
  gene: string | null;
  transcript: string | null;
  hgvsc: string | null;
  hgvsp: string | null;
  consequence: string | null;
  impact: string | null;
  maneSelect: boolean;
  genotypes: SignedGenotype[];
}

/** A reported structural variant or CNV as the analyst reviewed it, one per caller. */
export interface SignedStructuralVariantDescription {
  variantId: string | null;
  source: string | null;
  svType: string | null;
  copyChange: string | null;
  chrom: string | null;
  start: number | null;
  end: number | null;
  length: number | null;
  remoteChrom: string | null;
  remoteStart: number | null;
  genes: string[];
  cytoband: string | null;
  genotypes: SignedGenotype[];
}

export interface SignedVariantDescriptions {
  small: SignedSmallVariantDescription[];
  structural: SignedStructuralVariantDescription[];
}

/** A gene-panel version the family's searches applied, with the release of its source. */
export interface SignedGenePanel {
  panelId: string | null;
  name: string | null;
  version: number | null;
  source: string | null;
  geneCount: number | null;
  externalId: string | null;
  externalVersion: string | null;
  externalVersionCreated: string | null;
  confidenceLevels: string[];
  sourceUpdatedAt: string | null;
  firstAppliedAt: string | null;
  firstAppliedBy: string | null;
}

export interface SignedGeneReferenceSource {
  source: string;
  status: string | null;
  release: string | null;
  checksum: string | null;
  genes: string[];
}

export interface SignedGeneReference {
  genes: string[];
  sources: SignedGeneReferenceSource[];
  notInReference: string[];
}

export interface SignedCnvKnowledgebase {
  source: string | null;
  release: string | null;
  releaseDate: string | null;
  loadedAt: string | null;
  records: number | null;
}

export interface SignedReferenceData {
  geneReference: SignedRecordSection<SignedGeneReference>;
  /** `recorded` with null: no knowledgebase was loaded. */
  clinicalCnvs: SignedRecordSection<SignedCnvKnowledgebase | null>;
}

export interface SignedRepeatAllele {
  repeatCount: number | null;
  bpLength: number | null;
  status: string;
}

export interface SignedRepeatCall {
  sample: string;
  genotype: string | null;
  status: string;
  note: string | null;
  alleles: SignedRepeatAllele[];
}

export interface SignedRepeatLocus {
  locusId: string | null;
  gene: string | null;
  disease: string | null;
  chrom: string | null;
  start: number | null;
  motif: string | null;
  thresholds: {
    intermediateMin: number | null;
    premutationMin: number | null;
    pathogenicMin: number | null;
  };
  status: string;
  calls: SignedRepeatCall[];
}

export interface SignedCatalogue {
  /** How many loci (TRGT) or regions (Paraphase) it holds. */
  entries: number | null;
  sources: string[];
  digest: string | null;
}

export interface SignedRepeatExpansions {
  catalogue: SignedCatalogue | null;
  loci: SignedRepeatLocus[];
}

export interface SignedParaphaseResult {
  sample: string;
  status: string | null;
  copyNumberSignal: boolean;
  copyNumber: { label: string; value: number | null }[];
}

export interface SignedParaphaseGene {
  gene: string | null;
  region: string | null;
  /** How the catalogue reads the status ("status by smn1_cn: pathogenic = 0, …"), when it has a rule. */
  statusRule: string | null;
  results: SignedParaphaseResult[];
}

export interface SignedParaphase {
  catalogue: SignedCatalogue | null;
  genes: SignedParaphaseGene[];
}

/** A parental homolog as the PGT analysis names it: its side and its label ('0', '1'). */
export interface SignedPgtHaplotype {
  origin: 'paternal' | 'maternal' | null;
  homolog: string | null;
}

export interface SignedPgtDiseaseHaplotype extends SignedPgtHaplotype {
  kind: string | null;
}

/** A homolog an embryo carries at the ROI, with the extent of its block. */
export interface SignedPgtInherited extends SignedPgtHaplotype {
  start: number | null;
  end: number | null;
}

/** An embryo's phasing QC at the ROI, as the ROI marker review counts it. */
export interface SignedPgtEmbryoQc {
  informativeSites: number | null;
  mendelErrors: number | null;
  mendelRate: number | null;
  paternalMarkers: number | null;
  maternalMarkers: number | null;
}

/** An embryo's phasing QC at the ROI checked against the thresholds, as the record froze it. */
export interface SignedPgtQcVerdict {
  status: 'pass' | 'warning' | 'not_counted';
  /** The parental sides whose markers were checked: the sides the call reads. */
  sides: ('paternal' | 'maternal')[];
  /** Each measure that did not meet its threshold, with its value and the threshold. */
  findings: PgtEmbryoQcFindingOut[];
  /** Findings the record holds that cannot be read here (kept in the downloaded record). */
  unreadableFindings: number;
}

/** The thresholds each embryo's phasing QC was checked against, as the record froze them. */
export interface SignedPgtQcThresholds {
  minInformativeSites: number | null;
  maxMendelRate: number | null;
  minMarkersPerSide: number | null;
}

export interface SignedPgtEmbryo {
  sample: string;
  sex: string | null;
  /** Null: not assessed (no ROI, or no haplotypes read). */
  call: HaplotypeRiskState | null;
  roiNotCovered: boolean;
  sexDependent: { ifMale: HaplotypeRiskState; ifFemale: HaplotypeRiskState } | null;
  inherited: SignedPgtInherited[];
  recombinations: number[];
  qc: SignedPgtEmbryoQc | null;
  /** Null: not assessed, or a record signed before CoGA checked the QC against thresholds. */
  qcVerdict: SignedPgtQcVerdict | null;
}

export interface SignedPgtMember {
  sample: string;
  role: string | null;
  sex: string | null;
  affected: boolean;
  carrierStatus: string | null;
  carrierType: string | null;
}

export interface SignedPgtSource {
  sample: string;
  source: string | null;
  filename: string | null;
  blocks: number | null;
  loadedAt: string | null;
}

/** The PGT analysis at the ROI as a PGT family's record froze it (pgt_analysis.py). */
export interface SignedPgtAnalysis {
  roi: { query: string | null; label: string | null; chrom: string | null; start: number | null; end: number | null } | null;
  inheritance: { recorded: string | null; applied: string | null };
  diseaseHaplotypeResolved: boolean;
  diseaseHaplotypes: SignedPgtDiseaseHaplotype[];
  phaseCorrections: HaplotypePhaseCorrection[];
  embryos: SignedPgtEmbryo[];
  members: SignedPgtMember[];
  sources: SignedPgtSource[];
  blocksRead: number | null;
  blocksDigest: string | null;
  markersTruncated: boolean;
  recombinationFlank: number | null;
  /** Null: a record signed before CoGA checked each embryo's QC against thresholds. */
  qcThresholds: SignedPgtQcThresholds | null;
}

/** A NIPT quality check as frozen (`nipt_quality_checks`): its verdict and why. */
export interface SignedNiptCheck {
  status: string | null;
  message: string | null;
}

export interface SignedNiptFetalFraction extends SignedNiptCheck {
  ff: number | null;
  /** Null: not estimated (too few category-7 sites). */
  ffComputed: number | null;
  ffMedian: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  nSites: number | null;
  lowConfidence: boolean;
}

export interface SignedNiptFetalSex extends SignedNiptCheck {
  call: string | null;
  /** What the father's X alleles read, and what the plasma's chrY coverage reads. */
  paternalX: string | null;
  chry: string | null;
}

export interface SignedNiptTargetCoverage {
  targets: number | null;
  medianMean: number | null;
  belowCritical: number | null;
  belowAdvisory: number | null;
  incomplete: number | null;
  criticalMeanDepth: number | null;
  /** Each gene with a weak target. */
  weakGenes: { gene: string; targets: number | null; weakTargets: number | null; minMean: number | null }[];
}

/** A reported variant as the NIPT variant list classified it when the version was signed. */
export interface SignedNiptVariant {
  variantId: string | null;
  source: string | null;
  genes: string[];
  category: number | null;
  categoryLabel: string | null;
  maternalState: string | null;
  fetalInheritance: string | null;
  observedVaf: number | null;
  expectedVaf: number | null;
  confidence: number | null;
  paternalTransmissionProbability: number | null;
  maternalAlleleProbability: number | null;
  fetalHomAltProbability: number | null;
  flags: string[];
  plasma: { altReads: number | null; depth: number | null; depthEstimated: boolean };
  father: { state: string | null; vaf: number | null; depth: number | null };
  deNovo: { window: string | null; score: number | null; label: string | null; reasons: string[] } | null;
}

export interface SignedNiptRecessiveGene {
  gene: string | null;
  risk: number | null;
  riskUsesPrior: boolean;
  maternalVariantId: string | null;
  paternalVariantId: string | null;
}

/** A monogenic NIPT family's NIPT results, as its record froze them (`signed_record_nipt`). */
export interface SignedNipt {
  samples: { father: string | null; maternalPlasma: string | null; fetus: string | null };
  fetalFraction: SignedNiptFetalFraction | null;
  deNovoWindow: { strictMin: number | null; strictMax: number | null } | null;
  /** [category, count], in category order. */
  categoryCounts: [number, number][];
  filterCounts: Record<string, number>;
  /** [reason, count]: why cfDNA calls failed the quality filter. */
  qualityFailures: [string, number][];
  paternity: SignedNiptCheck | null;
  fetalSex: SignedNiptFetalSex | null;
  plasmaSample: SignedNiptCheck | null;
  targetCoverage: SignedNiptTargetCoverage | null;
  variants: SignedNiptVariant[];
  notClassified: { variantId: string; reason: string }[];
  recessiveGenes: SignedNiptRecessiveGene[];
  targetTables: { sample: string; file: string | null; targets: number | null; loadedAt: string | null }[];
  artifactList: {
    assayKey: string | null;
    entries: number | null;
    digest: string | null;
    removed: number | null;
    kept: { variantId: string; reason: string }[];
  } | null;
  modelReference: string | null;
}

export interface SignedReport {
  /** What the record says it is; null: signed before CoGA froze the sentence. */
  statement: string | null;
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
  /** What the analysis looked at (signed_record_sections.py). */
  variantDescriptions: SignedRecordSection<SignedVariantDescriptions>;
  genePanels: SignedRecordSection<SignedGenePanel[]>;
  referenceData: SignedRecordSection<SignedReferenceData>;
  repeatExpansions: SignedRecordSection<SignedRepeatExpansions>;
  paraphase: SignedRecordSection<SignedParaphase>;
  /** A PGT family's embryo calls at the ROI; `absent` from a record of any other family too. */
  pgtAnalysis: SignedRecordSection<SignedPgtAnalysis>;
  /** A monogenic NIPT family's NIPT results; `absent` from a record of any other family too. */
  nipt: SignedRecordSection<SignedNipt>;
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

// The consequence, frequencies and in-silico scores a small-variant classification's evidence
// freezes from its primary transcript, and its gene's missense Z (backend
// `small_variant_review_acmg.small_variant_evidence`), in the order a reviewer reads them, each
// with how the report writes it: an allele frequency as the variant description does, a count
// whole, a score to its digits, a consequence or a prediction as annotated.
interface EvidenceField {
  key: string;
  label: string;
  format: 'frequency' | 'count' | 'text' | number;
}

// The consequence term(s), the impact and LOFTEE's loss-of-function call, as the variant card
// names it (LoF HC).
const CONSEQUENCE_FIELDS: EvidenceField[] = [
  { key: 'effect', label: 'consequence', format: 'text' },
  { key: 'impact', label: 'impact', format: 'text' },
  { key: 'lof', label: 'LoF', format: 'text' },
];

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

// The gene's missense constraint the PP2 suggestion reads, from the gene's profile: frozen for a
// missense variant only.
const GENE_CONSTRAINT_FIELDS: EvidenceField[] = [{ key: 'gene_missense_z', label: 'gnomAD missense Z', format: 2 }];

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
          consequence: parseEvidenceValues(evidence.consequence, CONSEQUENCE_FIELDS),
          frequencies: parseEvidenceValues(evidence.frequencies, FREQUENCY_FIELDS),
          inSilico: parseEvidenceValues(evidence.in_silico, IN_SILICO_FIELDS),
          geneConstraint: parseEvidenceValues(evidence.gene_constraint, GENE_CONSTRAINT_FIELDS),
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
    application: text(value.application),
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

// --- What the analysis looked at ---------------------------------------------------

const UNREADABLE_SECTION = 'it cannot be read here; the downloaded record holds it as stored';

/** A section's state in a record: see `SignedRecordSection`. */
export const parseRecordSection = <T>(
  holder: Json,
  key: string,
  parse: (value: Json) => T,
): SignedRecordSection<T> => {
  if (!has(holder, key)) return { state: 'absent' };
  const value = holder[key];
  if (!isObject(value)) return { state: 'unavailable', reason: UNREADABLE_SECTION };
  const reason = text(value.unavailable);
  if (reason) return { state: 'unavailable', reason };
  return { state: 'recorded', value: parse(value) };
};

const numbers = (value: unknown): number[] | null =>
  Array.isArray(value) ? value.filter((item): item is number => number(item) !== null) : null;

const objects = (value: unknown): Json[] => (Array.isArray(value) ? value.filter(isObject) : []);

const parseGenotypes = (value: unknown): SignedGenotype[] =>
  objects(value).map((genotype) => ({
    sample: text(genotype.sample) ?? 'a sample',
    gt: text(genotype.gt),
    dp: number(genotype.dp),
    gq: number(genotype.gq),
    ad: numbers(genotype.ad),
    af: numbers(genotype.af),
    cn: number(genotype.cn),
    readSupport: number(genotype.read_support),
  }));

const parseVariantDescriptions = (value: Json): SignedVariantDescriptions => ({
  small: objects(value.small).map((entry) => ({
    variantId: text(entry.variant_id),
    source: text(entry.source),
    chrom: text(entry.chrom),
    start: number(entry.start),
    ref: text(entry.ref),
    alt: text(entry.alt),
    gene: text(entry.gene),
    transcript: text(entry.transcript),
    hgvsc: text(entry.hgvsc),
    hgvsp: text(entry.hgvsp),
    consequence: text(entry.consequence),
    impact: text(entry.impact),
    maneSelect: entry.mane_select === true,
    genotypes: parseGenotypes(entry.genotypes),
  })),
  structural: objects(value.structural).map((entry) => ({
    variantId: text(entry.variant_id),
    source: text(entry.source),
    svType: text(entry.sv_type),
    copyChange: text(entry.copy_change),
    chrom: text(entry.chrom),
    start: number(entry.start),
    end: number(entry.end),
    length: number(entry.length),
    remoteChrom: text(entry.remote_chrom),
    remoteStart: number(entry.remote_start),
    genes: strings(entry.genes),
    cytoband: text(entry.cytoband),
    genotypes: parseGenotypes(entry.genotypes),
  })),
});

const parseGenePanels = (value: Json): SignedGenePanel[] =>
  objects(value.applied).map((panel) => ({
    panelId: text(panel.panel_id),
    name: text(panel.name),
    version: number(panel.version),
    source: text(panel.source),
    geneCount: number(panel.gene_count),
    externalId: text(panel.external_id),
    externalVersion: text(panel.external_version),
    externalVersionCreated: text(panel.external_version_created),
    confidenceLevels: strings(panel.confidence_levels),
    sourceUpdatedAt: text(panel.source_updated_at),
    firstAppliedAt: text(panel.first_applied_at),
    firstAppliedBy: text(panel.first_applied_by),
  }));

const parseReferenceData = (value: Json): SignedReferenceData => ({
  geneReference: parseRecordSection(value, 'gene_reference', (part) => ({
    genes: strings(part.genes),
    sources: objects(part.sources).map((source) => ({
      source: text(source.source) ?? 'source',
      status: text(source.status),
      release: text(source.release),
      checksum: text(source.checksum),
      genes: strings(source.genes),
    })),
    notInReference: strings(part.not_in_reference),
  })),
  // A knowledgebase never loaded is frozen as null: recorded, as none.
  clinicalCnvs: !has(value, 'clinical_cnvs')
    ? { state: 'absent' }
    : value.clinical_cnvs === null
      ? { state: 'recorded', value: null }
      : parseRecordSection(value, 'clinical_cnvs', (part) => ({
          source: text(part.source),
          release: text(part.release),
          releaseDate: text(part.release_date),
          loadedAt: text(part.loaded_at),
          records: number(part.records),
        })),
});

const parseCatalogue = (value: unknown, entries: string): SignedCatalogue | null =>
  isObject(value)
    ? { entries: number(value[entries]), sources: strings(value.sources), digest: text(value.digest) }
    : null;

const parseRepeatExpansions = (value: Json): SignedRepeatExpansions => ({
  catalogue: parseCatalogue(value.catalogue, 'loci'),
  loci: objects(value.loci).map((locus) => {
    const thresholds = isObject(locus.thresholds) ? locus.thresholds : {};
    return {
      locusId: text(locus.locus_id),
      gene: text(locus.gene),
      disease: text(locus.disease),
      chrom: text(locus.chrom),
      start: number(locus.start),
      motif: text(locus.motif),
      thresholds: {
        intermediateMin: number(thresholds.intermediate_min),
        premutationMin: number(thresholds.premutation_min),
        pathogenicMin: number(thresholds.pathogenic_min),
      },
      status: text(locus.status) ?? 'unknown',
      calls: objects(locus.calls).map((call) => ({
        sample: text(call.sample) ?? 'a sample',
        genotype: text(call.genotype),
        status: text(call.status) ?? 'unknown',
        note: text(call.note),
        alleles: objects(call.alleles).map((allele) => ({
          repeatCount: number(allele.repeat_count),
          bpLength: number(allele.bp_length),
          status: text(allele.status) ?? 'unknown',
        })),
      })),
    };
  }),
});

const RULE_OPERATORS: Record<string, string> = { eq: '=', lte: '≤', gte: '≥', lt: '<', gt: '>' };

/** A Paraphase status rule as words: "status by smn1_cn: pathogenic = 0, carrier = 1, normal ≥ 2". */
const describeStatusRule = (rule: unknown): string | null => {
  if (!isObject(rule) || !text(rule.field)) return null;
  const parts = ['pathogenic', 'carrier', 'normal']
    .filter((status) => isObject(rule[status]))
    .map(
      (status) =>
        `${status} ${Object.entries(rule[status] as Json)
          .map(([operator, bound]) => `${RULE_OPERATORS[operator] ?? operator} ${String(bound)}`)
          .join(' and ')}`,
    );
  return `status by ${String(rule.field)}: ${parts.join(', ')}`;
};

const parseParaphase = (value: Json): SignedParaphase => ({
  catalogue: parseCatalogue(value.catalogue, 'regions'),
  genes: objects(value.genes).map((gene) => ({
    gene: text(gene.gene),
    region: text(gene.region),
    statusRule: describeStatusRule(gene.status_rule),
    results: objects(gene.samples).map((result) => ({
      sample: text(result.sample) ?? 'a sample',
      status: text(result.status),
      copyNumberSignal: result.copy_number_signal === true,
      copyNumber: objects(result.copy_number).map((metric) => ({
        label: text(metric.label) ?? text(metric.key) ?? 'copy number',
        value: number(metric.value),
      })),
    })),
  })),
});

const RISK_STATES: HaplotypeRiskState[] = ['affected_or_at_risk', 'carrier', 'unaffected_non_carrier', 'uninformative'];
const riskState = (value: unknown): HaplotypeRiskState | null =>
  RISK_STATES.includes(value as HaplotypeRiskState) ? (value as HaplotypeRiskState) : null;
const parentSide = (value: unknown): 'paternal' | 'maternal' | null =>
  value === 'paternal' || value === 'maternal' ? value : null;

const QC_VERDICT_STATUSES: SignedPgtQcVerdict['status'][] = ['pass', 'warning', 'not_counted'];
const QC_MEASURES: PgtEmbryoQcFindingOut['measure'][] = [
  'informative_sites',
  'mendel_rate',
  'paternal_markers',
  'maternal_markers',
];

/** An embryo's QC verdict as frozen; null when the record holds none (or none it can read). */
const parseQcVerdict = (value: unknown): SignedPgtQcVerdict | null => {
  if (!isObject(value)) return null;
  const status = QC_VERDICT_STATUSES.find((candidate) => candidate === value.status);
  if (!status) return null;
  const entries = Array.isArray(value.findings) ? (value.findings as unknown[]) : [];
  const findings = entries.flatMap((entry): PgtEmbryoQcFindingOut[] => {
    if (!isObject(entry)) return [];
    const measure = QC_MEASURES.find((candidate) => candidate === entry.measure);
    const found = number(entry.value);
    const threshold = number(entry.threshold);
    return measure && found !== null && threshold !== null ? [{ measure, value: found, threshold }] : [];
  });
  return {
    status,
    sides: (Array.isArray(value.sides) ? (value.sides as unknown[]) : []).flatMap((side) => {
      const read = parentSide(side);
      return read ? [read] : [];
    }),
    findings,
    unreadableFindings: entries.length - findings.length,
  };
};

const parseQcThresholds = (value: unknown): SignedPgtQcThresholds | null =>
  isObject(value)
    ? {
        minInformativeSites: number(value.min_informative_sites),
        maxMendelRate: number(value.max_mendel_rate),
        minMarkersPerSide: number(value.min_markers_per_side),
      }
    : null;

const parsePgtAnalysis = (value: Json): SignedPgtAnalysis => {
  const roi = isObject(value.region_of_interest) ? value.region_of_interest : null;
  const inheritance = isObject(value.inheritance_model) ? value.inheritance_model : {};
  const inputs = isObject(value.inputs) ? value.inputs : {};
  const rules = isObject(value.rules) ? value.rules : {};
  return {
    roi: roi
      ? {
          query: text(roi.query),
          label: text(roi.label),
          chrom: text(roi.chrom),
          start: number(roi.start),
          end: number(roi.end),
        }
      : null,
    inheritance: { recorded: text(inheritance.recorded), applied: text(inheritance.applied) },
    diseaseHaplotypeResolved: value.disease_haplotype_resolved === true,
    diseaseHaplotypes: objects(value.disease_haplotypes).map((haplotype) => ({
      origin: parentSide(haplotype.origin),
      homolog: text(haplotype.homolog),
      kind: text(haplotype.kind),
    })),
    phaseCorrections: objects(value.phase_corrections).flatMap((correction): HaplotypePhaseCorrection[] => {
      const parent = text(correction.parent);
      const chrom = text(correction.chr);
      const position = number(correction.position);
      if (!parent || !chrom || position === null) return [];
      return [
        {
          parent,
          side: correction.side === 'mother' ? 'mother' : 'father',
          chr: chrom,
          position,
          end: number(correction.end) ?? position,
          children_switching: number(correction.children_switching) ?? 0,
          children: number(correction.children) ?? 0,
        },
      ];
    }),
    embryos: objects(value.embryos).map((embryo) => {
      const sexDependent = isObject(embryo.sex_dependent) ? embryo.sex_dependent : null;
      const ifMale = riskState(sexDependent?.if_male);
      const ifFemale = riskState(sexDependent?.if_female);
      const qc = isObject(embryo.qc) ? embryo.qc : null;
      return {
        sample: text(embryo.sample) ?? 'an embryo',
        sex: text(embryo.sex),
        call: riskState(embryo.call),
        roiNotCovered: embryo.roi_not_covered === true,
        sexDependent: ifMale && ifFemale ? { ifMale, ifFemale } : null,
        inherited: objects(embryo.inherited).map((homolog) => ({
          origin: parentSide(homolog.origin),
          homolog: text(homolog.homolog),
          start: number(homolog.start),
          end: number(homolog.end),
        })),
        recombinations: numbers(embryo.recombinations) ?? [],
        qc: qc
          ? {
              informativeSites: number(qc.informative_sites),
              mendelErrors: number(qc.mendel_errors),
              mendelRate: number(qc.mendel_rate),
              paternalMarkers: number(qc.paternal_markers),
              maternalMarkers: number(qc.maternal_markers),
            }
          : null,
        qcVerdict: parseQcVerdict(embryo.qc_verdict),
      };
    }),
    members: objects(value.members).map((member) => ({
      sample: text(member.sample) ?? 'a member',
      role: text(member.role),
      sex: text(member.sex),
      affected: member.affected === true,
      carrierStatus: text(member.carrier_status),
      carrierType: text(member.carrier_type),
    })),
    sources: objects(inputs.haplotype_sources).map((source) => ({
      sample: text(source.sample) ?? 'a member',
      source: text(source.source),
      filename: text(source.filename),
      blocks: number(source.blocks),
      loadedAt: text(source.loaded_at),
    })),
    blocksRead: number(inputs.blocks_read),
    blocksDigest: text(inputs.blocks_digest),
    markersTruncated: inputs.markers_truncated === true,
    recombinationFlank: number(rules.recombination_flank),
    qcThresholds: parseQcThresholds(rules.qc_thresholds),
  };
};

const niptCheck = (value: unknown): SignedNiptCheck | null =>
  isObject(value) ? { status: text(value.status), message: text(value.message) } : null;

// A count keyed by name ({"low_quality": 3}), its counts only, in key order.
const countEntries = (value: unknown): [string, number][] =>
  isObject(value)
    ? Object.keys(value)
        .sort()
        .filter((key) => number(value[key]) !== null)
        .map((key): [string, number] => [key, number(value[key]) as number])
    : [];

const parseNiptVariant = (entry: Json): SignedNiptVariant => {
  const plasma = isObject(entry.plasma) ? entry.plasma : {};
  const father = isObject(entry.father) ? entry.father : {};
  const deNovo = isObject(entry.de_novo) ? entry.de_novo : null;
  return {
    variantId: text(entry.variant_id),
    source: text(entry.source),
    genes: strings(entry.genes),
    category: number(entry.category),
    categoryLabel: text(entry.category_label),
    maternalState: text(entry.maternal_state),
    fetalInheritance: text(entry.fetal_inheritance),
    observedVaf: number(entry.observed_vaf),
    expectedVaf: number(entry.expected_vaf),
    confidence: number(entry.confidence),
    paternalTransmissionProbability: number(entry.paternal_transmission_probability),
    maternalAlleleProbability: number(entry.maternal_allele_probability),
    fetalHomAltProbability: number(entry.fetal_hom_alt_probability),
    flags: strings(entry.flags),
    plasma: {
      altReads: number(plasma.alt_reads),
      depth: number(plasma.depth),
      depthEstimated: plasma.depth_estimated === true,
    },
    father: { state: text(father.state), vaf: number(father.vaf), depth: number(father.depth) },
    deNovo: deNovo
      ? {
          window: text(deNovo.window),
          score: number(deNovo.score),
          label: text(deNovo.label),
          reasons: strings(deNovo.reasons),
        }
      : null,
  };
};

const parseNipt = (value: Json): SignedNipt => {
  const samples = isObject(value.samples) ? value.samples : {};
  const ff = isObject(value.fetal_fraction) ? value.fetal_fraction : null;
  const checks = isObject(value.checks) ? value.checks : {};
  const sex = isObject(checks.fetal_sex) ? checks.fetal_sex : null;
  const window = isObject(value.de_novo_window) ? value.de_novo_window : null;
  const coverage = isObject(value.target_coverage) ? value.target_coverage : null;
  const inputs = isObject(value.inputs) ? value.inputs : {};
  const artifacts = isObject(inputs.artifact_list) ? inputs.artifact_list : null;
  const model = isObject(value.model) ? value.model : {};
  const filterCounts = Object.fromEntries(countEntries(value.filter_counts));
  return {
    samples: {
      father: text(samples.father),
      maternalPlasma: text(samples.maternal_plasma),
      fetus: text(samples.fetus),
    },
    fetalFraction: ff
      ? {
          status: text(ff.status),
          message: text(ff.message),
          ff: number(ff.ff),
          ffComputed: number(ff.ff_computed),
          ffMedian: number(ff.ff_median),
          ciLow: number(ff.ci_low),
          ciHigh: number(ff.ci_high),
          nSites: number(ff.n_sites),
          lowConfidence: ff.low_confidence === true,
        }
      : null,
    deNovoWindow: window ? { strictMin: number(window.strict_min), strictMax: number(window.strict_max) } : null,
    categoryCounts: countEntries(value.category_counts)
      .map(([category, count]): [number, number] => [Number(category), count])
      .filter(([category]) => Number.isFinite(category))
      .sort((a, b) => a[0] - b[0]),
    filterCounts,
    qualityFailures: countEntries(value.quality_failures),
    paternity: niptCheck(checks.paternity),
    fetalSex: sex
      ? { ...(niptCheck(sex) as SignedNiptCheck), call: text(sex.call), paternalX: text(sex.paternal_x), chry: text(sex.chry) }
      : null,
    plasmaSample: niptCheck(checks.plasma_sample),
    targetCoverage: coverage
      ? {
          targets: number(coverage.targets),
          medianMean: number(coverage.median_mean),
          belowCritical: number(coverage.below_critical),
          belowAdvisory: number(coverage.below_advisory),
          incomplete: number(coverage.incomplete),
          criticalMeanDepth: number(coverage.critical_mean_depth),
          weakGenes: objects(coverage.genes).map((gene) => ({
            gene: text(gene.gene) ?? 'a gene',
            targets: number(gene.targets),
            weakTargets: number(gene.weak_targets),
            minMean: number(gene.min_mean),
          })),
        }
      : null,
    variants: objects(value.variants).map(parseNiptVariant),
    notClassified: objects(value.not_classified).map((entry) => ({
      variantId: text(entry.variant_id) ?? 'a variant',
      reason: text(entry.reason) ?? 'unknown',
    })),
    recessiveGenes: objects(value.recessive_genes).map((gene) => ({
      gene: text(gene.gene),
      risk: number(gene.risk),
      riskUsesPrior: gene.risk_uses_prior === true,
      maternalVariantId: text(gene.maternal_variant_id),
      paternalVariantId: text(gene.paternal_variant_id),
    })),
    targetTables: objects(inputs.target_tables).map((table) => ({
      sample: text(table.sample) ?? 'a sample',
      file: text(table.file),
      targets: number(table.targets),
      loadedAt: text(table.loaded_at),
    })),
    artifactList: artifacts
      ? {
          assayKey: text(artifacts.assay_key),
          entries: number(artifacts.entries),
          digest: text(artifacts.digest),
          removed: number(artifacts.removed),
          kept: objects(artifacts.kept).map((entry) => ({
            variantId: text(entry.variant_id) ?? 'a variant',
            reason: text(entry.reason) ?? 'unknown',
          })),
        }
      : null,
    modelReference: text(model.reference),
  };
};

/** The signed version's frozen snapshot, read; null when there is no readable snapshot. */
export const parseSignedReport = (snapshot: unknown): SignedReport | null => {
  if (!isObject(snapshot)) return null;
  const software = isObject(snapshot.software) ? snapshot.software : null;
  return {
    statement: text(snapshot.record_statement),
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
    variantDescriptions: parseRecordSection(snapshot, 'variant_descriptions', parseVariantDescriptions),
    genePanels: parseRecordSection(snapshot, 'gene_panels', parseGenePanels),
    referenceData: parseRecordSection(snapshot, 'reference_data', parseReferenceData),
    repeatExpansions: parseRecordSection(snapshot, 'repeat_expansions', parseRepeatExpansions),
    paraphase: parseRecordSection(snapshot, 'paraphase', parseParaphase),
    pgtAnalysis: parseRecordSection(snapshot, 'pgt_analysis', parsePgtAnalysis),
    nipt: parseRecordSection(snapshot, 'nipt', parseNipt),
  };
};

/**
 * Whether the record is a monogenic NIPT family's: it holds the NIPT results, or its Sample QC
 * was run for a NIPT family (a record signed before CoGA froze the NIPT results). Any other
 * family's record holds no NIPT section, and the signed view says nothing of one.
 */
export const isNiptRecord = (record: SignedReport): boolean =>
  record.nipt.state !== 'absent' || record.sampleQc?.application === 'nipt';

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
  variant_descriptions: 'variant descriptions',
  gene_panels: 'gene panels applied',
  reference_data: 'reference-data releases',
  repeat_expansions: 'repeat expansions',
  paraphase: 'Paraphase results',
  pgt_analysis: 'PGT embryo results',
  nipt: 'NIPT results',
};

// --- Wording of what the analysis looked at --------------------------------------------

const joinNumbers = (values: number[] | null): string | null =>
  values && values.length ? values.join(',') : null;

/** One sample's genotype as reviewed: "PROBAND 0/1 (depth 30, AD 15,15)". */
export const describeGenotype = (genotype: SignedGenotype): string => {
  const details = [
    genotype.dp !== null ? `depth ${genotype.dp}` : null,
    genotype.gq !== null ? `GQ ${genotype.gq}` : null,
    joinNumbers(genotype.ad) ? `AD ${joinNumbers(genotype.ad)}` : null,
    joinNumbers(genotype.af) ? `VAF ${joinNumbers(genotype.af)}` : null,
    genotype.cn !== null ? `copy number ${genotype.cn}` : null,
    genotype.readSupport !== null ? `${genotype.readSupport} supporting reads` : null,
  ].filter((part): part is string => Boolean(part));
  return `${genotype.sample} ${genotype.gt ?? 'no call'}${details.length ? ` (${details.join(', ')})` : ''}`;
};

/** A small variant as reviewed: "GENE c.10C>T p.(Arg4Trp), missense_variant (MODERATE)". */
export const describeSmallVariantDescription = (description: SignedSmallVariantDescription): string => {
  const name = [description.gene ?? 'No gene', description.hgvsc, description.hgvsp]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  const consequence = description.consequence
    ? `, ${description.consequence.replace(/_/g, ' ')}${description.impact ? ` (${description.impact})` : ''}`
    : '';
  return `${name}${consequence}`;
};

/** Where it is, on which transcript, in which callset. */
export const describeSmallVariantLocus = (description: SignedSmallVariantDescription): string =>
  [
    description.chrom && description.start !== null
      ? `${description.chrom}:${description.start.toLocaleString()} ${description.ref ?? '?'} → ${description.alt ?? '?'}`
      : null,
    description.transcript
      ? `transcript ${description.transcript}${description.maneSelect ? ' (MANE Select)' : ''}`
      : null,
    description.source ? `callset ${description.source}` : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' · ');

const SV_TYPE_WORDS: Record<string, string> = {
  DEL: 'Deletion',
  DUP: 'Duplication',
  INS: 'Insertion',
  INV: 'Inversion',
  BND: 'Breakend',
  TRA: 'Translocation',
  CNV: 'Copy-number variant',
};

/** A structural variant as reviewed: "Deletion (copy loss), 1:7,000-12,000, 5,000 bp, genes BRCA2". */
export const describeStructuralVariantDescription = (
  description: SignedStructuralVariantDescription,
): string => {
  const type = description.svType ? (SV_TYPE_WORDS[description.svType.toUpperCase()] ?? description.svType) : 'Structural variant';
  const change = description.copyChange ? ` (copy ${description.copyChange})` : '';
  const locus =
    description.chrom && description.start !== null && description.end !== null
      ? `${description.chrom}:${description.start.toLocaleString()}-${description.end.toLocaleString()}`
      : null;
  const mate =
    description.remoteChrom && description.remoteStart !== null
      ? `mate ${description.remoteChrom}:${description.remoteStart.toLocaleString()}`
      : null;
  return [
    `${type}${change}`,
    locus,
    mate,
    description.length !== null ? `${description.length.toLocaleString()} bp` : null,
    description.genes.length ? `genes ${description.genes.join(', ')}` : 'no gene',
    description.cytoband,
    description.source ? `caller ${description.source}` : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(', ');
};

/** A panel version the analysis applied, and the release it was read from. */
export const describeGenePanel = (panel: SignedGenePanel): string => {
  const genes = panel.geneCount !== null ? `, ${panel.geneCount} gene${panel.geneCount === 1 ? '' : 's'}` : '';
  const head = `${panel.name ?? panel.panelId ?? 'A panel'}, version ${panel.version ?? '?'}${genes}`;
  let source = '';
  if (panel.source === 'panelapp') {
    source = `; PanelApp panel ${panel.externalId ?? '?'}, version ${panel.externalVersion ?? 'not stated'}${
      panel.confidenceLevels.length ? ` (confidence levels ${panel.confidenceLevels.join(', ')})` : ''
    }${panel.sourceUpdatedAt ? `, imported ${formatReportTime(panel.sourceUpdatedAt)}` : ''}`;
  } else if (panel.externalVersion) {
    source = `; ${panel.source ?? 'source'} release ${panel.externalVersion}`;
  }
  const first = panel.firstAppliedAt
    ? `; first applied ${formatReportTime(panel.firstAppliedAt)}${panel.firstAppliedBy ? ` by ${panel.firstAppliedBy}` : ''}`
    : '';
  return `${head}${source}${first}`;
};

const REPEAT_STATUS_WORDS: Record<string, string> = {
  normal: 'Normal',
  intermediate: 'Grey zone',
  premutation: 'Premutation',
  pathogenic: 'Pathogenic',
  review: 'Review: outside catalogued ranges',
  not_assessed: 'Not assessed',
  unknown: 'Unknown',
};

export const repeatStatusWords = (status: string): string => REPEAT_STATUS_WORDS[status] ?? status;

/** A locus's thresholds as the record froze them: "grey zone from 27, pathogenic from 36". */
export const describeRepeatThresholds = (locus: SignedRepeatLocus): string => {
  const parts = [
    locus.thresholds.intermediateMin !== null ? `grey zone from ${locus.thresholds.intermediateMin}` : null,
    locus.thresholds.premutationMin !== null ? `premutation from ${locus.thresholds.premutationMin}` : null,
    locus.thresholds.pathogenicMin !== null ? `pathogenic from ${locus.thresholds.pathogenicMin}` : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(', ') : 'no thresholds';
};

/** One sample's call: "PROBAND 0/1: 18, 40 repeats — Pathogenic". */
export const describeRepeatCall = (call: SignedRepeatCall): string => {
  const sizes = call.alleles
    .map((allele) => (allele.repeatCount !== null ? String(allele.repeatCount) : allele.bpLength !== null ? `${allele.bpLength} bp` : '?'))
    .join(', ');
  return `${call.sample} ${call.genotype ?? 'no call'}${sizes ? `: ${sizes} repeats` : ''} — ${repeatStatusWords(call.status)}${
    call.note ? ` (${call.note})` : ''
  }`;
};

const PARAPHASE_STATUS_WORDS: Record<string, string> = {
  pathogenic: 'Pathogenic',
  carrier: 'Carrier',
  normal: 'Normal',
  review: 'Review',
  no_call: 'No-call',
  none: 'No change',
};

export const paraphaseStatusWords = (status: string | null): string =>
  status ? (PARAPHASE_STATUS_WORDS[status] ?? status) : 'Not assessed';

const GENE_REFERENCE_SOURCES: Record<string, string> = {
  hgnc_complete_set: 'HGNC',
  dbnsfp_gene: 'dbNSFP',
  clingen_gene_validity: 'ClinGen validity',
  clingen_dosage: 'ClinGen dosage',
  gencc: 'GenCC',
  clinvar_gene_condition: 'ClinVar gene-condition',
  ncbi: 'NCBI Gene',
};

export const geneReferenceSourceLabel = (source: string): string =>
  GENE_REFERENCE_SOURCES[source] ?? source;

/** Each gene-reference source and the releases that answered: "HGNC 2026-09-01 · dbNSFP 5.4". */
export const describeGeneReferenceReleases = (reference: SignedGeneReference): string | null => {
  const releases = new Map<string, Set<string>>();
  reference.sources.forEach((source) => {
    if (!source.release) return;
    const label = geneReferenceSourceLabel(source.source);
    releases.set(label, (releases.get(label) ?? new Set()).add(source.release));
  });
  return releases.size
    ? Array.from(releases.entries())
        .map(([label, values]) => `${label} ${joinWithAnd(Array.from(values))}`)
        .join(' · ')
    : null;
};

/** The clinical CNV knowledgebase as signed: "loaded 2026-10-01 09:00 UTC (release not stated)". */
export const describeCnvKnowledgebase = (knowledgebase: SignedCnvKnowledgebase | null): string =>
  knowledgebase
    ? `${knowledgebase.source ?? 'source not named'}${
        knowledgebase.loadedAt ? `, loaded ${formatReportTime(knowledgebase.loadedAt)}` : ''
      } (release ${knowledgebase.release ?? 'not stated'}${
        knowledgebase.releaseDate ? `, ${knowledgebase.releaseDate}` : ''
      })`
    : 'none loaded';

/** A catalogue as the record names it: "75 loci (STRchive, CoGA built-in), digest 1a2b3c4d…". */
export const describeCatalogue = (catalogue: SignedCatalogue | null, noun: string): string =>
  catalogue
    ? `${catalogue.entries ?? '?'} ${noun}${catalogue.sources.length ? ` (${catalogue.sources.join(', ')})` : ''}${
        catalogue.digest ? `, digest ${catalogue.digest.slice(0, 12)}` : ''
      }`
    : 'not recorded';

// --- Wording of a NIPT family's NIPT results -------------------------------------------

const sitesWord = (count: number | null): string =>
  count === null ? 'an unrecorded number of category-7 sites' : `${formatCount(count)} category-7 site${count === 1 ? '' : 's'}`;

/** The fetal fraction as signed: "12.0% (95% CI 10.9%–13.1%) from 40 category-7 sites; per-site median 11.8%". */
export const describeNiptFetalFraction = (ff: SignedNiptFetalFraction): string => {
  if (ff.ffComputed === null) return `not estimated, from ${sitesWord(ff.nSites)}`;
  const interval = ff.ciLow !== null && ff.ciHigh !== null ? ` (95% CI ${pct(ff.ciLow)}–${pct(ff.ciHigh)})` : '';
  const median = ff.ffMedian !== null ? `; per-site median ${pct(ff.ffMedian)}` : '';
  return `${pct(ff.ffComputed)}${interval} from ${sitesWord(ff.nSites)}${median}`;
};

/** The fetal sex as signed, with what each signal read. */
export const describeNiptFetalSex = (sex: SignedNiptFetalSex): string => {
  const call = sex.call ?? 'not recorded';
  return `${call.charAt(0).toUpperCase()}${call.slice(1)} (the father's X alleles read ${
    sex.paternalX ?? 'nothing'
  }; the plasma's chrY coverage reads ${sex.chry ?? 'nothing'})`;
};

/** The quality and artifact filters as signed: "48 cfDNA calls: 1 quality-filtered, 1 artifact-filtered, 46 analysed". */
export const describeNiptFunnel = (counts: Record<string, number>): string => {
  const parts = [
    `${formatCount(counts.total_in ?? 0)} cfDNA calls: ${formatCount(counts.failed_quality ?? 0)} quality-filtered`,
    `${formatCount(counts.failed_artifact ?? 0)} artifact-filtered`,
    `${formatCount(counts.passed ?? 0)} analysed`,
  ];
  const paternalOnly = counts.paternal_only ?? 0;
  return `${parts.join(', ')}${
    paternalOnly ? `; ${formatCount(paternalOnly)} paternal allele${paternalOnly === 1 ? '' : 's'} without a plasma call of ${paternalOnly === 1 ? 'its' : 'their'} own` : ''
  }`;
};

/** The target coverage as signed: "median 800x over 60,000 targets; 1,500 below 300x, 1,000 with uncovered bases". */
export const describeNiptCoverage = (coverage: SignedNiptTargetCoverage): string =>
  `median ${depth(coverage.medianMean)} over ${formatCount(coverage.targets ?? 0)} targets; ${formatCount(
    coverage.belowCritical ?? 0,
  )} below ${depth(coverage.criticalMeanDepth)}, ${formatCount(coverage.incomplete ?? 0)} with uncovered bases`;

/** A reported variant's NIPT classification as signed, worded as the NIPT page words it. */
export const describeNiptVariant = (variant: SignedNiptVariant): string => {
  const category =
    variant.category !== null
      ? `Category ${variant.category}${variant.categoryLabel ? ` (${variant.categoryLabel})` : ''}`
      : `Unclassified${variant.categoryLabel ? ` (${variant.categoryLabel})` : ''}`;
  const confidence = variant.confidence !== null ? `, confidence ${variant.confidence.toFixed(2)}` : '';
  const father = variant.father.state
    ? `; father ${FATHER_STATE_LABELS[variant.father.state] ?? variant.father.state}${
        variant.father.vaf !== null ? ` · ${pct(variant.father.vaf)}` : ''
      }${variant.father.depth !== null ? ` · ${variant.father.depth}×` : ''}`
    : '';
  const reads = readsText({
    cf_alt_reads: variant.plasma.altReads,
    cf_depth: variant.plasma.depth,
    cf_depth_estimated: variant.plasma.depthEstimated,
  });
  const evidence = niptEvidenceSummary({
    paternal_transmission_probability: variant.paternalTransmissionProbability,
    maternal_allele_probability: variant.maternalAlleleProbability,
    fetal_hom_alt_probability: variant.fetalHomAltProbability,
    de_novo:
      variant.deNovo && variant.deNovo.label && variant.deNovo.score !== null
        ? { label: variant.deNovo.label, score: variant.deNovo.score }
        : null,
  });
  return `${category}${confidence}: maternal ${variant.maternalState ?? 'n/a'}, fetal ${
    variant.fetalInheritance ?? 'n/a'
  }; VAF ${pct(variant.observedVaf)} (expected ${pct(variant.expectedVaf)}); plasma reads ${reads}${father}${
    evidence ? `. ${evidence}` : ''
  }.`;
};

/** Why a reported variant has no NIPT classification in the record. */
export const NIPT_NOT_CLASSIFIED_REASONS: Record<string, string> = {
  not_in_data: 'it was not in the family’s NIPT data',
  artifact_list: 'it is on the assay’s recurrent-artifact list, which the analysis leaves out',
  no_plasma_call: 'the plasma has no call of it, so the analysis does not classify it',
};

/** The sections the sign-out check names as changed: "reported small variants and …". */
export const describeReportSections = (sections: string[]): string =>
  joinWithAnd(sections.map((section) => REPORT_SECTION_LABELS[section] ?? section));

/** A QC verdict in the words the Sample QC page uses. */
export const qcStatusLabel = (status: string | null): string =>
  status ? (QC_STATUS_LABEL[status as QcStatus] ?? status) : 'not recorded';

/**
 * The frozen consequence, frequencies and in-silico scores of a classification and its gene's
 * missense Z, as the signed view writes them: "Read from its primary transcript ENST…:
 * consequence stop_gained, impact HIGH and LoF HC; population frequencies gnomAD AF 3.10e-5, …;
 * in silico scores CADD 24.3, …. From its gene's profile: gnomAD missense Z 3.52." A group
 * the record does not hold (frozen before CoGA froze it) is left out; null when it holds none.
 */
export const describeFrozenEvidenceValues = (evidence: SignedEvidence): string | null => {
  const list = (values: SignedEvidenceValue[]) =>
    joinWithAnd(values.map((entry) => `${entry.label} ${entry.value ?? 'not annotated'}`));
  const groups = [
    evidence.consequence?.length ? list(evidence.consequence) : null,
    evidence.frequencies?.length ? `population frequencies ${list(evidence.frequencies)}` : null,
    evidence.inSilico?.length ? `in silico scores ${list(evidence.inSilico)}` : null,
  ].filter((group): group is string => group !== null);
  const sentences = [
    groups.length
      ? `Read from its primary transcript${evidence.transcriptId ? ` ${evidence.transcriptId}` : ''}: ${groups.join('; ')}.`
      : null,
    evidence.geneConstraint?.length ? `From its gene's profile: ${list(evidence.geneConstraint)}.` : null,
  ].filter((sentence): sentence is string => sentence !== null);
  return sentences.length ? sentences.join(' ') : null;
};

/**
 * What moved in a small-variant classification's frozen values ("consequence stop_gained →
 * stop_gained&splice_region_variant; REVEL 0.412 → 0.951; gnomAD missense Z 2.95 → 3.52");
 * null when none of them moved. The live report and the signed version both describe drift
 * this way.
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
  [...CONSEQUENCE_FIELDS, ...FREQUENCY_FIELDS, ...IN_SILICO_FIELDS, ...GENE_CONSTRAINT_FIELDS]
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

// --- Wording of the PGT embryo results ---------------------------------------------

/** A homolog as the haplotype track's legend names it: P1/P2 the father's, M1/M2 the mother's. */
export const homologLabel = (haplotype: SignedPgtHaplotype): string => {
  const side = haplotype.origin === 'paternal' ? 'P' : haplotype.origin === 'maternal' ? 'M' : '?';
  return haplotype.homolog !== null && /^[0-9]+$/.test(haplotype.homolog)
    ? `${side}${Number(haplotype.homolog) + 1}`
    : `${side}?`;
};

const DISEASE_KIND_WORDS: Record<string, string> = {
  dominant: 'dominant',
  'recessive-paternal': 'recessive, the father’s side',
  'recessive-maternal': 'recessive, the mother’s side',
  'x-linked': 'X-linked',
};

/** The disease haplotype(s) the calls were made against: "P2 (dominant)"; null: none resolved. */
export const describeDiseaseHaplotypes = (analysis: SignedPgtAnalysis): string | null =>
  analysis.diseaseHaplotypeResolved && analysis.diseaseHaplotypes.length
    ? joinWithAnd(
        analysis.diseaseHaplotypes.map(
          (haplotype) =>
            `${homologLabel(haplotype)} (${haplotype.kind ? (DISEASE_KIND_WORDS[haplotype.kind] ?? haplotype.kind) : 'kind not recorded'})`,
        ),
      )
    : null;

/** The model the calls used: "AD", or "not recorded; CoGA assumed AR". */
export const describePgtInheritance = (analysis: SignedPgtAnalysis): string =>
  analysis.inheritance.recorded
    ? analysis.inheritance.recorded
    : `not recorded; CoGA assumed ${analysis.inheritance.applied ?? 'none'}`;

const formatPosition = (value: number | null): string => (value === null ? '?' : value.toLocaleString());

/** The homologs an embryo carries at the ROI, block by block: "P2 and M1 (29,000,000–32,000,000)". */
export const describeInherited = (inherited: SignedPgtInherited[]): string | null => {
  if (!inherited.length) return null;
  const blocks = new Map<string, { start: number | null; end: number | null; labels: string[] }>();
  inherited.forEach((homolog) => {
    const key = `${homolog.start}-${homolog.end}`;
    const block = blocks.get(key) ?? { start: homolog.start, end: homolog.end, labels: [] };
    block.labels.push(homologLabel(homolog));
    blocks.set(key, block);
  });
  return Array.from(blocks.values())
    .map((block) => `${joinWithAnd(block.labels)} (${formatPosition(block.start)}–${formatPosition(block.end)})`)
    .join('; ');
};

/** Why an embryo's call is uninformative, in the words of the family page's warning. */
export const describeNoCall = (embryo: SignedPgtEmbryo, analysis: SignedPgtAnalysis): string | null => {
  if (embryo.call !== 'uninformative') return null;
  if (embryo.sexDependent) return sexUnknownWarning(embryo.sexDependent);
  if (embryo.roiNotCovered) {
    return 'This embryo’s haplotype does not cover the ROI on the parental side the call needs (missing data), so no call can be made.';
  }
  return analysis.diseaseHaplotypeResolved
    ? 'No call could be made against the disease haplotypes resolved at the ROI.'
    : 'No disease haplotype could be resolved at the ROI (uninformative markers).';
};

/** An embryo's phasing QC at the ROI: "40 informative sites, Mendelian error rate 2.5% (1 of 40); …". */
export const describeEmbryoQc = (qc: SignedPgtEmbryoQc): string => {
  const sites = qc.informativeSites ?? 0;
  const rate = qc.mendelRate !== null ? `${(qc.mendelRate * 100).toFixed(1)}%` : 'not recorded';
  return `${sites.toLocaleString()} informative site${sites === 1 ? '' : 's'}, Mendelian error rate ${rate} (${(
    qc.mendelErrors ?? 0
  ).toLocaleString()} of ${sites.toLocaleString()}); informative markers ${qc.paternalMarkers ?? 0} paternal, ${
    qc.maternalMarkers ?? 0
  } maternal`;
};

/**
 * The thresholds the record's embryo QC was checked against: "at least 50 informative sites, a
 * Mendelian error rate of at most 2% and at least 6 markers on each parental side the call reads".
 */
export const describeQcThresholds = (thresholds: SignedPgtQcThresholds): string => {
  const parts = [
    thresholds.minInformativeSites !== null
      ? `at least ${thresholds.minInformativeSites.toLocaleString()} informative sites`
      : null,
    thresholds.maxMendelRate !== null ? `a Mendelian error rate of at most ${formatQcRate(thresholds.maxMendelRate)}` : null,
    thresholds.minMarkersPerSide !== null
      ? `at least ${thresholds.minMarkersPerSide.toLocaleString()} markers on each parental side the call reads`
      : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length ? joinWithAnd(parts) : 'thresholds the record does not state';
};

/**
 * An embryo's QC verdict as signed: that its phasing QC at the ROI met the thresholds, did not
 * (each measure, its value and the threshold), or was not counted, so the call was not checked.
 */
export const describeSignedQcVerdict = (verdict: SignedPgtQcVerdict): string => {
  if (verdict.status === 'pass') return `The phasing QC at the ROI met its thresholds (${describeQcSides(verdict.sides)}).`;
  if (verdict.status === 'not_counted') return '⚠ The call was not checked against the QC thresholds.';
  const findings = verdict.findings.length ? `: ${describeQcFindings(verdict)}` : '';
  const unreadable = verdict.unreadableFindings
    ? ` ${verdict.unreadableFindings} measure${verdict.unreadableFindings === 1 ? '' : 's'} of it cannot be read here; the downloaded record holds ${
        verdict.unreadableFindings === 1 ? 'it' : 'them'
      } as stored.`
    : '';
  return `⚠ The phasing QC at the ROI did not meet its thresholds (${describeQcSides(verdict.sides)})${findings}.${unreadable}`;
};

/** An embryo's call as signed: its badge's words, or that it was not assessed. */
export const describeEmbryoCall = (embryo: SignedPgtEmbryo): string =>
  embryo.call ? segregationStateLabel(embryo.call) : 'Not assessed';

/** What the calls were read from: the haplotype files, the blocks read and their digest. */
export const describePgtInputs = (analysis: SignedPgtAnalysis): string => {
  const files = new Map<string, string[]>();
  analysis.sources.forEach((source) => {
    const name = `${source.filename || 'a file not named'}${source.loadedAt ? `, loaded ${formatReportTime(source.loadedAt)}` : ''}`;
    files.set(name, [...(files.get(name) ?? []), source.sample]);
  });
  const parts = [
    files.size
      ? `the haplotype blocks of ${Array.from(files.entries())
          .map(([name, samples]) => `${name} (${joinWithAnd(samples)})`)
          .join('; ')}`
      : 'no recorded haplotype file',
    analysis.blocksRead !== null
      ? `${analysis.blocksRead.toLocaleString()} block${analysis.blocksRead === 1 ? '' : 's'} read${
          analysis.blocksDigest ? `, digest ${analysis.blocksDigest.slice(0, 12)}` : ''
        }`
      : null,
  ].filter((part): part is string => Boolean(part));
  return parts.join('; ');
};

/** The members' statuses the disease haplotype was inferred from: "FATHER (father, affected)". */
export const describePgtMembers = (members: SignedPgtMember[]): string | null =>
  members.length
    ? members
        .map((member) => {
          const status = [
            member.role,
            member.affected ? 'affected' : null,
            member.carrierStatus === 'carrier'
              ? `${member.carrierType ? `${member.carrierType} ` : ''}carrier`
              : member.carrierStatus === 'not_carrier'
                ? 'not a carrier'
                : null,
          ].filter((part): part is string => Boolean(part));
          return `${member.sample}${status.length ? ` (${status.join(', ')})` : ''}`;
        })
        .join(', ')
    : null;

// --- A live report beside the latest signed version -------------------------------

/**
 * How a live report (the family report's, or the NIPT report's) relates to the family's
 * latest signed version. It is never the signed report, whatever the state: only the signed
 * version, rendered from its record, is (#508).
 */
export type LiveReportSignedState =
  | 'loading'
  | 'none'
  | 'checking'
  | 'matches'
  | 'changed'
  | 'unverified'
  | 'unknown';

export const liveReportSignedState = ({
  signoutsFailed,
  signoutsPending,
  latestVersion,
  checkFailed,
  check,
}: {
  signoutsFailed: boolean;
  signoutsPending: boolean;
  latestVersion: number | null;
  checkFailed: boolean;
  check: { version?: number | null; matches?: boolean | null } | undefined;
}): LiveReportSignedState =>
  // A sign-out record that could not be loaded leaves it unknown whether the case is
  // signed: the page is then neither a draft nor the signed report (#605). Still loading,
  // it is not known either: not yet a draft (#605).
  signoutsFailed
    ? 'unknown'
    : signoutsPending
      ? 'loading'
      : latestVersion === null
        ? 'none'
        : checkFailed
          ? 'unverified'
          : !check
            ? 'checking'
            : check.version !== latestVersion || typeof check.matches !== 'boolean'
              ? 'unverified'
              : check.matches
                ? 'matches'
                : 'changed';

/**
 * The notice that heads every printout of a live report: even content that matches the
 * latest signed version is drawn from current data, which the signed record does not all hold.
 */
export const liveReportSignedNotice = (state: LiveReportSignedState, latestVersion: number | null): string =>
  state === 'unknown'
    ? 'The sign-out record could not be loaded — do not use as the signed report.'
    : state === 'loading'
      ? 'The sign-out record is still loading — do not use as the signed report.'
      : state === 'none'
        ? 'Draft — this report has not been signed.'
        : state === 'changed'
          ? `Not the signed report — the content differs from signed version ${latestVersion}.`
          : state === 'matches'
            ? `Not the signed report — this is the live report. Print signed version ${latestVersion} from its record.`
            : `Not verified against signed version ${latestVersion} — do not use as the signed report.`;

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
