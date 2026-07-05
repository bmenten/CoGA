import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  defaultCoverageRange,
  getCoverageLowerThreshold,
  getCoverageRange,
  getCoverageUpperThreshold,
} from '../../lib/settings';
import { cssVar } from '../../lib/colors';
import { storage } from '../../lib/storage';
import { TRACK_DOT_RADIUS } from '../../lib/trackSampling';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizLoadingOverlay from './VizLoadingOverlay';

const DEFAULT_CHROMS = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  'X',
  'Y',
];

const normalizeChrom = (value: string): string =>
  value.toLowerCase().startsWith('chr') ? value.slice(3) : value;

interface CoverageBin {
  chr: string;
  start: number;
  end: number;
  value: number;
}

interface Segment {
  chr: string;
  start: number;
  end: number;
  value: number;
}

interface SegmentPointerState {
  index: number;
}

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
}

interface CoverageTrackData {
  bins: CoverageBin[];
  segments: Segment[];
}

interface BedRecordPayload<T> {
  items?: T[];
}

type BedRecordResponse<T> = BedRecordPayload<T> | T[];

const fetchJsonOrNull = async <T,>(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<BedRecordResponse<T> | null> => {
  try {
    const response = await fetch(url, { headers, signal });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as BedRecordResponse<T>;
  } catch {
    if (signal.aborted) {
      throw new DOMException('Aborted', 'AbortError');
    }
    return null;
  }
};

const splitKey = (key: string): string[] => (key ? key.split('\n').filter(Boolean) : []);

const payloadItems = <T,>(payload: BedRecordResponse<T> | null): T[] => {
  if (!payload) {
    return [];
  }
  if (Array.isArray(payload)) {
    return payload;
  }
  return Array.isArray(payload.items) ? payload.items : [];
};

const deriveLayoutFromBins = (
  bins: CoverageBin[],
  chroms: string[],
  regionStart?: number,
  regionEnd?: number,
): Layout => {
  const lengths: Record<string, number> = Object.create(null);
  chroms.forEach((chrom) => {
    lengths[chrom] = 0;
  });

  bins.forEach((bin) => {
    lengths[bin.chr] = Math.max(lengths[bin.chr] ?? 0, bin.end);
  });

  const offsets: Record<string, number> = Object.create(null);
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
  coverageUrls: string[];
  segmentsUrls?: string[];
  width?: number;
  height?: number;
  chroms?: string[];
  regionStart?: number;
  regionEnd?: number;
  onRegionSelect?: (start: number, end: number) => void;
  onChromosomeClick?: (chrom: string) => void;
  onLayout?: (layout: Layout & { chroms: string[] }) => void;
  layout?: Layout;
}

const CoverageSegmentsChart: React.FC<Props> = ({
  coverageUrls,
  segmentsUrls,
  width = 800,
  height = 120,
  chroms = DEFAULT_CHROMS,
  regionStart,
  regionEnd,
  onRegionSelect,
  onChromosomeClick,
  onLayout,
  layout,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragStart = useRef<number | null>(null);
  const [dragCurrent, setDragCurrent] = useState<number | null>(null);
  const layoutRef = useRef<Layout>({ offsets: {}, lengths: {}, total: 0 });

  const coverageUrlKey = coverageUrls.join('\n');
  const segmentUrlKey = (segmentsUrls ?? []).join('\n');
  const chromKey = chroms.join('\n');

  const stableCoverageUrls = useMemo(() => splitKey(coverageUrlKey), [coverageUrlKey]);
  const stableSegmentsUrls = useMemo(() => splitKey(segmentUrlKey), [segmentUrlKey]);
  const stableChroms = useMemo(() => splitKey(chromKey), [chromKey]);

  // React Query caches/dedupes by URL across re-renders and navigation (was a raw
  // fetch in useEffect that re-ran on every mount/resize). The data is static per
  // family, so it is held indefinitely.
  const { data: trackData = null, isLoading } = useQuery<CoverageTrackData>({
    queryKey: ['genome-coverage', coverageUrlKey, segmentUrlKey, chromKey],
    enabled: stableCoverageUrls.length > 0,
    staleTime: Infinity,
    gcTime: Infinity,
    queryFn: async ({ signal }) => {
      const headers: Record<string, string> = {};
      const token = storage.getItem('token');
      if (token) headers.Authorization = `Bearer ${token}`;
      const allowedChroms = new Set(stableChroms.map(normalizeChrom));
      const [coveragePayloads, segmentPayloads] = await Promise.all([
        Promise.all(stableCoverageUrls.map((url) => fetchJsonOrNull<CoverageBin>(url, headers, signal))),
        Promise.all(stableSegmentsUrls.map((url) => fetchJsonOrNull<Segment>(url, headers, signal))),
      ]);
      const bins: CoverageBin[] = [];
      coveragePayloads.forEach((payload) => {
        payloadItems(payload).forEach((item) => {
          const chromName = normalizeChrom(item.chr);
          if (allowedChroms.has(chromName)) {
            bins.push({ chr: chromName, start: item.start, end: item.end, value: item.value });
          }
        });
      });
      const segments: Segment[] = [];
      segmentPayloads.forEach((payload) => {
        payloadItems(payload).forEach((item) => {
          const chromName = normalizeChrom(item.chr);
          if (allowedChroms.has(chromName)) {
            segments.push({ chr: chromName, start: item.start, end: item.end, value: item.value });
          }
        });
      });
      return { bins, segments };
    },
  });
  // Keep the previous window painted across a pan (same span) so the track glides
  // instead of blanking; on a zoom the stale data is dropped (see the hook).
  const displayData = useSameSpanFallbackData(trackData, (regionEnd ?? 0) - (regionStart ?? 0));
  const loading = isLoading && stableCoverageUrls.length > 0 && displayData === null;
  // null while loading (don't blank the chart), false when there are no URLs or the
  // payloads are empty, true when either track has data.
  const hasData =
    stableCoverageUrls.length === 0
      ? false
      : displayData
        ? displayData.bins.length > 0 || displayData.segments.length > 0
        : loading
          ? null
          : false;

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) {
      return;
    }

    ctx.clearRect(0, 0, width, height);

    if (!displayData || (displayData.bins.length === 0 && displayData.segments.length === 0)) {
      layoutRef.current = { offsets: {}, lengths: {}, total: 0 };
      return;
    }

    const { bins, segments } = displayData;
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
    const horizontalSpan = isFocusedRegion ? regionEnd - regionStart : totalLength;
    if (horizontalSpan <= 0) {
      return;
    }

    // Guard against a zero/non-finite range: a 0-width domain makes every point
    // a non-finite coordinate and silently blanks the chart.
    const configuredRange = getCoverageRange();
    const range =
      Number.isFinite(configuredRange) && configuredRange > 0
        ? configuredRange
        : defaultCoverageRange;
    const minVal = -range;
    const maxVal = range;
    const yScale = (value: number) =>
      height - ((value - minVal) / (maxVal - minVal)) * height;

    const clampY = (value: number) => {
      if (value < minVal) {
        return yScale(minVal) - 2;
      }
      if (value > maxVal) {
        return yScale(maxVal) + 2;
      }
      return yScale(value);
    };

    const tickValues: number[] = [];
    for (
      let tick = Math.ceil(minVal / 0.5) * 0.5;
      tick <= maxVal + 1e-6;
      tick += 0.5
    ) {
      tickValues.push(Number(tick.toFixed(2)));
    }

    const gridColor = cssVar('--color-grid');
    const textColor = cssVar('--color-coverage-neutral');
    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'alphabetic';
    tickValues.forEach((tick) => {
      const y = yScale(tick);
      ctx.strokeStyle = gridColor;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.fillText(String(tick), 2, y - 2);
    });

    const upperThreshold = getCoverageUpperThreshold();
    const lowerThreshold = getCoverageLowerThreshold();
    const gainColor = cssVar('--color-coverage-gain');
    const lossColor = cssVar('--color-coverage-loss');
    const neutralColor = cssVar('--color-coverage-neutral');
    const colorForValue = (value: number) =>
      value > upperThreshold
        ? gainColor
        : value < lowerThreshold
          ? lossColor
          : neutralColor;

    const segmentsByChr: Record<string, Segment[]> = {};
    segments.forEach((segment) => {
      if (!segmentsByChr[segment.chr]) {
        segmentsByChr[segment.chr] = [];
      }
      segmentsByChr[segment.chr].push(segment);
    });
    Object.values(segmentsByChr).forEach((chrSegments) => {
      chrSegments.sort((left, right) => left.start - right.start);
    });

    const segmentPointers: Record<string, SegmentPointerState> = {};

    bins.forEach((bin) => {
      if (
        isFocusedRegion &&
        (bin.end < regionStart || bin.start > regionEnd)
      ) {
        return;
      }

      const offset = offsets[bin.chr] ?? 0;
      const center = (bin.start + bin.end) / 2;
      const cx = isFocusedRegion
        ? ((center - regionStart) / horizontalSpan) * width
        : ((offset + center) / totalLength) * width;
      const cy = clampY(bin.value);
      const chrSegments = segmentsByChr[bin.chr] || [];
      const pointer = segmentPointers[bin.chr] || { index: 0 };

      while (
        pointer.index < chrSegments.length &&
        chrSegments[pointer.index].end < center
      ) {
        pointer.index += 1;
      }
      segmentPointers[bin.chr] = pointer;

      const candidate = chrSegments[pointer.index];
      const segment =
        candidate && candidate.start <= center && candidate.end >= center
          ? candidate
          : undefined;
      const color = segment ? colorForValue(segment.value) : colorForValue(bin.value);

      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.arc(cx, cy, TRACK_DOT_RADIUS, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.lineWidth = 1;
    segments.forEach((segment) => {
      if (
        isFocusedRegion &&
        (segment.end < regionStart || segment.start > regionEnd)
      ) {
        return;
      }

      const offset = offsets[segment.chr] ?? 0;
      const x1 = isFocusedRegion
        ? ((segment.start - regionStart) / horizontalSpan) * width
        : ((offset + segment.start) / totalLength) * width;
      const x2 = isFocusedRegion
        ? ((segment.end - regionStart) / horizontalSpan) * width
        : ((offset + segment.end) / totalLength) * width;
      const y = clampY(segment.value);

      ctx.beginPath();
      ctx.strokeStyle = colorForValue(segment.value);
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
    });

    if (stableChroms.length > 1) {
      ctx.strokeStyle = cssVar('--color-grid');
      ctx.lineWidth = 0.5;
      stableChroms.slice(0, -1).forEach((chrom) => {
        const boundary = (offsets[chrom] ?? 0) + (chromLengths[chrom] ?? 0);
        const x = (boundary / totalLength) * width;
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
    displayData,
    width,
  ]);

  const handleMouseDown = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onRegionSelect || regionStart === undefined || regionEnd === undefined) {
      return;
    }
    dragStart.current = event.nativeEvent.offsetX;
    setDragCurrent(event.nativeEvent.offsetX);
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (dragStart.current === null) {
      return;
    }
    setDragCurrent(event.nativeEvent.offsetX);
  };

  const handleMouseUp = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (
      !onRegionSelect ||
      regionStart === undefined ||
      regionEnd === undefined ||
      dragStart.current === null
    ) {
      dragStart.current = null;
      setDragCurrent(null);
      return;
    }

    const startX = dragStart.current;
    const endX = event.nativeEvent.offsetX;
    const x1 = Math.min(startX, endX);
    const x2 = Math.max(startX, endX);
    dragStart.current = null;
    setDragCurrent(null);

    if (Math.abs(x2 - x1) < 5) {
      return;
    }

    const newStart = regionStart + (x1 / width) * (regionEnd - regionStart);
    const newEnd = regionStart + (x2 / width) * (regionEnd - regionStart);
    onRegionSelect(Math.floor(newStart), Math.floor(newEnd));
  };

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

  const rectStyle = useMemo(() => {
    if (dragStart.current === null || dragCurrent === null) {
      return { display: 'none' } as React.CSSProperties;
    }

    const x1 = Math.min(dragStart.current, dragCurrent);
    const widthRect = Math.abs(dragCurrent - dragStart.current);
    return {
      position: 'absolute',
      left: x1,
      top: 0,
      width: widthRect,
      height: '100%',
      border: `1px dashed ${cssVar('--color-coverage-border')}`,
      background: 'transparent',
      pointerEvents: 'none',
    } as React.CSSProperties;
  }, [dragCurrent]);

  return (
    <div style={{ position: 'relative', width, height }}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        data-audit-id="coverage-segments-chart"
        data-audit-label="Coverage chart"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onClick={handleClick}
      />
      {loading && <VizLoadingOverlay message="Loading coverage" />}
      {!loading && hasData === false && (
        <div className="viz-empty-overlay">No coverage data in this region</div>
      )}
      <div style={rectStyle} />
    </div>
  );
};

export default CoverageSegmentsChart;
