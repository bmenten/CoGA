import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import type { NavigateFunction } from 'react-router-dom';
import type { ApiFamilyMember, ApiFamilyRecord } from '../../lib/apiTypes';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import {
  hasNonDefaultGenotypeSelection,
  parseSerializedGenotypeSelection,
} from '../../lib/sampleFilterState';
import { parseGeneOrRegionInput } from '../../lib/variantSearch';
import type { AcmgMitoContext } from '../../lib/acmg';

export interface SmallVariantGenotype {
  sample?: string;
  gt: string;
  ps?: number;
  dp?: number;
  ad?: number[];
  af?: number[];
}

// Persisted ACMG classification (snake_case to match the backend payload).
export interface AcmgReviewCriterion {
  code: string;
  strength: string;
  accepted: boolean;
  evidence?: string | null;
  auto_suggested: boolean;
}

export interface AcmgReviewPayload {
  criteria: AcmgReviewCriterion[];
  point_total?: number | null;
  classification?: string | null;
  // MAGI-ACMG VUS sub-tier ('hot' | 'warm' | 'cold'); only set when class is VUS.
  vus_tier?: string | null;
}

export interface CnvAcmgReviewCriterion {
  code: string;
  points: number;
  accepted: boolean;
  evidence?: string | null;
  auto_suggested: boolean;
}

export interface CnvAcmgReviewPayload {
  kind: 'loss' | 'gain';
  criteria: CnvAcmgReviewCriterion[];
  point_total?: number | null;
  classification?: string | null;
}

export interface SmallVariantReview {
  variant_id: string;
  classification?: string | null;
  tags: string[];
  tag_metadata: Record<string, SmallVariantReviewTagMetadata>;
  note?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
  compound_het?: SmallVariantCompoundHetReview | null;
  acmg?: AcmgReviewPayload | null;
  // ClinGen CNV classification — only populated for structural-variant reviews.
  cnv_acmg?: CnvAcmgReviewPayload | null;
}

