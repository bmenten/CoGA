import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  buildCompactGenotypeSummary,
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  buildReviewTagTooltip,
  buildSmallVariantGeneInfoHref,
  buildSmallVariantNavigation,
  formatCompoundHetPhaseStatus,
  getClinvarHighlightTone,
  getReviewClassificationTone,
  getReviewTagStyle,
  sortReviewTagKeys,
  sortSmallVariants,
  type TableSortKey,
} from './smallVariantResultUtils';
import VariantScoreCell from './VariantScoreCell';
import SvSecondHitBadge from './SvSecondHitBadge';

interface SmallVariantTableProps {
  variants: SmallVariant[];
  members: FamilyMember[];
  familyId?: string;
  projectId?: string;
  locationSearch: string;
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview: (variant: SmallVariant) => void;
  onAcmgClassify: (variant: SmallVariant) => void;
  onToggleReviewTag: (variant: SmallVariant, tagKey: string) => Promise<void>;
}

const MAX_ALLELE_DISPLAY_LENGTH = 48;

const formatNiptPct = (value?: number | null): string =>
  value == null ? '—' : `${(value * 100).toFixed(1)}%`;

const formatAlleleDisplay = (value?: string) => {
  const allele = String(value || '').trim();
  if (!allele) return '—';
  if (allele.length <= MAX_ALLELE_DISPLAY_LENGTH) return allele;
  return `${allele.slice(0, 30)}…${allele.slice(-12)}`;
};

const AlleleCell = ({ value }: { value?: string }) => {
  const allele = String(value || '').trim();
  if (!allele) {
    return <span className="table-empty">—</span>;
  }
  return (
    <span className="variant-allele-value" title={allele}>
      {formatAlleleDisplay(allele)}
    </span>
  );
};

