import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiRepeatExpansionTrackResponse, ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import { normalizeChrom } from '../../lib/chromosomes';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizErrorOverlay from './VizErrorOverlay';
import type { GenomeLayout } from './genomeBinLayout';
import { RepeatLocusTooltip, describeRepeatLoci, repeatTrackColors } from './repeatExpansionHelpers';
import { apiPath, raw } from '../../lib/apiPath';

interface Props {
  familyId: string;
  sampleId: string;
  chroms: string[];
  layout: GenomeLayout | null;
  width: number;
  height: number;
  projectId?: string;
}

// Markers are drawn from the least to the most severe status, so the most severe is on
// top: at genome scale a 4 px marker covers loci a few hundred kb away, and a normal AFF2
// was painted over a pathogenic FMR1 (#526). The sort is stable, so ties keep API order.
const STATUS_DRAW_RANK: Record<string, number> = {
  unknown: 0,
  normal: 1,
  review: 2,
  not_assessed: 3,
  intermediate: 4,
  premutation: 5,
  pathogenic: 6,
};

const GenomeRepeatExpansionTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chroms,
  layout,
  width,
  height,
  projectId,
}) => {
  const { data, isLoading, isError, refetch } = useQuery<ApiRepeatExpansionTrackResponse>({
    queryKey: ['genome-repeat-expansions', familyId, sampleId, chroms.join(','), projectId],
    queryFn: async () => {
      const params = new URLSearchParams();
      chroms.forEach((chrom) => params.append('chr', chrom));
      if (projectId) params.set('project_id', projectId);
      const response = await api.get(
        apiPath`/families/${familyId}/repeat-expansions/sample/${sampleId}?${raw(params.toString())}`,
      );
      return response.data as ApiRepeatExpansionTrackResponse;
    },
    enabled: chroms.length > 0,
  });

  const [tooltip, setTooltip] = useState<{
    item: ApiRepeatExpansionTrackItem;
    x: number;
    y: number;
  } | null>(null);

  // Resolved once: not per locus inside the render map.
  const statusColors = useMemo(() => repeatTrackColors(), []);

  const items = useMemo(() => {
    if (!layout) return [];
    // The layout names a chromosome as normalizeChrom does (`1`, `X`, `MT`); a locus comes under
    // its VCF's name, with or without `chr`: `M` or `chrM` for the mitochondrion.
    return (data?.items || [])
      .filter((item) => layout.offsets[normalizeChrom(item.chr)] !== undefined)
      .sort((a, b) => (STATUS_DRAW_RANK[a.status] ?? 0) - (STATUS_DRAW_RANK[b.status] ?? 0));
  }, [data?.items, layout]);

  // The chart's accessible name (#529): what it shows now. A failure or a load is said
  // as such, never as zero loci (#510). Nor is a track that asked for nothing: without a
  // layout no locus can be placed yet (the genome overview is still sizing it), and
  // without a chromosome in view none is requested (#602).
  const answered = Boolean(layout) && chroms.length > 0;
  const lociSummary = useMemo(() => describeRepeatLoci(items), [items]);
  const chartState = isError
    ? 'failed to load'
    : isLoading || !layout
      ? 'loading'
      : chroms.length === 0
        ? 'no chromosomes in view'
        : items.length === 0
          ? 'none'
          : lociSummary;
  const chartLabel = `Repeat loci of ${sampleId} in view: ${chartState}`;

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
        {items.map((item) => {
          if (!layout) return null;
          const offset = layout.offsets[normalizeChrom(item.chr)];
          const centerBp = offset + (item.start + item.end) / 2;
          const x = Math.min(Math.max((centerBp / Math.max(layout.total, 1)) * width, 3), width - 3);
          const color = statusColors[item.status] || statusColors.unknown;
          return (
            <rect
              key={`${item.sample}-${item.locus_id}-${item.chr}-${item.start}`}
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
      {answered && !isLoading && !isError && items.length === 0 && (
        <div className="viz-empty-overlay">No repeat loci in view</div>
      )}
      {tooltip && (
        <RepeatLocusTooltip x={tooltip.x} y={tooltip.y} item={tooltip.item} />
      )}
    </div>
  );
};

export default GenomeRepeatExpansionTrack;
