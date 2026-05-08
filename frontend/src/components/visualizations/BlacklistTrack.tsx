import React from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";

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
  const { data } = useQuery<Region[]>({
    queryKey: ["blacklist", assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/blacklist/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Region[];
    },
    enabled: regionEnd > regionStart,
  });

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
          stroke={cssVar("--color-grid")}
          strokeWidth={1}
        />
      {data.map((r, idx) => {
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
