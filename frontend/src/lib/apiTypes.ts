// The frontend's hand-written API types. A response is read through its generated type
// (apiSchema.generated.ts) where it has one: what stays here are the types apiContract.ts
// checks against theirs, aliases of generated types, and those with no generated equivalent,
// each saying why.
import type {
  FamilyOut,
  IdeogramBandOut,
  NiptQcOut,
  NiptTargetCoverageOut,
  ProjectDashboardOut,
  QcMetricCatalogueOut,
  RepeatCountUnitWarningOut,
  RepeatExpansionRowOut,
  ReportSignoutSummary,
  SampleIntegrityPaternityCheckOut,
  TrackAvailabilityOut,
} from './apiSchema.generated';

/**
 * A response the backend serialises without its null fields (`exclude_none`): each field that
 * can be null is left out instead, at every depth. The SV list answers so in track mode
 * (`track_mode=true`), to keep the genome tracks' payload small.
 */
export type WithoutNulls<T> = T extends (infer Item)[]
  ? WithoutNulls<Item>[]
  : T extends object
    ? { [K in keyof T as null extends T[K] ? never : K]: WithoutNulls<T[K]> } & {
        [K in keyof T as null extends T[K] ? K : never]?: WithoutNulls<Exclude<T[K], null>>;
      }
    : T;

export interface ApiFamilyMember {
  sample_id: string;
  role: string;
  affected: boolean;
  sex: string;
  clinical_status?: 'unknown' | 'unaffected' | 'affected';
  carrier_status?: 'unknown' | 'not_carrier' | 'carrier';
  carrier_type?: 'obligate' | 'proven' | 'reported' | 'inferred' | null;
  carrier_evidence?: Record<string, unknown>;
  active?: boolean;
  sample_metadata?: Record<string, unknown>;
  // Null when the sample has no sequencing QC to judge (the backend sends null, #528).
  sequencing_qc?: ApiSampleSequencingQcEvaluation | null;
}

/** A sequencing-QC metric judged against its configured cut-offs. */
export interface ApiSampleSequencingQcMetric {
  metric_key: string;
  label: string;
  unit: string;
  direction: 'lower_is_worse' | 'higher_is_worse';
  value: number;
  warn_value?: number | null;
  error_value?: number | null;
  /** `skip` = no cut-off configured, i.e. not checked — distinct from `pass`. Shares
   *  the QcStatus vocabulary used by sample-integrity QC. */
  verdict: QcStatus;
}

export interface ApiSampleSequencingQcEvaluation {
  verdict: QcStatus;
  metrics: ApiSampleSequencingQcMetric[];
  breached: string[];
  profile_key?: string | null;
  profile_label?: string | null;
}

export interface ApiQcThreshold {
  metric_key: string;
  label: string;
  unit: string;
  direction: 'lower_is_worse' | 'higher_is_worse';
  known_metric: boolean;
  warn_value?: number | null;
  error_value?: number | null;
}

export interface ApiQcThresholdProfile {
  id: string;
  key: string;
  label: string;
  description?: string | null;
  is_default: boolean;
  sort_order: number;
  thresholds: ApiQcThreshold[];
}

/** One entry of the append-only cut-off history, with both sides of the edit. */
export interface ApiQcThresholdChange {
  profile_key: string;
  metric_key: string;
  previous_warn_value?: number | null;
  previous_error_value?: number | null;
  warn_value?: number | null;
  error_value?: number | null;
  changed_by_email?: string | null;
  changed_at: string;
  /** Why the cut-off moved; null only for edits made before it was required. */
  reason?: string | null;
}

export interface ApiQcThresholdCatalogue {
  metrics: QcMetricCatalogueOut[];
  profiles: ApiQcThresholdProfile[];
  changes: ApiQcThresholdChange[];
}

export interface ApiFamilyMemberImpact {
  sample_id: string;
  pedigree_references: Record<string, number>;
  data_counts: Record<string, number>;
  affected_analysis_scopes: string[];
  stale_analysis_scopes: string[];
  warnings: string[];
  destructive: boolean;
  requires_manual_recalculation: boolean;
}

