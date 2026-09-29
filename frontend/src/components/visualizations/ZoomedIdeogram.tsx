import React from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import { getStainColor } from "../../lib/stainColors";
import { getAcenDirection, getBandGradientStops, niceTickInterval } from "../../lib/ideogram";
import VizTooltip from "./VizTooltip";
import VizErrorOverlay from "./VizErrorOverlay";
import { apiPath } from '../../lib/apiPath';

interface IdeogramBand {
  name: string;
  start: number;
  end: number;
  stain: string;
}

interface Chromosome {
  chr: string;
  size: number;
  bands: IdeogramBand[];
}

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  height: number;
  regionStart: number;
  regionEnd: number;
}

const AXIS_HEIGHT = 20;
const BAND_STROKE = 0.5;

// As many decimals as the tick spacing needs, and at least two: at gene-level zoom the
// ticks are hundreds of bp apart, and two decimals of a Mb made most of them read the
// same, each up to 5 kb off its position (#526).
const formatBp = (bp: number, tickInterval: number): string => {
  const scaled = (unit: number, suffix: string) => {
    const decimals = Math.max(2, Math.ceil(-Math.log10(tickInterval / unit)));
    return `${(bp / unit).toFixed(Math.min(decimals, 6))} ${suffix}`;
  };
  if (bp >= 1_000_000) return scaled(1_000_000, "Mb");
  if (bp >= 1_000) return scaled(1_000, "kb");
  return `${bp} bp`;
};

