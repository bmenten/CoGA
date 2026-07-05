import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';

interface SegmentalDuplication {
  start: number;
  end: number;
  label: string;
}

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  height: number;
  regionStart: number;
  regionEnd: number;
}

const SegmentalDuplicationTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const { data: rawData } = useQuery<SegmentalDuplication[]>({
    queryKey: ['segmental-duplications', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/segmental-duplications/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as SegmentalDuplication[];
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSameSpanFallbackData(rawData, (regionEnd ?? 0) - (regionStart ?? 0));

  if (!data) return <svg width={width} height={height} />;

  const regionLength = regionEnd - regionStart;
  const trackY = Math.max(2, Math.floor(height * 0.2));
  const trackHeight = Math.max(height - trackY * 2, 4);
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
        {data.map((interval, index) => {
          const start = Math.max(interval.start, regionStart);
          const end = Math.min(interval.end, regionEnd);
          const x = ((start - regionStart) / regionLength) * width;
          const w = Math.max(((end - start) / regionLength) * width, 2);
          return (
            <rect
              key={index}
              x={x}
              y={trackY}
              width={w}
              height={trackHeight}
              fill={cssVar('--color-segmental-duplication')}
            >
              <title>{interval.label}</title>
            </rect>
          );
        })}
      </svg>
      {data.length === 0 && (
        <div className="viz-empty-overlay">No segmental duplications/LCRs in this region</div>
      )}
    </div>
  );
};

export default SegmentalDuplicationTrack;
