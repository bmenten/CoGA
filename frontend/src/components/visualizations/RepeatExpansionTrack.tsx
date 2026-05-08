import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiRepeatExpansionTrackResponse, ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';
import { cssVar } from '../../lib/colors';
import VizLoadingOverlay from './VizLoadingOverlay';

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

const STATUS_COLORS = {
  normal: () => cssVar('--color-repeat-normal'),
  intermediate: () => cssVar('--color-repeat-intermediate'),
  pathogenic: () => cssVar('--color-repeat-pathogenic'),
  unknown: () => cssVar('--color-repeat-unknown'),
};

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
  const { data, isLoading } = useQuery<ApiRepeatExpansionTrackResponse>({
    queryKey: [
      'repeat-expansions',
      familyId,
      sampleId,
      chrom,
      overviewMode ? 'chromosome' : 'region',
      overviewMode ? chromosomeSize : regionStart,
      regionEnd,
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
        `/families/${familyId}/repeat-expansions/sample/${sampleId}`,
        {
          params,
        },
      );
      return response.data as ApiRepeatExpansionTrackResponse;
    },
    enabled: overviewMode || regionEnd > regionStart,
  });

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

  const [tooltip, setTooltip] = useState<{
    item: ApiRepeatExpansionTrackItem;
    x: number;
    y: number;
  } | null>(null);

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
        {visibleItems.map((item) => {
          const center = overviewMode
            ? ((item.start + item.end) / 2) / Math.max(chromosomeSize || 0, 1)
            : ((Math.max(item.start, regionStart) + Math.min(item.end, regionEnd)) / 2 - regionStart) /
              regionLength;
          const x = Math.min(Math.max(center * width, 3), width - 3);
          const color = STATUS_COLORS[item.status]?.() || cssVar('--color-repeat-unknown');
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
      {!isLoading && visibleItems.length === 0 && (
        <div className="viz-empty-overlay">
          {overviewMode ? 'No repeat loci for this chromosome' : 'No repeat loci in this region'}
        </div>
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

export default RepeatExpansionTrack;
