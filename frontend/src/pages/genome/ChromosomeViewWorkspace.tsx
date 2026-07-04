import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiFamilyMember, ApiFamilyRegionOfInterest } from '../../lib/apiTypes';
import CoverageSegmentsChart from '../../components/visualizations/CoverageSegmentsChart';
import ApcadChart from '../../components/visualizations/ApcadChart';
import Ideogram from '../../components/visualizations/Ideogram';
import ZoomedIdeogram from '../../components/visualizations/ZoomedIdeogram';
import VariantTrack from '../../components/visualizations/VariantTrack';
import HaplotypePhasedTrack from '../../components/visualizations/HaplotypePhasedTrack';
import HaplotypeLegend from '../../components/visualizations/HaplotypeLegend';
import GeneTrack from '../../components/visualizations/GeneTrack';
import BlacklistTrack from '../../components/visualizations/BlacklistTrack';
import CnvTrack from '../../components/visualizations/CnvTrack';
import DgvTrack from '../../components/visualizations/DgvTrack';
import SegmentalDuplicationTrack from '../../components/visualizations/SegmentalDuplicationTrack';
import SmallVariantTrack from '../../components/visualizations/SmallVariantTrack';
import RepeatExpansionTrack from '../../components/visualizations/RepeatExpansionTrack';
import VizLoadingOverlay from '../../components/visualizations/VizLoadingOverlay';
import { getErrorMessage } from '../../lib/errorMessage';
import { resolveHaplotypeInheritanceModel } from '../../lib/haplotypeRisk';
import { formatResolvedReferenceLabel } from '../../lib/reference';
import ViewerMemberSection from './ViewerMemberSection';
import ViewerTrackBlock from './ViewerTrackBlock';
import ViewerInteractionSurface from './ViewerInteractionSurface';
import type { ChromosomeTrackVisibility } from './ChromosomeViewSidebar';
import {
  CHROMS,
  buildTrackFilterSummary,
  formatChromosomeLabel,
  formatBp,
  formatRoiCoordinates,
  normalizeChrom,
} from './viewerShared';

const TRACK_HEIGHT = 120;
const VARIANT_TRACK_HEIGHT = 80;
// Combined haplotype + phased-marker track: two lanes (paternal / maternal),
// each a thin haplotype line. Taller (room for the marker dots in both lanes)
// when the overlay is on, a slim two-line band when it is off.
const HAPLOTYPE_TRACK_HEIGHT = 20;
const HAPLOTYPE_MARKERS_TRACK_HEIGHT = 40;
const ZOOMED_IDEOGRAM_HEIGHT = 40;
const CNV_TRACK_HEIGHT = 20;
const DGV_TRACK_HEIGHT = 48;
const SEGMENTAL_DUPLICATION_TRACK_HEIGHT = 20;
const BLACKLIST_TRACK_HEIGHT = 20;
const SMALL_VARIANT_TRACK_HEIGHT = 20;
// Taller frame when the small-variant track splits into three parental-origin rows.
const SMALL_VARIANT_ORIGIN_TRACK_HEIGHT = 45;
const REPEAT_TRACK_HEIGHT = 20;
const GENE_JUMP_MIN_PADDING_BP = 5_000;
const GENE_JUMP_PADDING_RATIO = 0.1;

interface ChromosomeTrackAvailability {
  coverage: boolean;
  apcad: boolean;
  apcadPcf: boolean;
  variants: boolean;
  smallVariants: boolean;
  haplotypes: boolean;
  repeatExpansions: boolean;
}

interface ChromosomeRoiRange {
  startX: number;
  endX: number;
}

