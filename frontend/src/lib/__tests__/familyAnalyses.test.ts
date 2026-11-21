import { describe, expect, it } from 'vitest';

import {
  analysisLabel,
  analysisQuery,
  analysisTypeOf,
  familyAnalyses,
  familyAnalysisTypes,
  holdsAnalysis,
  niptReportAnalysis,
  pregnancyAnalyses,
  pregnancyAnalysisOf,
  pregnancyOf,
  reportPageAnalyses,
  separatelySignedAnalyses,
  smallVariantAnalyses,
  variantAnalysisOf,
  soleAnalysis,
  withAnalysisParam,
} from '../familyAnalyses';
import { sourceBase } from '../pregnancySources';

// The monogenic and the conventional NIPT of the pregnancy ZW1, and the monogenic NIPT of a later
// pregnancy ZW2, by their keys (backend family_analysis_types.analysis_key).
const MNIPT1 = 'monogenic_nipt:ZW1';
const MNIPT2 = 'monogenic_nipt:ZW2';
const CNIPT1 = 'conventional_nipt:ZW1';

// A family may hold several analyses (backend family_analysis_types.py), each by its key: its
// type, and a NIPT's pregnancy. Readers ask whether it holds a type, or read one analysis by key.
describe('the analyses a family holds', () => {
  it('reads every analysis the metadata lists, and their types', () => {
    const metadata = { analyses: [CNIPT1, MNIPT1, MNIPT2] };
    expect(familyAnalyses(metadata)).toEqual([CNIPT1, MNIPT1, MNIPT2]);
    expect(familyAnalysisTypes(metadata)).toEqual(['conventional_nipt', 'monogenic_nipt']);
    expect(holdsAnalysis(metadata, 'monogenic_nipt')).toBe(true);
    expect(holdsAnalysis(metadata, 'conventional_nipt')).toBe(true);
    expect(holdsAnalysis(metadata, 'pgt')).toBe(false);
  });

  it('holds none without a list', () => {
    for (const metadata of [undefined, null, {}, { analyses: 'monogenic_nipt:ZW1' }, { analysis_type: 'monogenic_nipt' }]) {
      expect(familyAnalysisTypes(metadata)).toEqual([]);
      expect(holdsAnalysis(metadata, 'monogenic_nipt')).toBe(false);
    }
  });

  it('reads an analysis key as its type and its pregnancy', () => {
    expect([analysisTypeOf(MNIPT2), pregnancyOf(MNIPT2)]).toEqual(['monogenic_nipt', 'ZW2']);
    expect([analysisTypeOf('short_read_wgs'), pregnancyOf('short_read_wgs')]).toEqual(['short_read_wgs', null]);
    // A pregnancy ID may hold the separator: the key splits at the first.
    expect(pregnancyOf('conventional_nipt:ZW:2')).toBe('ZW:2');
    expect(pregnancyOf(null)).toBeNull();
  });
});

// The owner's decisions of 2026-11-19 and 2026-11-21: each analysis of a family that holds several
// is signed out on its own, from its own report, each pregnancy's monogenic NIPT from its NIPT
// report; a family of one analysis or none is signed out whole.
describe('the analysis each report signs out', () => {
  it('signs out each analysis of a family that holds several on its own', () => {
    const nipts = { analyses: [MNIPT1, CNIPT1, MNIPT2] };
    expect(separatelySignedAnalyses(nipts)).toEqual([CNIPT1, MNIPT1, MNIPT2]);
    // Each NIPT report signs out its pregnancy's monogenic NIPT; the Report page every other.
    expect(niptReportAnalysis(nipts, MNIPT2)).toBe(MNIPT2);
    expect(reportPageAnalyses(nipts)).toEqual([CNIPT1]);
    const germline = { analyses: ['short_read_wgs', 'long_read_wgs'] };
    expect(niptReportAnalysis(germline, null)).toBeNull();
    expect(reportPageAnalyses(germline)).toEqual(['long_read_wgs', 'short_read_wgs']);
  });

  it('signs out a family of one analysis or none whole', () => {
    for (const metadata of [{ analyses: [MNIPT1] }, { analyses: [] }, undefined]) {
      expect(separatelySignedAnalyses(metadata)).toEqual([]);
      expect(niptReportAnalysis(metadata, MNIPT1)).toBeNull();
      expect(reportPageAnalyses(metadata)).toEqual([]);
    }
  });

  // The analysis whose record a page reads when it names none, as the sign-out check resolves it.
  it('reads the record of a family of one analysis as that analysis’s', () => {
    expect(soleAnalysis({ analyses: ['short_read_wgs'] })).toBe('short_read_wgs');
    expect(soleAnalysis({ analyses: [] })).toBeNull();
    expect(soleAnalysis(undefined)).toBeNull();
    // Of several, the page must name one.
    expect(soleAnalysis({ analyses: ['long_read_wgs', 'short_read_wgs'] })).toBeNull();
  });

  it('names each analysis in a sentence, a NIPT with its pregnancy', () => {
    expect(analysisLabel('conventional_nipt')).toBe('conventional NIPT');
    expect(analysisLabel(MNIPT2)).toBe('monogenic NIPT of pregnancy ZW2');
    expect(analysisLabel('unspecified')).toBeNull();
    expect(analysisLabel(null)).toBeNull();
  });
});

