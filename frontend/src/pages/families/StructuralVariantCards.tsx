import { Link } from 'react-router-dom';
import { formatGt } from '../../lib/genotypes';
import {
  buildReviewTagTooltip,
  formatFrequency,
  formatLocus,
  formatScore,
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
  StructuralVariant,
  StructuralVariantFamilyMember,
  StructuralVariantGenotype,
} from './structuralVariantSearch';
import { formatStructuralLength } from './StructuralVariantTable';

interface StructuralVariantCardsProps {
  familyId?: string;
  linkSearch: string;
  members: StructuralVariantFamilyMember[];
  projectId?: string;
  variants: StructuralVariant[];
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview?: (variant: StructuralVariant) => void;
  onToggleReviewTag?: (variant: StructuralVariant, tagKey: string) => Promise<void>;
}

const compactText = (value?: string, maxLength = 96) => {
  if (!value) return '—';
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
};

const parseHpoTerms = (value?: string | number | boolean | string[]) => {
  if (!value) return [];
  const rawTerms = Array.isArray(value)
    ? value.flatMap((entry) => String(entry).split(/[;,|]/))
    : String(value).split(/[;,|]/);
  return rawTerms.map((term) => term.trim()).filter(Boolean);
};

const buildHpoHref = (terms: string[], familyId?: string) => {
  const params = new URLSearchParams();
  terms.forEach((term) => params.append('term', term));
  if (familyId) params.set('family_id', familyId);
  return `/hpo?${params.toString()}`;
};

const orderGenotypesForCard = (
  members: StructuralVariantFamilyMember[],
  genotypes: StructuralVariantGenotype[],
) => {
  const genotypeMap = new Map(
    genotypes
      .filter((entry) => entry.sample)
      .map((entry) => [entry.sample as string, entry]),
  );
  const orderedMembers = [...members].sort((left, right) => {
    if (left.affected !== right.affected) return left.affected ? -1 : 1;
    if (left.role === 'proband' && right.role !== 'proband') return -1;
    if (right.role === 'proband' && left.role !== 'proband') return 1;
    return left.sample_id.localeCompare(right.sample_id);
  });
  const seen = new Set<string>();
  const memberEntries = orderedMembers.map((member) => {
    seen.add(member.sample_id);
    return {
      key: member.sample_id,
      sampleId: member.sample_id,
      member,
      genotype: genotypeMap.get(member.sample_id),
    };
  });
  const extraEntries = genotypes
    .filter((entry) => !entry.sample || !seen.has(entry.sample))
    .map((entry, index) => ({
      key: `${entry.sample || 'sample'}-${index}`,
      sampleId: entry.sample || 'Sample',
      member: null,
      genotype: entry,
    }));
  return [...memberEntries, ...extraEntries];
};

const buildStructuralVariantNavigation = ({
  familyId,
  linkSearch,
  projectId,
  variant,
}: {
  familyId?: string;
  linkSearch: string;
  projectId?: string;
  variant: StructuralVariant;
}) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  const locus = `${chr}:${Math.max(1, variant.start)}-${Math.max(variant.start, variant.end)}`;
  const backPath = `/families/${familyId}/structural-variants${linkSearch}`;
  const igvHref = `/families/${familyId}/igv?locus=${encodeURIComponent(locus)}${
    projectId ? `&project_id=${projectId}` : ''
  }&back_path=${encodeURIComponent(backPath)}`;
  const viewHref = `/families/${familyId}/chromosome/${variant.chr.replace(/^chr/, '')}?start=${Math.max(
    0,
    variant.start - 1_000_000,
  )}&end=${variant.end + 1_000_000}${linkSearch ? `&${linkSearch.slice(1)}` : ''}${
    projectId ? `&project_id=${projectId}` : ''
  }`;
  return { locus, igvHref, viewHref };
};

