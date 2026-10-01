import { describe, expect, it } from 'vitest';

import {
  NUCLEOTIDE_COLORS,
  NUCLEOTIDE_FALLBACK_COLOR,
  alleleBase,
  isDeletedHaplotype,
  nucleotideColor,
} from '../phasedMarkers';

describe('alleleBase', () => {
  it('maps a phased index to its allele: 0 = ref, n = nth alt', () => {
    expect(alleleBase('0', 'A', 'G')).toBe('A');
    expect(alleleBase('1', 'A', 'G')).toBe('G');
    expect(alleleBase('2', 'A', 'G,T')).toBe('T');
  });

  it('marks a missing index and an index past the alts', () => {
    expect(alleleBase('.', 'A', 'G')).toBe('·');
    expect(alleleBase('3', 'A', 'G')).toBe('?');
  });
});

describe('isDeletedHaplotype', () => {
  it('is true only for the deleted-lane value', () => {
    expect(isDeletedHaplotype('.')).toBe(true);
    expect(isDeletedHaplotype('0')).toBe(false);
    expect(isDeletedHaplotype('')).toBe(false);
  });
});

describe('NUCLEOTIDE_COLORS', () => {
  it('colours the four bases', () => {
    expect(Object.keys(NUCLEOTIDE_COLORS).sort()).toEqual(['A', 'C', 'G', 'T']);
  });
});

describe('nucleotideColor', () => {
  it('draws a single base in its IGV colour, in either case', () => {
    expect(nucleotideColor('A')).toBe(NUCLEOTIDE_COLORS.A);
    expect(nucleotideColor('g')).toBe(NUCLEOTIDE_COLORS.G);
  });

  it('draws every other allele in the one fallback grey', () => {
    // The haplotype tooltip drew these #9ca3af, or #cbd5e1 for a multi-base allele; the ROI
    // marker table drew all of them #cbd5e1, the colour of its uninformative alleles.
    expect(NUCLEOTIDE_FALLBACK_COLOR).toBe('#9ca3af');
    for (const allele of ['N', '·', '?', '*', 'GT', 'ACGT', '']) {
      expect(nucleotideColor(allele)).toBe(NUCLEOTIDE_FALLBACK_COLOR);
    }
  });

  it('keeps the fallback apart from the four base colours', () => {
    expect(Object.values(NUCLEOTIDE_COLORS)).not.toContain(NUCLEOTIDE_FALLBACK_COLOR);
  });
});
