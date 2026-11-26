import { Link } from 'react-router';
import type {
  FamilyMember,
  SmallVariant,
  SmallVariantCarrierGene,
  SmallVariantGroup,
  SmallVariantResultGroup,
} from './smallVariantSearch';
import {
  buildSmallVariantGeneInfoHref,
  formatCompoundHetPhaseStatus,
  formatVariantChange,
  reviewClassChips,
  sortReviewTagKeys,
} from './smallVariantResultUtils';
import CardCollapseToggle from './CardCollapseToggle';
import ReviewTagChip from './ReviewTagChip';
import SmallVariantTable from './SmallVariantTable';
import {
  SmallVariantCard,
  geneDiseaseStateOf,
  useSmallVariantCardList,
  type SmallVariantCardList,
  type SmallVariantCardListProps,
} from './SmallVariantCards';
import { GeneCurationLine, GeneDiseaseSummaryLine } from './VariantGeneDiseases';

interface SmallVariantGroupCardsProps extends SmallVariantCardListProps {
  groups: SmallVariantResultGroup[];
  // How a group lists its variants: each as its full card, or as the rows of a table.
  memberView: 'cards' | 'table';
}

// Read-backed phasing sees both alts on opposite haplotypes; segregation traces one hit
// to each parent. Say which, and claim neither when the payload does not.
const transPhaseLabel = (evidence: SmallVariantGroup['phase_evidence']): string => {
  if (evidence === 'read') return 'In trans · read-backed';
  if (evidence === 'segregation') return 'In trans · by segregation';
  return 'In trans';
};

// A partner of a carrier screen by their samples, with the role the family gives them.
const partnerName = (samples: string[], members: FamilyMember[]) =>
  samples
    .map((sample) => {
      const role = members.find((member) => member.sample_id === sample)?.role;
      return role && role !== 'other' ? `${sample} (${role})` : sample;
    })
    .join(' + ') || 'Unnamed partner';

const listInWords = (items: string[]) =>
  items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

// The variants of the group a partner carries, as the panel names them: by their place when the
// group lists its variants as cards headed "Variant 1 of 3", by their change in a table.
const carriedVariants = (variants: SmallVariant[], ids: string[], memberView: 'cards' | 'table') => {
  const carried = variants
    .map((variant, index) => ({ variant, place: index + 1 }))
    .filter(({ variant }) => ids.includes(variant._id));
  if (!carried.length) return null;
  if (memberView === 'table') return listInWords(carried.map(({ variant }) => formatVariantChange(variant)));
  return `${carried.length === 1 ? 'variant' : 'variants'} ${listInWords(carried.map(({ place }) => String(place)))}`;
};

/** Why the couple is at risk in the gene, in the screen's own terms (REQ-CARR-002). */
const carrierBasis = (group: SmallVariantCarrierGene, gene: string) => {
  if (group.both_partners_carry && group.x_linked) {
    return `Both partners carry a variant in ${gene}, on chrX outside the pseudo-autosomal regions.`;
  }
  if (group.both_partners_carry) {
    return `Both partners carry a variant in ${gene}: a child can inherit one from each.`;
  }
  return `On chrX outside the pseudo-autosomal regions: one partner's variant is a finding on its own.`;
};

