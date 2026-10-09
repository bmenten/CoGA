import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';

interface ReferenceInterval {
  start: number;
  end: number;
  label: string;
}

export interface ReferenceIntervalTrackProps {
  assembly: string;
  chrom: string;
  width: number;
  height: number;
  regionStart: number;
  regionEnd: number;
}

/** Which reference intervals a track draws: where it reads them, and how it names them. */
export interface ReferenceIntervalKind {
  /** The first part of the query key. */
  queryKey: string;
  /** The endpoint of a chromosome's intervals, built with apiPath. */
  path: (assembly: string, chrom: string) => string;
  /** The intervals in the track's accessible name: "<label> on chr22:…". */
  label: string;
  /** What could not be loaded, for the error overlay. */
  what: string;
  /** The theme colour of an interval's block. */
  fill: string;
  /** Said over a region that holds none. */
  emptyMessage: string;
}

/**
 * Reference intervals over the region in view, one block each, labelled on hover: the
 * blacklist (BlacklistTrack) and the segmental duplications/LCRs (SegmentalDuplicationTrack).
 */
const ReferenceIntervalTrack: React.FC<ReferenceIntervalTrackProps & { kind: ReferenceIntervalKind }> = ({
  kind,
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const { data: rawData, isError, refetch } = useQuery<ReferenceInterval[]>({
    queryKey: [kind.queryKey, assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(kind.path(assembly, chrom), {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as ReferenceInterval[];
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

  // The surface's name for a screen reader (#529): how many intervals are in view.
  const intervalsOn = `${kind.label} on ${describeTrackRegion(chrom, regionStart, regionEnd)}`;

  // A failed request must never read as an empty region (#510).
  if (isError) {
    return (
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} role="img" aria-label={`${intervalsOn}: failed to load`} />
        <VizErrorOverlay what={kind.what} onRetry={() => void refetch()} />
      </div>
    );
  }

  // A view with no width asks for nothing: it is neither loading nor empty (#602).
  if (!hasRegionInView(regionStart, regionEnd)) {
    return <svg width={width} height={height} role="img" aria-label={`${intervalsOn}: ${NO_REGION_IN_VIEW}`} />;
  }

  if (!data) {
    return <svg width={width} height={height} role="img" aria-label={`${intervalsOn}: loading`} />;
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
      <svg width={width} height={height} role="img" aria-label={`${intervalsOn}: ${inViewSummary}`}>
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
              fill={cssVar(kind.fill)}
            >
              <title>{interval.label}</title>
            </rect>
          );
        })}
      </svg>
      {data.length === 0 && <div className="viz-empty-overlay">{kind.emptyMessage}</div>}
    </div>
  );
};

export default ReferenceIntervalTrack;
