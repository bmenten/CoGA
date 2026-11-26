import { Link } from 'react-router';
import type {
  CompoundHetPair,
  FamilyMember,
  SmallVariant,
  SmallVariantCarrierGene,
  SmallVariantCompoundHetGene,
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

type MemberView = 'cards' | 'table';

interface SmallVariantGroupCardsProps extends SmallVariantCardListProps {
  groups: SmallVariantResultGroup[];
  // How a group lists its variants: each as its full card, or as the rows of a table.
  memberView: MemberView;
}

// Read-backed phasing sees both alts on opposite haplotypes; segregation traces one hit
// to each parent. Say which, and claim neither when the payload does not.
const transPhaseLabel = (evidence: CompoundHetPair['phase_evidence']): string => {
  if (evidence === 'read') return 'In trans · read-backed';
  if (evidence === 'segregation') return 'In trans · by segregation';
  return 'In trans';
};

const roleOf = (sample: string, members: FamilyMember[]) =>
  members.find((member) => member.sample_id === sample)?.role;

// A person by their samples, with the role the family gives them.
const personName = (samples: string[], members: FamilyMember[]) =>
  samples
    .map((sample) => {
      const role = roleOf(sample, members);
      return role && role !== 'other' ? `${sample} (${role})` : sample;
    })
    .join(' + ') || 'Unnamed';

const listInWords = (items: string[]) =>
  items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

// Some of a group's variants, as its lines name them: by their place when the group lists its
// variants as cards headed "Variant 1 of 3", by their change in a table.
const namedVariants = (variants: SmallVariant[], ids: string[], memberView: MemberView) => {
  const named = variants
    .map((variant, index) => ({ variant, place: index + 1 }))
    .filter(({ variant }) => ids.includes(variant._id));
  if (!named.length) return null;
  if (memberView === 'table') return listInWords(named.map(({ variant }) => formatVariantChange(variant)));
  return `${named.length === 1 ? 'variant' : 'variants'} ${listInWords(named.map(({ place }) => String(place)))}`;
};

// A pair of a gene, as its line names it: "Variants 1 + 3" over cards, the two changes in a table.
const pairName = (variants: SmallVariant[], pair: CompoundHetPair, memberView: MemberView) => {
  const places = pair.variant_ids.map((id) => variants.findIndex((variant) => variant._id === id));
  if (memberView === 'table') return places.map((place) => formatVariantChange(variants[place])).join(' + ');
  return `Variants ${places.map((place) => place + 1).join(' + ')}`;
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

// The parents a compound-het gene's variants trace to, father first, then mother: their order
// gives each its colour, on the lines and on the cards.
const tracedParents = (group: SmallVariantCompoundHetGene, members: FamilyMember[]) => {
  const rank = (sample: string) => ({ father: 0, mother: 1 })[roleOf(sample, members) ?? ''] ?? 2;
  return [...new Set(Object.values(group.inherited_from))].sort(
    (left, right) => rank(left) - rank(right) || left.localeCompare(right),
  );
};

// A parent as the chip on a card names them: by the copy they gave, else by their sample.
const parentChipLabel = (sample: string, members: FamilyMember[]) => {
  const role = roleOf(sample, members);
  if (role === 'father') return 'Paternal';
  if (role === 'mother') return 'Maternal';
  return `From ${sample}`;
};

/** One pair of a compound-het gene: its variants, its phase and the pair review it holds. */
function PairLine({
  group,
  pair,
  list,
  memberView,
}: {
  group: SmallVariantCompoundHetGene;
  pair: CompoundHetPair;
  list: SmallVariantCardList;
  memberView: MemberView;
}) {
  const review = pair.review;
  // The pair review's class once, by the one rule: a class tag that is not the class shown
  // is no chip.
  const pairClass = reviewClassChips(
    review?.classification,
    review?.tags,
    sortReviewTagKeys(review?.tags || [], list.tagMap),
  );
  return (
    <div className="variant-group-pair">
      <span className="variant-group-pair-name">{pairName(group.variants, pair, memberView)}</span>
      {/* What the reads or the parents' genotypes say. Cis pairs never reach the client:
          they are dropped as candidates. */}
      {pair.phase === 'trans' ? (
        <span className="variant-card-chip variant-card-chip--success">{transPhaseLabel(pair.phase_evidence)}</span>
      ) : (
        <span className="variant-card-chip variant-card-chip--neutral">Not resolved by reads or segregation</span>
      )}
      {review ? (
        <>
          {/* The curator's own call, apart from what the reads or the parents say. */}
          <span className="variant-group-pair-recorded">
            Recorded phase: {formatCompoundHetPhaseStatus(review.phase_status)}
          </span>
          {/* The class the pair review shows: a class word as its label, not as typed. */}
          {pairClass.label ? (
            <span className={`variant-card-chip variant-card-chip--${pairClass.tone}`}>{pairClass.label}</span>
          ) : null}
          {pairClass.tagKeys.map((tagKey) => (
            <ReviewTagChip
              key={`${pair.pair_key}:${tagKey}`}
              className="variant-card-chip variant-card-chip--tag"
              tagKey={tagKey}
              tagMap={list.tagMap}
              tagMetadata={review.tag_metadata}
            />
          ))}
          {review.note ? <span className="variant-card-review-note">{review.note}</span> : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * A compound-het gene's lines: its variants by the parent each traces to, then each pair the
 * search keeps among them. Plain lines under the gene's, not a box: the cards are boxed already.
 */
function CompoundHetLines({
  group,
  list,
  members,
  memberView,
}: {
  group: SmallVariantCompoundHetGene;
  list: SmallVariantCardList;
  members: FamilyMember[];
  memberView: MemberView;
}) {
  const parents = tracedParents(group, members);
  const untraced = group.variants.map((variant) => variant._id).filter((id) => !(id in group.inherited_from));
  return (
    <div className="variant-group-lines">
      <div className="variant-group-summary">
        {parents.map((parent, index) => (
          <div key={parent} className="variant-compound-het-summary-row">
            <span className={`variant-card-chip variant-group-person variant-group-person--${index + 1}`}>
              {parentChipLabel(parent, members)}
            </span>
            <span>
              {personName([parent], members)} gave{' '}
              {namedVariants(
                group.variants,
                Object.keys(group.inherited_from).filter((id) => group.inherited_from[id] === parent),
                memberView,
              )}
            </span>
          </div>
        ))}
        {untraced.length ? (
          <div className="variant-compound-het-summary-row">
            <span className="variant-card-chip variant-card-chip--neutral">Parent not traced</span>
            <span>{namedVariants(group.variants, untraced, memberView)}</span>
          </div>
        ) : null}
      </div>
      <div className="variant-group-pairs">
        {group.pairs.map((pair) => (
          <PairLine key={pair.pair_key} group={group} pair={pair} list={list} memberView={memberView} />
        ))}
      </div>
    </div>
  );
}

/**
 * The couple in a carrier screen's gene: why it is a finding, and what each partner carries.
 * Plain lines under the gene's, not a box of their own: the cards below are boxed already.
 */
function CoupleLines({
  group,
  gene,
  members,
  memberView,
}: {
  group: SmallVariantCarrierGene;
  gene: string;
  members: FamilyMember[];
  memberView: MemberView;
}) {
  return (
    <div className="variant-group-lines">
      <p className="variant-card-review-note">{carrierBasis(group, gene)}</p>
      <div className="variant-group-summary">
        {group.partners.map((partner, index) => {
          const carried = namedVariants(group.variants, partner.variant_ids, memberView);
          return (
            <div key={partner.samples.join('+') || index} className="variant-compound-het-summary-row">
              <span className={`variant-card-chip variant-group-person variant-group-person--${index + 1}`}>
                {personName(partner.samples, members)}
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
 * A search's grouped results: each gene with compound-het pairs, with every variant of its pairs
 * and the pairs among them, and each gene of an expanded carrier screen with every variant the
 * couple carries there, however many. A group is headed by its gene, once with the gene's
 * diseases and curated inheritance, and holds its variants as the single-variant list shows
 * them: each as its full card, or as the rows of a table.
 */
export default function SmallVariantGroupCards({ groups, memberView, ...props }: SmallVariantGroupCardsProps) {
  const { members, familyId, projectId } = props;
  const list = useSmallVariantCardList(
    groups.flatMap((group) => [group.gene, ...group.variants.map((variant) => variant.gene)]),
    props.tags,
  );

  return (
    <div className="variant-card-list">
      {groups.map((group) => {
        const anchorVariant = group.variants[0];
        const isCompoundHet = group.group_type === 'compound_het';
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
        const kind = isCompoundHet ? 'compound het gene' : 'carrier gene';
        const folded = list.collapsedCards.isCollapsed(group.group_key);
        const geneDiseaseState = geneDiseaseStateOf(list, group.gene || anchorVariant?.gene);
        const firstCardKey = anchorVariant ? `${group.group_key}:${anchorVariant._id}` : group.group_key;
        const count = group.variants.length;
        const parents = isCompoundHet ? tracedParents(group, members) : [];
        // On each card, in the lines' colours: the parent the variant traces to, in a
        // compound-het gene; each partner who carries it, in a carrier screen.
        const personChips = (variant: SmallVariant) => {
          if (isCompoundHet) {
            const parent = group.inherited_from[variant._id];
            if (!parent) return null;
            return (
              <span
                className={`variant-card-chip variant-group-person variant-group-person--${parents.indexOf(parent) + 1}`}
                title={`Traced to ${personName([parent], members)}`}
              >
                {parentChipLabel(parent, members)}
              </span>
            );
          }
          return group.partners.map((partner, index) =>
            partner.variant_ids.includes(variant._id) ? (
              <span
                key={partner.samples.join('+') || index}
                className={`variant-card-chip variant-group-person variant-group-person--${index + 1}`}
                title="Carried by this partner"
              >
                {partner.samples.join(' + ')}
              </span>
            ) : null,
          );
        };

        return (
          <article
            key={group.group_key}
            className={`variant-card variant-group-card${folded ? ' variant-card--collapsed' : ''}`}
            aria-label={`${isCompoundHet ? 'Compound het gene' : 'Carrier gene'} ${geneName}`}
          >
            <div className="variant-card-topbar">
              <div className="variant-card-topline">
                <CardCollapseToggle
                  collapsed={folded}
                  onToggle={() => list.collapsedCards.toggle(group.group_key)}
                  subject={`the ${kind} ${geneName}`}
                />
                {/* The gene as a single variant's card names it, and the way to it in the
                    gene explorer. */}
                {geneInfoHref && groupGene ? (
                  <Link to={geneInfoHref} className="variant-card-gene">
                    {groupGene}
                  </Link>
                ) : (
                  <span className="variant-card-gene">{groupGene || 'Unnamed gene'}</span>
                )}
                <span className="variant-card-toplabel">{isCompoundHet ? 'Compound het' : 'Carrier gene'}</span>
                <span className="variant-card-chip variant-card-chip--neutral">
                  {count} variant{count === 1 ? '' : 's'}
                </span>
                {isCompoundHet ? (
                  <span className="variant-card-chip variant-card-chip--neutral">
                    {group.pairs.length} pair{group.pairs.length === 1 ? '' : 's'}
                  </span>
                ) : null}
                {!isCompoundHet && group.both_partners_carry ? (
                  <span className="variant-card-chip variant-card-chip--critical">Both partners</span>
                ) : null}
                {!isCompoundHet && group.x_linked ? (
                  <span className="variant-card-chip variant-card-chip--warning">X-linked</span>
                ) : null}
              </div>
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

                {isCompoundHet ? (
                  <CompoundHetLines group={group} list={list} members={members} memberView={memberView} />
                ) : (
                  <CoupleLines group={group} gene={geneName} members={members} memberView={memberView} />
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
                          aside: personChips(variant),
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
