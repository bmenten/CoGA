import React, { useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { formatChromosomeLabel, normalizeChrom } from '../../lib/chromosomes';
import { cssVar } from '../../lib/colors';
import { fetchTrackJson } from '../../lib/trackFetch';
import { TRACK_DOT_RADIUS } from '../../lib/trackSampling';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizErrorOverlay from './VizErrorOverlay';
import type { BedApcadTrackOut } from '../../lib/apiSchema.generated';

/**
 * An embryo's parental origin on one chromosome, from the PGT pipeline's segregation table
 * (`apcad_segregation`): for an aneuploid chromosome, whether it carries both of a parent's
 * homologs or two copies of one, which tells a meiotic error from a mitotic one.
 *
 * Two panels, one per parent: the sites where that parent is heterozygous and the other
 * homozygous, each at the embryo's fraction of the allele the homozygous parent does not carry
 * (backend/app/services/family_package_tracks.py, `_parse_segregation_row`). Read against the
 * guide lines:
 *
 *   - 0 and 1/2: one homolog of that parent (disomy);
 *   - 1/3 alone, no 0: both of that parent's homologs beside one of the other's (heterodisomy,
 *     a trisomy from a meiosis I error where it lies at the centromere);
 *   - 0 and 2/3: two copies of one of that parent's homologs (isodisomy: from a meiosis II
 *     error at the centromere, switching to heterodisomy past a crossover, or a mitotic error
 *     along the whole chromosome);
 *   - 0 and 1/3 on the other parent's panel: that parent gave one copy of three.
 *
 * Below each panel a strip gives, per window, the share of its sites at 0: near 0 along a
 * stretch where both of that parent's homologs are present. It describes; the analyst reads.
 */

interface Point {
  start: number;
  value: number;
}

interface Props {
  url: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width?: number;
  height?: number;
}

const PANELS: { origin: 'maternal' | 'paternal'; label: string; colorVar: string }[] = [
  { origin: 'maternal', label: 'Mother heterozygous', colorVar: '--color-apcad-maternal' },
  { origin: 'paternal', label: 'Father heterozygous', colorVar: '--color-apcad-paternal' },
];
const GUIDES: { value: number; label: string }[] = [
  { value: 0, label: '0' },
  { value: 1 / 3, label: '⅓' },
  { value: 1 / 2, label: '½' },
  { value: 2 / 3, label: '⅔' },
  { value: 1, label: '1' },
];
// A site counts as at 0 below this fraction: the embryo carries none of the allele the
// homozygous parent lacks, but for read errors.
export const ZERO_BAND = 0.15;
const STRIP_HEIGHT = 6;
const WINDOWS = 40;

/** Per window of the view, the share of a panel's sites at 0; null for a window without one. */
export const zeroShareByWindow = (
  points: Point[],
  regionStart: number,
  regionEnd: number,
  windows: number = WINDOWS,
): (number | null)[] => {
  const span = Math.max(1, regionEnd - regionStart);
  const totals = new Array<number>(windows).fill(0);
  const zeros = new Array<number>(windows).fill(0);
  points.forEach((point) => {
    if (point.start < regionStart || point.start > regionEnd) return;
    const index = Math.min(windows - 1, Math.floor(((point.start - regionStart) / span) * windows));
    totals[index] += 1;
    if (point.value < ZERO_BAND) zeros[index] += 1;
  });
  return totals.map((total, index) => (total ? zeros[index] / total : null));
};

const ParentalOriginChart: React.FC<Props> = ({
  url,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width = 800,
  height = 160,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { data, isLoading, isError, refetch } = useQuery<Record<'maternal' | 'paternal', Point[]>>({
    queryKey: ['parental-origin', url],
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      // A 404 is the BED endpoints' "no data" answer.
      const payload = await fetchTrackJson<BedApcadTrackOut>(url, { signal, emptyOn404: true });
      const points: Record<'maternal' | 'paternal', Point[]> = { maternal: [], paternal: [] };
      payload?.items.forEach((item) => {
        const origin = (item.origin || '').toLowerCase();
        if (
          normalizeChrom(item.chr) === normalizeChrom(chrom) &&
          (origin === 'maternal' || origin === 'paternal') &&
          item.value !== null &&
          Number.isFinite(item.value)
        ) {
          points[origin].push({ start: item.start, value: item.value });
        }
      });
      return points;
    },
  });

  const strips = useMemo(
    () =>
      data
        ? {
            maternal: zeroShareByWindow(data.maternal, regionStart, regionEnd),
            paternal: zeroShareByWindow(data.paternal, regionStart, regionEnd),
          }
        : null,
    [data, regionStart, regionEnd],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, width, height);
    if (!data || !strips) return;
    const span = Math.max(1, regionEnd - regionStart);
    const xScale = (position: number) => ((position - regionStart) / span) * width;
    const panelHeight = height / 2 - STRIP_HEIGHT - 2;
    const gridColor = cssVar('--color-grid');
    const textColor = cssVar('--color-apcad-default');
    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'middle';
    PANELS.forEach((panel, index) => {
      const top = index * (height / 2);
      const pad = TRACK_DOT_RADIUS + 1;
      const yScale = (value: number) => top + pad + (1 - value) * (panelHeight - 2 * pad);
      ctx.strokeStyle = gridColor;
      ctx.fillStyle = textColor;
      GUIDES.forEach((guide) => {
        const y = yScale(guide.value);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
        ctx.fillText(guide.label, 2, y);
      });
      ctx.fillText(panel.label, 14, top + 6);
      ctx.save();
      ctx.globalAlpha = 0.5;
      ctx.fillStyle = cssVar(panel.colorVar);
      data[panel.origin].forEach((point) => {
        if (point.start < regionStart || point.start > regionEnd) return;
        ctx.beginPath();
        ctx.arc(xScale(point.start), yScale(point.value), TRACK_DOT_RADIUS, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.restore();
      // The share at 0 per window: dark where it is high, light where both homologs are present.
      const stripTop = top + panelHeight + 1;
      const windowWidth = width / WINDOWS;
      strips[panel.origin].forEach((share, window) => {
        if (share === null) return;
        ctx.save();
        ctx.globalAlpha = 0.15 + 0.85 * share;
        ctx.fillStyle = textColor;
        ctx.fillRect(window * windowWidth, stripTop, Math.max(1, windowWidth - 1), STRIP_HEIGHT);
        ctx.restore();
      });
    });
  }, [data, strips, width, height, regionStart, regionEnd]);

  const counts = data ? { maternal: data.maternal.length, paternal: data.paternal.length } : null;
  const empty = counts !== null && counts.maternal + counts.paternal === 0;
  const label =
    `Parental origin of ${sampleId} on ${formatChromosomeLabel(chrom)}:${regionStart.toLocaleString()}–${regionEnd.toLocaleString()}` +
    (counts
      ? `: ${counts.maternal.toLocaleString()} sites where the mother is heterozygous, ${counts.paternal.toLocaleString()} where the father is`
      : '');

  return (
    <div className="relative" style={{ width, height }}>
      <canvas ref={canvasRef} width={width} height={height} role="img" aria-label={label} />
      {isLoading && <VizLoadingOverlay message="Loading parental origin" />}
      {isError && <VizErrorOverlay what="the segregation table" onRetry={() => void refetch()} />}
      {!isLoading && !isError && empty && <div className="viz-empty-overlay">No segregation sites in this region</div>}
    </div>
  );
};

export default ParentalOriginChart;
