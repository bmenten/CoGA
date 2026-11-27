import type { ApiNiptCoverageLowRegion, NiptLowCoverageReason } from '../../lib/apiTypes';
import type { NiptRecessiveGeneOut } from '../../lib/apiSchema.generated';
import { holdsAnalysis } from '../../lib/familyAnalyses';

// Shared presentation helpers for the monogenic NIPT views (the workbench page
// and the clinical report). Kept separate from the filter-state presets in
// smallVariantSearch so both surfaces format categories / coverage identically.

/** The analysis type of a monogenic NIPT, among a family's analysis types (`familyAnalysisTypes`). */
export const MONOGENIC_NIPT_ANALYSIS_TYPE = 'monogenic_nipt';

/** Whether a family holds a monogenic NIPT (it may hold a conventional NIPT beside it). */
export const isMonogenicNipt = (metadata: unknown): boolean => holdsAnalysis(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE);

// How the NIPT quality checks' tokens read (nipt_quality_checks), on the screen's QC panel and
// in the printed report alike: the report printed the raw tokens, "female_no_chrY_signal"
// where the panel reads "no chrY signal".
export const NIPT_FETAL_SEX_LABEL: Record<string, string> = {
  female: 'Female',
  male: 'Male',
  indeterminate: 'Indeterminate',
  discordant: 'Discordant',
};

export const NIPT_CHRY_PROFILE_LABEL: Record<string, string> = {
  female_no_chrY_signal: 'no chrY signal',
  female_with_male_fetal_signal: 'male fetal chrY signal',
  high_chrY_review: 'chrY near the autosomal level',
  male_like_not_maternal_plasma: 'male DNA',
  no_chrY_targets: 'no chrY target in the panel',
};

export const niptFetalSexLabel = (call: string): string => NIPT_FETAL_SEX_LABEL[call] ?? call;

/** The chrY coverage profile, or "no target table" when the family has none. */
export const niptChryProfileLabel = (profile: string | null | undefined): string =>
  profile ? (NIPT_CHRY_PROFILE_LABEL[profile] ?? profile) : 'no target table';

export const CATEGORY_LABELS: Record<number, string> = {
  1: 'De novo in fetus',
  2: 'Maternal het, not inherited',
  3: 'Maternal het, inherited',
  4: 'Maternal het → hom fetus',
  5: 'Maternal hom, het fetus',
  6: 'Maternal & fetal hom',
  7: 'Paternal, transmitted',
  8: 'Paternal hom-alt, absent (FN)',
};

export interface NiptInheritanceGroup {
  key: string;
  label: string;
  description: string;
  categories: number[];
}

// The three clinically actionable buckets mirror the De novo / dominant /
// Recessive built-in presets. Any category not listed here (2 = not inherited,
// 8 = paternal false-negative, unclassified) falls into the report's "Other"
// catch-all rather than a candidate group.
export const NIPT_INHERITANCE_GROUPS: NiptInheritanceGroup[] = [
  {
    key: 'de_novo',
    label: 'De novo in fetus',
    description: 'Present in the fetus and absent in both parents (category 1).',
    categories: [1],
  },
  {
    key: 'dominant',
    label: 'Dominant, transmitted',
    description: 'A heterozygous variant transmitted from a parent (categories 3 and 7).',
    categories: [3, 7],
  },
  {
    key: 'recessive',
    label: 'Recessive / biallelic risk',
    description:
      'Maternal and/or fetal homozygous states relevant to recessive disease (categories 4–6).',
    categories: [4, 5, 6],
  },
];

/** The terms of a NIPT search's gene query, split as the backend splits it. */
export const niptGeneTerms = (gene?: string): string[] =>
  gene ? gene.split(/[\s,;]+/).filter(Boolean) : [];

export const pct = (value?: number | null): string =>
  value == null ? '—' : `${(value * 100).toFixed(1)}%`;

/**
 * The QC gate's bound as it applied (REQ-NIPT-021): QA's, with its warning bound, or the
 * provisional one, which the sign-out asks a reason for.
 */