/** The pair's review and what the reads or the parents say of its phase. */
function PairPanel({
  group,
  list,
  geneInfoHref,
}: {
  group: SmallVariantGroup;
  list: SmallVariantCardList;
  geneInfoHref: string | null;
}) {
  // The pair review's class once, by the one rule: a class tag that is not the class shown
  // is no chip.
  const pairClass = reviewClassChips(
    group.review?.classification,
    group.review?.tags,
    sortReviewTagKeys(group.review?.tags || [], list.tagMap),
  );
  return (
    <div className="variant-card-review-panel variant-group-panel">
      <div className="variant-card-review-header">
        <p className="variant-card-section-title">Pair review</p>
        {geneInfoHref ? (
          <Link to={geneInfoHref} className="variant-card-inline-link">
            Gene context
          </Link>
        ) : null}
      </div>
      <div className="variant-compound-het-summary variant-group-summary">
        <div className="variant-compound-het-summary-row">
          <span className="analysis-pill analysis-pill--muted">Phase</span>
          <span>{formatCompoundHetPhaseStatus(group.review?.phase_status)}</span>
        </div>
        {/* What the reads or the parents' genotypes say, as opposed to what the curator
            recorded above. Cis pairs never reach the client — they are dropped as candidates. */}
        <div className="variant-compound-het-summary-row">
          <span className="analysis-pill analysis-pill--muted">Phasing</span>
          {group.phase === 'trans' ? (
            <span className="variant-card-chip variant-card-chip--success">
              {transPhaseLabel(group.phase_evidence)}
            </span>
          ) : (
            <span>Not resolved by reads or segregation</span>
          )}
        </div>
      </div>
      {/* The class the pair review shows: a class word as its label, not as typed. */}
      {pairClass.label || pairClass.tagKeys.length ? (
        <div className="variant-card-chip-row">
          {pairClass.label ? (
            <span className={`variant-card-chip variant-card-chip--${pairClass.tone}`}>{pairClass.label}</span>
          ) : null}
          {pairClass.tagKeys.map((tagKey) => (
            <ReviewTagChip
              key={`${group.group_key}:${tagKey}`}
              className="variant-card-chip variant-card-chip--tag"
              tagKey={tagKey}
              tagMap={list.tagMap}
              tagMetadata={group.review?.tag_metadata}
            />
          ))}
        </div>
      ) : null}
      <p className="variant-card-review-note">{group.review?.note || 'No pair review saved.'}</p>
    </div>
  );
}

