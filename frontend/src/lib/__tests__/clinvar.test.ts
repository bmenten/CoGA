// The app's one reading of a ClinVar record as a class. The ACMG evaluators matched
// "pathogenic" as a substring, so "Conflicting classifications of pathogenicity" suggested PP5
// and argued against BP6; a value that lists classifications that disagree was read as pathogenic
// and tinted red.

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
    'pathogenic&likely_pathogenic',
  ])('reads %s as pathogenic', (clinvar) => {
    expect(clinvarClass({ clinvar })).toBe('pathogenic');
  });

  it.each(['Benign', 'Likely_benign', 'Benign/Likely_benign', 'likely benign', 'benign&likely_benign'])(
    'reads %s as benign',
    (clinvar) => {
      expect(clinvarClass({ clinvar })).toBe('benign');
    },
  );

  it.each([
    'uncertain_significance&likely_pathogenic',
    'benign&pathogenic',
    'likely_benign,pathogenic/likely_pathogenic',
    'conflicting_interpretations_of_pathogenicity&pathogenic',
  ])('reads %s, a conflict that lists a (likely) pathogenic classification, as conflicting_pathogenic', (clinvar) => {
    expect(clinvarClass({ clinvar })).toBe('conflicting_pathogenic');
  });

  it.each([
    'Conflicting_classifications_of_pathogenicity',
    'Conflicting interpretations of pathogenicity',
    'Conflicting_classifications_of_pathogenicity|risk_factor',
    'benign&uncertain_significance',
    'likely_benign&conflicting_interpretations_of_pathogenicity',
  ])('reads a conflict that names no (likely) pathogenic classification (%s) as no class', (clinvar) => {
    expect(clinvarClass({ clinvar })).toBeUndefined();
  });

  it.each(['Uncertain_significance', 'risk_factor', 'drug_response', 'not_provided', '-', '', null, undefined])(
    'reads %s as no class',
    (clinvar) => {
      expect(clinvarClass({ clinvar })).toBeUndefined();
    },
  );

  // ClinVar's aggregate conflict, with the classifications its submitters gave (CLNSIGCONF).
  it.each([
    [['Pathogenic (2)', 'Uncertain significance (1)'], 'conflicting_pathogenic'],
    [['Likely pathogenic (1)', 'Likely benign (3)'], 'conflicting_pathogenic'],
    [['Uncertain significance (2)', 'Likely benign (1)'], undefined],
    [[], undefined],
  ])('reads a conflict whose submitters gave %j as %s', (conflicts, expected) => {
    expect(
      clinvarClass({ clinvar: 'Conflicting_classifications_of_pathogenicity', clinvar_conflicts: conflicts }),
    ).toBe(expected);
  });
});
