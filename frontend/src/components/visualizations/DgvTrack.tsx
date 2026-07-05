import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import VizTooltip from './VizTooltip';

type DgvClass = 'gain' | 'loss' | 'mixed' | 'other';

interface DgvVariant {
  chr: string;
  start: number;
  end: number;
  accession?: string | null;
  variant_type?: string | null;
  variant_subtype?: string | null;
  variant_class: DgvClass;
  frequency?: number | null;
  observed_gains?: number | null;
  observed_losses?: number | null;
  source?: string | null;
}

interface DgvDensityBin {
  start: number;
  end: number;
  gain: number;
  loss: number;
  mixed: number;
  other: number;
}

interface DgvTrackData {
  total: number;
  mode: 'lines' | 'density';
  bin_size: number;
  variants: DgvVariant[];
  bins: DgvDensityBin[];
}

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  height: number;
  regionStart: number;
  regionEnd: number;
}

// gain = blue, loss = red, mixed (gain+loss/complex) = purple, other = grey.
const CLASS_VAR: Record<DgvClass, string> = {
  gain: '--color-dgv-gain',
  loss: '--color-dgv-loss',
  mixed: '--color-dgv-mixed',
  other: '--color-dgv-other',
};
// Bidirectional layout: duplications/gains (and the minor mixed/other) sit above
// a centre baseline, deletions/losses below it — the standard CNV convention.
const UP_CLASSES: DgvClass[] = ['gain', 'mixed', 'other'];
const DOWN_CLASSES: DgvClass[] = ['loss'];
const LANE_GAP_PX = 1;
const LANE_MIN_HEIGHT = 2.5;

const dgvColor = (klass: DgvClass) => cssVar(CLASS_VAR[klass] ?? '--color-dgv-other');

const lineTooltip = (v: DgvVariant): React.ReactNode => (
  <div>
    <strong>{v.accession ?? 'DGV variant'}</strong>
    <div>{v.variant_subtype ?? v.variant_type ?? v.variant_class}</div>
    {typeof v.frequency === 'number' && v.frequency > 0 ? (
      <div>frequency {v.frequency.toFixed(4)}</div>
    ) : null}
  </div>
);

const densityTooltip = (b: DgvDensityBin, total: number): React.ReactNode => (
  <div>
    <strong>{total.toLocaleString()} DGV variants</strong>
    <div>
      gain {b.gain.toLocaleString()} · loss {b.loss.toLocaleString()} · mixed{' '}
      {b.mixed.toLocaleString()}
    </div>
  </div>
);

/**
 * DGV (Database of Genomic Variants) track. The server returns individual
 * variants when few enough overlap the view (`lines` mode, packed into lanes as
 * thin gain/loss-coloured bars), otherwise a per-bin density profile (`density`
 * mode) so a whole chromosome stays legible. Both modes are drawn around a
 * centre baseline: duplications/gains grow upward, deletions/losses downward.
 */
const DgvTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const [tooltip, setTooltip] = React.useState<{
    x: number;
    y: number;
    node: React.ReactNode;
  } | null>(null);

  const { data: rawData } = useQuery<DgvTrackData>({
    queryKey: ['dgv', assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/dgv/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as DgvTrackData;
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSameSpanFallbackData(rawData, (regionEnd ?? 0) - (regionStart ?? 0));

  if (!data) return <svg width={width} height={height} />;

  const regionLength = regionEnd - regionStart;
  // Tolerate an unexpected/empty response shape without crashing the track.
  const variants = Array.isArray(data.variants) ? data.variants : [];
  const bins = Array.isArray(data.bins) ? data.bins : [];
  const total = typeof data.total === 'number' ? data.total : variants.length;
  const mode: 'lines' | 'density' = data.mode === 'density' ? 'density' : 'lines';

  const packLanes = (group: DgvVariant[], maxLanes: number) => {
    const laneLastX: number[] = [];
    let overflow = 0;
    const placed = group
      .map((v) => {
        const s = Math.max(v.start, regionStart);
        const e = Math.min(v.end, regionEnd);
        const x = ((s - regionStart) / regionLength) * width;
        const w = Math.max(((e - s) / regionLength) * width, 1);
        return { v, x, w };
      })
      .sort((a, b) => a.x - b.x)
      .map((item) => {
        let lane = laneLastX.findIndex((lastX) => lastX <= item.x - LANE_GAP_PX);
        if (lane === -1) {
          if (laneLastX.length >= maxLanes) {
            overflow += 1;
            return null;
          }
          lane = laneLastX.length;
        }
        laneLastX[lane] = item.x + item.w;
        return { ...item, lane };
      })
      .filter((item): item is { v: DgvVariant; x: number; w: number; lane: number } =>
        item !== null,
      );
    return { placed, laneCount: Math.max(laneLastX.length, 1), overflow };
  };

  const renderLines = () => {
    const baseline = height / 2;
    const half = height / 2;
    const maxLanes = Math.max(1, Math.floor(half / LANE_MIN_HEIGHT));
    // Gains (and minor mixed/other) above the baseline, losses below it.
    const up = packLanes(variants.filter((v) => v.variant_class !== 'loss'), maxLanes);
    const down = packLanes(variants.filter((v) => v.variant_class === 'loss'), maxLanes);
    const upLaneH = half / up.laneCount;
    const downLaneH = half / down.laneCount;
    const upBarH = Math.max(upLaneH - LANE_GAP_PX, 1);
    const downBarH = Math.max(downLaneH - LANE_GAP_PX, 1);
    const overflow = up.overflow + down.overflow;

    const lineRect = (
      item: { v: DgvVariant; x: number; w: number; lane: number },
      y: number,
      barH: number,
      key: string,
    ) => (
      <rect
        key={key}
        x={item.x}
        y={y}
        width={item.w}
        height={barH}
        fill={dgvColor(item.v.variant_class)}
        className="cursor-pointer"
        aria-label={item.v.accession ?? item.v.variant_subtype ?? 'DGV variant'}
        onMouseMove={(event) =>
          setTooltip({ x: event.clientX, y: event.clientY, node: lineTooltip(item.v) })
        }
        onMouseLeave={() => setTooltip(null)}
      />
    );

    return (
      <>
        <line
          x1={0}
          x2={width}
          y1={baseline}
          y2={baseline}
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
        {up.placed.map((item, idx) =>
          lineRect(item, baseline - (item.lane + 1) * upLaneH, upBarH, `u${idx}`),
        )}
        {down.placed.map((item, idx) =>
          lineRect(item, baseline + item.lane * downLaneH, downBarH, `d${idx}`),
        )}
        {overflow > 0 ? (
          <text
            x={width - 2}
            y={height - 2}
            textAnchor="end"
            style={{ fill: cssVar('--color-text-muted'), fontSize: '9px' }}
          >
            +{overflow.toLocaleString()} more
          </text>
        ) : null}
      </>
    );
  };

  const renderDensity = () => {
    const baseline = height / 2;
    const half = height / 2;
    const sumOf = (b: DgvDensityBin, keys: DgvClass[]) => keys.reduce((s, k) => s + b[k], 0);
    // Scale up and down sides by the same factor so magnitudes stay comparable.
    const maxAmp = bins.reduce(
      (m, b) => Math.max(m, sumOf(b, UP_CLASSES), sumOf(b, DOWN_CLASSES)),
      0,
    );
    if (maxAmp === 0) return null;
    return (
      <>
        <line
          x1={0}
          x2={width}
          y1={baseline}
          y2={baseline}
          stroke={cssVar('--color-grid')}
          strokeWidth={1}
        />
        {bins.map((b, idx) => {
          const total = b.gain + b.loss + b.mixed + b.other;
          if (total === 0) return null;
          const x = ((b.start - regionStart) / regionLength) * width;
          const w = Math.max(((b.end - b.start) / regionLength) * width, 1);
          let yUp = baseline;
          const upRects = UP_CLASSES.map((klass) => {
            const n = b[klass];
            if (n === 0) return null;
            const segH = (n / maxAmp) * half;
            yUp -= segH;
            return <rect key={`u${klass}`} x={x} y={yUp} width={w} height={segH} fill={dgvColor(klass)} />;
          });
          let yDown = baseline;
          const downRects = DOWN_CLASSES.map((klass) => {
            const n = b[klass];
            if (n === 0) return null;
            const segH = (n / maxAmp) * half;
            const rect = (
              <rect key={`d${klass}`} x={x} y={yDown} width={w} height={segH} fill={dgvColor(klass)} />
            );
            yDown += segH;
            return rect;
          });
          return (
            <g key={idx}>
              {upRects}
              {downRects}
              <rect
                x={x}
                y={0}
                width={w}
                height={height}
                fill="transparent"
                className="cursor-pointer"
                onMouseMove={(event) =>
                  setTooltip({ x: event.clientX, y: event.clientY, node: densityTooltip(b, total) })
                }
                onMouseLeave={() => setTooltip(null)}
              />
            </g>
          );
        })}
      </>
    );
  };

  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height}>
        {mode === 'lines' ? renderLines() : renderDensity()}
      </svg>
      {total === 0 ? (
        <div className="viz-empty-overlay">No DGV variants in this region</div>
      ) : null}
      {mode === 'density' && total > 0 ? (
        <div
          style={{
            position: 'absolute',
            top: 2,
            right: 4,
            fontSize: '10px',
            color: cssVar('--color-text-muted'),
            pointerEvents: 'none',
          }}
        >
          density · {total.toLocaleString()} variants
        </div>
      ) : null}
      {tooltip ? (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          {tooltip.node}
        </VizTooltip>
      ) : null}
    </div>
  );
};

export default DgvTrack;
