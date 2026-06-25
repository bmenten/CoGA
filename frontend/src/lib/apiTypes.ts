export interface ApiPaginatedTotalResponse {
  total: number;
}

export interface ApiVariantPage<TVariant> extends ApiPaginatedTotalResponse {
  total_is_estimated?: boolean;
  count_limit?: number | null;
  variants: TVariant[];
}

export interface ApiFamilyMemberRef {
  sample_id: string;
}

export interface ApiFamilyMember extends ApiFamilyMemberRef {
  role: string;
  affected: boolean;
  sex: string;
  clinical_status?: 'unknown' | 'unaffected' | 'affected';
  carrier_status?: 'unknown' | 'not_carrier' | 'carrier';
  carrier_type?: 'obligate' | 'proven' | 'reported' | 'inferred' | null;
  carrier_evidence?: Record<string, unknown>;
  active?: boolean;
  sample_metadata?: Record<string, unknown>;
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

export interface ApiFamilyMemberUpdateResponse {
  family: ApiFamilyRecord;
  member: ApiFamilyMember;
  father_id?: string | null;
  mother_id?: string | null;
  impact: ApiFamilyMemberImpact;
  warnings?: string[];
  stale_analysis_scopes?: string[];
  data_counts?: Record<string, number>;
  cleared_data_counts?: Record<string, number>;
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

export interface ApiFamilyMemberBatchUpdateResponse {
  family: ApiFamilyRecord;
  warnings?: string[];
  stale_analysis_scopes?: string[];
  data_counts?: Record<string, number>;
  cleared_data_counts?: Record<string, number>;
}

export interface ApiFamilyMemberDeleteResponse {
  family: ApiFamilyRecord;
  impact: ApiFamilyMemberImpact;
  warnings?: string[];
  stale_analysis_scopes?: string[];
  data_counts?: Record<string, number>;
  cleared_data_counts?: Record<string, number>;
}

export interface ApiFamilyRelationship {
  id: string;
  relationship_type: 'parent_child' | 'couple';
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

/** Full admin-managed status definition (catalogue + dropdown options). */
export interface ApiFamilyStatusDefinition extends ApiFamilyStatusRef {
  id: string;
  description?: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface ApiFamilyBase<TMember = ApiFamilyMemberRef> {
  family_id: string;
  members: TMember[];
  projects?: string[];
  created_at?: string;
  status?: ApiFamilyStatusRef | null;
  assigned_to?: ApiUserRef | null;
  reviewed_by?: ApiUserRef | null;
}

export interface ApiFamilySummary extends ApiFamilyBase<ApiFamilyMember> {
  id?: string;
  _id?: string;
}

export interface ApiFamilyRecord extends ApiFamilyBase<ApiFamilyMember> {
  _id?: string;
  relationships?: ApiFamilyRelationship[];
  structure_version?: ApiFamilyStructureVersion | null;
  pedigree?: string | null;
  roi?: ApiFamilyRegionOfInterest | null;
  metadata?: Record<string, unknown>;
}

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

export interface ApiHpoFamilyQuery {
  hpo_id: string;
  include_descendants: boolean;
  sample_ids: string[];
  annotations: ApiHpoAnnotation[];
}

export interface ApiSmallVariantReviewSummary {
  reviewed_variant_count: number;
  note_count: number;
  tag_counts: Record<string, number>;
}

export interface ApiNiptFetalFraction {
  ff: number;
  ff_computed?: number | null;
  ff_external?: number | null;
  ff_median?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  n_sites: number;
  method: string;
  low_confidence: boolean;
  disagreement: boolean;
}

export interface ApiNiptSummary {
  family_id: string;
  fetal_fraction: ApiNiptFetalFraction;
  // Category counts are keyed by the category number (1-8), serialized as strings.
  category_counts: Record<string, number>;
  filter_counts: Record<string, number>;
}

export interface ApiNiptVariant {
  variant_id: string;
  chr: string;
  pos: number;
  ref: string;
  alt: string;
  gene?: string | null;
  impact?: string | null;
  consequence?: string | null;
  category?: number | null;
  category_label: string;
  maternal_state: string;
  fetal_inheritance: string;
  expected_vaf: number;
  observed_vaf?: number | null;
  confidence: number;
  flags: string[];
}

export interface ApiNiptVariantPage {
  family_id: string;
  total: number;
  fetal_fraction: ApiNiptFetalFraction;
  variants: ApiNiptVariant[];
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
}

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

export interface ApiSampleIntegrityPaternityCheck {
  father: string;
  cat7_transmitted: number;
  cat8_absent: number;
  informative_sites: number;
  status: QcStatus;
  message: string;
}

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

export type SampleQcApplication = 'wgs' | 'pgt' | 'nipt' | 'couple' | 'single' | 'unknown';

export interface ApiAnnotationModule {
  key: string;
  label: string;
  version: string | null;
  detail: string | null;
  layer: string; // 'pipeline' | 'reference'
}

export interface ApiAnnotationManifest {
  family_id: string;
  assembly: string | null;
  source: string | null;
  recorded_at: string | null;
  recorded_by: string | null;
  modules: ApiAnnotationModule[];
}

export interface ApiClassificationDriftItem {
  variant_id: string;
  acmg_class: string | null;
  classified_by: string | null;
  classified_at: string | null;
  status: string; // 'drifted' | 'variant_missing'
  annotation_version_from: string | null;
  annotation_version_to: string | null;
  clinvar_from: string | null;
  clinvar_to: string | null;
}

export interface ApiClassificationDrift {
  family_id: string;
  checked: number;
  drifted_count: number;
  drifted: ApiClassificationDriftItem[];
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

export interface ApiClinicalAudit {
  family_id: string;
  events: ApiClinicalAuditEvent[];
}

export interface ApiReportSignout {
  version: number;
  signed_out_by: string;
  signed_out_at: string;
  content_hash: string;
  snapshot?: Record<string, unknown> | null;
}

export interface ApiReportSignoutList {
  family_id: string;
  latest: ApiReportSignout | null;
  signouts: ApiReportSignout[];
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

export interface ApiProjectRecord<TFamily = ApiFamilySummary> {
  id: string;
  name: string;
  description?: string;
  species_id?: string;
  assembly_id?: string;
  species_name?: string;
  assembly_name?: string;
  assembly_version?: string;
  user_ids?: string[];
  families: TFamily[];
  samples: string[];
}

export interface ApiUserRecord {
  id: string;
  email: string;
  first_name?: string | null;
  last_name?: string | null;
  affiliation?: string | null;
  role: string;
  is_active: boolean;
  projects: string[];
}

export interface ApiSpeciesRecord {
  id: string;
  name: string;
  tax_id?: string | number;
  common_name?: string | null;
}

export interface ApiAssemblyRecord {
  id: string;
  species_id?: string;
  assembly_name: string;
  version: string;
}

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
}

export interface ApiChromosomeBand {
  name: string;
  start: number;
  end: number;
  stain: string;
}

export interface ApiChromosome {
  chr: string;
  size: number;
  bands: ApiChromosomeBand[];
}

export type ApiTrackAvailabilityResponse<TTrackAvailability> = {
  samples: Record<string, TTrackAvailability>;
};

export interface ApiChromosomeTrackAvailability {
  coverage: boolean;
  apcad: boolean;
  apcad_pcf: boolean;
  variants: boolean;
  small_variants: boolean;
  haplotypes: boolean;
  repeat_expansions: boolean;
}

export interface ApiGenomeTrackAvailability {
  coverage: boolean;
  segments: boolean;
  apcad: boolean;
  apcad_pcf: boolean;
  haplotypes: boolean;
  variants: boolean;
  repeat_expansions: boolean;
}

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
  status: 'normal' | 'intermediate' | 'pathogenic' | 'unknown';
}

export interface ApiRepeatExpansionSampleCall {
  sample: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  genotype: string;
  allele_count: number;
  alleles: ApiRepeatExpansionAllele[];
  status: 'normal' | 'intermediate' | 'pathogenic' | 'unknown';
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
  status: 'normal' | 'intermediate' | 'pathogenic' | 'unknown';
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
  status: 'pass' | 'warning' | 'fail' | 'unknown';
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
    | 'maternal_only'
    | 'father_only'
    | 'family_private'
    | 'no_alt_calls'
    | 'unknown';
}

export interface ApiFamilyMitoDNAAnalysis {
  samples: ApiMitoDNASample[];
  variants: ApiMitoDNAVariant[];
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
  status: 'normal' | 'intermediate' | 'pathogenic' | 'unknown';
  allele_repeat_counts: number[];
  allele_bp_lengths: number[];
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

export interface MendeliomeRegenerateResponse {
  panel: GenePanel;
  message: string;
  changed: boolean;
  version: number;
  monarch_release?: string | null;
  gene_count: number;
  missing_genes: string[];
}
