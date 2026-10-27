/** The nuclear chromosomes in karyotype order, 1–22, X and Y, as the genome-wide views draw them. */
export const NUCLEAR_CHROMOSOMES: string[] = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  'X',
  'Y',
];

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

/**
 * A cytoband as the ideograms name it: in ISCN form, the chromosome without its `chr` and the
 * band (`1p36.33`, `Xq28`). The mitochondrion has no cytobands, and a band without a name of
 * its own is its chromosome, as {@link formatChromosomeLabel} names it: GRCh38's chrM band has
 * no name and T2T's is named `chrM`, and both are `chrM`, never the route's key joined to the
 * stored name (`MT`, `MTchrM`).
 */
export const formatCytobandLabel = (chrom: string, band: string): string => {
  const chromosome = normalizeChrom(chrom);
  const name = band.trim();
  if (chromosome === 'MT' || !name || normalizeChrom(name) === chromosome) {
    return formatChromosomeLabel(chrom);
  }
  return `${chromosome}${name}`;
};
