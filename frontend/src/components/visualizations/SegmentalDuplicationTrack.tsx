import React from 'react';
import { apiPath } from '../../lib/apiPath';
import ReferenceIntervalTrack, {
  type ReferenceIntervalKind,
  type ReferenceIntervalTrackProps,
} from './ReferenceIntervalTrack';

const SEGMENTAL_DUPLICATIONS: ReferenceIntervalKind = {
  queryKey: 'segmental-duplications',
  path: (assembly, chrom) => apiPath`/segmental-duplications/${assembly}/${chrom}`,
  label: 'Segmental duplications',
  what: 'segmental duplications',
  fill: '--color-segmental-duplication',
  emptyMessage: 'No segmental duplications/LCRs in this region',
};

/** The assembly's segmental duplications and LCRs over the region in view. */
const SegmentalDuplicationTrack: React.FC<ReferenceIntervalTrackProps> = (props) => (
  <ReferenceIntervalTrack kind={SEGMENTAL_DUPLICATIONS} {...props} />
);

export default SegmentalDuplicationTrack;
