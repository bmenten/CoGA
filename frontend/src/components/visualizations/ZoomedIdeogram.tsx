import React from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import { getStainColor } from "../../lib/stainColors";

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

const getAcenDirection = (
  band: Pick<IdeogramBand, "name" | "start" | "end">,
  chromLength: number,
): "p" | "q" => {
  const bandName = `${band.name || ""}`.toLowerCase();
  if (/(^|[^a-z])p\d/.test(bandName) || bandName.startsWith("p")) return "p";
  if (/(^|[^a-z])q\d/.test(bandName) || bandName.startsWith("q")) return "q";
  return band.start + band.end <= chromLength ? "p" : "q";
};

const blendHex = (hex: string, target: string, amount: number): string => {
  const normalize = (value: string) => {
    if (!value.startsWith("#")) return null;
    const trimmed = value.slice(1);
    if (trimmed.length === 3) {
      return trimmed
        .split("")
        .map((char) => char + char)
        .join("");
    }
    if (trimmed.length === 6) return trimmed;
    return null;
  };

  const sourceHex = normalize(hex);
  const targetHex = normalize(target);
  if (!sourceHex || !targetHex) return hex;

  const source = [
    parseInt(sourceHex.slice(0, 2), 16),
    parseInt(sourceHex.slice(2, 4), 16),
    parseInt(sourceHex.slice(4, 6), 16),
  ];
  const destination = [
    parseInt(targetHex.slice(0, 2), 16),
    parseInt(targetHex.slice(2, 4), 16),
    parseInt(targetHex.slice(4, 6), 16),
  ];

  const blended = source.map((component, index) =>
    Math.round(component + (destination[index] - component) * amount),
  );

  return `#${blended.map((component) => component.toString(16).padStart(2, "0")).join("")}`;
};

const formatBp = (bp: number): string => {
  if (bp >= 1_000_000) return `${(bp / 1_000_000).toFixed(2)} Mb`;
  if (bp >= 1_000) return `${(bp / 1_000).toFixed(2)} kb`;
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
  const { data } = useQuery<Chromosome>({
    queryKey: ["chromosome", assembly, chrom],
    queryFn: async () => {
      const res = await api.get(`/chromosomes/${assembly}/${chrom}`);
      return res.data as Chromosome;
    },
  });

  if (!data || regionEnd <= regionStart) {
    return <svg width={width} height={height} />;
  }

  const viewHeight = Math.max(height - AXIS_HEIGHT, 0);
  const regionLength = Math.max(regionEnd - regionStart, 1);

  const bands = data.bands.filter(
    (b) => b.end > regionStart && b.start < regionEnd
  );

  const minTickSpacingPx = 60;
  const maxTickCount = Math.max(Math.floor(width / minTickSpacingPx), 1);
  const roughTickInterval = regionLength / maxTickCount;
  const exponent = Math.floor(Math.log10(roughTickInterval));
  const base = Math.pow(10, exponent);
  const fraction = roughTickInterval / base;
  let niceFraction: number;
  if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  else niceFraction = 10;
  const tickInterval = Math.max(1, niceFraction * base);
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
        <stop offset="0%" stopColor={blendHex(color, "#ffffff", 0.28)} />
        <stop offset="18%" stopColor={blendHex(color, "#ffffff", 0.14)} />
        <stop offset="52%" stopColor={color} />
        <stop offset="100%" stopColor={blendHex(color, "#000000", 0.12)} />
      </linearGradient>
    );
  });

  return (
    <svg width={width} height={height}>
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
            >
              <title>{band.name}</title>
            </polygon>
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
            >
              <title>{band.name}</title>
            </path>
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
            >
              <title>{band.name}</title>
            </path>
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
          >
            <title>{band.name}</title>
          </rect>
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
              {formatBp(t)}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

export default ZoomedIdeogram;
