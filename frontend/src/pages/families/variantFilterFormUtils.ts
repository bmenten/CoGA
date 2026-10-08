import type { SyntheticEvent } from 'react';
import { parseGeneOrRegionInput } from '../../lib/variantSearch';

// Helpers shared by the small-variant and structural-variant searches and filter forms.

/**
 * The location filters as both family searches send them: a locus as written and as the
 * region or gene it reads as; without a locus, the gene, chromosome, start and end as set.
 */
export const setLocationParams = (
  params: URLSearchParams,
  filters: { locus: string; gene: string; chr: string; start: string; end: string },
) => {
  if (filters.locus) {
    params.set('locus', filters.locus);
    const parsedLocus = parseGeneOrRegionInput(filters.locus);
    if (parsedLocus?.kind === 'region') {
      params.set('chr', parsedLocus.chr);
      params.set('start', parsedLocus.start);
      params.set('end', parsedLocus.end);
    } else if (parsedLocus?.kind === 'gene') {
      params.set('gene', parsedLocus.gene);
    }
    return;
  }
  if (filters.gene) params.set('gene', filters.gene);
  if (filters.chr) params.set('chr', filters.chr);
  if (filters.start) params.set('start', filters.start);
  if (filters.end) params.set('end', filters.end);
};

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