export interface ApiFamilyMemberDetail {
  member: ApiFamilyMember;
  father_id?: string | null;
  mother_id?: string | null;
  hpo_annotations: ApiHpoAnnotation[];
  impact: ApiFamilyMemberImpact;
}

export interface ApiFamilyMemberBatchUpdateItem {
  sample_id: string;
  new_sample_id?: string | null;
  sex?: 'male' | 'female' | 'und' | null;
  role?: 'proband' | 'father' | 'mother' | 'sibling' | 'embryo' | 'relative' | null;
  clinical_status?: 'unknown' | 'unaffected' | 'affected' | null;
  carrier_status?: 'unknown' | 'not_carrier' | 'carrier' | null;
  carrier_type?: 'obligate' | 'proven' | 'reported' | 'inferred' | null;
  father_id?: string | null;
  mother_id?: string | null;
}

export interface ApiFamilyRelationship {
  id: string;
  /** `relative`: `sample_id_b` is related to the family through `sample_id_a`, by an unknown degree. */
  relationship_type: 'parent_child' | 'couple' | 'relative';
  sample_id_a: string;
  sample_id_b: string;
  role_a?: string | null;
  role_b?: string | null;
  source?: string;
  metadata?: Record<string, unknown>;
}

export interface ApiFamilyStructureVersion {
  version: number;
  structure_hash?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ApiFamilyRegionOfInterest {
  query: string;
  label: string;
  source: 'gene' | 'region';
  assembly_id?: string | null;
  chr: string;
  start: number;
  end: number;
}

export interface ApiUserRef {
  id: string;
  username: string;
  email: string;
  first_name?: string;
  last_name?: string;
}

/** Compact status reference embedded in a family record. */
export interface ApiFamilyStatusRef {
  key: string;
  label: string;
  color: string;
}

// A family as the family endpoints serve it (FamilyOut).
export type ApiFamilyRecord = FamilyOut;

export interface ApiHpoTerm {
  hpo_id: string;
  label: string;
  definition?: string | null;
  is_obsolete?: boolean;
}

export interface ApiHpoAnnotation {
  id: string;
  sample_id: string;
  hpo_id: string;
  label: string;
  definition?: string | null;
  status: 'present' | 'absent' | 'unknown';
  onset?: string | null;
  evidence?: string | null;
  source: string;
  note?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiSmallVariantReviewSummary {
  reviewed_variant_count: number;
  note_count: number;
  tag_counts: Record<string, number>;
}

export interface ApiNiptFetalFraction {
  ff: number;
  ff_computed?: number | null;
  ff_median?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  n_sites: number;
  method: string;
  low_confidence: boolean;
  // The 5th / 95th percentile of the FF sites' allele fractions (the de novo window).
  vaf_q05?: number | null;
  vaf_q95?: number | null;
}

export interface ApiNiptSummary {
  family_id: string;
  fetal_fraction: ApiNiptFetalFraction;
  // Category counts are keyed by the category number (1-8), serialized as strings.
  category_counts: Record<string, number>;
  filter_counts: Record<string, number>;
  // Paternity, fetal sex, the plasma's sex profile, target coverage and the model.
  qc?: NiptQcOut | null;
}

export interface ApiNiptCoverageRegion {
  label: string;
  chr: string;
  start: number;
  end: number;
  median_coverage?: number | null;
  covered_bases: number;
  target_bases: number;
}

// The reasons the backend writes; it serves `NiptCoverageLowRegionOut.reason` as a string.
export type NiptLowCoverageReason = 'no_coverage' | 'low_depth' | 'partial_coverage';

export interface ApiNiptCoverageLowRegion {
  label: string;
  chr: string;
  median_coverage?: number | null;
  covered_fraction: number;
  reason: NiptLowCoverageReason;
}

export interface ApiNiptCoverageSummary {
  family_id: string;
  overall_median_on_target?: number | null;
  target_region_count: number;
  per_region: ApiNiptCoverageRegion[];
  min_depth: number;
  min_covered_fraction: number;
  low_coverage_regions?: ApiNiptCoverageLowRegion[];
  // The capture targets' QC, when the plasma has a per-target coverage table.
  targets?: NiptTargetCoverageOut | null;
  // Without a table: why each of the plasma's coverage tracks over the regions was not read
  // as depth (TRACE-9): a log2 ratio, or a coverage BED that does not declare what it holds.
  coverage_notes?: string[];
}

// The vocabulary the QC verdicts share. The sequencing-QC verdict is served as this union
// (SampleSequencingQcMetricOut.verdict); the sample-integrity statuses as strings of it.
export type QcStatus = 'pass' | 'warn' | 'fail' | 'skip';

export interface ApiSampleIntegritySexCheck {
  sample_id: string;
  recorded_sex: string;
  inferred_sex: string;
  x_het_rate?: number | null;
  x_sites: number;
  status: QcStatus;
  message: string;
}

export interface ApiSampleIntegrityRelatednessCheck {
  sample_a: string;
  sample_b: string;
  expected_relationship: string;
  inferred_relationship: string;
  kinship: number;
  ibs0_rate: number;
  informative_sites: number;
  status: QcStatus;
  message: string;
}

export interface ApiSampleIntegrityMendelianCheck {
  child: string;
  parents: string[];
  informative_sites: number;
  mendel_errors: number;
  mendel_rate: number;
  status: QcStatus;
  message: string;
}

// The NIPT paternity check as served (its generated type), its status read as a QC verdict.
export type ApiSampleIntegrityPaternityCheck = Omit<SampleIntegrityPaternityCheckOut, 'status'> & {
  status: QcStatus;
};

export interface ApiSampleIntegrityFetalSexCheck {
  inferred_sex: string;
  x_transmitted: number;
  x_not_transmitted: number;
  informative_sites: number;
  status: QcStatus;
  message: string;
}

export interface ApiSampleIntegrityCategoryQc {
  denovo: number;
  paternal_absent: number;
  maternal_informative: number;
  maternal_inherited: number;
  maternal_inherited_rate: number;
  status: QcStatus;
  message: string;
}

// The applications the backend names; it serves `SampleIntegrityQcOut.application` as a string.
export type SampleQcApplication = 'wgs' | 'pgt' | 'nipt' | 'couple' | 'single' | 'unknown';

export interface ApiAnnotationModule {
  key: string;
  label: string;
  version: string | null;
  detail: string | null;
  layer: string; // 'pipeline' | 'reference'
  // Per-modality versions (issue #294): { modality: version } when a database was
  // cited at different releases by different pipelines (e.g. SNV vs SV GENCODE).
  by_modality: Record<string, string> | null;
}

export interface ApiAnnotationManifest {
  family_id: string;
  assembly: string | null;
  source: string | null;
  recorded_at: string | null;
  recorded_by: string | null;
  modules: ApiAnnotationModule[];
}

// The primary transcript, its consequence, the population frequencies and in-silico scores, and
// (for a missense variant) the gene's missense Z a small-variant classification rested on, as
// frozen and as they are now, by field (backend
// services/small_variant_review_acmg.py::small_variant_evidence).
export type ApiSmallVariantEvidence = Record<string, unknown>;

export interface ApiClassificationDriftItem {
  variant_id: string;
  acmg_class: string | null;
  classified_by: string | null;
  classified_at: string | null;
  status: string; // 'drifted' | 'unknown' | 'variant_missing'
  annotation_version_from: string | null;
  annotation_version_to: string | null;
  clinvar_from: string | null;
  clinvar_to: string | null;
  // What moved: 'clinvar', 'transcript_id', a consequence, frequency or in-silico field
  // ('effect', 'lof', 'gnomad_af', 'revel', …), a missense variant's gene 'gene_missense_z' or
  // 'annotations' (the annotation set).
  changed: string[];
  evidence_from: ApiSmallVariantEvidence | null;
  evidence_to: ApiSmallVariantEvidence | null;
}

// The evidence a structural-variant / CNV classification rested on, as frozen and as it
// is now (backend services/structural_variant_evidence.py). The backend serves it as an open
// object; this names the fields it writes.
export interface ApiStructuralEvidence {
  source?: string | null;
  sv_type?: string | null;
  chrom?: string | null;
  start?: number | null;
  end?: number | null;
  gene_symbols?: string[] | null;
  gene_count?: number | null;
  pli?: number | null;
  inheritance?: string | null;
  [field: string]: unknown;
}

export interface ApiStructuralClassificationDriftItem {
  variant_id: string;
  classification: string | null;
  cnv_class: string | null;
  classified_by: string | null;
  classified_at: string | null;
  status: string; // 'drifted' | 'unknown' | 'variant_missing'
  // What moved: 'source' | 'sv_type' | 'locus' | 'gene_symbols' | 'pli' | 'inheritance' | 'annotations'.
  changed: string[];
  evidence_from: ApiStructuralEvidence | null;
  evidence_to: ApiStructuralEvidence | null;
}

export interface ApiStructuralClassificationDrift {
  checked: number;
  drifted_count: number;
  drifted: ApiStructuralClassificationDriftItem[];
}

export interface ApiClassificationDrift {
  family_id: string;
  checked: number;
  drifted_count: number;
  drifted: ApiClassificationDriftItem[];
  // The structural variants' and CNVs' classifications (the small variants are above).
  structural?: ApiStructuralClassificationDrift;
}

export interface ApiClinicalAuditEvent {
  id: string;
  created_at: string;
  variant_id: string | null;
  actor: string;
  action: string;
  summary: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

// A page of a family's clinical audit trail, most recent first. `total` is how many events
// the trail holds; `has_more` says older events remain, read with `before` = the id of the
// page's last event.
export interface ApiClinicalAudit {
  family_id: string;
  total: number;
  has_more: boolean;
  events: ApiClinicalAuditEvent[];
}

// The sign-outs are served as ReportSignoutSummary: each field's meaning is on the backend
// model (schemas/traceability.py).
export interface ApiReportSignoutList {
  family_id: string;
  latest: ReportSignoutSummary | null;
  signouts: ReportSignoutSummary[];
}

// Whether the report, as it would be signed now, matches the latest sign-out (#508).
// `matches` is null when the family has never been signed out; `changed_sections` names
// the snapshot sections that differ, `not_compared` those an older snapshot predates.
export interface ApiReportSnapshotGap {
  section: string;
  item: string;
  reason: string;
}

export interface ApiReportSignoutCheck {
  family_id: string;
  version: number | null;
  content_hash: string | null;
  matches: boolean | null;
  changed_sections: string[];
  not_compared: string[];
  // Parts the signed snapshot froze as unavailable because a lookup failed (#514).
  not_captured?: ApiReportSnapshotGap[];
  checked_at: string;
}

export interface ApiSampleIntegrityQc {
  family_id: string;
  overall_status: QcStatus;
  application: SampleQcApplication;
  application_label: string;
  application_summary: string;
  genotype_source?: string | null;
  sex_checks: ApiSampleIntegritySexCheck[];
  relatedness_checks: ApiSampleIntegrityRelatednessCheck[];
  mendelian_checks: ApiSampleIntegrityMendelianCheck[];
  paternity_check?: ApiSampleIntegrityPaternityCheck | null;
  fetal_sex_check?: ApiSampleIntegrityFetalSexCheck | null;
  category_qc_check?: ApiSampleIntegrityCategoryQc | null;
  autosomal_sites: number;
  notes: string[];
}

// A project as the catalogue holds it (useProjectCatalog): ProjectDashboardOut, with the `_id`
// it is served under copied to `id` (withEntityId).
export type ApiProjectRecord = ProjectDashboardOut & { id: string };

export interface ApiClinicalCnv {
  _id: string;
  chr: string;
  start: number;
  end: number;
  type?: string | null;
  label: string;
  details_html?: string | null;
  assembly?: string | null;
  omim_id?: string | null;
  omim_title?: string | null;
  decipher_id?: string | null;
  description?: string | null;
  cytoband?: string | null;
  source_id?: string | null;
  orpha_id?: string | null;
  orpha_name?: string | null;
  // Pathogenic ClinVar CNVs overlapping the region by >= 30 % reciprocally, per side, and
  // their VariationIDs, from the knowledgebase build (#624). null: not recorded, not zero.
  clinvar_pathogenic_loss_count?: number | null;
  clinvar_pathogenic_gain_count?: number | null;
  clinvar_pathogenic_accessions?: string[] | null;
}

export type ApiChromosomeBand = IdeogramBandOut;

export interface ApiChromosome {
  chr: string;
  size: number;
  bands: ApiChromosomeBand[];
}

/** What a coverage track holds, as the track availability serves it (TRACE-9). */
export type CoverageContent = TrackAvailabilityOut['coverage_contents'][string];

/**
 * A repeat allele's, call's or locus's status. `not_assessed` is a call at a locus without
 * thresholds (no catalogue row names its TRID, or the row states none; CLIN-26) whose count is
 * outside any benign range the row states (CLIN-36); `unknown` a no-call.
 */
export type ApiRepeatStatus = RepeatExpansionRowOut['status'];

/** Why a call or a locus reads `review`: a count the catalogue cannot classify, or a man's two
 *  different chrX alleles (CLIN-14). */
export type ApiRepeatReviewReason = NonNullable<RepeatExpansionRowOut['review_reason']>;

export interface ApiRepeatExpansionMotifCount {
  motif: string;
  count: number;
}

export interface ApiRepeatExpansionAllele {
  repeat_count?: number | null;
  bp_length?: number | null;
  confidence_interval?: string | null;
  support_reads?: number | null;
  purity?: number | null;
  methylation?: number | null;
  motif_counts?: ApiRepeatExpansionMotifCount[];
  motif_spans?: string | null;
  interrupted?: boolean;
  interruption_label?: string | null;
  status: ApiRepeatStatus;
  // Set when the count is near a boundary at a locus whose TRGT counting units QA has not
  // validated: a difference of a few repeats there could change the status.
  count_unit_warning?: RepeatCountUnitWarningOut | null;
}

export interface ApiRepeatExpansionSampleCall {
  sample: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  genotype: string;
  allele_count: number;
  alleles: ApiRepeatExpansionAllele[];
  status: ApiRepeatStatus;
  review_reason?: ApiRepeatReviewReason | null;
  // Why the call needs a second look (a male with two different chrX alleles).
  note?: string | null;
}

export interface ApiRepeatExpansionRow {
  locus_id: string;
  gene: string;
  display_name: string;
  disease: string;
  inheritance?: string | null;
  chr: string;
  start: number;
  end: number;
  motif?: string | null;
  warning_min?: number | null;
  pathogenic_min?: number | null;
  benign_min?: number | null;
  benign_max?: number | null;
  pathogenic_max?: number | null;
  // The first count of a premutation (FMR1 55), between the grey zone and the pathogenic
  // threshold; absent for a locus without one.
  premutation_min?: number | null;
  // Whether QA validated that TRGT's count at the locus is the clinical repeat number, and
  // what is known of TRGT's count at the gene (FMR1, HTT, POLG).
  count_units_validated?: boolean;
  count_units_note?: string | null;
  status: ApiRepeatStatus;
  review_reason?: ApiRepeatReviewReason | null;
  calls: Record<string, ApiRepeatExpansionSampleCall>;
}

export interface ApiFamilyRepeatExpansionTable {
  samples: ApiFamilyMember[];
  loci: ApiRepeatExpansionRow[];
}

export interface ApiParaphaseMetric {
  key: string;
  label: string;
  value?: number | null;
}

export interface ApiParaphaseHaplotypeGroup {
  key: string;
  label: string;
  count: number;
  haplotypes: string[];
}

export interface ApiParaphaseDisorder {
  name: string;
  omim_url?: string | null;
}

export interface ApiParaphaseClinical {
  interpretation?: string | null;
  normal?: string | null;
  carrier?: string | null;
  pathogenic?: string | null;
  caveats?: string | null;
}

export interface ApiParaphaseRegionInfo {
  region_id: string;
  display_name: string;
  genes: string[];
  summary?: string | null;
  clinical?: ApiParaphaseClinical | null;
  clinical_priority: number;
  key_copy_number_fields: string[];
  key_read_fields: string[];
  key_haplotype_fields: string[];
  key_extra_fields: string[];
  field_descriptions: Record<string, string>;
  notes: string[];
  disorders: ApiParaphaseDisorder[];
}

export interface ApiParaphaseExtraField {
  key: string;
  label: string;
  value?: unknown;
  description?: string | null;
}

// The statuses the backend writes; it serves `ParaphaseSampleResultOut.clinical_status` as a
// string.
export type ParaphaseClinicalStatus =
  | 'pathogenic'
  | 'carrier'
  | 'normal'
  | 'review'
  | 'no_call'
  | 'none';

export interface ApiParaphaseSampleResult {
  sample: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  clinical_status?: ParaphaseClinicalStatus | null;
  total_cn?: number | null;
  gene_cn?: number | null;
  highest_total_cn?: number | null;
  sample_sex?: string | null;
  phase_region?: string | null;
  region_depth: Record<string, unknown>;
  genome_depth?: number | null;
  final_haplotype_count: number;
  assembled_haplotype_count: number;
  variant_site_count: number;
  heterozygous_site_count: number;
  fusion_count?: number | null;
  copy_number_signal?: boolean;
  copy_number_metrics?: ApiParaphaseMetric[];
  read_metrics?: ApiParaphaseMetric[];
  haplotype_groups?: ApiParaphaseHaplotypeGroup[];
  extra_fields?: ApiParaphaseExtraField[];
  uploaded_at?: string | null;
}

export interface ApiParaphaseGeneResult {
  gene_symbol: string;
  is_medically_relevant?: boolean;
  region_info?: ApiParaphaseRegionInfo | null;
  max_total_cn?: number | null;
  max_gene_cn?: number | null;
  max_highest_total_cn?: number | null;
  has_copy_number_signal?: boolean;
  samples: Record<string, ApiParaphaseSampleResult>;
}

export interface ApiFamilyParaphaseTable {
  samples: ApiFamilyMember[];
  genes: ApiParaphaseGeneResult[];
}

export interface ApiMitoDNACoverage {
  mean_depth?: number | null;
  min_depth?: number | null;
  max_depth?: number | null;
  breadth?: number | null;
  source?: string | null;
  regions: number;
}

export interface ApiMitoDNAQc {
  /** Shares the app-wide QC vocabulary; `skip` means not assessed, never `pass`. */
  status: QcStatus;
  notes: string[];
  contamination?: number | null;
  mean_depth?: number | null;
  min_mean_depth?: number | null;
}

export interface ApiMitoDNASample {
  sample_id: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  haplogroup?: string | null;
  coverage: ApiMitoDNACoverage;
  qc: ApiMitoDNAQc;
}

export interface ApiMitoDNAVariantSampleCall {
  sample: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  genotype: string;
  allele_fraction?: number | null;
  depth?: number | null;
  alt_depth?: number | null;
  zygosity: 'homoplasmic' | 'heteroplasmic' | 'low_level' | 'reference' | 'no_call' | 'unknown';
  display: string;
}

export interface ApiMitoDNAVariantAnnotation {
  region: string;
  gene?: string | null;
  consequence?: string | null;
  consequence_terms?: string[];
  gnomad_af?: number | null;
  category?: string | null;
  clinical_significance:
    | 'pathogenic'
    | 'likely_pathogenic'
    | 'benign'
    | 'likely_benign'
    | 'reported'
    | 'uncertain'
    | 'polymorphism'
    | 'unknown';
  disorders: string[];
  polymorphism_notes: string[];
  mitomap_query: string;
  mitomap_url: string;
  source_keys: string[];
}

// Not checked in apiContract.ts, nor ApiFamilyMitoDNAAnalysis: the note there says why.
export interface ApiMitoDNAVariant {
  variant_id: string;
  position: number;
  ref: string;
  alt: string;
  label: string;
  type: string;
  rsid?: string | null;
  annotation: ApiMitoDNAVariantAnnotation;
  calls: Record<string, ApiMitoDNAVariantSampleCall>;
  // MT variants reuse the small-variant review store (shared ACMG/tags/notes).
  // Type-only import keeps the shared shape without duplicating it; it is erased
  // at compile time so it introduces no runtime dependency on the page module.
  review?: import('../pages/families/smallVariantSearch').SmallVariantReview | null;
  maternal_transmission:
    | 'maternal_shared'
    | 'maternal_not_observed'
    | 'mother_not_assessed'
    | 'no_proband'
    | 'maternal_only'
    | 'father_only'
    | 'family_private'
    | 'no_alt_calls'
    | 'unknown';
}

export interface ApiMitoDNAStructuralVariantCall {
  sample: string;
  role?: string | null;
  genotype: string;
  // Fraction of reads carrying the event (Sniffles VAF): its heteroplasmy.
  heteroplasmy?: number | null;
  read_support?: number | null;
}

export interface ApiMitoDNAStructuralVariant {
  variant_id: string;
  sv_type: string;
  start: number;
  end: number;
  length?: number | null;
  genes: string[];
  source?: string | null;
  filters: string[];
  calls: Record<string, ApiMitoDNAStructuralVariantCall>;
}

export interface ApiFamilyMitoDNAAnalysis {
  samples: ApiMitoDNASample[];
  variants: ApiMitoDNAVariant[];
  // chrM large deletions and duplications.
  structural_variants: ApiMitoDNAStructuralVariant[];
  qc_notes: string[];
  heteroplasmy_threshold: number;
  homoplasmy_threshold: number;
}

export interface ApiRepeatExpansionTrackItem {
  sample: string;
  locus_id: string;
  gene: string;
  display_name: string;
  disease: string;
  chr: string;
  start: number;
  end: number;
  motif?: string | null;
  warning_min?: number | null;
  pathogenic_min?: number | null;
  // As the family table reads the call: against the catalogue row that classifies it now
  // (CLIN-14), not the status stored at import.
  status: ApiRepeatStatus;
  review_reason?: ApiRepeatReviewReason | null;
  allele_repeat_counts: number[];
  allele_bp_lengths: number[];
  count_units_validated?: boolean;
  count_units_note?: string | null;
  count_unit_warnings?: RepeatCountUnitWarningOut[];
}

export interface ApiRepeatExpansionTrackResponse {
  items: ApiRepeatExpansionTrackItem[];
}

export interface ApiGithubRelease {
  version: string;
  name?: string | null;
  published_at: string;
  summary: string;
  url: string;
  prerelease: boolean;
}

export interface ApiGithubReleaseCatalog {
  repository: string;
  repository_url: string;
  releases_url: string;
  issues_url: string;
  repo_visibility: 'private' | 'public' | 'unknown';
  sync_status: 'ok' | 'unavailable';
  sync_error?: string | null;
  fetched_at?: string | null;
  releases: ApiGithubRelease[];
}

// Shared shape for the /panels payload, consumed by GenePanelsPage and
// GenePanelDetailPage. (The narrower GenePanel stubs in smallVariantSearch.ts /
// GeneTrack.tsx are intentionally separate.)
export interface GeneLocation {
  gene: string;
  chr: string;
  start: number;
  end: number;
  // The assembly these coordinates belong to: panel regions are stored per assembly (#515).
  assembly_id?: string | null;
  assembly?: string | null;
}

export interface GenePanel {
  _id: string;
  name: string;
  version?: number;
  genes: string[];
  gene_count?: number;
  regions: GeneLocation[];
  created_by: string;
  created_by_email?: string | null;
  created_at: string;
  description?: string | null;
  source?: string;
  external_id?: string | null;
  external_version?: string | null;
  external_url?: string | null;
  source_updated_at?: string | null;
  source_metadata?: Record<string, unknown>;
}

export interface GenePanelVersionSummary {
  version: number;
  name: string;
  source?: string | null;
  external_version?: string | null;
  gene_count: number;
  created_by_email?: string | null;
  created_at: string;
}

export interface GenePanelVersionList {
  panel_id: string;
  current_version: number;
  versions: GenePanelVersionSummary[];
}
