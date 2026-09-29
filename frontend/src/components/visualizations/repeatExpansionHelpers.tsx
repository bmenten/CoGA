import type { ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import { cssVar } from '../../lib/colors';
import VizTooltip from './VizTooltip';

// Repeat-expansion status palette. Each value is a getter so cssVar() (a
// getComputedStyle flush) is only resolved when the palette is read. Shared by
// RepeatExpansionTrack and GenomeRepeatExpansionTrack.
export const STATUS_COLORS = {
  normal: () => cssVar('--color-repeat-normal'),
  review: () => cssVar('--color-repeat-review'),
  intermediate: () => cssVar('--color-repeat-intermediate'),
  pathogenic: () => cssVar('--color-repeat-pathogenic'),
  unknown: () => cssVar('--color-repeat-unknown'),
};

// The statuses a track draws in their own colour; any other is drawn, and counted, as unknown.
const CALLED_STATUSES = new Set<string>(['normal', 'review', 'intermediate', 'pathogenic']);
// Pathogenic loci named in a track's accessible name before the rest are only counted.
const NAMED_PATHOGENIC_LOCI = 3;

/**
 * The drawn loci in words, for a repeat track's accessible name (#529): how many, the
 * pathogenic ones by name, and how many are intermediate, need review or are unknown —
 * "40, 1 pathogenic (FMR1), 2 intermediate".
 */
export const describeRepeatLoci = (
  items: Pick<ApiRepeatExpansionTrackItem, 'status' | 'display_name' | 'gene' | 'locus_id'>[],
): string => {
  const countOf = (status: string) => items.filter((item) => item.status === status).length;
  const pathogenic = items
    .filter((item) => item.status === 'pathogenic')
    .map((item) => item.display_name || item.gene || item.locus_id);
  const unnamed = pathogenic.length - NAMED_PATHOGENIC_LOCI;
  const named = pathogenic.slice(0, NAMED_PATHOGENIC_LOCI).join(', ');
  const pathogenicNames = unnamed > 0 ? `${named} +${unnamed.toLocaleString()} more` : named;
  const intermediate = countOf('intermediate');
  const review = countOf('review');
  const unknown = items.filter((item) => !CALLED_STATUSES.has(item.status)).length;
  const parts = [
    pathogenic.length ? `${pathogenic.length.toLocaleString()} pathogenic (${pathogenicNames})` : null,
    intermediate ? `${intermediate.toLocaleString()} intermediate` : null,
    review ? `${review.toLocaleString()} needing review` : null,
    unknown ? `${unknown.toLocaleString()} unknown` : null,
  ].filter((part): part is string => part !== null);
  return `${items.length.toLocaleString()}, ${parts.length ? parts.join(', ') : 'all normal'}`;
};

type RepeatLocusTooltipItem = {
  display_name: string;
  disease: string;
  allele_repeat_counts: Array<number | string>;
};

export const RepeatLocusTooltip = ({
  x,
  y,
  item,
}: {
  x: number;
  y: number;
  item: RepeatLocusTooltipItem;
}) => (
  <VizTooltip x={x} y={y}>
    <div>{item.display_name}</div>
    <div>{item.disease}</div>
    <div>{item.allele_repeat_counts.join(' / ') || 'no call'} repeats</div>
  </VizTooltip>
);
