/**
 * The app's one reading of a ClinVar clinical significance as a class, for everything that
 * turns ClinVar into one: the colour of a small variant's ClinVar chip, card and table row, the
 * small-variant track's marks and the ACMG evaluators' PP5 / BP6.
 *
 * A significance can list several classifications: ClinVar's own aggregate
 * (Pathogenic/Likely_pathogenic), a classification with what it adds (Pathogenic|risk_factor,
 * Pathogenic,_low_penetrance), or the VEP cache's list of every ClinVar record at the position
 * (uncertain_significance&likely_pathogenic). It is (likely) pathogenic or (likely) benign only
 * when every classification it lists is. Classifications that disagree are a conflict, as
 * ClinVar's own "Conflicting classifications of pathogenicity" (before 2024 "conflicting
 * interpretations"), which contains "pathogenic" but is neither pathogenic nor benign; neither
 * suggests PP5 or BP6. A conflict that lists a (likely) pathogenic classification is
 * 'conflicting_pathogenic', to look at; ClinVar's aggregate conflict names none, as only the
 * aggregate is imported. The significances that classify nothing (risk factor, drug response,
 * low penetrance, not provided, …) take no part.
 */
export type ClinvarClass = 'pathogenic' | 'benign' | 'conflicting_pathogenic';

type Classification = 'pathogenic' | 'uncertain' | 'benign' | 'conflicting';

// As the import splits a significance into the terms it stores (clinvar_terms).
const TERM_SEPARATORS = /[,;&|/]+/;

const classificationOf = (term: string): Classification | undefined => {
  const value = term.toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (value.includes('conflict')) return 'conflicting';
  if (value.includes('pathogenic')) return 'pathogenic';
  if (value.includes('benign')) return 'benign';
  if (value === 'uncertain significance') return 'uncertain';
  return undefined;
};

/** ClinVar's class, or undefined for a significance that is no class. */
export const clinvarClass = (clinvar?: string | null): ClinvarClass | undefined => {
  const listed = new Set((clinvar || '').split(TERM_SEPARATORS).flatMap((term) => classificationOf(term) ?? []));
  if (listed.size === 1 && listed.has('pathogenic')) return 'pathogenic';
  if (listed.size === 1 && listed.has('benign')) return 'benign';
  return listed.has('pathogenic') ? 'conflicting_pathogenic' : undefined;
};
