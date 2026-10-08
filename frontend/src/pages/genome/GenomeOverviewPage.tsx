import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiFamilyRecord, ApiGenomeTrackAvailability, ApiTrackAvailabilityResponse } from '../../lib/apiTypes';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { getGenomeWindow } from '../../lib/settings';
import { parseExplicitSampleFilterMap } from '../../lib/sampleFilterState';
import {
  getAdaptiveTrackWindow,
  getApcadPointLimit,
  getTrackBinLimit,
  getTrackSegmentLimit,
} from '../../lib/trackSampling';
import { useFamilyReference } from '../../lib/reference';
import GenomeOverviewSidebar, { type GenomeTrackKey, type GenomeTrackVisibility } from './GenomeOverviewSidebar';
import GenomeOverviewWorkspace, { type Layout } from './GenomeOverviewWorkspace';
import { normalizeChrom } from '../../lib/chromosomes';
import {
  CHROMS,
  DEFAULT_TRACK_WIDTH,
  roiOnAssembly,
  searchWithResolvedProject,
  useTrackWidth,
} from './viewerShared';
import { apiPath, raw } from '../../lib/apiPath';

// Inter-chromosome gap in px. Module-level so it is referentially stable and
// does not need to appear in render memo dependency arrays.
const CHROM_GAP_PX = 8;

/** Every chromosome, each set to `selected`. */
const allChroms = (selected: boolean): Record<string, boolean> =>
  CHROMS.reduce((acc, chrom) => ({ ...acc, [chrom]: selected }), {} as Record<string, boolean>);

/** The chromosomes the page's `?chrom=` list selects: all of them when it names none. */
const chromSelectionFromSearch = (search: string): Record<string, boolean> => {
  const chromParams = new URLSearchParams(search).getAll('chrom');
  if (chromParams.length === 0) {
    return allChroms(true);
  }
  const selectedChroms = new Set(chromParams.map(normalizeChrom));
  return CHROMS.reduce(
    (acc, chrom) => ({ ...acc, [chrom]: selectedChroms.has(chrom) }),
    {} as Record<string, boolean>,
  );
};

const GenomeOverviewPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const { data, isLoading, isError, error, refetch } = useQuery<
    Pick<ApiFamilyRecord, 'family_id' | 'members' | 'projects' | 'roi' | 'metadata'>
  >({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}`);
      return response.data as Pick<ApiFamilyRecord, 'family_id' | 'members' | 'projects' | 'roi' | 'metadata'>;
    },
  });

  const orderedMembers = useMemo(() => sortFamilyMembersProbandFirst(data?.members || []), [data?.members]);

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
  const [chromSelected, setChromSelected] = useState<Record<string, boolean>>(() =>
    chromSelectionFromSearch(location.search),
  );

  const chroms = useMemo(() => CHROMS.filter((chrom) => chromSelected[chrom]), [chromSelected]);

  useEffect(() => {
    setChromSelected(chromSelectionFromSearch(location.search));
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

  const [trackAreaRef, trackWidth] = useTrackWidth();

  const trackHeight = 120;
  const svTrackHeight = 80;
  const win = useMemo(() => getGenomeWindow(), []);
  // Quantise the width that feeds the per-track row limits to coarse (100px) steps,
  // so small ResizeObserver jitter — and the initial 0 -> measured settle — does not
  // change the fetch URLs and re-fire every per-sample track request. `trackWidth`
  // itself stays precise for canvas rendering.
  const limitWidth = useMemo(
    () => Math.max(Math.round(trackWidth / 100) * 100, DEFAULT_TRACK_WIDTH),
    [trackWidth],
  );
  const binLimit = useMemo(() => getTrackBinLimit(limitWidth), [limitWidth]);
  const apcadPointLimit = useMemo(() => getApcadPointLimit(limitWidth), [limitWidth]);
  const segmentLimit = useMemo(() => getTrackSegmentLimit(limitWidth), [limitWidth]);
  const projectIdParam = new URLSearchParams(location.search).get('project_id') || undefined;

  const {
    speciesName,
    assemblyName,
    assemblyVersion,
    assemblyId,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(data?.projects as string[] | undefined, projectIdParam);
  const resolvedSearch = useMemo(
    () => searchWithResolvedProject(location.search, resolvedProjectId),
    [location.search, resolvedProjectId],
  );
  const backSearch = useMemo(() => {
    const params = new URLSearchParams(resolvedSearch);
    params.delete('sample');
    params.delete('chrom');
    return params.toString();
  }, [resolvedSearch]);

  const {
    data: chromSizes,
    isLoading: chromSizesLoading,
    isError: chromSizesFailed,
    error: chromSizesError,
    refetch: refetchChromSizes,
  } = useQuery<Record<string, number>>({
    queryKey: ['chromosome-sizes', assemblyName],
    queryFn: async () => {
      const response = await api.get(apiPath`/chromosomes/${assemblyName}`);
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
    const bpPerPx = totalNoGap / (trackWidth - gapCount * CHROM_GAP_PX);
    const gapBp = bpPerPx * CHROM_GAP_PX;
    const offsets: Record<string, number> = {};
    let offset = 0;
    chroms.forEach((chrom, index) => {
      offsets[chrom] = offset;
      offset += lengths[chrom];
      if (index < gapCount) offset += gapBp;
    });
    setLayout({ offsets, lengths, total: offset, chroms });
  }, [chromSizes, chroms, trackWidth]);

  const haplotypeUrls = useMemo(() => {
    const params = new URLSearchParams();
    chroms.forEach((chrom) => params.append('chr', chrom));
    return [`${api.defaults.baseURL}/families/${encodeURIComponent(familyId ?? '')}/haplotypes/batch?${params.toString()}`];
  }, [chroms, familyId]);

  const urlMaps = useMemo(() => {
    if (!data) {
      return {
        coverageTrackUrls: () => ({ coverageUrls: [], segmentsUrls: [] }),
        apcad: {},
        apcadPcf: {},
        haplotypes: {},
        sv: {},
      };
    }

    const buildBatchBedUrl = (
      sampleId: string,
      bedType: 'coverage' | 'segments' | 'apcad' | 'apcad_pcf',
      extra: Record<string, string>,
    ) => {
      const params = new URLSearchParams();
      chroms.forEach((chrom) => params.append('chrom', chrom));
      params.set('format', 'json');
      Object.entries(extra).forEach(([key, value]) => params.set(key, value));
      return `${api.defaults.baseURL}/bed/${encodeURIComponent(sampleId)}/${encodeURIComponent(bedType)}/batch?${params.toString()}`;
    };

    const apcad: Record<string, string[]> = {};
    const apcadPcf: Record<string, string[]> = {};
    const haplotypes: Record<string, string[]> = {};
    const sv: Record<string, string> = {};

    orderedMembers.forEach((member) => {

      apcad[member.sample_id] = [
        buildBatchBedUrl(member.sample_id, 'apcad', {
          window: String(genomeTrackWindow),
          limit: String(apcadPointLimit),
        }),
      ];
      apcadPcf[member.sample_id] = [
        buildBatchBedUrl(member.sample_id, 'apcad_pcf', {
          limit: String(segmentLimit),
        }),
      ];
      haplotypes[member.sample_id] = haplotypeUrls;

      const svParams = new URLSearchParams(baseVariantParams);
      svParams.set('page_size', '0');
      svParams.set('track_mode', 'true');
      svParams.append('sample', member.sample_id);
      const svSampleFilter = sampleFilterMap[member.sample_id];
      if (svSampleFilter) svParams.append('sample_filter', svSampleFilter);
      sv[member.sample_id] =
        `${api.defaults.baseURL}/families/${encodeURIComponent(familyId ?? '')}/structural-variants?${svParams.toString()}`;
    });

    // A builder rather than a prebuilt map: which callers a sample has comes from
    // the availability query, which is resolved after this memo, and threading it
    // back in would mean reordering the hooks for no gain.
    const coverageTrackUrls = (sampleId: string, source: string) => ({
      coverageUrls: [
        buildBatchBedUrl(sampleId, 'coverage', {
          window: String(genomeTrackWindow),
          limit: String(binLimit),
          source,
        }),
      ],
      segmentsUrls: [
        buildBatchBedUrl(sampleId, 'segments', { limit: String(segmentLimit), source }),
      ],
    });

    return { coverageTrackUrls, apcad, apcadPcf, haplotypes, sv };
  }, [
    apcadPointLimit,
    baseVariantParams,
    binLimit,
    chroms,
    data,
    familyId,
    genomeTrackWindow,
    haplotypeUrls,
    orderedMembers,
    sampleFilterMap,
    segmentLimit,
  ]);

  useEffect(() => {
    if (!orderedMembers.length) return;
    const initial: Record<string, boolean> = {};
    orderedMembers.forEach((member) => {
      initial[member.sample_id] = sampleFilters.length === 0 || sampleFilters.includes(member.sample_id);
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
    isError: availabilityFailed,
    error: availabilityError,
    refetch: refetchAvailability,
  } = useQuery<ApiTrackAvailabilityResponse<ApiGenomeTrackAvailability>>({
    queryKey: ['family', familyId, 'track-availability', availabilitySearch],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}/track-availability?${raw(availabilitySearch)}`);
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
            coverageSources: entry.coverage_sources ?? [],
            segmentsSources: entry.segments_sources ?? [],
            segments: entry.segments,
            apcad: entry.apcad,
            apcadSources: entry.apcad_sources ?? [],
            apcadPcf: !!entry.apcad_pcf,
            haplotypes: entry.haplotypes,
            sv: entry.variants,
            repeatExpansions: entry.repeat_expansions,
          },
        ]),
      ) as Record<
        string,
        {
          coverage: boolean;
          coverageSources: string[];
          segmentsSources: string[];
          segments: boolean;
          apcad: boolean;
          apcadSources: string[];
          apcadPcf: boolean;
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
      if (entry.apcad || entry.apcadPcf) tracks.add('apcad');
      if (entry.sv) tracks.add('sv');
      if (entry.haplotypes) tracks.add('haplotypes');
      if (entry.repeatExpansions) tracks.add('repeatExpansions');
    });
    return Array.from(tracks);
  }, [availability, orderedMembers]);

  const visibleRoi = useMemo(() => roiOnAssembly(data?.roi, assemblyId), [assemblyId, data?.roi]);
  const inheritanceModel = (data?.metadata?.pgt as { inheritance_model?: string | null } | undefined)
    ?.inheritance_model;

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

  if (isError) {
    return (
      <FamilyLoadFailure
        kicker="Visualization"
        what="Family"
        error={error}
        notFoundMessage="This genome overview could not resolve the requested family."
        onRetry={() => void refetch()}
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

  // The project catalogue failed: the reference is unknown, not missing (#608).
  if (referenceFailed) {
    return (
      <PageState
        kicker="Visualization"
        title="Reference could not be loaded"
        message="The family's project, and with it the reference assembly, could not be loaded. The genome overview needs it to draw the tracks."
        action={
          <button type="button" className="button-secondary" onClick={retryReference}>
            Retry
          </button>
        }
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
      entry?.apcadPcf ||
      entry?.haplotypes ||
      entry?.sv ||
      entry?.repeatExpansions
    );
  });
  // Without the chromosome lengths there is no layout to wait for: the failure is said in
  // place of the tracks, not as a load that never ends (#610).
  const availabilityPending =
    visibleMembers.length > 0 &&
    chroms.length > 0 &&
    !chromSizesFailed &&
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
          setSelected((current) => ({
            ...current,
            [sampleId]: !current[sampleId],
          }))
        }
        onToggleTrack={(track) =>
          setTrackVisibility((current) => ({
            ...current,
            [track]: !current[track],
          }))
        }
        onToggleChrom={(chrom) =>
          setChromSelected((current) => ({
            ...current,
            [chrom]: !current[chrom],
          }))
        }
        onSelectAllChroms={() => setChromSelected(allChroms(true))}
        onDeselectAllChroms={() => setChromSelected(allChroms(false))}
      />
      <GenomeOverviewWorkspace
        trackAreaRef={trackAreaRef}
        familyId={familyId || data.family_id}
        familyDisplayId={data.family_id}
        speciesName={speciesName}
        assemblyVersion={assemblyVersion}
        assembly={assemblyName}
        projectId={resolvedProjectId}
        backDest={`/families/${familyId}/structural-variants${backSearch ? `?${backSearch}` : ''}`}
        visibleRoi={visibleRoi}
        inheritanceModel={inheritanceModel}
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
        familyMembers={orderedMembers}
        visibleMembers={visibleMembers}
        membersWithData={membersWithData}
        trackVisibility={trackVisibility}
        availability={availability}
        variantFilters={variantFilters}
        sampleFilterMap={sampleFilterMap}
        urlMaps={urlMaps}
        layout={layout}
        trackWidth={trackWidth}
        trackHeight={trackHeight}
        svTrackHeight={svTrackHeight}
        showViewerLoading={showViewerLoading}
        tracksFailure={
          chromSizesFailed
            ? {
                what: 'the chromosome lengths',
                error: chromSizesError,
                retry: () => void refetchChromSizes(),
              }
            : availabilityFailed
              ? {
                  what: 'which tracks each sample has',
                  error: availabilityError,
                  retry: () => void refetchAvailability(),
                }
              : null
        }
      />
    </div>
  );
};

export default GenomeOverviewPage;
