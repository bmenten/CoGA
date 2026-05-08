import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiChromosomeTrackAvailability,
  ApiFamilyRecord,
  ApiFamilyRegionOfInterest,
  ApiTrackAvailabilityResponse,
} from '../../lib/apiTypes';
import PageState from '../../components/PageState';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { getChromosomeWindow } from '../../lib/settings';
import { parseExplicitSampleFilterMap } from '../../lib/sampleFilterState';
import { getAdaptiveTrackWindow, getTrackBinLimit, getTrackSegmentLimit } from '../../lib/trackSampling';
import { useFamilyReference } from '../../lib/reference';
import { useMeasuredWidth } from '../../lib/useMeasuredWidth';
import ChromosomeViewSidebar, {
  type ChromosomeTrackKey,
  type ChromosomeTrackVisibility,
} from './ChromosomeViewSidebar';
import ChromosomeViewWorkspace from './ChromosomeViewWorkspace';
import {
  DEFAULT_TRACK_WIDTH,
  TRACK_WIDTH_PADDING,
  normalizeChrom,
} from './viewerShared';

interface ChromInfo {
  chr: string;
  size: number;
}

const getRoiViewWindow = (
  roi: ApiFamilyRegionOfInterest,
  chromSize?: number,
): { start: number; end: number } => {
  const span = Math.max(roi.end - roi.start, 1);
  const padding = Math.max(Math.round(span * 0.1), 5_000);
  const start = Math.max(0, roi.start - padding);
  const unclampedEnd = Math.max(roi.end + padding, start + 1);
  const end = chromSize ? Math.min(chromSize, unclampedEnd) : unclampedEnd;
  return { start, end: Math.max(end, start + 1) };
};

const clampRegionWindow = (
  start: number,
  end: number,
  chromSize: number,
): { start: number; end: number } => {
  if (chromSize <= 1) {
    return { start: 0, end: chromSize };
  }

  const targetSpan = Math.max(Math.round(end - start), 1);
  if (targetSpan >= chromSize) {
    return { start: 0, end: chromSize };
  }

  let nextStart = Math.round(start);
  let nextEnd = nextStart + targetSpan;

  if (nextStart < 0) {
    nextStart = 0;
    nextEnd = targetSpan;
  }

  if (nextEnd > chromSize) {
    nextEnd = chromSize;
    nextStart = chromSize - targetSpan;
  }

  return { start: nextStart, end: Math.max(nextEnd, nextStart + 1) };
};

const TRACK_LABELS: Record<ChromosomeTrackKey, string> = {
  coverage: 'Coverage',
  apcad: 'APCAD',
  variants: 'SVs',
  smallVariants: 'Small variants',
  haplotypes: 'Haplotypes',
  repeatExpansions: 'Repeat expansions',
};