export interface SmallVariantReviewTagMetadata {
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface SmallVariantCompoundHetReview {
  group_id: string;
  partner_variant_ids: string[];
  gene?: string | null;
  gene_id?: string | null;
  classification?: string | null;
  tags: string[];
  tag_metadata: Record<string, SmallVariantReviewTagMetadata>;
  note?: string | null;
  phase_status?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface SmallVariantTranscript {
  gene?: string | null;
  gene_id?: string | null;
  transcript_id?: string | null;
  transcript_source?: string | null;
  feature_type?: string | null;
  transcript_biotype?: string | null;
  impact?: string | null;
  effect?: string | null;
  hgvsc?: string | null;
  hgvsp?: string | null;
  exon?: string | null;
  intron?: string | null;
  canonical?: boolean;
  mane_select?: boolean;
  mane_plus_clinical?: boolean;
  primary?: boolean;
}

export interface SmallVariant {
  _id: string;
  chr: string;
  start: number;
  end: number;
  type: string;
  source?: string;
  ref?: string;
  alt?: string;
  ps?: number;
  gene?: string;
  gene_id?: string;
  transcript_id?: string;
  feature_type?: string;
  transcript_biotype?: string;
  impact?: string;
  effect?: string;
  clinvar?: string;
  rsid?: string;
  hgvsc?: string;
  hgvsp?: string;
  canonical?: boolean;
  mane_select?: boolean;
  mane_plus_clinical?: boolean;
  exon?: string;
  intron?: string;
  lof?: string;
  lof_filter?: string;
  lof_flags?: string;
  gnomad_af?: number;
  gnomad_hom_count?: number;
  gene_pli?: number;
  gene_missense_z?: number;
  population_frequencies?: Record<string, number>;
  cadd_raw?: number;
  cadd_phred?: number;
  revel?: number;
  sift?: string;
  polyphen?: string;
  spliceai_ds_ag?: number;
  spliceai_ds_al?: number;
  spliceai_ds_dg?: number;
  spliceai_ds_dl?: number;
  spliceai_max?: number;
  alpha_missense_pathogenicity?: number;
  alpha_missense_class?: string | null;
  annotation_extra?: Record<string, string | number | boolean | null>;
  transcripts?: SmallVariantTranscript[];
  genotypes: SmallVariantGenotype[];
  review?: SmallVariantReview | null;
  internal_cohort?: SmallVariantInternalCohort | null;
  priority?: SmallVariantPriority | null;
  sv_second_hit?: SvSecondHit | null;
  // mtDNA-specific ACMG context, attached by the mitochondrial page adapter for
  // MT variants so the modal can run the mt-specific evaluator.
  mito?: AcmgMitoContext | null;
  // Monogenic NIPT classification, attached only by the NIPT variants endpoint.
  nipt?: NiptClassification | null;
}

export interface NiptClassification {
  category?: number | null;
  category_label: string;
  maternal_state: string;
  fetal_inheritance: string;
  expected_vaf: number;
  observed_vaf?: number | null;
  confidence: number;
  flags: string[];
}

export interface SvSecondHit {
  sv_count: number;
  sv_types: string[];
  affected_zygosity?: string | null;
  has_deletion: boolean;
  phase?: string;
  phase_evidence?: string | null;
  deletion_unmasked?: boolean;
}

export interface SmallVariantPriorityMatch {
  hpo_id: string;
  label?: string | null;
}

export interface SmallVariantPriority {
  combined_score: number;
  variant_score: number;
  pathogenicity_score: number;
  frequency_score: number;
  segregation_weight: number;
  phenotype_score?: number | null;
  segregation_modes: string[];
  phenotype_gene?: string | null;
  phenotype_matches: SmallVariantPriorityMatch[];
  rank?: number | null;
}

export interface SmallVariantInternalCohort {
  samples: number;
  het: number;
  hom: number;
  families: number;
}

export interface SmallVariantPage {
  variants: SmallVariant[];
  variant_groups?: SmallVariantGroup[];
  total: number;
  total_is_estimated?: boolean;
  unfiltered_total?: number | null;
  unfiltered_total_is_estimated?: boolean;
  count_limit?: number | null;
  ranking_truncated?: boolean;
  ranking_cached?: boolean;
  ranking_computed_at?: string | null;
  small_variant_summary?: SmallVariantSummary | null;
}

export interface SmallVariantSampleSummary {
  sample_id: string;
  non_ref_count: number;
  het_count: number;
  hom_alt_count: number;
}

export interface SmallVariantSummary {
  total_variants: number;
  snv_count: number;
  indel_count: number;
  sample_counts: SmallVariantSampleSummary[];
}

export interface SmallVariantGroup {
  group_type: 'compound_het';
  group_key: string;
  gene?: string;
  gene_id?: string;
  variants: SmallVariant[];
  review?: SmallVariantCompoundHetReview | null;
}

export type SmallVariantReviewSavePayload = {
  classification?: string;
  tags: string[];
  note?: string;
  compound_het?: {
    partner_variant_id?: string;
    classification?: string;
    tags: string[];
    note?: string;
  };
  acmg?: AcmgReviewPayload;
  cnv_acmg?: CnvAcmgReviewPayload;
};

export interface SmallVariantFilterPreset {
  _id: string;
  family_id?: string | null;
  scope: 'family' | 'global';
  owner: string;
  name: string;
  description?: string | null;
  filters: Record<string, unknown>;
  sample_filters: Record<string, unknown>;
  sample_templates: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SmallVariantTagDefinition {
  key: string;
  label: string;
  description?: string | null;
  group: 'collaboration' | 'classification' | 'custom';
  color: string;
  sort_order: number;
  scope: 'system' | 'global' | 'project';
  project_id?: string | null;
  shared_project_ids?: string[];
  is_custom: boolean;
}

export type FamilyMember = ApiFamilyMember;
export type SmallVariantFamily = Pick<
  ApiFamilyRecord,
  'members' | 'relationships' | 'pedigree' | 'projects' | 'metadata'
>;

export interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

export interface GenePanel {
  _id: string;
  name: string;
  source?: string;
}

export type SmallVariantSampleFilter = {
  gt: string[];
  qual: string;
  dp: string;
  af: string;
  ad_alt: string;
};

export type SmallFilterState = {
  locus: string;
  chr: string;
  start: string;
  end: string;
  intervals: string;
  inheritance: string;
  expanded_carrier_screening: string;
  prioritize: string;
  require_sv_second_hit: string;
  ps: string;
  type: string;
  source: string;
  gene: string;
  transcript: string;
  impact: string;
  effect: string;
  clinvar: string;
  exclude_clinvar: string;
  clinvar_overrides_frequency: string;
  exclude_review_tags: string;
  exclude_gene: string;
  exclude_intervals: string;
  rsid: string;
  hgvsc: string;
  hgvsp: string;
  canonical_only: string;
  mane_only: string;
  lof_only: string;
  max_gnomad_af: string;
  max_gnomad_exomes_af: string;
  max_gnomad_genomes_af: string;
  max_gnomad_popmax_af: string;
  max_topmed_af: string;
  max_gnomad_ac: string;
  max_gnomad_hom_count: string;
  max_gnomad_hemi_count: string;
  min_cadd: string;
  min_revel: string;
  min_spliceai: string;
  sift: string;
  polyphen: string;
  panel_id: string;
  classification: string;
  review_tags: string;
  has_notes: string;
  // Monogenic NIPT only: the maternal/fetal category multi-select and the
  // minimum classification confidence. Never set by the small-variant UI.
  category: string;
  min_confidence: string;
};

export type ActiveSmallFilterChip =
  | {
      id: string;
      label: string;
      kind: 'top';
      key: keyof SmallFilterState;
      value?: string;
    }
  | {
      id: string;
      label: string;
      kind: 'sample-gt';
      sample: string;
    }
  | {
      id: string;
      label: string;
      kind: 'sample-field';
      sample: string;
      field: Exclude<keyof SmallVariantSampleFilter, 'gt'>;
    };

export type SmallPreset =
  | 'dominant_strict'
  | 'dominant_relaxed'
  | 'compound_het'
  | 'expanded_carrier_screening'
  | 'phenotype_priority'
  | 'recessive_hom'
  | 'recessive_permissive'
  | 'any_affected'
  | 'clinvar_review'
  // Monogenic NIPT built-in presets (shown only in the NIPT filter form).
  | 'nipt_de_novo'
  | 'nipt_recessive';

export const HOM_GT_GROUP = ['1/1', '1|1'];
export const HET_GT_GROUP = ['0/1', '1/0', '0|1', '1|0'];
export const REF_GT_GROUP = ['0/0', '0|0', './.', 'absent'];
export const ALL_GT_GROUPS = [...HOM_GT_GROUP, ...HET_GT_GROUP, ...REF_GT_GROUP];
export const CARD_VIEW_THRESHOLD = 100;

export const SYSTEM_TAG_GROUP_LABELS: Record<SmallVariantTagDefinition['group'], string> = {
  collaboration: 'Collaboration',
  classification: 'ACMG classification',
  custom: 'Custom',
};

export const COLLABORATION_QUICK_TAGS = {
  review: 'review',
  excluded: 'excluded',
  report: 'report',
} as const;

export const REPORT_TAG_KEY = COLLABORATION_QUICK_TAGS.report;

export const ACMG_CLASSIFICATION_TAGS = [
  { key: 'acmg_class_5', label: 'Pathogenic - class 5' },
  { key: 'acmg_class_4', label: 'Likely Pathogenic - class 4' },
  { key: 'acmg_class_3', label: 'VUS - class 3' },
  { key: 'acmg_class_2', label: 'Likely benign - class 2' },
  { key: 'acmg_class_1', label: 'Benign - class 1' },
] as const;

export const ACMG_CLASSIFICATION_TAG_KEYS = ACMG_CLASSIFICATION_TAGS.map((entry) => entry.key);
export const ACMG_CLASSIFICATION_LABELS = Object.fromEntries(
  ACMG_CLASSIFICATION_TAGS.map((entry) => [entry.key, entry.label]),
) as Record<string, string>;
export const REVIEW_CLASSIFICATION_OPTIONS = ACMG_CLASSIFICATION_TAGS.map((entry) => entry.label);

// MAGI-ACMG VUS sub-tier tags. Auto-managed alongside the acmg_class_* tags so a
// "hot VUS" is filterable/reportable through the existing review-tag pipeline.
export const ACMG_VUS_TIER_TAGS = [
  { key: 'acmg_vus_hot', label: 'VUS — Hot' },
  { key: 'acmg_vus_warm', label: 'VUS — Warm' },
  { key: 'acmg_vus_cold', label: 'VUS — Cold' },
] as const;

export const ACMG_VUS_TIER_TAG_KEYS = ACMG_VUS_TIER_TAGS.map((entry) => entry.key);

export const ACMG_VUS_TIER_TAG_BY_TIER: Record<'hot' | 'warm' | 'cold', string> = {
  hot: 'acmg_vus_hot',
  warm: 'acmg_vus_warm',
  cold: 'acmg_vus_cold',
};

// All ACMG-derived tags the modal owns automatically (class + VUS tier); these
// are excluded from the manually-editable tag set and re-derived on each save.
export const ACMG_AUTO_MANAGED_TAG_KEYS = [
  ...ACMG_CLASSIFICATION_TAG_KEYS,
  ...ACMG_VUS_TIER_TAG_KEYS,
];

export const COMPOUND_HET_PHASE_STATUS_LABELS: Record<string, string> = {
  likely_in_trans: 'Likely in trans',
  likely_cis: 'Likely cis',
  same_phase_set: 'Same phase set',
  different_phase_sets: 'Different phase sets',
  unknown: 'Phase unknown',
};

export const SMALL_INHERITANCE_LABELS: Record<string, string> = {
  de_novo_dominant: 'De novo / dominant',
  recessive_homozygous: 'Recessive (homozygous)',
  compound_het: 'Compound heterozygous',
  x_linked: 'X-linked recessive',
  recessive: 'Recessive (hom + compound het + X-linked)',
};

export const BUILT_IN_SMALL_PRESETS: Array<{
  value: SmallPreset;
  label: string;
  description: string;
}> = [
  {
    value: 'phenotype_priority',
    label: 'Phenotype priority (Exomiser-style)',
    description:
      'Rank rare, impactful, segregating variants by how well each gene matches the affected individuals’ HPO phenotypes (Monarch).',
  },
  {
    value: 'dominant_strict',
    label: 'Dominant strict',
    description: 'Rare, high-confidence heterozygous calls in affected individuals.',
  },
  {
    value: 'dominant_relaxed',
    label: 'Dominant relaxed',
    description: 'Looser thresholds for exploratory de novo or dominant review.',
  },
  {
    value: 'expanded_carrier_screening',
    label: 'Expanded carrier screening',
    description:
      'Couple-only screen for genes where both partners carry a rare variant in the same gene.',
  },
  {
    value: 'compound_het',
    label: 'Compound het',
    description:
      'Automatically keep variants that form a qualifying compound-heterozygous pair.',
  },
  {
    value: 'recessive_hom',
    label: 'Recessive hom',
    description: 'Homozygous recessive candidates with supportive parental genotypes.',
  },
  {
    value: 'recessive_permissive',
    label: 'Recessive broad',
    description: 'Broader recessive sweep for inherited candidate review.',
  },
  {
    value: 'any_affected',
    label: 'Any affected',
    description: 'Fast rare-coding search across all affected family members.',
  },
  {
    value: 'clinvar_review',
    label: 'ClinVar review',
    description: 'Focus on rare ClinVar-supported calls for triage and reporting.',
  },
];

// Built-in presets shown in the monogenic NIPT filter form (in place of the
// small-variant built-ins, which rely on per-sample genotype/phenotype).
export const NIPT_BUILT_IN_PRESETS: Array<{
  value: SmallPreset;
  label: string;
  description: string;
}> = [
  {
    value: 'nipt_de_novo',
    label: 'De novo',
    description:
      'Category-1 de-novo candidates: high/moderate impact, gnomAD <1% (ClinVar P/LP overrides), absent in both parents.',
  },
  {
    value: 'nipt_recessive',
    label: 'Recessive (both parents carrier)',
    description:
      'Genes where both parents carry a rare, high/moderate variant (ClinVar P/LP overrides frequency); shows every carrier variant so you can read whether the fetus inherited none, one, or both.',
  },
];

const SMALL_FILTER_LABELS: Record<keyof SmallFilterState, string> = {
  locus: 'Location',
  chr: 'Chromosome',
  start: 'Start',
  end: 'End',
  intervals: 'Intervals',
  inheritance: 'Inheritance',
  expanded_carrier_screening: 'Expanded carrier screening',
  prioritize: 'Phenotype prioritization',
  require_sv_second_hit: 'Also hit by an SV',
  ps: 'Phase set',
  type: 'Variant type',
  source: 'Callset',
  gene: 'Gene list',
  transcript: 'Transcript',
  impact: 'Impact',
  effect: 'Annotation',
  clinvar: 'Pathogenicity',
  exclude_clinvar: 'Exclude pathogenicity',
  clinvar_overrides_frequency: 'ClinVar P/LP overrides frequency',
  exclude_review_tags: 'Exclude review tags',
  exclude_gene: 'Exclude gene list',
  exclude_intervals: 'Exclude intervals',
  rsid: 'dbSNP',
  hgvsc: 'HGVS.c',
  hgvsp: 'HGVS.p',
  canonical_only: 'Canonical only',
  mane_only: 'MANE only',
  lof_only: 'LoF only',
  max_gnomad_af: 'Max gnomAD AF',
  max_gnomad_exomes_af: 'Max gnomAD exomes AF',
  max_gnomad_genomes_af: 'Max gnomAD genomes AF',
  max_gnomad_popmax_af: 'Max gnomAD popmax AF',
  max_topmed_af: 'Max TOPMed AF',
  max_gnomad_ac: 'Max gnomAD AC',
  max_gnomad_hom_count: 'Max gnomAD H/H',
  max_gnomad_hemi_count: 'Max gnomAD hemi',
  min_cadd: 'Min CADD',
  min_revel: 'Min REVEL',
  min_spliceai: 'Min SpliceAI',
  sift: 'SIFT',
  polyphen: 'PolyPhen',
  panel_id: 'Gene panel',
  classification: 'Classification',
  review_tags: 'Review tags',
  has_notes: 'Has notes',
  category: 'Category',
  min_confidence: 'Min confidence',
};

const SAMPLE_FIELD_LABELS: Record<Exclude<keyof SmallVariantSampleFilter, 'gt'>, string> = {
  qual: 'QUAL',
  dp: 'DP',
  af: 'AF',
  ad_alt: 'AD alt',
};

const SINGLE_SAMPLE_FILTER_KEYS: Array<keyof SmallVariantSampleFilter> = [
  'gt',
  'qual',
  'dp',
  'af',
  'ad_alt',
];

export const MULTI_VALUE_FILTER_KEYS = new Set<
  keyof SmallFilterState
>([
  'impact',
  'effect',
  'clinvar',
  'exclude_clinvar',
  'exclude_review_tags',
  'classification',
  'review_tags',
  'category',
]);

export const parseCommaSeparatedValues = (value: string) =>
  value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

export const joinFilterValues = (values: Iterable<string>) =>
  Array.from(new Set(Array.from(values).map((value) => value.trim()).filter(Boolean))).join(', ');

const cloneSingleSampleFilter = (
  filter?: Partial<SmallVariantSampleFilter> | null,
): SmallVariantSampleFilter => ({
  gt:
    filter?.gt && Array.isArray(filter.gt) && filter.gt.length
      ? filter.gt.map((value) => String(value))
      : [...ALL_GT_GROUPS],
  qual: filter?.qual ? String(filter.qual) : '',
  dp: filter?.dp ? String(filter.dp) : '',
  af: filter?.af ? String(filter.af) : '',
  ad_alt: filter?.ad_alt ? String(filter.ad_alt) : '',
});

const normalizeStoredSampleFilterMap = (
  value: unknown,
): Record<string, Partial<SmallVariantSampleFilter>> => {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([, filter]) => filter && typeof filter === 'object')
      .map(([key, filter]) => {
        const rawFilter = filter as Partial<SmallVariantSampleFilter>;
        const normalizedFilter: Partial<SmallVariantSampleFilter> = {};

        if (Array.isArray(rawFilter.gt)) {
          normalizedFilter.gt = rawFilter.gt.map((value) => String(value));
        }
        if (rawFilter.qual !== undefined && rawFilter.qual !== null) {
          normalizedFilter.qual = String(rawFilter.qual);
        }
        if (rawFilter.dp !== undefined && rawFilter.dp !== null) {
          normalizedFilter.dp = String(rawFilter.dp);
        }
        if (rawFilter.af !== undefined && rawFilter.af !== null) {
          normalizedFilter.af = String(rawFilter.af);
        }
        if (rawFilter.ad_alt !== undefined && rawFilter.ad_alt !== null) {
          normalizedFilter.ad_alt = String(rawFilter.ad_alt);
        }

        return [key, normalizedFilter];
      }),
  );
};

const normalizeStoredFilterValue = (
  key: keyof SmallFilterState,
  value: unknown,
): string => {
  if (value === null || value === undefined) return '';
  if (MULTI_VALUE_FILTER_KEYS.has(key)) {
    if (Array.isArray(value)) {
      return joinFilterValues(value.map((entry) => String(entry)));
    }
    if (typeof value === 'string') return joinFilterValues(parseCommaSeparatedValues(value));
    return '';
  }
  if (key === 'has_notes') {
    if (value === true || value === 'true') return 'true';
    return '';
  }
  if (key === 'expanded_carrier_screening') {
    if (value === true || value === 'true') return 'true';
    return '';
  }
  if (key === 'prioritize' || key === 'require_sv_second_hit') {
    if (value === true || value === 'true') return 'true';
    return '';
  }
  return String(value);
};

export const createEmptySmallFilters = (): SmallFilterState => ({
  locus: '',
  chr: '',
  start: '',
  end: '',
  intervals: '',
  inheritance: '',
  expanded_carrier_screening: '',
  prioritize: '',
  require_sv_second_hit: '',
  ps: '',
  type: '',
  source: '',
  gene: '',
  transcript: '',
  impact: '',
  effect: '',
  clinvar: '',
  exclude_clinvar: '',
  // Standard on: ClinVar P/LP variants survive the gnomAD frequency filter.
  clinvar_overrides_frequency: 'true',
  exclude_review_tags: '',
  exclude_gene: '',
  exclude_intervals: '',
  rsid: '',
  hgvsc: '',
  hgvsp: '',
  canonical_only: '',
  mane_only: '',
  lof_only: '',
  max_gnomad_af: '',
  max_gnomad_exomes_af: '',
  max_gnomad_genomes_af: '',
  max_gnomad_popmax_af: '',
  max_topmed_af: '',
  max_gnomad_ac: '',
  max_gnomad_hom_count: '',
  max_gnomad_hemi_count: '',
  min_cadd: '',
  min_revel: '',
  min_spliceai: '',
  sift: '',
  polyphen: '',
  panel_id: '',
  classification: '',
  review_tags: '',
  has_notes: '',
  category: '',
  min_confidence: '',
});

export const parsePedigree = (pedigree?: string | null): PedRow[] => {
  if (!pedigree) return [];
  return pedigree
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const [fid, iid, pid, mid, sex, phen] = line.trim().split(/\s+/);
      return { fid, iid, pid, mid, sex, phen };
    });
};

