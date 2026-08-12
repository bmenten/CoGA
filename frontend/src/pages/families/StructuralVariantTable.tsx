import { useMemo } from 'react';
import { formatGt } from '../../lib/genotypes';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import {
  buildReviewTagTooltip,
  formatFrequency,
  getReviewClassificationTone,
  getReviewTagStyle,
  sortReviewTagKeys,
} from './smallVariantResultUtils';
import {
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  normalizeReviewClassification,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import type {
  StructuralSortableKeys,
  StructuralVariant,
  StructuralVariantFamilyMember,
  StructuralVariantGenotype,
} from './structuralVariantSearch';
import { SV_IGV_FLANK_BP, buildStructuralVariantNavigation } from './structuralVariantNavigation';
import VariantScoreCell from './VariantScoreCell';

interface StructuralVariantTableProps {
  familyId?: string;
  linkSearch: string;
  members: StructuralVariantFamilyMember[];
  projectId?: string;
  variants: StructuralVariant[];
  sortKey: StructuralSortableKeys;
  sortAsc: boolean;
  hasPriority?: boolean;
  visible: Record<string, boolean>;
  onSort: (key: StructuralSortableKeys) => void;
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview?: (variant: StructuralVariant) => void;
  onClassifyCnv?: (variant: StructuralVariant) => void;
  onToggleReviewTag?: (variant: StructuralVariant, tagKey: string) => Promise<void>;
}

function MetricTextList({
  genotypes,
  selector,
}: {
  genotypes: StructuralVariantGenotype[];
  selector: (genotype: StructuralVariantGenotype) => string | number | undefined;
}) {
  return (
    <div className="sv-table-metric-list">
      {genotypes.map((genotype, index) => {
        const value = selector(genotype);
        return (
          <span key={index}>{genotype.sample ? `${genotype.sample}: ${value ?? '—'}` : value ?? '—'}</span>
        );
      })}
    </div>
  );
}

const formatAnnotationText = (value?: string | number | boolean | string[]) => {
  if (Array.isArray(value)) return value.filter(Boolean).join(', ') || '—';
  if (value === undefined || value === null || value === '') return '—';
  return String(value);
};

const phenotypeSummary = (variant: StructuralVariant) =>
  variant.annotation_extra?.omim_phenotype ||
  variant.annotation_extra?.gencc_phenotype ||
  '—';

const compactText = (value: string, maxLength = 96) =>
  value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export const formatStructuralLength = (length?: number | null) => {
  if (typeof length !== 'number' || !Number.isFinite(length)) return '—';
  const sign = length < 0 ? '-' : '';
  const value = Math.abs(length);
  if (value >= 1_000_000) return `${sign}${(value / 1_000_000).toFixed(1)} Mb`;
  if (value > 100) return `${sign}${(value / 1_000).toFixed(1)} kb`;
  return `${length} bp`;
};

export default function StructuralVariantTable({
  familyId,
  linkSearch,
  members,
  projectId,
  variants,
  sortKey,
  sortAsc,
  hasPriority = false,
  visible,
  onSort,
  tags,
  reviewIsPending = false,
  onEditReview,
  onClassifyCnv,
  onToggleReviewTag,
}: StructuralVariantTableProps) {
  // Pure derivations of props — memoize so they aren't rebuilt on every unrelated re-render
  // (this table re-renders on review/sort state changes that don't touch tags/members).
  const tagMap = useMemo(() => getTagDefinitionMap(tags), [tags]);
  const memberMap = useMemo(
    () => new Map(members.map((member) => [member.sample_id, member])),
    [members],
  );
  return (
    <div className="analysis-results-card sv-results-table-card overflow-x-auto">
      <table className="analysis-table table-sticky sv-results-table">
        <thead>
          <tr>
            {hasPriority && (
              <th
                className="table-sortable"
                onClick={() => onSort('priority')}
                title="Exomiser-style priority: event class + gene constraint + rarity + segregation + gene–phenotype match"
              >
                Score {sortKey === 'priority' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.chr && (
              <th className="table-sortable" onClick={() => onSort('chr')}>
                Chr {sortKey === 'chr' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.start && (
              <th className="table-sortable" onClick={() => onSort('start')}>
                Start {sortKey === 'start' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.end && (
              <th className="table-sortable" onClick={() => onSort('end')}>
                End {sortKey === 'end' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.length && (
              <th className="table-sortable" onClick={() => onSort('length')}>
                Length {sortKey === 'length' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.type && (
              <th className="table-sortable" onClick={() => onSort('type')}>
                Type {sortKey === 'type' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.source && (
              <th className="table-sortable" onClick={() => onSort('source')}>
                Source {sortKey === 'source' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.gene && (
              <th className="table-sortable" onClick={() => onSort('gene')}>
                Gene {sortKey === 'gene' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.cytoband && (
              <th className="table-sortable" onClick={() => onSort('cytoband')}>
                Band {sortKey === 'cytoband' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.inheritance && (
              <th className="table-sortable" onClick={() => onSort('inheritance')}>
                Inheritance {sortKey === 'inheritance' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.control_af && (
              <th className="table-sortable" onClick={() => onSort('control_af')}>
                Control AF {sortKey === 'control_af' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.phenotype && (
              <th className="table-sortable" onClick={() => onSort('phenotype')}>
                Phenotype {sortKey === 'phenotype' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.region_flags && (
              <th className="table-sortable" onClick={() => onSort('region_flags')}>
                Regions {sortKey === 'region_flags' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.qual && (
              <th className="table-sortable" onClick={() => onSort('qual')}>
                QUAL {sortKey === 'qual' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.read_support && (
              <th className="table-sortable" onClick={() => onSort('read_support')}>
                Read support {sortKey === 'read_support' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.filter && (
              <th className="table-sortable" onClick={() => onSort('filter')}>
                Filter {sortKey === 'filter' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.remote_chr && (
              <th className="table-sortable" onClick={() => onSort('remote_chr')}>
                Remote chr {sortKey === 'remote_chr' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            {visible.remote_start && (
              <th className="table-sortable" onClick={() => onSort('remote_start')}>
                Remote start {sortKey === 'remote_start' && (sortAsc ? '▲' : '▼')}
              </th>
            )}
            <th>Review</th>
            {visible.genotypes && <th>Genotypes</th>}
            <th>IGV</th>
            <th>View</th>
          </tr>
        </thead>
        <tbody>
          {variants.length ? (
            variants.map((variant) => {
              const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
              const hasReviewTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.review);
              const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
              const hasReportTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.report);
              const visibleReviewTags = sortedReviewTags.filter(
                (tagKey) =>
                  tagKey !== COLLABORATION_QUICK_TAGS.review &&
                  tagKey !== COLLABORATION_QUICK_TAGS.excluded &&
                  tagKey !== COLLABORATION_QUICK_TAGS.report,
              );
              const normalizedClassification = normalizeReviewClassification(
                variant.review?.classification,
                variant.review?.tags,
              );

              return (
              <tr key={variant._id} className={isExcluded ? 'variant-table-row--excluded' : undefined}>
                {hasPriority && <VariantScoreCell variant={variant} />}
                {visible.chr && <td className="whitespace-nowrap">{variant.chr}</td>}
                {visible.start && <td className="table-mono">{variant.start}</td>}
                {visible.end && <td className="table-mono">{variant.end}</td>}
                {visible.length && (
                  <td className="table-mono">{formatStructuralLength(variant.length)}</td>
                )}
                {visible.type && (
                  <td className="whitespace-nowrap">{variant.type || '—'}</td>
                )}
                {visible.source && (
                  <td className="whitespace-nowrap">
                    {variant.source || <span className="table-empty">—</span>}
                  </td>
                )}
                {visible.gene && <td>{variant.gene || '—'}</td>}
                {visible.cytoband && (
                  <td className="whitespace-nowrap">{variant.annotation_extra?.cytoband || '—'}</td>
                )}
                {visible.inheritance && (
                  <td>
                    {variant.annotation_extra?.inheritance || <span className="table-empty">—</span>}
                  </td>
                )}
                {visible.control_af && (
                  <td className="table-mono">
                    {formatFrequency(variant.annotation_extra?.control_af)}
                  </td>
                )}
                {visible.phenotype && (
                  <td className="sv-table-phenotype">
                    <div>
                      <p title={phenotypeSummary(variant)}>{compactText(phenotypeSummary(variant))}</p>
                      {variant.annotation_extra?.hpo_terms ? (
                        <p className="table-subtle" title={formatAnnotationText(variant.annotation_extra.hpo_terms)}>
                          HPO {compactText(formatAnnotationText(variant.annotation_extra.hpo_terms), 64)}
                        </p>
                      ) : null}
                    </div>
                  </td>
                )}
                {visible.region_flags && (
                  <td>
                    {[
                      ...(variant.annotation_extra?.region_flags || []),
                      ...(isFiniteNumber(variant.gene_pli) ? [`pLI ${variant.gene_pli.toFixed(3)}`] : []),
                    ].join(', ') || '—'}
                  </td>
                )}
                {visible.qual && (
                  <td>
                    <MetricTextList
                      genotypes={variant.genotypes}
                      selector={(genotype) => genotype.qual}
                    />
                  </td>
                )}
                {visible.read_support && (
                  <td>
                    <MetricTextList
                      genotypes={variant.genotypes}
                      selector={(genotype) => genotype.read_support}
                    />
                  </td>
                )}
                {visible.filter && (
                  <td>
                    <MetricTextList
                      genotypes={variant.genotypes}
                      selector={(genotype) => genotype.filter}
                    />
                  </td>
                )}
                {visible.remote_chr && (
                  <td className="whitespace-nowrap">{variant.remote_chr ?? '—'}</td>
                )}
                {visible.remote_start && (
                  <td className="table-mono">{variant.remote_start ?? '—'}</td>
                )}
                <td className="sv-review-cell">
                  <div className="variant-table-review">
                    <div className="variant-table-review-actions">
                      <button
                        type="button"
                        className={`variant-quick-toggle${hasReviewTag ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending || !onToggleReviewTag}
                        onClick={() => {
                          void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.review);
                        }}
                      >
                        Review
                      </button>
                      <button
                        type="button"
                        className={`variant-quick-toggle${isExcluded ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending || !onToggleReviewTag}
                        onClick={() => {
                          void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.excluded);
                        }}
                      >
                        Exclude
                      </button>
                      <button
                        type="button"
                        className={`variant-quick-toggle${hasReportTag ? ' variant-quick-toggle--active' : ''}`}
                        disabled={reviewIsPending || !onToggleReviewTag}
                        title="Flag this SV for the family clinical report"
                        onClick={() => {
                          void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.report);
                        }}
                      >
                        Report
                      </button>
                      <button
                        type="button"
                        className="variant-review-link"
                        onClick={() => onClassifyCnv?.(variant)}
                      >
                        ACMG (CNV)
                      </button>
                      <button
                        type="button"
                        className="variant-review-link"
                        onClick={() => onEditReview?.(variant)}
                      >
                        More tags
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
                    {normalizedClassification &&
                    !visibleReviewTags.some((tagKey) => tagMap[tagKey]?.group === 'classification') ? (
                      <span
                        className={`table-chip table-chip--${getReviewClassificationTone(
                          normalizedClassification,
                        )}`}
                      >
                        {normalizedClassification}
                      </span>
                    ) : null}
                    {variant.review?.note ? (
                      <p className="variant-table-review-note">{variant.review.note}</p>
                    ) : null}
                  </div>
                </td>
                {visible.genotypes && (
                  <td>
                    <div className="variant-table-genotypes">
                      {variant.genotypes.map((genotype, index) => {
                        const member = genotype.sample ? memberMap.get(genotype.sample) : undefined;
                        return (
                        <div
                          key={index}
                          className={`variant-table-genotype-item${
                            member?.affected ? ' variant-table-genotype-item--affected' : ''
                          }${member?.role === 'proband' ? ' variant-table-genotype-item--proband' : ''}`}
                        >
                          <span className="variant-table-genotype-sample">
                          {genotype.sample
                            ? genotype.sample
                            : 'Sample'}
                          </span>
                          <span className="variant-table-genotype-value">{formatGt(genotype.gt)}</span>
                        </div>
                        );
                      })}
                    </div>
                  </td>
                )}
                <td className="whitespace-nowrap">
                  {(() => {
                    const { igvLocus: locusA, igvHref: hrefA } = buildStructuralVariantNavigation({
                      familyId,
                      linkSearch,
                      projectId,
                      variant,
                    });
                    const backPath = `/families/${familyId}/structural-variants${linkSearch}`;
                    const hasRemote = !!variant.remote_chr && !!variant.remote_start;
                    const remoteChr = variant.remote_chr?.startsWith('chr')
                      ? variant.remote_chr
                      : variant.remote_chr
                        ? `chr${variant.remote_chr}`
                        : '';
                    // The partner side of a BND is a single breakpoint rather than a
                    // span, so it gets the same flank as the primary locus — otherwise
                    // IGV opens on a 1 bp window with no sequence context around it.
                    const locusB = hasRemote
                      ? `${remoteChr}:${Math.max(1, variant.remote_start! - SV_IGV_FLANK_BP)}-${
                          variant.remote_start! + SV_IGV_FLANK_BP
                        }`
                      : '';
                    const hrefB = hasRemote
                      ? `/families/${familyId}/igv?locus=${encodeURIComponent(
                          locusB,
                        )}${projectId ? `&project_id=${projectId}` : ''}&back_path=${encodeURIComponent(
                          backPath,
                        )}`
                      : '';
                    return (
                      <div className="inline-actions">
                        <GenomeWorkspaceLink to={hrefA} className="table-link" label={`Open IGV at ${locusA}`}>
                          IGV
                        </GenomeWorkspaceLink>
                        {hasRemote && (
                          <GenomeWorkspaceLink to={hrefB} className="table-link" label={`Open IGV at ${locusB}`}>
                            IGV
                          </GenomeWorkspaceLink>
                        )}
                      </div>
                    );
                  })()}
                </td>
                <td className="whitespace-nowrap">
                  <GenomeWorkspaceLink
                    className="table-link"
                    to={buildStructuralVariantNavigation({ familyId, linkSearch, projectId, variant }).viewHref}
                    label={`Open chromosome view around ${variant.chr}:${variant.start}-${variant.end}`}
                  >
                    View
                  </GenomeWorkspaceLink>
                </td>
              </tr>
              );
            })
          ) : (
            <tr>
              <td colSpan={hasPriority ? 19 : 18}>
                <p className="table-empty">No structural variants match the current search.</p>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
