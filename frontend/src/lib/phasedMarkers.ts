// Helpers shared by the phased-marker views: the haplotype tracks and the ROI marker table.

/** A haplotype lane value of '.': the block is deleted on that lane. */
export const isDeletedHaplotype = (value: string): boolean => value === '.';

/** IGV-style nucleotide colours for allele letters. */
export const NUCLEOTIDE_COLORS: Record<string, string> = {
  A: '#2e9e4f',
  C: '#2f6fe0',
  G: '#e8a33d',
  T: '#d6453d',
};

/**
 * The colour of an allele that is not a single A, C, G or T: an N, a missing '·' or unknown
 * '?' allele, a multi-base (indel) allele. It is the theme's grey for an unknown (the value of
 * --color-haplotype-unknown), kept a literal like the four colours above. It is darker than the
 * ROI marker table's uninformative allele (#cbd5e1), so an informative allele that is not a
 * single base does not read as uninformative there.
 */
export const NUCLEOTIDE_FALLBACK_COLOR = '#9ca3af';

/** The colour an allele's letters are drawn in: its IGV colour, else the fallback grey. */
export const nucleotideColor = (base: string): string =>
  (base.length === 1 ? NUCLEOTIDE_COLORS[base.toUpperCase()] : undefined) ??
  NUCLEOTIDE_FALLBACK_COLOR;

/** The allele a phased index refers to: 0 = ref, n = nth alt; '.' = missing ('·'). */
export const alleleBase = (index: string, ref: string, alt: string): string => {
  if (index === '0') return ref;
  const n = parseInt(index, 10);
  if (Number.isNaN(n)) return '·';
  return alt.split(',')[n - 1] ?? '?';
};
