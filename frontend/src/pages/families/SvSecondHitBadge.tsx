import React, { useState, type MouseEvent, type FocusEvent } from 'react';
import type { SvSecondHitSvOut } from '../../lib/apiSchema.generated';
import type { SvSecondHit } from './smallVariantSearch';
import { formatLocus } from './smallVariantResultUtils';

// How many of a gene's SVs the tooltip names; the rest are counted.
const LISTED_SVS = 5;

// Read-based phasing (shared phase set) is direct evidence; segregation is inferred.
const evidencePhrase = (evidence?: string | null) =>
  evidence === 'read' ? ' by read phasing' : evidence === 'segregation' ? ' by segregation' : '';

// The SVs that tell most about this SNV first: a copy loss in trans (it unmasks the SNV),
// then trans, cis, unknown phase, and last an SV no affected member carries.
const svRank = (sv: SvSecondHitSvOut) => {
  if (!sv.affected_zygosity) return 4;
  if (sv.phase === 'trans') return sv.copy_loss ? 0 : 1;
  if (sv.phase === 'cis') return 2;
  return 3;
};

const describeSv = (sv: SvSecondHitSvOut) => {
  const where =
    sv.chr && typeof sv.start === 'number' && typeof sv.end === 'number'
      ? ` ${formatLocus({ chr: sv.chr, start: sv.start, end: sv.end })}`
      : '';
  if (!sv.affected_zygosity) return `${sv.sv_type}${where}, not carried by an affected member`;
  const verdict =
    sv.phase === 'trans' || sv.phase === 'cis'
      ? `in ${sv.phase}${evidencePhrase(sv.phase_evidence)}`
      : 'phase unknown';
  return `${sv.sv_type}${where} (${sv.affected_zygosity}), ${verdict}`;
};

// Each SV with its own phase, the most telling first, as one sentence of the tooltip.
const describeSvs = (svs: SvSecondHitSvOut[]) => {
  const ranked = [...svs].sort((a, b) => svRank(a) - svRank(b));
  const listed = ranked.slice(0, LISTED_SVS).map(describeSv);
  const more = ranked.length - listed.length;
  return ` Each SV: ${listed.join('; ')}${more > 0 ? `; and ${more} more` : ''}.`;
};

/**
 * Flags a small variant whose gene is also hit by a structural variant — the cross-type
 * "second hit" that can complete a recessive genotype. A deletion in trans with a het SNV
 * (deletion_unmasked) is the headline: the deletion removes the other allele, so the pair is
 * effectively biallelic, whatever the gene's other SVs are.
 *
 * Each SV has its own phase (`svs`); the chip shows the gene's, drawn from theirs, and the
 * tooltip lists them when there are several (CLIN-28: one SV's cis used to stand for all).
 *
 * The explanation is shown via a fixed-position hover/focus tooltip rather than the native
 * `title` attribute: the badge's inline SVG icon swallows the pointer, so `title` never
 * fired, and a fixed tooltip also escapes the variant table's scroll clipping.
 */
const SvSecondHitBadge: React.FC<{ hit?: SvSecondHit | null; href?: string | null }> = ({
  hit,
  href,
}) => {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);
  if (!hit) return null;

  const types = hit.sv_types.length ? hit.sv_types.join(', ') : 'SV';
  const zygosity = hit.affected_zygosity ? ` · ${hit.affected_zygosity}` : '';
  const phase = hit.phase === 'trans' || hit.phase === 'cis' ? hit.phase : '';
  const byReads = hit.phase_evidence === 'read';
  const unmasked = Boolean(hit.deletion_unmasked);
  const svs = hit.svs ?? [];
  const several = svs.length > 1;

  const how = evidencePhrase(hit.phase_evidence);
  const biallelic =
    `in trans${how} with this heterozygous SNV — the deletion removes the other allele, ` +
    'so the gene is effectively biallelic.';
  const phaseSentence =
    hit.phase === 'trans'
      ? ` ${several ? 'At least one is' : 'It is'} in trans (on the other allele)${how}` +
        ' — a compound-heterozygous candidate.'
      : hit.phase === 'cis'
        ? ` ${several ? 'Every one the affected members carry is' : 'It is'} in cis` +
          ` (the same allele as this SNV)${how}.`
        : '';
  const hitBySeveral = `This gene is also hit by ${svs.length} structural variants (${types}${zygosity})`;
  const explanation = several
    ? (unmasked
        ? `${hitBySeveral}, among them a deletion ${biallelic}`
        : `${hitBySeveral} — possible cross-type second hits.${phaseSentence}`) + describeSvs(svs)
    : unmasked
      ? `This gene is also hit by a deletion (${types}${zygosity}) ${biallelic}`
      : `This gene is also hit by a structural variant (${types}${zygosity})` +
        ` — a possible cross-type second hit.${phaseSentence}`;
  // The click target is not obvious from a badge, so the tooltip that explains the
  // pattern also says where it goes.
  const message = href
    ? `${explanation} Opens the family's structural variants for this gene in a new tab.`
    : explanation;

  const showTip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: rect.left });
  };
  const hideTip = () => setTip(null);

  const className = [
    'sv-second-hit-badge',
    hit.has_deletion ? 'sv-second-hit-badge--del' : '',
    unmasked ? 'sv-second-hit-badge--unmasked' : '',
    href ? 'sv-second-hit-badge--link' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const body = (
    <>
      SV: {types}
      {hit.sv_count > 1 ? ` ×${hit.sv_count}` : ''}
      {phase ? (
        <span className={`sv-second-hit-phase${byReads ? ' sv-second-hit-phase--read' : ''}`}>
          {phase}
          {byReads ? '✓' : ''}
        </span>
      ) : null}
    </>
  );

  return (
    <>
      {href ? (
        // New tab, like the other genome links, so the filtered variant list survives.
        <a
          className={className}
          href={href}
          target="_blank"
          rel="noreferrer"
          aria-label={message}
          onMouseEnter={showTip}
          onMouseLeave={hideTip}
          onFocus={showTip}
          onBlur={hideTip}
        >
          {body}
        </a>
      ) : (
        // A button, so keyboard users can reach the explanation, as with the link; it
        // has no action of its own.
        <button
          type="button"
          className={className}
          aria-label={message}
          onMouseEnter={showTip}
          onMouseLeave={hideTip}
          onFocus={showTip}
          onBlur={hideTip}
        >
          {body}
        </button>
      )}
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
