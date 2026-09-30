import React from 'react';
import type { NiptClassification } from './smallVariantSearch';
import { pct } from './niptClassification';

interface NiptClassificationBlockProps {
  nipt: NiptClassification;
}

/**
 * The monogenic NIPT classification, shown on the small-variant card/table when
 * the variant carries NIPT data (i.e. on the NIPT variant list).
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
    </dl>
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
