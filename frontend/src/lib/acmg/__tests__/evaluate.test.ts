import { describe, expect, it } from 'vitest';

import { evaluateAcmg, type AcmgVariantInput } from '../evaluate';
import { buildInitialSelections } from '../index';
import type { AcmgCriterionCode, AcmgSuggestion } from '../types';

function codes(suggestions: AcmgSuggestion[]): AcmgCriterionCode[] {
  return suggestions.map((s) => s.code);
}

function find(suggestions: AcmgSuggestion[], code: AcmgCriterionCode) {
  return suggestions.find((s) => s.code === code);
}

describe('evaluateAcmg', () => {
  it('suggests PVS1 very strong for a HC LOF variant in a haploinsufficient gene', () => {
    const variant: AcmgVariantInput = { effect: 'stop_gained', lof: 'HC', gnomad_af: undefined };
    const suggestions = evaluateAcmg(variant, {
      clingenDosageHaploinsufficiency: 'Sufficient evidence for dosage pathogenicity',
    });
    const pvs1 = find(suggestions, 'PVS1');
    expect(pvs1?.strength).toBe('very_strong');
    // Absent from gnomAD also yields PM2.
    expect(codes(suggestions)).toContain('PM2');
  });

  it('downgrades PVS1 to strong when the LOF mechanism is unconfirmed', () => {
    const suggestions = evaluateAcmg({ effect: 'frameshift_variant', lof: 'HC' });
    expect(find(suggestions, 'PVS1')?.strength).toBe('strong');
  });

  it('rules out the other frequency criteria for a common variant', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant', gnomad_af: 0.12 });
    expect(find(suggestions, 'BA1')?.disposition).toBe('applies');
    // PM2/BS1 are ruled out by the observed frequency (not applicable).
    expect(find(suggestions, 'PM2')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BS1')?.disposition).toBe('not_applicable');
  });

  it('rules out BA1/BS1 for a rare variant and BS2 when no homozygotes', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant', gnomad_af: 1e-5 });
    expect(find(suggestions, 'PM2')?.disposition).toBe('applies');
    expect(find(suggestions, 'BA1')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BS1')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BS2')?.disposition).toBe('not_applicable');
  });

  it('contraindicates the opposing in-silico criterion', () => {
    const deleterious = evaluateAcmg({ effect: 'missense_variant', revel: 0.95 });
    expect(find(deleterious, 'PP3')?.disposition).toBe('applies');
    expect(find(deleterious, 'BP4')?.disposition).toBe('contraindicated');

    const benign = evaluateAcmg({ effect: 'missense_variant', revel: 0.05, spliceai_max: 0 });
    expect(find(benign, 'BP4')?.disposition).toBe('applies');
    expect(find(benign, 'PP3')?.disposition).toBe('contraindicated');
  });

  it('scales PP3 strength by REVEL', () => {
    expect(find(evaluateAcmg({ effect: 'missense_variant', revel: 0.95 }), 'PP3')?.strength).toBe(
      'strong',
    );
    expect(find(evaluateAcmg({ effect: 'missense_variant', revel: 0.8 }), 'PP3')?.strength).toBe(
      'moderate',
    );
    expect(find(evaluateAcmg({ effect: 'missense_variant', revel: 0.7 }), 'PP3')?.strength).toBe(
      'supporting',
    );
  });

  it('suggests BP4 for a benign-leaning REVEL with no splice impact', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant', revel: 0.05, spliceai_max: 0.0 });
    expect(codes(suggestions)).toContain('BP4');
  });

  it('suggests BP7 for a synonymous variant without splice impact', () => {
    const suggestions = evaluateAcmg({ effect: 'synonymous_variant', spliceai_max: 0.01 });
    expect(codes(suggestions)).toContain('BP7');
  });

  it('maps ClinVar assertions to PP5 / BP6', () => {
    expect(codes(evaluateAcmg({ clinvar: 'pathogenic' }))).toContain('PP5');
    expect(codes(evaluateAcmg({ clinvar: 'likely_benign' }))).toContain('BP6');
  });

  it('suggests PP4 only when proband HPO overlaps the gene phenotype', () => {
    const variant: AcmgVariantInput = { effect: 'missense_variant' };
    const gene = { geneHpoIds: ['HP:0001250'] };
    expect(codes(evaluateAcmg(variant, gene, { probandHpoIds: ['HP:0001250'] }))).toContain('PP4');
    expect(codes(evaluateAcmg(variant, gene, { probandHpoIds: ['HP:0000118'] }))).not.toContain(
      'PP4',
    );
  });

  it('scales PP4 strength by the Monarch phenotype-specificity score', () => {
    const variant: AcmgVariantInput = { effect: 'missense_variant' };
    const gene = { geneHpoIds: ['HP:0001250'] };
    // High specificity → Moderate.
    expect(
      find(evaluateAcmg(variant, gene, { probandHpoIds: [], phenotypeScore: 0.7 }), 'PP4')?.strength,
    ).toBe('moderate');
    // Meaningful but lower specificity → Supporting.
    expect(
      find(evaluateAcmg(variant, gene, { probandHpoIds: [], phenotypeScore: 0.4 }), 'PP4')?.strength,
    ).toBe('supporting');
    // Below threshold and no overlap → not suggested.
    expect(
      codes(evaluateAcmg(variant, gene, { probandHpoIds: [], phenotypeScore: 0.1 })),
    ).not.toContain('PP4');
    // No score → falls back to HPO overlap at Supporting.
    expect(
      find(evaluateAcmg(variant, gene, { probandHpoIds: ['HP:0001250'] }), 'PP4')?.strength,
    ).toBe('supporting');
  });

  it('rules out criteria that cannot apply to the molecular class', () => {
    // Missense: PVS1, PM4, BP7 are not applicable.
    const missense = evaluateAcmg({ effect: 'missense_variant' });
    expect(find(missense, 'PVS1')?.disposition).toBe('not_applicable');
    expect(find(missense, 'PM4')?.disposition).toBe('not_applicable');
    expect(find(missense, 'BP7')?.disposition).toBe('not_applicable');

    // LOF: PP2 (missense) and BP7 (synonymous) are not applicable.
    const lof = evaluateAcmg({ effect: 'stop_gained' });
    expect(find(lof, 'PP2')?.disposition).toBe('not_applicable');
    expect(find(lof, 'BP7')?.disposition).toBe('not_applicable');
  });
});