const buildDefaultSampleFilters = (
  members: FamilyMember[],
): Record<string, SmallVariantSampleFilter> =>
  Object.fromEntries(
    members.map((member) => [
      member.sample_id,
      {
        gt: [...ALL_GT_GROUPS],
        qual: '',
        dp: '',
        af: '',
        ad_alt: '',
      },
    ]),
  );

const cloneSampleFilters = (filters: Record<string, SmallVariantSampleFilter>) =>
  Object.fromEntries(
    Object.entries(filters).map(([sample, filter]) => [sample, cloneSingleSampleFilter(filter)]),
  );

const sampleFiltersEqual = (
  left?: Partial<SmallVariantSampleFilter> | null,
  right?: Partial<SmallVariantSampleFilter> | null,
) => {
  const normalizedLeft = cloneSingleSampleFilter(left);
  const normalizedRight = cloneSingleSampleFilter(right);
  const sortedLeftGt = [...normalizedLeft.gt].sort();
  const sortedRightGt = [...normalizedRight.gt].sort();

  return (
    normalizedLeft.qual === normalizedRight.qual &&
    normalizedLeft.dp === normalizedRight.dp &&
    normalizedLeft.af === normalizedRight.af &&
    normalizedLeft.ad_alt === normalizedRight.ad_alt &&
    sortedLeftGt.length === sortedRightGt.length &&
    sortedLeftGt.every((value, index) => value === sortedRightGt[index])
  );
};

