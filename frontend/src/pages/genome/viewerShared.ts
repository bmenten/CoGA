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
