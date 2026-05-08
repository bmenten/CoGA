import React, { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../lib/api';
import type { ApiFamilyMember, ApiFamilyRegionOfInterest } from '../../lib/apiTypes';
import { cssVar } from '../../lib/colors';
import CoverageSegmentsChart from '../../components/visualizations/CoverageSegmentsChart';
import ApcadChart from '../../components/visualizations/ApcadChart';
import SvTrack from '../../components/visualizations/SvTrack';
import GenomeHaplotypeTrack from '../../components/visualizations/GenomeHaplotypeTrack';
import GenomeRepeatExpansionTrack from '../../components/visualizations/GenomeRepeatExpansionTrack';
import Ideogram from '../../components/visualizations/Ideogram';
import VizLoadingOverlay from '../../components/visualizations/VizLoadingOverlay';
import { formatResolvedReferenceLabel } from '../../lib/reference';
import type { GenomeTrackVisibility } from './GenomeOverviewSidebar';
import ViewerMemberSection from './ViewerMemberSection';
import ViewerTrackBlock from './ViewerTrackBlock';
import { buildTrackFilterSummary, formatRoiCoordinates } from './viewerShared';

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
  chroms: string[];
}

interface GenomeTrackAvailability {
  coverage: boolean;
  segments: boolean;
  apcad: boolean;
  haplotypes: boolean;
  sv: boolean;
  repeatExpansions: boolean;
}

interface GenomeRoiRange {
  startX: number;
  endX: number;
}

interface GenomeRegionSelection {
  chrom: string;
  start: number;
  end: number;
}

interface GenomeOverviewWorkspaceProps {
  familyId: string;
  familyDisplayId: string;
  speciesName?: string;
  assemblyName: string;
  assemblyVersion?: string;
  assembly: string;
  projectId?: string;
  trackAreaRef: React.RefObject<HTMLElement | null>;
  backDest: string;
  visibleRoi: ApiFamilyRegionOfInterest | null;
  genomeRoiRange: GenomeRoiRange | null;
  navigateToChromosome: (
    chrom: string,
    region?: { start: number; end: number },
  ) => void;
  visibleMembers: ApiFamilyMember[];
  membersWithData: ApiFamilyMember[];
  trackVisibility: GenomeTrackVisibility;
  availability: Record<string, GenomeTrackAvailability>;
  variantFilters: Record<string, string>;
  sampleFilterMap: Record<string, string>;
  baseVariantParams: URLSearchParams;
  urlMaps: {
    coverage: Record<string, string[]>;
    segments: Record<string, string[]>;
    apcad: Record<string, string[]>;
    haplotypes: Record<string, string[]>;
  };
  layout: Layout | null;
  trackWidth: number;
  trackHeight: number;
  svTrackHeight: number;
  showViewerLoading: boolean;
}

const MIN_REGION_SELECT_WIDTH_PX = 5;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const getGenomePointAtX = (
  layout: Layout,
  width: number,
  x: number,
): { chrom: string; position: number } | null => {
  if (width <= 0 || layout.total <= 0) return null;
  const genomePosition = clamp((x / width) * layout.total, 0, layout.total);
  for (const chrom of layout.chroms) {
    const offset = layout.offsets[chrom];
    const length = layout.lengths[chrom];
    if (offset === undefined || !length) continue;
    const chromEnd = offset + length;
    const isLastChrom = chrom === layout.chroms[layout.chroms.length - 1];
    if (genomePosition >= offset && (genomePosition < chromEnd || (isLastChrom && genomePosition <= chromEnd))) {
      return {
        chrom,
        position: clamp(genomePosition - offset, 0, length),
      };
    }
  }
  return null;
};

const resolveGenomeRegionSelection = (
  layout: Layout,
  width: number,
  startX: number,
  endX: number,
): GenomeRegionSelection | null => {
  const x1 = Math.min(startX, endX);
  const x2 = Math.max(startX, endX);
  const startPoint = getGenomePointAtX(layout, width, x1);
  const endPoint = getGenomePointAtX(layout, width, x2);
  if (!startPoint || !endPoint || startPoint.chrom !== endPoint.chrom) {
    return null;
  }
  const chromLength = layout.lengths[startPoint.chrom] ?? 0;
  if (chromLength <= 0) return null;
  const start = clamp(Math.floor(Math.min(startPoint.position, endPoint.position)), 0, chromLength - 1);
  const end = clamp(Math.ceil(Math.max(startPoint.position, endPoint.position)), start + 1, chromLength);
  return {
    chrom: startPoint.chrom,
    start,
    end,
  };
};

