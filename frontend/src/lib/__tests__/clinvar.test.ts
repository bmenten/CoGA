// The app's one reading of a ClinVar clinical significance as pathogenic or benign. The
// ACMG evaluators matched "pathogenic" as a substring, so "Conflicting classifications of
// pathogenicity" suggested PP5 and argued against BP6.

import { describe, expect, it } from 'vitest';

import { clinvarClass } from '../clinvar';

describe('clinvarClass', () => {
  it.each([
    'Pathogenic',
    'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic,_low_penetrance',
    'Pathogenic|risk_factor',
  ])('reads %s as pathogenic', (clinvar) => {
    expect(clinvarClass(clinvar)).toBe('pathogenic');
  });

  it.each(['Benign', 'Likely_benign', 'Benign/Likely_benign', 'likely benign'])('reads %s as benign', (clinvar) => {
    expect(clinvarClass(clinvar)).toBe('benign');
  });

  it.each([
    'Conflicting_classifications_of_pathogenicity',
    'Conflicting interpretations of pathogenicity',
    'Conflicting_classifications_of_pathogenicity|risk_factor',
  ])('reads a conflicting record (%s) as neither pathogenic nor benign', (clinvar) => {
    expect(clinvarClass(clinvar)).toBeUndefined();
  });

  it.each(['Uncertain_significance', 'risk_factor', 'drug_response', 'not_provided', '', null, undefined])(
    'reads %s as neither',
    (clinvar) => {
      expect(clinvarClass(clinvar)).toBeUndefined();
    },
  );
});