const ZoomedIdeogram: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
}) => {
  const { data, isError, refetch } = useQuery<Chromosome>({
    queryKey: ["chromosome", assembly, chrom],
    queryFn: async () => {
      const res = await api.get(apiPath`/chromosomes/${assembly}/${chrom}`);
      return res.data as Chromosome;
    },
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const [bandTooltip, setBandTooltip] = React.useState<{
    x: number;
    y: number;
    name: string;
  } | null>(null);

  // The surface's name for a screen reader (#529): the cytobands in the region, in ISCN
  // form (13q13.1) like the band tooltip. A failure is never "none" (#510).
  const chromName = chrom.replace(/^chr/i, "");
  const bandsOn =
    `Cytobands on chr${chromName}:` +
    `${regionStart.toLocaleString()}–${regionEnd.toLocaleString()}`;

  if (isError) {
    return (
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} role="img" aria-label={`${bandsOn}: failed to load`} />
        <VizErrorOverlay what="the chromosome ideogram" onRetry={() => void refetch()} />
      </div>
    );
  }

  if (!data || regionEnd <= regionStart) {
    return (
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={data ? `Cytobands on chr${chromName}: no region in view` : `${bandsOn}: loading`}
      />
    );
  }

  const bandHoverHandlers = (band: IdeogramBand) => ({
    onMouseMove: (e: React.MouseEvent) =>
      setBandTooltip({ x: e.clientX, y: e.clientY, name: band.name }),
    onMouseLeave: () => setBandTooltip(null),
  });

  const viewHeight = Math.max(height - AXIS_HEIGHT, 0);
  const regionLength = Math.max(regionEnd - regionStart, 1);

  const bands = data.bands.filter(
    (b) => b.end > regionStart && b.start < regionEnd
  );
  const bandNames = bands.slice(0, 3).map((band) => `${chromName}${band.name}`).join(", ");
  const ariaLabel = bands.length
    ? `${bandsOn}: ${bands.length.toLocaleString()} (${bandNames}${bands.length > 3 ? ", …" : ""})`
    : `${bandsOn}: none`;

  const tickInterval = niceTickInterval(regionLength, width);
  const tickValues: number[] = [regionStart];
  for (
    let pos = Math.ceil(regionStart / tickInterval) * tickInterval;
    pos < regionEnd;
    pos += tickInterval
  ) {
    tickValues.push(pos);
  }
  tickValues.push(regionEnd);
  const ticks = Array.from(new Set(tickValues)).sort((a, b) => a - b);
  // Gradients for bands to add subtle rounding effect
  const bandGradients = bands.map((band, i) => {
    const color = getStainColor(band.stain);
    const id = `zoomed-ideogram-gradient-${chrom}-${i}`;
    return (
      <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
        {getBandGradientStops(color, "glossy").map((stop) => (
          <stop
            key={`${id}-${stop.offset}`}
            offset={stop.offset}
            stopColor={stop.stopColor}
            stopOpacity={stop.stopOpacity}
          />
        ))}
      </linearGradient>
    );
  });

  return (
    <>
    <svg width={width} height={height} role="img" aria-label={ariaLabel}>
      <defs>{bandGradients}</defs>
      {bands.map((band, i) => {
        const start = Math.max(band.start, regionStart);
        const end = Math.min(band.end, regionEnd);
        const x = ((start - regionStart) / regionLength) * width;
        const bandWidth = ((end - start) / regionLength) * width;
        const gradientId = `zoomed-ideogram-gradient-${chrom}-${i}`;
        const isTelomereStart = band.start === 0 && regionStart === 0;
        const isTelomereEnd = band.end === data.size && regionEnd === data.size;
        const r = Math.min(viewHeight / 2, bandWidth);

        if (band.stain === "acen") {
          const dir = getAcenDirection(band, data.size);
          const points =
            dir === "p"
              ? `${x},0 ${x + bandWidth},${viewHeight / 2} ${x},${viewHeight}`
              : `${x},${viewHeight / 2} ${x + bandWidth},0 ${x + bandWidth},${viewHeight}`;
          return (
            <polygon
              key={i}
              points={points}
              fill={`url(#${gradientId})`}
              stroke={cssVar("--color-black")}
              strokeWidth={BAND_STROKE}
              {...bandHoverHandlers(band)}
            />
          );
        }
        if (isTelomereStart) {
          const d = `M ${x + bandWidth} 0 H ${x + r} A ${r} ${r} 0 0 0 ${x + r} ${viewHeight} H ${x + bandWidth} Z`;
          return (
            <path
              key={i}
              d={d}
              fill={`url(#${gradientId})`}
              stroke={cssVar("--color-black")}
              strokeWidth={BAND_STROKE}
              {...bandHoverHandlers(band)}
            />
          );
        }
        if (isTelomereEnd) {
          const d = `M ${x} 0 H ${x + bandWidth - r} A ${r} ${r} 0 0 1 ${x + bandWidth - r} ${viewHeight} H ${x} Z`;
          return (
            <path
              key={i}
              d={d}
              fill={`url(#${gradientId})`}
              stroke={cssVar("--color-black")}
              strokeWidth={BAND_STROKE}
              {...bandHoverHandlers(band)}
            />
          );
        }
        return (
          <rect
            key={i}
            x={x}
            y={0}
            width={bandWidth}
            height={viewHeight}
            fill={`url(#${gradientId})`}
            stroke={cssVar("--color-black")}
            strokeWidth={BAND_STROKE}
            {...bandHoverHandlers(band)}
          />
        );
      })}
      <line
        x1={0}
        x2={0}
        y1={0}
        y2={viewHeight}
        stroke={cssVar("--color-signature-red")}
        strokeWidth={1}
        pointerEvents="none"
      />
      <line
        x1={width}
        x2={width}
        y1={0}
        y2={viewHeight}
        stroke={cssVar("--color-signature-red")}
        strokeWidth={1}
        pointerEvents="none"
      />
      {ticks.map((t) => {
        const x = ((t - regionStart) / regionLength) * width;
        return (
          <g key={t} pointerEvents="none">
            <line
              x1={x}
              x2={x}
              y1={viewHeight}
              y2={viewHeight + 6}
              stroke={cssVar("--color-black")}
              strokeWidth={BAND_STROKE}
            />
            <text
              x={x}
              y={viewHeight + 16}
              fontSize={10}
              textAnchor="middle"
            >
              {formatBp(t, tickInterval)}
            </text>
          </g>
        );
      })}
    </svg>
    {bandTooltip && (
      <VizTooltip x={bandTooltip.x} y={bandTooltip.y}>
        <div>{`${chrom.replace(/^chr/i, "")}${bandTooltip.name}`}</div>
      </VizTooltip>
    )}
    </>
  );
};

export default ZoomedIdeogram;
