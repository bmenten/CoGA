import { describe, expect, it } from 'vitest';

import {
  analysisLabel,
  familyAnalysisTypes,
  holdsAnalysis,
  niptReportAnalysis,
  reportPageAnalyses,
  separatelySignedAnalyses,
  soleAnalysis,
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
