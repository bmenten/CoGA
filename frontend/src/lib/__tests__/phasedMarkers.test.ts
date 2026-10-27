import { describe, expect, it } from 'vitest';

import {
  NO_ALLELE,
  NUCLEOTIDE_COLORS,
  NUCLEOTIDE_FALLBACK_COLOR,
  alleleBase,
  inRanges,
  isDeletedHaplotype,
  nucleotideColor,
  rowAlleles,
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

// The rule the ROI marker review and the haplotype track's tooltip both draw a call by (ENG-19).
describe('rowAlleles', () => {
  it('puts a phased call one allele on each homolog', () => {
    expect(rowAlleles('0|1', false, 'mother')).toEqual({ hap1: '0', hap2: '1' });
    expect(rowAlleles('1|1', false, 'embryo')).toEqual({ hap1: '1', hap2: '1' });
  });

  it('puts a call of one allele on the father’s first homolog and on anyone else’s second', () => {
    expect(rowAlleles('0', false, 'father')).toEqual({ hap1: '0', hap2: null });
    expect(rowAlleles('1', true, 'Father')).toEqual({ hap1: '1', hap2: null });
    expect(rowAlleles('1', false, 'embryo')).toEqual({ hap1: null, hap2: '1' });
    expect(rowAlleles('1', false, undefined)).toEqual({ hap1: null, hap2: '1' });
    expect(NO_ALLELE).toBe('–');
  });

  it('reads a man’s same allele twice as his one allele where he has one X, as written elsewhere', () => {
    expect(rowAlleles('1|1', true, 'embryo')).toEqual({ hap1: null, hap2: '1' });
    expect(rowAlleles('0/0', true, 'father')).toEqual({ hap1: '0', hap2: null });
    // Two different alleles are not one copy: drawn as called.
    expect(rowAlleles('0|1', true, 'embryo')).toEqual({ hap1: '0', hap2: '1' });
  });

  it('gives a missing or unphased call no allele on either homolog, as a missing one', () => {
    for (const gt of ['', '.', './.', '0/1']) {
      expect(rowAlleles(gt, false, 'mother')).toEqual({ hap1: '', hap2: '' });
    }
  });
});

describe('inRanges', () => {
  it('holds within a [first, last] range, both ends included', () => {
    const ranges = [
      [10, 20],
      [40, 50],
    ];
    expect([9, 10, 20, 21, 45, 51].map((pos) => inRanges(ranges, pos))).toEqual([false, true, true, false, true, false]);
    expect(inRanges([], 10)).toBe(false);
  });
});
