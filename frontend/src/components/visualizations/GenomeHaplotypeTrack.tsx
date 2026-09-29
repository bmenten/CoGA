import React, { useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { cssVar } from '../../lib/colors';
import { fetchTrackJson } from '../../lib/trackFetch';
import { drawHaplotypeRiskOverlay, haplotypeRiskPattern } from '../../lib/haplotypeCanvas';
import { segregationStateLabel } from '../../lib/embryoSegregation';
import {
  defaultDiseaseHaplotypeModel,
  diseaseHaplotypeKindForLane,
  getHaplotypeLaneSignature,
  getRenderableHaplotypeLanes,
  inferDiseaseHaplotypes,
  interpretSampleHaplotypeRisk,
  normalizeHaplotypeChrom,
  resolveHaplotypeInheritanceModel,
  type DiseaseHaplotypeKind,
  type HaplotypeLane,
  type HaplotypeMemberLike,
  type HaplotypeRiskRegion,
  type HaplotypeRiskState,
} from '../../lib/haplotypeRisk';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizErrorOverlay from './VizErrorOverlay';
import { formatChromosomeLabel } from '../../lib/chromosomes';

const DEFAULT_CHROMS = [...Array.from({ length: 22 }, (_, i) => String(i + 1)), 'X', 'Y'];

// Stable fallback for the optional familyMembers prop. An inline `= []` default
// is a fresh array every render, which would defeat the memos that depend on it.
const EMPTY_MEMBERS: HaplotypeMemberLike[] = [];

interface Segment {
  chr: string;
  start: number;
  end: number;
  hap1: string;
  hap2: string;
  ps?: number | null;
  // Pedigree-aware colour class per lane; see getHaplotypeLaneSignature.
  hap1_lineage?: string | null;
  hap2_lineage?: string | null;
  // A male has one copy of this block (chrX outside the PARs); see HaplotypeSegmentLike.
  hemizygous_in_males?: boolean | null;
}

interface HaplotypeSourceSample {
  sample: string;
  segments: Array<Segment | Omit<Segment, 'chr'>>;
}

interface HaplotypeSourceResponse {
  samples?: HaplotypeSourceSample[];
}

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
  chroms: string[];
}

interface Props {
  urls: string[];
  sampleId: string;
  role: string;
  affected: boolean;
  sex?: string | null;
  carrierStatus?: boolean | null;
  carrierType?: string | null;
  highlightRiskHaplotype?: boolean;
  layout: Layout | null;
  width?: number;
  height?: number;
  disorder?: 'dominant' | 'recessive';
  inheritanceModel?: string | null;
  familyMembers?: HaplotypeMemberLike[];
  riskRegion?: HaplotypeRiskRegion | null;
  chroms?: string[];
}

const isDeletedHaplotype = (value: string): boolean => value === '.';

