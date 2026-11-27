import type { ClinvarCogaOut } from './apiSchema.generated';

/**
 * A small variant's ClinVar is that of the ClinVar release CoGA loads itself, and only that: the
 * ClinVar of a callset's annotation (a ClinVar file, or VEP's cache) is neither shown nor used.
 * The API serves the shown ALT's values in that release as the variant's `clinvar`,
 * `clinvar_review_status`, `clinvar_stars`, `clinvar_conditions` and `clinvar_variation_id`
 * (none where the release does not hold the allele), and each ALT's value, or why it has none,
 * as `clinvar_coga`.
 *
 * `clinvarClass` is the app's one reading of a ClinVar clinical significance as a class, for
 * everything that turns ClinVar into one: the colour of a small variant's ClinVar chip, card and
 * table row, the small-variant track's marks and the ACMG evaluators' PP5 / BP6.
 *
 * A significance can list several classifications: ClinVar's own aggregate
 * (Pathogenic/Likely_pathogenic), a classification with what it adds (Pathogenic|risk_factor,
 * Pathogenic,_low_penetrance), or those of the several ALTs a Variant Explorer row joins
 * (Uncertain_significance; Likely_pathogenic). It is (likely) pathogenic or (likely) benign only
 * when every classification it lists is. Classifications that disagree are a conflict, as
 * ClinVar's own "Conflicting classifications of pathogenicity", which contains "pathogenic" but
 * is neither pathogenic nor benign; neither suggests PP5 or BP6. A conflict that lists a (likely)
 * pathogenic classification is 'conflicting_pathogenic', to look at; ClinVar's aggregate conflict
 * names none. The significances that classify nothing (risk factor, drug response, low
 * penetrance, not provided, …) take no part.
 */
export type ClinvarClass = 'pathogenic' | 'benign' | 'conflicting_pathogenic';

type Classification = 'pathogenic' | 'uncertain' | 'benign' | 'conflicting';

// ClinVar's separators between the classifications a significance lists.
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
 * ClinVar named by its release, where a record must say which release a value is of (the signed
 * record, the drift, the reclassifications list): "ClinVar 2026-11-22", or "ClinVar" without one.
 */
export const clinvarReleaseLabel = (release?: string | null): string =>
  release ? `ClinVar ${release}` : 'ClinVar';

/** ClinVar's review stars, as ClinVar draws them: "★★☆☆" for 2 of 4. */
export const formatClinvarStars = (stars?: number | null): string | null =>
  typeof stars === 'number' && stars >= 0 && stars <= 4 ? `${'★'.repeat(stars)}${'☆'.repeat(4 - stars)}` : null;

// What the release says of an allele it gives no classification.
const STATUS_TEXT: Record<Exclude<ClinvarCogaOut['status'], 'in_release'>, string> = {
  not_in_release: 'not in this release',
  no_release: 'no release loaded',
  not_available: 'not available for this assembly',
};

/** One ALT's value in CoGA's ClinVar release as a phrase: "Likely pathogenic ★★★☆", or why it has none. */
export const clinvarValueText = (value: ClinvarCogaOut): string => {
  if (value.status !== 'in_release') return STATUS_TEXT[value.status];
  if (value.included_only || !value.classification) return 'only as part of a combination';
  const stars = formatClinvarStars(value.stars);
  return `${value.classification.replace(/_/g, ' ')}${stars ? ` ${stars}` : ''}`;
};
