import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import { drawHaplotypeRiskOverlay } from '../../lib/haplotypeCanvas';
import {
  defaultHaplotypeRiskRegion,
  diseaseHaplotypeKindForLane,
  getHaplotypeLaneSignature,
  getRenderableHaplotypeLanes,
  inferDiseaseHaplotypes,
  interpretSampleHaplotypeRisk,
  resolveHaplotypeInheritanceModel,
  type DiseaseHaplotypeKind,
  type HaplotypeLane,
  type HaplotypeMemberLike,
  type HaplotypeRiskRegion,
} from '../../lib/haplotypeRisk';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizTooltip from './VizTooltip';

interface Segment {
  start: number;
  end: number;
  hap1: string;
  hap2: string;
  ps?: number | null;
  // Pedigree-aware colour class per lane (paternal/maternal -> coloured,
  // untransmitted/unknown -> grey). Drives baseColorForLane via the shared
  // getHaplotypeLaneSignature.
  hap1_lineage?: string | null;
  hap2_lineage?: string | null;
}

interface SampleSegments {
  sample: string;
  segments: Segment[];
}

interface HaplotypeResponse {
  chr: string;
  start: number;
  end: number;
  samples: SampleSegments[];
}

interface PhasedMarker {
  pos: number;
  // Value drawn on each lane (0/1). Child: inherited paternal (hap1) / maternal
  // (hap2) homolog; parent: the allele on their own hap1 / hap2.
  hap1: number | null;
  hap2: number | null;
}

interface PhasedSite {
  pos: number;
  ref: string;
  alt: string;
  // Raw phased a|b genotype per member, aligned to `samples` order.
  gts: string[];
}

interface PhasedSampleQc {
  // Sites where both parents and this child are jointly genotyped.
  informative_sites: number;
  // Subset of those whose child genotype is impossible given the parents (a true
  // Mendelian inconsistency — likely sample swap / wrong pedigree).
  mendel_errors: number;
  mendel_rate: number;
}

interface PhasedMarkerResponse {
  samples: { sample: string; markers: PhasedMarker[]; reference?: boolean; qc?: PhasedSampleQc | null }[];
  sites?: PhasedSite[];
  // The per-site fetch is capped server-side and drops the highest-coordinate tail
  // when the region holds too many sites. When true, the overlay would be partial
  // and misleading, so the markers/sites are empty and we show a 'zoom in' hint.
  truncated?: boolean;
  covered?: number[] | null;
}

const isDeletedHaplotype = (value: string): boolean => value === '.';

const laneValue = (value: number | null): string => (value === null ? '.' : String(value));

// IGV-style nucleotide colours for the hover tooltip's phased genotypes.
const NUCLEOTIDE_COLORS: Record<string, string> = {
  A: '#2e9e4f',
  C: '#2f6fe0',
  G: '#e8a33d',
  T: '#d6453d',
};

/** The allele a phased index refers to: 0 = ref, n = nth alt; '.' = missing. */
const alleleBase = (index: string, ref: string, alt: string): string => {
  if (index === '0') return ref;
  const n = parseInt(index, 10);
  if (Number.isNaN(n)) return '·';
  return alt.split(',')[n - 1] ?? '?';
};

const baseSpan = (base: string, key: number): React.ReactNode => (
  <span
    key={key}
    style={{ color: base.length === 1 ? NUCLEOTIDE_COLORS[base.toUpperCase()] ?? '#9ca3af' : '#cbd5e1', fontWeight: 700 }}
  >
    {base}
  </span>
);

/** Render a member's phased ``a|b`` genotype as two colour-coded nucleotides. */
const renderPhasedGenotype = (gt: string, ref: string, alt: string): React.ReactNode => {
  if (!gt || !gt.includes('|')) return <span style={{ color: '#9ca3af' }}>—</span>;
  const [a, b] = gt.split('|');
  return (
    <span>
      {baseSpan(alleleBase(a, ref, alt), 0)}
      <span style={{ color: '#94a3b8' }}> | </span>
      {baseSpan(alleleBase(b, ref, alt), 1)}
    </span>
  );
};

