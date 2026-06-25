import React, { useState, type MouseEvent, type FocusEvent } from 'react';
import type { SvSecondHit } from './smallVariantSearch';

/**
 * Flags a small variant whose gene is also hit by a structural variant — the cross-type
 * "second hit" that can complete a recessive genotype. A deletion in trans with a het SNV
 * (deletion_unmasked) is the headline: the deletion removes the other allele, so the pair is
 * effectively biallelic.
 *
 * The explanation is shown via a fixed-position hover/focus tooltip rather than the native
 * `title` attribute: the badge's inline SVG icon swallows the pointer, so `title` never
 * fired, and a fixed tooltip also escapes the variant table's scroll clipping.
 */
const SvSecondHitBadge: React.FC<{ hit?: SvSecondHit | null }> = ({ hit }) => {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);
  if (!hit) return null;

  const types = hit.sv_types.length ? hit.sv_types.join(', ') : 'SV';
  const zygosity = hit.affected_zygosity ? ` · ${hit.affected_zygosity}` : '';
  const phase = hit.phase === 'trans' || hit.phase === 'cis' ? hit.phase : '';
  const byReads = hit.phase_evidence === 'read';
  const unmasked = Boolean(hit.deletion_unmasked);

  // Read-based phasing (shared phase set) is direct evidence; segregation is inferred.
  const how = byReads
    ? ' by read phasing'
    : hit.phase_evidence === 'segregation'
      ? ' by segregation'
      : '';
  const phaseSentence =
    hit.phase === 'trans'
      ? ` It is in trans (on the other allele)${how} — a compound-heterozygous candidate.`
      : hit.phase === 'cis'
        ? ` It is in cis (the same allele as this SNV)${how}.`
        : '';
  const message = unmasked
    ? `This gene is also hit by a deletion (${types}${zygosity}) in trans${how} with this heterozygous SNV — the deletion removes the other allele, so the gene is effectively biallelic.`
    : `This gene is also hit by a structural variant (${types}${zygosity}) — a possible cross-type second hit.${phaseSentence}`;

  const showTip = (event: MouseEvent<HTMLSpanElement> | FocusEvent<HTMLSpanElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: rect.left });
  };
  const hideTip = () => setTip(null);

  const className = [
    'sv-second-hit-badge',
    hit.has_deletion ? 'sv-second-hit-badge--del' : '',
    unmasked ? 'sv-second-hit-badge--unmasked' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <>
      <span
        className={className}
        tabIndex={0}
        onMouseEnter={showTip}
        onMouseLeave={hideTip}
        onFocus={showTip}
        onBlur={hideTip}
      >
        SV: {types}
        {hit.sv_count > 1 ? ` ×${hit.sv_count}` : ''}
        {phase ? (
          <span
            className={`sv-second-hit-phase${byReads ? ' sv-second-hit-phase--read' : ''}`}
          >
            {phase}
            {byReads ? '✓' : ''}
          </span>
        ) : null}
      </span>
      {tip ? (
        <span
          className="sv-second-hit-tooltip"
          style={{ top: tip.top, left: tip.left }}
          role="tooltip"
        >
          {message}
        </span>
      ) : null}
    </>
  );
};

export default SvSecondHitBadge;
