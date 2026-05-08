import React, { useEffect, useMemo, useRef, useState } from 'react';
import { cssVar } from '../../lib/colors';
import { storage } from '../../lib/storage';
import VizLoadingOverlay from './VizLoadingOverlay';

const DEFAULT_CHROMS = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  'X',
  'Y',
];

const normalizeChrom = (value: string): string =>
  value.toLowerCase().startsWith('chr') ? value.slice(3) : value;

interface ApcadBin {
  chr: string;
  start: number;
  end: number;
  value: number;
  origin: string;
}

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
}

interface ApcadTrackData {
  bins: ApcadBin[];
}

interface BedRecordPayload<T> {
  items: T[];
}

const fetchJsonOrNull = async <T,>(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<BedRecordPayload<T> | null> => {
  try {
    const response = await fetch(url, { headers, signal });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as BedRecordPayload<T>;
  } catch {
    if (signal.aborted) {
      throw new DOMException('Aborted', 'AbortError');
    }
    return null;
  }
};

const splitKey = (key: string): string[] => (key ? key.split('\n').filter(Boolean) : []);

const deriveLayoutFromBins = (
  bins: ApcadBin[],
  chroms: string[],
  regionStart?: number,
  regionEnd?: number,
): Layout => {
  const lengths: Record<string, number> = {};
  chroms.forEach((chrom) => {
    lengths[chrom] = 0;
  });

  bins.forEach((bin) => {
    lengths[bin.chr] = Math.max(lengths[bin.chr] ?? 0, bin.end);
  });

  const offsets: Record<string, number> = {};
  let total = 0;
  chroms.forEach((chrom) => {
    offsets[chrom] = total;
    total += lengths[chrom] ?? 0;
  });

  if (
    regionStart !== undefined &&
    regionEnd !== undefined &&
    chroms.length === 1
  ) {
    total = regionEnd - regionStart;
  }

  return { offsets, lengths, total };
};

interface Props {
  apcadUrls: string[];
  width?: number;
  height?: number;
  chroms?: string[];
  regionStart?: number;
  regionEnd?: number;
  onChromosomeClick?: (chrom: string) => void;
  onLayout?: (layout: Layout & { chroms: string[] }) => void;
  layout?: Layout;
}

const ApcadChart: React.FC<Props> = ({
  apcadUrls,
  width = 800,
  height = 120,
  chroms = DEFAULT_CHROMS,
  regionStart,
  regionEnd,
  onChromosomeClick,
  onLayout,
  layout,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasData, setHasData] = useState<boolean | null>(null);
  const [trackData, setTrackData] = useState<ApcadTrackData | null>(null);
  const layoutRef = useRef<Layout>({ offsets: {}, lengths: {}, total: 0 });

  const apcadUrlKey = apcadUrls.join('\n');
  const chromKey = chroms.join('\n');

  const stableApcadUrls = useMemo(() => splitKey(apcadUrlKey), [apcadUrlKey]);
  const stableChroms = useMemo(() => splitKey(chromKey), [chromKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    const load = async () => {
      if (stableApcadUrls.length === 0) {
        if (active) {
          setTrackData(null);
          setHasData(false);
          setLoading(false);
        }
        return;
      }

      if (active) {
        setLoading(true);
      }

      const headers: Record<string, string> = {};
      const token = storage.getItem('token');
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }

      try {
        const allowedChroms = new Set(stableChroms.map(normalizeChrom));
        const payloads = await Promise.all(
          stableApcadUrls.map((url) =>
            fetchJsonOrNull<ApcadBin>(url, headers, controller.signal),
          ),
        );

        if (!active) {
          return;
        }

        const bins: ApcadBin[] = [];
        payloads.forEach((payload) => {
          if (!payload) {
            return;
          }
          payload.items.forEach((item) => {
            const chromName = normalizeChrom(item.chr);
            if (!allowedChroms.has(chromName)) {
              return;
            }
            bins.push({
              chr: chromName,
              start: item.start,
              end: item.end,
              value: item.value,
              origin: (item.origin || 'und').toLowerCase(),
            });
          });
        });

        if (active) {
          setTrackData({ bins });
          setHasData(bins.length > 0);
        }
      } catch {
        if (controller.signal.aborted) {
          return;
        }
        if (active) {
          setTrackData(null);
          setHasData(false);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    load();

    return () => {
      active = false;
      controller.abort();
    };
  }, [stableApcadUrls, stableChroms]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) {
      return;
    }

    ctx.clearRect(0, 0, width, height);

    if (!trackData || trackData.bins.length === 0) {
      layoutRef.current = { offsets: {}, lengths: {}, total: 0 };
      return;
    }

    const { bins } = trackData;
    let chromLengths: Record<string, number>;
    let offsets: Record<string, number>;
    let totalLength: number;

    if (layout) {
      chromLengths = layout.lengths;
      offsets = layout.offsets;
      totalLength = layout.total;
    } else {
      const derivedLayout = deriveLayoutFromBins(
        bins,
        stableChroms,
        regionStart,
        regionEnd,
      );
      chromLengths = derivedLayout.lengths;
      offsets = derivedLayout.offsets;
      totalLength = derivedLayout.total;
    }

    layoutRef.current = { offsets, lengths: chromLengths, total: totalLength };
    if (!layout) {
      onLayout?.({
        offsets,
        lengths: chromLengths,
        total: totalLength,
        chroms: stableChroms,
      });
    }

    const isFocusedRegion =
      regionStart !== undefined &&
      regionEnd !== undefined &&
      stableChroms.length === 1;
    const xDomainStart = isFocusedRegion ? regionStart : 0;
    const xDomainEnd = isFocusedRegion ? regionEnd : totalLength;
    const xDomainSpan = xDomainEnd - xDomainStart;
    if (xDomainSpan <= 0) {
      return;
    }

    const xScale = (value: number) =>
      ((value - xDomainStart) / xDomainSpan) * width;
    const yScale = (value: number) => (1 - value) * height;

    const tickValues = [0, 0.33, 0.5, 0.66, 1];
    const gridColor = cssVar('--color-grid');
    const textColor = cssVar('--color-apcad-default');
    const paternalColor = cssVar('--color-apcad-paternal');
    const maternalColor = cssVar('--color-apcad-maternal');

    ctx.save();
    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    tickValues.forEach((tick) => {
      const y = yScale(tick);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.fillText(tick.toFixed(2), 4, y);
    });
    ctx.restore();

    bins.forEach((bin) => {
      if (
        isFocusedRegion &&
        (bin.end < regionStart || bin.start > regionEnd)
      ) {
        return;
      }

      const offset = offsets[bin.chr] ?? 0;
      const center = (bin.start + bin.end) / 2;
      const genomeCenter = isFocusedRegion ? center : offset + center;
      const cx = xScale(genomeCenter);
      const cy = yScale(bin.value);

      let fill = textColor;
      if (bin.origin === 'paternal') {
        fill = paternalColor;
      } else if (bin.origin === 'maternal') {
        fill = maternalColor;
      }

      ctx.fillStyle = fill;
      ctx.beginPath();
      ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
      ctx.fill();
    });

    if (stableChroms.length > 1) {
      ctx.strokeStyle = cssVar('--color-grid');
      ctx.lineWidth = 0.5;
      stableChroms.slice(0, -1).forEach((chrom) => {
        const boundary = (offsets[chrom] ?? 0) + (chromLengths[chrom] ?? 0);
        const x = xScale(boundary);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      });
    }
  }, [
    height,
    layout,
    onLayout,
    regionEnd,
    regionStart,
    stableChroms,
    trackData,
    width,
  ]);

  const handleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onChromosomeClick) {
      return;
    }

    const { offsets, lengths, total } = layoutRef.current;
    if (total <= 0) {
      return;
    }

    const genomePos = (event.nativeEvent.offsetX / width) * total;
    for (const chrom of stableChroms) {
      const start = offsets[chrom] ?? 0;
      const end = start + (lengths[chrom] ?? 0);
      if (genomePos >= start && genomePos < end) {
        onChromosomeClick(chrom);
        break;
      }
    }
  };

  return (
    <div className="relative" style={{ width, height }}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        onClick={handleClick}
      />
      {loading && <VizLoadingOverlay message="Loading APCAD" />}
      {!loading && hasData === false && (
        <div className="viz-empty-overlay">No APCAD data in this region</div>
      )}
    </div>
  );
};

export default ApcadChart;