export const describeNiptGateBound = (gate: {
  error_value?: number | null;
  warn_value?: number | null;
  provisional: boolean;
}): string =>
  `A fetal-specific allele has about median target depth × FF/2 reads. Bound: ${
    gate.error_value == null ? 'no error bound set' : `at least ${gate.error_value}`
  }${gate.warn_value != null ? `, warning below ${gate.warn_value}` : ''}${
    gate.provisional ? ' (provisional: QA has not set it)' : ' (set by QA)'
  }.`;

export type RecessiveStatus = NiptRecessiveGeneOut['status'];
export type RecessiveMode = NiptRecessiveGeneOut['mode'];

// The fetal status of a recessive gene (nipt_triage): affected, carrier, unaffected, not
// affected with the carrier status not told (one allele surely not inherited, the other not
// told), or undetermined (an inheritance between 20% and 80%, or one the cfDNA did not tell).
export const RECESSIVE_STATUS_LABELS: Record<RecessiveStatus, string> = {
  affected: 'Affected',
  carrier: 'Carrier',
  unaffected: 'Unaffected',
  carrier_or_unaffected: 'Not affected (carrier status undetermined)',
  undetermined: 'Undetermined',
};

const RECESSIVE_STATUS_TONES: Record<RecessiveStatus, string> = {
  affected: 'critical',
  undetermined: 'warning',
  carrier: 'success',
  carrier_or_unaffected: 'success',
  unaffected: 'success',
};

export const recessiveStatusTone = (status: RecessiveStatus): string =>
  `table-chip table-chip--${RECESSIVE_STATUS_TONES[status]}`;

// How a recessive gene's chrX is read (nipt_triage.RecessiveMode), as the report names it; an
// autosome needs no name.
export const RECESSIVE_MODE_LABELS: Record<RecessiveMode, string | null> = {
  autosomal: null,
  x_linked_son: 'X-linked, a son: his mother’s allele alone',
  x_linked_daughter: 'X-linked, a daughter: paired as an autosome, one parent’s allele at most a carrier',
  x_linked_sex_unknown: 'X-linked, the fetal sex not known: each parent’s alleles',
};

/** Whether a signed record's text is one of the statuses. */
export const isRecessiveStatus = (value: string): value is RecessiveStatus => Object.hasOwn(RECESSIVE_STATUS_LABELS, value);

/** Whether a signed record's text is one of the modes. */
export const isRecessiveMode = (value: string): value is RecessiveMode => Object.hasOwn(RECESSIVE_MODE_LABELS, value);

/**
 * "affected 92.0%, carrier 7.0%, unaffected 1.0%": a recessive gene's fetal probabilities by its
 * `mode`. A son's X-linked gene has no carrier state: his mother's allele alone makes him
 * affected. Of a fetus whose sex is not known none is told (null).
 */
export const recessiveProbabilitiesText = (
  affected: number | null,
  carrier: number | null,
  unaffected: number | null,
  mode: RecessiveMode,
): string | null =>
  mode === 'x_linked_sex_unknown'
    ? null
    : [`affected ${pct(affected)}`, mode === 'x_linked_son' ? null : `carrier ${pct(carrier)}`, `unaffected ${pct(unaffected)}`]
        .filter(Boolean)
        .join(', ');

/**
 * The alleles a recessive gene's status rests on: "maternal A, paternal B", "A, a site both
 * parents carry", or one parent's allele alone, each by its variant ID or as `name` names it;
 * null when nothing is paired (the fetal sex not known).
 */
export const recessivePairText = (
  maternal: string | null,
  paternal: string | null,
  name: (variantId: string) => string = (variantId) => variantId,
): string | null => {
  if (maternal && paternal) {
    return maternal === paternal
      ? `${name(maternal)}, a site both parents carry`
      : `maternal ${name(maternal)}, paternal ${name(paternal)}`;
  }
  if (maternal) return `maternal ${name(maternal)}`;
  return paternal ? `paternal ${name(paternal)}` : null;
};

type RecessiveGeneReading = Pick<NiptRecessiveGeneOut, 'status' | 'mode' | 'maternal' | 'paternal'>;

/** Whether the mother and the father each carry an allele of the gene. */
export const bothParentsCarry = (gene: Pick<NiptRecessiveGeneOut, 'maternal' | 'paternal'>): boolean =>
  gene.maternal.length > 0 && gene.paternal.length > 0;

