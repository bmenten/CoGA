/**
 * The app's one reading of a ClinVar clinical significance as pathogenic or benign, for
 * everything that turns ClinVar into a class: the small-variant track's marks (#529) and
 * the ACMG evaluators' PP5 / BP6.
 *
 * "Conflicting classifications of pathogenicity" (before 2024 "conflicting interpretations")
 * contains "pathogenic", but it is neither pathogenic nor benign, as in the backend's
 * prioritisation (variant_prioritization.pathogenicity_score) and its mtDNA status. Read as a
 * substring it suggested PP5 and argued against BP6. The significances that decide nothing
 * (uncertain, risk factor, drug response, not provided, …) are neither too.
 */
export type ClinvarClass = 'pathogenic' | 'benign';

const normalizeClinvar = (clinvar?: string | null): string =>
  (clinvar || '')
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

/** "pathogenic" / "benign" for a decisive ClinVar class, else undefined. */
export const clinvarClass = (clinvar?: string | null): ClinvarClass | undefined => {
  const value = normalizeClinvar(clinvar);
  if (!value) return undefined;
  if (value.includes('conflict')) return undefined;
  if (value.includes('pathogenic')) return 'pathogenic';
  if (value.includes('benign')) return 'benign';
  return undefined;
};
