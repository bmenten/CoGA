import type { ApiRepeatStatus } from './apiTypes';
import type { RepeatCountUnitWarningOut } from './apiSchema.generated';

/** A repeat status as the repeat page names it: on its chips, and in a count-unit warning. */
export const repeatStatusLabel = (status: ApiRepeatStatus): string => {
  switch (status) {
    case 'review':
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
