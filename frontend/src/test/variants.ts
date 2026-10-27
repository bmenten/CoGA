import type {
  GenotypeOut,
  NiptClassificationOut,
  SvSecondHitOut,
  VariantOut,
} from '../lib/apiSchema.generated';
import type {
  SmallVariant,
  SmallVariantReview,
  SmallVariantTranscript,
} from '../pages/families/smallVariantSearch';
import type { StructuralVariant } from '../pages/families/structuralVariantSearch';

// What the family variant lists send for a variant without the field (VariantOut): null, an
// empty list or map, false.
const ABSENT_VARIANT_FIELDS: Omit<
  VariantOut,
  '_id' | 'chr' | 'start' | 'end' | 'type' | 'review' | 'annotation_extra'
> = {
  length: null,
  source: null,
  qual: null,
  read_support: null,
  filter: null,
  remote_chr: null,
  remote_start: null,
  ref: null,
  alt: null,
  ps: null,
  gene: null,
  gene_id: null,
  gene_symbols: [],
  gene_count: null,
  transcript_id: null,
  feature_type: null,
  transcript_biotype: null,
  impact: null,
  effect: null,
  clinvar: null,
  rsid: null,
  hgvsc: null,
  hgvsp: null,
  canonical: false,
  mane_select: false,
  mane_plus_clinical: false,
  exon: null,
  intron: null,
  lof: null,
  lof_filter: null,
  lof_flags: null,
  gnomad_af: null,
  gnomad_hom_count: null,
  gene_pli: null,
  gene_missense_z: null,
  population_frequencies: {},
  cadd_raw: null,
  cadd_phred: null,
  revel: null,
  sift: null,
  polyphen: null,
  spliceai_ds_ag: null,
  spliceai_ds_al: null,
  spliceai_ds_dg: null,
  spliceai_ds_dl: null,
  spliceai_max: null,
  alpha_missense_pathogenicity: null,
  alpha_missense_class: null,
  cytoband: null,
  transcripts: [],
  filter_transcript: null,
  genotypes: [],
  hemizygous_in_males: false,
  copy_change: null,
  internal_cohort: null,
  priority: null,
  sv_second_hit: null,
};

type Locus = Pick<VariantOut, '_id' | 'chr' | 'start' | 'end' | 'type'>;

/** A small variant as the family's list serves it, the fields a test leaves out absent. */
export const smallVariant = (fields: Locus & Partial<SmallVariant>): SmallVariant => ({
  ...ABSENT_VARIANT_FIELDS,
  annotation_extra: {},
  review: null,
  ...fields,
});

/** An SV as the family's SV list serves it, the fields a test leaves out absent. */
export const structuralVariant = (
  fields: Locus & Partial<StructuralVariant>,
): StructuralVariant => ({
  ...ABSENT_VARIANT_FIELDS,
  annotation_extra: {},
  review: null,
  ...fields,
});

/** A sample's call, the fields a test leaves out absent. */
export const genotype = (
  fields: Pick<GenotypeOut, 'sample' | 'gt'> & Partial<GenotypeOut>,
): GenotypeOut => ({
  dp: null,
  ad: null,
  af: null,
  read_support: null,
  qual: null,
  filter: null,
  ps: null,
  gq: null,
  cn: null,
  ...fields,
});

/** One of a variant's transcripts, the fields a test leaves out absent. */
export const transcript = (fields: Partial<SmallVariantTranscript>): SmallVariantTranscript => ({
  gene: null,
  gene_id: null,
  transcript_id: null,
  transcript_source: null,
  feature_type: null,
  transcript_biotype: null,
  impact: null,
  effect: null,
  hgvsc: null,
  hgvsp: null,
  exon: null,
  intron: null,
  canonical: false,
  mane_select: false,
  mane_plus_clinical: false,
  primary: false,
  ...fields,
});

/** A variant's review, the fields a test leaves out absent: no tags, note or classification. */
export const review = (
  fields: Pick<SmallVariantReview, 'variant_id'> & Partial<SmallVariantReview>,
): SmallVariantReview => ({
  classification: null,
  tags: [],
  tag_metadata: {},
  note: null,
  updated_by: null,
  updated_at: null,
  compound_het: null,
  acmg: null,
  cnv_acmg: null,
  acmg_unreadable: false,
  ...fields,
});

/** The SV second-hit badge's data, the fields a test leaves out absent. */
export const svSecondHit = (
  fields: Pick<SvSecondHitOut, 'sv_count' | 'sv_types' | 'has_deletion' | 'phase'> &
    Partial<SvSecondHitOut>,
): SvSecondHitOut => ({
  gene: null,
  chr: null,
  start: null,
  end: null,
  affected_zygosity: null,
  phase_evidence: null,
  deletion_unmasked: false,
  svs: [],
  ...fields,
});

/** A NIPT classification, the fields a test leaves out absent. */
export const niptClassification = (
  fields: Pick<
    NiptClassificationOut,
    'category_label' | 'maternal_state' | 'fetal_inheritance' | 'expected_vaf' | 'confidence'
  > &
    Partial<NiptClassificationOut>,
): NiptClassificationOut => ({
  category: null,
  observed_vaf: null,
  flags: [],
  runner_up_category: null,
  runner_up_confidence: null,
  paternal_transmission_probability: null,
  maternal_allele_probability: null,
  fetal_hom_alt_probability: null,
  fetal_genotype_posterior: null,
  quality_failures: [],
  cf_alt_reads: null,
  cf_depth: null,
  cf_depth_estimated: false,
  father_state: null,
  father_vaf: null,
  father_depth: null,
  de_novo: null,
  ...fields,
});
