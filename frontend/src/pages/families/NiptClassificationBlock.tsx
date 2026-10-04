import React from 'react';
import type { NiptClassification } from './smallVariantSearch';
import { FATHER_STATE_LABELS, DE_NOVO_LABELS, pct, readsText } from './niptClassification';

interface NiptClassificationBlockProps {
  nipt: NiptClassification;
}

const Probability: React.FC<{ label: string; value?: number | null }> = ({ label, value }) =>
  value == null ? null : (
    <div>
      <dt>{label}</dt>
      <dd>{pct(value)}</dd>
    </div>
  );

/**
 * The monogenic NIPT classification, shown on the small-variant card/table when
 * the variant carries NIPT data (i.e. on the NIPT variant list): the category, the reads
 * behind it, the fetal-inheritance probabilities and, for a de novo candidate, its triage.
 */
const NiptClassificationBlock: React.FC<NiptClassificationBlockProps> = ({ nipt }) => (
  <div className="nipt-classification-block">
    <div className="nipt-classification-headline">
      <span className="nipt-classification-category">
        {nipt.category != null ? `Category ${nipt.category}` : 'Unclassified'}
      </span>
      <span className="nipt-classification-label">{nipt.category_label}</span>
      <span className="nipt-classification-confidence">confidence {nipt.confidence.toFixed(2)}</span>
    </div>
    <dl className="nipt-classification-stats">
      <div>
        <dt>Maternal</dt>
        <dd>{nipt.maternal_state}</dd>
      </div>
      <div>
        <dt>Fetal</dt>
        <dd>{nipt.fetal_inheritance}</dd>
      </div>
      <div>
        <dt>VAF obs / exp</dt>
        <dd>
          {pct(nipt.observed_vaf)} / {pct(nipt.expected_vaf)}
        </dd>
      </div>
      {nipt.cf_depth != null || nipt.cf_alt_reads != null ? (
        <div>
          <dt>Plasma reads</dt>
          <dd>{readsText(nipt)}</dd>
        </div>
      ) : null}
      {nipt.father_state ? (
        <div>
          <dt>Father</dt>
          <dd>
            {FATHER_STATE_LABELS[nipt.father_state] ?? nipt.father_state}
            {nipt.father_vaf != null ? ` · ${pct(nipt.father_vaf)}` : ''}
            {nipt.father_depth != null ? ` · ${nipt.father_depth}×` : ''}
          </dd>
        </div>
      ) : null}
      <Probability label="Paternal allele inherited" value={nipt.paternal_transmission_probability} />
      <Probability label="Maternal allele inherited" value={nipt.maternal_allele_probability} />
      <Probability label="Fetus homozygous" value={nipt.fetal_hom_alt_probability} />
    </dl>
    {nipt.de_novo ? (
      <div className="nipt-classification-triage">
        <span className={DE_NOVO_LABELS[nipt.de_novo.label]?.chip ?? 'table-chip'}>
          De novo: {DE_NOVO_LABELS[nipt.de_novo.label]?.label ?? nipt.de_novo.label}
        </span>
        <span className="table-subtle">
          score {nipt.de_novo.score} · {nipt.de_novo.window} window
          {nipt.de_novo.reasons.length ? ` · ${nipt.de_novo.reasons.join(', ')}` : ''}
        </span>
      </div>
    ) : null}
    {nipt.flags.length ? (
      <div className="nipt-classification-flags">
        {nipt.flags.map((flag) => (
          <span key={flag} className="table-chip">
            {flag}
          </span>
        ))}
      </div>
    ) : null}
  </div>
);

export default NiptClassificationBlock;
