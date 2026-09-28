import React from 'react';
import { normalizeHaplotypeInheritance } from '../../lib/haplotypeRisk';

interface HaplotypeLegendProps {
  inheritanceModel?: string | null;
}

interface LegendEntry {
  label: string;
  color: string;
  /** Risk alleles are marked by a thin line under the homolog fill, not a solid block. */
  risk?: boolean;
  /** A carrier's line is dashed, an affected haplotype's solid (#529). */
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

const swatchStyle = (entry: LegendEntry): React.CSSProperties =>
  entry.risk
    ? {
        background: 'transparent',
        borderBottomColor: entry.color,
        borderBottomWidth: '2px',
        borderBottomStyle: entry.dashed ? 'dashed' : 'solid',
      }
    : { background: entry.color };

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
