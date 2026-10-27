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

// On chrX outside the PARs a man has one X (CLIN-60), as the backend's QC counts read it:
// his call written as one allele ('1') or twice ('1|1') is that allele, read as a pair; a
// call of two different alleles is one he cannot have.
export const callAlleles = (gt: string): number[] | null => {
  const parts = gt.trim().split(/[|/]/);
  if (parts.length > 2 || parts.some((part) => !/^\d+$/.test(part))) return null;
  return parts.map((part) => parseInt(part, 10));
};
export const oneCopyAlleles = (gt: string): number[] | null => {
  const alleles = callAlleles(gt);
  return alleles && new Set(alleles).size === 1 ? [alleles[0], alleles[0]] : null;
};
export const twoDifferentAlleles = (gt: string): boolean => {
  const alleles = callAlleles(gt);
  return !!alleles && alleles.length === 2 && alleles[0] !== alleles[1];
};
/** Whether a position lies in one of the `[first, last]` ranges (both ends included), such as
 * the phased-marker response's `hemizygous_x_ranges`: where a man has one X. */
export const inRanges = (ranges: number[][], pos: number): boolean =>
  ranges.some(([first, last]) => first <= pos && pos <= last);

// What a member's two homologs show at a site: the allele index on each, '' when the call is
// missing or unphased, null for no allele. A phased call has one allele per homolog. A call of
// one allele, written once, or a man's same allele twice where he has one X (which the QC reads
// as his one allele), is shown once (ENG-19): on the homolog the haplotype tracks draw a man's
// one X on, the father's first and anyone else's second (a child's maternal one: a son's X is
// his mother's), with no allele on the other. The ROI marker review draws its two rows by it,
// and the haplotype track's tooltip writes a call by it.
export type RowAlleles = { hap1: string | null; hap2: string | null };
export const rowAlleles = (gt: string, oneCopyMan: boolean, role: string | null | undefined): RowAlleles => {
  const alleles = callAlleles(gt);
  const single = alleles?.length === 1 ? alleles[0] : oneCopyMan ? oneCopyAlleles(gt)?.[0] : undefined;
  if (single !== undefined) {
    return String(role ?? '').trim().toLowerCase() === 'father'
      ? { hap1: String(single), hap2: null }
      : { hap1: null, hap2: String(single) };
  }
  const [hap1, hap2] = gt.includes('|') ? gt.split('|') : ['', ''];
  return { hap1, hap2 };
};
/** Shown on the homolog without an allele: not '·', which is a missing allele. */
export const NO_ALLELE = '–';
