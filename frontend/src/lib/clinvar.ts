/**
 * The app's one reading of a ClinVar clinical significance as a class, for everything that
 * turns ClinVar into one: the colour of a small variant's ClinVar chip, card and table row, the
 * small-variant track's marks and the ACMG evaluators' PP5 / BP6.
 *
 * A significance can list several classifications: ClinVar's own aggregate
 * (Pathogenic/Likely_pathogenic), a classification with what it adds (Pathogenic|risk_factor,
 * Pathogenic,_low_penetrance), or those of several ClinVar records the annotation joins
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

/**
 * The name a ClinVar classification is shown under. One read from VEP's cache
 * (`clinvar_from_vep_cache`: the callset was annotated without a ClinVar file) is the known
 * variant's, the dbSNP rsID's: this allele's where the cache holds one per allele, else every
 * allele's of the rsID, and the annotation does not say which. Every view names it as the
 * cache's (`VEP_CACHE_CLINVAR_NOTE` says why on hover).
 */
export const clinvarLabel = (fromVepCache?: boolean | null): string =>
  fromVepCache ? 'ClinVar (VEP cache)' : 'ClinVar';

/**
 * The names of the two ClinVar values a small variant shows (the owner's decision B7): the
 * annotation's, of the release of the ClinVar file the pipeline annotated, and CoGA's own release.
 * A value read from VEP's cache keeps its own name.
 */
export const clinvarAnnotationLabel = (fromVepCache?: boolean | null, release?: string | null): string =>
  fromVepCache ? clinvarLabel(true) : release ? `ClinVar (annotation, ${release})` : 'ClinVar (annotation)';

export const clinvarCogaLabel = (release?: string | null): string =>
  release ? `ClinVar (CoGA, ${release})` : 'ClinVar (CoGA)';

// A classification's terms as the import stores them (clinvar_terms): lower case, spaces for
// underscores, split on ClinVar's separators.
const termsOf = (classification?: string | null): Set<string> =>
  new Set(
    (classification || '')
      .split(TERM_SEPARATORS)
      .map((term) => term.toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ').trim())
      .filter(Boolean),
  );

/** One ALT's value in CoGA's own ClinVar release, as the API serves it. */
export interface ClinvarCogaValue {
  alt: string;
  status: 'in_release' | 'not_in_release' | 'no_release' | 'not_available';
  classification?: string | null;
  stars?: number | null;
}

/**
 * Whether CoGA's own release classifies the allele otherwise than its annotation: another
 * classification (one side classifying it and the other not included), or, both classifying it
 * alike, other stars (where the annotation's stars are known). False when CoGA reads no release
 * for it.
 */
export const clinvarCogaChanged = (
  annotated: { clinvar?: string | null; clinvar_stars?: number | null },
  coga?: ClinvarCogaValue | null,
): boolean => {
  if (!coga || coga.status === 'no_release' || coga.status === 'not_available') return false;
  const ours = termsOf(annotated.clinvar);
  const theirs = termsOf(coga.classification);
  if (ours.size !== theirs.size || [...ours].some((term) => !theirs.has(term))) return true;
  // Stars are compared where the annotation's are known (a family's variant; not an Explorer row).
  return ours.size > 0 && annotated.clinvar_stars !== undefined && (annotated.clinvar_stars ?? null) !== (coga.stars ?? null);
};

/** The value of a small variant's shown ALT in CoGA's release: of the allele its transcript
 * fields are of (a multi-allelic record's ``alt_allele``), else of its one ALT. */
export const clinvarCogaOf = <T extends ClinvarCogaValue>(variant: {
  alt?: string | null;
  alt_allele?: string | null;
  clinvar_coga?: T[] | null;
}): T | undefined => {
  const values = variant.clinvar_coga ?? [];
  const shown = variant.alt_allele ?? variant.alt;
  return values.find((value) => value.alt === shown) ?? (values.length === 1 ? values[0] : undefined);
};

export const VEP_CACHE_CLINVAR_NOTE =
  "From VEP's cache (CLIN_SIG), as no ClinVar file was annotated: the classification of the " +
  'known variant (dbSNP rsID), which may be that of another allele at this position. Check it in ClinVar.';
