import * as d3 from 'd3';

import { type ClinvarClass, clinvarClass } from './clinvar';

/**
 * How the small-variant track marks a variant:
 *
 * - ClinVar (likely) pathogenic is a larger red diamond, whatever the impact; a ClinVar
 *   conflict that lists a (likely) pathogenic classification the same diamond in orange;
 * - every other variant is a dot, whose colour gives its class: green for ClinVar
 *   (likely) benign, then by impact orange for HIGH, yellow for MODERATE and grey for LOW,
 *   MODIFIER or none;
 * - a review tag draws a ring around the mark in the tag's colour and leaves the mark
 *   itself alone.
 *
 * The dots differ by colour only; the tooltip names the impact and the ClinVar class.
 */
export type SmallVariantMarkKind = ClinvarClass | 'high' | 'moderate' | 'other';

interface MarkStyle {
  label: string;
  symbol: d3.SymbolType;
  /** Area relative to a dot of the track's radius. */
  scale: number;
  color: string;
}

const dot = (label: string, color: string): MarkStyle => ({ label, symbol: d3.symbolCircle, scale: 1, color });
// Half as large again as a dot: larger, and still within a parental-origin row.
const diamond = (label: string, color: string): MarkStyle => ({ label, symbol: d3.symbolDiamond2, scale: 1.5, color });

// Literal hex, so the track renders without the theme stylesheet (and stays testable).
export const SMALL_VARIANT_MARKS: Record<SmallVariantMarkKind, MarkStyle> = {
  pathogenic: diamond('ClinVar P/LP', '#dc2626'),
  conflicting_pathogenic: diamond('ClinVar conflicting with P/LP', '#d97706'),
  benign: dot('ClinVar B/LB', '#16a34a'),
  high: dot('HIGH impact', '#ea580c'),
  moderate: dot('MODERATE', '#eab308'),
  other: dot('LOW / MODIFIER', '#9ca3af'),
};

/** Legend order. */
export const SMALL_VARIANT_MARK_ORDER: SmallVariantMarkKind[] = [
  'pathogenic',
  'conflicting_pathogenic',
  'benign',
  'high',
  'moderate',
  'other',
];
/** Draw order: the salient marks end up on top. */
const DRAW_RANK: Record<SmallVariantMarkKind, number> = {
  other: 0,
  benign: 1,
  moderate: 2,
  high: 3,
  conflicting_pathogenic: 4,
  pathogenic: 5,
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