const PAIR_INHERITANCE: Record<RecessiveStatus, string> = {
  affected: 'Both variants inherited: affected',
  carrier: 'One of the two variants inherited: a carrier',
  unaffected: 'Neither variant inherited',
  carrier_or_unaffected: 'Not both inherited: one variant surely not, the other not told',
  undetermined: 'Not told which of the variants the fetus inherited',
};

/**
 * Which of the gene's alleles the fetus inherited, in words: both, one of the two or neither for
 * a pair (an autosome, a daughter's X); a daughter's one parent's allele; a son's mother's X
 * allele; nothing told of a fetus whose sex is not known.
 */
export const recessiveInheritanceText = (gene: RecessiveGeneReading): string => {
  if (gene.mode === 'x_linked_sex_unknown') return 'Not told whether the fetus is affected';
  if (gene.mode === 'x_linked_son') {
    if (gene.status === 'affected') return "Inherited his mother's variant: affected";
    if (gene.status === 'unaffected') return "Did not inherit his mother's variant";
    return "Not told whether he inherited his mother's variant";
  }
  if (!bothParentsCarry(gene)) {
    // A daughter's X: one parent's allele alone.
    if (gene.status === 'carrier') return 'Inherited the variant: a carrier';
    if (gene.status === 'unaffected') return 'Did not inherit the variant';
    return 'Not told whether she inherited the variant: a carrier at most';
  }
  return PAIR_INHERITANCE[gene.status];
};

// The father's genotype classes (nipt_analysis.FATHER_STATES): his HaplotypeCaller genotype, or
// his allele depths for a call without a GQ (nipt_service.derive_father_state).
export const FATHER_STATE_LABELS: Record<string, string> = {
  hom_ref: 'reference',
  absent: 'no call (reference)',
  het: 'het',
  hom_alt: 'hom-alt',
  low_vaf: 'low-level signal',
  low_support: 'too thin to class',
  missing: 'no data',
};

// The de novo triage's priorities (nipt_triage), with the chip each shows.
export const DE_NOVO_LABELS: Record<string, { label: string; chip: string }> = {
  high: { label: 'high priority', chip: 'table-chip table-chip--critical' },
  medium: { label: 'medium priority', chip: 'table-chip table-chip--warning' },
  low: { label: 'low priority', chip: 'table-chip table-chip--neutral' },
  excluded_recurrent: { label: 'recurrent in other samples', chip: 'table-chip table-chip--neutral' },
  outside_window: { label: 'outside the fetal window', chip: 'table-chip table-chip--neutral' },
};

/** The plasma reads behind a call: "k of n", the depth estimated from the coverage when
 * the plasma has no call there. */
export const readsText = (nipt: {
  cf_alt_reads?: number | null;
  cf_depth?: number | null;
  cf_depth_estimated?: boolean;
}): string => {
  if (nipt.cf_depth == null) return nipt.cf_depth_estimated ? 'no call, outside the targets' : '—';
  if (nipt.cf_depth_estimated) return `no call at ~${nipt.cf_depth}× (target coverage)`;
  return `${nipt.cf_alt_reads ?? 0} of ${nipt.cf_depth}`;
};

export const depth = (value?: number | null): string =>
  value == null ? '—' : `${value.toFixed(0)}x`;

/** A capture target's exon: the last field of its attribute (gene;transcript;…;exon). */
export const targetExon = (attribute: string): string => {
  const parts = attribute.split(';');
  return parts.length > 4 ? parts[parts.length - 1].trim() : '';
};

// Short, hover-able reason for why a panel gene failed the coverage QC check.
export const lowCoverageDetail = (region: ApiNiptCoverageLowRegion): string => {
  if (region.reason === 'no_coverage') return 'no coverage';
  if (region.reason === 'partial_coverage')
    return `${Math.round(region.covered_fraction * 100)}% of target covered`;
  return `${depth(region.median_coverage)} median`;
};

export const lowCoverageChipClass = (reason: NiptLowCoverageReason): string =>
  `table-chip ${reason === 'no_coverage' ? 'table-chip--critical' : 'table-chip--warning'}`;

