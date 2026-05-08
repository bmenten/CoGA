export interface ApiPaginatedTotalResponse {
  total: number;
}

export interface ApiVariantPage<TVariant> extends ApiPaginatedTotalResponse {
  variants: TVariant[];
}

export interface ApiFamilyMemberRef {
  sample_id: string;
}

export interface ApiFamilyMember extends ApiFamilyMemberRef {
  role: string;
  affected: boolean;
  sex: string;
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

export interface ApiFamilyBase<TMember = ApiFamilyMemberRef> {
  family_id: string;
  members: TMember[];
  projects?: string[];
}

export interface ApiFamilySummary extends ApiFamilyBase<ApiFamilyMember> {
  id?: string;
  _id?: string;
}

export interface ApiFamilyRecord extends ApiFamilyBase<ApiFamilyMember> {
  _id?: string;
  pedigree?: string | null;
  roi?: ApiFamilyRegionOfInterest | null;
  metadata?: Record<string, unknown>;
}

export interface ApiSmallVariantReviewSummary {
  reviewed_variant_count: number;
  note_count: number;
  tag_counts: Record<string, number>;
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

export type ApiTrackAvailabilityResponse<TTrackAvailability> = {
  samples: Record<string, TTrackAvailability>;
};

export interface ApiChromosomeTrackAvailability {
  coverage: boolean;
  apcad: boolean;
  variants: boolean;
  small_variants: boolean;
  haplotypes: boolean;
  repeat_expansions: boolean;
}

export interface ApiGenomeTrackAvailability {
  coverage: boolean;
  segments: boolean;
  apcad: boolean;
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

export interface ApiParaphaseRegionInfo {
  region_id: string;
  display_name: string;
  genes: string[];
  summary?: string | null;
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

export interface ApiParaphaseSampleResult {
  sample: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
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
