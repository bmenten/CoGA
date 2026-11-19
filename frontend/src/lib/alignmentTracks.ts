import type { AlignmentManifestEntryOut } from './apiSchema.generated';
import { analysisLabel } from './familyAnalyses';

/**
 * An alignment track's name: the sample's, and the analysis whose reads it holds when the
 * sample has an alignment of more than one (short-read and then long-read WGS of the same DNA).
 */
export const alignmentTrackName = (
  entry: AlignmentManifestEntryOut,
  entries: AlignmentManifestEntryOut[],
): string => {
  const several = entries.filter((other) => other.sample_id === entry.sample_id).length > 1;
  if (!several || !entry.analysis_type) return entry.sample_id;
  return `${entry.sample_id} (${analysisLabel(entry.analysis_type) ?? entry.analysis_type})`;
};
