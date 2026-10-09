import type { ApiNiptCoverageLowRegion, NiptLowCoverageReason } from '../../lib/apiTypes';

// Shared presentation helpers for the monogenic NIPT views (the workbench page
// and the clinical report). Kept separate from the filter-state presets in
// smallVariantSearch so both surfaces format categories / coverage identically.

/** The family metadata `analysis_type` that marks a monogenic NIPT family. */
export const MONOGENIC_NIPT_ANALYSIS_TYPE = 'monogenic_nipt';

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

// The father's genotype classes (nipt_analysis.FATHER_STATES), read from his allele depths.
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
