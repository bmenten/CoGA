import { describe, expect, it } from 'vitest';
import { compareChromosomes, formatChromosomeLabel, normalizeChrom } from '../chromosomes';

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