export default function StructuralVariantCards({
  familyId,
  linkSearch,
  members,
  projectId,
  variants,
  tags,
  reviewIsPending = false,
  onEditReview,
  onToggleReviewTag,
}: StructuralVariantCardsProps) {
  const tagMap = getTagDefinitionMap(tags);
  if (!variants.length) {
    return (
      <div className="variant-results-empty">
        <p className="table-empty">No structural variants match the current search.</p>
      </div>
    );
  }

  return (
    <div className="variant-card-list">
      {variants.map((variant) => {
        const extra = variant.annotation_extra || {};
        const { locus, igvHref, viewHref } = buildStructuralVariantNavigation({
          familyId,
          linkSearch,
          projectId,
          variant,
        });
        const phenotype = extra.omim_phenotype || extra.gencc_phenotype || 'No disease annotation';
        const controlFrequency =
          typeof extra.control_af === 'number'
            ? extra.control_af
            : typeof extra.population_af === 'number'
              ? extra.population_af
              : undefined;
        const regionFlags = Array.isArray(extra.region_flags) ? extra.region_flags : [];
        const populationFrequencies = Object.entries(extra.population_frequencies || {}).filter(
          ([, value]) => typeof value === 'number' && Number.isFinite(value),
        );
        const hpoTerms = parseHpoTerms(extra.hpo_terms);
        const visibleHpoTerms = hpoTerms.slice(0, 3);
        const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
        const hasReviewTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.review);
        const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
        const visibleReviewTags = sortedReviewTags.filter(
          (tagKey) =>
            tagKey !== COLLABORATION_QUICK_TAGS.review &&
            tagKey !== COLLABORATION_QUICK_TAGS.excluded,
        );
        const normalizedClassification = normalizeReviewClassification(
          variant.review?.classification,
          variant.review?.tags,
        );

        return (
          <article
            key={variant._id}
            className={`variant-card${isExcluded ? ' variant-card--excluded' : ''}`}
          >
            <div className="variant-card-topbar">
              <div className="variant-card-topline">
                <span className="variant-card-toplabel">Needlr SV</span>
                <span className="variant-card-banner variant-card-banner--neutral">
                  {variant.type}
                </span>
                <span className="variant-card-banner variant-card-banner--strong">
                  {formatStructuralLength(variant.length)}
                </span>
                <span className="variant-card-locus">{locus}</span>
                {extra.cytoband ? (
                  <span className="variant-card-banner variant-card-banner--neutral">
                    {extra.cytoband}
                  </span>
                ) : null}
                {extra.inheritance ? (
                  <span className="variant-card-banner variant-card-banner--strong">
                    {extra.inheritance}
                  </span>
                ) : null}
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

            <div className="variant-card-body sv-card-body">
              <div className="variant-card-column variant-card-column--primary">
                <div className="space-y-2">
                  <div className="variant-card-title-row">
                    <h3 className="variant-card-title">{variant.gene || 'Intergenic SV'}</h3>
                  </div>
                  <p className="variant-card-subtitle">{phenotype}</p>
                </div>

                <div className="variant-card-chip-row">
                  <span className="variant-card-chip variant-card-chip--neutral">
                    Source {variant.source || '—'}
                  </span>
                  <span className="variant-card-chip variant-card-chip--soft">
                    Control AF {formatFrequency(controlFrequency)}
                  </span>
                  {typeof variant.gene_pli === 'number' ? (
                    <span className="variant-card-chip variant-card-chip--strong">
                      pLI {formatScore(variant.gene_pli, 3)}
                    </span>
                  ) : null}
                  {regionFlags.map((flag) => (
                    <span key={flag} className="variant-card-chip variant-card-chip--neutral">
                      {flag}
                    </span>
                  ))}
                </div>
                <div className="variant-card-section">
                  <p className="variant-card-section-title">Variant summary</p>
                  <dl className="variant-card-detail-list">
                    {[
                      ['Type', variant.type || '—'],
                      ['Length', formatStructuralLength(variant.length)],
                      ['Remote', variant.remote_chr ? `${variant.remote_chr}:${variant.remote_start ?? '—'}` : '—'],
                      ['Source', variant.source || '—'],
                    ].map(([label, value]) => (
                      <div key={label} className="variant-card-detail-row">
                        <dt>{label}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>

              <div className="variant-card-column variant-card-column--middle">
                <div className="variant-card-review-panel">
                  <div className="variant-card-review-header variant-card-review-header--compact">
                    <p className="variant-card-section-title">Review</p>
                    <div className="variant-card-review-actions">
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
                        className="variant-review-link"
                        onClick={() => onEditReview?.(variant)}
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
                  {normalizedClassification &&
                  !visibleReviewTags.some((tagKey) => tagMap[tagKey]?.group === 'classification') ? (
                      <span
                        className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                          normalizedClassification,
                        )}`}
                      >
                        {normalizedClassification}
                      </span>
                  ) : null}
                  {variant.review?.note ? (
                    <p className="variant-card-review-note">{variant.review.note}</p>
                  ) : null}
                </div>

                <div className="variant-card-section">
                  <p className="variant-card-section-title">Disease context</p>
                <dl className="variant-card-detail-list">
                  {[
                    ['OMIM', extra.omim_phenotype || '—'],
                    ['GenCC', extra.gencc_phenotype || '—'],
                    ['GenCC support', extra.gencc_support || '—'],
                    ['MOI', extra.omim_moi || extra.gencc_moi || '—'],
                  ].map(([label, value]) => (
                    <div key={label} className="variant-card-detail-row">
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                  <div className="variant-card-detail-row">
                    <dt>HPO</dt>
                    <dd>
                      {hpoTerms.length ? (
                        <Link
                          to={buildHpoHref(hpoTerms, familyId)}
                          className="variant-card-inline-link"
                          title={hpoTerms.join(', ')}
                        >
                          {visibleHpoTerms.join(', ')}
                          {hpoTerms.length > visibleHpoTerms.length
                            ? ` +${hpoTerms.length - visibleHpoTerms.length}`
                            : ''}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </dd>
                  </div>
                  <div className="variant-card-detail-row">
                    <dt>Control support</dt>
                    <dd title={String(extra.control_support || '')}>
                      {compactText(extra.control_support, 72)}
                    </dd>
                  </div>
                </dl>
                </div>
              </div>

              <div className="variant-card-column variant-card-column--metrics">
                <div className="variant-card-section">
                  <p className="variant-card-section-title">Population</p>
                  <dl className="variant-card-stat-list">
                    {populationFrequencies.length ? (
                      populationFrequencies.slice(0, 8).map(([label, value]) => (
                        <div key={label} className="variant-card-stat-row">
                          <dt>{label.replace(/_/g, ' ')}</dt>
                          <dd>{formatFrequency(value)}</dd>
                        </div>
                      ))
                    ) : (
                      <div className="variant-card-stat-row">
                        <dt>Frequency</dt>
                        <dd>—</dd>
                      </div>
                    )}
                  </dl>
                </div>
              </div>
            </div>

            <div className="variant-card-section">
              <p className="variant-card-section-title">Family genotypes</p>
              <div className="variant-card-genotype-strip sv-card-genotype-strip">
                {orderGenotypesForCard(members, variant.genotypes).map(({ key, sampleId, member, genotype }) => (
                  <div
                    key={key}
                    className={`variant-card-genotype-tile${
                      member?.affected ? ' variant-card-genotype-tile--affected' : ''
                    }${member?.role === 'proband' ? ' variant-card-genotype-tile--proband' : ''}`}
                  >
                    <div className="variant-card-genotype-header">
                      <span className="variant-card-genotype-sample">{sampleId}</span>
                      {member ? (
                        <>
                          <span className={`table-chip ${member.affected ? 'badge-chip--signature' : ''}`}>
                            {member.affected ? 'affected' : 'unaffected'}
                          </span>
                          <span className="table-chip">{member.role}</span>
                        </>
                      ) : null}
                    </div>
                    <div className="variant-card-genotype-body">
                      <span className="variant-card-genotype-value">
                        {genotype ? formatGt(genotype.gt) : '—'}
                      </span>
                      <span>QUAL {genotype?.qual ?? '—'}</span>
                      <span>Reads {genotype?.read_support ?? '—'}</span>
                      <span>Filter {genotype?.filter || '—'}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
