// The genome-wide layout the binned charts (coverage/segments and APCAD) lay their
// chromosomes out on, shared so the two charts line up.

export interface GenomeLayout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
}

/** A newline-joined chromosome key back into its chromosomes. */
export const splitKey = (key: string): string[] => (key ? key.split('\n').filter(Boolean) : []);

/**
 * Each chromosome's length (the end of its last bin) and its offset along the chart. A single
 * chromosome with a region spans just that region.
 */
export const deriveLayoutFromBins = (
  bins: ReadonlyArray<{ chr: string; end: number }>,
  chroms: string[],
  regionStart?: number,
  regionEnd?: number,
): GenomeLayout => {
  const lengths: Record<string, number> = Object.create(null);
  chroms.forEach((chrom) => {
    lengths[chrom] = 0;
  });

  bins.forEach((bin) => {
    lengths[bin.chr] = Math.max(lengths[bin.chr] ?? 0, bin.end);
  });

  const offsets: Record<string, number> = Object.create(null);
  let total = 0;
  chroms.forEach((chrom) => {
    offsets[chrom] = total;
    total += lengths[chrom] ?? 0;
  });

  if (
    regionStart !== undefined &&
    regionEnd !== undefined &&
    chroms.length === 1
  ) {
    total = regionEnd - regionStart;
  }

  return { offsets, lengths, total };
};
