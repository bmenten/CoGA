import { describe, expect, it } from 'vitest';

import {
  buildInitialCnvSelections,
  classKeyForPoints,
  cnvCriterionMap,
  cnvGeneCountMode,
  cnvGeneCountTier,
  cnvKindForType,
  computeCnvClassification,
  evaluateCnv,
  formatCnvPoints,
  type CnvKind,
  type CnvVariantInput,
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

  it('reads an SV type without its subtypes, as the server does (CLIN-47)', () => {
    // A tandem duplication opened on the loss criteria: only the exact type DUP was a gain.
    expect(cnvKindForType('DUP:TANDEM')).toBe('gain');
    expect(cnvKindForType('dup:tandem')).toBe('gain');
    expect(cnvKindForType('INS:ME')).toBe('gain');
    expect(cnvKindForType('DEL:ME')).toBe('loss');
    // A CNV names no direction in its type: the dialog opens it on loss, as before.
    expect(cnvKindForType('CNV')).toBe('loss');
    expect(cnvGeneCountMode('DUP:TANDEM')).toBe('all');
    expect(cnvGeneCountMode('DEL:ME')).toBe('all');
    expect(cnvGeneCountMode('INS:ME')).toBe('disrupted');
  });

  it('suggests the gain criteria for a tandem duplication in a constrained gene', () => {
    const suggestions = evaluateCnv({ type: 'DUP:TANDEM', gene: 'TCF4', genePli: 0.99, geneCount: 40 });
    // 2H is a loss criterion (haploinsufficiency predictors), and 40 genes is 3B for a gain
    // (35–49), where it is 3C for a loss.
    expect(suggestions.some((s) => s.code === '2H')).toBe(false);
    expect(suggestions.find((s) => s.code.startsWith('3'))?.code).toBe('3B');
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

  it('suggests for the kind it is asked for, not the kind of the type (CLIN-55)', () => {
    // A 30-gene deletion in a constrained gene. As a loss, 25–34 genes are 3B and the pLI
    // suggests 2H; as a gain, 30 genes are 3A and 2H is not suggested (a gain's 2H is an HI
    // gene inside the gain). The dialog seeded both kinds from the loss's suggestions, so on
    // gain 3B was an accepted +0.45 and 2H carried the constrained-gene note.
    const deletion: CnvVariantInput = { type: 'DEL', gene: 'GENEX', genePli: 0.99, geneCount: 30 };
    const asLoss = evaluateCnv(deletion, 'loss');
    expect(asLoss.find((s) => s.code.startsWith('3'))).toMatchObject({ code: '3B', points: 0.45 });
    expect(asLoss.map((s) => s.code)).toContain('2H');
    const asGain = evaluateCnv(deletion, 'gain');
    expect(asGain.find((s) => s.code.startsWith('3'))).toMatchObject({ code: '3A', points: 0 });
    expect(asGain.map((s) => s.code)).not.toContain('2H');
    // Asked for no kind, it suggests for the kind of the type.
    expect(evaluateCnv(deletion)).toEqual(asLoss);

    // The other way: a 40-gene duplication is 3B as a gain, and 3C with 2H as a loss.
    const duplication: CnvVariantInput = { type: 'DUP', gene: 'GENEX', genePli: 0.99, geneCount: 40 };
    expect(evaluateCnv(duplication).find((s) => s.code.startsWith('3'))?.code).toBe('3B');
    const dupAsLoss = evaluateCnv(duplication, 'loss');
    expect(dupAsLoss.find((s) => s.code.startsWith('3'))).toMatchObject({ code: '3C', points: 0.9 });
    expect(dupAsLoss.map((s) => s.code)).toContain('2H');
  });

  it("suggests only the kind's own criteria, each within its range, so the server keeps them", () => {
    // The server keeps a point value within its criterion's range (cnv_acmg_points.clamp_points):
    // a suggestion outside it was shown and sent at one value and scored at another.
    const inputs: CnvVariantInput[] = [
      { type: 'DEL', gene: 'GENEX', genePli: 0.99, geneCount: 30, inheritance: 'de novo' },
      { type: 'DUP:TANDEM', gene: 'GENEX', genePli: 0.95, geneCount: 60, inheritance: 'maternal' },
      { type: 'CNV', gene: null, geneCount: 0 },
      { type: 'INV', gene: 'GENEX', genePli: 0.99, geneCount: 12 },
      { type: 'INS:ME', gene: 'GENEX', geneCount: 3 },
    ];
    const kinds: CnvKind[] = ['loss', 'gain'];
    const outside = kinds.flatMap((kind) =>
      inputs.flatMap((input) =>
        evaluateCnv(input, kind)
          .filter((suggestion) => {
            const def = cnvCriterionMap(kind)[suggestion.code];
            return !def || suggestion.points < def.minPoints || suggestion.points > def.maxPoints;
          })
          .map((suggestion) => `${input.type} as ${kind}: ${suggestion.code} ${suggestion.points}`),
      ),
    );
    expect(outside).toEqual([]);
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