const GenomeRegionSelectionSurface: React.FC<{
  layout: Layout | null;
  width: number;
  height: number;
  onSelectRegion: (chrom: string, region: { start: number; end: number }) => void;
  testId?: string;
  children: React.ReactNode;
}> = ({ layout, width, height, onSelectRegion, testId, children }) => {
  const [dragRange, setDragRange] = useState<{ startX: number; currentX: number } | null>(null);
  const suppressClickRef = useRef(false);

  const getLocalX = (event: React.MouseEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return clamp(event.clientX - bounds.left, 0, width);
  };

  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!layout) return;
    const nextX = getLocalX(event);
    setDragRange({ startX: nextX, currentX: nextX });
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const nextX = getLocalX(event);
    setDragRange((current) =>
      current ? { ...current, currentX: nextX } : current,
    );
  };

  const finishDrag = (endX: number) => {
    setDragRange((current) => {
      if (!current || !layout) {
        return null;
      }
      if (Math.abs(endX - current.startX) < MIN_REGION_SELECT_WIDTH_PX) {
        return null;
      }
      suppressClickRef.current = true;
      const selection = resolveGenomeRegionSelection(layout, width, current.startX, endX);
      if (selection) {
        onSelectRegion(selection.chrom, { start: selection.start, end: selection.end });
      }
      return null;
    });
  };

  return (
    <div
      data-testid={testId}
      className="relative"
      style={{ width, height }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={(event) => finishDrag(getLocalX(event))}
      onMouseLeave={() => setDragRange(null)}
      onClickCapture={(event) => {
        if (!suppressClickRef.current) return;
        suppressClickRef.current = false;
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      {children}
      {dragRange && (
        <div
          style={{
            position: 'absolute',
            left: Math.min(dragRange.startX, dragRange.currentX),
            top: 0,
            width: Math.abs(dragRange.currentX - dragRange.startX),
            height: '100%',
            border: `1px dashed ${cssVar('--color-coverage-border')}`,
            background: 'transparent',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  );
};

const TrackMeta: React.FC<{
  variantFilters: Record<string, string>;
  sampleFilter?: string;
}> = ({ variantFilters, sampleFilter }) => {
  const summary = buildTrackFilterSummary(variantFilters, sampleFilter);
  if (!summary) return null;
  return <span className="viewer-track-meta">{summary}</span>;
};

const GenomeOverviewWorkspace: React.FC<GenomeOverviewWorkspaceProps> = ({
  familyId,
  familyDisplayId,
  speciesName,
  assemblyName,
  assemblyVersion,
  assembly,
  projectId,
  trackAreaRef,
  backDest,
  visibleRoi,
  genomeRoiRange,
  navigateToChromosome,
  visibleMembers,
  membersWithData,
  trackVisibility,
  availability,
  variantFilters,
  sampleFilterMap,
  baseVariantParams,
  urlMaps,
  layout,
  trackWidth,
  trackHeight,
  svTrackHeight,
  showViewerLoading,
}) => {
  const roiTitle = visibleRoi ? `ROI: ${visibleRoi.label}` : undefined;
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    'Reference not linked',
  );
  const suppressedChromClick = useRef<{ chrom: string; ts: number } | null>(null);

  const handleChromosomeRegionJump = (chrom: string, start: number, end: number) => {
    suppressedChromClick.current = { chrom, ts: Date.now() };
    navigateToChromosome(chrom, { start, end });
  };

  const handleChromosomeClick = (chrom: string) => {
    if (
      suppressedChromClick.current &&
      suppressedChromClick.current.chrom === chrom &&
      Date.now() - suppressedChromClick.current.ts < 500
    ) {
      suppressedChromClick.current = null;
      return;
    }
    suppressedChromClick.current = null;
    navigateToChromosome(chrom);
  };

  return (
  <main ref={trackAreaRef} className="analysis-main analysis-main--viewer genome-view-main">
    <section className="surface-card page-top-card">
      <div className="page-header">
        <div className="space-y-2">
          <p className="page-kicker">Visualization</p>
          <h1 className="catalog-card-title">Genome overview for family {familyDisplayId}</h1>
          <p className="catalog-card-copy">{referenceLabel}</p>
        </div>
        <Link to={backDest} className="button-secondary hover:no-underline">
          Back to variants
        </Link>
      </div>
      {visibleRoi && (
        <div className="analysis-toolbar mt-5">
          <span className="badge-chip badge-chip--signature">
            ROI {visibleRoi.label} · {formatRoiCoordinates(visibleRoi)}
          </span>
        </div>
      )}
    </section>
    <section className="surface-card genome-visualization-panel space-y-6">
      <section className="viz-shell">
        {membersWithData.map((member) => (
          <ViewerMemberSection key={member.sample_id} member={member}>
            <div className="overflow-x-auto">
              {trackVisibility.coverage && availability[member.sample_id]?.coverage && (
                <ViewerTrackBlock
                  label="Coverage"
                  width={trackWidth}
                  frameClassName="mb-2 h-[120px] cursor-pointer"
                  roiRange={genomeRoiRange}
                  roiTitle={roiTitle}
                >
                  {layout && (
                    <GenomeRegionSelectionSurface
                      layout={layout}
                      width={trackWidth}
                      height={trackHeight}
                      onSelectRegion={navigateToChromosome}
                      testId={`genome-region-select-coverage-${member.sample_id}`}
                    >
                      <CoverageSegmentsChart
                        coverageUrls={urlMaps.coverage[member.sample_id]}
                        segmentsUrls={
                          trackVisibility.segments && availability[member.sample_id]?.segments
                            ? urlMaps.segments[member.sample_id]
                            : undefined
                        }
                        width={trackWidth}
                        height={trackHeight}
                        onChromosomeClick={navigateToChromosome}
                        layout={layout}
                        chroms={layout.chroms}
                      />
                    </GenomeRegionSelectionSurface>
                  )}
                </ViewerTrackBlock>
              )}
              {trackVisibility.apcad && availability[member.sample_id]?.apcad && (
                <ViewerTrackBlock
                  label="APCAD"
                  width={trackWidth}
                  frameClassName="mb-2 h-[120px] cursor-pointer"
                  roiRange={genomeRoiRange}
                  roiTitle={roiTitle}
                >
                  {layout && (
                    <GenomeRegionSelectionSurface
                      layout={layout}
                      width={trackWidth}
                      height={trackHeight}
                      onSelectRegion={navigateToChromosome}
                      testId={`genome-region-select-apcad-${member.sample_id}`}
                    >
                      <ApcadChart
                        apcadUrls={urlMaps.apcad[member.sample_id]}
                        width={trackWidth}
                        height={trackHeight}
                        onChromosomeClick={navigateToChromosome}
                        layout={layout}
                        chroms={layout.chroms}
                      />
                    </GenomeRegionSelectionSurface>
                  )}
                </ViewerTrackBlock>
              )}
              {trackVisibility.sv && availability[member.sample_id]?.sv && (
                <ViewerTrackBlock
                  label="SVs"
                  width={trackWidth}
                  meta={
                    <TrackMeta
                      variantFilters={variantFilters}
                      sampleFilter={sampleFilterMap[member.sample_id]}
                    />
                  }
                  frameClassName="mb-2 h-[80px]"
                  roiRange={genomeRoiRange}
                  roiTitle={roiTitle}
                >
                  {layout && (
                    <GenomeRegionSelectionSurface
                      layout={layout}
                      width={trackWidth}
                      height={svTrackHeight}
                      onSelectRegion={navigateToChromosome}
                      testId={`genome-region-select-sv-${member.sample_id}`}
                    >
                      <SvTrack
                        url={`${api.defaults.baseURL}/families/${familyId}/structural-variants?${(() => {
                          const params = new URLSearchParams(baseVariantParams);
                          params.set('page_size', '0');
                          params.set('track_mode', 'true');
                          params.append('sample', member.sample_id);
                          const sampleFilter = sampleFilterMap[member.sample_id];
                          if (sampleFilter) params.append('sample_filter', sampleFilter);
                          return params.toString();
                        })()}`}
                        layout={layout}
                        sampleId={member.sample_id}
                        width={trackWidth}
                        height={svTrackHeight}
                      />
                    </GenomeRegionSelectionSurface>
                  )}
                </ViewerTrackBlock>
              )}
              {trackVisibility.haplotypes && availability[member.sample_id]?.haplotypes && (
                <ViewerTrackBlock
                  label="Haplotypes"
                  width={trackWidth}
                  frameClassName="mb-2 h-[40px]"
                  roiRange={genomeRoiRange}
                  roiTitle={roiTitle}
                >
                  {layout && (
                    <GenomeRegionSelectionSurface
                      layout={layout}
                      width={trackWidth}
                      height={40}
                      onSelectRegion={navigateToChromosome}
                      testId={`genome-region-select-haplotype-${member.sample_id}`}
                    >
                      <GenomeHaplotypeTrack
                        urls={urlMaps.haplotypes[member.sample_id]}
                        sampleId={member.sample_id}
                        role={member.role}
                        affected={member.affected}
                        layout={layout}
                        width={trackWidth}
                        height={40}
                        chroms={layout.chroms}
                      />
                    </GenomeRegionSelectionSurface>
                  )}
                </ViewerTrackBlock>
              )}
              {trackVisibility.repeatExpansions &&
                availability[member.sample_id]?.repeatExpansions && (
                  <ViewerTrackBlock
                    label="Repeat expansions"
                    width={trackWidth}
                    frameClassName="mb-2 h-[20px]"
                    roiRange={genomeRoiRange}
                    roiTitle={roiTitle}
                  >
                    {layout && (
                      <GenomeRegionSelectionSurface
                        layout={layout}
                        width={trackWidth}
                        height={20}
                        onSelectRegion={navigateToChromosome}
                        testId={`genome-region-select-repeat-${member.sample_id}`}
                      >
                        <GenomeRepeatExpansionTrack
                          familyId={familyId}
                          sampleId={member.sample_id}
                          chroms={layout.chroms}
                          layout={layout}
                          width={trackWidth}
                          height={20}
                          projectId={projectId}
                        />
                      </GenomeRegionSelectionSurface>
                    )}
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
          <p className="analysis-count">No samples selected</p>
        )}
        {membersWithData.length === 0 && !showViewerLoading && visibleMembers.length > 0 && (
          <p className="analysis-count">No data for selected samples</p>
        )}
      </section>
      {layout && (
        <section className="viz-panel overflow-x-auto">
          <ViewerTrackBlock
            label="Chromosomes"
            width={trackWidth}
            frameClassName="pt-6"
            roiRange={genomeRoiRange}
            roiTitle={roiTitle}
          >
              <div className="relative h-[50px]" style={{ width: trackWidth }}>
                {layout.chroms.map((chrom) => {
                  const width = (layout.lengths[chrom] / layout.total) * trackWidth;
                  const left = (layout.offsets[chrom] / layout.total) * trackWidth;
                  return (
                    <div
                      key={chrom}
                      className="absolute cursor-pointer"
                      style={{ width, left }}
                      onClick={() => handleChromosomeClick(chrom)}
                    >
                      <Ideogram
                        assembly={assembly}
                        chrom={chrom}
                        width={width}
                        height={20}
                        regionStart={0}
                        regionEnd={layout.lengths[chrom]}
                        onRegionSelect={(start, end) =>
                          handleChromosomeRegionJump(chrom, start, end)
                        }
                        showAxis={false}
                        bandResolution="compact"
                        cornerRoundness={0.22}
                        bandFinish="glossy"
                      />
                      <div className="text-xs text-center">{chrom}</div>
                    </div>
                  );
                })}
              </div>
          </ViewerTrackBlock>
          <p className="analysis-count mt-3">
            Click a chromosome to open it, or drag within one chromosome on any genome track or
            ideogram to jump into a specific region.
          </p>
        </section>
      )}
    </section>
  </main>
  );
};

export default GenomeOverviewWorkspace;