interface ChromosomeViewWorkspaceProps {
  familyId: string;
  familyDisplayId: string;
  chrom: string;
  speciesName?: string;
  assemblyVersion?: string;
  assembly: string;
  assemblyId?: string;
  projectId?: string;
  trackAreaRef: React.RefObject<HTMLElement | null>;
  region: { start: number; end: number };
  trackWidth: number;
  backDest: string;
  genomeViewHref: string;
  igvHref: string;
  chromInfoSize?: number;
  visibleRoi: ApiFamilyRegionOfInterest | null;
  inheritanceModel?: string | null;
  chromosomeRoiRange: ChromosomeRoiRange | null;
  regionRoiRange: ChromosomeRoiRange | null;
  onChromChange: (chrom: string) => void;
  onRegionStartChange: (value: number) => void;
  onRegionEndChange: (value: number) => void;
  onResetRange: () => void;
  onPan: (direction: -1 | 1) => void;
  onZoom: (factor: number) => void;
  onZoomAt: (factor: number, focus: number) => void;
  onRegionSelect: (start: number, end: number) => void;
  onRoiZoom: () => void;
  onJumpToRegion: (chrom: string, region: { start: number; end: number }) => void;
  familyMembers: ApiFamilyMember[];
  visibleMembers: ApiFamilyMember[];
  membersWithData: ApiFamilyMember[];
  availability: Record<string, ChromosomeTrackAvailability>;
  trackVisibility: ChromosomeTrackVisibility;
  variantFilters: Record<string, string>;
  sampleFilterMap: Record<string, string>;
  detailWindow: number;
  binLimit: number;
  apcadPointLimit: number;
  segmentLimit: number;
  showViewerLoading: boolean;
}

interface GeneSuggestion {
  symbol: string;
  gene_id: string;
  chr: string;
  start: number;
  end: number;
  transcript_count: number;
  assembly_count: number;
}

interface GeneJumpProfile {
  symbol: string;
  chr: string;
  start: number;
  end: number;
}

const TrackMeta: React.FC<{
  variantFilters: Record<string, string>;
  sampleFilter?: string;
}> = ({ variantFilters, sampleFilter }) => {
  const summary = buildTrackFilterSummary(variantFilters, sampleFilter);
  if (!summary) return null;
  return <span className="viewer-track-meta">{summary}</span>;
};

// normalizeChrom already trims, strips `chr`, maps m/mt→MT, collapses numeric
// strings, and uppercases — so no further post-processing is needed here.
const normalizeChromosomeTarget = (value: string): string => normalizeChrom(value.trim());

const parseJumpRegion = (value: string): { chrom: string; start: number; end: number } | null => {
  const match = value.trim().match(/^([^:]+)\s*:\s*([\d,]+)(?:\s*[-–]\s*([\d,]+))?$/i);
  if (!match) return null;

  const chrom = normalizeChromosomeTarget(match[1]);
  const start = Number.parseInt(match[2].replace(/,/g, ''), 10);
  const end = match[3] ? Number.parseInt(match[3].replace(/,/g, ''), 10) : start + 1;

  if (!chrom || Number.isNaN(start) || Number.isNaN(end) || start < 0 || end < start) {
    return null;
  }

  return {
    chrom,
    start,
    end: Math.max(end, start + 1),
  };
};

const buildGeneJumpRegion = (profile: GeneJumpProfile): { chrom: string; start: number; end: number } => {
  const span = Math.max(profile.end - profile.start, 1);
  const padding = Math.max(Math.round(span * GENE_JUMP_PADDING_RATIO), GENE_JUMP_MIN_PADDING_BP);
  return {
    chrom: normalizeChromosomeTarget(profile.chr),
    start: Math.max(0, profile.start - padding),
    end: profile.end + padding,
  };
};