describe('evaluateAcmg — trio / segregation', () => {
  const probandHet = { sampleId: 'P', role: 'proband', affected: true, gt: '0/1' };

  it('flags de novo (PM6) when absent in both sequenced parents', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [
        probandHet,
        { sampleId: 'F', role: 'father', affected: false, gt: '0/0' },
        { sampleId: 'M', role: 'mother', affected: false, gt: '0/0' },
      ],
    });
    expect(find(suggestions, 'PM6')?.disposition).toBe('applies');
  });

  it('rules out de novo when inherited from a parent', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [
        probandHet,
        { sampleId: 'F', role: 'father', affected: false, gt: '0/1' },
        { sampleId: 'M', role: 'mother', affected: false, gt: '0/0' },
      ],
    });
    expect(find(suggestions, 'PM6')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PS2')?.disposition).toBe('not_applicable');
  });

  it('rules out de novo / segregation when there is no family data', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' });
    expect(find(suggestions, 'PS2')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PM6')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PP1')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BS4')?.disposition).toBe('not_applicable');
  });

  it('suggests PP1 cosegregation across multiple affected carriers', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [
        probandHet,
        { sampleId: 'S', role: 'sibling', affected: true, gt: '0/1' },
        { sampleId: 'M', role: 'mother', affected: true, gt: '0/1' },
      ],
    });
    expect(find(suggestions, 'PP1')?.disposition).toBe('consider');
  });

  it('suggests BS4 when an affected relative lacks the variant', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [probandHet, { sampleId: 'S', role: 'sibling', affected: true, gt: '0/0' }],
    });
    expect(find(suggestions, 'BS4')?.disposition).toBe('consider');
  });

  it('suggests PP1 when an extra affected relative carries the variant', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [probandHet, { sampleId: 'M', role: 'mother', affected: true, gt: '0/1' }],
    });
    expect(find(suggestions, 'PP1')?.disposition).toBe('consider');
  });
});

