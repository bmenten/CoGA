// The app's one reading of a ClinVar clinical significance as a class. The ACMG evaluators
// matched "pathogenic" as a substring, so "Conflicting classifications of pathogenicity"
// suggested PP5 and argued against BP6; a value that lists classifications that disagree was
// read as pathogenic and tinted red.

import { describe, expect, it } from 'vitest';

import type { ClinvarCogaOut } from '../apiSchema.generated';
import { clinvarClass, clinvarReleaseLabel, clinvarValueText } from '../clinvar';

describe('clinvarClass', () => {
  it.each([
    'Pathogenic',
    'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic,_low_penetrance',
    'Pathogenic|risk_factor',
    'pathogenic&likely_pathogenic',
  ])('reads %s as pathogenic', (clinvar) => {
    expect(clinvarClass(clinvar)).toBe('pathogenic');
  });

  it.each(['Benign', 'Likely_benign', 'Benign/Likely_benign', 'likely benign', 'benign&likely_benign'])(
    'reads %s as benign',
    (clinvar) => {
      expect(clinvarClass(clinvar)).toBe('benign');
    },
  );

  it.each([
    'uncertain_significance&likely_pathogenic',
    'benign&pathogenic',
    'likely_benign,pathogenic/likely_pathogenic',
    'conflicting_interpretations_of_pathogenicity&pathogenic',
  ])('reads %s, a conflict that lists a (likely) pathogenic classification, as conflicting_pathogenic', (clinvar) => {
    expect(clinvarClass(clinvar)).toBe('conflicting_pathogenic');
  });

  it.each([
    'Conflicting_classifications_of_pathogenicity',
    'Conflicting interpretations of pathogenicity',
    'Conflicting_classifications_of_pathogenicity|risk_factor',
    'benign&uncertain_significance',
    'likely_benign&conflicting_interpretations_of_pathogenicity',
  ])('reads a conflict that names no (likely) pathogenic classification (%s) as no class', (clinvar) => {
    expect(clinvarClass(clinvar)).toBeUndefined();
  });

  it.each(['Uncertain_significance', 'risk_factor', 'drug_response', 'not_provided', '-', '', null, undefined])(
    'reads %s as no class',
    (clinvar) => {
      expect(clinvarClass(clinvar)).toBeUndefined();
    },
  );
});

describe('clinvarReleaseLabel', () => {
  it('names ClinVar by its release, and plainly without one', () => {
    expect(clinvarReleaseLabel('2026-11-22')).toBe('ClinVar 2026-11-22');
    expect(clinvarReleaseLabel(null)).toBe('ClinVar');
  });
});

describe('clinvarValueText', () => {
  const value = (overrides: Partial<ClinvarCogaOut> = {}): ClinvarCogaOut => ({
    alt: 'A',
    status: 'in_release',
    release: '2026-11-22',
    classification: 'Likely_pathogenic',
    review_status: 'reviewed by expert panel',
    stars: 3,
    conditions: ['Synthetic disorder 1'],
    variation_id: 900002,
    included_only: false,
    ...overrides,
  });

  it('gives the classification with its stars', () => {
    expect(clinvarValueText(value())).toBe('Likely pathogenic ★★★☆');
    expect(clinvarValueText(value({ stars: null }))).toBe('Likely pathogenic');
  });

  it('says why an allele has no classification', () => {
    expect(clinvarValueText(value({ status: 'not_in_release' }))).toBe('not in this release');
    expect(clinvarValueText(value({ status: 'no_release' }))).toBe('no release loaded');
    expect(clinvarValueText(value({ status: 'not_available' }))).toBe('not available for this assembly');
    expect(clinvarValueText(value({ classification: null, included_only: true }))).toBe('only as part of a combination');
  });
});