const ChromosomeViewPage: React.FC = () => {
  const { familyId, chrom: chromParam } = useParams<{ familyId: string; chrom: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const { data, isLoading } = useQuery<
    Pick<ApiFamilyRecord, 'family_id' | 'members' | 'projects' | 'roi'>
  >({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const response = await api.get(`/families/${familyId}`);
      return response.data as Pick<ApiFamilyRecord, 'family_id' | 'members' | 'projects' | 'roi'>;
    },
  });

  const orderedMembers = useMemo(
    () => sortFamilyMembersProbandFirst(data?.members || []),
    [data?.members],
  );

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [trackVisibility, setTrackVisibility] = useState<ChromosomeTrackVisibility>({
    coverage: true,
    apcad: true,
    variants: true,
    smallVariants: true,
    haplotypes: true,
    repeatExpansions: true,
  });
  const chrom = chromParam || '1';
  const [region, setRegion] = useState<{ start: number; end: number }>({ start: 0, end: 0 });
  const [trackAreaRef, trackAreaWidth] = useMeasuredWidth<HTMLElement>();

  const trackWidth = useMemo(() => {
    if (trackAreaWidth <= 0) return DEFAULT_TRACK_WIDTH;
    return Math.max(Math.round(trackAreaWidth - TRACK_WIDTH_PADDING), DEFAULT_TRACK_WIDTH);
  }, [trackAreaWidth]);

  const win = useMemo(() => getChromosomeWindow(), []);
  const regionSpan = Math.max(region.end - region.start, 1);
  const detailWindow = useMemo(
    () => getAdaptiveTrackWindow(regionSpan, trackWidth, win),
    [regionSpan, trackWidth, win],
  );
  const binLimit = useMemo(() => getTrackBinLimit(trackWidth), [trackWidth]);
  const segmentLimit = useMemo(() => getTrackSegmentLimit(trackWidth), [trackWidth]);

  const sampleFilterMap = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return parseExplicitSampleFilterMap(params);
  }, [location.search]);
  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const sampleFilters = useMemo(() => searchParams.getAll('sample'), [searchParams]);

  const variantFilters = useMemo(() => {
    const params = new URLSearchParams(location.search);
    params.delete('start');
    params.delete('end');
    params.delete('chr');
    params.delete('chrom');
    params.delete('chromosome');
    params.delete('project_id');
    params.delete('sample');
    params.delete('sample_filter');
    const filters: Record<string, string> = {};
    params.forEach((value, key) => {
      filters[key] = value;
    });
    return filters;
  }, [location.search]);

  const projectIdParam = new URLSearchParams(location.search).get('project_id') || undefined;
  const {
    speciesName,
    assemblyName,
    assemblyVersion,
    assemblyId,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
  } = useFamilyReference(
    data?.projects as string[] | undefined,
    projectIdParam,
  );
  const resolvedSearch = useMemo(() => {
    const params = new URLSearchParams(location.search);
    params.delete('project_id');
    if (resolvedProjectId) {
      params.set('project_id', resolvedProjectId);
    }
    return params.toString();
  }, [location.search, resolvedProjectId]);
  const backSearch = useMemo(() => {
    const params = new URLSearchParams(resolvedSearch);
    params.delete('start');
    params.delete('end');
    return params.toString();
  }, [resolvedSearch]);

  const backDest = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const origin = params.get('origin');
    const suffix = backSearch ? `?${backSearch}` : '';
    if (origin === 'small') {
      return `/families/${familyId}/small-variants${suffix}`;
    }
    return `/families/${familyId}/structural-variants${suffix}`;
  }, [backSearch, familyId, location.search]);

  const genomeViewHref = useMemo(
    () => `/families/${familyId}/genome${backSearch ? `?${backSearch}` : ''}`,
    [backSearch, familyId],
  );

  const igvHref = useMemo(() => {
    const chrLabel = chrom.startsWith('chr') ? chrom : `chr${chrom}`;
    const start1 = Math.max(1, region.start);
    const end1 = Math.max(start1, region.end || start1 + 1);
    const locus = `${chrLabel}:${start1}-${end1}`;
    const base = `/families/${familyId}/igv?locus=${encodeURIComponent(locus)}`;
    const backPath = `${location.pathname}${resolvedSearch ? `?${resolvedSearch}` : ''}`;
    const withProject = resolvedProjectId ? `${base}&project_id=${resolvedProjectId}` : base;
    return `${withProject}&back_path=${encodeURIComponent(backPath)}`;
  }, [chrom, familyId, location.pathname, region.end, region.start, resolvedProjectId, resolvedSearch]);

  const { data: chromInfo, isLoading: chromInfoLoading } = useQuery<ChromInfo>({
    queryKey: ['chrom-info', assemblyName, chrom],
    queryFn: async () => {
      const response = await api.get(`/chromosomes/${assemblyName}/${chrom}`);
      return response.data as ChromInfo;
    },
    enabled: Boolean(assemblyName),
  });

  useEffect(() => {
    if (!chromInfo) return;

    const params = new URLSearchParams(location.search);
    const startParam = params.get('start');
    const endParam = params.get('end');

    let nextStart = 0;
    let nextEnd = chromInfo.size;

    if (startParam || endParam) {
      const parsedStart = startParam ? Number(startParam) : 0;
      const parsedEnd = endParam ? Number(endParam) : 0;
      nextStart = Number.isFinite(parsedStart) ? Math.max(0, parsedStart) : 0;
      nextEnd = Number.isFinite(parsedEnd) ? parsedEnd : 0;
      nextEnd = Math.min(chromInfo.size, nextEnd === 0 ? chromInfo.size : nextEnd);
      if (nextEnd < nextStart) nextEnd = nextStart;
    }

    setRegion((current) =>
      current.start === nextStart && current.end === nextEnd
        ? current
        : { start: nextStart, end: nextEnd },
    );
  }, [chromInfo, location.search]);

  useEffect(() => {
    if (!orderedMembers.length) return;
    const initial: Record<string, boolean> = {};
    orderedMembers.forEach((member) => {
      initial[member.sample_id] =
        sampleFilters.length === 0 || sampleFilters.includes(member.sample_id);
    });
    setSelected(initial);
  }, [orderedMembers, sampleFilters]);

  const availabilitySearch = useMemo(() => {
    const params = new URLSearchParams({
      chrom,
      start: String(region.start),
      end: String(region.end),
      include_small_variants: 'true',
    });
    if (resolvedProjectId) params.set('project_id', resolvedProjectId);
    Object.entries(variantFilters).forEach(([key, value]) => params.append(key, value));
    Object.values(sampleFilterMap).forEach((entry) => params.append('sample_filter', entry));
    return params.toString();
  }, [chrom, resolvedProjectId, region.end, region.start, sampleFilterMap, variantFilters]);

  const {
    data: availabilityData,
    isLoading: availabilityLoading,
    isFetching: availabilityFetching,
  } = useQuery<ApiTrackAvailabilityResponse<ApiChromosomeTrackAvailability>>({
    queryKey: ['family', familyId, 'track-availability', availabilitySearch],
    queryFn: async () => {
      const response = await api.get(`/families/${familyId}/track-availability?${availabilitySearch}`);
      return response.data as ApiTrackAvailabilityResponse<ApiChromosomeTrackAvailability>;
    },
    enabled: !!familyId && !!data && region.end > region.start,
  });

  const availability = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(availabilityData?.samples || {}).map(([sampleId, entry]) => [
          sampleId,
          {
            coverage: entry.coverage,
            apcad: entry.apcad,
            variants: entry.variants,
            smallVariants: entry.small_variants,
            haplotypes: entry.haplotypes,
            repeatExpansions: entry.repeat_expansions,
          },
        ]),
      ) as Record<
        string,
        ApiChromosomeTrackAvailability & { smallVariants: boolean; repeatExpansions: boolean }
      >,
    [availabilityData],
  );

  const availableTracks = useMemo(() => {
    const tracks = new Set<ChromosomeTrackKey>();
    orderedMembers.forEach((member) => {
      const entry = availability[member.sample_id];
      if (!entry) return;
      if (entry.coverage) tracks.add('coverage');
      if (entry.apcad) tracks.add('apcad');
      if (entry.variants) tracks.add('variants');
      if (entry.smallVariants) tracks.add('smallVariants');
      if (entry.haplotypes) tracks.add('haplotypes');
      if (entry.repeatExpansions) tracks.add('repeatExpansions');
    });
    return Array.from(tracks);
  }, [availability, orderedMembers]);

  const navigateToChromosomeRegion = (
    nextChrom: string,
    nextRegion?: { start: number; end: number },
    replace = false,
  ) => {
    if (!familyId) return;
    const params = new URLSearchParams(resolvedSearch);
    if (nextRegion) {
      params.set('start', String(nextRegion.start));
      params.set('end', String(nextRegion.end));
    } else {
      params.delete('start');
      params.delete('end');
    }
    const search = params.toString();
    navigate(
      {
        pathname: `/families/${familyId}/chromosome/${nextChrom}`,
        search: search ? `?${search}` : '',
      },
      { replace },
    );
  };

  const setClampedRegion = (start: number, end: number) => {
    if (!chromInfo?.size) return;
    const nextRegion = clampRegionWindow(start, end, chromInfo.size);
    setRegion(nextRegion);
    navigateToChromosomeRegion(chrom, nextRegion, true);
  };

  const handlePan = (direction: -1 | 1) => {
    if (!chromInfo?.size || region.end <= region.start) return;
    const span = Math.max(region.end - region.start, 1);
    const shift = Math.max(Math.round(span * 0.25), 1);
    setClampedRegion(region.start + direction * shift, region.end + direction * shift);
  };

  const handleZoom = (factor: number) => {
    if (!chromInfo?.size || region.end <= region.start) return;
    const span = Math.max(region.end - region.start, 1);
    const targetSpan = Math.max(Math.round(span * factor), 1);
    const center = region.start + span / 2;
    setClampedRegion(center - targetSpan / 2, center + targetSpan / 2);
  };

  const visibleRoi = useMemo(() => {
    if (!data?.roi) return null;
    if (data.roi.assembly_id && assemblyId && data.roi.assembly_id !== assemblyId) {
      return null;
    }
    return data.roi;
  }, [assemblyId, data?.roi]);

  const chromosomeRoiRange = useMemo(() => {
    if (!visibleRoi || !chromInfo?.size) return null;
    if (normalizeChrom(visibleRoi.chr) !== normalizeChrom(chrom)) return null;
    return {
      startX: (visibleRoi.start / chromInfo.size) * trackWidth,
      endX: (visibleRoi.end / chromInfo.size) * trackWidth,
    };
  }, [chrom, chromInfo?.size, trackWidth, visibleRoi]);

  const regionRoiRange = useMemo(() => {
    if (!visibleRoi || region.end <= region.start) return null;
    if (normalizeChrom(visibleRoi.chr) !== normalizeChrom(chrom)) return null;
    const clippedStart = Math.max(visibleRoi.start, region.start);
    const clippedEnd = Math.min(visibleRoi.end, region.end);
    if (clippedEnd < region.start || clippedStart > region.end || clippedEnd < clippedStart) {
      return null;
    }
    return {
      startX: ((clippedStart - region.start) / (region.end - region.start)) * trackWidth,
      endX: ((clippedEnd - region.start) / (region.end - region.start)) * trackWidth,
    };
  }, [chrom, region.end, region.start, trackWidth, visibleRoi]);

  const handleRoiZoom = () => {
    if (!visibleRoi || !familyId) return;
    const roiChrom = normalizeChrom(visibleRoi.chr);
    const { start, end } = getRoiViewWindow(
      visibleRoi,
      roiChrom === normalizeChrom(chrom) ? chromInfo?.size : undefined,
    );
    setRegion({ start, end });
    navigateToChromosomeRegion(roiChrom, { start, end });
  };

  if (isLoading || (data?.projects?.length && referenceLoading)) {
    return (
      <PageState
        kicker="Visualization"
        title="Loading chromosome view"
        message="Preparing the chromosome workspace, tracks and filters."
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Visualization"
        title="Family not found"
        message="This chromosome view could not resolve the requested family."
      />
    );
  }

  if (!assemblyName) {
    return (
      <PageState
        kicker="Visualization"
        title="Reference not linked"
        message="This chromosome view requires a family-linked project assembly."
      />
    );
  }

  const selectedInitialized = orderedMembers.every((member) =>
    Object.prototype.hasOwnProperty.call(selected, member.sample_id),
  );
  const visibleMembers = orderedMembers.filter((member) => selected[member.sample_id]);
  const membersWithData = visibleMembers.filter((member) => {
    const entry = availability[member.sample_id];
    return (
      entry?.coverage ||
      entry?.apcad ||
      entry?.variants ||
      entry?.smallVariants ||
      entry?.haplotypes ||
      entry?.repeatExpansions
    );
  });
  const availabilityPending =
    visibleMembers.length > 0 &&
    (chromInfoLoading ||
      region.end <= region.start ||
      availabilityLoading ||
      (!availabilityData && availabilityFetching));
  const showViewerLoading = !selectedInitialized || availabilityPending;

  return (
    <div className="page-shell analysis-grid analysis-grid--viewer">
      <ChromosomeViewSidebar
        members={orderedMembers}
        selected={selected}
        availableTracks={availableTracks}
        trackVisibility={trackVisibility}
        trackLabels={TRACK_LABELS}
        onToggleSample={(sampleId) =>
          setSelected((current) => ({ ...current, [sampleId]: !current[sampleId] }))
        }
        onToggleTrack={(track) =>
          setTrackVisibility((current) => ({ ...current, [track]: !current[track] }))
        }
      />
      <ChromosomeViewWorkspace
        trackAreaRef={trackAreaRef}
        familyId={familyId || data.family_id}
        familyDisplayId={data.family_id}
        chrom={chrom}
        speciesName={speciesName}
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        assembly={assemblyName}
        assemblyId={assemblyId}
        projectId={resolvedProjectId}
        region={region}
        trackWidth={trackWidth}
        backDest={backDest}
        genomeViewHref={genomeViewHref}
        igvHref={igvHref}
        chromInfoSize={chromInfo?.size}
        visibleRoi={visibleRoi}
        chromosomeRoiRange={chromosomeRoiRange}
        regionRoiRange={regionRoiRange}
        onChromChange={(nextChrom) => {
          navigateToChromosomeRegion(nextChrom);
          setRegion({ start: 0, end: 0 });
        }}
        onRegionStartChange={(value) => {
          const nextRegion = { start: value, end: region.end };
          setRegion(nextRegion);
          navigateToChromosomeRegion(chrom, nextRegion, true);
        }}
        onRegionEndChange={(value) => {
          const nextRegion = { start: region.start, end: value };
          setRegion(nextRegion);
          navigateToChromosomeRegion(chrom, nextRegion, true);
        }}
        onResetRange={() => {
          if (!chromInfo) return;
          setRegion({ start: 0, end: chromInfo.size });
          navigateToChromosomeRegion(chrom, undefined, true);
        }}
        onPan={handlePan}
        onZoom={handleZoom}
        onRegionSelect={(start, end) => {
          const nextRegion = { start, end };
          setRegion(nextRegion);
          navigateToChromosomeRegion(chrom, nextRegion, true);
        }}
        onRoiZoom={handleRoiZoom}
        onJumpToRegion={(nextChrom, nextRegion) => {
          setRegion(nextRegion);
          navigateToChromosomeRegion(nextChrom, nextRegion);
        }}
        visibleMembers={visibleMembers}
        membersWithData={membersWithData}
        availability={availability}
        trackVisibility={trackVisibility}
        variantFilters={variantFilters}
        sampleFilterMap={sampleFilterMap}
        detailWindow={detailWindow}
        binLimit={binLimit}
        segmentLimit={segmentLimit}
        showViewerLoading={showViewerLoading}
      />
    </div>
  );
};

export default ChromosomeViewPage;