const GenomeHaplotypeTrack: React.FC<Props> = ({
  urls,
  sampleId,
  role,
  affected,
  sex,
  carrierStatus = false,
  carrierType,
  highlightRiskHaplotype,
  layout,
  width = 800,
  height = 40,
  disorder = 'dominant',
  inheritanceModel,
  familyMembers = EMPTY_MEMBERS,
  riskRegion,
  chroms = DEFAULT_CHROMS,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const {
    data: segmentMap = {},
    isLoading,
    isError,
    refetch,
  } = useQuery<Record<string, Segment[]>>({
    queryKey: ['genome-haplotypes', urls.join(','), chroms.join(',')],
    queryFn: async () => {
      if (!layout) return {};
      // Every source must load. A failed source used to be dropped and the rest mapped,
      // so the disease-haplotype model and the risk state were built from partial data —
      // and cached for the session (#510). Now any failure fails the query, which is
      // never cached as data.
      const responses = await Promise.all(
        urls.map((u) => fetchTrackJson<HaplotypeSourceResponse>(u)),
      );
      const map: Record<string, Segment[]> = {};
      responses.forEach((j, idx) => {
        if (!j) return;
        (j.samples || []).forEach((s: HaplotypeSourceSample) => {
          const arr = map[s.sample] || [];
          (s.segments || []).forEach((seg) => {
            const chrom = ('chr' in seg && seg.chr ? seg.chr : undefined) || chroms[idx];
            if (!chrom) return;
            arr.push({ ...seg, chr: chrom });
          });
          map[s.sample] = arr;
        });
      });
      return map;
    },
    enabled: !!layout && urls.length > 0,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const segments = useMemo(() => segmentMap[sampleId] || [], [segmentMap, sampleId]);
  // Shared once-per-segmentMap projection reused by both diseaseModel and riskState.
  const samplesArray = useMemo(
    () =>
      Object.entries(segmentMap).map(([sample, sampleSegments]) => ({
        sample,
        segments: sampleSegments,
      })),
    [segmentMap],
  );
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
  // The disease haplotype is inferred at a locus, the ROI. Without one there is none to
  // assess: the whole of the first chromosome shown used to stand in, and the track then
  // showed a risk state for a disorder whose locus it did not know (#588).
  // An ROI without a chromosome cannot be placed on the genome either.
  const analysisRegion = riskRegion?.chr ? riskRegion : null;
  const riskEnabled =
    highlightRiskHaplotype ?? membersForRisk.some((member) => member.affected || member.carrier_status === 'carrier');
  const diseaseModel = useMemo(
    () =>
      riskEnabled && analysisRegion
        ? inferDiseaseHaplotypes({
            samples: samplesArray,
            members: membersForRisk,
            inheritanceModel: resolveHaplotypeInheritanceModel(effectiveInheritanceModel, membersForRisk),
            region: analysisRegion,
          })
        : defaultDiseaseHaplotypeModel(effectiveInheritanceModel),
    [analysisRegion, effectiveInheritanceModel, membersForRisk, riskEnabled, samplesArray],
  );

  useEffect(() => {
    if (isLoading) return;
    if (!layout || !canvasRef.current) return;
    if (isError) {
      const errorCtx = canvasRef.current.getContext('2d');
      errorCtx?.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
      return;
    }
    const ctx = canvasRef.current.getContext('2d');
    if (!ctx) return;

    canvasRef.current.width = width;
    canvasRef.current.height = height;
    ctx.clearRect(0, 0, width, height);

    const fatherColors = [cssVar('--color-haplotype-father-dark'), cssVar('--color-haplotype-father-light')];
    const motherColors = [cssVar('--color-haplotype-mother-dark'), cssVar('--color-haplotype-mother-light')];
    const riskColors: Record<DiseaseHaplotypeKind, string> = {
      dominant: cssVar('--color-haplotype-affected'),
      'recessive-maternal': cssVar('--color-haplotype-carrier'),
      'recessive-paternal': cssVar('--color-haplotype-carrier'),
      'x-linked': cssVar('--color-haplotype-affected'),
    };
    const unknownColor = cssVar('--color-haplotype-unknown');
    const deletedFill = cssVar('--color-haplotype-deleted-fill');
    const deletedStroke = cssVar('--color-haplotype-deleted-stroke');

    const half = height / 2;

    const recombXs: number[] = [];
    const prevByChr: Record<string, Segment> = {};
    // A signature is a parent of origin and a homolog label, and the labels are defined per
    // chromosome: found at the ROI, it means nothing on another chromosome, where it matched
    // unrelated homologs and drew them as the risk haplotype (#588).
    const riskChrom = analysisRegion ? normalizeHaplotypeChrom(analysisRegion.chr) : null;

    const baseColorForLane = (seg: Segment, lane: HaplotypeLane): string => {
      const value = seg[lane];
      if (isDeletedHaplotype(value)) return deletedFill;
      const parsed = parseInt(value, 10);
      const signature = getHaplotypeLaneSignature(currentMember, seg, lane, seg.chr);
      if (!signature) return unknownColor;
      const palette = signature.origin === 'paternal' ? fatherColors : motherColors;
      return isNaN(parsed) ? unknownColor : palette[parsed] || unknownColor;
    };

    segments.forEach((seg) => {
      const chr = seg.chr;
      const offset = layout.offsets[chr];
      if (offset === undefined) return;
      const x1 = ((offset + seg.start) / layout.total) * width;
      const x2 = ((offset + seg.end) / layout.total) * width;
      const w = Math.max(x2 - x1, 1);
      const prev = prevByChr[chr];
      if (prev && (seg.ps !== prev.ps || seg.hap1 !== prev.hap1 || seg.hap2 !== prev.hap2)) {
        recombXs.push(x1);
      }
      prevByChr[chr] = seg;
      const lanes = getRenderableHaplotypeLanes(currentMember, seg, chr);
      lanes.forEach((lane) => {
        const riskKind =
          riskChrom !== null && normalizeHaplotypeChrom(chr) === riskChrom
            ? diseaseHaplotypeKindForLane(diseaseModel, currentMember, seg, lane, chr)
            : null;
        const isSingleLane = lanes.length === 1;
        const y = isSingleLane ? 0 : lane === 'hap1' ? 0 : half + 1;
        const rectHeight = isSingleLane ? height : half - 1;
        ctx.fillStyle = baseColorForLane(seg, lane);
        ctx.fillRect(x1, y, w, rectHeight);
        if (isDeletedHaplotype(seg[lane])) {
          ctx.strokeStyle = deletedStroke;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x1 + 0.75, y + 1);
          ctx.lineTo(x2 - 0.75, y + rectHeight - 1);
          ctx.stroke();
        }
        if (riskKind) {
          drawHaplotypeRiskOverlay(ctx, x1, y, w, rectHeight, riskColors[riskKind], {
            pattern: haplotypeRiskPattern(riskKind),
          });
        }
      });
    });

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
  }, [
    segments,
    role,
    affected,
    sex,
    carrierStatus,
    layout,
    width,
    height,
    currentMember,
    diseaseModel,
    analysisRegion,
    chroms,
    isLoading,
    isError,
  ]);
  const riskState = useMemo<HaplotypeRiskState | null>(
    () =>
      !analysisRegion
        ? null
        : segments.length > 0
          ? interpretSampleHaplotypeRisk({
              model: diseaseModel,
              samples: samplesArray,
              member: currentMember,
              region: analysisRegion,
            })
          : 'uninformative',
    [segments, diseaseModel, samplesArray, currentMember, analysisRegion],
  );

  // A failed load has no risk state — not "uninformative", which is a real outcome — and
  // without an ROI none was assessed (#588).
  const shownRiskState = isError ? 'unavailable' : (riskState ?? 'not_assessed');

  // The accessible name: whose haplotypes, where, and the risk state the track's border
  // shows, with the ROI it was assessed at. A load in flight claims no risk state, a
  // failed one says so (#510, #529), and without an ROI none was assessed (#588).
  const trackLabel = (() => {
    const where = chroms.length === 1 ? `on ${formatChromosomeLabel(chroms[0])}` : `across ${chroms.length} chromosomes`;
    const subject = `Haplotypes of ${sampleId} ${where}`;
    if (isError) return `${subject}: failed to load; risk state: unavailable`;
    if (isLoading || !layout) return `${subject}: loading`;
    const shown = `${subject}${segments.length ? '' : ': no data'}`;
    if (!analysisRegion || !riskState) return `${shown}; risk state: not assessed, no region of interest`;
    const riskScope = `${formatChromosomeLabel(String(analysisRegion.chr))}:${analysisRegion.start.toLocaleString()}–${analysisRegion.end.toLocaleString()}`;
    return `${shown}; risk state at ${riskScope}: ${segregationStateLabel(riskState).toLowerCase()}`;
  })();

  return (
    <div
      className={`relative haplotype-track haplotype-track--${shownRiskState}`}
      data-risk-state={shownRiskState}
      style={{ width, height }}
    >
      <canvas ref={canvasRef} role="img" aria-label={trackLabel} />
      {isLoading && <VizLoadingOverlay message="Loading haplotypes" />}
      {isError && <VizErrorOverlay what="haplotypes" onRetry={() => void refetch()} />}
      {!isLoading && !isError && layout && segments.length === 0 && (
        <div className="viz-empty-overlay">No haplotype data</div>
      )}
    </div>
  );
};

export default GenomeHaplotypeTrack;
