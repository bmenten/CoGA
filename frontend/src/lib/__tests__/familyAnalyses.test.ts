import { describe, expect, it } from 'vitest';

import {
  analysisLabel,
  analysisQuery,
  familyAnalysisTypes,
  holdsAnalysis,
  niptReportAnalysis,
  niptVariantAnalysis,
  reportPageAnalyses,
  separatelySignedAnalyses,
  smallVariantAnalyses,
  variantAnalysisOf,
  soleAnalysis,
  withAnalysisParam,
} from '../familyAnalyses';

// A family may hold several analyses (backend family_analysis_types.py): the monogenic and the
// conventional NIPT of one pregnancy. Readers ask whether it holds one, never which one it holds.
describe('the analyses a family holds', () => {
  it('reads every analysis type the metadata lists', () => {
    const metadata = { analysis_types: ['conventional_nipt', 'monogenic_nipt'] };
    expect(familyAnalysisTypes(metadata)).toEqual(['conventional_nipt', 'monogenic_nipt']);
    expect(holdsAnalysis(metadata, 'monogenic_nipt')).toBe(true);
    expect(holdsAnalysis(metadata, 'conventional_nipt')).toBe(true);
    expect(holdsAnalysis(metadata, 'pgt')).toBe(false);
  });

  it('holds none without a list', () => {
    for (const metadata of [undefined, null, {}, { analysis_types: 'monogenic_nipt' }, { analysis_type: 'monogenic_nipt' }]) {
      expect(familyAnalysisTypes(metadata)).toEqual([]);
      expect(holdsAnalysis(metadata, 'monogenic_nipt')).toBe(false);
    }
  });
});

// The owner's decision of 2026-11-19: each analysis of a family that holds several is signed out on
// its own, from its own report; a family of one analysis or none is signed out whole.
describe('the analysis each report signs out', () => {
  it('signs out each analysis of a family that holds several on its own', () => {
    const nipts = { analysis_types: ['monogenic_nipt', 'conventional_nipt'] };
    expect(separatelySignedAnalyses(nipts)).toEqual(['conventional_nipt', 'monogenic_nipt']);
    // The NIPT report signs out the monogenic NIPT; the Report page every other.
    expect(niptReportAnalysis(nipts)).toBe('monogenic_nipt');
    expect(reportPageAnalyses(nipts)).toEqual(['conventional_nipt']);
    const germline = { analysis_types: ['short_read_wgs', 'long_read_wgs'] };
    expect(niptReportAnalysis(germline)).toBeNull();
    expect(reportPageAnalyses(germline)).toEqual(['long_read_wgs', 'short_read_wgs']);
  });

  it('signs out a family of one analysis or none whole', () => {
    for (const metadata of [{ analysis_types: ['monogenic_nipt'] }, { analysis_types: [] }, undefined]) {
      expect(separatelySignedAnalyses(metadata)).toEqual([]);
      expect(niptReportAnalysis(metadata)).toBeNull();
      expect(reportPageAnalyses(metadata)).toEqual([]);
    }
  });

  // The analysis whose record a page reads when it names none, as the sign-out check resolves it.
  it('reads the record of a family of one analysis as that analysis’s', () => {
    expect(soleAnalysis({ analysis_types: ['short_read_wgs'] })).toBe('short_read_wgs');
    expect(soleAnalysis({ analysis_types: [] })).toBeNull();
    expect(soleAnalysis(undefined)).toBeNull();
    // Of several, the page must name one.
    expect(soleAnalysis({ analysis_types: ['long_read_wgs', 'short_read_wgs'] })).toBeNull();
  });

  it('names each analysis in a sentence', () => {
    expect(analysisLabel('conventional_nipt')).toBe('conventional NIPT');
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
  analysis_types: ['long_read_wgs', 'short_read_wgs'],
  package_imports: {
    long_read_wgs: record(['deepvariant'], '2026-11-02T10:00:00'),
    short_read_wgs: record(['haplotypecaller'], '2026-11-01T10:00:00'),
  },
};
const TWO_NIPTS = {
  analysis_types: ['conventional_nipt', 'monogenic_nipt'],
  package_imports: {
    monogenic_nipt: record(['nipt'], '2026-11-01T10:00:00'),
    conventional_nipt: record([], '2026-11-02T10:00:00'),
  },
};

describe('the analysis a page of the family’s variants reads', () => {
  it('lists the analyses with small variants of their own, in the order they were imported', () => {
    expect(smallVariantAnalyses(TWO_WGS)).toEqual(['short_read_wgs', 'long_read_wgs']);
    expect(smallVariantAnalyses(TWO_NIPTS)).toEqual(['monogenic_nipt']);
    expect(smallVariantAnalyses(null)).toEqual([]);
  });

  it('reads the analysis the URL names, else the one imported last', () => {
    expect(variantAnalysisOf(TWO_WGS, '?analysis=short_read_wgs')).toBe('short_read_wgs');
    expect(variantAnalysisOf(TWO_WGS, '')).toBe('long_read_wgs');
    expect(variantAnalysisOf(TWO_WGS, '?analysis=pgt')).toBe('long_read_wgs');
  });

  it('names none for a family with at most one analysis with small variants', () => {
    expect(variantAnalysisOf(TWO_NIPTS, '?analysis=monogenic_nipt')).toBeNull();
    expect(niptVariantAnalysis(TWO_NIPTS)).toBeNull();
    expect(analysisQuery(null)).toBe('');
    expect(withAnalysisParam(new URLSearchParams('page=1'), null).toString()).toBe('page=1');
  });

  it('names the analysis in a request', () => {
    expect(analysisQuery('long_read_wgs')).toBe('?analysis=long_read_wgs');
    expect(withAnalysisParam(new URLSearchParams('page=1'), 'short_read_wgs').toString()).toBe(
      'page=1&analysis=short_read_wgs',
    );
    // The monogenic NIPT page names its analysis beside the couple's carrier screening.
    const withScreening = {
      analysis_types: ['expanded_carrier_screening', 'monogenic_nipt'],
      package_imports: {
        expanded_carrier_screening: record(['deepvariant'], '2026-10-01T10:00:00'),
        monogenic_nipt: record(['nipt'], '2026-11-01T10:00:00'),
      },
    };
    expect(niptVariantAnalysis(withScreening)).toBe('monogenic_nipt');
  });
});
