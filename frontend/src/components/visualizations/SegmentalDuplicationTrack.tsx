import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import { apiPath } from '../../lib/apiPath';

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
  const { data: rawData, isError, refetch } = useQuery<SegmentalDuplication[]>({
    queryKey: ['segmental-duplications', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/segmental-duplications/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as SegmentalDuplication[];
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSameSpanFallbackData(
    rawData,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${assembly}|${chrom}`,
    isError,
  );

  // The surface's name for a screen reader (#529): how many duplications are in view.
  const segdupsOn = `Segmental duplications on ${describeTrackRegion(chrom, regionStart, regionEnd)}`;

  // A failed request must never read as an empty region (#510).
  if (isError) {
    return (
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} role="img" aria-label={`${segdupsOn}: failed to load`} />
        <VizErrorOverlay what="segmental duplications" onRetry={() => void refetch()} />
      </div>
    );
  }

  // A view with no width asks for nothing: it is neither loading nor empty (#602).
  if (!hasRegionInView(regionStart, regionEnd)) {
    return <svg width={width} height={height} role="img" aria-label={`${segdupsOn}: ${NO_REGION_IN_VIEW}`} />;
  }

  if (!data) {
    return <svg width={width} height={height} role="img" aria-label={`${segdupsOn}: loading`} />;
  }

  const regionLength = regionEnd - regionStart;
  const trackY = Math.max(2, Math.floor(height * 0.2));
  const trackHeight = Math.max(height - trackY * 2, 4);
  // Only what overlaps the region: while a pan loads, the previous window's intervals
  // are held, and one left of the new window was drawn as a 2 px block at its left
  // edge, under its own label (#526).
  const inView = data.filter(
    (interval) => interval.end > regionStart && interval.start < regionEnd,
  );
  // Nothing in view while a pan's new window is still on its way is not "none".
  const inViewSummary =
    inView.length > 0 ? inView.length.toLocaleString() : rawData ? 'none' : 'loading';
  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height} role="img" aria-label={`${segdupsOn}: ${inViewSummary}`}>
        <line
          x1={0}
          x2={width}
          y1={trackY + trackHeight / 2}
          y2={trackY + trackHeight / 2}
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
        {inView.map((interval, index) => {
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
