import { cssVar } from './colors';
import {
  getHaplotypeLaneSignature,
  type DiseaseHaplotypeKind,
  type HaplotypeLane,
  type HaplotypeMemberLike,
  type HaplotypeSegmentLike,
} from './haplotypeRisk';
import { isDeletedHaplotype } from './phasedMarkers';

/**
 * Mark the lane of a haplotype that carries the disease allele (the risk haplotype): the lane
 * is bracketed by a line in the risk colour along its top and its bottom edge
 * (`drawHaplotypeRiskLane`), and, where the band is thinner than its lane (the chromosome view),
 * the lane behind the band is tinted in the risk colour (`fillHaplotypeRiskTint`). The homolog
 * (P1/P2/M1/M2) colour of the band itself is left untouched, so its origin stays readable,
 * while the risk lane stands out as a highlighted strip.
 *
 * The mark does not rely on its hue alone: the tint differs from the white track in lightness,
 * and an affected haplotype's edges are solid where a recessive carrier's are dashed, so the
 * two read apart without colour. The dashes are long enough to read as a line, and laid on a
 * grid of the canvas, so the blocks of one risk haplotype join into one continuous line.
 *
 * Fill the tint *before* the band, which then sits on it. Draw the edges *after* the band and
 * any marker ticks, which then never cover them; on a band that fills its lane (the genome
 * overview) they lie on the band's top and bottom.
 */
const RISK_EDGE = 2;
const RISK_TINT_ALPHA = 0.28;
const RISK_DASH = 7;
const RISK_DASH_GAP = 3;

export type HaplotypeRiskPattern = 'solid' | 'dashed';

/** Solid for an affected (dominant or X-linked) haplotype, dashed for a recessive carrier's. */
export const haplotypeRiskPattern = (kind: DiseaseHaplotypeKind): HaplotypeRiskPattern =>
  kind === 'recessive-maternal' || kind === 'recessive-paternal' ? 'dashed' : 'solid';

const fillRiskEdge = (
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  pattern: HaplotypeRiskPattern,
): void => {
  if (pattern === 'solid') {
    ctx.fillRect(x, y, w, RISK_EDGE);
    return;
  }
  const period = RISK_DASH + RISK_DASH_GAP;
  const end = x + w;
  for (let dashX = Math.floor(x / period) * period; dashX < end; dashX += period) {
    const from = Math.max(dashX, x);
    const to = Math.min(dashX + RISK_DASH, end);
    if (to > from) ctx.fillRect(from, y, to - from, RISK_EDGE);
  }
};

/** The risk lane's tint, over the lane's whole height: fill it before the band. */
export const fillHaplotypeRiskTint = (
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  color: string,
): void => {
  ctx.save();
  ctx.fillStyle = color;
  ctx.globalAlpha = RISK_TINT_ALPHA;
  ctx.fillRect(x, y, Math.max(w, 0.5), h);
  ctx.restore();
};

/** The risk lane's edges, along its top and its bottom: draw them after the band and markers. */
export const drawHaplotypeRiskLane = (
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  color: string,
  { pattern = 'solid' }: { pattern?: HaplotypeRiskPattern } = {},
): void => {
  const width = Math.max(w, 0.5);
  ctx.save();
  ctx.fillStyle = color;
  fillRiskEdge(ctx, x, y, width, pattern);
  fillRiskEdge(ctx, x, y + h - RISK_EDGE, width, pattern);
  ctx.restore();
};

/** The haplotype tracks' colours, read from the theme once per draw. */
export interface HaplotypePalette {
  /** A homolog's colour by its value ('0' dark, '1' light), on the father's side. */
  father: string[];
  /** The same on the mother's side. */
  mother: string[];
  /** The risk lane's colour, by the kind of disease haplotype. */
  risk: Record<DiseaseHaplotypeKind, string>;
  unknown: string;
  deletedFill: string;
  deletedStroke: string;
}

/** The palette of the chromosome and the whole-genome haplotype tracks. */
export const readHaplotypePalette = (): HaplotypePalette => ({
  father: [cssVar('--color-haplotype-father-dark'), cssVar('--color-haplotype-father-light')],
  mother: [cssVar('--color-haplotype-mother-dark'), cssVar('--color-haplotype-mother-light')],
  risk: {
    dominant: cssVar('--color-haplotype-affected'),
    'recessive-maternal': cssVar('--color-haplotype-carrier'),
    'recessive-paternal': cssVar('--color-haplotype-carrier'),
    'x-linked': cssVar('--color-haplotype-affected'),
  },
  unknown: cssVar('--color-haplotype-unknown'),
  deletedFill: cssVar('--color-haplotype-deleted-fill'),
  deletedStroke: cssVar('--color-haplotype-deleted-stroke'),
});

/**
 * A lane's base fill: the deleted fill for a deleted lane ('.'), else its parent of origin's
 * colour for its homolog, else the unknown grey (a lane without an origin or a homolog).
 */
export const haplotypeLaneColor = (
  palette: HaplotypePalette,
  member: HaplotypeMemberLike,
  segment: HaplotypeSegmentLike,
  lane: HaplotypeLane,
  chrom?: string | null,
): string => {
  const value = segment[lane];
  if (isDeletedHaplotype(value)) return palette.deletedFill;
  const parsed = parseInt(value, 10);
  const signature = getHaplotypeLaneSignature(member, segment, lane, chrom);
  if (!signature) return palette.unknown;
  const colors = signature.origin === 'paternal' ? palette.father : palette.mother;
  return Number.isNaN(parsed) ? palette.unknown : colors[parsed] || palette.unknown;
};
