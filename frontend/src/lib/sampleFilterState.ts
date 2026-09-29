const DIPLOID_GENOTYPE_PATTERN = /absent|hom_alt|hom_ref|hom|het|ref|wt|\.\/\.|[0-9.][/|][0-9.]/g;
const GENOTYPE_ALIASES: Record<string, string[]> = {
  het: ['0/1', '1/0', '0|1', '1|0'],
  hom: ['1/1', '1|1'],
  hom_alt: ['1/1', '1|1'],
  ref: ['0/0', '0|0'],
  wt: ['0/0', '0|0'],
  hom_ref: ['0/0', '0|0'],
};

export const hasNonDefaultGenotypeSelection = (
  selected: string[],
  universe: string[],
) => {
  const uniqueSelected = Array.from(new Set(selected));
  return (
    uniqueSelected.length !== universe.length ||
    universe.some((genotype) => !uniqueSelected.includes(genotype))
  );
};

export const parseSerializedGenotypeSelection = (
  entry: string,
  fallback: string[],
) => {
  const parts = entry.split(':');
  if (parts.length <= 1) return fallback;
  const rawValue = parts[1];
  if (!rawValue) return [];

  const phasedMatches = rawValue.toLowerCase().match(DIPLOID_GENOTYPE_PATTERN);
  if (phasedMatches?.length) {
    return phasedMatches.flatMap((value) => GENOTYPE_ALIASES[value] || [value]);
  }

  return rawValue.split('|').filter(Boolean);
};

export const parseExplicitSampleFilterMap = (
  params: URLSearchParams,
): Record<string, string> => {
  const map: Record<string, string> = Object.create(null);
  params.getAll('sample_filter').forEach((entry) => {
    const [sample] = entry.split(':');
    if (sample) {
      map[sample] = entry;
    }
  });
  return map;
};

// Comma-separated filter values, shared by the small-variant, structural-variant and
// cohort searches (#528). The copies had diverged: one sorted its output, and one removed
// duplicates before trimming, so "a" and " a" both survived as "a, a".
export const parseCommaSeparatedValues = (value: string) =>
  value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

/** Trim, drop empties, drop duplicates after trimming, keep the first-seen order. */
export const joinFilterValues = (values: Iterable<string>) =>
  Array.from(new Set(Array.from(values).map((value) => value.trim()).filter(Boolean))).join(', ');

export interface GenotypeGroups {
  hom: readonly string[];
  het: readonly string[];
  ref: readonly string[];
}

/** Name the genotype groups a selection fully covers ("Hom / Het"), or "No genotype". */
export const describeGenotypeSelection = (selection: string[], groups: GenotypeGroups) => {
  const labels: string[] = [];
  if (groups.hom.every((gt) => selection.includes(gt))) labels.push('Hom');
  if (groups.het.every((gt) => selection.includes(gt))) labels.push('Het');
  if (groups.ref.every((gt) => selection.includes(gt))) labels.push('WT');
  return labels.length ? labels.join(' / ') : 'No genotype';
};
