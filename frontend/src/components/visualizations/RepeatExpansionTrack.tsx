import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import api from '../../lib/api';
import type { ApiRepeatExpansionTrackResponse, ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import VizLoadingOverlay from './VizLoadingOverlay';
import { formatChromosomeLabel } from '../../lib/chromosomes';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import { RepeatLocusTooltip, describeRepeatLoci, repeatTrackColors } from './repeatExpansionHelpers';
import { apiPath } from '../../lib/apiPath';

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  projectId?: string;
  chromosomeSize?: number;
}

const RepeatExpansionTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  projectId,
  chromosomeSize,
}) => {
  const overviewMode = Number.isFinite(chromosomeSize) && (chromosomeSize ?? 0) > 0;
  // Overview mode asks for the whole chromosome; otherwise a view with no width asks for
  // nothing, and is neither loading nor "none" (#602).
  const regionInView = overviewMode || hasRegionInView(regionStart, regionEnd);
  const { data: rawData, isLoading, isError, refetch } = useQuery<ApiRepeatExpansionTrackResponse>({
    queryKey: [
      'repeat-expansions',
      familyId,
      sampleId,
      chrom,
      overviewMode ? 'chromosome' : 'region',
      overviewMode ? chromosomeSize : regionStart,
      // In overview mode the request is chromosome-wide ({chr, project_id} only),
      // so keep the key stable across pan/zoom instead of refetching the identical
      // payload each time region.end changes — mirrors the regionStart guard above.
      overviewMode ? null : regionEnd,
      projectId,
    ],
    queryFn: async () => {
      const params: Record<string, string | number | undefined> = {
        chr: chrom,
        project_id: projectId,
      };
      if (!overviewMode) {
        params.start = regionStart;
        params.end = regionEnd;
      }
      const response = await api.get(
        apiPath`/families/${familyId}/repeat-expansions/sample/${sampleId}`,
        {
          params,
        },
      );
      return response.data as ApiRepeatExpansionTrackResponse;
    },
    enabled: regionInView,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSameSpanFallbackData(
    rawData,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${familyId}|${sampleId}|${chrom}`,
    isError,
  );

  const regionLength = Math.max(regionEnd - regionStart, 1);
  const visibleItems = useMemo(
    () =>
      (data?.items || []).filter(
        (item) =>
          item.chr.replace(/^chr/i, '') === chrom.replace(/^chr/i, '') &&
          (overviewMode || (item.end >= regionStart && item.start <= regionEnd)),
      ),
    [chrom, data?.items, overviewMode, regionEnd, regionStart],
  );

  // The chart's accessible name (#529): what it shows now — the whole chromosome in
  // overview mode, else the region. A failure or a load is said as such, never as zero
  // loci (#510).
  const lociSummary = useMemo(() => describeRepeatLoci(visibleItems), [visibleItems]);
  const chartScope = overviewMode
    ? formatChromosomeLabel(chrom)
    : describeTrackRegion(chrom, regionStart, regionEnd);
  const chartState = isError
    ? 'failed to load'
    : !regionInView
      ? NO_REGION_IN_VIEW
      : isLoading
        ? 'loading'
        : visibleItems.length === 0
          ? 'none'
          : lociSummary;
  const chartLabel = `Repeat loci of ${sampleId} on ${chartScope}: ${chartState}`;

  const [tooltip, setTooltip] = useState<{
    item: ApiRepeatExpansionTrackItem;
    x: number;
    y: number;
  } | null>(null);

  // Resolved once: not per locus inside the render map, which re-runs on every
  // mousemove tooltip update.
  const statusColors = useMemo(() => repeatTrackColors(), []);

  const trackY = Math.max(2, Math.floor(height * 0.28));
  const trackHeight = Math.max(height - trackY * 2, 6);

  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height} role="img" aria-label={chartLabel}>
        <line
          x1={0}
          x2={width}
          y1={trackY + trackHeight / 2}
          y2={trackY + trackHeight / 2}
          stroke={statusColors.grid}
          strokeWidth={1}
        />
        {visibleItems.map((item) => {
          const center = overviewMode
            ? ((item.start + item.end) / 2) / Math.max(chromosomeSize || 0, 1)
            : ((Math.max(item.start, regionStart) + Math.min(item.end, regionEnd)) / 2 - regionStart) /
              regionLength;
          const x = Math.min(Math.max(center * width, 3), width - 3);
          const color = statusColors[item.status] || statusColors.unknown;
          return (
            <rect
              key={`${item.locus_id}-${item.start}-${item.end}`}
              data-repeat-locus-id={item.locus_id}
              x={x - 2}
              y={trackY}
              width={4}
              height={trackHeight}
              rx={2}
              fill={color}
              onMouseMove={(event) => {
                setTooltip({
                  item,
                  x: event.clientX,
                  y: event.clientY,
                });
              }}
              onMouseLeave={() => setTooltip(null)}
            />
          );
        })}
      </svg>
      {isLoading && <VizLoadingOverlay message="Loading repeat expansions" />}
      {isError && <VizErrorOverlay what="repeat expansions" onRetry={() => void refetch()} />}
      {regionInView && !isLoading && !isError && visibleItems.length === 0 && (
        <div className="viz-empty-overlay">
          {overviewMode ? 'No repeat loci for this chromosome' : 'No repeat loci in this region'}
        </div>
      )}
      {tooltip && (
        <RepeatLocusTooltip x={tooltip.x} y={tooltip.y} item={tooltip.item} />
      )}
    </div>
  );
};

export default RepeatExpansionTrack;
