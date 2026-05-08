import React from 'react';
import { Link } from 'react-router-dom';
import {
  getTagDefinitionMap,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantGroup,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  buildReviewTagTooltip,
  buildSmallVariantGeneInfoHref,
  buildSmallVariantNavigation,
  formatCompoundHetPhaseStatus,
  formatFrequency,
  formatLocus,
  formatTokenLabel,
  getImpactTone,
  getReviewClassificationTone,
  getReviewTagStyle,
  sortReviewTagKeys,
} from './smallVariantResultUtils';

interface SmallVariantPairCardsProps {
  groups: SmallVariantGroup[];
  members: FamilyMember[];
  familyId?: string;
  projectId?: string;
  locationSearch: string;
  tags: SmallVariantTagDefinition[];
  onEditReview: (variant: SmallVariant) => void;
}

const buildCompactGenotypeText = (variant: SmallVariant, members: FamilyMember[]) =>
  members
    .map((member) => {
      const genotype = variant.genotypes.find((entry) => entry.sample === member.sample_id);
      return genotype?.gt ? `${member.sample_id} ${genotype.gt}` : null;
    })
    .filter((entry): entry is string => Boolean(entry))
    .join(' · ');

export default function SmallVariantPairCards({
  groups,
  members,
  familyId,
  projectId,
  locationSearch,
  tags,
  onEditReview,
}: SmallVariantPairCardsProps) {
  const tagMap = getTagDefinitionMap(tags);

  return (
    <div className="variant-card-list">
      {groups.map((group) => {
        const anchorVariant = group.variants[0];
        const groupGene = group.gene || group.gene_id || anchorVariant?.gene || anchorVariant?.gene_id;
        const geneInfoHref = anchorVariant
          ? buildSmallVariantGeneInfoHref(
              {
                ...anchorVariant,
                gene: group.gene || anchorVariant.gene,
                gene_id: group.gene_id || anchorVariant.gene_id,
              },
              familyId,
              projectId,
            )
          : null;
        const pairReviewTags = sortReviewTagKeys(group.review?.tags || [], tagMap);

        return (
          <article key={group.group_key} className="variant-card">
            <div className="variant-card-topbar">
              <div className="variant-card-topline">
                <span className="variant-card-toplabel">Compound het pair</span>
                <span className="variant-card-banner variant-card-banner--strong">
                  {groupGene || 'Grouped result'}
                </span>
              </div>
              {anchorVariant ? (
                <div className="variant-card-actions">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => onEditReview(anchorVariant)}
                  >
                    Review pair
                  </button>
                </div>
              ) : null}
            </div>

            <div className="variant-card-body">
              <div className="variant-card-column variant-card-column--primary">
                <div className="variant-card-review-panel">
                  <div className="variant-card-review-header variant-card-review-header--compact">
                    <p className="variant-card-section-title">Pair review</p>
                    {geneInfoHref && groupGene ? (
                      <Link to={geneInfoHref} className="variant-card-inline-link">
                        Gene context
                      </Link>
                    ) : null}
                  </div>
                  <div className="variant-compound-het-summary">
                    <div className="variant-compound-het-summary-row">
                      <span className="analysis-pill analysis-pill--muted">Gene</span>
                      <span>{groupGene || 'Not annotated'}</span>
                    </div>
                    <div className="variant-compound-het-summary-row">
                      <span className="analysis-pill analysis-pill--muted">Phase</span>
                      <span>{formatCompoundHetPhaseStatus(group.review?.phase_status)}</span>
                    </div>
                  </div>
                  {group.review?.classification ? (
                    <span
                      className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                        group.review.classification,
                      )}`}
                    >
                      {group.review.classification}
                    </span>
                  ) : null}
                  {pairReviewTags.length ? (
                    <div className="variant-card-chip-row">
                      {pairReviewTags.map((tagKey) => (
                        <span
                          key={`${group.group_key}:${tagKey}`}
                          className="variant-card-chip variant-card-chip--tag"
                          style={getReviewTagStyle(tagKey, tagMap)}
                          title={buildReviewTagTooltip({
                            tagKey,
                            tagMap,
                            tagMetadata: group.review?.tag_metadata,
                          })}
                        >
                          {tagMap[tagKey]?.label || tagKey}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <p className="variant-card-review-note">
                    {group.review?.note || 'No pair review saved.'}
                  </p>
                </div>
              </div>

              <div className="variant-card-column variant-card-column--middle">
                {group.variants.map((variant, index) => {
                  const { igvHref, viewHref } = buildSmallVariantNavigation({
                    variant,
                    familyId,
                    locationSearch,
                    projectId,
                  });
                  return (
                    <div key={variant._id} className="variant-card-note-box">
                      <div className="variant-card-review-header variant-card-review-header--compact">
                        <p className="variant-card-section-title">Variant {index + 1}</p>
                        <div className="variant-card-actions">
                          <Link to={igvHref} className="button-secondary">
                            IGV
                          </Link>
                          <Link to={viewHref} className="button-secondary">
                            View
                          </Link>
                        </div>
                      </div>
                      <p className="variant-card-subtitle">
                        {formatLocus(variant)} · {variant.ref || '—'} → {variant.alt || '—'}
                      </p>
                      <p className="table-subtle">
                        {variant.hgvsp || variant.hgvsc || formatTokenLabel(variant.effect)}
                      </p>
                      <div className="variant-card-chip-row">
                        <span
                          className={`variant-card-chip variant-card-chip--${getImpactTone(variant.impact)}`}
                        >
                          {variant.impact || 'Impact n/a'}
                        </span>
                        <span className="variant-card-chip variant-card-chip--neutral">
                          {formatTokenLabel(variant.effect)}
                        </span>
                        <span className="variant-card-chip variant-card-chip--neutral">
                          gnomAD {formatFrequency(variant.gnomad_af)}
                        </span>
                      </div>
                      <p className="table-subtle">
                        {buildCompactGenotypeText(variant, members) || 'No family genotypes available'}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
