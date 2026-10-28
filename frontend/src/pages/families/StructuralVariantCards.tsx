import { Link, useLocation } from 'react-router';
import { formatGt, genotypeZygosity } from '../../lib/genotypes';
import {
  buildGnomadSvRegionHref,
  formatFrequency,
  formatScore,
  getReviewClassificationTone,
  sortReviewTagKeys,
  visibleReviewTagKeys,
} from './smallVariantResultUtils';
import {
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  normalizeReviewClassification,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  structuralVariantRowKey,
  type StructuralVariant,
  type StructuralVariantFamilyMember,
} from './structuralVariantSearch';
import { buildStructuralVariantNavigation } from './structuralVariantNavigation';
import ReviewTagChip from './ReviewTagChip';
import FamilyGenotypeGrid, { type GenotypeColumn, type GenotypeTone } from './FamilyGenotypeGrid';
import { FindingLinks } from './FindingCard';
import GeneDiseaseHeading from '../../components/GeneDiseaseHeading';
import InfoTip from '../../components/InfoTip';
import { geneInfoHref } from '../../lib/geneInfo';
import { omimGeneHref, omimSearchHref } from '../../lib/omim';
import { formatStructuralLength } from './StructuralVariantTable';
import VariantPriorityBlock from './VariantPriorityBlock';
import { READ_ONLY_HINT, useFamilyReadOnly } from '../../lib/familyAccess';

