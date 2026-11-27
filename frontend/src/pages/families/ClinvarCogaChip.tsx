import React from 'react';

import type { ClinvarCogaOut } from '../../lib/apiSchema.generated';
import { clinvarCogaChanged, clinvarCogaLabel, clinvarCogaOf } from '../../lib/clinvar';
import { formatClinvarStars, formatTokenLabel, getClinvarTone } from './smallVariantResultUtils';

// What CoGA's release says of an allele it gives no classification.
const STATUS_TEXT: Record<Exclude<ClinvarCogaOut['status'], 'in_release'>, string> = {
  not_in_release: 'not in this release',
  no_release: 'no release loaded',
  not_available: 'not available for this assembly',
};

export const clinvarCogaText = (value: ClinvarCogaOut): string => {
  if (value.status !== 'in_release') return STATUS_TEXT[value.status];
  if (value.included_only || !value.classification) return 'only as part of a combination';
  const stars = formatClinvarStars(value.stars);
  return `${formatTokenLabel(value.classification)}${stars ? ` ${stars}` : ''}`;
};

/**
 * A small variant's ClinVar in CoGA's own release, beside its annotation's (the owner's decision
 * B7): "ClinVar (CoGA, 2026-11-22): Likely pathogenic ★★★☆", linked to the record's VariationID,
 * with **changed** where the classification or the stars differ from the annotation's.
 */
const ClinvarCogaChip: React.FC<{
  value: ClinvarCogaOut;
  release?: string | null;
  annotated: { clinvar?: string | null; clinvar_stars?: number | null };
}> = ({ value, release, annotated }) => {
  const changed = clinvarCogaChanged(annotated, value);
  const title =
    [value.review_status, value.conditions.join('; ')].filter(Boolean).join(' — ') || undefined;
  const tone = value.status === 'in_release' ? getClinvarTone(value.classification ?? undefined) : 'neutral';
  const content = (
    <>
      <span className="variant-card-chip-key">{clinvarCogaLabel(release ?? value.release)}</span>
      {clinvarCogaText(value)}
      {changed ? <strong className="variant-card-chip-flag">changed</strong> : null}
    </>
  );
  return value.variation_id != null ? (
    <a
      href={`https://www.ncbi.nlm.nih.gov/clinvar/variation/${value.variation_id}/`}
      target="_blank"
      rel="noreferrer"
      title={title}
      className={`variant-card-chip variant-card-chip--link variant-card-chip--${tone}`}
    >
      {content}
    </a>
  ) : (
    <span className={`variant-card-chip variant-card-chip--${tone}`} title={title}>
      {content}
    </span>
  );
};

/**
 * A table row's sign that CoGA's own ClinVar release classifies the variant otherwise than its
 * annotation; CoGA's value on hover. Nothing where they agree, or CoGA reads no release.
 */
export const ClinvarCogaMark: React.FC<{
  variant: {
    alt?: string | null;
    alt_allele?: string | null;
    clinvar?: string | null;
    clinvar_stars?: number | null;
    clinvar_coga?: ClinvarCogaOut[] | null;
  };
  release?: string | null;
}> = ({ variant, release }) => {
  const value = clinvarCogaOf(variant);
  if (!value || !clinvarCogaChanged(variant, value)) return null;
  const said = `${clinvarCogaLabel(release ?? value.release)}: ${clinvarCogaText(value)}`;
  return (
    <span className="clinvar-coga-mark" role="img" aria-label={said} title={said}>
      ≠
    </span>
  );
};

export default ClinvarCogaChip;
