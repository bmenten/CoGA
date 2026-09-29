import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import { apiPath } from '../../lib/apiPath';

interface Region {
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

const BlacklistTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const { data: rawData, isError, refetch } = useQuery<Region[]>({
    queryKey: ["blacklist", assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/blacklist/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Region[];
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSameSpanFallbackData(
    isError ? null : rawData,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${assembly}|${chrom}`,
  );

  // The surface's name for a screen reader (#529): how many regions are in view.
  const regionsOn =
    `Blacklist regions on chr${chrom.replace(/^chr/i, "")}:` +
    `${regionStart.toLocaleString()}–${regionEnd.toLocaleString()}`;

  // A failed request must never read as an empty region (#510).
  if (isError) {
    return (
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} role="img" aria-label={`${regionsOn}: failed to load`} />
        <VizErrorOverlay what="blacklist regions" onRetry={() => void refetch()} />
      </div>
    );
  }

  if (!data) {
    return <svg width={width} height={height} role="img" aria-label={`${regionsOn}: loading`} />;
  }

  const regionLength = regionEnd - regionStart;
  const trackY = Math.max(2, Math.floor(height * 0.2));
  const trackHeight = Math.max(height - trackY * 2, 4);
  // Only what overlaps the region; see SegmentalDuplicationTrack (#526).
  const inView = data.filter((r) => r.end > regionStart && r.start < regionEnd);
  // Nothing in view while a pan's new window is still on its way is not "none".
  const inViewSummary =
    inView.length > 0 ? inView.length.toLocaleString() : rawData ? "none" : "loading";
  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height} role="img" aria-label={`${regionsOn}: ${inViewSummary}`}>
        <line
          x1={0}
          x2={width}
          y1={trackY + trackHeight / 2}
          y2={trackY + trackHeight / 2}
          stroke={cssVar("--color-grid")}
          strokeWidth={1}
        />
      {inView.map((r, idx) => {
        const start = Math.max(r.start, regionStart);
        const end = Math.min(r.end, regionEnd);
        const x = ((start - regionStart) / regionLength) * width;
        const w = Math.max(((end - start) / regionLength) * width, 2);
        return (
          <rect
            key={idx}
            x={x}
            y={trackY}
            width={w}
            height={trackHeight}
            fill={cssVar("--color-blacklist")}
          >
            <title>{r.label}</title>
          </rect>
        );
      })}
      </svg>
      {data.length === 0 && (
        <div className="viz-empty-overlay">No blacklist regions in this region</div>
      )}
    </div>
  );
};

export default BlacklistTrack;
