import React from 'react';
import type { CcnvTrackSummaryOut } from '../../lib/apiSchema.generated';
import { ccnvFetalFractionEstimates, ccnvTrackHeadline } from '../../lib/ccnvTracks';

/**
 * A ccnv track's note, in its head: the maternal plasma's fetal fraction (the effective one the
 * expected lines are drawn at), fetal sex and sex-chromosome call, as ccnv gave them; every
 * fetal-fraction estimate on hover. ccnv's low-fetal-fraction warning is said in the warning
 * tone.
 */
const CcnvTrackNote: React.FC<{ summary: CcnvTrackSummaryOut }> = ({ summary }) => (
  <span
    className={`viz-scale-note${summary.low_ff_warning ? ' viz-scale-note--warning' : ''}`}
    role="note"
    title={ccnvFetalFractionEstimates(summary).join('\n')}
  >
    {ccnvTrackHeadline(summary)}
    {summary.low_ff_warning ? ' · low fetal fraction (ccnv)' : ''}
  </span>
);

export default CcnvTrackNote;
