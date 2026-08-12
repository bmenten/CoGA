import { describe, expect, it } from 'vitest';

import {
  buildSvSecondHitHref,
  formatHgvsG,
  formatPredictionScore,
  parseVariantIds,
} from '../smallVariantResultUtils';

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

describe('formatPredictionScore', () => {
  it('spaces the score off the call', () => {
    expect(formatPredictionScore('deleterious(0.01)')).toBe('deleterious (0.01)');
    expect(formatPredictionScore('tolerated(1)')).toBe('tolerated (1)');
  });

  it('un-snakes a multi-word call', () => {
    expect(formatPredictionScore('deleterious_low_confidence(0)')).toBe(
      'deleterious low confidence (0)',
    );
    expect(formatPredictionScore('probably_damaging(0.999)')).toBe('probably damaging (0.999)');
  });

  it('leaves a bare call alone apart from the underscores', () => {
    expect(formatPredictionScore('probably_damaging')).toBe('probably damaging');
  });

  it('renders a missing or no-call value as a dash', () => {
    expect(formatPredictionScore('')).toBe('—');
    expect(formatPredictionScore(null)).toBe('—');
    expect(formatPredictionScore('-')).toBe('—');
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

  // VEP joins co-located ids with `&`, and the stored data uses it about as often as
  // the comma. Splitting only on commas left these whole and unlinked.
  it('splits an ampersand-joined pair', () => {
    expect(parseVariantIds('rs777972314&COSV106382136')).toEqual([
      {
        id: 'rs777972314',
        source: 'dbSNP',
        href: 'https://www.ncbi.nlm.nih.gov/snp/rs777972314',
      },
      {
        id: 'COSV106382136',
        source: 'COSMIC',
        href: 'https://cancer.sanger.ac.uk/cosmic/search?q=COSV106382136',
      },
    ]);
  });

  it('routes every HGMD class present in the data', () => {
    // CR/CM/CS/CD/CX/CP/CI/BM/HM/HD all occur in the stored ID column.
    for (const id of ['CR090490', 'CM950484', 'CS100321', 'CD0911544', 'CX1314995', 'CP015821', 'CI151835', 'BM1350102', 'HM972178', 'HD070030']) {
      const [entry] = parseVariantIds(`rs1&${id}`).slice(1);
      expect(entry.source, id).toBe('HGMD');
      expect(entry.href, id).toBe(`https://www.hgmd.cf.ac.uk/ac/mut.php?acc=${id}`);
    }
  });

  it('does not mistake a COSMIC accession for HGMD', () => {
    expect(parseVariantIds('COSV59694286')[0].source).toBe('COSMIC');
  });

  it('splits three ids joined by commas', () => {
    expect(parseVariantIds('rs477067,COSV61661500,COSV61661861').map((e) => e.id)).toEqual([
      'rs477067',
      'COSV61661500',
      'COSV61661861',
    ]);
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

describe('buildSvSecondHitHref', () => {
  const variant = (gene?: string, geneId?: string) =>
    ({ gene, gene_id: geneId }) as Parameters<typeof buildSvSecondHitHref>[0];

  it('points at the family SV list filtered to the gene', () => {
    expect(buildSvSecondHitHref(variant('TRNT1'), 'F1')).toBe(
      '/families/F1/structural-variants?gene=TRNT1',
    );
  });

  it('carries the project through', () => {
    expect(buildSvSecondHitHref(variant('TRNT1'), 'F1', 'P1')).toBe(
      '/families/F1/structural-variants?gene=TRNT1&project_id=P1',
    );
  });

  it('falls back to the gene id when there is no symbol', () => {
    expect(buildSvSecondHitHref(variant(undefined, 'ENSG00000072756'), 'F1')).toBe(
      '/families/F1/structural-variants?gene=ENSG00000072756',
    );
  });

  it('escapes a symbol that needs it', () => {
    expect(buildSvSecondHitHref(variant('HLA-DRB1'), 'F1')).toContain('gene=HLA-DRB1');
    expect(buildSvSecondHitHref(variant('C4A/C4B'), 'F1')).toContain('gene=C4A%2FC4B');
  });

  it('declines without a gene or without a family', () => {
    expect(buildSvSecondHitHref(variant(), 'F1')).toBeNull();
    expect(buildSvSecondHitHref(variant('  '), 'F1')).toBeNull();
    expect(buildSvSecondHitHref(variant('TRNT1'), undefined)).toBeNull();
  });
});
