import type { ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import type { RepeatCountUnitWarningOut } from '../../lib/apiSchema.generated';
import { cssVar } from '../../lib/colors';
import {
  COUNT_UNIT_MARK,
  COUNT_UNITS_NOT_VALIDATED,
  describeCountUnitWarning,
} from '../../lib/repeatStatus';
import VizTooltip from './VizTooltip';

/**
 * The repeat tracks' colours: one per status, and the track's grid line. Each is a cssVar()
 * (a getComputedStyle flush), so a track reads them once, in a memo, and never per locus in
 * its render. Shared by RepeatExpansionTrack and GenomeRepeatExpansionTrack.
 */
export const repeatTrackColors = () => ({
  normal: cssVar('--color-repeat-normal'),
  review: cssVar('--color-repeat-review'),
  not_assessed: cssVar('--color-repeat-not-assessed'),
  intermediate: cssVar('--color-repeat-intermediate'),
  premutation: cssVar('--color-repeat-premutation'),
  pathogenic: cssVar('--color-repeat-pathogenic'),
  unknown: cssVar('--color-repeat-unknown'),
  grid: cssVar('--color-grid'),
});

// The statuses a track draws in their own colour; any other is drawn, and counted, as unknown.
const CALLED_STATUSES = new Set<string>([
  'normal',
  'review',
  'not_assessed',
  'intermediate',
  'premutation',
  'pathogenic',
]);
// Pathogenic loci named in a track's accessible name before the rest are only counted.
const NAMED_PATHOGENIC_LOCI = 3;

/**
 * The drawn loci in words, for a repeat track's accessible name (#529): how many, the
 * pathogenic ones by name, and how many are premutations, intermediate, not assessed (a
 * locus without thresholds), need review or are unknown — "40, 1 pathogenic (FMR1),
 * 2 intermediate" — and how many have a count near a boundary while TRGT's counting units
 * at the locus are not validated.
 */
export const describeRepeatLoci = (
  items: Pick<
    ApiRepeatExpansionTrackItem,
    'status' | 'display_name' | 'gene' | 'locus_id' | 'count_unit_warnings'
  >[],
): string => {
  const countOf = (status: string) => items.filter((item) => item.status === status).length;
  const pathogenic = items
    .filter((item) => item.status === 'pathogenic')
    .map((item) => item.display_name || item.gene || item.locus_id);
  const unnamed = pathogenic.length - NAMED_PATHOGENIC_LOCI;
  const named = pathogenic.slice(0, NAMED_PATHOGENIC_LOCI).join(', ');
  const pathogenicNames = unnamed > 0 ? `${named} +${unnamed.toLocaleString()} more` : named;
  const premutation = countOf('premutation');
  const intermediate = countOf('intermediate');
  const notAssessed = countOf('not_assessed');
  const review = countOf('review');
  const unknown = items.filter((item) => !CALLED_STATUSES.has(item.status)).length;
  const parts = [
    pathogenic.length ? `${pathogenic.length.toLocaleString()} pathogenic (${pathogenicNames})` : null,
    premutation ? `${premutation.toLocaleString()} premutation` : null,
    intermediate ? `${intermediate.toLocaleString()} intermediate` : null,
    notAssessed ? `${notAssessed.toLocaleString()} not assessed` : null,
    review ? `${review.toLocaleString()} needing review` : null,
    unknown ? `${unknown.toLocaleString()} unknown` : null,
  ].filter((part): part is string => part !== null);
  const nearBoundary = items.filter((item) => (item.count_unit_warnings ?? []).length > 0).length;
  const near = nearBoundary ? `, ${nearBoundary.toLocaleString()} near a boundary` : '';
  return `${items.length.toLocaleString()}, ${parts.length ? parts.join(', ') : 'all normal'}${near}`;
};

type RepeatLocusTooltipItem = {
  display_name: string;
  disease: string;
  allele_repeat_counts: Array<number | string>;
  // A count near a boundary while TRGT's counting units at the locus are not validated,
  // and what is known of TRGT's count at the gene.
  count_unit_warnings?: RepeatCountUnitWarningOut[];
  count_units_note?: string | null;
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
    {(item.count_unit_warnings ?? []).length > 0 && (
      <>
        <div>
          {COUNT_UNIT_MARK}: {(item.count_unit_warnings ?? []).map(describeCountUnitWarning).join('; ')}
        </div>
        <div>{COUNT_UNITS_NOT_VALIDATED}</div>
        {item.count_units_note && <div>Known: {item.count_units_note}</div>}
      </>
    )}
  </VizTooltip>
);
