import type { SyntheticEvent } from 'react';

// Helpers shared by the small-variant and structural-variant filter forms.

/** How many of the values are filled in (after trimming): a section's active-filter count. */
export const countNonEmpty = (...values: string[]) => values.filter((value) => value.trim()).length;

// Keeps a click on a quick control in a section's summary bar from also toggling the
// section open or closed.
export const stopSummaryInteraction = (event: SyntheticEvent<HTMLElement>) => {
  event.stopPropagation();
};

/** A frequency filter value as a number for its slider: 0 when unset or negative, at most 10. */
export const normalizePercentValue = (value: string) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return 0;
  return Math.min(10, parsed);
};

/** A frequency filter (a fraction) as a percent label: "0.001" → "0.1%"; empty or 0 → "Any". */
export const formatPercentFilterValue = (value: string) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 'Any';
  return `${(parsed * 100).toFixed(2).replace(/\.?0+$/, '')}%`;
};
