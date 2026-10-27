import { describe, expect, it } from 'vitest';

import {
  buildGnomadSvRegionHref,
  buildReviewTagTooltip,
  buildSvSecondHitHref,
  describeFilterTranscript,
  formatGenomicChange,
  formatPredictionScore,
  formatReviewTagLabel,
  formatVariantTotal,
  getClinvarHighlightTone,
  getClinvarTone,
  getReviewTagStyle,
  getUcscDb,
  isDeletedReviewTag,
  parseVariantIds,
  visibleReviewTagKeys,
} from '../smallVariantResultUtils';
import type { SmallVariantTagDefinition } from '../smallVariantSearch';

const variant = (chr: string, start: number, ref: string, alt: string) =>
  ({ chr, start, ref, alt }) as Parameters<typeof formatGenomicChange>[0];

describe('formatGenomicChange', () => {
  it('writes a substitution at the variant position', () => {
    expect(formatGenomicChange(variant('13', 32316461, 'G', 'A'))).toBe('chr13:g.32316461G>A');
  });

  it('keeps a chr-prefixed chromosome as given', () => {
    expect(formatGenomicChange(variant('chrX', 100, 'C', 'T'))).toBe('chrX:g.100C>T');
  });

  it('drops the VCF padding base from a deletion and spans the deleted bases', () => {
    // ACT>A deletes CT at 101-102, not ACT at 100.
    expect(formatGenomicChange(variant('1', 100, 'ACT', 'A'))).toBe('chr1:g.101_102del');
    expect(formatGenomicChange(variant('1', 100, 'AC', 'A'))).toBe('chr1:g.101del');
  });

  it('places an insertion between the flanking bases', () => {
    expect(formatGenomicChange(variant('1', 100, 'A', 'ACT'))).toBe('chr1:g.100_101insCT');
  });

  it('writes a multi-base substitution as delins', () => {
    expect(formatGenomicChange(variant('1', 100, 'AC', 'TG'))).toBe('chr1:g.100_101delinsTG');
    expect(formatGenomicChange(variant('1', 100, 'ACT', 'AG'))).toBe('chr1:g.101_102delinsG');
  });

  it('trims a shared suffix before deciding the span', () => {
    // AAT>AT is a deletion of one A, not a two-base delins.
    expect(formatGenomicChange(variant('1', 100, 'AAT', 'AT'))).toBe('chr1:g.101del');
  });

  it('returns null for symbolic or absent alleles', () => {
    expect(formatGenomicChange(variant('1', 100, '<DEL>', 'A'))).toBeNull();
    expect(formatGenomicChange(variant('1', 100, '', 'A'))).toBeNull();
    expect(formatGenomicChange(variant('1', 100, 'A', 'A'))).toBeNull();
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

describe('describeFilterTranscript', () => {
  // CLIN-51: the card describes the variant's primary transcript; the annotation filters
  // held on another. The second transcript of the e2e ready case: SIFT tolerated on the
  // card's transcript, deleterious on the one the filter kept.
  const matched = {
    gene: 'SYN51',
    transcript_id: 'ENST_SYN51_2',
    impact: 'MODERATE',
    effect: 'missense_variant',
    hgvsc: 'ENST_SYN51_2:c.100C>T',
    hgvsp: null,
    lof: null,
    cadd_phred: 25.1,
    revel: 0.31,
    spliceai_max: null,
    sift: 'deleterious(0.02)',
    polyphen: 'probably_damaging(0.95)',
  };
  const card = {
    gene: 'SYN51',
    effect: 'missense_variant',
    cadd_phred: 25.1,
    revel: 0.31,
    sift: 'tolerated(0.2)',
    polyphen: 'benign(0.1)',
  };

  it('names the transcript the filters held on, with the predictions that read otherwise', () => {
    expect(describeFilterTranscript({ ...card, filter_transcript: matched })).toEqual({
      transcriptId: 'ENST_SYN51_2',
      gene: null,
      details: ['SIFT deleterious (0.02)', 'PolyPhen probably damaging (0.95)'],
    });
  });

  it('says nothing when the filters held on the card’s own transcript', () => {
    expect(describeFilterTranscript({ ...card, filter_transcript: null })).toBeNull();
    expect(describeFilterTranscript(card)).toBeNull();
  });

  it('gives the consequence, LoF and scores that differ, and another gene', () => {
    expect(
      describeFilterTranscript({
        ...card,
        filter_transcript: {
          ...matched,
          gene: 'SYN51AS',
          effect: 'splice_donor_variant',
          impact: 'HIGH',
          lof: 'HC',
          revel: 0.714,
          spliceai_max: 0.91,
          sift: 'tolerated(0.2)',
          polyphen: null,
        },
      }),
    ).toEqual({
      transcriptId: 'ENST_SYN51_2',
      gene: 'SYN51AS',
      // CADD and SIFT read as the card's, the transcript has no PolyPhen.
      details: ['splice donor variant', 'LoF HC', 'REVEL 0.71', 'SpliceAI 0.91'],
    });
  });

  it('compares the values as the card writes them', () => {
    // A REVEL that rounds to the card's is not repeated.
    expect(
      describeFilterTranscript({
        ...card,
        filter_transcript: { ...matched, revel: 0.312, sift: 'tolerated(0.2)', polyphen: 'benign(0.1)' },
      })?.details,
    ).toEqual([]);
  });

  it('copes with an annotation without a transcript ID', () => {
    expect(
      describeFilterTranscript({ ...card, filter_transcript: { ...matched, transcript_id: null } })
        ?.transcriptId,
    ).toBeNull();
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
  const variant = (gene?: string, geneId?: string, hit?: Record<string, unknown>) =>
    ({
      gene,
      gene_id: geneId,
      sv_second_hit: hit,
    }) as Parameters<typeof buildSvSecondHitHref>[0];
  const locusHit = (chr: string, start: number, end: number, gene?: string) => ({
    chr,
    start,
    end,
    ...(gene ? { gene } : {}),
  });

  // The SVs' own span is not open to annotation-vs-coordinate disagreement, so it is
  // preferred over any gene filter.
  it('links to the span covering the SVs', () => {
    expect(
      buildSvSecondHitHref(variant('CR2', undefined, locusHit('1', 207494109, 207494110)), 'F1'),
    ).toBe('/families/F1/structural-variants?locus=1%3A207494109-207494110');
  });

  it('prefers the span even when a matched gene is present', () => {
    const href = buildSvSecondHitHref(
      variant('RPE', undefined, locusHit('10', 103251100, 103251101, 'UNC80')),
      'F1',
    );
    expect(href).toContain('locus=10%3A103251100-103251101');
    expect(href).not.toContain('gene=');
  });

  it('falls back to the gene list when the payload carries no span', () => {
    expect(buildSvSecondHitHref(variant('TRNT1'), 'F1')).toBe(
      '/families/F1/structural-variants?gene=TRNT1',
    );
  });

  it('carries the project through', () => {
    expect(
      buildSvSecondHitHref(variant('TRNT1', undefined, locusHit('3', 100, 200)), 'F1', 'P1'),
    ).toBe('/families/F1/structural-variants?locus=3%3A100-200&project_id=P1');
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

  // A small variant can be annotated to several genes and the SV may sit on any of
  // them; filtering on the primary symbol then finds nothing.
  it('filters on the gene the SV actually hits, not the primary symbol', () => {
    expect(buildSvSecondHitHref(variant('RPE', undefined, { gene: 'UNC80' }), 'F1')).toBe(
      '/families/F1/structural-variants?gene=UNC80',
    );
  });

  it('falls back to the primary symbol when the payload names no gene', () => {
    expect(buildSvSecondHitHref(variant('TRNT1', undefined, undefined), 'F1')).toBe(
      '/families/F1/structural-variants?gene=TRNT1',
    );
  });

  it('declines without a gene or without a family', () => {
    expect(buildSvSecondHitHref(variant(), 'F1')).toBeNull();
    expect(buildSvSecondHitHref(variant('  '), 'F1')).toBeNull();
    expect(buildSvSecondHitHref(variant('TRNT1'), undefined)).toBeNull();
  });
});

describe('buildGnomadSvRegionHref', () => {
  const sv = (chr: string, start: number, end: number) =>
    ({ chr, start, end }) as Parameters<typeof buildGnomadSvRegionHref>[0]['variant'];

  it('opens the SV browser over the call span for GRCh38', () => {
    expect(
      buildGnomadSvRegionHref({
        variant: sv('2', 160120294, 160120688),
        speciesName: 'Homo sapiens',
        assemblyName: 'GRCh38',
      }),
    ).toBe(
      'https://gnomad.broadinstitute.org/region/2-160120294-160120688?dataset=gnomad_sv_r4',
    );
  });

  it('uses the v2 SV dataset for GRCh37', () => {
    const href = buildGnomadSvRegionHref({
      variant: sv('2', 100, 200),
      speciesName: 'Homo sapiens',
      assemblyName: 'GRCh37',
    });
    expect(href).toContain('dataset=gnomad_sv_r2_1');
  });

  it('strips a chr prefix, since gnomAD regions are unprefixed', () => {
    const href = buildGnomadSvRegionHref({
      variant: sv('chrX', 100, 200),
      speciesName: 'Homo sapiens',
      assemblyName: 'GRCh38',
    });
    expect(href).toContain('/region/X-100-200');
  });

  it('stays silent where gnomAD has nothing to compare against', () => {
    // T2T coordinates do not correspond to any gnomAD release.
    expect(
      buildGnomadSvRegionHref({
        variant: sv('1', 100, 200),
        speciesName: 'Homo sapiens',
        assemblyName: 'T2T-CHM13v2.0',
      }),
    ).toBeNull();
    expect(
      buildGnomadSvRegionHref({
        variant: sv('1', 100, 200),
        speciesName: 'Mus musculus',
        assemblyName: 'GRCm39',
      }),
    ).toBeNull();
  });

  it('never emits an inverted span', () => {
    const href = buildGnomadSvRegionHref({
      variant: sv('1', 500, 100),
      speciesName: 'Homo sapiens',
      assemblyName: 'GRCh38',
    });
    expect(href).toContain('/region/1-500-500');
  });
});

describe('getUcscDb', () => {
  it('links T2T-CHM13 to UCSC hs1 under each name the assembly carries', () => {
    for (const [name, version] of [
      ['T2T CHM13v2.0', 'T2T-CHM13v2.0'],
      ['T2T-CHM13v2.0', 'hs1'],
      ['T2T_CHM13v2.0', ''],
      ['hs1', 'hs1'],
    ]) {
      expect(getUcscDb('Homo sapiens', name, version)).toBe('hs1');
    }
  });

  it('links GRCh38 and GRCh37 as before, and another CHM13 release not to hs1', () => {
    expect(getUcscDb('Homo sapiens', 'GRCh38', 'p14')).toBe('hg38');
    expect(getUcscDb('Homo sapiens', 'GRCh37', 'p13')).toBe('hg19');
    // hs1 is T2T-CHM13v2.0: another release's genes lie elsewhere on the acrocentric arms.
    expect(getUcscDb('Homo sapiens', 'T2T-CHM13v1.0', 'v1.0')).toBeNull();
  });
});

describe('formatVariantTotal', () => {
  it('shows an exact total as it is', () => {
    expect(formatVariantTotal(24680, false, 10000)).toBe('24,680');
    expect(formatVariantTotal(undefined)).toBe('0');
  });

  it('shows a count past its limit as the limit and a lower bound', () => {
    // The backend counts one past the limit to tell "more" from "exactly the limit".
    expect(formatVariantTotal(10001, true, 10000)).toBe('10,000+');
    // Without a limit it falls back to one less than the count sent.
    expect(formatVariantTotal(1001, true)).toBe('1,000+');
  });

  it('counts what a capped candidate read found, as a lower bound (#725 follow-up)', () => {
    // 12 matches in a capped window: 12 or more, not "11+".
    expect(formatVariantTotal(12, true, 10000)).toBe('12+');
    // Nothing in a capped window is not "no matches".
    expect(formatVariantTotal(0, true, 10000)).toBe('0+');
  });
});

describe('visibleReviewTagKeys', () => {
  it('leaves out the review, excluded and report quick tags, which have their own toggles', () => {
    expect(
      visibleReviewTagKeys(['review', 'acmg_class_4', 'excluded', 'needs_sanger', 'report']),
    ).toEqual(['acmg_class_4', 'needs_sanger']);
  });

  it('keeps the order it is given, as the cards and tables sort it', () => {
    expect(visibleReviewTagKeys(['zeta', 'report', 'alpha'])).toEqual(['zeta', 'alpha']);
  });

  it('matches the quick tags by their exact key', () => {
    expect(visibleReviewTagKeys(['Review', 'reported', 'not_excluded'])).toEqual([
      'Review',
      'reported',
      'not_excluded',
    ]);
    expect(visibleReviewTagKeys([])).toEqual([]);
  });
});

describe('review tag chips', () => {
  // A deleted custom tag stays on the reviews that hold it: its chip keeps the label and
  // colour, marked, so it is not taken for a tag that can still be added.
  const tag = (key: string, label: string, isActive?: boolean): SmallVariantTagDefinition => ({
    key,
    label,
    group: 'custom',
    color: '#336699',
    sort_order: 500,
    scope: 'global',
    is_custom: true,
    ...(isActive === undefined ? {} : { is_active: isActive }),
  });
  const tagMap = {
    needs_segregation: tag('needs_segregation', 'Needs segregation', true),
    probe_x: tag('probe_x', 'Probe X', false),
    review: tag('review', 'Review'),
  };

  it('reads a deleted tag by its label, marked', () => {
    expect(formatReviewTagLabel('probe_x', tagMap)).toBe('Probe X (deleted)');
    expect(isDeletedReviewTag('probe_x', tagMap)).toBe(true);
  });

  it('reads an active tag, or one listed without the flag, by its label alone', () => {
    expect(formatReviewTagLabel('needs_segregation', tagMap)).toBe('Needs segregation');
    expect(formatReviewTagLabel('review', tagMap)).toBe('Review');
    expect(isDeletedReviewTag('review', tagMap)).toBe(false);
  });

  it('reads a key the tag list does not hold as the bare key', () => {
    expect(formatReviewTagLabel('legacy_key', tagMap)).toBe('legacy_key');
    expect(isDeletedReviewTag('legacy_key', tagMap)).toBe(false);
  });

  it('dashes the border of a deleted tag only', () => {
    expect(getReviewTagStyle('probe_x', tagMap)).toHaveProperty('borderStyle', 'dashed');
    expect(getReviewTagStyle('needs_segregation', tagMap)).not.toHaveProperty('borderStyle');
    expect(getReviewTagStyle('legacy_key', tagMap)).not.toHaveProperty('borderStyle');
  });

  it('marks a deleted tag in its tooltip too', () => {
    expect(
      buildReviewTagTooltip({
        tagKey: 'probe_x',
        tagMap,
        tagMetadata: { probe_x: { updated_by: 'reviewer', updated_at: null } },
      }),
    ).toBe('Probe X (deleted) · reviewer');
  });
});

// The chip tone and the table-row highlight read ClinVar's significance the same way.
describe('ClinVar tones', () => {
  it.each([
    ['Pathogenic', 'critical', 'pathogenic'],
    ['Likely_pathogenic', 'critical', 'pathogenic'],
    ['Pathogenic/Likely_pathogenic', 'critical', 'pathogenic'],
    ['likely pathogenic, low penetrance', 'critical', 'pathogenic'],
    ['risk_factor', 'warning', 'neutral'],
    ['Benign', 'success', 'benign'],
    ['Likely_benign', 'success', 'benign'],
    ['Benign/Likely_benign', 'success', 'benign'],
    ['Uncertain_significance', 'neutral', 'neutral'],
    ['Conflicting_classifications_of_pathogenicity', 'neutral', 'neutral'],
    [undefined, 'neutral', 'neutral'],
  ])('reads %s as a %s chip and a %s row', (clinvar, tone, highlight) => {
    expect(getClinvarTone(clinvar)).toBe(tone);
    expect(getClinvarHighlightTone(clinvar)).toBe(highlight);
  });
});
