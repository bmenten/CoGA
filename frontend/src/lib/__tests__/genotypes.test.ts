import { describe, expect, it } from 'vitest';

import { classifyGenotype, formatGt, genotypeZygosity, hasAltAllele } from '../genotypes';

describe('formatGt', () => {
  it('labels homozygous-alt genotypes', () => {
    expect(formatGt('1/1')).toBe('Hom');
    expect(formatGt('1|1')).toBe('Hom');
  });

  it('labels heterozygous genotypes regardless of allele order or phasing', () => {
    expect(formatGt('0/1')).toBe('Het');
    expect(formatGt('1/0')).toBe('Het');
    expect(formatGt('0|1')).toBe('Het');
    expect(formatGt('1|0')).toBe('Het');
  });

  it('labels reference genotypes as WT', () => {
    expect(formatGt('0/0')).toBe('WT');
    expect(formatGt('0|0')).toBe('WT');
  });

  it('labels missing/no-call genotypes distinctly, not as WT', () => {
    expect(formatGt('./.')).toBe('No call');
    expect(formatGt('.|.')).toBe('No call');
    expect(formatGt('.')).toBe('No call');
    expect(formatGt('')).toBe('No call');
    expect(formatGt(undefined)).toBe('No call');
  });
});

describe('hasAltAllele', () => {
  it('is true only for Het and Hom', () => {
    expect(hasAltAllele('1/1')).toBe(true);
    expect(hasAltAllele('0/1')).toBe(true);
    expect(hasAltAllele('1|0')).toBe(true);
  });

  it('excludes reference and no-call (matching backend carrier semantics)', () => {
    expect(hasAltAllele('0/0')).toBe(false);
    expect(hasAltAllele('./.')).toBe(false);
    expect(hasAltAllele('.')).toBe(false);
    expect(hasAltAllele('')).toBe(false);
    expect(hasAltAllele(undefined)).toBe(false);
  });
});

// #511: one classification, the same as the backend's services/genotypes.py.
describe('classifyGenotype', () => {
  it.each([
    ['1', 'hom_alt'],
    ['0', 'hom_ref'],
    ['2/2', 'hom_alt'],
    ['12|12', 'hom_alt'],
    ['1/2', 'het'],
    ['0/2', 'het'],
    ['./1', 'het'],
    ['./0', 'no_call'],
    ['./.', 'no_call'],
    ['HET', 'no_call'],
  ])('classifies %s as %s', (gt, expected) => {
    expect(classifyGenotype(gt)).toBe(expected);
  });
});

describe('haploid and multi-allelic calls', () => {
  it('labels a haploid alt call as Hom and a haploid reference call as WT', () => {
    // chrM, and chrX/chrY in males from callers that emit ploidy-1 calls.
    expect(formatGt('1')).toBe('Hom');
    expect(formatGt('0')).toBe('WT');
    expect(hasAltAllele('1')).toBe(true);
    expect(hasAltAllele('0')).toBe(false);
  });

  it('labels multi-allelic calls by their alleles', () => {
    expect(formatGt('1/2')).toBe('Het');
    expect(formatGt('2/2')).toBe('Hom');
    expect(hasAltAllele('0/2')).toBe(true);
  });

  it('gives the card zygosity from the same classes', () => {
    expect(genotypeZygosity('1')).toBe('hom');
    expect(genotypeZygosity('0')).toBe('ref');
    expect(genotypeZygosity('1/2')).toBe('het');
    expect(genotypeZygosity('./.')).toBe('na');
    expect(genotypeZygosity('absent')).toBe('na');
    expect(genotypeZygosity(undefined)).toBe('na');
  });
});
