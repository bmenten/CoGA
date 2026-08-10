import { Link } from 'react-router';
import { formatGt } from '../../lib/genotypes';
import {
  buildReviewTagTooltip,
  formatFrequency,
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
} from './structuralVariantSearch';
import { buildStructuralVariantNavigation } from './structuralVariantNavigation';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import { formatStructuralLength } from './StructuralVariantTable';
import VariantPriorityBlock from './VariantPriorityBlock';

interface StructuralVariantCardsProps {
  familyId?: string;
  linkSearch: string;
  members: StructuralVariantFamilyMember[];
  projectId?: string;
  variants: StructuralVariant[];
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview?: (variant: StructuralVariant) => void;
  onClassifyCnv?: (variant: StructuralVariant) => void;
  onToggleReviewTag?: (variant: StructuralVariant, tagKey: string) => Promise<void>;
}

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

const genotypeZygosity = (gt?: string): 'na' | 'ref' | 'hom' | 'het' => {
  if (!gt) return 'na';
  const normalized = gt.replace(/\|/g, '/');
  if (normalized === './.' || normalized === 'absent') return 'na';
  if (normalized === '0/0') return 'ref';
  if (normalized === '1/1') return 'hom';
  return 'het';
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
  onClassifyCnv,
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
        const regionFlags = Array.isArray(extra.region_flags) ? extra.region_flags : [];
        const populationFrequencies = Object.entries(extra.population_frequencies || {}).filter(
          ([, value]) => typeof value === 'number' && Number.isFinite(value),
        );
        const hpoTerms = parseHpoTerms(extra.hpo_terms);
        const visibleHpoTerms = hpoTerms.slice(0, 3);
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
        const normalizedClassification = normalizeReviewClassification(
          variant.review?.classification,
          variant.review?.tags,
        );

        const freqRows = [
          { label: 'Control', value: formatFrequency(extra.control_af) },
          { label: 'Population', value: formatFrequency(extra.population_af) },
          ...populationFrequencies
            .slice(0, 6)
            .map(([label, value]) => ({ label: label.replace(/_/g, ' '), value: formatFrequency(value) })),
        ];

        const gtMembers = members.length
          ? members
          : variant.genotypes
              .map((genotype) => ({
                sample_id: genotype.sample || 'Sample',
                role: '',
                sex: undefined,
                affected: false,
              }) as unknown as StructuralVariantFamilyMember);

        return (
          <article
            key={variant._id}
            className={`variant-card${isExcluded ? ' variant-card--excluded' : ''}`}
          >
            <div className="variant-card-head">
              <div className="variant-card-headline">
                <span className="variant-card-gene">{variant.gene || 'Intergenic SV'}</span>
                <span className="variant-card-locus">{locus}</span>
                <span className="variant-card-subtitle">{phenotype}</span>
              </div>
              <div className="variant-card-headtags">
                <span className="variant-card-chip variant-card-chip--impact">{variant.type || 'SV'}</span>
                <span className="variant-card-chip variant-card-chip--soft">
                  {formatStructuralLength(variant.length)}
                </span>
                {extra.inheritance ? (
                  <span className="variant-card-chip variant-card-chip--strong">{extra.inheritance}</span>
                ) : null}
                {normalizedClassification ? (
                  <span
                    className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                      normalizedClassification,
                    )}`}
                  >
                    {normalizedClassification}
                  </span>
                ) : null}
              </div>
            </div>

            {variant.priority ? <VariantPriorityBlock priority={variant.priority} /> : null}

            {visibleReviewTags.length ? (
              <div className="variant-card-chip-row variant-card-tag-row">
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

            <div className="variant-card-cols">
              <section className="variant-card-col">
                <p className="variant-card-col-title">Variant &amp; genotypes</p>
                <div className="variant-card-chip-row">
                  <span className="variant-card-chip variant-card-chip--neutral">
                    Source {variant.source || '—'}
                  </span>
                  {typeof variant.gene_pli === 'number' ? (
                    <span className="variant-card-chip variant-card-chip--strong">
                      pLI {formatScore(variant.gene_pli, 3)}
                    </span>
                  ) : null}
                  {regionFlags.map((flag) => (
                    <span key={flag} className="variant-card-chip variant-card-chip--soft">
                      {flag}
                    </span>
                  ))}
                </div>
                <dl className="variant-card-mini-dl">
                  <div>
                    <dt>Type</dt>
                    <dd>{variant.type || '—'}</dd>
                  </div>
                  <div>
                    <dt>Length</dt>
                    <dd>{formatStructuralLength(variant.length)}</dd>
                  </div>
                  <div>
                    <dt>Cytoband</dt>
                    <dd>{extra.cytoband || '—'}</dd>
                  </div>
                  <div>
                    <dt>Remote</dt>
                    <dd>
                      {variant.remote_chr
                        ? `${variant.remote_chr}:${variant.remote_start ?? '—'}`
                        : '—'}
                    </dd>
                  </div>
                </dl>
                {gtMembers.length ? (
                  <div className="variant-card-gtlist">
                    <div className="variant-card-gthead">
                      <span>Sample</span>
                      <span>Role</span>
                      <span>GT</span>
                      <span>QUAL</span>
                      <span>Reads</span>
                      <span>Filter</span>
                    </div>
                    {gtMembers.map((member) => {
                      const genotype = variant.genotypes.find(
                        (entry) => entry.sample === member.sample_id,
                      );
                      const gt = genotype ? formatGt(genotype.gt) : '—';
                      const zygosity = genotypeZygosity(genotype?.gt);
                      const sexSymbol =
                        member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚥';
                      return (
                        <div
                          key={member.sample_id}
                          className={`variant-card-gtline variant-card-gtline--${zygosity}${
                            member.affected ? ' is-affected' : ''
                          }${member.role === 'proband' ? ' is-proband' : ''}`}
                        >
                          <span className="variant-card-gtline-sample">
                            <span
                              className={`variant-card-gtline-sex variant-card-gtline-sex--${
                                member.sex || 'und'
                              }`}
                              title={member.sex || 'unknown sex'}
                            >
                              {sexSymbol}
                            </span>
                            {member.sample_id}
                          </span>
                          <span className="variant-card-gtline-role">{member.role || '—'}</span>
                          <span className="variant-card-gtline-gt">{gt}</span>
                          <span className="variant-card-gtline-metric">{genotype?.qual ?? '—'}</span>
                          <span className="variant-card-gtline-metric">
                            {genotype?.read_support ?? '—'}
                          </span>
                          <span className="variant-card-gtline-metric">{genotype?.filter || '—'}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </section>

              <section className="variant-card-col">
                <p className="variant-card-col-title">Frequencies</p>
                <dl className="variant-card-mini-dl">
                  {freqRows.map((row) => (
                    <div key={row.label}>
                      <dt>{row.label}</dt>
                      <dd>{row.value}</dd>
                    </div>
                  ))}
                </dl>
                {extra.control_support ? (
                  <div className="variant-card-coga">
                    <span className="variant-card-col-key">Control support</span>
                    <span className="variant-card-coga-value">{extra.control_support}</span>
                  </div>
                ) : null}
              </section>

              <section className="variant-card-col">
                <p className="variant-card-col-title">Disease &amp; predictors</p>
                <dl className="variant-card-mini-dl">
                  <div>
                    <dt>OMIM</dt>
                    <dd>{extra.omim_phenotype || '—'}</dd>
                  </div>
                  <div>
                    <dt>GenCC</dt>
                    <dd>{extra.gencc_phenotype || '—'}</dd>
                  </div>
                  <div>
                    <dt>GenCC support</dt>
                    <dd>{extra.gencc_support || '—'}</dd>
                  </div>
                  <div>
                    <dt>MOI</dt>
                    <dd>{extra.omim_moi || extra.gencc_moi || '—'}</dd>
                  </div>
                  <div>
                    <dt>HPO</dt>
                    <dd>
                      {hpoTerms.length ? (
                        <Link
                          to={buildHpoHref(hpoTerms, familyId)}
                          className="variant-card-resource variant-card-resource--clinical"
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
                </dl>
              </section>
            </div>

            {variant.review?.note ? (
              <p className="variant-card-review-note">{variant.review.note}</p>
            ) : null}

            <div className="variant-card-footer">
              <div className="variant-card-quick">
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
                  className={`variant-quick-toggle${isReported ? ' variant-quick-toggle--active' : ''}`}
                  disabled={reviewIsPending || !onToggleReviewTag}
                  onClick={() => {
                    void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.report);
                  }}
                >
                  Report
                </button>
                <button type="button" className="variant-review-link" onClick={() => onEditReview?.(variant)}>
                  Tags &amp; notes
                </button>
                <button type="button" className="variant-review-link" onClick={() => onClassifyCnv?.(variant)}>
                  ACMG (CNV)
                </button>
              </div>
              <div className="variant-card-nav">
                <GenomeWorkspaceLink
                  to={igvHref}
                  className="variant-card-resource variant-card-resource--clinical"
                  label={`Open IGV at ${locus}`}
                >
                  IGV
                </GenomeWorkspaceLink>
                <GenomeWorkspaceLink
                  to={viewHref}
                  className="variant-card-resource variant-card-resource--clinical"
                  label={`Open chromosome view around ${variant.chr}:${variant.start}-${variant.end}`}
                >
                  Chromosome view
                </GenomeWorkspaceLink>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
