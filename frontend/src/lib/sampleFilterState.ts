const DIPLOID_GENOTYPE_PATTERN = /absent|\.\/\.|[0-9.][/|][0-9.]/g;

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

  const phasedMatches = rawValue.match(DIPLOID_GENOTYPE_PATTERN);
  if (phasedMatches?.length) {
    return phasedMatches;
  }

  return rawValue.split('|').filter(Boolean);
};

export const parseExplicitSampleFilterMap = (
  params: URLSearchParams,
): Record<string, string> => {
  const map: Record<string, string> = {};
  params.getAll('sample_filter').forEach((entry) => {
    const [sample] = entry.split(':');
    if (sample) {
      map[sample] = entry;
    }
  });
  return map;
};
