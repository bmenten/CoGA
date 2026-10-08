import type { AcmgClassification } from '../../lib/acmg';

type AcmgScaleBarProps = {
  classification: AcmgClassification;
};

// The bar follows the Bayesian reading of the points scale (Tavtigian 2018/2020:
// prior 0.10, odds of pathogenicity 350^(points/8)). Like the ACGS/HEE VUS
// temperature chart it is not linear in points: Benign (< 0.1%) and Pathogenic
// (> 99%) are narrow end caps, and every VUS point gets its own equal cell, so
// the posterior ticks inside the VUS band are evenly spaced.
//
// Each segment covers a run of whole points; a total sits in the middle of its
// own point's cell. Widths are percentages of the bar and sum to 100.
type Segment = { from: number; to: number; width: number };

const SEGMENTS: Segment[] = [
  { from: -Infinity, to: -7, width: 6 }, // Benign
  { from: -6, to: -1, width: 14 }, // Likely benign
  { from: 0, to: 0, width: 10 }, // VUS, one cell per point
  { from: 1, to: 1, width: 10 },
  { from: 2, to: 2, width: 10 },
  { from: 3, to: 3, width: 10 },
  { from: 4, to: 4, width: 10 },
  { from: 5, to: 5, width: 10 },
  { from: 6, to: 9, width: 14 }, // Likely pathogenic
  { from: 10, to: Infinity, width: 6 }, // Pathogenic
];

// Left edge (%) of the cell holding `points`.
function cellStartPct(points: number): number {
  let start = 0;
  for (const segment of SEGMENTS) {
    if (points <= segment.to) {
      if (!Number.isFinite(segment.from)) return start;
      const cells = segment.to - segment.from + 1;
      return start + ((points - segment.from) / cells) * segment.width;
    }
    start += segment.width;
  }
  return 100;
}

// Centre (%) of the cell holding `points` — where the arrow points.
export function scalePositionPct(points: number): number {
  let start = 0;
  for (const segment of SEGMENTS) {
    if (points <= segment.to) {
      if (!Number.isFinite(segment.from) || !Number.isFinite(segment.to)) {
        return start + segment.width / 2;
      }
      const cells = segment.to - segment.from + 1;
      const cell = segment.width / cells;
      return start + (points - segment.from) * cell + cell / 2;
    }
    start += segment.width;
  }
  return 100;
}

// Ticks sit on the lower edge of a point's cell and carry that point's
// posterior (0 → 10%, 1 → 18.8%, … 6 → 90%). The two outer class boundaries
// carry the class definitions instead: Benign < 0.1%, Pathogenic > 99%. The
// labels are fixed text; the test checks them against the formula (prior 0.10,
// odds 350^(points/8)).
type Tick = { points: number; label: string; boundary: boolean };

export const SCALE_TICKS: Tick[] = [
  { points: -6, label: '0.1%', boundary: true },
  { points: 0, label: '10%', boundary: true },
  { points: 1, label: '18.8%', boundary: false },
  { points: 2, label: '32.5%', boundary: false },
  { points: 3, label: '50%', boundary: false },
  { points: 4, label: '67.5%', boundary: false },
  { points: 5, label: '81.2%', boundary: false },
  { points: 6, label: '90%', boundary: true },
  { points: 10, label: '99%', boundary: true },
];

const LEGEND = [
  { label: 'Benign', from: -Infinity, to: -7, align: 'start' },
  { label: 'Likely benign', from: -6, to: -1, align: 'center' },
  { label: 'VUS', from: 0, to: 5, align: 'center' },
  { label: 'Likely pathogenic', from: 6, to: 9, align: 'center' },
  { label: 'Pathogenic', from: 10, to: Infinity, align: 'end' },
] as const;

function legendStyle(item: (typeof LEGEND)[number]) {
  if (item.align === 'start') return { left: 0 };
  if (item.align === 'end') return { right: 0 };
  const left = cellStartPct(item.from);
  const right = cellStartPct(item.to + 1);
  return { left: `${(left + right) / 2}%`, transform: 'translateX(-50%)' };
}

const VUS_TIER_LABELS: Record<NonNullable<AcmgClassification['vusTier']>, string> = {
  hot: 'Hot',
  warm: 'Warm',
  cold: 'Cold',
};

export default function AcmgScaleBar({ classification }: AcmgScaleBarProps) {
  const { points, label, standAloneBenign, vusTier } = classification;
  const arrowPct = scalePositionPct(standAloneBenign ? -Infinity : points);
  const pointsLabel = standAloneBenign ? 'BA1' : points > 0 ? `+${points}` : `${points}`;
  const ariaLabel = `ACMG classification: ${label}${
    vusTier ? ` (${VUS_TIER_LABELS[vusTier]} VUS)` : ''
  }, ${pointsLabel} points`;

  return (
    <div className="acmg-scalebar" role="img" aria-label={ariaLabel}>
      <div className="acmg-scalebar-readout">
        <span className="acmg-scalebar-class">{label}</span>
        {vusTier ? (
          <span className={`acmg-vus-tier-chip acmg-vus-tier-chip--${vusTier}`}>
            {VUS_TIER_LABELS[vusTier]} VUS
          </span>
        ) : null}
        <span className="acmg-scalebar-points">{pointsLabel} pts</span>
      </div>
      <div className="acmg-scalebar-track">
        <div className="acmg-scalebar-gradient" />
        {SCALE_TICKS.map((tick) => (
          <span
            key={tick.points}
            className={`acmg-scalebar-tick${tick.boundary ? ' acmg-scalebar-tick--boundary' : ''}`}
            style={{ left: `${cellStartPct(tick.points)}%` }}
          />
        ))}
        <span className="acmg-scalebar-arrow" style={{ left: `${arrowPct}%` }} aria-hidden="true" />
      </div>
      <div className="acmg-scalebar-ticklabels" aria-hidden="true">
        {SCALE_TICKS.map((tick) => (
          <span
            key={tick.points}
            className="acmg-scalebar-ticklabel"
            style={{ left: `${cellStartPct(tick.points)}%` }}
          >
            {tick.label}
          </span>
        ))}
      </div>
      <div className="acmg-scalebar-legend">
        {LEGEND.map((item) => (
          <span key={item.label} className="acmg-scalebar-legend-item" style={legendStyle(item)}>
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}