const ChromosomeViewWorkspace: React.FC<ChromosomeViewWorkspaceProps> = ({
  familyId,
  familyDisplayId,
  chrom,
  speciesName,
  assemblyVersion,
  assembly,
  assemblyId,
  projectId,
  trackAreaRef,
  region,
  trackWidth,
  backDest,
  genomeViewHref,
  igvHref,
  chromInfoSize,
  visibleRoi,
  inheritanceModel,
  chromosomeRoiRange,
  regionRoiRange,
  onChromChange,
  onRegionStartChange,
  onRegionEndChange,
  onResetRange,
  onPan,
  onZoom,
  onZoomAt,
  onRegionSelect,
  onRoiZoom,
  onJumpToRegion,
  familyMembers,
  visibleMembers,
  membersWithData,
  availability,
  trackVisibility,
  variantFilters,
  sampleFilterMap,
  detailWindow,
  binLimit,
  apcadPointLimit,
  segmentLimit,
  showViewerLoading,
}) => {
  const roiTitle = visibleRoi ? `ROI: ${visibleRoi.label}` : undefined;
  const regionStartParam = Math.max(0, Math.floor(region.start));
  const regionEndParam = Math.ceil(region.end);
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName: assembly, assemblyVersion },
    'Reference not linked',
  );
  const [jumpQuery, setJumpQuery] = useState('');
  const [jumpError, setJumpError] = useState<string | null>(null);
  const [jumpLoading, setJumpLoading] = useState(false);
  // What a plain click-drag over the tracks does. Wheel always zooms; this only
  // switches the drag gesture between grabbing (pan) and rubber-band (zoom).
  const [interactionMode, setInteractionMode] = useState<'pan' | 'zoom'>('pan');
  const trimmedJumpQuery = jumpQuery.trim();
  const parsedJumpRegion = useMemo(() => parseJumpRegion(trimmedJumpQuery), [trimmedJumpQuery]);
  const isLocationJump = parsedJumpRegion !== null;
  const suggestionListId = `chromosome-jump-suggestions-${familyId}`;
  const viewportInteraction = chromInfoSize
    ? {
        chromSize: chromInfoSize,
        regionStart: region.start,
        regionEnd: region.end,
        mode: interactionMode,
        onChange: onRegionSelect,
        onZoomAt,
      }
    : undefined;
  const hasCarrierSegregation = familyMembers.some((member) => member.carrier_status === 'carrier');
  const resolvedHaplotypeInheritanceModel = resolveHaplotypeInheritanceModel(inheritanceModel, familyMembers);
  const haplotypeDisorder = hasCarrierSegregation ? 'recessive' : 'dominant';
  const highlightRiskHaplotype = familyMembers.some(
    (member) => member.affected || member.carrier_status === 'carrier',
  );
  const haplotypeRiskRegion =
    visibleRoi && normalizeChrom(visibleRoi.chr) === normalizeChrom(chrom) ? visibleRoi : null;
  // Per-marker parent-of-origin requires a trio: both parents present, and the
  // marker track is only meaningful for the children (non-parent members).
  const parentRole = (role?: string | null) => {
    const normalized = (role || '').toLowerCase();
    return normalized === 'father' || normalized === 'mother';
  };
  const hasBothParents =
    familyMembers.some((member) => (member.role || '').toLowerCase() === 'father') &&
    familyMembers.some((member) => (member.role || '').toLowerCase() === 'mother');
  const fatherSampleId =
    familyMembers.find((member) => (member.role || '').toLowerCase() === 'father')?.sample_id ?? null;
  const motherSampleId =
    familyMembers.find((member) => (member.role || '').toLowerCase() === 'mother')?.sample_id ?? null;
  const { data: geneSuggestions = [] } = useQuery<GeneSuggestion[]>({
    queryKey: ['chromosome-jump-suggestions', assemblyId, trimmedJumpQuery],
    enabled: trimmedJumpQuery.length >= 2 && !isLocationJump,
    queryFn: async () => {
      const response = await api.get('/genes/search', {
        params: { q: trimmedJumpQuery },
      });
      return response.data as GeneSuggestion[];
    },
    retry: false,
  });

  const handleJumpSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!trimmedJumpQuery) return;

    setJumpError(null);

    if (parsedJumpRegion) {
      onJumpToRegion(parsedJumpRegion.chrom, {
        start: parsedJumpRegion.start,
        end: parsedJumpRegion.end,
      });
      return;
    }

    setJumpLoading(true);
    try {
      const response = await api.get('/genes/profile', {
        params: {
          symbol: trimmedJumpQuery,
          assembly_id: assemblyId || undefined,
          family_id: familyId,
          project_id: projectId || undefined,
        },
      });
      const profile = response.data as GeneJumpProfile;
      const nextRegion = buildGeneJumpRegion(profile);
      setJumpQuery(profile.symbol);
      onJumpToRegion(nextRegion.chrom, {
        start: nextRegion.start,
        end: nextRegion.end,
      });
    } catch (error) {
      setJumpError(getErrorMessage(error, 'Could not resolve that gene or locus.'));
    } finally {
      setJumpLoading(false);
    }
  };

  return (
    <main ref={trackAreaRef} className="analysis-main analysis-main--viewer chromosome-view-main">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Visualization</p>
            <h1 className="catalog-card-title">Chromosome view for family {familyDisplayId}</h1>
            <p className="catalog-card-copy">
              {referenceLabel} • {formatChromosomeLabel(chrom)} • {formatBp(Math.max(region.end - region.start, 0))}
            </p>
          </div>
          <div className="inline-actions">
            <Link to={backDest} className="button-secondary hover:no-underline">
              Back to variants
            </Link>
            <Link to={genomeViewHref} className="button-secondary hover:no-underline">
              Genome view
            </Link>
            <Link to={igvHref} className="button-secondary hover:no-underline" title="Open this region in IGV">
              Open in IGV
            </Link>
          </div>
        </div>
        <div className="analysis-toolbar mt-5 items-end">
          <label className="field-label">
            Chromosome
            <select value={chrom} onChange={(event) => onChromChange(event.target.value)}>
              {CHROMS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            Start
            <input
              type="number"
              value={region.start}
              onChange={(event) => onRegionStartChange(Number(event.target.value))}
            />
          </label>
          <label className="field-label">
            End
            <input
              type="number"
              value={region.end}
              onChange={(event) => onRegionEndChange(Number(event.target.value))}
            />
          </label>
          <form className="analysis-jump-form" onSubmit={handleJumpSubmit}>
            <label className="field-label analysis-jump-field">
              Jump to gene or locus
              <input
                value={jumpQuery}
                onChange={(event) => setJumpQuery(event.target.value)}
                placeholder="BRCA1 or chr17:43,044,295-43,125,482"
                list={suggestionListId}
                aria-label="Jump to gene or locus"
              />
              <datalist id={suggestionListId}>
                {geneSuggestions.map((suggestion) => (
                  <option key={`${suggestion.symbol}-${suggestion.gene_id}`} value={suggestion.symbol}>
                    {`${formatChromosomeLabel(suggestion.chr)}:${suggestion.start.toLocaleString()}-${suggestion.end.toLocaleString()}`}
                  </option>
                ))}
              </datalist>
            </label>
            <button type="submit" className="form-button" disabled={!trimmedJumpQuery || jumpLoading}>
              {jumpLoading ? 'Going…' : 'Go'}
            </button>
          </form>
          <button
            type="button"
            onClick={onResetRange}
            className="analysis-pill analysis-pill--muted analysis-pill-button"
          >
            Reset range
          </button>
          <div className="analysis-control-group" aria-label="Viewport controls">
            <button
              type="button"
              className="analysis-icon-button"
              onClick={() => onPan(-1)}
              disabled={!chromInfoSize || region.end <= region.start}
              aria-label="Pan left"
              title="Pan left"
            >
              ←
            </button>
            <button
              type="button"
              className="analysis-icon-button"
              onClick={() => onZoom(2)}
              disabled={!chromInfoSize || region.end <= region.start}
              aria-label="Zoom out"
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              className="analysis-icon-button"
              onClick={() => onZoom(0.5)}
              disabled={!chromInfoSize || region.end <= region.start}
              aria-label="Zoom in"
              title="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              className="analysis-icon-button"
              onClick={() => onPan(1)}
              disabled={!chromInfoSize || region.end <= region.start}
              aria-label="Pan right"
              title="Pan right"
            >
              →
            </button>
          </div>
          <div className="analysis-control-group" role="group" aria-label="Drag mode">
            <button
              type="button"
              className={`analysis-pill-button${interactionMode === 'pan' ? ' analysis-pill-button--active' : ''}`}
              onClick={() => setInteractionMode('pan')}
              aria-pressed={interactionMode === 'pan'}
              title="Drag any track to pan left/right"
            >
              Pan
            </button>
            <button
              type="button"
              className={`analysis-pill-button${interactionMode === 'zoom' ? ' analysis-pill-button--active' : ''}`}
              onClick={() => setInteractionMode('zoom')}
              aria-pressed={interactionMode === 'zoom'}
              title="Drag across the tracks to select a region to zoom into"
            >
              Zoom
            </button>
          </div>
          <span className="analysis-pill analysis-pill--muted">
            Window {formatBp(Math.max(region.end - region.start, 0))}
          </span>
          {visibleRoi && (
            <button
              type="button"
              className="badge-chip badge-chip--signature badge-chip-button"
              onClick={onRoiZoom}
              title="Zoom to family ROI"
            >
              ROI {visibleRoi.label} · {formatRoiCoordinates(visibleRoi)}
            </button>
          )}
          <span className="analysis-pill analysis-pill--muted">
            Scroll to zoom · drag to {interactionMode === 'pan' ? 'pan' : 'select region'}
          </span>
        </div>
        {jumpError && <p className="status-note status-note--error mt-4">{jumpError}</p>}
      </section>

      <section className="surface-card chromosome-visualization-panel space-y-6">
        <section className="viz-panel">
          <div className="overflow-x-auto">
            <ViewerTrackBlock
              label="Ideogram"
              width={trackWidth}
              frameClassName="h-[40px]"
              roiRange={chromosomeRoiRange}
              roiTitle={roiTitle}
            >
              <Ideogram
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={40}
                regionStart={region.start}
                regionEnd={region.end}
                onRegionSelect={onRegionSelect}
                bandFinish="glossy"
              />
            </ViewerTrackBlock>
          </div>
        </section>

        <ViewerInteractionSurface className="space-y-6">
        <section className="viz-shell">
          {membersWithData.map((member) => (
            <ViewerMemberSection key={`${familyDisplayId}-${member.sample_id}`} member={member}>
              <div className="overflow-x-auto space-y-2">
                {trackVisibility.coverage && availability[member.sample_id]?.coverage && (
                  <ViewerTrackBlock
                    label="Coverage"
                    width={trackWidth}
                    frameClassName="h-[120px]"
                    roiRange={regionRoiRange}
                    roiTitle={roiTitle}
                    viewportInteraction={viewportInteraction}
                  >
                    <CoverageSegmentsChart
                      coverageUrls={[
                        `${api.defaults.baseURL}/bed/${member.sample_id}/coverage?chrom=${chrom}&start=${regionStartParam}&end=${regionEndParam}&window=${detailWindow}&limit=${binLimit}&format=json`,
                      ]}
                      segmentsUrls={[
                        `${api.defaults.baseURL}/bed/${member.sample_id}/segments?chrom=${chrom}&start=${regionStartParam}&end=${regionEndParam}&limit=${segmentLimit}&format=json`,
                      ]}
                      width={trackWidth}
                      height={TRACK_HEIGHT}
                      chroms={[chrom]}
                      regionStart={region.start}
                      regionEnd={region.end}
                    />
                  </ViewerTrackBlock>
                )}
                {trackVisibility.apcad &&
                  (availability[member.sample_id]?.apcad || availability[member.sample_id]?.apcadPcf) && (
                    <ViewerTrackBlock
                      label="APCAD"
                      width={trackWidth}
                      frameClassName="h-[120px]"
                      roiRange={regionRoiRange}
                      roiTitle={roiTitle}
                      viewportInteraction={viewportInteraction}
                    >
                      <ApcadChart
                        apcadUrls={[
                          `${api.defaults.baseURL}/bed/${member.sample_id}/apcad?chrom=${chrom}&start=${regionStartParam}&end=${regionEndParam}&window=${detailWindow}&limit=${apcadPointLimit}&format=json`,
                        ]}
                        pcfUrls={
                          availability[member.sample_id]?.apcadPcf
                            ? [
                                `${api.defaults.baseURL}/bed/${member.sample_id}/apcad_pcf?chrom=${chrom}&start=${regionStartParam}&end=${regionEndParam}&limit=${segmentLimit}&format=json`,
                              ]
                            : undefined
                        }
                        width={trackWidth}
                        height={TRACK_HEIGHT}
                        chroms={[chrom]}
                        regionStart={region.start}
                        regionEnd={region.end}
                      />
                    </ViewerTrackBlock>
                  )}
                {trackVisibility.variants && availability[member.sample_id]?.variants && (
                  <ViewerTrackBlock
                    label="SVs"
                    width={trackWidth}
                    meta={
                      <TrackMeta variantFilters={variantFilters} sampleFilter={sampleFilterMap[member.sample_id]} />
                    }
                    frameClassName="h-[80px]"
                    roiRange={regionRoiRange}
                    roiTitle={roiTitle}
                    viewportInteraction={viewportInteraction}
                  >
                    <VariantTrack
                      key={`${familyDisplayId}-${member.sample_id}`}
                      familyId={familyDisplayId}
                      sampleId={member.sample_id}
                      chrom={chrom}
                      regionStart={region.start}
                      regionEnd={region.end}
                      width={trackWidth}
                      height={VARIANT_TRACK_HEIGHT}
                      filters={{
                        ...variantFilters,
                        ...(sampleFilterMap[member.sample_id]
                          ? {
                              sample_filter: sampleFilterMap[member.sample_id],
                            }
                          : {}),
                      }}
                    />
                  </ViewerTrackBlock>
                )}
                {trackVisibility.smallVariants &&
                  availability[member.sample_id]?.smallVariants &&
                  (() => {
                    // Split into paternal/undetermined/maternal rows only for a
                    // child (non-parent) that has a parent present in the family.
                    const isChild = !parentRole(member.role);
                    const paternalSampleId = isChild ? fatherSampleId : null;
                    const maternalSampleId = isChild ? motherSampleId : null;
                    const originRows = Boolean(paternalSampleId || maternalSampleId);
                    return (
                      <ViewerTrackBlock
                        label="Small variants"
                        width={trackWidth}
                        meta={
                          <TrackMeta
                            variantFilters={variantFilters}
                            sampleFilter={sampleFilterMap[member.sample_id]}
                          />
                        }
                        frameClassName={originRows ? 'h-[45px]' : 'h-[20px]'}
                        roiRange={regionRoiRange}
                        roiTitle={roiTitle}
                        viewportInteraction={viewportInteraction}
                      >
                        <SmallVariantTrack
                          key={`sm-${familyDisplayId}-${member.sample_id}`}
                          familyId={familyDisplayId}
                          sampleId={member.sample_id}
                          chrom={chrom}
                          regionStart={region.start}
                          regionEnd={region.end}
                          width={trackWidth}
                          height={originRows ? SMALL_VARIANT_ORIGIN_TRACK_HEIGHT : SMALL_VARIANT_TRACK_HEIGHT}
                          paternalSampleId={paternalSampleId}
                          maternalSampleId={maternalSampleId}
                          filters={{
                            ...variantFilters,
                            ...(sampleFilterMap[member.sample_id]
                              ? {
                                  sample_filter: sampleFilterMap[member.sample_id],
                                }
                              : {}),
                          }}
                        />
                      </ViewerTrackBlock>
                    );
                  })()}
                {trackVisibility.haplotypes &&
                  availability[member.sample_id]?.haplotypes &&
                  (() => {
                    // Markers are drawn for the whole family (parents included)
                    // when both parents are present and the overlay is enabled.
                    const showMarkers = trackVisibility.phasedMarkers && hasBothParents;
                    return (
                      <ViewerTrackBlock
                        label="Haplotypes"
                        width={trackWidth}
                        meta={
                          highlightRiskHaplotype ? (
                            <HaplotypeLegend inheritanceModel={resolvedHaplotypeInheritanceModel} />
                          ) : undefined
                        }
                        frameClassName={showMarkers ? 'h-[40px]' : 'h-[20px]'}
                        roiRange={regionRoiRange}
                        roiTitle={roiTitle}
                        viewportInteraction={viewportInteraction}
                      >
                        <HaplotypePhasedTrack
                          familyId={familyDisplayId}
                          sampleId={member.sample_id}
                          chrom={chrom}
                          regionStart={region.start}
                          regionEnd={region.end}
                          width={trackWidth}
                          height={showMarkers ? HAPLOTYPE_MARKERS_TRACK_HEIGHT : HAPLOTYPE_TRACK_HEIGHT}
                          role={member.role}
                          affected={member.affected}
                          sex={member.sex}
                          carrierStatus={member.carrier_status === 'carrier'}
                          carrierType={member.carrier_type}
                          highlightRiskHaplotype={highlightRiskHaplotype}
                          disorder={haplotypeDisorder}
                          inheritanceModel={resolvedHaplotypeInheritanceModel}
                          familyMembers={familyMembers}
                          riskRegion={haplotypeRiskRegion}
                          showMarkers={showMarkers}
                        />
                      </ViewerTrackBlock>
                    );
                  })()}
                {trackVisibility.repeatExpansions && availability[member.sample_id]?.repeatExpansions && (
                  <ViewerTrackBlock
                    label="Repeat expansions"
                    width={trackWidth}
                    frameClassName="h-[20px]"
                    roiRange={chromosomeRoiRange}
                    roiTitle={roiTitle}
                    viewportInteraction={viewportInteraction}
                  >
                    <RepeatExpansionTrack
                      familyId={familyDisplayId}
                      sampleId={member.sample_id}
                      chrom={chrom}
                      regionStart={region.start}
                      regionEnd={region.end}
                      width={trackWidth}
                      height={REPEAT_TRACK_HEIGHT}
                      projectId={projectId}
                      chromosomeSize={chromInfoSize}
                    />
                  </ViewerTrackBlock>
                )}
              </div>
            </ViewerMemberSection>
          ))}
          {membersWithData.length === 0 && showViewerLoading && (
            <div className="viz-panel">
              <div className="viz-frame relative h-[120px]" style={{ width: trackWidth }}>
                <VizLoadingOverlay message="Loading selected tracks" />
              </div>
            </div>
          )}
          {membersWithData.length === 0 && !showViewerLoading && visibleMembers.length === 0 && (
            <p className="analysis-count">No samples selected.</p>
          )}
          {membersWithData.length === 0 && !showViewerLoading && visibleMembers.length > 0 && (
            <p className="analysis-count">No BED data for selected samples.</p>
          )}
        </section>

        <section className="viz-panel">
          <div className="overflow-x-auto space-y-2">
            <ViewerTrackBlock
              label="Genes"
              width={trackWidth}
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <GeneTrack
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
            <ViewerTrackBlock
              label="Clin CNVs"
              width={trackWidth}
              frameClassName="h-[20px]"
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <CnvTrack
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={CNV_TRACK_HEIGHT}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
            <ViewerTrackBlock
              label="DGV"
              width={trackWidth}
              frameClassName="h-[48px]"
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <DgvTrack
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={DGV_TRACK_HEIGHT}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
            <ViewerTrackBlock
              label="Blacklist"
              width={trackWidth}
              frameClassName="h-[20px]"
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <BlacklistTrack
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={BLACKLIST_TRACK_HEIGHT}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
            <ViewerTrackBlock
              label="SegDup/LCR"
              width={trackWidth}
              frameClassName="h-[20px]"
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <SegmentalDuplicationTrack
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={SEGMENTAL_DUPLICATION_TRACK_HEIGHT}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
            <ViewerTrackBlock
              label="Zoomed ideogram"
              width={trackWidth}
              frameClassName="h-[40px]"
              roiRange={regionRoiRange}
              roiTitle={roiTitle}
              viewportInteraction={viewportInteraction}
            >
              <ZoomedIdeogram
                assembly={assembly}
                chrom={chrom}
                width={trackWidth}
                height={ZOOMED_IDEOGRAM_HEIGHT}
                regionStart={region.start}
                regionEnd={region.end}
              />
            </ViewerTrackBlock>
          </div>
        </section>
        </ViewerInteractionSurface>
      </section>
    </main>
  );
};

export default ChromosomeViewWorkspace;
