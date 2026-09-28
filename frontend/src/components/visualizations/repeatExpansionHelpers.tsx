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
