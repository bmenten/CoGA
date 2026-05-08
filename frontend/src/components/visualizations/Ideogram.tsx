import React, { useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import {
  collapseBandsForResolution,
  getAcenDirection,
  getBandGradientStops,
} from "../../lib/ideogram";
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
  onRegionSelect?: (start: number, end: number) => void;
  /** Whether to render the axis with tick marks and labels. */
  showAxis?: boolean;
  bandResolution?: "full" | "compact";
  cornerRoundness?: number;
  bandFinish?: "standard" | "glossy";
}

const AXIS_HEIGHT = 20;
const BAND_STROKE = 0.5;

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const buildChromosomeOutlinePath = ({
  width,
  ideogramY,
  ideogramInnerHeight,
  capsuleRadius,
  chromLength,
  acenBands,
}: {
  width: number;
  ideogramY: number;
  ideogramInnerHeight: number;
  capsuleRadius: number;
  chromLength: number;
  acenBands: IdeogramBand[];
}) => {
  if (acenBands.length !== 2) return null;

  const sortedAcenBands = [...acenBands].sort((left, right) => left.start - right.start);
  const [firstAcen, secondAcen] = sortedAcenBands;
  if (
    getAcenDirection(firstAcen, chromLength) !== "p" ||
    getAcenDirection(secondAcen, chromLength) !== "q"
  ) {
    return null;
  }

  const topY = ideogramY;
  const bottomY = ideogramY + ideogramInnerHeight;
  const midY = ideogramY + ideogramInnerHeight / 2;
  const leftX = BAND_STROKE / 2;
  const rightX = Math.max(width - BAND_STROKE / 2, leftX + 1);
  const pStartX = (firstAcen.start / chromLength) * width;
  const centerX = (((firstAcen.end + secondAcen.start) / 2) / chromLength) * width;
  const qEndX = (secondAcen.end / chromLength) * width;

  if (
    !Number.isFinite(pStartX) ||
    !Number.isFinite(centerX) ||
    !Number.isFinite(qEndX) ||
    pStartX <= leftX + capsuleRadius ||
    qEndX >= rightX - capsuleRadius ||
    centerX <= pStartX ||
    centerX >= qEndX
  ) {
    return null;
  }

  return [
    `M ${leftX + capsuleRadius} ${topY}`,
    `H ${pStartX}`,
    `L ${centerX} ${midY}`,
    `L ${qEndX} ${topY}`,
    `H ${rightX - capsuleRadius}`,
    `A ${capsuleRadius} ${capsuleRadius} 0 0 1 ${rightX - capsuleRadius} ${bottomY}`,
    `H ${qEndX}`,
    `L ${centerX} ${midY}`,
    `L ${pStartX} ${bottomY}`,
    `H ${leftX + capsuleRadius}`,
    `A ${capsuleRadius} ${capsuleRadius} 0 0 1 ${leftX + capsuleRadius} ${topY}`,
    "Z",
  ].join(" ");
};

const formatBp = (bp: number): string => {
  if (bp >= 1_000_000) return `${Math.round(bp / 1_000_000)} Mb`;
  if (bp >= 1_000) return `${Math.round(bp / 1_000)} kb`;
  return `${bp} bp`;
};

