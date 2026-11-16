import React from 'react';
import { normalizeHaplotypeInheritance } from '../../lib/haplotypeRisk';

interface HaplotypeLegendProps {
  inheritanceModel?: string | null;
}

interface LegendEntry {
  label: string;
  color: string;
  /** The risk haplotype's lane is tinted and bracketed by edges in the risk colour, around the
   * homolog's own band (drawHaplotypeRiskLane), not filled. */
  risk?: boolean;
  /** A carrier's edges are dashed, an affected haplotype's solid. */
  dashed?: boolean;
}

// The four parental homologs (P1/P2 paternal, M1/M2 maternal) are always shown.
// "Untransmitted" (grey) marks a relative's homolog that was not passed down the
// line to the affected family — irrelevant to tracking the disease allele.
const HOMOLOG_ENTRIES: LegendEntry[] = [
  { label: 'P1', color: 'var(--color-haplotype-father-dark)' },
  { label: 'P2', color: 'var(--color-haplotype-father-light)' },
  { label: 'M1', color: 'var(--color-haplotype-mother-dark)' },
  { label: 'M2', color: 'var(--color-haplotype-mother-light)' },
  { label: 'Untransmitted', color: 'var(--color-haplotype-unknown)' },
];

const riskEntriesForModel = (inheritanceModel?: string | null): LegendEntry[] => {
  const mode = normalizeHaplotypeInheritance(inheritanceModel);
  if (mode === 'recessive') {
    return [{ label: 'Carrier', color: 'var(--color-haplotype-carrier)', risk: true, dashed: true }];
  }
  // dominant / x-linked / unknown
  return [{ label: 'Affected', color: 'var(--color-haplotype-affected)', risk: true }];
};

// The lane as the tracks draw it: a light tint of the risk colour between two edges in it.
const swatchStyle = (entry: LegendEntry): React.CSSProperties => {
  if (!entry.risk) return { background: entry.color };
  const edgeStyle = entry.dashed ? 'dashed' : 'solid';
  return {
    width: '1.1rem',
    borderRadius: 0,
    background: `color-mix(in srgb, ${entry.color} 28%, transparent)`,
    borderLeftStyle: 'none',
    borderRightStyle: 'none',
    borderTopStyle: edgeStyle,
    borderBottomStyle: edgeStyle,
    borderTopWidth: '2px',
    borderBottomWidth: '2px',
    borderTopColor: entry.color,
    borderBottomColor: entry.color,
  };
};

const HaplotypeLegend: React.FC<HaplotypeLegendProps> = ({ inheritanceModel }) => (
  <div className="haplotype-legend" aria-label="Haplotype color legend">
    {[...HOMOLOG_ENTRIES, ...riskEntriesForModel(inheritanceModel)].map((entry) => (
      <span key={entry.label} className="haplotype-legend-item">
        <span className="haplotype-legend-swatch" style={swatchStyle(entry)} />
        {entry.label}
      </span>
    ))}
  </div>
);

export default HaplotypeLegend;
