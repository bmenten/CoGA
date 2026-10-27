import { describe, expect, it } from 'vitest';
import {
  NUCLEAR_CHROMOSOMES,
  compareChromosomes,
  formatChromosomeLabel,
  formatCytobandLabel,
  normalizeChrom,
} from '../chromosomes';

describe('NUCLEAR_CHROMOSOMES', () => {
  it('lists 1–22, X and Y in karyotype order, without the mitochondrion', () => {
    expect(NUCLEAR_CHROMOSOMES).toHaveLength(24);
    expect(NUCLEAR_CHROMOSOMES.slice(0, 3)).toEqual(['1', '2', '3']);
    expect(NUCLEAR_CHROMOSOMES.slice(-3)).toEqual(['22', 'X', 'Y']);
    expect(NUCLEAR_CHROMOSOMES).not.toContain('MT');
  });
});

describe('compareChromosomes', () => {
  it('sorts chromosomes in natural order', () => {
    const input = ['chr2', 'chr1', 'chr10', 'chrX', 'chrY', 'MT'];
    const sorted = [...input].sort(compareChromosomes);
    expect(sorted).toEqual(['chr1', 'chr2', 'chr10', 'chrX', 'chrY', 'MT']);
  });

  it('handles values without chr prefix', () => {
    const input = ['2', '1', '10'];
    const sorted = [...input].sort(compareChromosomes);
    expect(sorted).toEqual(['1', '2', '10']);
  });
});

describe('normalizeChrom', () => {
  it.each([
    ['chr1', '1'],
    ['CHR01', '1'],
    [' 7 ', '7'],
    ['chrx', 'X'],
    ['chrM', 'MT'],
    ['chrMT', 'MT'],
    ['m', 'MT'],
  ])('%j → %j', (input, expected) => {
    expect(normalizeChrom(input)).toBe(expected);
  });
});

// #602 — one name per chromosome on screen: the mitochondrion is chrM however the data
// spells it, as the viewer header already wrote it.
describe('formatChromosomeLabel', () => {
  it.each([
    ['1', 'chr1'],
    ['chr1', 'chr1'],
    ['X', 'chrX'],
    ['MT', 'chrM'],
    ['chrMT', 'chrM'],
    ['chrM', 'chrM'],
  ])('%j → %j', (input, expected) => {
    expect(formatChromosomeLabel(input)).toBe(expected);
  });
});

// Found while fixing ENG-7 (#1019): the ideograms joined the route's key to the band's stored
// name, so the mitochondrion's band read "MT" (GRCh38, no name) or "MTchrM" (T2T, named chrM).
describe('formatCytobandLabel', () => {
  it.each([
    ['1', 'p36.33', '1p36.33'],
    ['chr1', 'q21.1', '1q21.1'],
    ['X', 'q28', 'Xq28'],
    // The mitochondrion has no cytobands: its band is the chromosome, however either is spelled.
    ['MT', '', 'chrM'],
    ['MT', 'chrM', 'chrM'],
    ['chrM', 'M', 'chrM'],
    // A band without a name of its own is its chromosome, as the header names it.
    ['1', '', 'chr1'],
    ['1', 'chr1', 'chr1'],
  ])('%j, band %j → %j', (chrom, band, expected) => {
    expect(formatCytobandLabel(chrom, band)).toBe(expected);
  });
});
