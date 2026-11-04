import type { ApiRepeatReviewReason, ApiRepeatStatus } from './apiTypes';
import type { RepeatCountUnitWarningOut } from './apiSchema.generated';

/**
 * A repeat status as the repeat page names it: on its chips, and in a count-unit warning. A
 * review says why, by the reason the API gives with it (CLIN-14): it said "outside catalogued
 * ranges" for a man's two chrX alleles too.
 */
export const repeatStatusLabel = (status: ApiRepeatStatus, reviewReason?: ApiRepeatReviewReason | null): string => {
  switch (status) {
    case 'review':
      // A man's two different chrX alleles at a locus where he carries one.
      if (reviewReason === 'male_x_alleles') return 'Review: two chrX alleles in a male';
      // A count outside every catalogued range at a contraction locus: the catalog
      // cannot classify it, so it is surfaced rather than read as normal (#535).
      return 'Review: outside catalogued ranges';
    case 'not_assessed':
      // A locus without thresholds: no catalogue row names the call's TRID, or the row
      // states none. Read as normal before, so an expansion there could not be seen (CLIN-26).
      // A count inside the benign range the row states there reads normal (CLIN-36).
      return 'Not assessed';
    case 'intermediate':
      return 'Grey zone';
    case 'premutation':
      // FMR1 55-200: not affected, but the allele can expand to a full mutation in a child.
      return 'Premutation';
    case 'pathogenic':
      return 'Pathogenic';
    case 'unknown':
      return 'Unknown';
    default:
      return 'Normal';
  }
};

/**
 * QA's count correction at a locus in words (repeat_count_units.py, CLIN-57): "TRGT's count −1",
 * "TRGT's count +2"; null without one (0, or a record from before corrections).
 */
export const describeCountOffset = (offset: number | null | undefined): string | null =>
  offset ? `TRGT’s count ${offset > 0 ? '+' : '−'}${Math.abs(offset)}` : null;

/**
 * An allele's or a call's counts as CoGA classifies them, with TRGT's own beside them where QA's
 * correction changed them: "17 / 40 (TRGT 18 / 41)"; "17 / 40" without a correction. A no-call
 * allele is left out of both; null when no allele has a count.
 */
export const formatCorrectedCounts = (
  counts: Array<number | null | undefined>,
  trgtCounts: Array<number | null | undefined>,
): string | null => {
  const shown = counts.filter((count): count is number => count != null);
  if (!shown.length) return null;
  const trgt = trgtCounts.filter((count): count is number => count != null);
  const corrected = trgt.length === shown.length && trgt.some((count, index) => count !== shown[index]);
  return corrected ? `${shown.join(' / ')} (TRGT ${trgt.join(' / ')})` : shown.join(' / ');
};

/** The mark of a count near a boundary while TRGT's counting units are not validated. */
export const COUNT_UNIT_MARK = 'Near a boundary';

/** Why the mark matters, said once for every count it marks at a locus. */
export const COUNT_UNITS_NOT_VALIDATED =
  "TRGT's counting units are not yet validated for this locus, so its count may differ from the clinical repeat number by a few repeats.";

/**
 * One count near a boundary: "53 is within 3 repeats of 55 (Premutation)", or of the nearest
 * count on each side that reads another status, "11 is within 3 repeats of 10 (Normal) and
 * 12 (Pathogenic)". The backend finds them with the classifier (repeat_count_units.py).
 */
export const describeCountUnitWarning = (warning: RepeatCountUnitWarningOut): string => {
  const boundaries = warning.boundaries
    .map((boundary) => `${boundary.repeat_count} (${repeatStatusLabel(boundary.status)})`)
    .join(' and ');
  return `${warning.repeat_count} is within ${warning.margin} repeats of ${boundaries}`;
};

/**
 * The tooltip of a call's or a locus's mark: each count near a boundary, why, and what is
 * known of TRGT's count at the gene (FMR1, HTT, POLG).
 */
export const countUnitWarningText = (
  warnings: RepeatCountUnitWarningOut[],
  note?: string | null,
): string => {
  const counts = warnings.map(describeCountUnitWarning).join('; ');
  return [`${COUNT_UNIT_MARK}: ${counts}.`, COUNT_UNITS_NOT_VALIDATED, note ? `Known: ${note}` : null]
    .filter((part): part is string => Boolean(part))
    .join(' ');
};
