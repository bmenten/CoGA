import { describe, expect, it } from 'vitest';

import {
  buildInitialCnvSelections,
  classKeyForPoints,
  cnvGeneCountMode,
  cnvGeneCountTier,
  cnvKindForType,
  computeCnvClassification,
  evaluateCnv,
  formatCnvPoints,
} from '../cnvAcmg';

describe('cnvAcmg score', () => {
  it('maps point totals to ClinGen classes', () => {
    expect(classKeyForPoints(0.99)).toBe('cnv_class_5');
    expect(classKeyForPoints(0.9)).toBe('cnv_class_4');
    expect(classKeyForPoints(0)).toBe('cnv_class_3');
    expect(classKeyForPoints(-0.9)).toBe('cnv_class_2');
    expect(classKeyForPoints(-0.99)).toBe('cnv_class_1');
  });

  it('sums only accepted criteria and clamps to range', () => {
    const result = computeCnvClassification('loss', [
      { code: '2A', points: 1.0, accepted: true, autoSuggested: false },
      { code: '2H', points: 5.0, accepted: true, autoSuggested: false }, // clamps to 0.15
      { code: '3B', points: 0.45, accepted: false, autoSuggested: false }, // ignored
    ]);
    expect(result.pointTotal).toBe(1.15);
    expect(result.classKey).toBe('cnv_class_5');
  });
});

describe('formatCnvPoints', () => {
  it('writes a value signed and to two decimals, so the class thresholds read apart', () => {
    // 0.90 is Likely pathogenic and 0.99 Pathogenic: one decimal ("+0.9") hid the difference.
    expect(classKeyForPoints(0.9)).toBe('cnv_class_4');
    expect(formatCnvPoints(0.9)).toBe('+0.90');
    expect(classKeyForPoints(0.99)).toBe('cnv_class_5');
    expect(formatCnvPoints(0.99)).toBe('+0.99');
    expect(formatCnvPoints(-0.9)).toBe('-0.90');
    expect(formatCnvPoints(-0.99)).toBe('-0.99');
    expect(formatCnvPoints(0.45)).toBe('+0.45');
    expect(formatCnvPoints(-0.6)).toBe('-0.60');
    expect(formatCnvPoints(1.15)).toBe('+1.15');
  });

  it('keeps both decimals on a whole number', () => {
    expect(formatCnvPoints(1)).toBe('+1.00');
    expect(formatCnvPoints(-1)).toBe('-1.00');
  });

  it('writes zero as 0.00 without a sign, also when a sum leaves floating-point noise', () => {
    expect(formatCnvPoints(0)).toBe('0.00');
    expect(formatCnvPoints(-0)).toBe('0.00');
    expect(formatCnvPoints(0.1 + 0.2 - 0.3)).toBe('0.00'); // 5.6e-17
    expect(formatCnvPoints(0.3 - 0.1 - 0.2)).toBe('0.00'); // -2.8e-17, not "-0.00"
  });
});

describe('cnvAcmg evaluate', () => {
  it('infers kind from SV type', () => {
    expect(cnvKindForType('DEL')).toBe('loss');
    expect(cnvKindForType('DUP')).toBe('gain');
  });

  it('suggests 1B for an intergenic event', () => {
    const suggestions = evaluateCnv({ type: 'DEL', gene: null });
    expect(suggestions.some((s) => s.code === '1B')).toBe(true);
  });

  it('counts all genes for deletions but only disrupted genes for inv/bnd/ins', () => {
    expect(cnvGeneCountMode('DEL')).toBe('all');
    expect(cnvGeneCountMode('DUP')).toBe('all');
    expect(cnvGeneCountMode('INV')).toBe('disrupted');
    expect(cnvGeneCountMode('BND')).toBe('disrupted');
    expect(cnvGeneCountMode('INS')).toBe('disrupted');
  });

  it('tiers section-3 gene counts per ClinGen thresholds', () => {
    expect(cnvGeneCountTier('loss', 10).code).toBe('3A');
    expect(cnvGeneCountTier('loss', 30).code).toBe('3B');
    expect(cnvGeneCountTier('loss', 40).code).toBe('3C');
    // Gains use the higher thresholds.
    expect(cnvGeneCountTier('gain', 40).code).toBe('3B');
    expect(cnvGeneCountTier('gain', 60).code).toBe('3C');
  });

  it('auto-scores section 3 from gene content for a multi-gene deletion', () => {
    const suggestions = evaluateCnv({ type: 'DEL', gene: 'GENE1', geneCount: 40 });
    const tier = suggestions.find((s) => s.code.startsWith('3'));
    expect(tier?.code).toBe('3C');
    expect(tier?.points).toBe(0.9);
  });

  it('does not auto-score section 3 above 3A for inversions', () => {
    const suggestions = evaluateCnv({ type: 'INV', gene: 'GENE1', geneCount: 40 });
    const tier = suggestions.find((s) => s.code.startsWith('3'));
    expect(tier?.code).toBe('3A');
  });

  it('suggests 2H for a constrained-gene loss', () => {
    const suggestions = evaluateCnv({ type: 'DEL', gene: 'TCF4', genePli: 0.99 });
    expect(suggestions.some((s) => s.code === '2H')).toBe(true);
  });

  it('builds a full selection list with saved precedence', () => {
    const suggestions = evaluateCnv({ type: 'DEL', gene: 'TCF4', genePli: 0.99 });
    const selections = buildInitialCnvSelections('loss', suggestions, [
      { code: '2A', points: 1.0, accepted: true, auto_suggested: false },
    ]);
    const twoA = selections.find((s) => s.code === '2A');
    expect(twoA?.accepted).toBe(true);
    expect(twoA?.points).toBe(1.0);
    // 2H came from the evaluator (auto-suggested, accepted).
    const twoH = selections.find((s) => s.code === '2H');
    expect(twoH?.autoSuggested).toBe(true);
  });
});
