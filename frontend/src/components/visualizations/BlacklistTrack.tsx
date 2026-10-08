import React from 'react';
import { apiPath } from '../../lib/apiPath';
import ReferenceIntervalTrack, {
  type ReferenceIntervalKind,
  type ReferenceIntervalTrackProps,
} from './ReferenceIntervalTrack';

const BLACKLIST: ReferenceIntervalKind = {
  queryKey: 'blacklist',
  path: (assembly, chrom) => apiPath`/blacklist/${assembly}/${chrom}`,
  label: 'Blacklist regions',
  what: 'blacklist regions',
  fill: '--color-blacklist',
  emptyMessage: 'No blacklist regions in this region',
};

/** The assembly's blacklist regions over the region in view. */
const BlacklistTrack: React.FC<ReferenceIntervalTrackProps> = (props) => (
  <ReferenceIntervalTrack kind={BLACKLIST} {...props} />
);

export default BlacklistTrack;