const Ideogram: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  height,
  regionStart,
  regionEnd,
  onRegionSelect,
  showAxis = true,
  bandResolution = "full",
  cornerRoundness = 0.5,
  bandFinish = "standard",
}) => {
  const clipId = useId().replace(/:/g, "");
  const dragStart = useRef<number | null>(null);
  const [dragCurrent, setDragCurrent] = useState<number | null>(null);
  const { data } = useQuery<Chromosome>({
    queryKey: ["chromosome", assembly, chrom],
    queryFn: async () => {
      const res = await api.get(`/chromosomes/${assembly}/${chrom}`);
      return res.data as Chromosome;
    },
  });
  const chromLength = data?.size ?? 1;
  const axisHeight = showAxis ? AXIS_HEIGHT : 0;
  const ideogramHeight = Math.max(height - axisHeight, 0);
  const ideogramY = BAND_STROKE / 2;
  const ideogramInnerHeight = Math.max(ideogramHeight - BAND_STROKE, 0);
  const capsuleRadius = clamp(ideogramInnerHeight * cornerRoundness, 0, ideogramInnerHeight / 2);
  const start = Math.max(0, regionStart);
  const end = Math.min(chromLength, regionEnd);
  const startPx = (start / chromLength) * width;
  const endPx = (end / chromLength) * width;
  const regionWidth = Math.max(endPx - startPx, 1);
  const showHighlight = regionWidth < width;

  const minTickSpacingPx = 60;
  const ticks: number[] = [];
  if (showAxis) {
    const maxTickCount = Math.max(Math.floor(width / minTickSpacingPx), 1);
    const roughTickInterval = chromLength / maxTickCount;
    const exponent = Math.floor(Math.log10(roughTickInterval));
    const base = Math.pow(10, exponent);
    const fraction = roughTickInterval / base;
    let niceFraction: number;
    if (fraction <= 1) niceFraction = 1;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 5) niceFraction = 5;
    else niceFraction = 10;
    const tickInterval = niceFraction * base;
    for (let pos = 0; pos <= chromLength; pos += tickInterval) {
      ticks.push(pos);
    }
  }

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onRegionSelect || !data) return;
    dragStart.current = e.nativeEvent.offsetX;
    setDragCurrent(e.nativeEvent.offsetX);
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (dragStart.current === null) return;
    setDragCurrent(e.nativeEvent.offsetX);
  };

  const handleMouseUp = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onRegionSelect || !data || dragStart.current === null) {
      dragStart.current = null;
      setDragCurrent(null);
      return;
    }
    const startX = dragStart.current;
    const endX = e.nativeEvent.offsetX;
    dragStart.current = null;
    setDragCurrent(null);
    const x1 = Math.min(startX, endX);
    const x2 = Math.max(startX, endX);
    if (Math.abs(x2 - x1) < 5) return;
    const newStart = Math.floor((x1 / width) * chromLength);
    const newEnd = Math.ceil((x2 / width) * chromLength);
    onRegionSelect(newStart, newEnd);
  };

  const rectX =
    dragStart.current !== null && dragCurrent !== null
      ? Math.min(dragStart.current, dragCurrent)
      : 0;
  const rectWidth =
    dragStart.current !== null && dragCurrent !== null
      ? Math.abs(dragCurrent - dragStart.current)
      : 0;

  const renderBands = useMemo(
    () => collapseBandsForResolution(data?.bands ?? [], chromLength, width, bandResolution),
    [data?.bands, chromLength, width, bandResolution],
  );

  const bandGradients = renderBands.map((band, i) => {
    const color = getStainColor(band.stain);
    const id = `ideogram-gradient-${chrom}-${bandResolution}-${bandFinish}-${i}`;
    return (
      <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
        {getBandGradientStops(color, bandFinish).map((stop) => (
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
  const outlinePath = useMemo(
    () =>
      buildChromosomeOutlinePath({
        width,
        ideogramY,
        ideogramInnerHeight,
        capsuleRadius,
        chromLength,
        acenBands: renderBands.filter((band) => band.stain === "acen"),
      }),
    [width, ideogramY, ideogramInnerHeight, capsuleRadius, chromLength, renderBands],
  );

  if (!data) {
    return <svg width={width} height={height} />;
  }

  return (
    <svg
      width={width}
      height={height}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
    >
      <defs>
        {bandGradients}
        <clipPath id={clipId}>
          <rect
            x={BAND_STROKE / 2}
            y={ideogramY}
            width={Math.max(width - BAND_STROKE, 0)}
            height={ideogramInnerHeight}
            rx={capsuleRadius}
            ry={capsuleRadius}
          />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        {renderBands.map((band, i) => {
          const x = (band.start / chromLength) * width;
          const bandWidth = ((band.end - band.start) / chromLength) * width;
          const gradientId = `ideogram-gradient-${chrom}-${bandResolution}-${bandFinish}-${i}`;
          if (band.stain === "acen") {
            const direction = getAcenDirection(band, chromLength);
            const points =
              direction === "p"
                ? `${x},${ideogramY} ${x + bandWidth},${ideogramY + ideogramInnerHeight / 2} ${x},${ideogramY + ideogramInnerHeight}`
                : `${x},${ideogramY + ideogramInnerHeight / 2} ${x + bandWidth},${ideogramY} ${x + bandWidth},${ideogramY + ideogramInnerHeight}`;
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
          return (
            <rect
              key={i}
              x={x}
              y={ideogramY}
              width={bandWidth}
              height={ideogramInnerHeight}
              fill={`url(#${gradientId})`}
              stroke={cssVar("--color-black")}
              strokeWidth={BAND_STROKE}
            >
              <title>{band.name}</title>
            </rect>
          );
        })}
      </g>
      {outlinePath ? (
        <path
          d={outlinePath}
          fill="none"
          stroke={cssVar("--color-black")}
          strokeWidth={BAND_STROKE}
        />
      ) : (
        <rect
          x={BAND_STROKE / 2}
          y={ideogramY}
          width={Math.max(width - BAND_STROKE, 0)}
          height={ideogramInnerHeight}
          rx={capsuleRadius}
          ry={capsuleRadius}
          fill="none"
          stroke={cssVar("--color-black")}
          strokeWidth={BAND_STROKE}
        />
      )}
      {showHighlight && (
        <rect
          x={startPx}
          y={0}
          width={regionWidth}
          height={ideogramHeight}
          fill={cssVar("--color-signature-red-soft")}
          stroke={cssVar("--color-signature-red")}
          strokeWidth={BAND_STROKE}
          pointerEvents="none"
        />
      )}
      {dragStart.current !== null && dragCurrent !== null && (
        <rect
          x={rectX}
          y={0}
          width={rectWidth}
          height={ideogramHeight}
          fill={cssVar("--color-signature-red-soft")}
          stroke={cssVar("--color-signature-red")}
          strokeDasharray="4"
          strokeWidth={BAND_STROKE}
          pointerEvents="none"
        />
      )}
      {showAxis &&
        ticks.map((t) => {
          const x = (t / chromLength) * width;
          return (
            <g key={t} pointerEvents="none">
              <line
                x1={x}
                x2={x}
                y1={ideogramHeight}
                y2={ideogramHeight + 6}
                stroke={cssVar("--color-black")}
                strokeWidth={BAND_STROKE}
              />
              <text
                x={x}
                y={ideogramHeight + 16}
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

export default Ideogram;