export default function SmallVariantTable({
  variants,
  members,
  familyId,
  projectId,
  locationSearch,
  tags,
  reviewIsPending = false,
  onEditReview,
  onAcmgClassify,
  onToggleReviewTag,
}: SmallVariantTableProps) {
  const [tableSortKey, setTableSortKey] = useState<TableSortKey>('position');
  const [tableSortAsc, setTableSortAsc] = useState(true);
  const tagMap = useMemo(() => getTagDefinitionMap(tags), [tags]);

  const hasPriority = useMemo(
    () => variants.some((variant) => variant.priority),
    [variants],
  );
  const hasNipt = useMemo(() => variants.some((variant) => variant.nipt), [variants]);
  // When the backend returns prioritized results, default to the priority ranking.
  useEffect(() => {
    if (hasPriority) {
      setTableSortKey('priority');
      setTableSortAsc(false);
    }
  }, [hasPriority]);

  const sortedVariants = useMemo(
    () => sortSmallVariants(variants, tableSortKey, tableSortAsc),
    [tableSortAsc, tableSortKey, variants],
  );

  const handleTableSort = (nextKey: TableSortKey) => {
    if (tableSortKey === nextKey) {
      setTableSortAsc((current) => !current);
      return;
    }

    setTableSortKey(nextKey);
    setTableSortAsc(true);
  };

  const getSortIndicator = (key: TableSortKey) =>
    tableSortKey === key ? (tableSortAsc ? '▲' : '▼') : '';

  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky small-variant-table">
        <colgroup>
          {hasPriority && <col className="small-variant-col-impact" />}
          {hasNipt && <col className="small-variant-col-effect" />}
          <col className="small-variant-col-chr" />
          <col className="small-variant-col-position" />
          <col className="small-variant-col-position" />
          <col className="small-variant-col-gene" />
          <col className="small-variant-col-allele" />
          <col className="small-variant-col-allele" />
          <col className="small-variant-col-impact" />
          <col className="small-variant-col-effect" />
          <col className="small-variant-col-review" />
          <col className="small-variant-col-genotypes" />
          <col className="small-variant-col-action" />
          <col className="small-variant-col-view" />
        </colgroup>
        <thead>
          <tr>
            {hasPriority && (
              <th
                className="table-sortable"
                onClick={() => handleTableSort('priority')}
                title="Exomiser-style priority: variant impact + rarity + segregation + gene–phenotype match"
              >
                Score {getSortIndicator('priority')}
              </th>
            )}
            {hasNipt && <th>NIPT</th>}
            <th className="table-sortable" onClick={() => handleTableSort('position')}>
              Chr {getSortIndicator('position')}
            </th>
            <th className="table-sortable" onClick={() => handleTableSort('position')}>
              Start {getSortIndicator('position')}
            </th>
            <th>End</th>
            <th className="table-sortable" onClick={() => handleTableSort('gene')}>
              Gene {getSortIndicator('gene')}
            </th>
            <th>Ref</th>
            <th>Alt</th>
            <th className="table-sortable" onClick={() => handleTableSort('impact')}>
              Impact {getSortIndicator('impact')}
            </th>
            <th>Effect</th>
            <th>Review</th>
            <th>Genotypes</th>
            <th>IGV</th>
            <th className="small-variant-view-cell">View</th>
          </tr>
        </thead>
        <tbody>
          {sortedVariants.map((variant) => {
            const { locus, igvHref, viewHref } = buildSmallVariantNavigation({
              variant,
              familyId,
              locationSearch,
              projectId,
            });
            const geneInfoHref = buildSmallVariantGeneInfoHref(variant, familyId, projectId);
            const compactGenotypes = buildCompactGenotypeSummary(variant, members);
            const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
            const hasReviewTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.review);
            const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
            const isReported = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.report);
            const visibleReviewTags = sortedReviewTags.filter(
              (tagKey) =>
                tagKey !== COLLABORATION_QUICK_TAGS.review &&
                tagKey !== COLLABORATION_QUICK_TAGS.excluded &&
                tagKey !== COLLABORATION_QUICK_TAGS.report,
            );

            return (
              <tr
                key={variant._id}
                className={`variant-table-row--clinvar-${getClinvarHighlightTone(variant.clinvar)}${
                  isExcluded ? ' variant-table-row--excluded' : ''
                }`}
              >
                {hasPriority && <VariantScoreCell variant={variant} />}
                {hasNipt && (
                  <td>
                    {variant.nipt ? (
                      <div className="nipt-table-cell">
                        <span className="nipt-table-category">
                          {variant.nipt.category != null ? `Cat ${variant.nipt.category}` : 'Unclassified'}
                        </span>
                        <span className="nipt-table-detail">{variant.nipt.category_label}</span>
                        <span className="nipt-table-detail">
                          {variant.nipt.maternal_state} → {variant.nipt.fetal_inheritance}
                        </span>
                        <span className="nipt-table-detail">
                          VAF {formatNiptPct(variant.nipt.observed_vaf)} /{' '}
                          {formatNiptPct(variant.nipt.expected_vaf)} · conf{' '}
                          {variant.nipt.confidence.toFixed(2)}
                        </span>
                      </div>
                    ) : (
                      '—'
                    )}
                  </td>
                )}
                <td>{variant.chr}</td>
                <td className="table-mono">{variant.start}</td>
                <td className="table-mono">{variant.end}</td>
                <td>
                  {geneInfoHref && variant.gene ? (
                    <Link to={geneInfoHref} className="table-link">
                      {variant.gene}
                    </Link>
                  ) : (
                    variant.gene || '—'
                  )}
                  {variant.sv_second_hit ? (
                    <>
                      {' '}
                      <SvSecondHitBadge hit={variant.sv_second_hit} />
                    </>
                  ) : null}
                </td>
                <td className="table-mono variant-allele-cell">
                  <AlleleCell value={variant.ref} />
                </td>
                <td className="table-mono variant-allele-cell">
                  <AlleleCell value={variant.alt} />
                </td>
                <td>{variant.impact || '—'}</td>
                <td>{variant.effect || '—'}</td>
                <td>
                  <div className="variant-table-review">
                    <div className="variant-table-review-actions">
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
                        className={`variant-quick-toggle${isReported ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending}
                        onClick={() => {
                          void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.report);
                        }}
                      >
                        Report
                      </button>
                      <button
                        type="button"
                        className="variant-review-link"
                        onClick={() => onEditReview(variant)}
                      >
                        Tags &amp; notes
                      </button>
                      <button
                        type="button"
                        className="variant-review-link"
                        onClick={() => onAcmgClassify(variant)}
                      >
                        ACMG classify
                      </button>
                    </div>
                    {visibleReviewTags.length ? (
                      <div className="table-chip-list">
                        {visibleReviewTags.map((tagKey) => (
                          <span
                            key={tagKey}
                            className="table-chip table-chip--tag"
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
                        className={`table-chip table-chip--${getReviewClassificationTone(
                          variant.review.classification,
                        )}`}
                      >
                        {variant.review.classification}
                      </span>
                    ) : null}
                    {variant.review?.compound_het ? (
                      <div className="variant-table-review-group">
                        <span className="table-chip table-chip--accent">Compound het pair</span>
                        <p className="table-subtle">
                          Partner variants: {variant.review.compound_het.partner_variant_ids.join(', ')}
                        </p>
                        <p className="table-subtle">
                          {formatCompoundHetPhaseStatus(variant.review.compound_het.phase_status)}
                        </p>
                        {variant.review.compound_het.classification ? (
                          <span
                            className={`table-chip table-chip--${getReviewClassificationTone(
                              variant.review.compound_het.classification,
                            )}`}
                          >
                            {variant.review.compound_het.classification}
                          </span>
                        ) : null}
                        {variant.review.compound_het.tags.length ? (
                          <div className="table-chip-list">
                            {sortReviewTagKeys(variant.review?.compound_het?.tags || [], tagMap).map((tagKey) => (
                              <span
                                key={`compound-het-${tagKey}`}
                                className="table-chip table-chip--tag"
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
                          <p className="variant-table-review-note">
                            {variant.review.compound_het.note}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                    {variant.review?.note ? (
                      <p className="variant-table-review-note">{variant.review.note}</p>
                    ) : null}
                  </div>
                </td>
                <td>
                  {compactGenotypes.length ? (
                    <div className="variant-table-genotypes">
                      {compactGenotypes.map((entry) => (
                        <div
                          key={entry.sampleId}
                          className={`variant-table-genotype-item${
                            entry.affected ? ' variant-table-genotype-item--affected' : ''
                          }${entry.role === 'proband' ? ' variant-table-genotype-item--proband' : ''
                          }`}
                        >
                          <span className="variant-table-genotype-sample">{entry.sampleId}</span>
                          <span className="variant-table-genotype-value">{entry.gt}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <span className="table-empty">—</span>
                  )}
                </td>
                <td>
                  <Link to={igvHref} className="table-link" title={`Open IGV at ${locus}`}>
                    IGV
                  </Link>
                </td>
                <td className="small-variant-view-cell">
                  <Link
                    to={viewHref}
                    className="table-link"
                    title={`Open chromosome view around ${variant.chr}:${variant.start}-${variant.end}`}
                  >
                    View
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