export const resolveCarrierScreeningCoupleMembers = (
  members: FamilyMember[],
  relationships: SmallVariantFamily['relationships'] = [],
) => {
  const memberMap = new Map(members.map((member) => [member.sample_id, member]));
  const couple = relationships?.find((relationship) => (
    relationship.relationship_type === 'couple' &&
    memberMap.has(relationship.sample_id_a) &&
    memberMap.has(relationship.sample_id_b)
  ));
  if (couple) {
    return {
      left: memberMap.get(couple.sample_id_a)!,
      right: memberMap.get(couple.sample_id_b)!,
    };
  }

  const mother = members.find((member) => member.role === 'mother') || null;
  const father = members.find((member) => member.role === 'father') || null;
  if (mother && father) {
    return { left: mother, right: father };
  }

  if (members.length === 2) {
    const [left, right] = members;
    if (left.sample_id !== right.sample_id) {
      return { left, right };
    }
  }

  return null;
};

const buildPresetState = (
  preset: SmallPreset,
  members: FamilyMember[],
): {
  filters: SmallFilterState;
  sampleFilters: Record<string, SmallVariantSampleFilter>;
} => {
  const filters = createEmptySmallFilters();
  const sampleFilters = buildDefaultSampleFilters(members);

  const setAffectedGenotypes = (gt: string[], thresholds?: Partial<SmallVariantSampleFilter>) => {
    members.forEach((member) => {
      if (!member.affected) return;
      sampleFilters[member.sample_id] = {
        ...sampleFilters[member.sample_id],
        ...thresholds,
        gt: [...gt],
      };
    });
  };

  const setUnaffectedGenotypes = (
    gt: string[],
    thresholds?: Partial<SmallVariantSampleFilter>,
  ) => {
    members.forEach((member) => {
      if (member.affected) return;
      sampleFilters[member.sample_id] = {
        ...sampleFilters[member.sample_id],
        ...thresholds,
        gt: [...gt],
      };
    });
  };

  if (preset === 'dominant_strict') {
    filters.max_gnomad_af = '0.001';
    filters.impact = 'HIGH';
    filters.canonical_only = 'true';
    filters.min_cadd = '20';
    filters.min_revel = '0.5';
    setAffectedGenotypes(HET_GT_GROUP, { qual: '20', dp: '10', af: '0.2', ad_alt: '4' });
    setUnaffectedGenotypes(REF_GT_GROUP);
  } else if (preset === 'dominant_relaxed') {
    filters.max_gnomad_af = '0.01';
    filters.impact = 'HIGH, MODERATE';
    filters.canonical_only = 'true';
    filters.min_cadd = '15';
    setAffectedGenotypes(HET_GT_GROUP, { qual: '15', dp: '8', af: '0.18', ad_alt: '3' });
    setUnaffectedGenotypes(REF_GT_GROUP);
  } else if (preset === 'nipt_de_novo') {
    // Monogenic NIPT: de-novo candidates -- category 1 only (de novo in the
    // fetus), high/moderate impact, rare in gnomAD, ClinVar P/LP overriding the
    // frequency cut-off. No genotype filters (the fetal call is inferred from the
    // cfDNA VAF).
    filters.inheritance = 'de_novo';
    filters.category = '1';
    filters.impact = 'HIGH, MODERATE';
    filters.max_gnomad_af = '0.01';
    filters.clinvar_overrides_frequency = 'true';
  } else if (preset === 'nipt_recessive') {
    // Monogenic NIPT: genes where both parents carry a high/moderate, rare
    // variant; the backend recessive_at_risk grouping returns every carrier
    // variant in those genes so the analyst can read the fetal inheritance
    // (none / one / both) off the categories. ClinVar P/LP overrides frequency.
    filters.inheritance = 'recessive_at_risk';
    filters.impact = 'HIGH, MODERATE';
    filters.max_gnomad_af = '0.01';
    filters.clinvar_overrides_frequency = 'true';
  } else if (preset === 'expanded_carrier_screening') {
    const couple = resolveCarrierScreeningCoupleMembers(members);
    if (!couple) {
      return { filters, sampleFilters };
    }
    filters.expanded_carrier_screening = 'true';
    filters.max_gnomad_af = '0.01';
  } else if (preset === 'phenotype_priority') {
    // Exomiser-style: rare candidate set ranked by gene-phenotype match. gnomAD
    // <1% + H/H <=10, but ClinVar P/LP overrules the frequency cut-off; ClinVar
    // benign / likely benign excluded; affected individuals must carry the
    // variant (Hom or Het, not WT).
    filters.prioritize = 'true';
    filters.impact = 'HIGH, MODERATE';
    filters.max_gnomad_exomes_af = '0.01';
    filters.max_gnomad_genomes_af = '0.01';
    filters.max_gnomad_hom_count = '10';
    filters.max_gnomad_hemi_count = '10';
    filters.clinvar_overrides_frequency = 'true';
    filters.exclude_clinvar = 'Benign, Likely benign';
    // Standard proband QC: GQ/QUAL >= 20, DP >= 10, AF >= 0.2, AD alt >= 4.
    setAffectedGenotypes([...HOM_GT_GROUP, ...HET_GT_GROUP], {
      qual: '20',
      dp: '10',
      af: '0.2',
      ad_alt: '4',
    });
  } else if (preset === 'compound_het') {
    filters.inheritance = 'compound_het';
    filters.max_gnomad_af = '0.02';
    filters.impact = 'HIGH, MODERATE';
    filters.canonical_only = 'true';
    setAffectedGenotypes(HET_GT_GROUP, { qual: '15', dp: '6', af: '0.18', ad_alt: '3' });
    setUnaffectedGenotypes([...REF_GT_GROUP, ...HET_GT_GROUP]);
  } else if (preset === 'recessive_hom') {
    filters.max_gnomad_af = '0.01';
    filters.impact = 'HIGH, MODERATE';
    filters.canonical_only = 'true';
    setAffectedGenotypes(HOM_GT_GROUP, { qual: '20', dp: '8', af: '0.75', ad_alt: '5' });
    members.forEach((member) => {
      if (member.role === 'mother' || member.role === 'father') {
        sampleFilters[member.sample_id] = {
          ...sampleFilters[member.sample_id],
          gt: [...HET_GT_GROUP],
          qual: '15',
          dp: '8',
        };
      }
    });
  } else if (preset === 'recessive_permissive') {
    filters.inheritance = 'recessive';
    filters.max_gnomad_af = '0.02';
    filters.impact = 'HIGH, MODERATE';
    filters.canonical_only = 'true';
    setAffectedGenotypes([...HET_GT_GROUP, ...HOM_GT_GROUP], {
      qual: '15',
      dp: '6',
      af: '0.18',
      ad_alt: '3',
    });
    setUnaffectedGenotypes([...REF_GT_GROUP, ...HET_GT_GROUP]);
  } else if (preset === 'any_affected') {
    filters.max_gnomad_af = '0.01';
    filters.impact = 'HIGH, MODERATE';
    filters.canonical_only = 'true';
    filters.min_cadd = '15';
    setAffectedGenotypes([...HET_GT_GROUP, ...HOM_GT_GROUP], {
      qual: '15',
      dp: '6',
      af: '0.18',
      ad_alt: '3',
    });
  } else if (preset === 'clinvar_review') {
    filters.max_gnomad_af = '0.01';
    filters.clinvar = 'Pathogenic';
    filters.classification =
      'Pathogenic - class 5, Likely Pathogenic - class 4, VUS - class 3';
    filters.has_notes = 'true';
    members.forEach((member) => {
      if (member.affected) {
        sampleFilters[member.sample_id] = {
          ...sampleFilters[member.sample_id],
          gt: [...HET_GT_GROUP, ...HOM_GT_GROUP],
        };
      }
    });
  }

  return { filters, sampleFilters };
};

