import React, { useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { formatChromosomeLabel, normalizeChrom } from '../../lib/chromosomes';
import { cssVar } from '../../lib/colors';
import { fetchTrackJson } from '../../lib/trackFetch';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizErrorOverlay from './VizErrorOverlay';
import type { GenomeLayout } from './genomeBinLayout';
import type { BedSegregationTrackOut } from '../../lib/apiSchema.generated';

/**
 * An embryo's parental origin, from the PGT pipeline's segregation table (`apcad_segregation`):
 * whether an aneuploid chromosome carries both of a parent's homologs or two copies of one, which
 * tells a meiotic error from a mitotic one. Drawn as the pipeline's own plots draw it, which the
 * laboratory reads: one panel per heterozygous parent, each site at the embryo's own allele
 * fraction (0 to 1), coloured by the other parent's homozygous allele (A alternate, B reference).
 *
 *   - one homolog of the heterozygous parent (disomy): bands at 0, 1/2 and 1;
 *   - both of its homologs (heterodisomy): only 1/3 and 2/3, nothing at 0 or 1;
 *   - two copies of one of its homologs (isodisomy), or the other parent's panel in a trisomy:
 *     0, 1/3, 2/3 and 1.
 *
 * In the genome view every chromosome is drawn on the overview's axis, so an aneuploid one stands
 * out against the others; in the chromosome view the region in view, against the ideogram's
 * centromere. The server stores each site at the fraction of the allele the homozygous parent
 * lacks and serves its group (backend/app/services/family_package_tracks.py,
 * `_parse_segregation_row`): the allele fraction is that value for B, one minus it for A.
 */

type Origin = 'maternal' | 'paternal';

export interface SegregationPoint {
  chr: string;
  pos: number;
  /** The embryo's alternate-allele fraction. */
  fraction: number;
  origin: Origin;
  /** The other parent's homozygous allele: A alternate, B reference. */
  allele: 'A' | 'B';
}

/** A served site as drawn; null for one without a group, a value or a known origin. */
export const segregationPoint = (item: BedSegregationTrackOut['items'][number]): SegregationPoint | null => {
  const origin = (item.origin || '').toLowerCase();
  const group = item.group ?? '';
  if ((origin !== 'maternal' && origin !== 'paternal') || !/^[23][AB]$/.test(group)) return null;
  if (item.value === null || !Number.isFinite(item.value)) return null;
  const allele = group[1] as 'A' | 'B';
  return {
    chr: normalizeChrom(item.chr),
    pos: item.end,
    fraction: allele === 'B' ? item.value : 1 - item.value,
    origin,
    allele,
  };
};

const PANELS: { origin: Origin; title: string; other: string; group: string; colorVar: string }[] = [
  { origin: 'maternal', title: 'Mother heterozygous', other: 'father', group: '2', colorVar: '--color-haplotype-mother-dark' },
  { origin: 'paternal', title: 'Father heterozygous', other: 'mother', group: '3', colorVar: '--color-haplotype-father-dark' },
];
// The other parent homozygous for the reference allele (B): one colour on both panels.
const REFERENCE_COLOR_VAR = '--color-segregation-reference';
const GUIDES: { value: number; label: string; dashed: boolean }[] = [
  { value: 1, label: '1', dashed: false },
  { value: 2 / 3, label: '⅔', dashed: true },
  { value: 1 / 2, label: '½', dashed: false },
  { value: 1 / 3, label: '⅓', dashed: true },
  { value: 0, label: '0', dashed: false },
];
const NO_POINTS: SegregationPoint[] = [];
const TITLE_HEIGHT = 18;
const CHROM_LABEL_HEIGHT = 14;
const KEY_HEIGHT = 18;

/** The height the chart takes: its two panels, their titles, the genome view's chromosome
 * labels and the reading key. */
export const parentalOriginChartHeight = (panelHeight: number, genome: boolean): number =>
  2 * (TITLE_HEIGHT + panelHeight) + (genome ? CHROM_LABEL_HEIGHT : 0) + KEY_HEIGHT;

interface Props {
  /** The BED reads of the embryo's segregation track: the region in view, or every chromosome. */
  urls: string[];
  sampleId: string;
  /** The chromosomes drawn: the one in view, or the overview's. */
  chroms: string[];
  /** The chromosome view's region. */
  regionStart?: number;
  regionEnd?: number;
  /** The genome view's axis: each chromosome's offset and the genome's length. */
  layout?: GenomeLayout | null;
  width?: number;
  panelHeight?: number;
}

const Panel: React.FC<{
  points: SegregationPoint[];
  panel: (typeof PANELS)[number];
  xOf: (point: SegregationPoint) => number | null;
  width: number;
  height: number;
  shade: { x: number; w: number }[];
  genome: boolean;
}> = ({ points, panel, xOf, width, height, shade, genome }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    const pad = 4;
    const yOf = (fraction: number) => pad + (1 - fraction) * (height - 2 * pad);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.035)';
    shade.forEach((band) => ctx.fillRect(band.x, 0, band.w, height));
    const grid = cssVar('--color-grid');
    const guideInk = cssVar('--color-gray');
    GUIDES.forEach((guide) => {
      const y = Math.round(yOf(guide.value)) + 0.5;
      ctx.strokeStyle = guide.dashed ? guideInk : grid;
      ctx.setLineDash(guide.dashed ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    const alternate = cssVar(panel.colorVar);
    const reference = cssVar(REFERENCE_COLOR_VAR);
    const radius = genome ? 1.2 : 1.7;
    ctx.globalAlpha = 0.5;
    points.forEach((point) => {
      const x = xOf(point);
      if (x === null) return;
      ctx.fillStyle = point.allele === 'A' ? alternate : reference;
      ctx.beginPath();
      ctx.arc(x, yOf(point.fraction), radius, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;
    // The scale's labels, on a white chip so they stay legible over the points.
    ctx.font = '9px sans-serif';
    ctx.textBaseline = 'middle';
    GUIDES.forEach((guide) => {
      const y = Math.min(height - 5, Math.max(5, yOf(guide.value)));
      ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
      ctx.fillRect(0, y - 5, 12, 10);
      ctx.fillStyle = guideInk;
      ctx.fillText(guide.label, 2, y);
    });
  }, [points, panel, xOf, width, height, shade, genome]);
  return <canvas ref={canvasRef} width={width} height={height} style={{ display: 'block' }} />;
};

const ParentalOriginChart: React.FC<Props> = ({
  urls,
  sampleId,
  chroms,
  regionStart,
  regionEnd,
  layout,
  width = 800,
  panelHeight = 120,
}) => {
  const genome = Boolean(layout);
  const urlKey = urls.join('\n');
  const chromKey = chroms.map(normalizeChrom).join(',');
  const { data: points, isLoading, isError, isSuccess, refetch } = useQuery<Record<Origin, SegregationPoint[]>>({
    queryKey: ['parental-origin', urlKey, chromKey],
    enabled: urls.length > 0,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      // A 404 is the BED endpoints' "no data" answer.
      const payloads = await Promise.all(
        urlKey.split('\n').map((url) => fetchTrackJson<BedSegregationTrackOut>(url, { signal, emptyOn404: true })),
      );
      const wanted = new Set(chromKey.split(','));
      const byOrigin: Record<Origin, SegregationPoint[]> = { maternal: [], paternal: [] };
      payloads.forEach((payload) =>
        payload?.items.forEach((item) => {
          const point = segregationPoint(item);
          if (point && wanted.has(point.chr)) byOrigin[point.origin].push(point);
        }),
      );
      return byOrigin;
    },
  });
  const settled = isSuccess && points !== undefined;

  const xOf = useMemo(() => {
    if (layout) {
      const total = layout.total || 1;
      return (point: SegregationPoint) =>
        point.chr in layout.offsets ? ((layout.offsets[point.chr] + point.pos) / total) * width : null;
    }
    const start = regionStart ?? 0;
    const span = Math.max(1, (regionEnd ?? start + 1) - start);
    return (point: SegregationPoint) =>
      point.pos < start || point.pos > start + span ? null : ((point.pos - start) / span) * width;
  }, [layout, regionStart, regionEnd, width]);

  // The genome view shades every other chromosome and names each below the panels.
  const chromBands = useMemo(() => {
    if (!layout) return [];
    const total = layout.total || 1;
    return chroms
      .map(normalizeChrom)
      .filter((chrom) => chrom in layout.offsets)
      .map((chrom, index) => ({
        chrom,
        index,
        x: (layout.offsets[chrom] / total) * width,
        w: ((layout.lengths[chrom] ?? 0) / total) * width,
      }));
  }, [layout, chroms, width]);
  const shade = useMemo(
    () => chromBands.filter((band) => band.index % 2 === 1).map(({ x, w }) => ({ x, w })),
    [chromBands],
  );

  const counts = { maternal: points?.maternal.length ?? 0, paternal: points?.paternal.length ?? 0 };
  const where = genome
    ? `across ${chroms.length} chromosomes`
    : `on ${formatChromosomeLabel(chroms[0] ?? '')}:${(regionStart ?? 0).toLocaleString()}–${(regionEnd ?? 0).toLocaleString()}`;
  const label =
    `Parental origin of ${sampleId} ${where}` +
    (settled
      ? `: ${counts.maternal.toLocaleString()} sites where the mother is heterozygous, ${counts.paternal.toLocaleString()} where the father is`
      : '');
  const empty = settled && counts.maternal + counts.paternal === 0;

  return (
    <div className="relative parental-origin-chart" role="img" aria-label={label} style={{ width }}>
      {PANELS.map((panel) => (
        <div key={panel.origin}>
          <div className="parental-origin-title" style={{ height: TITLE_HEIGHT }}>
            <strong>{panel.title}</strong>
            <span className="parental-origin-chip">
              <i style={{ background: `var(${REFERENCE_COLOR_VAR})` }} aria-hidden="true" />
              {panel.other} homozygous reference ({panel.group}B)
            </span>
            <span className="parental-origin-chip">
              <i style={{ background: `var(${panel.colorVar})` }} aria-hidden="true" />
              {panel.other} homozygous alternate ({panel.group}A)
            </span>
          </div>
          <Panel
            points={points?.[panel.origin] ?? NO_POINTS}
            panel={panel}
            xOf={xOf}
            width={width}
            height={panelHeight}
            shade={shade}
            genome={genome}
          />
        </div>
      ))}
      {genome && (
        <div className="parental-origin-chroms" style={{ height: CHROM_LABEL_HEIGHT }} aria-hidden="true">
          {chromBands.map((band) => (
            <span key={band.chrom} style={{ left: band.x, width: band.w }}>
              {band.w >= 10 ? band.chrom : ''}
            </span>
          ))}
        </div>
      )}
      <div className="parental-origin-key" style={{ minHeight: KEY_HEIGHT }}>
        0, ½ and 1: one homolog of that parent · only ⅓ and ⅔, nothing at 0 or 1: both of its homologs
        (heterodisomy) · 0, ⅓, ⅔ and 1: two copies of one homolog (isodisomy), or the other parent&rsquo;s panel in
        a trisomy
      </div>
      {isLoading && <VizLoadingOverlay message="Loading parental origin" />}
      {isError && <VizErrorOverlay what="the segregation table" onRetry={() => void refetch()} />}
      {!isLoading && !isError && empty && <div className="viz-empty-overlay">No segregation sites in this view</div>}
    </div>
  );
};

export default ParentalOriginChart;
