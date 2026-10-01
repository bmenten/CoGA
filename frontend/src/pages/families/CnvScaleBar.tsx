import { formatCnvPoints, type CnvClassification } from '../../lib/cnvAcmg';

type CnvScaleBarProps = {
  classification: CnvClassification;
};

// Visible point domain for the arrow (ClinGen CNV totals run roughly −1…+1).
const DOMAIN_MIN = -1.2;
const DOMAIN_MAX = 1.2;

const TICKS = [-0.99, -0.9, 0, 0.9, 0.99];

const positionPct = (points: number): number => {
  const clamped = Math.min(DOMAIN_MAX, Math.max(DOMAIN_MIN, points));
  return ((clamped - DOMAIN_MIN) / (DOMAIN_MAX - DOMAIN_MIN)) * 100;
};

export default function CnvScaleBar({ classification }: CnvScaleBarProps) {
  const { pointTotal, classLabel } = classification;
  const arrowPct = positionPct(pointTotal);

  return (
    <div
      className="acmg-scalebar"
      role="img"
      aria-label={`ClinGen CNV classification: ${classLabel}, ${formatCnvPoints(pointTotal)} points`}
    >
      <div className="acmg-scalebar-readout">
        <span className="acmg-scalebar-class">{classLabel}</span>
        <span className="acmg-scalebar-points">{formatCnvPoints(pointTotal)} pts</span>
      </div>
      <div className="acmg-scalebar-track">
        <div className="acmg-scalebar-gradient" />
        {TICKS.map((tick) => (
          <span
            key={tick}
            className="acmg-scalebar-tick"
            style={{ left: `${positionPct(tick)}%` }}
          />
        ))}
        <span className="acmg-scalebar-arrow" style={{ left: `${arrowPct}%` }} aria-hidden="true" />
      </div>
      <div className="acmg-scalebar-legend">
        <span>Benign</span>
        <span>Likely benign</span>
        <span>VUS</span>
        <span>Likely pathogenic</span>
        <span>Pathogenic</span>
      </div>
    </div>
  );
}
