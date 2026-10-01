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

export const pct = (value?: number | null): string =>
  value == null ? '—' : `${(value * 100).toFixed(1)}%`;

export const depth = (value?: number | null): string =>
  value == null ? '—' : `${value.toFixed(0)}x`;

// Short, hover-able reason for why a panel gene failed the coverage QC check.
export const lowCoverageDetail = (region: ApiNiptCoverageLowRegion): string => {
  if (region.reason === 'no_coverage') return 'no coverage';
  if (region.reason === 'partial_coverage')
    return `${Math.round(region.covered_fraction * 100)}% of target covered`;
  return `${depth(region.median_coverage)} median`;
};

export const lowCoverageChipClass = (reason: NiptLowCoverageReason): string =>
  `table-chip ${reason === 'no_coverage' ? 'table-chip--critical' : 'table-chip--warning'}`;
