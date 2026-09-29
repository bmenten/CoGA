import { describe, expect, it } from 'vitest';

import { evaluateMitoAcmg } from '../evaluateMito';
import type { AcmgVariantInput } from '../evaluate';
import type { AcmgCriterionCode, AcmgMitoContext, AcmgSuggestion } from '../types';

function find(suggestions: AcmgSuggestion[], code: AcmgCriterionCode) {
  return suggestions.find((s) => s.code === code);
}
function codes(suggestions: AcmgSuggestion[]): AcmgCriterionCode[] {
  return suggestions.map((s) => s.code);
}

const baseVariant: AcmgVariantInput = { effect: 'missense_variant', gene: 'MT-ND1' };

describe('evaluateMitoAcmg', () => {
  it('applies PVS1 for a LOF variant in a protein-coding mt gene', () => {
    const variant: AcmgVariantInput = { effect: 'stop_gained', gene: 'MT-ND5' };
    const mito: AcmgMitoContext = { category: 'protein coding' };
    expect(find(evaluateMitoAcmg(variant, mito), 'PVS1')?.disposition).toBe('applies');
  });

  it('rules out PVS1 for a tRNA/rRNA/control locus', () => {
    const variant: AcmgVariantInput = { effect: 'stop_gained', gene: 'MT-TL1' };
    expect(find(evaluateMitoAcmg(variant, { category: 'tRNA' }), 'PVS1')?.disposition).toBe(
      'not_applicable',
    );
    expect(find(evaluateMitoAcmg(variant, { category: 'rRNA' }), 'PVS1')?.disposition).toBe(
      'not_applicable',
    );
  });

  it('uses mt-specific frequency bands (BA1 at 0.5%)', () => {
    expect(
      find(evaluateMitoAcmg({ ...baseVariant, gnomad_af: 0.01 }, { category: 'protein coding' }), 'BA1')
        ?.disposition,
    ).toBe('applies');
    // 0.05% sits in the BS1 band (≥ 2e-4, < 5e-3).
    expect(
      find(evaluateMitoAcmg({ ...baseVariant, gnomad_af: 5e-4 }, { category: 'protein coding' }), 'BS1')
        ?.disposition,
    ).toBe('applies');
    // Absent → PM2 supporting.
    expect(
      find(evaluateMitoAcmg(baseVariant, { category: 'protein coding' }), 'PM2')?.disposition,
    ).toBe('applies');
  });

  it('treats a MITOMAP polymorphism as BS1 (haplogroup marker / common)', () => {
    const mito: AcmgMitoContext = { category: 'protein coding', clinicalSignificance: 'polymorphism' };
    const suggestions = evaluateMitoAcmg(baseVariant, mito);
    expect(find(suggestions, 'BS1')?.disposition).toBe('applies');
    expect(find(suggestions, 'BP6')?.disposition).toBe('applies');
  });

  it('maps MITOMAP/ClinVar status to PP5 / BP6', () => {
    expect(
      find(evaluateMitoAcmg(baseVariant, { clinicalSignificance: 'pathogenic' }), 'PP5')?.disposition,
    ).toBe('applies');
    expect(
      find(evaluateMitoAcmg(baseVariant, { clinicalSignificance: 'benign' }), 'BP6')?.disposition,
    ).toBe('applies');
    // The backend's likely classes (MitoDNAVariantAnnotationOut.clinical_significance).
    expect(
      find(evaluateMitoAcmg(baseVariant, { clinicalSignificance: 'likely_pathogenic' }), 'PP5')?.disposition,
    ).toBe('applies');
    expect(
      find(evaluateMitoAcmg(baseVariant, { clinicalSignificance: 'likely_benign' }), 'BP6')?.disposition,
    ).toBe('applies');
  });

  // "Conflicting classifications of pathogenicity" contains "pathogenic": read as a substring,
  // it applied PP5 and argued against BP6. The backend reads it as 'uncertain'.
  it.each(['Conflicting_classifications_of_pathogenicity', 'uncertain'])(
    'suggests neither PP5 nor BP6 for a conflicting or uncertain status (%s)',
    (clinicalSignificance) => {
      const suggestions = evaluateMitoAcmg(baseVariant, { category: 'protein coding', clinicalSignificance });

      expect(find(suggestions, 'PP5')).toBeUndefined();
      expect(find(suggestions, 'BP6')).toBeUndefined();
    },
  );

  it('marks PP3/BP4 not applicable (no mt in-silico predictor)', () => {
    const suggestions = evaluateMitoAcmg(baseVariant, { category: 'protein coding' });
    expect(find(suggestions, 'PP3')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'BP4')?.disposition).toBe('not_applicable');
  });

  it('rules out de novo (maternal inheritance) and nuclear-only criteria', () => {
    const suggestions = evaluateMitoAcmg(baseVariant, { category: 'protein coding' });
    expect(find(suggestions, 'PS2')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PM6')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PP2')?.disposition).toBe('not_applicable');
    expect(find(suggestions, 'PM3')?.disposition).toBe('not_applicable');
  });

  it('suggests PM1 (consider) for a tRNA locus', () => {
    expect(find(evaluateMitoAcmg(baseVariant, { category: 'tRNA' }), 'PM1')?.disposition).toBe(
      'consider',
    );
  });

  it('derives PP1 from maternal segregation across affected carriers', () => {
    const mito: AcmgMitoContext = {
      category: 'protein coding',
      maternalTransmission: 'maternal_shared',
      calls: [
        { sampleId: 'P', role: 'proband', affected: true, zygosity: 'heteroplasmic', alleleFraction: 0.6 },
        { sampleId: 'M', role: 'mother', affected: true, zygosity: 'heteroplasmic', alleleFraction: 0.3 },
      ],
    };
    expect(find(evaluateMitoAcmg(baseVariant, mito), 'PP1')?.disposition).toBe('consider');
  });

  it('derives BS4 when an affected maternal relative lacks the variant', () => {
    const mito: AcmgMitoContext = {
      category: 'protein coding',
      maternalTransmission: 'maternal_shared',
      calls: [
        { sampleId: 'P', role: 'proband', affected: true, zygosity: 'heteroplasmic' },
        { sampleId: 'S', role: 'sibling', affected: true, zygosity: 'reference' },
      ],
    };
    expect(find(evaluateMitoAcmg(baseVariant, mito), 'BS4')?.disposition).toBe('consider');
  });

  it('applies PP4 on HPO overlap, noting proband heteroplasmy', () => {
    const mito: AcmgMitoContext = {
      category: 'protein coding',
      calls: [{ sampleId: 'P', role: 'proband', affected: true, zygosity: 'homoplasmic', alleleFraction: 0.98 }],
    };
    const suggestions = evaluateMitoAcmg(
      baseVariant,
      mito,
      { geneHpoIds: ['HP:0001250'] },
      { probandHpoIds: ['HP:0001250'] },
    );
    const pp4 = find(suggestions, 'PP4');
    expect(pp4?.disposition).toBe('applies');
    expect(pp4?.evidence).toContain('heteroplasmy');
    expect(codes(suggestions)).toContain('PP4');
  });

  // #609 — without the gene profile or the HPO terms the overlap is unknown, not absent.
  it('offers PP4 for review when a lookup it needs failed', () => {
    const mito: AcmgMitoContext = { category: 'protein coding', calls: [] };
    const pp4 = find(
      evaluateMitoAcmg(baseVariant, mito, { geneHpoIds: ['HP:0001250'] }, { probandHpoUnavailable: true }),
      'PP4',
    );

    expect(pp4?.disposition).toBe('consider');
    expect(pp4?.evidence).toBe(
      "Not assessed: the family's HPO terms could not be loaded. Review the phenotype match by hand.",
    );
  });
});
