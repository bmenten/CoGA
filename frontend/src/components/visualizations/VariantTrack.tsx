import React from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiVariantPage } from '../../lib/apiTypes';
import { formatGt } from '../../lib/genotypes';
import { cssVar } from '../../lib/colors';
import { getTrackVariantLimit } from '../../lib/trackSampling';
import VizLoadingOverlay from './VizLoadingOverlay';

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
  const pageSize = React.useMemo(() => getTrackVariantLimit(width), [width]);
  const { data, isLoading } = useQuery<ApiVariantPage<Variant>>({
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
      const params: Record<string, any> = {
        chr: chrom,
        start: regionStart,
        end: regionEnd,
        overlap: true,
        page_size: pageSize,
        track_mode: true,
        sample: sampleId,
        ...(filters || {}),
      };
      const res = await api.get(`/families/${familyId}/structural-variants`, { params });
      return res.data as ApiVariantPage<Variant>;
    },
    enabled: regionEnd > regionStart,
  });

  const variants = React.useMemo(
    () =>
      (data?.variants || []).filter((v) => {
        const typeKey = v.type?.toUpperCase() ?? '';
        return (
          isSupportedVariantType(typeKey) &&
          v.genotypes?.some((g) => g.sample === sampleId && formatGt(g.gt) !== 'WT')
        );
      }),
    [data?.variants, sampleId],
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

  const [tooltip, setTooltip] = React.useState<{
    x: number;
    y: number;
    v: PositionedVariant;
  }>();

  const handleVariantPointer = (
    event: React.MouseEvent<SVGElement>,
    variant: PositionedVariant,
  ) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    setTooltip({
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
      v: variant,
    });
  };

  return (
    <div className="relative" style={{ width, height }}>
      <svg width={width} height={height}>
        {TYPE_ORDER.map((typeKey, index) => {
          const rowTop = index * rowHeight;
          const rowFill = typeColors[typeKey] || cssVar('--color-variant-default');
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
                fill={cssVar('--color-text-muted')}
                fontSize={10}
                dominantBaseline="hanging"
              >
                {typeKey}
              </text>
            </g>
          );
        })}
        {!isLoading && items.length === 0 && (
          <text
            x={4}
            y={height / 2 + 4}
            fontSize={12}
            fill={cssVar('--color-variant-default')}
          >
            no SVs for this region / sample
          </text>
        )}
        {items.map((v, index) => {
          const color = typeColors[v.typeKey] || cssVar('--color-variant-default');
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
              fill={v.typeKey === 'INV' ? cssVar('--color-white') : color}
              fillOpacity={v.typeKey === 'INV' ? 1 : 0.82}
              stroke={v.typeKey === 'INV' ? color : undefined}
              strokeWidth={v.typeKey === 'INV' ? 2 : undefined}
            />
          );
        })}
      </svg>
      {isLoading && <VizLoadingOverlay message="Loading SVs" />}
      {tooltip && (
        <div
          className="viz-tooltip"
          style={{ left: tooltip.x + 8, top: tooltip.y + 8 }}
        >
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
        </div>
      )}
    </div>
  );
};

export default VariantTrack;