const countActiveFilters = (
  filters: SmallFilterState,
  sampleFilters: Record<string, SmallVariantSampleFilter>,
): number => {
  const topLevel = Object.entries(filters).reduce((sum, [key, value]) => {
    if (!value.trim()) return sum;
    // Behaviour modifiers, not narrowing filters — don't inflate the count.
    if (key === 'prioritize' || key === 'clinvar_overrides_frequency') return sum;
    if (MULTI_VALUE_FILTER_KEYS.has(key as keyof SmallFilterState)) {
      return sum + parseCommaSeparatedValues(value).length;
    }
    return sum + 1;
  }, 0);

  const sampleLevel = Object.values(sampleFilters).reduce((sum, filter) => {
    const genotypeActive = filter.gt.length > 0 && filter.gt.length < ALL_GT_GROUPS.length ? 1 : 0;
    const thresholdActive = [filter.qual, filter.dp, filter.af, filter.ad_alt].filter(Boolean)
      .length;
    return sum + genotypeActive + thresholdActive;
  }, 0);

  return topLevel + sampleLevel;
};

const hasActiveSampleFilter = (filter: SmallVariantSampleFilter) => {
  if (filter.qual || filter.dp || filter.af || filter.ad_alt) return true;
  return hasNonDefaultGenotypeSelection(filter.gt, ALL_GT_GROUPS);
};

