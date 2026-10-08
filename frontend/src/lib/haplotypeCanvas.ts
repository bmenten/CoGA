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
 * Mark a haplotype band as an affected/carrier (risk) allele with a thin line in the
 * risk colour. The homolog (P1/P2/M1/M2) fill is left untouched, so origin stays fully
 * readable and the risk allele is flagged unobtrusively, on both the chromosome and
 * whole-genome tracks.
 *
 * The line does not rely on its hue alone (#529): a red or orange line on a dark green
 * or blue band has little contrast for many colour-blind readers. So an affected
 * haplotype's line is solid and a carrier's is dashed, and the line is set off from
 * the band by a light gap: along the band's bottom edge for a band tall enough to keep
 * its colour (`inside`), or just below a thin band, which then stays whole (`below`).
 *
 * Call this *after* the band's base fill has been drawn at (x, y, w, h).
 */
const RISK_LINE_THICKNESS = 2.5;
const RISK_LINE_THICKNESS_BELOW = 2;
const RISK_GAP = 1;
const RISK_GAP_COLOR = 'rgba(255, 255, 255, 0.85)';
const RISK_DASH = 4;
const RISK_DASH_GAP = 2;

export type HaplotypeRiskPattern = 'solid' | 'dashed';

/** Solid for an affected (dominant or X-linked) haplotype, dashed for a recessive carrier's. */
export const haplotypeRiskPattern = (kind: DiseaseHaplotypeKind): HaplotypeRiskPattern =>
  kind === 'recessive-maternal' || kind === 'recessive-paternal' ? 'dashed' : 'solid';

const fillRiskLine = (
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  thickness: number,
  pattern: HaplotypeRiskPattern,
): void => {
  if (pattern === 'solid') {
    ctx.fillRect(x, y, w, thickness);
    return;
  }
  for (let dashX = x; dashX < x + w; dashX += RISK_DASH + RISK_DASH_GAP) {
    ctx.fillRect(dashX, y, Math.min(RISK_DASH, x + w - dashX), thickness);
  }
};

export const drawHaplotypeRiskOverlay = (
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  color: string,
  {
    pattern = 'solid',
    placement = 'inside',
  }: { pattern?: HaplotypeRiskPattern; placement?: 'inside' | 'below' } = {},
): void => {
  const width = Math.max(w, 0.5);
  ctx.save();
  if (placement === 'below') {
    // The gap is left empty: the track background shows through.
    ctx.fillStyle = color;
    fillRiskLine(ctx, x, y + h + RISK_GAP, width, RISK_LINE_THICKNESS_BELOW, pattern);
  } else {
    const lineY = y + h - RISK_LINE_THICKNESS;
    ctx.fillStyle = RISK_GAP_COLOR;
    ctx.fillRect(x, lineY - RISK_GAP, width, RISK_GAP);
    ctx.fillStyle = color;
    fillRiskLine(ctx, x, lineY, width, RISK_LINE_THICKNESS, pattern);
  }
  ctx.restore();
};

/** The haplotype tracks' colours, read from the theme once per draw. */
export interface HaplotypePalette {
  /** A homolog's colour by its value ('0' dark, '1' light), on the father's side. */
  father: string[];
  /** The same on the mother's side. */
  mother: string[];
  /** The risk line's colour, by the kind of disease haplotype. */
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
