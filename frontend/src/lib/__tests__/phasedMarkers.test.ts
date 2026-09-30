import { describe, expect, it } from 'vitest';

import { NUCLEOTIDE_COLORS, alleleBase, isDeletedHaplotype } from '../phasedMarkers';

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
