import * as d3 from 'd3';

import { clinvarClass } from './clinvar';

/**
 * How the small-variant track marks a variant (#529).
 *
 * The track drew every variant as the same dot and told the classes apart by colour
 * alone: red, orange, green, blue. A review tag's colour replaced the fill, which hid
 * the ClinVar class of a tagged variant. Now:
 *
 * - shape and size carry the clinically salient classes: a larger diamond for ClinVar
 *   (likely) pathogenic, a triangle for HIGH impact, a hollow square for ClinVar
 *   (likely) benign, and a dot for the rest;
 * - the fill colour repeats the class, and for the dots it gives the impact: green
 *   for MODERATE, grey for LOW, MODIFIER or none;
 * - a review tag draws a ring around the mark in the tag's colour and leaves the mark
 *   itself alone.
 *
 * MODERATE and LOW dots still differ by colour only. They are the least salient
 * classes, and the tooltip names the impact.
 */
export type SmallVariantMarkKind = 'pathogenic' | 'high' | 'moderate' | 'benign' | 'other';

interface MarkStyle {
  label: string;
  symbol: d3.SymbolType;
  /** Area relative to a dot of the track's radius. */
  scale: number;
  color: string;
  /** Drawn as an outline in `color`, not filled. */
  hollow?: boolean;
}

// Literal hex, so the track renders without the theme stylesheet (and stays testable).
export const SMALL_VARIANT_MARKS: Record<SmallVariantMarkKind, MarkStyle> = {
  pathogenic: { label: 'ClinVar P/LP', symbol: d3.symbolDiamond2, scale: 4, color: '#dc2626' },
  high: { label: 'HIGH impact', symbol: d3.symbolTriangle, scale: 3, color: '#fb923c' },
  moderate: { label: 'MODERATE', symbol: d3.symbolCircle, scale: 1, color: '#4ade80' },
  other: { label: 'LOW / other', symbol: d3.symbolCircle, scale: 1, color: '#9ca3af' },
  benign: {
    label: 'ClinVar B/LB',
    symbol: d3.symbolSquare,
    scale: 1.6,
    color: '#60a5fa',
    hollow: true,
  },
};

/** Legend order, and the order marks are drawn in: the salient ones end up on top. */
export const SMALL_VARIANT_MARK_ORDER: SmallVariantMarkKind[] = [
  'pathogenic',
  'high',
  'moderate',
  'other',
  'benign',
];
const DRAW_RANK: Record<SmallVariantMarkKind, number> = {
  other: 0,
  moderate: 1,
  benign: 2,
  high: 3,
  pathogenic: 4,
};

export const smallVariantMarkKind = (variant: {
  clinvar?: string | null;
  impact?: string | null;
}): SmallVariantMarkKind => {
  const clinvar = clinvarClass(variant.clinvar);
  if (clinvar) return clinvar;
  switch ((variant.impact || '').toUpperCase()) {
    case 'HIGH':
      return 'high';
    case 'MODERATE':
    case 'MEDIUM':
      return 'moderate';
    default:
      return 'other';
  }
};

export const markDrawRank = (kind: SmallVariantMarkKind): number => DRAW_RANK[kind];

/** The SVG path of a mark centred on 0,0, for a track whose dots have `radius`. */
export const smallVariantMarkPath = (kind: SmallVariantMarkKind, radius: number): string => {
  const style = SMALL_VARIANT_MARKS[kind];
  return d3.symbol(style.symbol, Math.PI * radius * radius * style.scale)() ?? '';
};

/** Half the width of a mark, for the tag ring drawn around it. */
export const smallVariantMarkExtent = (kind: SmallVariantMarkKind, radius: number): number =>
  radius * Math.sqrt(SMALL_VARIANT_MARKS[kind].scale);
