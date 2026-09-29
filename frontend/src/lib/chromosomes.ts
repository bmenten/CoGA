export function compareChromosomes(a: string, b: string): number {
  const rank = (chr: string): number => {
    const cleaned = chr.replace(/^chr/i, '').toUpperCase();
    const num = parseInt(cleaned, 10);
    if (!Number.isNaN(num)) return num;
    if (cleaned === 'X') return 23;
    if (cleaned === 'Y') return 24;
    if (cleaned === 'MT' || cleaned === 'M') return 25;
    return Number.MAX_SAFE_INTEGER;
  };
  const diff = rank(a) - rank(b);
  return diff !== 0 ? diff : a.localeCompare(b);
}

/** A chromosome name without its `chr` prefix: `chr01` → `1`, `chrM` and `m` → `MT`. */
export const normalizeChrom = (value: string): string => {
  const stripped = value.trim().replace(/^chr/i, '');
  if (/^m(t)?$/i.test(stripped)) return 'MT';
  if (/^\d+$/.test(stripped)) return String(Number(stripped));
  return stripped.toUpperCase();
};

/**
 * A chromosome as the viewer names it: `chr1`, `chrX`, and `chrM` for the mitochondrion,
 * however the data spells it. The tracks, the chromosome lists and the viewer header all
 * use it, so one chromosome has one name on screen (#602).
 */
export const formatChromosomeLabel = (value: string): string => {
  const chrom = normalizeChrom(value);
  return chrom === 'MT' ? 'chrM' : `chr${chrom}`;
};
