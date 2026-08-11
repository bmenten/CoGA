import { describe, expect, it } from 'vitest';

import { formatHgvsG, parseVariantIds } from '../smallVariantResultUtils';

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

describe('parseVariantIds', () => {
  it('links a lone rsID to dbSNP', () => {
    expect(parseVariantIds('rs80359550')).toEqual([
      { id: 'rs80359550', source: 'dbSNP', href: 'https://www.ncbi.nlm.nih.gov/snp/rs80359550' },
    ]);
  });

  it('splits a comma-separated ID column and routes each to its own database', () => {
    expect(parseVariantIds('rs201819463,COSV53715949')).toEqual([
      {
        id: 'rs201819463',
        source: 'dbSNP',
        href: 'https://www.ncbi.nlm.nih.gov/snp/rs201819463',
      },
      {
        id: 'COSV53715949',
        source: 'COSMIC',
        href: 'https://cancer.sanger.ac.uk/cosmic/search?q=COSV53715949',
      },
    ]);
  });

  it('recognises an HGMD accession alongside an rsID', () => {
    const [, hgmd] = parseVariantIds('rs753078725,CM175273');
    expect(hgmd).toEqual({
      id: 'CM175273',
      source: 'HGMD',
      href: 'https://www.hgmd.cf.ac.uk/ac/mut.php?acc=CM175273',
    });
  });

  it('keeps an unrecognised accession as unlinked text', () => {
    expect(parseVariantIds('rs1,WEIRD_ID_9')).toEqual([
      { id: 'rs1', source: 'dbSNP', href: 'https://www.ncbi.nlm.nih.gov/snp/rs1' },
      { id: 'WEIRD_ID_9', source: null, href: null },
    ]);
  });

  it('yields nothing for an empty or placeholder ID column', () => {
    expect(parseVariantIds('')).toEqual([]);
    expect(parseVariantIds(null)).toEqual([]);
    expect(parseVariantIds('.')).toEqual([]);
    expect(parseVariantIds(' , ')).toEqual([]);
  });

  it('drops repeats so the same accession is not linked twice', () => {
    expect(parseVariantIds('rs1;rs1 rs2').map((entry) => entry.id)).toEqual(['rs1', 'rs2']);
  });
});
