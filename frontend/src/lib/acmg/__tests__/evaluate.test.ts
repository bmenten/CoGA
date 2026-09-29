import { describe, expect, it } from 'vitest';

import { evaluateAcmg, type AcmgVariantInput } from '../evaluate';
import { buildInitialSelections } from '../index';
import type { AcmgCriterionCode, AcmgFamilyMemberCall, AcmgParentLink, AcmgSuggestion } from '../types';

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

    const pathogenic = evaluateAcmg({ clinvar: 'Pathogenic/Likely_pathogenic' });
    expect(find(pathogenic, 'PP5')?.disposition).toBe('applies');
    expect(find(pathogenic, 'BP6')?.disposition).toBe('contraindicated');
    const benign = evaluateAcmg({ clinvar: 'Benign/Likely_benign' });
    expect(find(benign, 'BP6')?.disposition).toBe('applies');
    expect(find(benign, 'PP5')?.disposition).toBe('contraindicated');
  });

  // "Conflicting classifications of pathogenicity" contains "pathogenic": read as a
  // substring, it applied PP5 and argued against BP6.
  it.each([
    'Conflicting_classifications_of_pathogenicity',
    'Conflicting interpretations of pathogenicity',
    'Conflicting_classifications_of_pathogenicity|risk_factor',
  ])('suggests neither PP5 nor BP6 for a conflicting ClinVar record (%s)', (clinvar) => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant', clinvar });

    expect(find(suggestions, 'PP5')).toBeUndefined();
    expect(find(suggestions, 'BP6')).toBeUndefined();
  });

  it('suggests neither PP5 nor BP6 for an uncertain ClinVar record', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant', clinvar: 'Uncertain_significance' });

    expect(find(suggestions, 'PP5')).toBeUndefined();
    expect(find(suggestions, 'BP6')).toBeUndefined();
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

// The pedigree's parent-child links of a trio: PM6/PS2 read the proband's parents from them.
const trioLinks: AcmgParentLink[] = [
  { childId: 'P', parentId: 'F', role: 'father' },
  { childId: 'P', parentId: 'M', role: 'mother' },
];

describe('evaluateAcmg — trio / segregation', () => {
  const probandHet = { sampleId: 'P', role: 'proband', affected: true, gt: '0/1' };

  it('flags de novo (PM6) when absent in both sequenced parents', () => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, {
      members: [
        probandHet,
        { sampleId: 'F', role: 'father', affected: false, gt: '0/0' },
        { sampleId: 'M', role: 'mother', affected: false, gt: '0/0' },
      ],
      parentLinks: trioLinks,
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
      parentLinks: trioLinks,
    });
    expect(find(suggestions, 'PM6')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PM6')?.evidence).toBe('Inherited from a parent — not de novo.');
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

// PM6/PS2 compare the proband with the parents the pedigree links to them, as the backend's de
// novo mode does. The member roles cannot say who they are: a PED import stores everyone who has
// a child as 'father' or 'mother', grandparents included.
describe('evaluateAcmg — de novo against the parents the pedigree names', () => {
  const call = (sampleId: string, role: string, gt: string, dp?: number): AcmgFamilyMemberCall => ({
    sampleId,
    role,
    affected: sampleId === 'P',
    gt,
    dp,
  });
  // The paternal grandparents (GF, GM) are the father's parents.
  const threeGenerationLinks: AcmgParentLink[] = [
    ...trioLinks,
    { childId: 'F', parentId: 'GF', role: 'father' },
    { childId: 'F', parentId: 'GM', role: 'mother' },
  ];
  const deNovo = (members: AcmgFamilyMemberCall[], parentLinks?: AcmgParentLink[]) => {
    const suggestions = evaluateAcmg({ effect: 'missense_variant' }, undefined, undefined, { members, parentLinks });
    return { pm6: find(suggestions, 'PM6'), ps2: find(suggestions, 'PS2') };
  };

  it('does not call a variant de novo from reference grandparents when the father carries it', () => {
    // The grandparents are listed first, so a lookup by role finds them.
    const { pm6, ps2 } = deNovo(
      [
        call('GF', 'father', '0/0'),
        call('GM', 'mother', '0/0'),
        call('P', 'proband', '0/1'),
        call('F', 'father', '0/1'),
        call('M', 'mother', '0/0'),
      ],
      threeGenerationLinks,
    );

    expect(pm6?.disposition).toBe('not_applicable');
    expect(pm6?.evidence).toBe('Inherited from a parent — not de novo.');
    expect(ps2?.disposition).toBe('not_applicable');
  });

  it('applies PM6 when both parents are reference, though a grandparent carries the variant', () => {
    const { pm6 } = deNovo(
      [
        call('GF', 'father', '0/1'),
        call('GM', 'mother', '0/0'),
        call('P', 'proband', '0/1'),
        call('F', 'father', '0/0'),
        call('M', 'mother', '0/0'),
      ],
      threeGenerationLinks,
    );

    expect(pm6?.disposition).toBe('applies');
  });

  it('does not assess de novo when the pedigree does not link the proband to both parents', () => {
    const members = [call('P', 'proband', '0/1'), call('F', 'father', '0/0'), call('M', 'mother', '0/0')];
    const unlinked = {
      disposition: 'not_applicable',
      evidence: 'The pedigree does not name both parents of the proband — de novo cannot be assessed.',
    };

    // Reference parents by role, but no links.
    expect(deNovo(members).pm6).toMatchObject(unlinked);
    expect(deNovo(members).ps2).toMatchObject(unlinked);
    // One parent linked (e.g. a donor conception).
    expect(deNovo(members, [{ childId: 'P', parentId: 'M', role: 'mother' }]).pm6).toMatchObject(unlinked);
  });

  it('cannot assess de novo when a linked parent has no call or is not sequenced', () => {
    const missing = 'No complete parental genotypes — de novo cannot be assessed.';

    expect(deNovo([call('P', 'proband', '0/1'), call('F', 'father', './.'), call('M', 'mother', '0/0')], trioLinks).pm6)
      .toMatchObject({ disposition: 'not_applicable', evidence: missing });
    expect(deNovo([call('P', 'proband', '0/1'), call('M', 'mother', '0/0')], trioLinks).pm6?.evidence).toBe(missing);
  });

  // The backend's de novo mode trusts a parent's reference call from 8 reads
  // (_DE_NOVO_MIN_PARENT_DP): at lower depth the parent may be a missed heterozygote.
  it('offers PM6 for review, not applied, when a reference parent is covered by fewer than 8 reads', () => {
    const { pm6, ps2 } = deNovo(
      [call('P', 'proband', '0/1', 35), call('F', 'father', '0/0', 5), call('M', 'mother', '0/0', 30)],
      trioLinks,
    );

    expect(pm6?.disposition).toBe('consider');
    expect(pm6?.evidence).toBe(
      "Absent in both sequenced parents, but the father's reference call has read depth 5, below the 8 the de novo filter requires, so an inherited allele may have been missed — review before applying.",
    );
    expect(ps2).toBeUndefined();
  });

  it('names every parent whose reference call is too shallow', () => {
    const { pm6 } = deNovo([call('P', 'proband', '0/1'), call('F', 'father', '0/0', 5), call('M', 'mother', '0/0', 6)], trioLinks);

    expect(pm6?.disposition).toBe('consider');
    expect(pm6?.evidence).toMatch(/but the father's and the mother's reference calls have read depth 5 and 6, below the 8/);
  });

  // As in the backend's de novo mode, which wants a heterozygous proband on an autosome, in a
  // PAR and in a daughter: a de novo event changes one copy.
  it('offers PM6 for review, not applied, when the proband is homozygous and both parents are reference', () => {
    const { pm6, ps2 } = deNovo([call('P', 'proband', '1/1'), call('F', 'father', '0/0'), call('M', 'mother', '0/0')], trioLinks);

    expect(pm6?.disposition).toBe('consider');
    expect(pm6?.evidence).toBe(
      'Absent in both sequenced parents, but the proband is homozygous, which a de novo event alone does not explain (a deletion of the other allele, uniparental disomy or a genotyping error) — review before applying.',
    );
    expect(ps2).toBeUndefined();
  });

  it('names both reasons for a homozygous proband with a shallow parent', () => {
    const { pm6 } = deNovo([call('P', 'proband', '1/1'), call('F', 'father', '0/0', 5), call('M', 'mother', '0/0')], trioLinks);

    expect(pm6?.disposition).toBe('consider');
    expect(pm6?.evidence).toMatch(
      /but the proband is homozygous, .*genotyping error\), and the father's reference call has read depth 5, below the 8 .* — review before applying\.$/,
    );
  });

  it('applies PM6 from 8 reads, and when the depth is not reported', () => {
    expect(deNovo([call('P', 'proband', '0/1'), call('F', 'father', '0/0', 8), call('M', 'mother', '0/0', 8)], trioLinks).pm6?.disposition)
      .toBe('applies');
    expect(deNovo([call('P', 'proband', '0/1'), call('F', 'father', '0/0'), call('M', 'mother', '0/0', 40)], trioLinks).pm6?.disposition)
      .toBe('applies');
  });
});

// #621 — a son is hemizygous on chrX/chrY outside the PARs: the parent who passes him that
// chromosome decides PM6/PS2, as in the backend's de novo segregation mode.
describe('evaluateAcmg — de novo in a son where he is hemizygous', () => {
  const son = (gt: string): AcmgFamilyMemberCall => ({ sampleId: 'P', role: 'proband', affected: true, gt, sex: 'male' });
  const father = (gt?: string, dp?: number): AcmgFamilyMemberCall => ({
    sampleId: 'F',
    role: 'father',
    affected: false,
    gt,
    dp,
    sex: 'male',
  });
  const mother = (gt?: string, dp?: number): AcmgFamilyMemberCall => ({
    sampleId: 'M',
    role: 'mother',
    affected: false,
    gt,
    dp,
    sex: 'female',
  });
  const onX: AcmgVariantInput = { effect: 'missense_variant', chr: 'chrX', hemizygous_in_males: true };
  const onY: AcmgVariantInput = { effect: 'missense_variant', chr: 'chrY', hemizygous_in_males: true };
  const pm6 = (variant: AcmgVariantInput, members: AcmgFamilyMemberCall[], parentLinks: AcmgParentLink[] = trioLinks) =>
    find(evaluateAcmg(variant, undefined, undefined, { members, parentLinks }), 'PM6');

  it("follows the son's mother on his X, not a grandmother who shares the role", () => {
    // The maternal grandmother (MGM) is listed first, so a lookup by role finds her.
    const grandmother: AcmgFamilyMemberCall = { sampleId: 'MGM', role: 'mother', affected: false, gt: '0/0', sex: 'female' };
    const suggestion = pm6(onX, [grandmother, son('1'), mother('0/1'), father('0')], [
      ...trioLinks,
      { childId: 'M', parentId: 'MGM', role: 'mother' },
    ]);

    expect(suggestion?.disposition).toBe('not_applicable');
    expect(suggestion?.evidence).toBe('Inherited from the mother, who passes a son his X — not de novo.');
  });

  it('cannot assess de novo when the pedigree does not name the parent who passes the chromosome on', () => {
    const suggestion = pm6(onX, [son('1'), mother('0/0'), father('0')], [{ childId: 'P', parentId: 'F', role: 'father' }]);

    expect(suggestion?.disposition).toBe('not_applicable');
    expect(suggestion?.evidence).toBe(
      "The pedigree does not name the proband's mother, who passes a son his X — de novo cannot be assessed.",
    );
  });

  it('offers PM6 for review when the reference call of the parent who passes the chromosome on is shallow', () => {
    const suggestion = pm6(onX, [son('1'), mother('0/0', 5), father('0', 30)]);

    expect(suggestion?.disposition).toBe('consider');
    expect(suggestion?.evidence).toBe(
      "Absent in the mother, who passes a son his X, but the mother's reference call has read depth 5, below the 8 the de novo filter requires, so an inherited allele may have been missed — review before applying.",
    );
    // The other parent does not pass a son his X: only an ALT call there counts against de novo.
    expect(pm6(onX, [son('1'), mother('0/0', 30), father('0', 3)])?.disposition).toBe('applies');
  });

  it('applies PM6 to a Y variant with a reference father, though the mother has no call', () => {
    const suggestion = pm6(onY, [son('1'), father('0'), mother(undefined)]);
    expect(suggestion?.disposition).toBe('applies');
    expect(suggestion?.evidence).toBe(
      'Absent in the father, who passes a son his Y (de novo) — parentage not molecularly confirmed (use PS2 if confirmed).',
    );
  });

  it('applies PM6 to an X variant with a reference mother, though the father has no call', () => {
    expect(pm6(onX, [son('1'), mother('0/0'), father('./.')])?.disposition).toBe('applies');
    expect(pm6(onX, [son('1/1'), mother('0/0')])?.disposition).toBe('applies');
  });

  it('says a son\'s X variant is inherited from the mother, not from "a parent"', () => {
    const suggestion = pm6(onX, [son('1'), mother('0/1'), father('0')]);
    expect(suggestion?.disposition).toBe('not_applicable');
    expect(suggestion?.evidence).toBe('Inherited from the mother, who passes a son his X — not de novo.');
  });

  it('does not call a variant shared with the father de novo on a son\'s X', () => {
    const suggestion = pm6(onX, [son('1'), mother('0/0'), father('1')]);
    expect(suggestion?.disposition).toBe('not_applicable');
    expect(suggestion?.evidence).toMatch(/^Also called in the father, who does not pass a son his X/);
  });

  it('cannot assess de novo without the parent who passes the chromosome on', () => {
    const suggestion = pm6(onY, [son('1'), mother('0/0')]);
    expect(suggestion?.disposition).toBe('not_applicable');
    expect(suggestion?.evidence).toBe("The father's genotype, who passes a son his Y, is missing — de novo cannot be assessed.");
  });

  it('asks a heterozygous call in a PAR and of a daughter, but takes a son\'s hemizygous call of either ploidy', () => {
    const inPar: AcmgVariantInput = { effect: 'missense_variant', chr: 'chrX', hemizygous_in_males: false };
    const daughter: AcmgFamilyMemberCall = { sampleId: 'P', role: 'proband', affected: true, gt: '1/1', sex: 'female' };

    expect(pm6(inPar, [son('1/1'), mother('0/0'), father('0/0')])?.disposition).toBe('consider');
    expect(pm6(onX, [daughter, mother('0/0'), father('0')])?.disposition).toBe('consider');
    expect(pm6(onX, [son('1/1'), mother('0/0')])?.disposition).toBe('applies');
  });

  it('keeps the trio rule in a PAR, for a daughter and without the backend flag', () => {
    const inPar: AcmgVariantInput = { effect: 'missense_variant', chr: 'chrX', hemizygous_in_males: false };
    expect(pm6(inPar, [son('0/1'), mother('0/0'), father('./.')])?.disposition).toBe('not_applicable');
    const daughter: AcmgFamilyMemberCall = { sampleId: 'P', role: 'proband', affected: true, gt: '0/1', sex: 'female' };
    expect(pm6(onX, [daughter, mother('0/0'), father('./.')])?.disposition).toBe('not_applicable');
    expect(pm6(onX, [daughter, mother('0/0'), father('0')])?.disposition).toBe('applies');
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
