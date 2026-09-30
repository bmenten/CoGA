import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import api from '../../lib/api';
import type { ApiVariantPage } from '../../lib/apiTypes';
import { formatGt, hasAltAllele } from '../../lib/genotypes';
import { cssVar } from '../../lib/colors';
import { getTrackVariantLimit } from '../../lib/trackSampling';
import VizLoadingOverlay from './VizLoadingOverlay';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import VizTooltip from './VizTooltip';
import { apiPath } from '../../lib/apiPath';

interface Genotype {
  sample: string;
  gt: string;
  read_support?: number;
  qual?: number;
  filter?: string;
}

interface Variant {
  chr: string;
  start: number;
  end: number;
  type: string;
  length?: number;
  source?: string;
  read_support?: number;
  qual?: number;
  filter?: string;
  genotypes?: Genotype[];
}

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  filters?: Record<string, string>;
}

const TYPE_ORDER = ['DEL', 'DUP', 'INV', 'INS', 'BND'] as const;

type VariantType = (typeof TYPE_ORDER)[number];

interface PositionedVariant extends Variant {
  x1: number;
  x2: number;
  y1: number;
  y2: number;
  typeKey: VariantType;
}

const isSupportedVariantType = (value: string): value is VariantType =>
  TYPE_ORDER.includes(value as VariantType);

/** The drawn SVs in words, for the chart's accessible name (#529): "3 (2 DEL, 1 DUP)". */
const describeTypes = (items: PositionedVariant[]): string => {
  const byType = TYPE_ORDER.map((typeKey) => ({
    typeKey,
    count: items.filter((item) => item.typeKey === typeKey).length,
  }))
    .filter(({ count }) => count > 0)
    .map(({ typeKey, count }) => `${count.toLocaleString()} ${typeKey}`);
  return `${items.length.toLocaleString()} (${byType.join(', ')})`;
};

const VariantTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  filters,
}) => {
  const typeColors = React.useMemo<Record<string, string>>(
    () => ({
      DEL: cssVar('--color-variant-del'),
      DUP: cssVar('--color-variant-dup'),
      INS: cssVar('--color-variant-ins'),
      INV: cssVar('--color-variant-inv'),
      BND: cssVar('--color-variant-bnd'),
    }),
    [],
  );
  // Fallback palette resolved once: cssVar() runs getComputedStyle, so it should
  // not be called per item inside the render map below.
  const fallbackColors = React.useMemo(
    () => ({
      default: cssVar('--color-variant-default'),
      white: cssVar('--color-white'),
      muted: cssVar('--color-text-muted'),
    }),
    [],
  );
  const pageSize = React.useMemo(() => getTrackVariantLimit(width), [width]);
  const regionInView = hasRegionInView(regionStart, regionEnd);
  const { data: rawData, isLoading, isError, refetch } = useQuery<ApiVariantPage<Variant>>({
    queryKey: [
      'variants',
      familyId,
      sampleId,
      chrom,
      regionStart,
      regionEnd,
      pageSize,
      filters,
    ],
    queryFn: async () => {
      const params: Record<string, unknown> = {
        chr: chrom,
        start: regionStart,
        end: regionEnd,
        overlap: true,
        page_size: pageSize,
        track_mode: true,
        sample: sampleId,
        ...(filters || {}),
      };
      const res = await api.get(apiPath`/families/${familyId}/structural-variants`, { params });
      return res.data as ApiVariantPage<Variant>;
    },
    enabled: regionInView,
  });
  const data = useSameSpanFallbackData(
    rawData,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${familyId}|${sampleId}|${chrom}`,
    isError,
  );

  // The view holds more SVs than one track page (or than the backend's candidate cap):
  // drawing the page would show only the left-most SVs, and the rest of the view as if
  // it had none (#585). Say so instead, as the small-variant track does past its cap.
  const tooManyVariants = Boolean(
    data && (data.total_is_estimated || data.total > (data.variants?.length ?? 0)),
  );
  const variants = React.useMemo(
    () =>
      (tooManyVariants ? [] : data?.variants || []).filter((v) => {
        const typeKey = v.type?.toUpperCase() ?? '';
        return (
          isSupportedVariantType(typeKey) &&
          v.genotypes?.some((g) => g.sample === sampleId && hasAltAllele(g.gt))
        );
      }),
    [data?.variants, sampleId, tooManyVariants],
  );
  const span = regionEnd - regionStart || 1;
  const rowHeight = React.useMemo(() => height / TYPE_ORDER.length, [height]);
  const items = React.useMemo<PositionedVariant[]>(() => {
    return variants
      .map((v) => {
        const typeKey = v.type.toUpperCase() as VariantType;
        const row = TYPE_ORDER.indexOf(typeKey);
        const x1 = ((v.start - regionStart) / span) * width;
        const x2 = ((v.end - regionStart) / span) * width;
        const y1 = row * rowHeight + 2;
        const y2 = (row + 1) * rowHeight - 2;
        return { ...v, x1, x2, y1, y2, typeKey };
      })
      .sort((left, right) => left.start - right.start);
  }, [regionStart, rowHeight, span, variants, width]);

  // The chart's accessible name (#529): what it shows now. A failure or a load is said
  // as such, never as zero SVs (#510).
  const typeSummary = React.useMemo(() => describeTypes(items), [items]);
  // A view with no width asks for nothing: it is not "none" (#602).
  const chartRegion = describeTrackRegion(chrom, regionStart, regionEnd);
  const chartState = isError
    ? 'failed to load'
    : !regionInView
      ? NO_REGION_IN_VIEW
      : isLoading
        ? 'loading'
        : tooManyVariants
          ? 'too many to display; zoom in or apply filters'
          : items.length === 0
            ? 'none'
            : typeSummary;
  const chartLabel = `Structural variants of ${sampleId} on ${chartRegion}: ${chartState}`;

  const [tooltip, setTooltip] = React.useState<{
    x: number;
    y: number;
    v: PositionedVariant;
  }>();

  const handleVariantPointer = (
    event: React.MouseEvent<SVGElement>,
    variant: PositionedVariant,
  ) => {
    setTooltip({
      x: event.clientX,
      y: event.clientY,
      v: variant,
    });
  };

  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height} role="img" aria-label={chartLabel}>
        {TYPE_ORDER.map((typeKey, index) => {
          const rowTop = index * rowHeight;
          const rowFill = typeColors[typeKey] || fallbackColors.default;
          return (
            <g key={typeKey}>
              <rect
                x={0}
                y={rowTop + 1}
                width={width}
                height={Math.max(rowHeight - 2, 1)}
                fill={rowFill}
                fillOpacity={0.06}
              />
              <text
                x={4}
                y={rowTop + 3}
                fill={fallbackColors.muted}
                fontSize={10}
                dominantBaseline="hanging"
              >
                {typeKey}
              </text>
            </g>
          );
        })}
        {!isLoading && !isError && items.length === 0 && (
          <text
            x={4}
            y={height / 2 + 4}
            fontSize={12}
            fill={fallbackColors.default}
          >
            {!regionInView
              ? 'No region in view'
              : tooManyVariants
                ? 'Too many SVs to display. Zoom in or apply filters.'
                : 'no SVs for this region / sample'}
          </text>
        )}
        {items.map((v, index) => {
          const color = typeColors[v.typeKey] || fallbackColors.default;
          const itemHeight = Math.max(v.y2 - v.y1, 2);
          const itemWidth = Math.max(v.x2 - v.x1, 1);
          const markerWidth = Math.max(itemWidth, 3);
          const midY = v.y1 + itemHeight / 2;
          const commonProps = {
            'data-variant-type': v.typeKey,
            onMouseMove: (event: React.MouseEvent<SVGElement>) => handleVariantPointer(event, v),
            onMouseLeave: () => setTooltip(undefined),
          };

          if (v.typeKey === 'INS') {
            return (
              <line
                key={`${v.typeKey}-${v.start}-${v.end}-${index}`}
                {...commonProps}
                x1={v.x1}
                x2={v.x1}
                y1={v.y1}
                y2={v.y2}
                stroke={color}
                strokeWidth={2}
              />
            );
          }

          if (v.typeKey === 'BND') {
            return (
              <path
                key={`${v.typeKey}-${v.start}-${v.end}-${index}`}
                {...commonProps}
                d={`M ${v.x1} ${midY} L ${v.x1 + markerWidth} ${v.y1} L ${v.x1 + markerWidth} ${v.y2} Z`}
                fill={color}
              />
            );
          }

          return (
            <rect
              key={`${v.typeKey}-${v.start}-${v.end}-${index}`}
              {...commonProps}
              x={v.x1}
              y={v.y1}
              width={itemWidth}
              height={itemHeight}
              fill={v.typeKey === 'INV' ? fallbackColors.white : color}
              fillOpacity={v.typeKey === 'INV' ? 1 : 0.82}
              stroke={v.typeKey === 'INV' ? color : undefined}
              strokeWidth={v.typeKey === 'INV' ? 2 : undefined}
            />
          );
        })}
      </svg>
      {isLoading && <VizLoadingOverlay message="Loading SVs" />}
      {isError && <VizErrorOverlay what="structural variants" onRetry={() => void refetch()} />}
      {tooltip && (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          <div>{`${tooltip.v.chr}:${tooltip.v.start}-${tooltip.v.end} ${tooltip.v.type}${
            tooltip.v.length !== undefined ? ` (len=${tooltip.v.length})` : ""
          }${tooltip.v.source ? ` [${tooltip.v.source}]` : ""}`}</div>
          {tooltip.v.genotypes?.map((g) => (
            <div key={g.sample}>{
              `${g.sample}: ${formatGt(g.gt)}${
                g.read_support !== undefined ? ` RS:${g.read_support}` : ""
              }${g.qual !== undefined ? ` Q:${g.qual}` : ""}${
                g.filter ? ` F:${g.filter}` : ""
              }`
            }</div>
          ))}
        </VizTooltip>
      )}
    </div>
  );
};

export default VariantTrack;
