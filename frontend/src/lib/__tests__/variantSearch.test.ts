// The locus box shared by the small- and structural-variant searches (#526): a region is
// chromosome:start[-end], with or without "chr" and thousands separators; anything else is
// a gene.
import { describe, expect, it } from 'vitest';

import { parseGeneOrRegionInput } from '../variantSearch';

describe('parseGeneOrRegionInput', () => {
  it.each([
    ['chr13:32,315,508-32,400,268', { kind: 'region', chr: '13', start: '32315508', end: '32400268' }],
    ['13:32315508-32400268', { kind: 'region', chr: '13', start: '32315508', end: '32400268' }],
    ['CHRX:100-200', { kind: 'region', chr: 'X', start: '100', end: '200' }],
    ['chrMT:3,243', { kind: 'region', chr: 'MT', start: '3243', end: '3243' }],
    ['  chr1:100-200  ', { kind: 'region', chr: '1', start: '100', end: '200' }],
  ])('reads %s as a region', (input, expected) => {
    expect(parseGeneOrRegionInput(input)).toEqual(expected);
  });

  it('reads a single position as a one-base region', () => {
    expect(parseGeneOrRegionInput('chr7:117,559,590')).toEqual({
      kind: 'region',
      chr: '7',
      start: '117559590',
      end: '117559590',
    });
  });

  it.each(['BRCA2', 'brca2', 'HLA-DRB1', 'chr1:abc', 'chr1:100-', '1:-200', 'chr1 100 200'])(
    'reads %s as a gene, trimmed',
    (input) => {
      expect(parseGeneOrRegionInput(`  ${input} `)).toEqual({ kind: 'gene', gene: input });
    },
  );

  it('reads nothing as nothing', () => {
    expect(parseGeneOrRegionInput('')).toBeNull();
    expect(parseGeneOrRegionInput('   ')).toBeNull();
  });
});
