import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiRepeatExpansionTrackResponse, ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import { cssVar } from '../../lib/colors';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizErrorOverlay from './VizErrorOverlay';
import { RepeatLocusTooltip, STATUS_COLORS } from './repeatExpansionHelpers';

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
}

interface Props {
  familyId: string;
  sampleId: string;
  chroms: string[];
  layout: Layout | null;
  width: number;
  height: number;
  projectId?: string;
}

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
        `/families/${familyId}/repeat-expansions/sample/${sampleId}?${params.toString()}`,
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

  // Resolve the status palette once: STATUS_COLORS values call cssVar()
  // (getComputedStyle), so they must not run per locus inside the render map.
  const statusColors = useMemo(
    () => ({
      normal: STATUS_COLORS.normal(),
      review: STATUS_COLORS.review(),
      intermediate: STATUS_COLORS.intermediate(),
      pathogenic: STATUS_COLORS.pathogenic(),
      unknown: STATUS_COLORS.unknown(),
      grid: cssVar('--color-grid'),
    }),
    [],
  );

  const items = useMemo(() => {
    if (!layout) return [];
    return (data?.items || []).filter((item) => layout.offsets[item.chr.replace(/^chr/i, '')] !== undefined);
  }, [data?.items, layout]);

  const trackY = Math.max(2, Math.floor(height * 0.28));
  const trackHeight = Math.max(height - trackY * 2, 6);

  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height}>
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
          const chrom = item.chr.replace(/^chr/i, '');
          const offset = layout.offsets[chrom];
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
      {!isLoading && !isError && items.length === 0 && (
        <div className="viz-empty-overlay">No repeat loci for this sample</div>
      )}
      {tooltip && (
        <RepeatLocusTooltip x={tooltip.x} y={tooltip.y} item={tooltip.item} />
      )}
    </div>
  );
};

export default GenomeRepeatExpansionTrack;
