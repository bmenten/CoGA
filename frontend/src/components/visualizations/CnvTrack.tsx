import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';

interface Cnv {
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

const TYPE_COLORS: Record<string, string> = {
  DEL: cssVar('--color-variant-del'),
  DUP: cssVar('--color-variant-dup'),
};

const CnvTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const navigate = useNavigate();
  const { data } = useQuery<Cnv[]>({
    queryKey: ['cnvs', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/cnvs/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Cnv[];
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
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
      {data.map((r, idx) => {
        const start = Math.max(r.start, regionStart);
        const end = Math.min(r.end, regionEnd);
        const x = ((start - regionStart) / regionLength) * width;
        const w = Math.max(((end - start) / regionLength) * width, 2);
        const typeKey = r.type?.toUpperCase() ?? '';
        const color = TYPE_COLORS[typeKey] || cssVar('--color-cnv-default');
        return (
          <rect
            key={idx}
            x={x}
            y={trackY}
            width={w}
            height={trackHeight}
            fill={color}
            className="cursor-pointer"
            onClick={() => {
              if (r.details_html) {
                navigate('/cnv-details', { state: { html: r.details_html } });
              }
            }}
          >
            <title>{r.label}</title>
          </rect>
        );
      })}
      </svg>
      {data.length === 0 && (
        <div className="viz-empty-overlay">No Clin CNVs in this region</div>
      )}
    </div>
  );
};

export default CnvTrack;