// Short-read and then long-read WGS of the same DNA, each with a callset of its own: the family's
// small variants are read one analysis at a time (the owner's decision of 2026-11-20).
const record = (smallVariants: string[], registeredAt: string) => ({
  sources: { small_variants: smallVariants, structural_variants: [], tracks: [], repeats: [], paraphase: [] },
  registered_at: registeredAt,
});
const TWO_WGS = {
  analyses: ['long_read_wgs', 'short_read_wgs'],
  package_imports: {
    long_read_wgs: record(['deepvariant'], '2026-11-02T10:00:00'),
    short_read_wgs: record(['haplotypecaller'], '2026-11-01T10:00:00'),
  },
};
const ONE_PREGNANCY = {
  analyses: [CNIPT1, MNIPT1],
  package_imports: {
    [MNIPT1]: record(['nipt_p1a2b3c4d'], '2026-11-01T10:00:00'),
    [CNIPT1]: record([], '2026-11-02T10:00:00'),
  },
};
// Two pregnancies of one woman, each with its monogenic NIPT, the first with its conventional one.
const TWO_PREGNANCIES = {
  analyses: [CNIPT1, MNIPT1, MNIPT2],
  package_imports: {
    [MNIPT2]: record(['nipt_p9f8e7d6c'], '2026-11-20T10:00:00'),
    [MNIPT1]: record(['nipt_p1a2b3c4d'], '2026-11-01T10:00:00'),
    [CNIPT1]: record([], '2026-11-02T10:00:00'),
  },
};

describe('the analysis a page of the family’s variants reads', () => {
  it('lists the analyses with small variants of their own, in the order they were imported', () => {
    expect(smallVariantAnalyses(TWO_WGS)).toEqual(['short_read_wgs', 'long_read_wgs']);
    expect(smallVariantAnalyses(ONE_PREGNANCY)).toEqual([MNIPT1]);
    expect(smallVariantAnalyses(TWO_PREGNANCIES)).toEqual([MNIPT1, MNIPT2]);
    expect(smallVariantAnalyses(null)).toEqual([]);
  });

  it('reads the analysis the URL names, else the one imported last', () => {
    expect(variantAnalysisOf(TWO_WGS, '?analysis=short_read_wgs')).toBe('short_read_wgs');
    expect(variantAnalysisOf(TWO_WGS, '')).toBe('long_read_wgs');
    expect(variantAnalysisOf(TWO_WGS, '?analysis=pgt')).toBe('long_read_wgs');
  });

  it('names none for a family with at most one analysis with small variants', () => {
    expect(variantAnalysisOf(ONE_PREGNANCY, `?analysis=${MNIPT1}`)).toBeNull();
    expect(analysisQuery(null)).toBe('');
    expect(withAnalysisParam(new URLSearchParams('page=1'), null).toString()).toBe('page=1');
  });

  it('names the analysis in a request', () => {
    expect(analysisQuery('long_read_wgs')).toBe('?analysis=long_read_wgs');
    expect(analysisQuery(MNIPT1)).toBe('?analysis=monogenic_nipt%3AZW1');
    expect(withAnalysisParam(new URLSearchParams('page=1'), 'short_read_wgs').toString()).toBe(
      'page=1&analysis=short_read_wgs',
    );
  });
});

// The owner's decision of 2026-11-21: a family holds the NIPT of each pregnancy, read one at a time.
describe('the pregnancy a NIPT page reads', () => {
  it('lists each pregnancy’s NIPT of a type, in the order they were imported', () => {
    expect(pregnancyAnalyses(TWO_PREGNANCIES, 'monogenic_nipt')).toEqual([MNIPT1, MNIPT2]);
    expect(pregnancyAnalyses(TWO_PREGNANCIES, 'conventional_nipt')).toEqual([CNIPT1]);
    expect(pregnancyAnalyses(TWO_WGS, 'monogenic_nipt')).toEqual([]);
  });

  it('reads the pregnancy the URL names, else the one imported last', () => {
    expect(pregnancyAnalysisOf(TWO_PREGNANCIES, 'monogenic_nipt', `?analysis=${encodeURIComponent(MNIPT1)}`)).toBe(
      MNIPT1,
    );
    expect(pregnancyAnalysisOf(TWO_PREGNANCIES, 'monogenic_nipt', '')).toBe(MNIPT2);
    // Another type's analysis in the URL: the last of this type.
    expect(pregnancyAnalysisOf(TWO_PREGNANCIES, 'monogenic_nipt', `?analysis=${CNIPT1}`)).toBe(MNIPT2);
    expect(pregnancyAnalysisOf(TWO_PREGNANCIES, 'conventional_nipt', '')).toBe(CNIPT1);
    expect(pregnancyAnalysisOf(TWO_WGS, 'monogenic_nipt', '')).toBeNull();
  });

  it('reads a pregnancy’s own storage source as the source it is kept for', () => {
    expect(sourceBase('ccnv_p1a2b3c4d')).toBe('ccnv');
    expect(sourceBase('nipt_p1a2b3c4d')).toBe('nipt');
    expect(sourceBase('web')).toBe('web');
    expect(sourceBase('deepvariant')).toBe('deepvariant');
    expect(sourceBase(null)).toBe('');
  });
});
