import { useMemo } from 'react';
import type { ApiFamilyRegionOfInterest } from '../../lib/apiTypes';
import { NUCLEAR_CHROMOSOMES, formatChromosomeLabel } from '../../lib/chromosomes';
import { useMeasuredWidth } from '../../lib/useMeasuredWidth';

export const CHROMS = [...NUCLEAR_CHROMOSOMES, 'MT'];

export const DEFAULT_TRACK_WIDTH = 1200;
export const TRACK_WIDTH_PADDING = 32;

/**
 * The track area's ref, and the width to draw its tracks at: the measured area less its
 * padding, never under DEFAULT_TRACK_WIDTH (which also stands in until it is measured).
 */
export const useTrackWidth = () => {
  const [trackAreaRef, trackAreaWidth] = useMeasuredWidth<HTMLElement>();
  const trackWidth = useMemo(() => {
    if (trackAreaWidth <= 0) return DEFAULT_TRACK_WIDTH;
    return Math.max(Math.round(trackAreaWidth - TRACK_WIDTH_PADDING), DEFAULT_TRACK_WIDTH);
  }, [trackAreaWidth]);
  return [trackAreaRef, trackWidth] as const;
};

/** The family's ROI when it lies on the assembly shown (or names none); null otherwise. */
export const roiOnAssembly = (
  roi: ApiFamilyRegionOfInterest | null | undefined,
  assemblyId: string | undefined,
): ApiFamilyRegionOfInterest | null => {
  if (!roi) return null;
  if (roi.assembly_id && assemblyId && roi.assembly_id !== assemblyId) {
    return null;
  }
  return roi;
};

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