const describeGenotypeSelection = (selection: string[]) => {
  const labels: string[] = [];
  if (HOM_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('Hom');
  if (HET_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('Het');
  if (REF_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('WT');
  return labels.length ? labels.join(' / ') : 'No genotype';
};

// `members` is expected proband-first (the search-state hook sorts it once via
// useMemo); this intentionally does not re-sort per variant row.
export const buildCompactGenotypeSummary = (
  variant: SmallVariant,
  members: FamilyMember[],
) =>
  members
    .map((member) => {
      const genotype = variant.genotypes.find((entry) => entry.sample === member.sample_id);
      return {
        sampleId: member.sample_id,
        affected: member.affected,
        role: member.role,
        gt: genotype?.gt || '—',
      };
    })
    .filter((entry) => entry.gt !== '—');

export const buildSmallVariantQueryParams = (
  currentFilters: SmallFilterState,
  currentSampleFilters: Record<string, SmallVariantSampleFilter>,
  nextPage: number,
  projectId?: string,
) => {
  const params = new URLSearchParams({
    page: String(nextPage),
    page_size: '100',
  });

  if (projectId) {
    params.set('project_id', projectId);
  }

  if (currentFilters.locus) {
    params.set('locus', currentFilters.locus);
    const parsedLocus = parseGeneOrRegionInput(currentFilters.locus);
    if (parsedLocus?.kind === 'region') {
      params.set('chr', parsedLocus.chr);
      params.set('start', parsedLocus.start);
      params.set('end', parsedLocus.end);
    } else if (parsedLocus?.kind === 'gene') {
      params.set('gene', parsedLocus.gene);
    }
  } else {
    if (currentFilters.gene) params.set('gene', currentFilters.gene);
    if (currentFilters.chr) params.set('chr', currentFilters.chr);
    if (currentFilters.start) params.set('start', currentFilters.start);
    if (currentFilters.end) params.set('end', currentFilters.end);
  }
  if (currentFilters.intervals) params.set('intervals', currentFilters.intervals);
  if (currentFilters.inheritance) params.set('inheritance', currentFilters.inheritance);
  // Monogenic NIPT filters; round-trip through the URL so they survive Apply.
  if (currentFilters.min_confidence) params.set('min_confidence', currentFilters.min_confidence);
  parseCommaSeparatedValues(currentFilters.category).forEach((value) => {
    params.append('category', value);
  });
  if (currentFilters.ps) params.set('ps', currentFilters.ps);
  if (currentFilters.expanded_carrier_screening === 'true') {
    params.set('expanded_carrier_screening', 'true');
  }
  if (currentFilters.type) params.set('type', currentFilters.type);
  if (currentFilters.source) params.set('source', currentFilters.source);
  if (currentFilters.transcript) params.set('transcript', currentFilters.transcript);
  parseCommaSeparatedValues(currentFilters.impact).forEach((value) => {
    params.append('impact', value);
  });
  parseCommaSeparatedValues(currentFilters.effect).forEach((value) => {
    params.append('effect', value);
  });
  parseCommaSeparatedValues(currentFilters.clinvar).forEach((value) => {
    params.append('clinvar', value);
  });
  parseCommaSeparatedValues(currentFilters.exclude_clinvar).forEach((value) => {
    params.append('exclude_clinvar', value);
  });
  // Standard on; emit explicitly so an off state survives the URL round-trip
  // (absence is treated as the default — on).
  params.set(
    'clinvar_overrides_frequency',
    currentFilters.clinvar_overrides_frequency === 'true' ? 'true' : 'false',
  );
  // Standard on: only an explicit "false" turns phenotype prioritization off.
  params.set('prioritize', currentFilters.prioritize === 'true' ? 'true' : 'false');
  if (currentFilters.require_sv_second_hit === 'true') {
    params.set('require_sv_second_hit', 'true');
  }
  parseCommaSeparatedValues(currentFilters.exclude_review_tags).forEach((value) => {
    params.append('exclude_review_tag', value);
  });
  if (currentFilters.exclude_gene) params.set('exclude_gene', currentFilters.exclude_gene);
  if (currentFilters.exclude_intervals) params.set('exclude_intervals', currentFilters.exclude_intervals);
  if (currentFilters.rsid) params.set('rsid', currentFilters.rsid);
  if (currentFilters.hgvsc) params.set('hgvsc', currentFilters.hgvsc);
  if (currentFilters.hgvsp) params.set('hgvsp', currentFilters.hgvsp);
  if (currentFilters.canonical_only === 'true') params.set('canonical_only', 'true');
  if (currentFilters.mane_only === 'true') params.set('mane_only', 'true');
  if (currentFilters.lof_only === 'true') params.set('lof_only', 'true');
  if (currentFilters.max_gnomad_af) params.set('max_gnomad_af', currentFilters.max_gnomad_af);
  if (currentFilters.max_gnomad_exomes_af) {
    params.set('max_gnomad_exomes_af', currentFilters.max_gnomad_exomes_af);
  }
  if (currentFilters.max_gnomad_genomes_af) {
    params.set('max_gnomad_genomes_af', currentFilters.max_gnomad_genomes_af);
  }
  if (currentFilters.max_gnomad_popmax_af) {
    params.set('max_gnomad_popmax_af', currentFilters.max_gnomad_popmax_af);
  }
  if (currentFilters.max_topmed_af) params.set('max_topmed_af', currentFilters.max_topmed_af);
  if (currentFilters.max_gnomad_ac) params.set('max_gnomad_ac', currentFilters.max_gnomad_ac);
  if (currentFilters.max_gnomad_hom_count) {
    params.set('max_gnomad_hom_count', currentFilters.max_gnomad_hom_count);
  }
  if (currentFilters.max_gnomad_hemi_count) {
    params.set('max_gnomad_hemi_count', currentFilters.max_gnomad_hemi_count);
  }
  if (currentFilters.min_cadd) params.set('min_cadd', currentFilters.min_cadd);
  if (currentFilters.min_revel) params.set('min_revel', currentFilters.min_revel);
  if (currentFilters.min_spliceai) params.set('min_spliceai', currentFilters.min_spliceai);
  if (currentFilters.sift) params.set('sift', currentFilters.sift);
  if (currentFilters.polyphen) params.set('polyphen', currentFilters.polyphen);
  if (currentFilters.panel_id) params.set('panel_id', currentFilters.panel_id);
  if (currentFilters.has_notes === 'true') params.set('has_notes', 'true');

  parseCommaSeparatedValues(currentFilters.classification).forEach((value) => {
    params.append('classification', value);
  });
  parseCommaSeparatedValues(currentFilters.review_tags).forEach((value) => {
    params.append('review_tag', value);
  });

  Object.entries(currentSampleFilters).forEach(([sample, filter]) => {
    const { gt, qual, dp, af, ad_alt } = filter;
    if (hasActiveSampleFilter(filter)) {
      params.append('sample_filter', [sample, gt.join('|'), qual, dp, af, ad_alt].join(':'));
    }
  });

  return params;
};

export const buildActiveFilterChips = (
  filters: SmallFilterState,
  members: FamilyMember[],
  sampleFilters: Record<string, SmallVariantSampleFilter>,
): ActiveSmallFilterChip[] => {
  const chips: ActiveSmallFilterChip[] = [];

  // Phenotype prioritization is family-aware; surface it as a removable chip
  // (it is hidden from the family-agnostic global explorer, which has no HPO).
  if (filters.prioritize === 'true' && members.length > 0) {
    chips.push({
      id: 'top:prioritize',
      label: SMALL_FILTER_LABELS.prioritize,
      kind: 'top',
      key: 'prioritize',
      value: 'true',
    });
  }

  const skipDerivedLocusKeys = filters.locus
    ? new Set<keyof SmallFilterState>(['chr', 'start', 'end', 'gene'])
    : null;

  (Object.entries(filters) as [keyof SmallFilterState, string][]).forEach(([key, value]) => {
    if (!value) return;
    if (skipDerivedLocusKeys?.has(key)) return;
    // Prioritization is surfaced as its own toggle, not as a removable chip.
    // (The ClinVar-overrides-frequency modifier does get a chip when on.)
    if (key === 'prioritize') return;
    if (MULTI_VALUE_FILTER_KEYS.has(key)) {
      parseCommaSeparatedValues(value).forEach((entry) => {
        chips.push({
          id: `top:${key}:${entry}`,
          label: `${SMALL_FILTER_LABELS[key]}: ${entry}`,
          kind: 'top',
          key,
          value: entry,
        });
      });
      return;
    }
    if (key === 'gene') {
      const genes = value
        .split(/\s|,|;/)
        .map((entry) => entry.trim())
        .filter(Boolean);
      chips.push({
        id: `top:${key}`,
        label: `${SMALL_FILTER_LABELS[key]}: ${genes.length} gene${genes.length === 1 ? '' : 's'}`,
        kind: 'top',
        key,
        value,
      });
      return;
    }
    if (key === 'intervals' || key === 'exclude_intervals') {
      const intervals = value
        .split(/\n|;/)
        .map((entry) => entry.trim())
        .filter(Boolean);
      chips.push({
        id: `top:${key}`,
        label: `${SMALL_FILTER_LABELS[key]}: ${intervals.length} interval${intervals.length === 1 ? '' : 's'}`,
        kind: 'top',
        key,
        value,
      });
      return;
    }
    if (key === 'inheritance') {
      chips.push({
        id: `top:${key}`,
        label: `${SMALL_FILTER_LABELS[key]}: ${SMALL_INHERITANCE_LABELS[value] || value}`,
        kind: 'top',
        key,
        value,
      });
      return;
    }
    chips.push({
      id: `top:${key}`,
      label:
        value === 'true'
          ? SMALL_FILTER_LABELS[key]
          : `${SMALL_FILTER_LABELS[key]}: ${value}`,
      kind: 'top',
      key,
      value,
    });
  });

  members.forEach((member) => {
    const filter = sampleFilters[member.sample_id];
    if (!filter) return;

    if (hasNonDefaultGenotypeSelection(filter.gt, ALL_GT_GROUPS)) {
      chips.push({
        id: `sample:${member.sample_id}:gt`,
        label: `${member.sample_id}: ${describeGenotypeSelection(filter.gt)}`,
        kind: 'sample-gt',
        sample: member.sample_id,
      });
    }

    (Object.entries(SAMPLE_FIELD_LABELS) as [
      Exclude<keyof SmallVariantSampleFilter, 'gt'>,
      string,
    ][]).forEach(([field, label]) => {
      const value = filter[field];
      if (!value) return;
      chips.push({
        id: `sample:${member.sample_id}:${field}`,
        label: `${member.sample_id} ${label} ${value}`,
        kind: 'sample-field',
        sample: member.sample_id,
        field,
      });
    });
  });

  return chips;
};

export const serializePresetFilters = (filters: SmallFilterState): Record<string, unknown> =>
  Object.fromEntries(
    (Object.entries(filters) as [keyof SmallFilterState, string][])
      .filter(([, value]) => value.trim())
      .map(([key, value]) => {
        if (MULTI_VALUE_FILTER_KEYS.has(key)) {
          return [key, parseCommaSeparatedValues(value)];
        }
        if (
          key === 'has_notes' ||
          key === 'expanded_carrier_screening' ||
          key === 'prioritize' ||
          key === 'require_sv_second_hit'
        ) {
          return [key, value === 'true'];
        }
        return [key, value];
      }),
  );

export const deserializePresetFilters = (
  filters: Record<string, unknown> | undefined,
): SmallFilterState => {
  const nextFilters = createEmptySmallFilters();
  if (!filters) return nextFilters;
  (Object.keys(nextFilters) as (keyof SmallFilterState)[]).forEach((key) => {
    nextFilters[key] = normalizeStoredFilterValue(key, filters[key]);
  });
  return nextFilters;
};

export const buildSampleTemplatesForPreset = (
  members: FamilyMember[],
  sampleFilters: Record<string, SmallVariantSampleFilter>,
) => {
  const templates: Record<string, SmallVariantSampleFilter> = {};
  const activeMembers = members.filter((member) =>
    hasActiveSampleFilter(sampleFilters[member.sample_id]),
  );

  if (!activeMembers.length) {
    return templates;
  }

  let sharedTemplate: SmallVariantSampleFilter | null = null;
  if (activeMembers.length === members.length) {
    const firstFilter = sampleFilters[members[0].sample_id];
    if (firstFilter && members.every((member) => sampleFiltersEqual(sampleFilters[member.sample_id], firstFilter))) {
      sharedTemplate = cloneSingleSampleFilter(firstFilter);
      templates.all = sharedTemplate;
    }
  }

  activeMembers.forEach((member) => {
    const filter = sampleFilters[member.sample_id];
    if (!filter) return;
    if (sharedTemplate && sampleFiltersEqual(filter, sharedTemplate)) {
      return;
    }

    const normalizedFilter = cloneSingleSampleFilter(filter);
    const templateKeys = [`role:${member.role}`, member.affected ? 'status:affected' : 'status:unaffected'];
    if (member.role === 'proband') {
      templateKeys.push('proband');
    }

    templateKeys.forEach((templateKey) => {
      if (!templates[templateKey]) {
        templates[templateKey] = normalizedFilter;
      }
    });
  });

  return templates;
};

export const resolveSampleFiltersFromPreset = (
  preset: SmallVariantFilterPreset,
  members: FamilyMember[],
) => {
  const exactFilters = normalizeStoredSampleFilterMap(preset.sample_filters);
  const templateFilters = normalizeStoredSampleFilterMap(preset.sample_templates);
  const defaults = buildDefaultSampleFilters(members);

  return Object.fromEntries(
    members.map((member) => {
      let resolvedFilter = cloneSingleSampleFilter(defaults[member.sample_id]);
      const templateKeys = [
        'all',
        member.affected ? 'status:affected' : 'status:unaffected',
        `role:${member.role}`,
        member.role,
        member.role === 'proband' ? 'proband' : null,
      ].filter((value): value is string => Boolean(value));

      templateKeys.forEach((templateKey) => {
        resolvedFilter = mergePresetSampleFilter(resolvedFilter, templateFilters[templateKey]);
      });

      resolvedFilter = mergePresetSampleFilter(resolvedFilter, exactFilters[member.sample_id]);
      return [member.sample_id, resolvedFilter];
    }),
  );
};

type UseSmallVariantSearchStateArgs = {
  family?: SmallVariantFamily;
  locationSearch: string;
  navigate: NavigateFunction;
  resolvedProjectId?: string;
  // The Mendeliome panel id, used to scope the default (fresh-open) search.
  mendeliomePanelId?: string;
  // Whether the panels query has settled, so the default can include the panel.
  panelsLoaded?: boolean;
};

export const useSmallVariantSearchState = ({
  family,
  locationSearch,
  navigate,
  resolvedProjectId,
  mendeliomePanelId,
  panelsLoaded = true,
}: UseSmallVariantSearchStateArgs) => {
  const emptyFilters = useMemo(() => createEmptySmallFilters(), []);
  const members = useMemo(
    () => sortFamilyMembersProbandFirst(family?.members || []),
    [family?.members],
  );
  const relationships = family?.relationships || [];

  const [filters, setFilters] = useState(emptyFilters);
  const [draftFilters, setDraftFilters] = useState(emptyFilters);
  const [sampleFilters, setSampleFilters] = useState<Record<string, SmallVariantSampleFilter>>({});
  const [sampleDraftFilters, setSampleDraftFilters] = useState<Record<
    string,
    SmallVariantSampleFilter
  >>({});
  const [page, setPage] = useState(1);
  const urlProjectId = useMemo(
    () => new URLSearchParams(locationSearch).get('project_id') || undefined,
    [locationSearch],
  );
  const queryProjectId = resolvedProjectId || urlProjectId;

  // Apply the fresh-open default (Phenotype-priority preset + Mendeliome panel) once
  // per family — keyed by the member set, since the family object has no stable id.
  const familyKey = useMemo(() => members.map((member) => member.sample_id).join('|'), [members]);
  const defaultInitKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!family) return;

    const params = new URLSearchParams(locationSearch);
    const hasExplicitSearch = Array.from(params.keys()).some(
      (key) => key !== 'project_id' && key !== 'page',
    );

    // No deep-linked search: default the page to the Phenotype-priority preset scoped
    // to the Mendeliome panel. Wait for the panels query to settle so the panel is
    // included, and apply only once per family (so a later Clear stays cleared).
    if (!hasExplicitSearch) {
      if (!panelsLoaded || defaultInitKeyRef.current === familyKey) return;
      defaultInitKeyRef.current = familyKey;
      const presetState = members.length
        ? buildPresetState('phenotype_priority', members)
        : {
            filters: createEmptySmallFilters(),
            sampleFilters: buildDefaultSampleFilters(family.members),
          };
      const defaultFilters: SmallFilterState = {
        ...presetState.filters,
        panel_id: mendeliomePanelId ?? '',
      };
      setFilters(defaultFilters);
      setDraftFilters(defaultFilters);
      setSampleFilters(cloneSampleFilters(presetState.sampleFilters));
      setSampleDraftFilters(cloneSampleFilters(presetState.sampleFilters));
      setPage(1);
      return;
    }
    // An explicit search counts as initialised, so Clear/Reset won't re-default.
    defaultInitKeyRef.current = familyKey;

    const initialFilters = { ...emptyFilters };
    (Object.keys(initialFilters) as (keyof SmallFilterState)[]).forEach((key) => {
      if (
        key === 'impact' ||
        key === 'effect' ||
        key === 'clinvar' ||
        key === 'exclude_clinvar' ||
        key === 'category'
      ) {
        initialFilters[key] = joinFilterValues(params.getAll(key));
        return;
      }
      if (key === 'classification') {
        initialFilters.classification = joinFilterValues(params.getAll('classification'));
        return;
      }
      if (key === 'review_tags') {
        initialFilters.review_tags = joinFilterValues(params.getAll('review_tag'));
        return;
      }
      if (key === 'exclude_review_tags') {
        initialFilters.exclude_review_tags = joinFilterValues(params.getAll('exclude_review_tag'));
        return;
      }
      if (key === 'has_notes') {
        initialFilters.has_notes = params.get('has_notes') === 'true' ? 'true' : '';
        return;
      }
      if (key === 'expanded_carrier_screening') {
        initialFilters.expanded_carrier_screening =
          params.get('expanded_carrier_screening') === 'true' ? 'true' : '';
        return;
      }
      if (key === 'prioritize') {
        // Standard on: only an explicit "false" turns it off; absence keeps the default.
        initialFilters.prioritize =
          params.get('prioritize') === 'false' ? '' : 'true';
        return;
      }
      if (key === 'clinvar_overrides_frequency') {
        // Standard on: only an explicit "false" turns it off; absence keeps the default.
        initialFilters.clinvar_overrides_frequency =
          params.get('clinvar_overrides_frequency') === 'false' ? '' : 'true';
        return;
      }
      const value = params.get(key);
      if (value !== null) initialFilters[key] = value;
    });
    if (initialFilters.locus) {
      initialFilters.chr = '';
      initialFilters.start = '';
      initialFilters.end = '';
      initialFilters.gene = '';
    }

    const pageParam = params.get('page');
    const parsedPage = pageParam ? Math.max(1, parseInt(pageParam, 10) || 1) : 1;

    const initialSampleFilters = buildDefaultSampleFilters(family.members);
    params.getAll('sample_filter').forEach((entry) => {
      const parts = entry.split(':');
      const sample = parts[0];
      if (!sample || !initialSampleFilters[sample]) return;
      initialSampleFilters[sample] = {
        gt: parseSerializedGenotypeSelection(entry, initialSampleFilters[sample].gt),
        qual: parts[2] ?? '',
        dp: parts[3] ?? '',
        af: parts[4] ?? '',
        ad_alt: parts[5] ?? '',
      };
    });

    setFilters(initialFilters);
    setDraftFilters(initialFilters);
    setSampleFilters(cloneSampleFilters(initialSampleFilters));
    setSampleDraftFilters(cloneSampleFilters(initialSampleFilters));
    setPage(parsedPage);
  }, [
    emptyFilters,
    family,
    locationSearch,
    members,
    familyKey,
    mendeliomePanelId,
    panelsLoaded,
  ]);

  const applySearchState = (
    nextFilters: SmallFilterState,
    nextSampleFilters: Record<string, SmallVariantSampleFilter>,
    nextPage: number,
  ) => {
    setDraftFilters(nextFilters);
    setFilters(nextFilters);
    setSampleDraftFilters(cloneSampleFilters(nextSampleFilters));
    setSampleFilters(cloneSampleFilters(nextSampleFilters));
    setPage(nextPage);
    navigate({
      search: buildSmallVariantQueryParams(
        nextFilters,
        nextSampleFilters,
        nextPage,
        queryProjectId,
      ).toString(),
    });
  };

  const setDraftFilterValue = (name: keyof SmallFilterState, value: string) => {
    setDraftFilters((prev) => ({ ...prev, [name]: value }));
  };

  const toggleDraftFilterListValue = (
    key: Extract<
      keyof SmallFilterState,
      | 'impact'
      | 'effect'
      | 'clinvar'
      | 'exclude_clinvar'
      | 'exclude_review_tags'
      | 'classification'
      | 'review_tags'
      | 'category'
    >,
    value: string,
  ) => {
    setDraftFilters((prev) => {
      const current = new Set(parseCommaSeparatedValues(prev[key]));
      if (current.has(value)) {
        current.delete(value);
      } else {
        current.add(value);
      }
      return { ...prev, [key]: joinFilterValues(current) };
    });
  };

  const handleFilterChange = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = event.target;
    setDraftFilterValue(name as keyof SmallFilterState, value);
  };

  const handleSampleFieldChange = (
    sample: string,
    field: keyof SmallVariantSampleFilter,
    value: string,
  ) => {
    setSampleDraftFilters((prev) => ({
      ...prev,
      [sample]: { ...prev[sample], [field]: value },
    }));
  };

  const handleGtToggle = (sample: string, group: string, checked: boolean) => {
    setSampleDraftFilters((prev) => {
      const current = prev[sample]?.gt ?? [];
      const next = new Set(current);
      const groupValues =
        group === 'hom-group'
          ? HOM_GT_GROUP
          : group === 'het-group'
            ? HET_GT_GROUP
            : REF_GT_GROUP;

      if (checked) {
        groupValues.forEach((gt) => next.add(gt));
      } else {
        groupValues.forEach((gt) => next.delete(gt));
      }

      return {
        ...prev,
        [sample]: { ...prev[sample], gt: Array.from(next) },
      };
    });
  };

  const applyPreset = (preset: SmallPreset) => {
    if (!members.length) return;
    const presetState = buildPresetState(preset, members);
    setDraftFilters(presetState.filters);
    setSampleDraftFilters(cloneSampleFilters(presetState.sampleFilters));
  };

  const applySavedPreset = (preset: SmallVariantFilterPreset) => {
    if (!members.length) return;
    const nextFilters = deserializePresetFilters(preset.filters);
    const nextSampleFilters = resolveSampleFiltersFromPreset(preset, members);
    // Populate the draft only; the single "Apply filters" button runs the search.
    setDraftFilters(nextFilters);
    setSampleDraftFilters(cloneSampleFilters(nextSampleFilters));
  };

  const handleApply = (event: FormEvent) => {
    event.preventDefault();
    const nextSampleFilters = cloneSampleFilters(sampleDraftFilters);
    applySearchState(draftFilters, nextSampleFilters, 1);
  };

  const handleReset = () => {
    if (!family) return;
    const resetSampleFilters = buildDefaultSampleFilters(family.members);
    setDraftFilters(emptyFilters);
    setFilters(emptyFilters);
    setSampleDraftFilters(cloneSampleFilters(resetSampleFilters));
    setSampleFilters(cloneSampleFilters(resetSampleFilters));
    setPage(1);
    navigate({
      search: queryProjectId
        ? buildSmallVariantQueryParams(
            emptyFilters,
            resetSampleFilters,
            1,
            queryProjectId,
          ).toString()
        : '',
    });
  };

  const activeFilterCount = useMemo(
    () => countActiveFilters(filters, sampleFilters),
    [filters, sampleFilters],
  );

  const activeFilterChips = useMemo(
    () => buildActiveFilterChips(filters, members, sampleFilters),
    [filters, members, sampleFilters],
  );

  const removeActiveFilterChip = (chip: ActiveSmallFilterChip) => {
    const nextFilters = { ...filters };
    const nextSampleFilters = cloneSampleFilters(sampleFilters);

    if (chip.kind === 'top') {
      if (MULTI_VALUE_FILTER_KEYS.has(chip.key) && chip.value) {
        nextFilters[chip.key] = joinFilterValues(
          parseCommaSeparatedValues(nextFilters[chip.key]).filter((value) => value !== chip.value),
        );
      } else {
        nextFilters[chip.key] = '';
      }
      if (chip.key === 'locus') {
        nextFilters.chr = '';
        nextFilters.start = '';
        nextFilters.end = '';
        nextFilters.gene = '';
      }
    } else if (chip.kind === 'sample-gt') {
      nextSampleFilters[chip.sample] = {
        ...nextSampleFilters[chip.sample],
        gt: [...ALL_GT_GROUPS],
      };
    } else {
      nextSampleFilters[chip.sample] = {
        ...nextSampleFilters[chip.sample],
        [chip.field]: '',
      };
    }

    applySearchState(nextFilters, nextSampleFilters, 1);
  };

  const requestQueryString = useMemo(
    () => buildSmallVariantQueryParams(filters, sampleFilters, page, queryProjectId).toString(),
    [filters, sampleFilters, page, queryProjectId],
  );

  const goToPage = (nextPage: number) => {
    applySearchState(filters, sampleFilters, nextPage);
  };

  return {
    activeFilterChips,
    activeFilterCount,
    applyPreset,
    applySavedPreset,
    draftFilters,
    emptyFilters,
    filters,
    goToPage,
    handleApply,
    handleFilterChange,
    handleGtToggle,
    handleReset,
    handleSampleFieldChange,
    members,
    relationships,
    page,
    removeActiveFilterChip,
    requestQueryString,
    sampleDraftFilters,
    sampleFilters,
    setDraftFilterValue,
    toggleDraftFilterListValue,
  };
};

