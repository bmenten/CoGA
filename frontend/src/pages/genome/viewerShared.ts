import type { ApiFamilyRegionOfInterest } from '../../lib/apiTypes';
import { NUCLEAR_CHROMOSOMES, formatChromosomeLabel } from '../../lib/chromosomes';

export const CHROMS = [...NUCLEAR_CHROMOSOMES, 'MT'];

export const DEFAULT_TRACK_WIDTH = 1200;
export const TRACK_WIDTH_PADDING = 32;

export const formatBp = (bp: number): string => {
  if (bp >= 1_000_000) return `${(bp / 1_000_000).toFixed(2)} Mb`;
  if (bp >= 1_000) return `${(bp / 1_000).toFixed(2)} kb`;
  return `${bp} bp`;
};

export const formatRoiCoordinates = (roi: ApiFamilyRegionOfInterest): string => {
  return `${formatChromosomeLabel(roi.chr)}:${roi.start.toLocaleString()}-${roi.end.toLocaleString()}`;
};

/**
 * The page's query string with its `project_id` replaced by the project the family's
 * reference was resolved from (or dropped when there is none), for the links between views.
 */
export const searchWithResolvedProject = (search: string, projectId?: string): string => {
  const params = new URLSearchParams(search);
  params.delete('project_id');
  if (projectId) {
    params.set('project_id', projectId);
  }
  return params.toString();
};

export const buildTrackFilterSummary = (
  variantFilters: Record<string, string>,
  sampleFilter?: string,
): string | null => {
  const parts = [
    ...Object.entries(variantFilters).map(([key, value]) => `${key}=${value}`),
    sampleFilter ? `sample_filter=${sampleFilter}` : null,
  ].filter(Boolean);

  return parts.length ? parts.join(', ') : null;
};