/** The couple in a carrier screen's gene: why it is a finding, and what each partner carries. */
function CouplePanel({
  group,
  gene,
  members,
  geneInfoHref,
  memberView,
}: {
  group: SmallVariantCarrierGene;
  gene: string;
  members: FamilyMember[];
  geneInfoHref: string | null;
  memberView: 'cards' | 'table';
}) {
  return (
    <div className="variant-card-review-panel variant-group-panel">
      <div className="variant-card-review-header">
        <p className="variant-card-section-title">Couple</p>
        {geneInfoHref ? (
          <Link to={geneInfoHref} className="variant-card-inline-link">
            Gene context
          </Link>
        ) : null}
      </div>
      <p className="variant-card-review-note">{carrierBasis(group, gene)}</p>
      <div className="variant-compound-het-summary variant-group-summary">
        {group.partners.map((partner, index) => {
          const carried = carriedVariants(group.variants, partner.variant_ids, memberView);
          return (
            <div key={partner.samples.join('+') || index} className="variant-compound-het-summary-row">
              <span className={`variant-card-chip variant-group-partner variant-group-partner--${index + 1}`}>
                {partnerName(partner.samples, members)}
              </span>
              <span>{carried ? `carries ${carried}` : `carries no variant in ${gene}`}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * A search's grouped results: each compound-het pair, and each gene of an expanded carrier
 * screen with every variant the couple carries there, however many. A group is headed by its
 * gene, once with the gene's diseases and curated inheritance, and holds its variants as the
 * single-variant list shows them: each as its full card, or as the rows of a table.
 */
export default function SmallVariantGroupCards({ groups, memberView, ...props }: SmallVariantGroupCardsProps) {
  const { members, familyId, projectId, onEditReview } = props;
  const list = useSmallVariantCardList(
    groups.flatMap((group) => [group.gene, ...group.variants.map((variant) => variant.gene)]),
    props.tags,
  );

  return (
    <div className="variant-card-list">
      {groups.map((group) => {
        const anchorVariant = group.variants[0];
        const isPair = group.group_type === 'compound_het';
        const groupGene = group.gene || group.gene_id || anchorVariant?.gene || anchorVariant?.gene_id;
        const geneName = groupGene || 'an unnamed gene';
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
        const subject = isPair ? `the compound het pair in ${geneName}` : `the carrier gene ${geneName}`;
        const folded = list.collapsedCards.isCollapsed(group.group_key);
        const geneDiseaseState = geneDiseaseStateOf(list, group.gene || anchorVariant?.gene);
        const firstCardKey = anchorVariant ? `${group.group_key}:${anchorVariant._id}` : group.group_key;
        const count = group.variants.length;
        // In a carrier screen, who carries each variant: a chip per partner, in the panel's colours.
        const carriersOf = (variant: SmallVariant) =>
          isPair
            ? null
            : group.partners.map((partner, index) =>
                partner.variant_ids.includes(variant._id) ? (
                  <span
                    key={partner.samples.join('+') || index}
                    className={`variant-card-chip variant-group-partner variant-group-partner--${index + 1}`}
                    title="Carried by this partner"
                  >
                    {partner.samples.join(' + ')}
                  </span>
                ) : null,
              );

        return (
          <article
            key={group.group_key}
            className={`variant-card variant-group-card${folded ? ' variant-card--collapsed' : ''}`}
            aria-label={isPair ? `Compound het pair in ${geneName}` : `Carrier gene ${geneName}`}
          >
            <div className="variant-card-topbar">
              <div className="variant-card-topline">
                <CardCollapseToggle
                  collapsed={folded}
                  onToggle={() => list.collapsedCards.toggle(group.group_key)}
                  subject={subject}
                />
                <span className="variant-card-toplabel">{isPair ? 'Compound het pair' : 'Carrier gene'}</span>
                <span className="variant-card-banner variant-card-banner--strong">
                  {groupGene || 'Grouped result'}
                </span>
                <span className="variant-card-chip variant-card-chip--neutral">
                  {count} variant{count === 1 ? '' : 's'}
                </span>
                {!isPair && group.both_partners_carry ? (
                  <span className="variant-card-chip variant-card-chip--critical">Both partners</span>
                ) : null}
                {!isPair && group.x_linked ? (
                  <span className="variant-card-chip variant-card-chip--warning">X-linked</span>
                ) : null}
              </div>
              {isPair && anchorVariant ? (
                <div className="variant-card-actions">
                  <button type="button" className="button-secondary" onClick={() => onEditReview(anchorVariant)}>
                    Review pair
                  </button>
                </div>
              ) : null}
            </div>

            {folded ? (
              // The group folds to its heading and the changes that tell its variants apart.
              <p className="variant-card-collapsed-summary">{group.variants.map(formatVariantChange).join('  +  ')}</p>
            ) : (
              <div className="variant-card-body">
                {/* The gene's diseases and curated inheritance, once for the group. */}
                <GeneDiseaseSummaryLine
                  {...geneDiseaseState}
                  onShowAll={() => list.setCardDetailsOpen(firstCardKey, true)}
                />
                <GeneCurationLine links={geneDiseaseState.links} />

                {isPair ? (
                  <PairPanel group={group} list={list} geneInfoHref={geneInfoHref} />
                ) : (
                  <CouplePanel
                    group={group}
                    gene={geneName}
                    members={members}
                    geneInfoHref={geneInfoHref}
                    memberView={memberView}
                  />
                )}

                {memberView === 'cards' ? (
                  <div className="variant-group-members">
                    {group.variants.map((variant, index) => (
                      <SmallVariantCard
                        key={variant._id}
                        variant={variant}
                        list={list}
                        cardKey={`${group.group_key}:${variant._id}`}
                        grouped={{
                          label: `Variant ${index + 1} of ${count}`,
                          subject: `variant ${index + 1} of ${count} in ${geneName}`,
                          aside: carriersOf(variant),
                        }}
                        {...props}
                      />
                    ))}
                  </div>
                ) : (
                  <SmallVariantTable variants={group.variants} {...props} />
                )}
              </div>
            )}
          </article>
        );
      })}
      {list.transcriptPopup}
    </div>
  );
}