describe('evaluateAcmg — in-silico availability', () => {
  it('rules out PP3/BP4 when no in-silico prediction is available', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' });
    expect(find(suggestions, 'PP3')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BP4')?.disposition).toBe('not_applicable');
  });
});

describe('buildInitialSelections', () => {
  it('auto-accepts an "applies" suggestion', () => {
    const selections = buildInitialSelections([
      { code: 'PVS1', strength: 'very_strong', evidence: 'x', disposition: 'applies' },
    ]);
    expect(selections[0]).toMatchObject({ code: 'PVS1', accepted: true, autoSuggested: true });
  });

  it('surfaces a "consider" suggestion unchecked', () => {
    const selections = buildInitialSelections([
      { code: 'PM4', strength: 'moderate', evidence: 'x', disposition: 'consider' },
    ]);
    expect(selections[0]).toMatchObject({ code: 'PM4', accepted: false, autoSuggested: true });
  });

  it('flags a "contraindicated" suggestion unchecked', () => {
    const selections = buildInitialSelections([
      { code: 'BP4', strength: 'supporting', evidence: 'x', disposition: 'contraindicated' },
    ]);
    expect(selections[0]).toMatchObject({
      code: 'BP4',
      accepted: false,
      autoSuggested: false,
      contraindicated: true,
    });
  });

  it('preserves a saved selection over a fresh suggestion', () => {
    const selections = buildInitialSelections(
      [{ code: 'PVS1', strength: 'strong', evidence: 'fresh', disposition: 'applies' }],
      [{ code: 'PVS1', strength: 'very_strong', accepted: true, autoSuggested: false }],
    );
    expect(selections).toHaveLength(1);
    expect(selections[0]).toMatchObject({ accepted: true, strength: 'very_strong' });
  });
});

// #609 — a lookup that failed is unknown, not a negative finding: the criteria that read it
// say so, and are surfaced for review rather than silently changed.
describe('evaluateAcmg when a lookup failed', () => {
  it('does not call the LOF mechanism unconfirmed when the gene profile could not be loaded', () => {
    const pvs1 = find(evaluateAcmg({ effect: 'stop_gained', lof: 'HC' }, { unavailable: true }), 'PVS1');

    expect(pvs1?.disposition).toBe('consider');
    expect(pvs1?.evidence).toMatch(/the gene profile could not be loaded, so its LOF mechanism .* is not assessed/);
    expect(pvs1?.evidence).not.toMatch(/unconfirmed/);
  });

  it('asks to confirm the inheritance mode for BS2 when the gene profile could not be loaded', () => {
    const bs2 = find(
      evaluateAcmg({ effect: 'missense_variant', gnomad_af: 0.001, gnomad_hom_count: 3 }, { unavailable: true }),
      'BS2',
    );

    expect(bs2?.disposition).toBe('consider');
    expect(bs2?.evidence).toMatch(/the gene profile could not be loaded: confirm inheritance mode/);
  });

  it.each([
    ['the gene profile', { unavailable: true }, { probandHpoIds: ['HP:1'] }, "the gene's HPO associations"],
    ['the HPO terms', { geneHpoIds: ['HP:1'] }, { probandHpoUnavailable: true }, "the family's HPO terms"],
  ])('offers PP4 for review, not as no match, when %s could not be loaded', (_what, gene, phenotype, missing) => {
    const pp4 = find(evaluateAcmg({ effect: 'missense_variant' }, gene, phenotype), 'PP4');

    expect(pp4?.disposition).toBe('consider');
    expect(pp4?.evidence).toBe(`Not assessed: ${missing} could not be loaded. Review the phenotype match by hand.`);
  });

  it('still applies PP4 from a phenotype score, which needs neither lookup', () => {
    const pp4 = find(
      evaluateAcmg({ effect: 'missense_variant' }, { unavailable: true }, { probandHpoUnavailable: true, phenotypeScore: 0.7 }),
      'PP4',
    );

    expect(pp4?.disposition).toBe('applies');
    expect(pp4?.strength).toBe('moderate');
  });
});
