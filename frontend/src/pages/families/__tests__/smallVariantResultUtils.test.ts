import { describe, expect, it } from 'vitest';

import { buildDbSnpHref, formatHgvsG } from '../smallVariantResultUtils';

const variant = (chr: string, start: number, ref: string, alt: string) =>
  ({ chr, start, ref, alt }) as Parameters<typeof formatHgvsG>[0];

describe('formatHgvsG', () => {
  it('writes a substitution at the variant position', () => {
    expect(formatHgvsG(variant('13', 32316461, 'G', 'A'))).toBe('chr13:g.32316461G>A');
  });

  it('keeps a chr-prefixed chromosome as given', () => {
    expect(formatHgvsG(variant('chrX', 100, 'C', 'T'))).toBe('chrX:g.100C>T');
  });

  it('drops the VCF padding base from a deletion and spans the deleted bases', () => {
    // ACT>A deletes CT at 101-102, not ACT at 100.
    expect(formatHgvsG(variant('1', 100, 'ACT', 'A'))).toBe('chr1:g.101_102del');
    expect(formatHgvsG(variant('1', 100, 'AC', 'A'))).toBe('chr1:g.101del');
  });

  it('places an insertion between the flanking bases', () => {
    expect(formatHgvsG(variant('1', 100, 'A', 'ACT'))).toBe('chr1:g.100_101insCT');
  });

  it('writes a multi-base substitution as delins', () => {
    expect(formatHgvsG(variant('1', 100, 'AC', 'TG'))).toBe('chr1:g.100_101delinsTG');
    expect(formatHgvsG(variant('1', 100, 'ACT', 'AG'))).toBe('chr1:g.101_102delinsG');
  });

  it('trims a shared suffix before deciding the span', () => {
    // AAT>AT is a deletion of one A, not a two-base delins.
    expect(formatHgvsG(variant('1', 100, 'AAT', 'AT'))).toBe('chr1:g.101del');
  });

  it('returns null for symbolic or absent alleles', () => {
    expect(formatHgvsG(variant('1', 100, '<DEL>', 'A'))).toBeNull();
    expect(formatHgvsG(variant('1', 100, '', 'A'))).toBeNull();
    expect(formatHgvsG(variant('1', 100, 'A', 'A'))).toBeNull();
  });
});

describe('buildDbSnpHref', () => {
  it('links a well-formed rsID to dbSNP', () => {
    expect(buildDbSnpHref('rs80359550')).toBe('https://www.ncbi.nlm.nih.gov/snp/rs80359550');
  });

  it('declines anything that is not an rsID', () => {
    expect(buildDbSnpHref('')).toBeNull();
    expect(buildDbSnpHref(null)).toBeNull();
    expect(buildDbSnpHref('.')).toBeNull();
    expect(buildDbSnpHref('COSM123')).toBeNull();
  });
});
