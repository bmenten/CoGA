import React, { useRef, useEffect, useState, useMemo } from 'react';
import { formatGt } from '../../lib/genotypes';
import { cssVar } from '../../lib/colors';
import { storage } from '../../lib/storage';
import VizLoadingOverlay from './VizLoadingOverlay';

interface Genotype {
  sample: string;
  gt: string;
}

interface Variant {
  chr: string;
  start: number;
  end: number;
  type: string;
  source?: string;
  genotypes?: Genotype[];
}

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
}

interface Props {
  url: string;
  layout: Layout | null;
  sampleId: string;
  width?: number;
  height?: number;
}

const TYPE_ORDER = ['DEL', 'DUP', 'INV', 'INS', 'BND'] as const;

type VariantType = (typeof TYPE_ORDER)[number];

interface PositionedVariant extends Variant {
  x1: number;
  x2: number;
  row: number;
  y1: number;
  y2: number;
  typeKey: VariantType;
}

const SvTrack: React.FC<Props> = ({
  url,
  layout,
  sampleId,
  width = 800,
  height = 40,
}) => {
  const [variants, setVariants] = useState<Variant[]>([]);
  const [loading, setLoading] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const typeColors = useMemo<Record<string, string>>(
    () => ({
      DEL: cssVar('--color-variant-del'),
      DUP: cssVar('--color-variant-dup'),
      INS: cssVar('--color-variant-ins'),
      INV: cssVar('--color-variant-inv'),
      BND: cssVar('--color-variant-bnd'),
    }),
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    async function load() {
      if (!layout) {
        if (active) {
          setVariants([]);
          setLoading(false);
        }
        return;
      }
      if (active) {
      setLoading(true);
      }
      const headers: Record<string, string> = {};
      const token = storage.getItem('token');
      if (token) headers.Authorization = `Bearer ${token}`;
      try {
        const res = await fetch(url, { headers, signal: controller.signal });
        if (!res.ok) {
          throw new Error(`SV track request failed with ${res.status}`);
        }
        const json = await res.json();
        const vars: Variant[] = (json.variants || [])
          .filter((v: Variant) =>
            Object.prototype.hasOwnProperty.call(typeColors, v.type)
          )
          .map((v: Variant) => ({
            ...v,
            genotypes: v.genotypes?.filter((g) => g.sample === sampleId),
          }))
          .filter(
            (v: Variant) =>
              v.genotypes &&
              v.genotypes.length > 0 &&
              formatGt(v.genotypes[0].gt) !== 'WT'
          );
        if (active) {
          setVariants(vars);
        }
      } catch {
        if (controller.signal.aborted) {
          return;
        }
        if (active) {
          setVariants([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    load();

    return () => {
      active = false;
      controller.abort();
    };
  }, [url, layout, sampleId, typeColors]);

  const rowHeight = useMemo(() => height / TYPE_ORDER.length, [height]);

  const items = useMemo<PositionedVariant[]>(() => {
    if (!layout) return [];
    return variants
      .map((v) => {
        const typeKey = v.type?.toUpperCase() as VariantType;
        const row = TYPE_ORDER.indexOf(typeKey);
        if (row < 0) return null;
        const chr =
          layout.offsets[v.chr] !== undefined ? v.chr : v.chr.replace(/^chr/i, '');
        const offset = layout.offsets[chr];
        if (offset === undefined) return null;
        const x1 = ((offset + v.start) / layout.total) * width;
        const x2 = ((offset + v.end) / layout.total) * width;
        const y1 = row * rowHeight + 2;
        const y2 = (row + 1) * rowHeight - 2;
        return { ...v, x1, x2, row, y1, y2, typeKey };
      })
      .filter(Boolean) as PositionedVariant[];
  }, [variants, layout, width, rowHeight]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, width, height);

    TYPE_ORDER.forEach((typeKey, index) => {
      const rowTop = index * rowHeight;
      const rowFill =
        typeColors[typeKey] || cssVar('--color-variant-default');
      ctx.globalAlpha = 0.06;
      ctx.fillStyle = rowFill;
      ctx.fillRect(0, rowTop + 1, width, Math.max(rowHeight - 2, 1));
      ctx.globalAlpha = 1;
      ctx.fillStyle = cssVar('--color-text-muted');
      ctx.font = '10px var(--font-sans, sans-serif)';
      ctx.textBaseline = 'top';
      ctx.fillText(typeKey, 4, rowTop + 3);
    });

    items.forEach((v) => {
      const color = typeColors[v.typeKey] || cssVar('--color-variant-default');
      const itemHeight = Math.max(v.y2 - v.y1, 2);
      const itemWidth = Math.max(v.x2 - v.x1, 1);
      const isInv = v.typeKey === 'INV';
      if (v.typeKey === 'INS') {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.moveTo(v.x1, v.y1);
        ctx.lineTo(v.x1, v.y2);
        ctx.stroke();
      } else if (v.typeKey === 'BND') {
        ctx.fillStyle = color;
        const markerWidth = Math.max(itemWidth, 3);
        ctx.beginPath();
        ctx.moveTo(v.x1, v.y1 + itemHeight / 2);
        ctx.lineTo(v.x1 + markerWidth, v.y1);
        ctx.lineTo(v.x1 + markerWidth, v.y2);
        ctx.closePath();
        ctx.fill();
      } else {
        ctx.globalAlpha = 0.82;
        ctx.fillStyle = isInv ? cssVar('--color-white') : color;
        ctx.fillRect(v.x1, v.y1, itemWidth, itemHeight);
        ctx.globalAlpha = 1;
        if (isInv) {
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.strokeRect(v.x1, v.y1, itemWidth, itemHeight);
        }
      }
    });
  }, [height, items, rowHeight, typeColors, width]);

  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    variant: PositionedVariant;
  }>();

  const handlePointerMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const hovered = [...items].reverse().find((item) => {
      if (y < item.y1 || y > item.y2) {
        return false;
      }
      if (item.typeKey === 'INS') {
        return Math.abs(x - item.x1) <= 3;
      }
      return x >= item.x1 && x <= item.x2;
    });
    if (!hovered) {
      setTooltip(undefined);
      return;
    }
    setTooltip({ x, y, variant: hovered });
  };

  return (
    <div className="relative" style={{ width, height }}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        onMouseMove={handlePointerMove}
        onMouseLeave={() => setTooltip(undefined)}
      />
      {loading && <VizLoadingOverlay message="Loading SVs" />}
      {!loading && items.length === 0 && (
        <div className="viz-empty-overlay">
          no SVs for this region / sample
        </div>
      )}
      {tooltip && (
        <div
          className="viz-tooltip"
          style={{ left: tooltip.x + 8, top: tooltip.y + 8 }}
        >
          <div>{`${tooltip.variant.chr}:${tooltip.variant.start}-${tooltip.variant.end} ${tooltip.variant.type}${
            tooltip.variant.source ? ` [${tooltip.variant.source}]` : ''
          }`}</div>
          {tooltip.variant.genotypes?.map((g) => (
            <div key={g.sample}>{`${g.sample}: ${formatGt(g.gt)}`}</div>
          ))}
        </div>
      )}
    </div>
  );
};

export default SvTrack;
