import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiFamilyRecord,
  ApiGenomeTrackAvailability,
  ApiTrackAvailabilityResponse,
} from '../../lib/apiTypes';
import PageState from '../../components/PageState';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { getGenomeWindow } from '../../lib/settings';
import { parseExplicitSampleFilterMap } from '../../lib/sampleFilterState';
import {
  getAdaptiveTrackWindow,
  getTrackBinLimit,
  getTrackSegmentLimit,
} from '../../lib/trackSampling';
import { useFamilyReference } from '../../lib/reference';
import { useMeasuredWidth } from '../../lib/useMeasuredWidth';
import GenomeOverviewSidebar, {
  type GenomeTrackKey,
  type GenomeTrackVisibility,
} from './GenomeOverviewSidebar';
import GenomeOverviewWorkspace from './GenomeOverviewWorkspace';
import {
  CHROMS,
  DEFAULT_TRACK_WIDTH,
  TRACK_WIDTH_PADDING,
  normalizeChrom,
} from './viewerShared';

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
  chroms: string[];
}

const GenomeOverviewPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
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
  const [trackVisibility, setTrackVisibility] = useState<GenomeTrackVisibility>({
    coverage: true,
    segments: true,
    apcad: true,
    sv: true,
    haplotypes: true,
    repeatExpansions: true,
  });
  const [layout, setLayout] = useState<Layout | null>(null);
  const [chromSelected, setChromSelected] = useState<Record<string, boolean>>(() => {
    const params = new URLSearchParams(location.search);
    const chromParams = params.getAll('chrom');
    if (chromParams.length === 0) {
      return CHROMS.reduce((acc, chrom) => ({ ...acc, [chrom]: true }), {} as Record<string, boolean>);
    }
    const selectedChroms = new Set(chromParams.map((chrom) => chrom.replace(/^chr/i, '')));
    return CHROMS.reduce(
      (acc, chrom) => ({ ...acc, [chrom]: selectedChroms.has(chrom) }),
      {} as Record<string, boolean>,
    );
  });

  const chroms = useMemo(() => CHROMS.filter((chrom) => chromSelected[chrom]), [chromSelected]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const chromParams = params.getAll('chrom');
    if (chromParams.length === 0) {
      setChromSelected(
        CHROMS.reduce((acc, chrom) => ({ ...acc, [chrom]: true }), {} as Record<string, boolean>),
      );
      return;
    }

    const next = new Set(chromParams.map((chrom) => chrom.replace(/^chr/i, '')));
    setChromSelected(
      CHROMS.reduce(
        (acc, chrom) => ({ ...acc, [chrom]: next.has(chrom) }),
        {} as Record<string, boolean>,
      ),
    );
  }, [location.search]);

  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const sampleFilters = useMemo(() => searchParams.getAll('sample'), [searchParams]);
  const baseVariantParams = useMemo(() => {
    const source = new URLSearchParams(location.search);
    const allowed = [
      'type',
      'source',
      'qual',
      'read_support',
      'filter',
      'length',
      'min_length',
      'remote_chr',
      'remote_start',
      'panel_id',
    ];
    const params = new URLSearchParams();
    allowed.forEach((key) => {
      const value = source.get(key);
      if (value) params.set(key, value);
    });
    return params;
  }, [location.search]);

  const variantFilters = useMemo(() => {
    const filters: Record<string, string> = {};
    baseVariantParams.forEach((value, key) => {
      filters[key] = value;
    });
    return filters;
  }, [baseVariantParams]);

  const sampleFilterMap = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return parseExplicitSampleFilterMap(params);
  }, [location.search]);

  const [trackAreaRef, trackAreaWidth] = useMeasuredWidth<HTMLElement>();
  const trackWidth = useMemo(() => {
    if (trackAreaWidth <= 0) return DEFAULT_TRACK_WIDTH;
    return Math.max(Math.round(trackAreaWidth - TRACK_WIDTH_PADDING), DEFAULT_TRACK_WIDTH);
  }, [trackAreaWidth]);

  const trackHeight = 120;
  const svTrackHeight = 80;
  const chromGapPx = 8;
  const win = useMemo(() => getGenomeWindow(), []);
  const binLimit = useMemo(() => getTrackBinLimit(trackWidth), [trackWidth]);
  const segmentLimit = useMemo(() => getTrackSegmentLimit(trackWidth), [trackWidth]);
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
    params.delete('sample');
    params.delete('chrom');
    return params.toString();
  }, [resolvedSearch]);

  const { data: chromSizes, isLoading: chromSizesLoading } = useQuery<Record<string, number>>({
    queryKey: ['chromosome-sizes', assemblyName],
    queryFn: async () => {
      const response = await api.get(`/chromosomes/${assemblyName}`);
      const lengths: Record<string, number> = {};
      (response.data as Array<{ chr: string; size: number }>).forEach((entry) => {
        lengths[normalizeChrom(entry.chr)] = entry.size;
      });
      return lengths;
    },
    enabled: Boolean(assemblyName),
    staleTime: Infinity,
  });

  const genomeTrackWindow = useMemo(() => {
    const span = layout?.total ?? chroms.reduce((sum, chrom) => sum + (chromSizes?.[chrom] ?? 0), 0);
    return getAdaptiveTrackWindow(span, trackWidth, win);
  }, [chromSizes, chroms, layout?.total, trackWidth, win]);

  useEffect(() => {
    if (!chromSizes) return;
    const lengths: Record<string, number> = {};
    const gapCount = chroms.length - 1;
    const totalNoGap = chroms.reduce((sum, chrom) => {
      const length = chromSizes[chrom] || 0;
      lengths[chrom] = length;
      return sum + length;
    }, 0);
    const bpPerPx = totalNoGap / (trackWidth - gapCount * chromGapPx);
    const gapBp = bpPerPx * chromGapPx;
    const offsets: Record<string, number> = {};
    let offset = 0;
    chroms.forEach((chrom, index) => {
      offsets[chrom] = offset;
      offset += lengths[chrom];
      if (index < gapCount) offset += gapBp;
    });
    setLayout({ offsets, lengths, total: offset, chroms });
  }, [chromGapPx, chromSizes, chroms, trackWidth]);

  const haplotypeUrls = useMemo(() => {
    const params = new URLSearchParams();
    chroms.forEach((chrom) => params.append('chr', chrom));
    return [`${api.defaults.baseURL}/families/${familyId}/haplotypes/batch?${params.toString()}`];
  }, [chroms, familyId]);

  const urlMaps = useMemo(() => {
    if (!data) {
      return { coverage: {}, segments: {}, apcad: {}, haplotypes: {} };
    }

    const buildBatchBedUrl = (
      sampleId: string,
      bedType: 'coverage' | 'segments' | 'apcad',
      extra: Record<string, string>,
    ) => {
      const params = new URLSearchParams();
      chroms.forEach((chrom) => params.append('chrom', chrom));
      params.set('format', 'json');
      Object.entries(extra).forEach(([key, value]) => params.set(key, value));
      return `${api.defaults.baseURL}/bed/${sampleId}/${bedType}/batch?${params.toString()}`;
    };

    const coverage: Record<string, string[]> = {};
    const segments: Record<string, string[]> = {};
    const apcad: Record<string, string[]> = {};
    const haplotypes: Record<string, string[]> = {};

    orderedMembers.forEach((member) => {
      coverage[member.sample_id] = [
        buildBatchBedUrl(member.sample_id, 'coverage', {
          window: String(genomeTrackWindow),
          limit: String(binLimit),
        }),
      ];
      segments[member.sample_id] = [
        buildBatchBedUrl(member.sample_id, 'segments', { limit: String(segmentLimit) }),
      ];
      apcad[member.sample_id] = [
        buildBatchBedUrl(member.sample_id, 'apcad', {
          window: String(genomeTrackWindow),
          limit: String(binLimit),
        }),
      ];
      haplotypes[member.sample_id] = haplotypeUrls;
    });

    return { coverage, segments, apcad, haplotypes };
  }, [binLimit, chroms, data, genomeTrackWindow, haplotypeUrls, orderedMembers, segmentLimit]);

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
    const params = new URLSearchParams({ include_small_variants: 'false' });
    chroms.forEach((chrom) => params.append('chrom', chrom));
    if (resolvedProjectId) params.set('project_id', resolvedProjectId);
    Object.entries(variantFilters).forEach(([key, value]) => params.append(key, value));
    Object.values(sampleFilterMap).forEach((entry) => params.append('sample_filter', entry));
    return params.toString();
  }, [chroms, resolvedProjectId, sampleFilterMap, variantFilters]);

  const {
    data: availabilityData,
    isLoading: availabilityLoading,
    isFetching: availabilityFetching,
  } = useQuery<ApiTrackAvailabilityResponse<ApiGenomeTrackAvailability>>({
    queryKey: ['family', familyId, 'track-availability', availabilitySearch],
    queryFn: async () => {
      const response = await api.get(`/families/${familyId}/track-availability?${availabilitySearch}`);
      return response.data as ApiTrackAvailabilityResponse<ApiGenomeTrackAvailability>;
    },
    enabled: !!familyId && !!data,
  });

  const availability = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(availabilityData?.samples || {}).map(([sampleId, entry]) => [
          sampleId,
          {
            coverage: entry.coverage,
            segments: entry.segments,
            apcad: entry.apcad,
            haplotypes: entry.haplotypes,
            sv: entry.variants,
            repeatExpansions: entry.repeat_expansions,
          },
        ]),
      ) as Record<
        string,
        {
          coverage: boolean;
          segments: boolean;
          apcad: boolean;
          haplotypes: boolean;
          sv: boolean;
          repeatExpansions: boolean;
        }
      >,
    [availabilityData],
  );

  const availableTracks = useMemo(() => {
    const tracks = new Set<GenomeTrackKey>();
    orderedMembers.forEach((member) => {
      const entry = availability[member.sample_id];
      if (!entry) return;
      if (entry.coverage) tracks.add('coverage');
      if (entry.coverage && entry.segments) tracks.add('segments');
      if (entry.apcad) tracks.add('apcad');
      if (entry.sv) tracks.add('sv');
      if (entry.haplotypes) tracks.add('haplotypes');
      if (entry.repeatExpansions) tracks.add('repeatExpansions');
    });
    return Array.from(tracks);
  }, [availability, orderedMembers]);

  const visibleRoi = useMemo(() => {
    if (!data?.roi) return null;
    if (data.roi.assembly_id && assemblyId && data.roi.assembly_id !== assemblyId) {
      return null;
    }
    return data.roi;
  }, [assemblyId, data?.roi]);

  const genomeRoiRange = useMemo(() => {
    if (!visibleRoi || !layout) return null;
    const roiChrom = normalizeChrom(visibleRoi.chr);
    const offset = layout.offsets[roiChrom];
    if (offset === undefined) return null;
    return {
      startX: ((offset + visibleRoi.start) / layout.total) * trackWidth,
      endX: ((offset + visibleRoi.end) / layout.total) * trackWidth,
    };
  }, [layout, trackWidth, visibleRoi]);

  if (isLoading || (data?.projects?.length && referenceLoading)) {
    return (
      <PageState
        kicker="Visualization"
        title="Loading genome overview"
        message="Preparing genome-wide tracks and family context."
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Visualization"
        title="Family not found"
        message="This genome overview could not resolve the requested family."
      />
    );
  }

  if (!assemblyName) {
    return (
      <PageState
        kicker="Visualization"
        title="Reference not linked"
        message="This genome overview requires a family-linked project assembly."
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
      entry?.segments ||
      entry?.apcad ||
      entry?.haplotypes ||
      entry?.sv ||
      entry?.repeatExpansions
    );
  });
  const availabilityPending =
    visibleMembers.length > 0 &&
    chroms.length > 0 &&
    (chromSizesLoading || !layout || availabilityLoading || (!availabilityData && availabilityFetching));
  const showViewerLoading = !selectedInitialized || availabilityPending;

  return (
    <div className="page-shell analysis-grid analysis-grid--viewer">
      <GenomeOverviewSidebar
        members={orderedMembers}
        selected={selected}
        availableTracks={availableTracks}
        trackVisibility={trackVisibility}
        chromSelected={chromSelected}
        onToggleSample={(sampleId) =>
          setSelected((current) => ({ ...current, [sampleId]: !current[sampleId] }))
        }
        onToggleTrack={(track) =>
          setTrackVisibility((current) => ({ ...current, [track]: !current[track] }))
        }
        onToggleChrom={(chrom) =>
          setChromSelected((current) => ({ ...current, [chrom]: !current[chrom] }))
        }
        onSelectAllChroms={() =>
          setChromSelected(
            CHROMS.reduce(
              (acc, chrom) => ({ ...acc, [chrom]: true }),
              {} as Record<string, boolean>,
            ),
          )
        }
        onDeselectAllChroms={() =>
          setChromSelected(
            CHROMS.reduce(
              (acc, chrom) => ({ ...acc, [chrom]: false }),
              {} as Record<string, boolean>,
            ),
          )
        }
      />
      <GenomeOverviewWorkspace
        trackAreaRef={trackAreaRef}
        familyId={familyId || data.family_id}
        familyDisplayId={data.family_id}
        speciesName={speciesName}
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        assembly={assemblyName}
        projectId={resolvedProjectId}
        backDest={`/families/${familyId}/structural-variants${backSearch ? `?${backSearch}` : ''}`}
        visibleRoi={visibleRoi}
        genomeRoiRange={genomeRoiRange}
        navigateToChromosome={(chrom, region) => {
          const params = new URLSearchParams(resolvedSearch);
          if (region) {
            params.set('start', String(region.start));
            params.set('end', String(region.end));
          } else {
            params.delete('start');
            params.delete('end');
          }
          const search = params.toString();
          navigate(`/families/${familyId}/chromosome/${chrom}${search ? `?${search}` : ''}`);
        }}
        visibleMembers={visibleMembers}
        membersWithData={membersWithData}
        trackVisibility={trackVisibility}
        availability={availability}
        variantFilters={variantFilters}
        sampleFilterMap={sampleFilterMap}
        baseVariantParams={baseVariantParams}
        urlMaps={urlMaps}
        layout={layout}
        trackWidth={trackWidth}
        trackHeight={trackHeight}
        svTrackHeight={svTrackHeight}
        showViewerLoading={showViewerLoading}
      />
    </div>
  );
};

export default GenomeOverviewPage;