export const buildPresetPayload = ({
  filters,
  members,
  sampleFilters,
}: {
  filters: SmallFilterState;
  members: FamilyMember[];
  sampleFilters: Record<string, SmallVariantSampleFilter>;
}) => {
  const activeSampleFilters = Object.fromEntries(
    Object.entries(sampleFilters)
      .filter(([, filter]) => hasActiveSampleFilter(filter))
      .map(([sample, filter]) => [sample, cloneSingleSampleFilter(filter)]),
  );

  return {
    filters: serializePresetFilters(filters),
    sample_filters: activeSampleFilters,
    sample_templates: buildSampleTemplatesForPreset(members, sampleFilters),
  };
};

export const countPresetRules = (preset: SmallVariantFilterPreset) => {
  const filterCount = Object.keys(preset.filters || {}).length;
  const sampleCount = Object.keys(preset.sample_filters || {}).length;
  const templateCount = Object.keys(preset.sample_templates || {}).length;
  return filterCount + sampleCount + templateCount;
};

export const getPresetScopeLabel = (scope: SmallVariantFilterPreset['scope']) =>
  scope === 'family' ? 'Family' : 'Reusable';

export const getTagDefinitionMap = (tags: SmallVariantTagDefinition[]) =>
  Object.fromEntries(tags.map((tag) => [tag.key, tag]));