interface StructuralVariantCardsProps {
  familyId?: string;
  // Needed to pick the right gnomAD dataset, and to stay silent on assemblies
  // gnomAD does not publish.
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion?: string;
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

// `from` is this SV page with its query (project and filters), so "Back to SVs" returns
// to it: the link used to drop the project, which scopes review saves and tags (#526).
const GENOTYPE_COLUMNS: GenotypeColumn[] = [
  { key: 'qual', label: 'QUAL', numeric: true },
  { key: 'reads', label: 'Reads', numeric: true },
  { key: 'filter', label: 'Filter' },
];

const ZYGOSITY_TONE: Record<ReturnType<typeof genotypeZygosity>, GenotypeTone> = {
  hom: 'alert',
  het: 'warn',
  ref: 'ref',
  na: 'na',
};

// An SV can span several genes, written as one list ("GENE1,GENE2"): the heading leads with
// the first and names the others in a tooltip.
const svGenes = (value?: string | null): string[] =>
  (value || '').split(/[,;|\s]+/).map((gene) => gene.trim()).filter(Boolean);

const buildHpoHref = (terms: string[], familyId?: string, from?: string) => {
  const params = new URLSearchParams();
  terms.forEach((term) => params.append('term', term));
  if (familyId) params.set('family_id', familyId);
  if (from) params.set('from', from);
  return `/hpo?${params.toString()}`;
};


export default function StructuralVariantCards({
  familyId,
  speciesName,
  assemblyName,
  assemblyVersion,
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
  // A reader in the family's projects sees the review but cannot change it (SEC-1).
  const readOnly = useFamilyReadOnly();
  const location = useLocation();
  const svPage = `${location.pathname}${location.search}`;
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
        const gnomadRegionHref = buildGnomadSvRegionHref({
          variant,
          speciesName,
          assemblyName,
          assemblyVersion,
        });
        const { locus, igvLocus, igvHref, viewHref } = buildStructuralVariantNavigation({
          familyId,
          linkSearch,
          projectId,
          variant,
        });
        const phenotype = extra.omim_phenotype || extra.gencc_phenotype || '';
        const genes = svGenes(variant.gene);
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
        const visibleReviewTags = visibleReviewTagKeys(sortedReviewTags);
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
            key={structuralVariantRowKey(variant)}
            className={`variant-card finding-card${isExcluded ? ' variant-card--excluded' : ''}`}
          >
            {/* The heading every variant card has: the gene (to its gene page) and its disease
                (to OMIM), what the SV is, and at the right its classification. */}
            <div className="variant-card-head">
              <GeneDiseaseHeading
                gene={genes[0] || 'Intergenic SV'}
                geneHref={geneInfoHref(genes[0], familyId, projectId)}
                diseases={phenotype ? [{ name: phenotype, omimUrl: omimSearchHref(phenotype) }] : []}
              >
                {genes.length > 1 ? (
                  <InfoTip staticChild label={genes.join(', ')} className="variant-card-chip variant-card-chip--neutral">
                    +{genes.length - 1} gene{genes.length === 2 ? '' : 's'}
                  </InfoTip>
                ) : null}
                <span className="variant-card-chip variant-card-chip--impact">{variant.type || 'SV'}</span>
                <span className="variant-card-chip variant-card-chip--soft">
                  {formatStructuralLength(variant.length)}
                </span>
                {extra.inheritance ? (
                  <span className="variant-card-chip variant-card-chip--strong">{extra.inheritance}</span>
                ) : null}
              </GeneDiseaseHeading>
              {normalizedClassification ? (
                <div className="variant-card-headtags">
                  <span
                    className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                      normalizedClassification,
                    )}`}
                  >
                    {normalizedClassification}
                  </span>
                </div>
              ) : null}
            </div>

            {variant.priority ? <VariantPriorityBlock priority={variant.priority} /> : null}

            {visibleReviewTags.length ? (
              <div className="variant-card-chip-row variant-card-tag-row">
                {visibleReviewTags.map((tagKey) => (
                  <ReviewTagChip
                    key={tagKey}
                    className="variant-card-chip variant-card-chip--tag"
                    tagKey={tagKey}
                    tagMap={tagMap}
                    tagMetadata={variant.review?.tag_metadata}
                  />
                ))}
              </div>
            ) : null}

            <div className="variant-card-cols">
              <section className="variant-card-col">
                <p className="variant-card-col-title">Variant</p>
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
                    <dt>Locus</dt>
                    <dd>{locus}</dd>
                  </div>
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
              </section>

              {gtMembers.length ? (
                <section className="variant-card-col">
                  <p className="variant-card-col-title">Genotypes</p>
                  <FamilyGenotypeGrid
                    callLabel="GT"
                    columns={GENOTYPE_COLUMNS}
                    entries={gtMembers.map((member) => {
                      const genotype = variant.genotypes.find(
                        (entry) => entry.sample === member.sample_id,
                      );
                      return {
                        member,
                        tone: ZYGOSITY_TONE[genotypeZygosity(genotype?.gt)],
                        call: genotype ? formatGt(genotype.gt) : '—',
                        values: {
                          qual: genotype?.qual ?? '—',
                          reads: genotype?.read_support ?? '—',
                          filter: genotype?.filter || '—',
                        },
                      };
                    })}
                  />
                </section>
              ) : null}

              <section className="variant-card-col">
                {/* gnomAD has no id matching a caller's SV, so this is its region view:
                    the analyst compares this call against gnomAD's SVs over the same span. */}
                {gnomadRegionHref ? (
                  <p className="variant-card-col-title">
                    <a
                      href={gnomadRegionHref}
                      target="_blank"
                      rel="noreferrer"
                      className="variant-card-col-title-link"
                      title="Open this region in the gnomAD SV browser"
                    >
                      Frequencies
                    </a>
                  </p>
                ) : (
                  <p className="variant-card-col-title">Frequencies</p>
                )}
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
                          to={buildHpoHref(hpoTerms, familyId, svPage)}
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
                  aria-pressed={hasReviewTag}
                  disabled={readOnly || reviewIsPending || !onToggleReviewTag}
                  title={readOnly ? READ_ONLY_HINT : undefined}
                  onClick={() => {
                    void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.review);
                  }}
                >
                  Review
                </button>
                <button
                  type="button"
                  className={`variant-quick-toggle${isExcluded ? ' variant-quick-toggle--active' : ''}`}
                  aria-pressed={isExcluded}
                  disabled={readOnly || reviewIsPending || !onToggleReviewTag}
                  title={readOnly ? READ_ONLY_HINT : undefined}
                  onClick={() => {
                    void onToggleReviewTag?.(variant, COLLABORATION_QUICK_TAGS.excluded);
                  }}
                >
                  Exclude
                </button>
                <button
                  type="button"
                  className={`variant-quick-toggle${isReported ? ' variant-quick-toggle--active' : ''}`}
                  aria-pressed={isReported}
                  disabled={readOnly || reviewIsPending || !onToggleReviewTag}
                  title={readOnly ? READ_ONLY_HINT : undefined}
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
              {/* The links every variant card has, in the same order (FindingLinks). */}
              <FindingLinks
                igv={{ href: igvHref, label: `Open IGV at ${igvLocus}` }}
                chromosome={{
                  href: viewHref,
                  label: `Open chromosome view around ${variant.chr}:${variant.start}-${variant.end}`,
                }}
                omim={genes[0] ? omimGeneHref(genes[0]) : null}
              />
            </div>
          </article>
        );
      })}
    </div>
  );
}
