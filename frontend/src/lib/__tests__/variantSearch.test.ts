// The locus box shared by the small- and structural-variant searches (#526): a region is
// chromosome:start[-end], with or without "chr" and thousands separators. Input with a
// colon, or a BED-like line, that does not parse is neither (#604); anything else is a gene.
import { describe, expect, it } from 'vitest';

import { intervalListProblems, parseGeneOrRegionInput } from '../variantSearch';

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

  it.each(['BRCA2', 'brca2', 'HLA-DRB1', 'BRCA1 BRCA2'])('reads %s as a gene, trimmed', (input) => {
    expect(parseGeneOrRegionInput(`  ${input} `)).toEqual({ kind: 'gene', gene: input });
  });

  // #604 — the dash of a range may be an en dash, as the viewer writes one.
  it.each(['chr13:32,315,508–32,400,268', 'chr13:32315508 - 32400268'])('reads %s as a region', (input) => {
    expect(parseGeneOrRegionInput(input)).toEqual({
      kind: 'region',
      chr: '13',
      start: '32315508',
      end: '32400268',
    });
  });

  // #604 — these were searched as gene names, matched nothing and read as no variants.
  it.each([
    ['chr1:abc', "Location 'chr1:abc' is not a gene or chr:start-end."],
    ['chr1:100-', "Location 'chr1:100-' is not a gene or chr:start-end."],
    ['1:-200', "Location '1:-200' is not a gene or chr:start-end."],
    ['chr1 100 200', "Location 'chr1 100 200' is not a gene or chr:start-end."],
    ['chr17\t43044295\t43125482', "Location 'chr17\t43044295\t43125482' is not a gene or chr:start-end."],
    ['chr1:200-100', "Location 'chr1:200-100' ends before it starts."],
  ])('reads %j as neither a gene nor a region', (input, problem) => {
    expect(parseGeneOrRegionInput(input)).toEqual({ kind: 'invalid', problem });
  });

  it('reads nothing as nothing', () => {
    expect(parseGeneOrRegionInput('')).toBeNull();
    expect(parseGeneOrRegionInput('   ')).toBeNull();
  });
});

// #604 — the interval lists, read as the backend reads them, so the form can name an
// entry the search would refuse before running it.
describe('intervalListProblems', () => {
  it('accepts ranges with a hyphen or an en dash, single positions, and blank entries', () => {
    expect(
      intervalListProblems('chr13:32315086-32400266\r\n\n13:1,000–2,000 ; chr7:117559590\n  \n'),
    ).toEqual([]);
    expect(intervalListProblems('')).toEqual([]);
  });

  it('names each entry that cannot be read, and why', () => {
    expect(
      intervalListProblems('chr13:32315086-32400266\nchr17\t43044295\t43125482\nchr1:100-\nchr1:200-100'),
    ).toEqual([
      "Interval 'chr17\t43044295\t43125482' is not chr:start-end.",
      "Interval 'chr1:100-' is not chr:start-end.",
      "Interval 'chr1:200-100' ends before it starts.",
    ]);
  });

  it('names an excluded interval as one', () => {
    expect(intervalListProblems('BRCA1', 'Excluded interval')).toEqual([
      "Excluded interval 'BRCA1' is not chr:start-end.",
    ]);
  });
});
