import { describe, expect, it } from 'vitest';

import {
  describeGenotypeSelection,
  hasNonDefaultGenotypeSelection,
  joinFilterValues,
  parseCommaSeparatedValues,
  parseExplicitSampleFilterMap,
  parseSerializedGenotypeSelection,
} from '../sampleFilterState';

describe('sampleFilterState', () => {
  const universe = ['1/1', '1|1', '0/1', '1/0', '0|1', '1|0', '0/0', '0|0', './.', 'absent'];

  it('treats the full genotype universe as neutral', () => {
    expect(hasNonDefaultGenotypeSelection(universe, universe)).toBe(false);
  });

  it('treats an explicit empty genotype selection as active', () => {
    expect(hasNonDefaultGenotypeSelection([], universe)).toBe(true);
  });

  it('preserves a blank serialized genotype field instead of falling back', () => {
    expect(parseSerializedGenotypeSelection('S1:::::', universe)).toEqual([]);
  });

  it('falls back only when no genotype field is serialized at all', () => {
    expect(parseSerializedGenotypeSelection('S1', universe)).toEqual(universe);
  });

  it('preserves phased diploid genotypes when parsing serialized small-variant filters', () => {
    expect(parseSerializedGenotypeSelection('S1:0/1|1/0|0|1|1|0::::', universe)).toEqual([
      '0/1',
      '1/0',
      '0|1',
      '1|0',
    ]);
  });

  it('expands genotype group aliases from genome links', () => {
    expect(parseSerializedGenotypeSelection('S1:het', universe)).toEqual([
      '0/1',
      '1/0',
      '0|1',
      '1|0',
    ]);
    expect(parseSerializedGenotypeSelection('S1:hom', universe)).toEqual(['1/1', '1|1']);
    expect(parseSerializedGenotypeSelection('S1:wt', universe)).toEqual(['0/0', '0|0']);
  });

  it('parses only explicit sample filter entries from the URL', () => {
    const params = new URLSearchParams(
      'sample_filter=KID1:0/1|1/1::::&sample_filter=MOM:0/0::::&sample=KID1',
    );

    expect(parseExplicitSampleFilterMap(params)).toEqual({
      KID1: 'KID1:0/1|1/1::::',
      MOM: 'MOM:0/0::::',
    });
  });

  it('returns an empty map when no sample filter is set', () => {
    expect(parseExplicitSampleFilterMap(new URLSearchParams('sample=KID1'))).toEqual({});
  });
});

// One implementation for every variant search (#528); the copies had diverged.
describe('comma-separated filter values', () => {
  it('parses a list, trimming entries and dropping empty ones', () => {
    expect(parseCommaSeparatedValues(' HIGH, ,MODERATE ,')).toEqual(['HIGH', 'MODERATE']);
    expect(parseCommaSeparatedValues('')).toEqual([]);
  });

  it('joins values in first-seen order, deduplicated after trimming', () => {
    expect(joinFilterValues(['b', ' a', 'a', 'b ', ''])).toBe('b, a');
    expect(joinFilterValues(new Set(['pathogenic', 'likely_pathogenic']))).toBe(
      'pathogenic, likely_pathogenic',
    );
  });
});

describe('describeGenotypeSelection', () => {
  const groups = { hom: ['1/1'], het: ['0/1', '1/0'], ref: ['0/0'] };

  it('names every group the selection fully covers', () => {
    expect(describeGenotypeSelection(['1/1', '0/1', '1/0'], groups)).toBe('Hom / Het');
    expect(describeGenotypeSelection(['0/0'], groups)).toBe('WT');
  });

  it('says so when no group is fully covered', () => {
    expect(describeGenotypeSelection(['0/1'], groups)).toBe('No genotype');
  });
});