/** A one-line summary of a variant's fetal-inheritance evidence for the table view. */
export const niptEvidenceSummary = (nipt: {
  paternal_transmission_probability?: number | null;
  maternal_allele_probability?: number | null;
  fetal_hom_alt_probability?: number | null;
  de_novo?: { label: string; score: number } | null;
}): string => {
  const parts: string[] = [];
  if (nipt.de_novo) {
    parts.push(`de novo: ${DE_NOVO_LABELS[nipt.de_novo.label]?.label ?? nipt.de_novo.label} (${nipt.de_novo.score})`);
  }
  if (nipt.paternal_transmission_probability != null) {
    parts.push(`paternal inherited ${pct(nipt.paternal_transmission_probability)}`);
  }
  if (nipt.maternal_allele_probability != null) {
    parts.push(`maternal inherited ${pct(nipt.maternal_allele_probability)}`);
  }
  if (nipt.fetal_hom_alt_probability != null) {
    parts.push(`fetus hom ${pct(nipt.fetal_hom_alt_probability)}`);
  }
  return parts.join(' · ');
};

/** How a variant's allele recurs in the other cfDNA samples of the assay (the NIPT list's
 * `recurrence`, and the signed record's, which does not hold how many other samples there were:
 * `other_samples` null). */
export interface NiptRecurrenceCounts {
  other_samples: number | null;
  carriers: number;
  maternal: number;
  paternal_low_vaf: number;
  unexplained_low_vaf: number;
  unexplained_median_vaf?: number | null;
  protection?: string | null;
}

const RECURRENCE_PROTECTION_LABELS: Record<string, string> = {
  clinvar: 'ClinVar may call it pathogenic',
  common: 'common in the population',
};

/** Why the recurrence filter keeps a variant whatever its count, or null. */
export const recurrenceProtectionText = (protection?: string | null): string | null =>
  protection ? `kept by the recurrence filter: ${RECURRENCE_PROTECTION_LABELS[protection] ?? protection}` : null;

const samplesText = (count: number): string => `${count} other cfDNA sample${count === 1 ? '' : 's'}`;

/**
 * The recurrence in words: "unexplained low-VAF in 4 of 31 other cfDNA samples (median 1.2%) ·
 * father's allele in 1 · maternal in 2", or that no other sample carries it. Without the number of
 * other samples (a signed record): "unexplained low-VAF in 4 other cfDNA samples …".
 */
export const recurrenceText = (recurrence: NiptRecurrenceCounts): string => {
  const others = recurrence.other_samples;
  if (others === 0) return 'no other cfDNA sample of the assay to compare with';
  if (recurrence.carriers === 0) {
    return others === null ? 'in no other cfDNA sample of the assay' : `in none of ${samplesText(others)}`;
  }
  const unexplained =
    others === null
      ? `unexplained low-VAF in ${samplesText(recurrence.unexplained_low_vaf)}`
      : `unexplained low-VAF in ${recurrence.unexplained_low_vaf} of ${samplesText(others)}`;
  const parts = [
    `${unexplained}${
      recurrence.unexplained_median_vaf != null ? ` (median ${pct(recurrence.unexplained_median_vaf)})` : ''
    }`,
  ];
  if (recurrence.paternal_low_vaf) parts.push(`father's allele in ${recurrence.paternal_low_vaf}`);
  if (recurrence.maternal) parts.push(`maternal in ${recurrence.maternal}`);
  const rest = recurrence.carriers - recurrence.unexplained_low_vaf - recurrence.paternal_low_vaf - recurrence.maternal;
  if (rest > 0) parts.push(`without allele depths in ${rest}`);
  const protection = recurrenceProtectionText(recurrence.protection);
  if (protection) parts.push(protection);
  return parts.join(' · ');
};

/** The table's short form: "recurrent 4/31" when unexplained calls recur, else null. */
export const recurrenceShortText = (recurrence: NiptRecurrenceCounts): string | null =>
  recurrence.unexplained_low_vaf > 0
    ? `recurrent ${recurrence.unexplained_low_vaf}/${recurrence.other_samples ?? '?'}${
        recurrence.protection ? ' (kept)' : ''
      }`
    : null;
