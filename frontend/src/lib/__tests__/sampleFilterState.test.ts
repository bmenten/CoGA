import { describe, expect, it } from 'vitest';

import {
  hasNonDefaultGenotypeSelection,
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