/** Cleaned block covering a position, by binary search over start-sorted segments. */
const findCoveringBlock = <T extends { start: number; end: number }>(
  segs: T[],
  pos: number,
): T | null => {
  let lo = 0;
  let hi = segs.length - 1;
  let found: T | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start <= pos) {
      found = segs[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found && pos < found.end ? found : null;
};

/** Nearest position to ``pos``, by binary search over an ascending position array. */
const nearestPos = (positions: number[], pos: number): number | null => {
  if (positions.length === 0) return null;
  let lo = 0;
  let hi = positions.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (positions[mid] < pos) lo = mid + 1;
    else hi = mid;
  }
  const candidate = positions[lo];
  if (lo > 0 && Math.abs(positions[lo - 1] - pos) < Math.abs(candidate - pos)) {
    return positions[lo - 1];
  }
  return candidate;
};

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  role: string;
  affected: boolean;
  sex?: string | null;
  carrierStatus?: boolean | null;
  carrierType?: string | null;
  highlightRiskHaplotype?: boolean;
  disorder?: 'dominant' | 'recessive';
  inheritanceModel?: string | null;
  familyMembers?: HaplotypeMemberLike[];
  riskRegion?: HaplotypeRiskRegion | null;
  /**
   * Overlay the raw per-marker phasing as dots on top of the (thin) haplotype
   * line. Enabled for the whole family when both parents are present. For a child
   * the dots are the inherited parental homolog; for a parent, the alleles on
   * their own homologs. Disagreements with the cleaned blocks stand out.
   */
  showMarkers?: boolean;
}

const BAND_THICKNESS = 4;
// Markers are drawn as crisp 1px vertical ticks at the floored x so they stay
// sharp (no anti-alias blur) and taller than the band so disagreements stand out.
const MARKER_WIDTH = 1;
const MARKER_LANE_PADDING = 2;
// A child's Mendel-error rate above this fraction of informative sites is a real
// signal (likely sample swap / wrong pedigree), not phasing noise; below it we stay
// silent so a stray imputation glitch never raises a false alarm in a clinical view.
const MENDEL_WARN_RATE = 0.02;
// Stable empty fallback so the optional familyMembers prop keeps one reference
// across renders (an inline `= []` default would defeat the memos below).
const EMPTY_MEMBERS: HaplotypeMemberLike[] = [];

const HaplotypePhasedTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  role,
  affected,
  sex,
  carrierStatus = false,
  carrierType,
  highlightRiskHaplotype,
  disorder = 'dominant',
  inheritanceModel,
  familyMembers = EMPTY_MEMBERS,
  riskRegion,
  showMarkers = false,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: React.ReactNode } | null>(
    null,
  );

  const hasRegion = regionEnd > regionStart;

  const { data: rawHaplotypeData, isLoading: haplotypeLoading } = useQuery<HaplotypeResponse>({
    queryKey: ['haplotypes', familyId, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/haplotypes`, {
        params: { chr: chrom, start: regionStart, end: regionEnd },
      });
      return res.data as HaplotypeResponse;
    },
    enabled: hasRegion,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  // Raw per-marker phasing (computed server-side, returned per member). Only
  // fetched when we are overlaying markers.
  const { data: rawPhasedData } = useQuery<PhasedMarkerResponse>({
    queryKey: ['phased-markers', familyId, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/phased-markers`, {
        params: { chr: chrom, start: regionStart, end: regionEnd },
      });
      return res.data as PhasedMarkerResponse;
    },
    enabled: hasRegion && showMarkers,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  // Keep the previous window painted across a pan (same span); dropped on zoom.
  const haplotypeData = useSameSpanFallbackData(rawHaplotypeData, (regionEnd ?? 0) - (regionStart ?? 0));
  const phasedData = useSameSpanFallbackData(rawPhasedData, (regionEnd ?? 0) - (regionStart ?? 0));
  const isLoading = haplotypeLoading && haplotypeData === null;

  const effectiveInheritanceModel = inheritanceModel || (disorder === 'recessive' ? 'AR' : 'AD');
  const currentMember: HaplotypeMemberLike = useMemo(
    () => ({
      sample_id: sampleId,
      role,
      affected,
      sex,
      carrier_status: carrierStatus ? 'carrier' : 'unknown',
      carrier_type: carrierType,
    }),
    [affected, carrierStatus, carrierType, role, sampleId, sex],
  );
  const membersForRisk = useMemo(
    () => (familyMembers.length > 0 ? familyMembers : [currentMember]),
    [familyMembers, currentMember],
  );
  const analysisRegion = useMemo(
    () => riskRegion || defaultHaplotypeRiskRegion(chrom, regionStart, regionEnd),
    [riskRegion, chrom, regionStart, regionEnd],
  );
  const riskEnabled =
    highlightRiskHaplotype ??
    membersForRisk.some((member) => member.affected || member.carrier_status === 'carrier');
  const diseaseModel = useMemo(
    () =>
      riskEnabled
        ? inferDiseaseHaplotypes({
            samples: haplotypeData?.samples || [],
            members: membersForRisk,
            inheritanceModel: resolveHaplotypeInheritanceModel(effectiveInheritanceModel, membersForRisk),
            region: analysisRegion,
          })
        : inferDiseaseHaplotypes({
            samples: [],
            members: [],
            inheritanceModel: effectiveInheritanceModel,
            region: analysisRegion,
          }),
    [analysisRegion, haplotypeData?.samples, effectiveInheritanceModel, membersForRisk, riskEnabled],
  );

  const segments = useMemo(
    () => haplotypeData?.samples.find((entry) => entry.sample === sampleId)?.segments || [],
    [haplotypeData?.samples, sampleId],
  );
  const markersFor = (id: string | null): PhasedMarker[] =>
    id ? phasedData?.samples.find((entry) => entry.sample === id)?.markers || [] : [];
  const markers = useMemo(
    () => (showMarkers ? markersFor(sampleId) : []),
    [showMarkers, phasedData?.samples, sampleId],
  );
  // Positions the hover tooltip can anchor on: the UNION of every member's marker
  // positions (equivalently, the sites positions — the server now emits one site
  // per position that produced a marker for >=1 member). This makes a RELATIVE's
  // own track hoverable even though it carries no markers of its own: the nearest
  // site still resolves and the all-member genotype table is shown. Members that do
  // have markers are unaffected (their positions are all present in this union).
  const sortedSitePositions = useMemo(() => {
    if (!showMarkers || !phasedData) return [] as number[];
    const positions = new Set<number>();
    (phasedData.sites ?? []).forEach((site) => positions.add(site.pos));
    (phasedData.samples ?? []).forEach((entry) =>
      entry.markers.forEach((marker) => positions.add(marker.pos)),
    );
    return [...positions].sort((a, b) => a - b);
  }, [showMarkers, phasedData]);

  // Raw phased genotypes per site (all members), for the hover tooltip. `gts` in
  // each site is aligned to the phased-marker `samples` order.
  const sitesByPos = useMemo(
    () => (showMarkers && phasedData?.sites ? new Map(phasedData.sites.map((s) => [s.pos, s])) : null),
    [showMarkers, phasedData?.sites],
  );
  const tooltipSampleOrder = useMemo(
    () => phasedData?.samples.map((s) => s.sample) ?? [],
    [phasedData?.samples],
  );
  const memberBySample = useMemo(() => {
    const map = new Map<string, HaplotypeMemberLike>();
    familyMembers.forEach((member) => map.set(member.sample_id, member));
    return map;
  }, [familyMembers]);
  const segmentsBySample = useMemo(() => {
    const map = new Map<string, Segment[]>();
    (haplotypeData?.samples || []).forEach((entry) => map.set(entry.sample, entry.segments));
    return map;
  }, [haplotypeData?.samples]);
  const markerByPosBySample = useMemo(() => {
    const map = new Map<string, Map<number, PhasedMarker>>();
    (showMarkers ? phasedData?.samples || [] : []).forEach((entry) =>
      map.set(entry.sample, new Map(entry.markers.map((m) => [m.pos, m]))),
    );
    return map;
  }, [showMarkers, phasedData?.samples]);
  // Index parents: their marker lanes are their own alleles, so the dots are
  // coloured by their (constant) block lane instead of the allele value.
  const referenceBySample = useMemo(
    () => new Set((phasedData?.samples || []).filter((s) => s.reference).map((s) => s.sample)),
    [phasedData?.samples],
  );
  const isReferenceMember = referenceBySample.has(sampleId);
  // Server capped the per-site fetch and dropped the tail, so any surviving markers
  // would render as a partial, misleading overlay. Suppress them entirely and show a
  // 'zoom in' hint; the haplotype blocks still render normally.
  const markersTruncated = showMarkers && phasedData?.truncated === true;
  // This track member's per-region QC (present only for the index couple's children).
  // Surfaced as a small informative-site count, with a Mendel-error warning when the
  // inconsistency rate clears the threshold. Hidden when the overlay is truncated
  // (the counts then reflect only the partial covered span, so they'd mislead).
  const sampleQc = useMemo(
    () => phasedData?.samples.find((entry) => entry.sample === sampleId)?.qc ?? null,
    [phasedData?.samples, sampleId],
  );
  const showQc = showMarkers && !markersTruncated && !!sampleQc;
  const mendelWarning = !!sampleQc && sampleQc.mendel_rate > MENDEL_WARN_RATE;

  // Haplotype-block palette, shared by the canvas and the tooltip so the tooltip
  // swatches exactly match the rendered blocks.
  const palette = useMemo(
    () => ({
      father: [cssVar('--color-haplotype-father-dark'), cssVar('--color-haplotype-father-light')],
      mother: [cssVar('--color-haplotype-mother-dark'), cssVar('--color-haplotype-mother-light')],
      unknown: cssVar('--color-haplotype-unknown'),
      deleted: cssVar('--color-haplotype-deleted-fill'),
    }),
    [],
  );
  const laneColor = useCallback(
    (member: HaplotypeMemberLike, seg: Segment, lane: HaplotypeLane): string => {
      const value = seg[lane];
      if (isDeletedHaplotype(value)) return palette.deleted;
      const signature = getHaplotypeLaneSignature(member, seg, lane, chrom);
      if (!signature) return palette.unknown;
      const parsed = parseInt(value, 10);
      const pal = signature.origin === 'paternal' ? palette.father : palette.mother;
      return Number.isNaN(parsed) ? palette.unknown : pal[parsed] || palette.unknown;
    },
    [palette, chrom],
  );
  // Does a member's raw marker agree with its cleaned block here? They agree when
  // the marker's drawn colour matches the block's on every called lane.
  const markerAgreesWithBlock = useCallback(
    (member: HaplotypeMemberLike, block: Segment, marker: PhasedMarker): boolean | null => {
      const lanes: HaplotypeLane[] = [];
      if (marker.hap1 !== null) lanes.push('hap1');
      if (marker.hap2 !== null) lanes.push('hap2');
      if (lanes.length === 0) return null;
      const raw: Segment = {
        ...block,
        hap1: laneValue(marker.hap1),
        hap2: laneValue(marker.hap2),
      };
      return lanes.every((lane) => laneColor(member, raw, lane) === laneColor(member, block, lane));
    },
    [laneColor],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = width;
    canvas.height = height;
    ctx.clearRect(0, 0, width, height);
    if (isLoading || !hasRegion) return;

    const span = regionEnd - regionStart || 1;
    const half = height / 2;
    const fatherColors = [
      cssVar('--color-haplotype-father-dark'),
      cssVar('--color-haplotype-father-light'),
    ];
    const motherColors = [
      cssVar('--color-haplotype-mother-dark'),
      cssVar('--color-haplotype-mother-light'),
    ];
    const riskColors: Record<DiseaseHaplotypeKind, string> = {
      dominant: cssVar('--color-haplotype-affected'),
      'recessive-maternal': cssVar('--color-haplotype-carrier'),
      'recessive-paternal': cssVar('--color-haplotype-carrier'),
      'x-linked': cssVar('--color-haplotype-affected'),
    };
    const unknownColor = cssVar('--color-haplotype-unknown');
    const deletedFill = cssVar('--color-haplotype-deleted-fill');
    const deletedStroke = cssVar('--color-haplotype-deleted-stroke');

    const laneCenter = (lane: HaplotypeLane, single: boolean): number =>
      single ? half : lane === 'hap1' ? half / 2 : half + half / 2;

    const baseColorForLane = (seg: Segment, lane: HaplotypeLane): string => {
      const value = seg[lane];
      if (isDeletedHaplotype(value)) return deletedFill;
      const parsed = parseInt(value, 10);
      const signature = getHaplotypeLaneSignature(currentMember, seg, lane, chrom);
      if (!signature) return unknownColor;
      const palette = signature.origin === 'paternal' ? fatherColors : motherColors;
      return Number.isNaN(parsed) ? unknownColor : palette[parsed] || unknownColor;
    };
    const laneRiskKind = (seg: Segment, lane: HaplotypeLane): DiseaseHaplotypeKind | null =>
      diseaseHaplotypeKindForLane(diseaseModel, currentMember, seg, lane, chrom);

    // Lane divider.
    ctx.strokeStyle = cssVar('--color-grid');
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, half + 0.5);
    ctx.lineTo(width, half + 0.5);
    ctx.stroke();

    // Haplotype blocks — always a thin line; markers (when shown) sit on top.
    const recombXs: number[] = [];
    segments.forEach((seg, idx) => {
      const x1 = ((seg.start - regionStart) / span) * width;
      const x2 = ((seg.end - regionStart) / span) * width;
      const w = Math.max(x2 - x1, 1);
      if (idx > 0) {
        const prev = segments[idx - 1];
        if (seg.ps !== prev.ps || seg.hap1 !== prev.hap1 || seg.hap2 !== prev.hap2) {
          recombXs.push(x1);
        }
      }
      const lanes = getRenderableHaplotypeLanes(currentMember, seg, chrom);
      const single = lanes.length === 1;
      lanes.forEach((lane) => {
        const y = laneCenter(lane, single) - BAND_THICKNESS / 2;
        ctx.fillStyle = baseColorForLane(seg, lane);
        ctx.fillRect(x1, y, w, BAND_THICKNESS);
        if (isDeletedHaplotype(seg[lane])) {
          ctx.strokeStyle = deletedStroke;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x1 + 0.75, y);
          ctx.lineTo(x2 - 0.75, y + BAND_THICKNESS);
          ctx.stroke();
        }
        const riskKind = laneRiskKind(seg, lane);
        if (riskKind) drawHaplotypeRiskOverlay(ctx, x1, y, w, BAND_THICKNESS, riskColors[riskKind]);
      });
    });

    // Recombination boundaries.
    ctx.strokeStyle = cssVar('--color-axis');
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 2]);
    recombXs.forEach((x) => {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // Truncated fetch -> markers are partial/misleading, so draw none (the blocks
    // above are already rendered) and let the 'zoom in' overlay explain why.
    if (!showMarkers || markersTruncated) return;

    // Covering cleaned block for a position. Raw markers inherit that block's
    // lineage — so an untransmitted (grey) lane greys its markers too — and its
    // affected-haplotype risk flag.
    const blockAt = (pos: number): Segment | null => findCoveringBlock(segments, pos);
    // Raw per-marker calls as dots on top of the thin line. The dot keeps the raw
    // value's shade (so phasing disagreements stay visible) but takes its
    // parent-of-origin / grey lineage from the block beneath it. Index parents are
    // the exception: their marker lanes are ref/alt alleles, not homolog indices,
    // so colouring by value would toggle dark/light meaninglessly — instead we
    // colour them by their (constant) block lane so they match the blocks.
    const dotColor = (marker: PhasedMarker, lane: HaplotypeLane, block: Segment | null): string => {
      if (isReferenceMember && block) return baseColorForLane(block, lane);
      const synthetic: Segment = {
        start: marker.pos,
        end: marker.pos + 1,
        hap1: laneValue(marker.hap1),
        hap2: laneValue(marker.hap2),
        ps: null,
        hap1_lineage: block?.hap1_lineage ?? null,
        hap2_lineage: block?.hap2_lineage ?? null,
      };
      return baseColorForLane(synthetic, lane);
    };
    const paternalCy = laneCenter('hap1', false);
    const maternalCy = laneCenter('hap2', false);
    const markerHalfHeight = Math.max(half / 2 - MARKER_LANE_PADDING, 1);
    const drawMarker = (x: number, cy: number, color: string) => {
      ctx.fillStyle = color;
      ctx.fillRect(Math.floor(x), cy - markerHalfHeight, MARKER_WIDTH, markerHalfHeight * 2);
    };
    // Markers sitting on the affected haplotype get the same risk line the cleaned
    // block carries, drawn at the bottom of the marker tick.
    const drawMarkerRisk = (x: number, cy: number, block: Segment | null, lane: HaplotypeLane) => {
      const kind = block ? laneRiskKind(block, lane) : null;
      if (!kind) return;
      drawHaplotypeRiskOverlay(
        ctx,
        Math.floor(x),
        cy - markerHalfHeight,
        MARKER_WIDTH,
        markerHalfHeight * 2,
        riskColors[kind],
      );
    };
    markers.forEach((marker) => {
      const x = ((marker.pos - regionStart) / span) * width;
      const block = blockAt(marker.pos);
      if (marker.hap1 !== null) {
        drawMarker(x, paternalCy, dotColor(marker, 'hap1', block));
        drawMarkerRisk(x, paternalCy, block, 'hap1');
      }
      if (marker.hap2 !== null) {
        drawMarker(x, maternalCy, dotColor(marker, 'hap2', block));
        drawMarkerRisk(x, maternalCy, block, 'hap2');
      }
    });
  }, [
    segments,
    markers,
    showMarkers,
    markersTruncated,
    diseaseModel,
    currentMember,
    isReferenceMember,
    chrom,
    width,
    height,
    regionStart,
    regionEnd,
    isLoading,
    hasRegion,
  ]);

  const hasSegments = segments.length > 0;
  const riskState = useMemo(
    () =>
      hasSegments
        ? interpretSampleHaplotypeRisk({
            model: diseaseModel,
            samples: haplotypeData?.samples || [],
            member: currentMember,
            region: analysisRegion,
          })
        : 'uninformative',
    [hasSegments, diseaseModel, haplotypeData?.samples, currentMember, analysisRegion],
  );

  const handleMouseMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    // Anchor the hover on the UNION of all members' marker positions, not just this
    // member's own markers. A relative carries no markers of its own, but hovering
    // its track must still surface the all-member genotype table at the nearest site.
    if (!showMarkers || markersTruncated || sortedSitePositions.length === 0) {
      setTooltip(null);
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const span = regionEnd - regionStart || 1;
    const pos = regionStart + (x / width) * span;
    const sitePos = nearestPos(sortedSitePositions, pos);
    if (sitePos === null) {
      setTooltip(null);
      return;
    }
    const markerX = ((sitePos - regionStart) / span) * width;
    if (Math.abs(markerX - x) > 3) {
      setTooltip(null);
      return;
    }
    const site = sitesByPos?.get(sitePos);
    const swatch = (color: string, key: number) => (
      <span
        key={key}
        style={{
          display: 'inline-block',
          width: 9,
          height: 9,
          borderRadius: 2,
          background: color,
          marginRight: 2,
          verticalAlign: 'middle',
        }}
      />
    );
    setTooltip({
      x: event.clientX,
      y: event.clientY,
      node: (
        <div>
          <div style={{ marginBottom: 4 }}>
            <strong>
              {chrom}:{sitePos.toLocaleString()}
            </strong>
            {site && (
              <span style={{ marginLeft: 6 }}>
                {baseSpan(site.ref, 0)}
                <span style={{ color: '#94a3b8' }}>›</span>
                {baseSpan(site.alt, 1)}
              </span>
            )}
          </div>
          {site ? (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto auto auto auto',
                columnGap: 8,
                rowGap: 1,
                alignItems: 'center',
              }}
            >
              {tooltipSampleOrder.map((name, i) => {
                const member: HaplotypeMemberLike = memberBySample.get(name) ?? { sample_id: name };
                const block = findCoveringBlock(segmentsBySample.get(name) ?? [], sitePos);
                const mk = markerByPosBySample.get(name)?.get(sitePos);
                // Index parents are the reference phasing — their markers always
                // match their block by construction.
                const agrees = referenceBySample.has(name)
                  ? true
                  : block && mk
                    ? markerAgreesWithBlock(member, block, mk)
                    : null;
                return (
                  <React.Fragment key={name}>
                    <span style={{ fontWeight: name === sampleId ? 700 : 400 }}>
                      {name}
                      {member.role ? <span style={{ color: '#94a3b8' }}> ({member.role})</span> : null}
                    </span>
                    {/* The member's haplotype-block colours at this position. */}
                    <span>
                      {block ? swatch(laneColor(member, block, 'hap1'), 0) : null}
                      {block ? swatch(laneColor(member, block, 'hap2'), 1) : null}
                    </span>
                    <span style={{ fontFamily: 'monospace' }}>
                      {renderPhasedGenotype(site.gts[i] ?? '', site.ref, site.alt)}
                    </span>
                    {/* Does the raw marker agree with the cleaned block here? */}
                    <span style={{ color: agrees === false ? '#d6453d' : '#5fb37a', fontWeight: 700 }}>
                      {agrees === null ? '' : agrees ? '✓' : '✗'}
                    </span>
                  </React.Fragment>
                );
              })}
            </div>
          ) : (
            <div style={{ color: '#9ca3af' }}>No genotypes at this position</div>
          )}
        </div>
      ),
    });
  };

  return (
    <div
      className={`relative haplotype-track haplotype-track--${riskState}`}
      data-risk-state={riskState}
      style={{ width, height }}
    >
      <canvas
        ref={canvasRef}
        className={showMarkers && !markersTruncated ? 'cursor-pointer' : undefined}
        aria-label={`Haplotype risk state: ${riskState}`}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setTooltip(null)}
      />
      {isLoading && <VizLoadingOverlay message="Loading haplotypes" />}
      {!isLoading && !hasSegments && (
        <div className="viz-empty-overlay">No haplotype data in this region</div>
      )}
      {/* Too many per-site markers to fetch fully: the overlay would be partial, so
          we suppress the dots and prompt the user to zoom in. Unobtrusive, top-right,
          pointer-events off so it never blocks block hover. */}
      {!isLoading && hasSegments && markersTruncated && (
        <div
          className="viz-marker-truncated"
          style={{
            position: 'absolute',
            top: 1,
            right: 4,
            fontSize: 10,
            lineHeight: '12px',
            color: '#9ca3af',
            pointerEvents: 'none',
          }}
          title="The per-site marker overlay is capped for performance; zoom in to a smaller region to see it."
        >
          too many sites — zoom in
        </div>
      )}
      {/* Per-child QC: informative-site count, plus a Mendel-error warning when the
          inconsistency rate clears the threshold (likely sample swap / wrong pedigree).
          Small and unobtrusive, top-right, pointer-events off so it never blocks hover. */}
      {!isLoading && hasSegments && showQc && sampleQc && (
        <div
          className="viz-phased-qc"
          style={{
            position: 'absolute',
            top: 1,
            right: 4,
            fontSize: 10,
            lineHeight: '12px',
            color: mendelWarning ? '#d6453d' : '#9ca3af',
            fontWeight: mendelWarning ? 700 : 400,
            pointerEvents: 'none',
          }}
          title={
            mendelWarning
              ? `${sampleQc.mendel_errors} Mendelian inconsistencies in ${sampleQc.informative_sites} informative sites ` +
                `(${(sampleQc.mendel_rate * 100).toFixed(1)}%) — check sample identity / pedigree.`
              : `${sampleQc.informative_sites} informative sites in this region.`
          }
        >
          {mendelWarning
            ? `⚠ ${sampleQc.mendel_errors}/${sampleQc.informative_sites} Mendel errors`
            : `${sampleQc.informative_sites} informative`}
        </div>
      )}
      {tooltip && (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          {tooltip.node}
        </VizTooltip>
      )}
    </div>
  );
};

export default HaplotypePhasedTrack;
