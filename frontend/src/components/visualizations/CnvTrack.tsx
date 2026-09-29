import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import { useNavigate } from 'react-router';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import VizTooltip from './VizTooltip';
import { apiPath } from '../../lib/apiPath';

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

// The CNV names are free text from the reference file, some near 100 characters long
// ("1q21.1 recurrent (TAR syndrome) region (proximal and distal, BP1-BP4) (includes …)").
const MAX_LABEL_LENGTH = 150;

// The surface's name for a screen reader (#529): how many clinical CNVs are in view and
// as many of the first three names as fit in about 150 characters, then "…".
const describeCnvs = (lead: string, cnvs: Cnv[]): string => {
  const head = `${lead}: ${cnvs.length.toLocaleString()} (`;
  const budget = MAX_LABEL_LENGTH - head.length - ', …)'.length;
  let named = cnvs.slice(0, 3).map((cnv) => cnv.label.trim());
  while (named.length > 1 && named.join(', ').length > budget) named = named.slice(0, -1);
  const names = named.join(', ');
  const shown = names.length > budget ? `${names.slice(0, budget - 1)}…` : names;
  return `${head}${shown}${named.length < cnvs.length ? ', …' : ''})`;
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
  const [tooltip, setTooltip] = React.useState<{ x: number; y: number; label: string } | null>(
    null,
  );
  const { data: rawData, isError, refetch } = useQuery<Cnv[]>({
    queryKey: ['cnvs', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/cnvs/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Cnv[];
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

  const cnvsOn =
    `Clinical CNVs on chr${chrom.replace(/^chr/i, '')}:` +
    `${regionStart.toLocaleString()}–${regionEnd.toLocaleString()}`;

  // A failed request must never read as an empty region (#510).
  if (isError) {
    return (
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} role="img" aria-label={`${cnvsOn}: failed to load`} />
        <VizErrorOverlay what="clinical CNV regions" onRetry={() => void refetch()} />
      </div>
    );
  }

  if (!data) {
    return <svg width={width} height={height} role="img" aria-label={`${cnvsOn}: loading`} />;
  }

  const regionLength = regionEnd - regionStart;
  const trackY = Math.max(2, Math.floor(height * 0.2));
  const trackHeight = Math.max(height - trackY * 2, 4);
  // Only what overlaps the region: while a pan loads, the previous window's regions
  // are held, and one left of the new window was drawn at its left edge under its own
  // name, as if that clinical CNV lay there (#526).
  const inView = data.filter((r) => r.end > regionStart && r.start < regionEnd);
  // Nothing in view while a pan's new window is still on its way is not "none".
  const ariaLabel =
    inView.length > 0
      ? describeCnvs(cnvsOn, inView)
      : `${cnvsOn}: ${rawData ? 'none' : 'loading'}`;
  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel}>
        <line
          x1={0}
          x2={width}
          y1={trackY + trackHeight / 2}
          y2={trackY + trackHeight / 2}
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
      {inView.map((r, idx) => {
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
