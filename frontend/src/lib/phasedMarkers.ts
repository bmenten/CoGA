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

/** The allele a phased index refers to: 0 = ref, n = nth alt; '.' = missing ('·'). */
export const alleleBase = (index: string, ref: string, alt: string): string => {
  if (index === '0') return ref;
  const n = parseInt(index, 10);
  if (Number.isNaN(n)) return '·';
  return alt.split(',')[n - 1] ?? '?';
};
