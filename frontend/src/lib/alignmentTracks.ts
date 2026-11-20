import type { AlignmentManifestEntryOut } from './apiSchema.generated';
import { analysisLabel } from './familyAnalyses';

/**
 * An alignment track's name: the sample's, and the analysis whose reads it holds whenever the
 * family holds several analyses (`severalAnalyses`: short-read and then long-read WGS of the same
 * DNA) or the sample has an alignment of more than one. So a track never reads as any analysis's
 * when the family holds another, also when only one of the sample's alignments can be shown. An
 * alignment found in the family's folders, which no import recorded, says so then.
 */
export const alignmentTrackName = (
  entry: AlignmentManifestEntryOut,
  entries: AlignmentManifestEntryOut[],
  severalAnalyses = false,
): string => {
  const named = severalAnalyses || entries.filter((other) => other.sample_id === entry.sample_id).length > 1;
  if (!named) return entry.sample_id;
  if (!entry.analysis_type) return `${entry.sample_id} (analysis not recorded)`;
  return `${entry.sample_id} (${analysisLabel(entry.analysis_type) ?? entry.analysis_type})`;
};