export const sortTagDefinitions = (tags: SmallVariantTagDefinition[]) =>
  [...tags].sort((left, right) => {
    const groupOrder = ['collaboration', 'classification', 'custom'];
    const groupDiff =
      groupOrder.indexOf(left.group) - groupOrder.indexOf(right.group);
    if (groupDiff !== 0) return groupDiff;
    if (left.sort_order !== right.sort_order) return left.sort_order - right.sort_order;
    return left.label.localeCompare(right.label, undefined, { sensitivity: 'base' });
  });

export const normalizeTagKeys = (keys: Iterable<string>) =>
  Array.from(new Set(Array.from(keys).map((key) => key.trim()).filter(Boolean))).sort((a, b) =>
    a.localeCompare(b),
  );

const LEGACY_CLASSIFICATION_MAP: Record<string, string> = {
  pathogenic: 'acmg_class_5',
  'likely pathogenic': 'acmg_class_4',
  vus: 'acmg_class_3',
  'likely benign': 'acmg_class_2',
  benign: 'acmg_class_1',
};

export const getClassificationTagKeyFromClassification = (value?: string | null) => {
  const normalized = value?.trim().toLowerCase() || '';
  if (!normalized) return '';
  return LEGACY_CLASSIFICATION_MAP[normalized] || '';
};

export const getClassificationTagKeyFromTags = (tags: Iterable<string>) =>
  ACMG_CLASSIFICATION_TAG_KEYS.find((key) => Array.from(tags).includes(key)) || '';

export const getClassificationLabelFromTagKey = (tagKey?: string | null) =>
  (tagKey ? ACMG_CLASSIFICATION_LABELS[tagKey] : '') || '';

export const normalizeReviewClassification = (value?: string | null, tags?: Iterable<string>) => {
  const explicitTagKey = getClassificationTagKeyFromClassification(value);
  if (explicitTagKey) {
    return getClassificationLabelFromTagKey(explicitTagKey);
  }
  const derivedTagKey = tags ? getClassificationTagKeyFromTags(tags) : '';
  if (derivedTagKey) {
    return getClassificationLabelFromTagKey(derivedTagKey);
  }
  return value?.trim() || '';
};

export const mergePresetSampleFilter = (
  base: SmallVariantSampleFilter,
  override: Partial<SmallVariantSampleFilter> | null | undefined,
) => {
  const merged = cloneSingleSampleFilter(base);
  if (!override) return merged;
  SINGLE_SAMPLE_FILTER_KEYS.forEach((key) => {
    if (key === 'gt') {
      if (override.gt?.length) merged.gt = [...override.gt];
      return;
    }
    const nextValue = override[key];
    if (typeof nextValue === 'string') merged[key] = nextValue;
  });
  return merged;
};

export type SmallVariantSearchState = ReturnType<typeof useSmallVariantSearchState>;
