import React from 'react';
import { Link } from 'react-router-dom';
import {
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  buildReviewTagTooltip,
  buildSmallVariantExternalLinks,
  buildSmallVariantGeneInfoHref,
  buildSmallVariantNavigation,
  formatFrequency,
  formatCompoundHetPhaseStatus,
  formatLocus,
  formatScore,
  formatTokenLabel,
  getClinvarHighlightTone,
  getClinvarTone,
  getFrequencyTone,
  getImpactTone,
  getReviewClassificationTone,
  getReviewTagStyle,
  sortReviewTagKeys,
} from './smallVariantResultUtils';

interface SmallVariantCardsProps {
  variants: SmallVariant[];
  members: FamilyMember[];
  familyId?: string;
  projectId?: string;
  locationSearch: string;
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion?: string;
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview: (variant: SmallVariant) => void;
  onToggleReviewTag: (variant: SmallVariant, tagKey: string) => Promise<void>;
}

export default function SmallVariantCards({
  variants,
  members,
  familyId,
  projectId,
  locationSearch,
  speciesName,
  assemblyName,
  assemblyVersion,
  tags,
  reviewIsPending = false,
  onEditReview,
  onToggleReviewTag,
}: SmallVariantCardsProps) {
  const tagMap = getTagDefinitionMap(tags);

  return (
    <div className="variant-card-list">
      {variants.map((variant) => {
        const { locus, igvHref, viewHref } = buildSmallVariantNavigation({
          variant,
          familyId,
          locationSearch,
          projectId,
        });
        const geneInfoHref = buildSmallVariantGeneInfoHref(variant, familyId, projectId);
        const externalLinks = buildSmallVariantExternalLinks({
          variant,
          speciesName,
          assemblyName,
          assemblyVersion,
        });
        const populationFrequencies = Object.entries(variant.population_frequencies || {}).filter(
          ([, value]) => typeof value === 'number' && Number.isFinite(value),
        );
        const additionalAnnotations = Object.entries(variant.annotation_extra || {}).filter(
          ([, value]) => value !== null && value !== '',
        );
        const scoreItems = [
          typeof variant.gene_pli === 'number'
            ? { label: 'pLI', value: formatScore(variant.gene_pli, 3) }
            : null,
          typeof variant.gene_missense_z === 'number'
            ? { label: 'Missense Z', value: formatScore(variant.gene_missense_z) }
            : null,
          variant.cadd_phred ? { label: 'CADD', value: formatScore(variant.cadd_phred) } : null,
          variant.revel ? { label: 'REVEL', value: formatScore(variant.revel) } : null,
          variant.spliceai_max
            ? { label: 'SpliceAI', value: formatScore(variant.spliceai_max) }
            : null,
          variant.sift ? { label: 'SIFT', value: variant.sift } : null,
          variant.polyphen ? { label: 'PolyPhen', value: variant.polyphen } : null,
          variant.lof_filter ? { label: 'LoF filter', value: variant.lof_filter } : null,
          variant.lof_flags ? { label: 'LoF flags', value: variant.lof_flags } : null,
        ].filter((item): item is { label: string; value: string } => Boolean(item));
        const frequencyItems = [
          typeof variant.gnomad_hom_count === 'number'
            ? { label: 'gnomAD hom', value: String(variant.gnomad_hom_count) }
            : null,
          ...populationFrequencies
            .filter(([label]) => label !== 'gnomad_af')
            .map(([label, value]) => ({
              label: label.replace(/_/g, ' '),
              value: formatFrequency(value),
            })),
        ].filter((item): item is { label: string; value: string } => Boolean(item));
        const transcriptItems = [
          { label: 'Transcript', value: variant.transcript_id || '—' },
          { label: 'Gene ID', value: variant.gene_id || '—' },
          { label: 'Biotype', value: variant.transcript_biotype || '—' },
          { label: 'Feature', value: variant.feature_type || '—' },
          { label: 'Exon / intron', value: variant.exon || variant.intron || '—' },
        ];
        const changeItems = [
          { label: 'Consequence', value: formatTokenLabel(variant.effect) },
          { label: 'HGVS.c', value: variant.hgvsc || '—' },
          { label: 'HGVS.p', value: variant.hgvsp || '—' },
          { label: 'Locus', value: locus },
          { label: 'dbSNP', value: variant.rsid || '—' },
          { label: 'Alleles', value: `${variant.ref || '—'} → ${variant.alt || '—'}` },
          {
            label: 'Type / source',
            value: `${variant.type}${variant.source ? ` · ${variant.source}` : ''}`,
          },
          { label: 'Phase set', value: String(variant.ps ?? '—') },
        ];
        const clinvarSummary = formatTokenLabel(variant.clinvar);
        const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
        const hasReviewTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.review);
        const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
        const visibleReviewTags = sortedReviewTags.filter(
          (tagKey) =>
            tagKey !== COLLABORATION_QUICK_TAGS.review &&
            tagKey !== COLLABORATION_QUICK_TAGS.excluded,
        );

        return (
          <article
            key={variant._id}
            className={`variant-card variant-card--clinvar-${getClinvarHighlightTone(
              variant.clinvar,
            )}${isExcluded ? ' variant-card--excluded' : ''}`}
          >
            <div className="variant-card-topbar">
              <div className="variant-card-topline">
                <span className="variant-card-toplabel">ClinVar</span>
                <span
                  className={`variant-card-banner variant-card-banner--${getClinvarTone(
                    variant.clinvar,
                  )}`}
                >
                  {clinvarSummary}
                </span>
              </div>
              <div className="variant-card-actions">
                <Link to={igvHref} className="button-secondary">
                  Open in IGV
                </Link>
                <Link to={viewHref} className="button-secondary">
                  Chromosome view
                </Link>
              </div>
            </div>

            <div className="variant-card-body">
              <div className="variant-card-column variant-card-column--primary">
                <div className="space-y-2">
                  <div className="variant-card-title-row">
                    {geneInfoHref ? (
                      <Link to={geneInfoHref} className="variant-card-title-link">
                        <h3 className="variant-card-title">
                          {variant.gene || variant.gene_id || 'Intergenic variant'}
                        </h3>
                      </Link>
                    ) : (
                      <h3 className="variant-card-title">
                        {variant.gene || variant.gene_id || 'Intergenic variant'}
                      </h3>
                    )}
                    <span className="variant-card-locus">{formatLocus(variant)}</span>
                  </div>
                  <p className="variant-card-subtitle">
                    {variant.hgvsp || variant.hgvsc || formatTokenLabel(variant.effect)}
                  </p>
                </div>

                <div className="variant-card-link-row">
                  {externalLinks.map((link) => (
                    <a
                      key={link.label}
                      href={link.href}
                      target="_blank"
                      rel="noreferrer"
                      className="variant-card-inline-link"
                    >
                      {link.label}
                    </a>
                  ))}
                </div>

                <div className="variant-card-chip-row">
                  <span
                    className={`variant-card-chip variant-card-chip--${getImpactTone(variant.impact)}`}
                  >
                    {variant.impact || 'Impact n/a'}
                  </span>
                  <span
                    className={`variant-card-chip variant-card-chip--${getFrequencyTone(
                      variant.gnomad_af,
                    )}`}
                  >
                    gnomAD {formatFrequency(variant.gnomad_af)}
                  </span>
                  <span className="variant-card-chip variant-card-chip--neutral">
                    {variant.type}
                  </span>
                  {variant.source ? (
                        <span className="variant-card-chip variant-card-chip--neutral">
                      {variant.source.toUpperCase()}
                    </span>
                  ) : null}
                  {variant.canonical ? (
                    <span className="variant-card-chip variant-card-chip--soft">Canonical</span>
                  ) : null}
                  {variant.mane_select ? (
                    <span className="variant-card-chip variant-card-chip--strong">MANE Select</span>
                  ) : null}
                  {variant.mane_plus_clinical ? (
                    <span className="variant-card-chip variant-card-chip--strong">
                      MANE Plus Clinical
                    </span>
                  ) : null}
                  {variant.lof ? (
                    <span className="variant-card-chip variant-card-chip--critical">
                      LoF {variant.lof}
                    </span>
                  ) : null}
                </div>

                <div className="variant-card-review-panel">
                  <div className="variant-card-review-header variant-card-review-header--compact">
                    <p className="variant-card-section-title">Review</p>
                    <div className="variant-card-review-actions">
                      <button
                        type="button"
                        className={`variant-quick-toggle${hasReviewTag ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending}
                        onClick={() => {
                          void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.review);
                        }}
                      >
                        Review
                      </button>
                      <button
                        type="button"
                        className={`variant-quick-toggle${isExcluded ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending}
                        onClick={() => {
                          void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.excluded);
                        }}
                      >
                        Exclude
                      </button>
                      <button
                        type="button"
                        className="variant-review-link"
                        onClick={() => onEditReview(variant)}
                      >
                        More tags
                      </button>
                    </div>
                  </div>
                  {visibleReviewTags.length ? (
                    <div className="variant-card-chip-row">
                      {visibleReviewTags.map((tagKey) => (
                        <span
                          key={tagKey}
                          className="variant-card-chip variant-card-chip--tag"
                          style={getReviewTagStyle(tagKey, tagMap)}
                          title={buildReviewTagTooltip({
                            tagKey,
                            tagMap,
                            tagMetadata: variant.review?.tag_metadata,
                          })}
                        >
                          {tagMap[tagKey]?.label || tagKey}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {variant.review?.classification &&
                  !visibleReviewTags.some((tagKey) => tagMap[tagKey]?.group === 'classification') ? (
                    <span
                      className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                        variant.review.classification,
                      )}`}
                    >
                      {variant.review.classification}
                    </span>
                  ) : null}
                  {variant.review?.compound_het ? (
                    <div className="variant-compound-het-summary">
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Pair</span>
                        <span>
                          {variant.review.compound_het.gene ||
                            variant.review.compound_het.gene_id ||
                            variant.gene ||
                            variant.gene_id ||
                            'Compound het group'}
                        </span>
                      </div>
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Partner</span>
                        <span>{variant.review.compound_het.partner_variant_ids.join(', ')}</span>
                      </div>
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Phase</span>
                        <span>
                          {formatCompoundHetPhaseStatus(variant.review.compound_het.phase_status)}
                        </span>
                      </div>
                      {variant.review.compound_het.classification ? (
                        <span
                          className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                            variant.review.compound_het.classification,
                          )}`}
                        >
                          Pair {variant.review.compound_het.classification}
                        </span>
                      ) : null}
                      {variant.review.compound_het.tags.length ? (
                        <div className="variant-card-chip-row">
                          {sortReviewTagKeys(variant.review?.compound_het?.tags || [], tagMap).map((tagKey) => (
                            <span
                              key={`compound-het-${tagKey}`}
                              className="variant-card-chip variant-card-chip--tag"
                              style={getReviewTagStyle(tagKey, tagMap)}
                              title={buildReviewTagTooltip({
                                tagKey,
                                tagMap,
                                tagMetadata: variant.review?.compound_het?.tag_metadata,
                              })}
                            >
                              {tagMap[tagKey]?.label || tagKey}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {variant.review.compound_het.note ? (
                        <p className="variant-card-review-note">
                          {variant.review.compound_het.note}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                  {variant.review?.note ? (
                    <p className="variant-card-review-note">{variant.review.note}</p>
                  ) : null}
                </div>

                <div className="variant-card-section">
                  <p className="variant-card-section-title">Transcript context</p>
                  <dl className="variant-card-detail-list">
                    {transcriptItems.map((item) => (
                      <div key={item.label} className="variant-card-detail-row">
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>

              <div className="variant-card-column variant-card-column--middle">
                <div className="variant-card-section">
                  <p className="variant-card-section-title">Variant summary</p>
                  <dl className="variant-card-detail-list">
                    {changeItems.map((item) => (
                      <div key={item.label} className="variant-card-detail-row">
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>

                {additionalAnnotations.length ? (
                  <div className="variant-card-note-box">
                    <p className="variant-card-section-title">Additional annotations</p>
                    <dl className="variant-card-detail-list">
                      {additionalAnnotations.map(([label, value]) => (
                        <div key={label} className="variant-card-detail-row">
                          <dt>{label.replace(/_/g, ' ')}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : null}
              </div>

              <div className="variant-card-column variant-card-column--metrics">
                <div className="variant-card-metric-panel">
                  <p className="variant-card-section-title">Scores and prediction</p>
                  {scoreItems.length ? (
                    <dl className="variant-card-stat-list">
                      {scoreItems.map((item) => (
                        <div key={item.label} className="variant-card-stat-row">
                          <dt>{item.label}</dt>
                          <dd>{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="variant-card-empty-note">No predictive scores imported.</p>
                  )}
                </div>

                <div className="variant-card-metric-panel">
                  <p className="variant-card-section-title">Population frequency</p>
                  {frequencyItems.length ? (
                    <dl className="variant-card-stat-list">
                      {frequencyItems.map((item) => (
                        <div key={item.label} className="variant-card-stat-row">
                          <dt>{item.label}</dt>
                          <dd>{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="variant-card-empty-note">
                      No population frequencies imported.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <div className="variant-card-section">
              <p className="variant-card-section-title">Family genotypes</p>
              <div className="variant-card-genotype-strip">
                {members.map((member) => {
                  const genotype = variant.genotypes.find(
                    (entry) => entry.sample === member.sample_id,
                  );
                  const gt = genotype?.gt || '—';
                  const depth = genotype?.dp;
                  const alleleDepths = genotype?.ad;
                  const alleleFrequencies = genotype?.af;
                  const refDepth =
                    alleleDepths && alleleDepths.length > 0 ? alleleDepths[0] : undefined;
                  const altDepths =
                    alleleDepths && alleleDepths.length > 1 ? alleleDepths.slice(1) : undefined;
                  const computedAfs =
                    alleleFrequencies && alleleFrequencies.length
                      ? alleleFrequencies
                      : typeof depth === 'number' && depth > 0 && altDepths?.length
                        ? altDepths
                            .map((value) =>
                              typeof value === 'number' ? value / depth : undefined,
                            )
                            .filter((value): value is number => typeof value === 'number')
                        : undefined;

                  return (
                    <div
                      key={member.sample_id}
                      className={`variant-card-genotype-tile${
                        member.affected ? ' variant-card-genotype-tile--affected' : ''
                      }${member.role === 'proband' ? ' variant-card-genotype-tile--proband' : ''}`}
                    >
                      <div className="variant-card-genotype-header">
                        <span className="variant-card-genotype-sample">{member.sample_id}</span>
                        <span
                          className={`table-chip ${
                            member.affected ? 'badge-chip--signature' : ''
                          }`}
                        >
                          {member.affected ? 'affected' : 'unaffected'}
                        </span>
                        <span className="table-chip">{member.role}</span>
                      </div>
                      <div className="variant-card-genotype-body">
                        <span className="variant-card-genotype-value">{gt}</span>
                        <span>DP {typeof depth === 'number' ? depth : '—'}</span>
                        <span>
                          AD{' '}
                          {typeof refDepth === 'number' || altDepths?.length
                            ? `${typeof refDepth === 'number' ? refDepth : '-'},${
                                altDepths ? altDepths.join(',') : '-'
                              }`
                            : '—'}
                        </span>
                        <span>
                          AF{' '}
                          {computedAfs?.length
                            ? computedAfs
                                .map((value) =>
                                  Number.isFinite(value) ? value.toFixed(2) : '-',
                                )
                                .join(', ')
                            : '—'}
                        </span>
                      </div>
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
