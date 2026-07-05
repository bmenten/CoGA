import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import { useNavigate } from 'react-router-dom';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import VizTooltip from './VizTooltip';

interface Cnv {
  _id: string;
  start: number;
  end: number;
  type?: string;
  label: string;
  details_html?: string;
}

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  height: number;
  regionStart: number;
  regionEnd: number;
}

const CnvTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = React.useState<{ x: number; y: number; label: string } | null>(
    null,
  );
  const { data: rawData } = useQuery<Cnv[]>({
    queryKey: ['cnvs', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/cnvs/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Cnv[];
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
      {data.map((r, idx) => {
        const start = Math.max(r.start, regionStart);
        const end = Math.min(r.end, regionEnd);
        const x = ((start - regionStart) / regionLength) * width;
        const w = Math.max(((end - start) / regionLength) * width, 2);
        // Clinical CNVs use a single orange accent (like genes use one blue),
        // independent of gain/loss type.
        const color = cssVar('--color-cnv-clinical');
        return (
          <rect
            key={idx}
            x={x}
            y={trackY}
            width={w}
            height={trackHeight}
            fill={color}
            className="cursor-pointer"
            aria-label={r.label}
            onMouseMove={(event) =>
              setTooltip({ x: event.clientX, y: event.clientY, label: r.label })
            }
            onMouseLeave={() => setTooltip(null)}
            onClick={() => navigate(`/cnv-details/${r._id}`)}
          />
        );
      })}
      </svg>
      {data.length === 0 && (
        <div className="viz-empty-overlay">No Clin CNVs in this region</div>
      )}
      {tooltip && (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          <div>{tooltip.label}</div>
        </VizTooltip>
      )}
    </div>
  );
};

export default CnvTrack;
