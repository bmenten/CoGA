import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiRepeatExpansionTrackResponse, ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import { cssVar } from '../../lib/colors';
import VizLoadingOverlay from './VizLoadingOverlay';

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

const STATUS_COLORS = {
  normal: () => cssVar('--color-repeat-normal'),
  intermediate: () => cssVar('--color-repeat-intermediate'),
  pathogenic: () => cssVar('--color-repeat-pathogenic'),
  unknown: () => cssVar('--color-repeat-unknown'),
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
  const { data, isLoading } = useQuery<ApiRepeatExpansionTrackResponse>({
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
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
        {items.map((item) => {
          if (!layout) return null;
          const chrom = item.chr.replace(/^chr/i, '');
          const offset = layout.offsets[chrom];
          const centerBp = offset + (item.start + item.end) / 2;
          const x = Math.min(Math.max((centerBp / Math.max(layout.total, 1)) * width, 3), width - 3);
          const color = STATUS_COLORS[item.status]?.() || cssVar('--color-repeat-unknown');
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
                const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
                if (!bounds) return;
                setTooltip({
                  item,
                  x: event.clientX - bounds.left,
                  y: event.clientY - bounds.top,
                });
              }}
              onMouseLeave={() => setTooltip(null)}
            />
          );
        })}
      </svg>
      {isLoading && <VizLoadingOverlay message="Loading repeat expansions" />}
      {!isLoading && items.length === 0 && (
        <div className="viz-empty-overlay">No repeat loci for this sample</div>
      )}
      {tooltip && (
        <div className="viz-tooltip" style={{ left: tooltip.x + 8, top: tooltip.y + 8 }}>
          <div>{tooltip.item.display_name}</div>
          <div>{tooltip.item.disease}</div>
          <div>{tooltip.item.allele_repeat_counts.join(' / ') || 'no call'} repeats</div>
        </div>
      )}
    </div>
  );
};

export default GenomeRepeatExpansionTrack;
