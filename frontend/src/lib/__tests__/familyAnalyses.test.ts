import { describe, expect, it } from 'vitest';

import { familyAnalysisTypes, holdsAnalysis } from '../familyAnalyses';

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
