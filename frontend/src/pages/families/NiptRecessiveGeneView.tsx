import type { NiptRecessiveGene, SmallVariant } from './smallVariantSearch';
import { namedVariants, type GroupView, type MemberView } from './smallVariantGroupView';
import {
  RECESSIVE_STATUS_LABELS,
  bothParentsCarry,
  pct,
  recessiveInheritanceText,
  recessivePairText,
  recessiveProbabilitiesText,
  recessiveStatusTone,
} from './niptClassification';

const NON_PAR = 'on chrX outside the pseudo-autosomal regions';

/** Why a recessive gene of the monogenic NIPT is listed, by how its chromosome is read. */
const recessiveBasis = (group: NiptRecessiveGene, gene: string): string => {
  switch (group.mode) {
    case 'x_linked_son':
      return `The fetus is a son and the variant lies ${NON_PAR}: his mother's variant in ${gene} alone makes him affected.`;
    case 'x_linked_sex_unknown':
      return `The variant lies ${NON_PAR} and the fetal sex is not known: a son inherits his mother's X alone, a daughter her father's X always.`;
    case 'x_linked_daughter':
      return bothParentsCarry(group)
        ? `Both parents carry a variant in ${gene}, ${NON_PAR}: a daughter inherits her father's X always.`
        : `The fetus is a daughter and the variant lies ${NON_PAR}: one parent's variant in ${gene} makes her a carrier at most.`;
    case 'autosomal':
      return `Both parents carry a variant in ${gene}: the fetus can inherit one from each.`;
  }
};

/**
 * A recessive gene's lines: why it is listed, which of its variants the fetus inherited and the
 * pair that status rests on, and what each parent carries in it, with the probability that the
 * fetus inherited each allele (not told: between 20% and 80%, or not read from the cfDNA).
 */
function RecessiveLines({ group, gene, memberView }: { group: NiptRecessiveGene; gene: string; memberView: MemberView }) {
  const name = (id: string) => namedVariants(group.variants, [id], memberView) ?? id;
  const pair = recessivePairText(group.maternal_variant_id, group.paternal_variant_id, name);
  const probabilities = recessiveProbabilitiesText(
    group.risk,
    group.carrier_probability,
    group.unaffected_probability,
    group.mode,
  );
  const parents = [
    { key: 'mother', label: 'Mother (plasma)', alleles: group.maternal, none: `carries no variant in ${gene}` },
    {
      key: 'father',
      label: 'Father',
      alleles: group.paternal,
      none: group.mode === 'x_linked_son' ? "a son does not inherit his father's X" : `carries no variant in ${gene}`,
    },
  ];
  return (
    <div className="variant-group-lines">
      <p className="variant-card-review-note">{recessiveBasis(group, gene)}</p>
      <p className="variant-card-review-note">
        <span className={recessiveStatusTone(group.status)}>{recessiveInheritanceText(group)}</span>
        {probabilities ? (
          <span className="table-subtle">
            {' '}
            {probabilities}
            {group.risk_uses_prior ? '; an inheritance the cfDNA did not tell is read at its 50% prior' : ''}
          </span>
        ) : null}
      </p>
      {pair ? <p className="variant-card-review-note table-subtle">The status rests on {pair}.</p> : null}
      <div className="variant-group-summary">
        {parents.map(({ key, label, alleles, none }, index) => (
          <div key={key} className="variant-compound-het-summary-row">
            <span className={`variant-card-chip variant-group-person variant-group-person--${index + 1}`}>{label}</span>
            {alleles.length ? (
              <span>
                {alleles.map((allele, position) => (
                  <span key={allele.variant_id}>
                    {position ? '; ' : ''}
                    carries {name(allele.variant_id)}
                    {allele.category == null ? '' : ` (category ${allele.category})`}, inherited{' '}
                    {allele.inherited_probability == null ? 'not told' : pct(allele.inherited_probability)}
                    {allele.note ? <span className="table-chip table-chip--warning">{allele.note}</span> : null}
                  </span>
                ))}
              </span>
            ) : (
              <span>{none}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

const PARENT_CHIPS = [
  { label: 'Maternal', title: 'Carried by the mother (the plasma)', side: 'maternal' },
  { label: 'Paternal', title: 'Carried by the father', side: 'paternal' },
] as const;

/** A recessive gene of the monogenic NIPT, as its group card shows it: its fetal status first. */
export const niptRecessiveGeneView = (
  group: NiptRecessiveGene,
  { gene, memberView }: { gene: string; memberView: MemberView },
): GroupView => ({
  label: 'Recessive gene',
  subject: 'recessive gene',
  aria: 'Recessive gene',
  chips: (
    <>
      {bothParentsCarry(group) ? <span className="variant-card-chip variant-card-chip--critical">Both parents</span> : null}
      {group.mode !== 'autosomal' ? <span className="variant-card-chip variant-card-chip--warning">X-linked</span> : null}
      <span className={recessiveStatusTone(group.status)}>{RECESSIVE_STATUS_LABELS[group.status]}</span>
    </>
  ),
  lines: <RecessiveLines group={group} gene={gene} memberView={memberView} />,
  personChips: (variant: SmallVariant) =>
    PARENT_CHIPS.map(({ label, title, side }, index) =>
      group[side].some((allele) => allele.variant_id === variant._id) ? (
        <span
          key={label}
          className={`variant-card-chip variant-group-person variant-group-person--${index + 1}`}
          title={title}
        >
          {label}
        </span>
      ) : null,
    ),
});
