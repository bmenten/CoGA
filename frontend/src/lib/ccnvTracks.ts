/**
 * How the genome and chromosome views draw a conventional NIPT's maternal plasma: its ccnv
 * coverage and segments (log2 ratios), with ccnv's results the track availability serves
 * (`ccnv`, `ccnv_output.ccnv_track_summary`).
 *
 * The plasma's ratios are small: a fetal trisomy at a fetal fraction f raises a chromosome by
 * log2(1 + f/2), about +0.08 at 12 %. On the views' log2-ratio axis (±1.5, gain and loss from
 * ±0.35) it would never be coloured. So a ccnv track has a NIPT axis of its own, colours each
 * segment by ccnv's own call, and draws the expected log2 ratio of a fetal trisomy and monosomy
 * at the plasma's fetal fraction as dashed lines, on chromosomes 1–22 and X, as ccnv's own plot
 * draws them (https://matthdsm.github.io/ccnv/).
 */

import type { CcnvTrackSummaryOut } from './apiSchema.generated';
import { normalizeChrom } from './chromosomes';

/** The caller whose tracks are a conventional NIPT's maternal plasma. */
export const CCNV_SOURCE = 'ccnv';

/** The NIPT axis: ± this log2 ratio, a gridline every `NIPT_AXIS_STEP`. */
export const NIPT_AXIS_RANGE = 0.3;
export const NIPT_AXIS_STEP = 0.1;

/** The log2 ratio a fetal trisomy gives the plasma at fetal fraction `fraction` (0–1). */
export const fetalTrisomyLog2 = (fraction: number): number => Math.log2(1 + fraction / 2);

/** The log2 ratio a fetal monosomy gives the plasma at fetal fraction `fraction` (0–1). */
export const fetalMonosomyLog2 = (fraction: number): number =>
  Math.log2(Math.max(1e-4, 1 - fraction / 2));

/**
 * Whether the expected trisomy and monosomy lines are drawn on `chrom`: on every chromosome but
 * Y, which a female fetus does not have and the maternal plasma has none of.
 */
export const drawsFetalFractionLines = (chrom: string): boolean => normalizeChrom(chrom) !== 'Y';

/** A fraction (0–1) as a percentage. */
export const formatPercent = (fraction: number, digits = 1): string =>
  `${(fraction * 100).toFixed(digits)}%`;

/** The one-line summary in a ccnv track's head: the fetal fraction, fetal sex and SCA call. */
export const ccnvTrackHeadline = (summary: CcnvTrackSummaryOut): string =>
  `FF ${formatPercent(summary.fetal_fraction)} · fetal sex ${summary.fetal_sex} · SCA ${summary.sca_call}`;

/** Each of ccnv's fetal-fraction estimates, the one the lines are drawn at first. */
export const ccnvFetalFractionEstimates = (summary: CcnvTrackSummaryOut): string[] => {
  const optional: [string, number | null | undefined][] = [
    ['FFY (chrY)', summary.ffy],
    ['FFX (chrX)', summary.ffx],
    ['FFA (autosomes)', summary.ffa],
    ['FF from fragment sizes', summary.ff_size],
  ];
  const active =
    `Active FF ${formatPercent(summary.active_ff)}` +
    (summary.active_ff_se != null ? ` ± ${formatPercent(summary.active_ff_se)}` : '') +
    (summary.active_ff_source ? ` (${summary.active_ff_source})` : '');
  return [
    `Effective FF ${formatPercent(summary.fetal_fraction)}: the expected lines are drawn at it`,
    active,
    ...optional
      .filter((entry): entry is [string, number] => entry[1] != null)
      .map(([label, value]) => `${label} ${formatPercent(value)}`),
    ...(summary.sca_confidence != null
      ? [`SCA ${summary.sca_call}, confidence ${formatPercent(summary.sca_confidence, 2)}`]
      : []),
    ...(summary.low_ff_warning ? ['ccnv warns of a low fetal fraction'] : []),
  ];
};

/** A segment's value as ccnv's details give it, or none. */
type SegmentDetails = Record<string, boolean | number | string> | null | undefined;

/** ccnv's call of a segment: `gain`, `loss`, or null for a segment it did not call. */
export const ccnvSegmentCall = (details: SegmentDetails): 'gain' | 'loss' | null => {
  const call = details?.call;
  return call === 'gain' || call === 'loss' ? call : null;
};

/** Whether ccnv flagged a segment as discordant with the fetal fraction. */
export const ccnvSegmentDiscordant = (details: SegmentDetails): boolean => details?.discordant === true;

const DETAIL_LABELS: [string, string][] = [
  ['call', 'Call'],
  ['zscore', 'z-score'],
  ['bin_count', 'Bins'],
  ['expected_ratio', 'Expected log2 ratio'],
  ['implied_ff', 'Implied fetal fraction'],
  ['discordant', 'Discordant'],
  ['discordance_reason', 'Discordance'],
  ['origin_call', 'Origin'],
  ['frag_short_ratio', 'Short-fragment share'],
];

const formatDetail = (key: string, value: boolean | number | string): string => {
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') {
    if (key === 'implied_ff' || key === 'frag_short_ratio') return formatPercent(value);
    if (key === 'bin_count') return String(value);
    return String(Number(value.toPrecision(3)));
  }
  return value;
};

/**
 * The lines of a segment's tooltip: each value its caller gives (`details`) that a reader can
 * tell, in a fixed order, labelled; a value of no listed kind is left out.
 */
export const segmentDetailLines = (details: SegmentDetails): string[] =>
  DETAIL_LABELS.filter(([key]) => details?.[key] !== undefined && details?.[key] !== '').map(
    ([key, label]) => `${label}: ${formatDetail(key, details![key])}`,
  );
