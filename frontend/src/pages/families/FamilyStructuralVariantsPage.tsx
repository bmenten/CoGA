import React, { useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isReviewConflict, withReviewVersion } from '../../lib/reviewConcurrency';
import { getErrorMessage } from '../../lib/errorMessage';
import FamilyCaseHeader, { FamilyFact } from './FamilyCaseHeader';
import FilterCollapseToggle from './FilterCollapseToggle';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import QueryFailure from '../../components/QueryFailure';
import AnnotationProvenanceSummary from './AnnotationProvenanceSummary';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import StructuralVariantFilterForm from './StructuralVariantFilterForm';
import StructuralVariantResults from './StructuralVariantResults';
import VariantResultsLoading from './VariantResultsLoading';
import {
  buildStructuralPresetPayload,
  structuralLocationProblem,
  useStructuralVariantSearchState,
  withProjectId,
  type StructuralGenePanel,
  type StructuralVariant,
  type StructuralVariantFamily,
  type StructuralVariantFilterPreset,
  type StructuralVariantPage,
  type StructuralVariantReview,
  type StructuralVariantReviewSavePayload,
  type StructuralVariantTagDefinition,
} from './structuralVariantSearch';
import { addableTagDefinitions } from './smallVariantSearch';
import { fetchFamilyReviewTags, quickTagTogglePayload, searchReadsReviews } from './smallVariantReview';
import { apiPath, raw } from '../../lib/apiPath';
import { routePath } from '../../lib/routePath';

const buildStructuralVariantReviewPath = (familyId: string, variantId: string): string =>
  apiPath`/families/${familyId}/structural-variants/${variantId}/review`;

const hasReviewContent = (review: StructuralVariantReview | null | undefined): boolean =>
  Boolean(review?.classification || review?.tags?.length || review?.note);

const buildOptimisticReview = (
  variant: StructuralVariant,
  payload: StructuralVariantReviewSavePayload,
): StructuralVariantReview | null => {
  const nextReview: StructuralVariantReview = {
    variant_id: variant.review?.variant_id || variant._id,
    classification: payload.classification ?? null,
    tags: payload.tags,
    tag_metadata: variant.review?.tag_metadata || {},
    note: payload.note ?? null,
    updated_by: variant.review?.updated_by ?? null,
    // The loaded version, not a client clock: it is sent back with the next save (#513).
    updated_at: variant.review?.updated_at ?? null,
    compound_het: null,
    // The CNV classification comes back with the saved review: the server keeps or
    // replaces it, and recomputes its points and class.
    acmg: null,
    cnv_acmg: null,
    acmg_unreadable: false,
  };
  return hasReviewContent(nextReview) ? nextReview : null;
};

const updateStructuralVariantPageReview = (
  page: StructuralVariantPage | undefined,
  variantId: string,
  review: StructuralVariantReview | null,
): StructuralVariantPage | undefined => {
  if (!page || !Array.isArray(page.variants)) return page;
  return {
    ...page,
    variants: page.variants.map((variant) =>
      variant._id === variantId ? { ...variant, review } : variant,
    ),
  };
};

const FamilyStructuralVariantsPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  // The family record holds what the header warns of: an import that left the family
  // incomplete, and (through its project) an assembly outside the validated scope.
  const {
    data: familyData,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<StructuralVariantFamily>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as StructuralVariantFamily;
    },
  });

  const {
    data: panels = [],
    isLoading: panelsLoading,
    isError: panelsFailed,
    error: panelsError,
    refetch: refetchPanels,
  } = useQuery<StructuralGenePanel[]>({
    queryKey: ['panels'],
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as StructuralGenePanel[];
    },
  });
  // The generated Mendeliome panel scopes the standard default SV search.
  const mendeliomePanelId = useMemo(
    () => panels.find((panel) => panel.source === 'mendeliome')?._id,
    [panels],
  );

  const {
    activeFilterChips,
    activeFilterCount,
    applyPreset,
    applySavedPreset,
    draftFilters,
    draftLocationProblem,
    filters,
    goToPage,
    handleGtToggle,
    handleReset,
    handleSampleFieldChange,
    handleSearch,
    linkSearch,
    orderedMembers,
    page,
    removeActiveFilterChip,
    requestQueryString,
    setDraftFilterValue,
    sampleDraftFilters,
    sampleFilters,
    toggleDraftFilterListValue,
  } = useStructuralVariantSearchState({
    family: familyData,
    locationSearch: location.search,
    navigate,
    mendeliomePanelId,
    panelsLoaded: !panelsLoading,
  });
  const [workspaceFeedback, setWorkspaceFeedback] = useState<{
    type: 'error' | 'success';
    message: string;
  } | null>(null);
  // Collapse the whole filter panel to give the variant table more room.
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(
    familyData?.projects as string[] | undefined,
    preferredProjectId,
  );
  // The page waits for the reference (below), so it is never shown as loading here.
  // As every family page's header names it: the assembly and its version.
  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion, isError: referenceFailed },
    'Not linked',
  );

  const { data: presets = [] } = useQuery<StructuralVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'structural-variant-filter-presets'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/structural-variant-filter-presets`);
      return res.data as StructuralVariantFilterPreset[];
    },
  });

  const { data: tags = [] } = useQuery<StructuralVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'structural-variant-tags', projectId || null],
    enabled: Boolean(familyId),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });

  // The list and its total are read in the page's project, as the export is: the project
  // picks the assembly, so a family of projects on two assemblies answered the list 400
  // where the export worked (UI-5). They wait for the family and its reference, which the
  // page waits for before it shows them anyway, so the search runs once, in that project.
  const projectKnown = Boolean(familyData) && !referenceLoading;
  const listQueryString = useMemo(() => withProjectId(requestQueryString, projectId), [requestQueryString, projectId]);
  const totalQueryString = useMemo(
    () => withProjectId(new URLSearchParams({ page: '1', page_size: '1' }).toString(), projectId),
    [projectId],
  );

  const { data, isLoading, isFetching, isPlaceholderData, isError, error, refetch } = useQuery<StructuralVariantPage>({
    queryKey: ['family', familyId, 'structural-variants', listQueryString],
    enabled: projectKnown,
    queryFn: async () => {
      // A location from the URL that reads as neither a gene nor a region fails the
      // search: sent on, it was searched as a gene name and read as no SVs (#604).
      const locationProblem = structuralLocationProblem(filters);
      if (locationProblem) throw new Error(locationProblem);
      const res = await api.get(apiPath`/families/${familyId}/structural-variants?${raw(listQueryString)}`);
      return res.data as StructuralVariantPage;
    },
    // Keep the previous page's results on screen while the next page/filter loads
    // so pagination and filter changes don't unmount and remount the whole page.
    placeholderData: keepPreviousData,
  });

  const { data: allData, isError: allDataFailed } = useQuery<{ total: number }>({
    queryKey: ['family', familyId, 'structural-variants', 'total', projectId || null],
    enabled: projectKnown,
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/structural-variants?${raw(totalQueryString)}`);
      return { total: res.data.total };
    },
  });

  const filteredTotal = data?.total ?? 0;
  // A capped read counts only what it read: a lower bound, marked as one.
  const filteredTotalLabel = `${filteredTotal.toLocaleString()}${data?.total_is_estimated ? '+' : ''}`;
  // A failed total is unknown, not the filtered count (#606).
  const overallTotal = allDataFailed ? null : (allData?.total ?? filteredTotal);
  const totalPages = Math.max(1, Math.ceil(filteredTotal / 100));

  const savePresetMutation = useMutation({
    mutationFn: async (payload: { name: string; description?: string; scope: 'family' | 'global' }) => {
      if (!familyId) throw new Error('Family id is required');
      const res = await api.post(apiPath`/families/${familyId}/structural-variant-filter-presets`, {
        ...payload,
        ...buildStructuralPresetPayload({
          filters,
          members: orderedMembers,
          sampleFilters,
        }),
      });
      return res.data as StructuralVariantFilterPreset;
    },
    onSuccess: async () => {
      setWorkspaceFeedback({ type: 'success', message: 'Saved search updated.' });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'structural-variant-filter-presets'],
      });
    },
    onError: (error) => {
      setWorkspaceFeedback({
        type: 'error',
        message: getErrorMessage(error, 'Unable to save this search preset'),
      });
    },
  });

  const reviewMutation = useMutation({
    mutationFn: async ({
      variant,
      payload,
    }: {
      variant: StructuralVariant;
      payload: StructuralVariantReviewSavePayload;
    }) => {
      if (!familyId) throw new Error('Family id is required');
      const reviewPath = buildStructuralVariantReviewPath(familyId, variant._id);
      const body = withReviewVersion(payload, variant.review);
      const res = projectId
        ? await api.put(reviewPath, body, { params: { project_id: projectId } })
        : await api.put(reviewPath, body);
      return { review: res.data as StructuralVariantReview, variantId: variant._id };
    },
    onMutate: async ({ variant, payload }) => {
      await queryClient.cancelQueries({ queryKey: ['family', familyId, 'structural-variants'] });
      const snapshots = queryClient.getQueriesData<StructuralVariantPage>({
        queryKey: ['family', familyId, 'structural-variants'],
      });
      const optimisticReview = buildOptimisticReview(variant, payload);
      snapshots.forEach(([queryKey]) => {
        queryClient.setQueryData<StructuralVariantPage>(queryKey, (current) =>
          updateStructuralVariantPageReview(current, variant._id, optimisticReview),
        );
      });
      return { snapshots };
    },
    onSuccess: ({ review, variantId }) => {
      queryClient
        .getQueriesData<StructuralVariantPage>({
          queryKey: ['family', familyId, 'structural-variants'],
        })
        .forEach(([queryKey]) => {
          queryClient.setQueryData<StructuralVariantPage>(queryKey, (current) =>
            updateStructuralVariantPageReview(current, variantId, hasReviewContent(review) ? review : null),
          );
        });
      setWorkspaceFeedback({ type: 'success', message: 'Variant review saved.' });
      // As on the small-variant page: the list is searched again only when its filters read
      // reviews; other cached searches are marked stale.
      void queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'structural-variants'],
        refetchType: searchReadsReviews(listQueryString) ? 'active' : 'none',
      });
    },
    onError: (error, _variables, context) => {
      context?.snapshots.forEach(([queryKey, snapshot]) => {
        queryClient.setQueryData(queryKey, snapshot);
      });
      setWorkspaceFeedback({
        type: 'error',
        message: getErrorMessage(error, 'Unable to save the variant review'),
      });
      // Someone else saved this review meanwhile: show theirs rather than a stale copy.
      if (isReviewConflict(error)) {
        void queryClient.invalidateQueries({ queryKey: ['family', familyId, 'structural-variants'] });
      }
    },
  });

  // Without the family it is not known whether its import is complete, nor whether its
  // assembly is validated: the SVs are not shown without those warnings (SAFE-14).
  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Structural variants"
        what="Family"
        error={familyError}
        notFoundMessage="This family could not be found."
        onRetry={() => void refetchFamily()}
      />
    );
  }

  // Nor are they shown before the family and its reference are in, the header with them.
  if (!familyData || (familyData.projects?.length && referenceLoading) || (isLoading && !data)) {
    return (
      <PageState
        loading
        kicker="Structural variants"
        title="Loading structural variants"
        message="Preparing the structural variant table, summaries, and filters."
      />
    );
  }

  return (
    <div className="page-shell analysis-shell">
      <FamilyCaseHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="Structural variants"
        familyId={familyId ?? ''}
        family={familyData}
        projectId={projectId}
        assemblyLabel={referenceLabel}
        current="structural-variants"
        className="variant-workbench-card"
        facts={
          <>
            {/* A failed search counts nothing: its count is unknown, not 0 (#606). */}
            <FamilyFact label="Showing" value={isError ? '—' : filteredTotalLabel} />
            <FamilyFact label="All variants" value={overallTotal ?? '—'} />
            <FamilyFact label="Active filters" value={activeFilterCount} />
            <FamilyFact label="Tag library" value={addableTagDefinitions(tags).length} />
            {isFetching ? <FamilyFact label="Updating…" /> : null}
          </>
        }
        views={
          <>
            <GenomeWorkspaceLink
              to={routePath`/families/${familyId}/genome${raw(linkSearch)}${raw(projectId ? `${linkSearch ? '&' : '?'}project_id=${encodeURIComponent(projectId)}` : '')}`}
              className="family-module-link family-module-link--view hover:no-underline"
              label="Open the genome overview"
            >
              Genome
            </GenomeWorkspaceLink>
            <GenomeWorkspaceLink
              to={routePath`/families/${familyId}/circos${raw(linkSearch)}${raw(projectId ? `${linkSearch ? '&' : '?'}project_id=${encodeURIComponent(projectId)}` : '')}`}
              className="family-module-link family-module-link--view hover:no-underline"
              label="Open the Circos view"
            >
              Circos
            </GenomeWorkspaceLink>
          </>
        }
        footer={
          <>

        <FilterCollapseToggle
          collapsed={filtersCollapsed}
          onToggle={() => setFiltersCollapsed((current) => !current)}
        />
        {!filtersCollapsed && (
        <StructuralVariantFilterForm
          activeFilterChips={activeFilterChips}
          applyPreset={applyPreset}
          applySavedPreset={applySavedPreset}
          draftFilters={draftFilters}
          draftLocationProblem={draftLocationProblem}
          feedback={workspaceFeedback}
          handleGtToggle={handleGtToggle}
          handleReset={handleReset}
          handleSampleFieldChange={handleSampleFieldChange}
          handleSearch={handleSearch}
          orderedMembers={orderedMembers}
          onSaveCurrentPreset={async (payload) => {
            await savePresetMutation.mutateAsync(payload);
          }}
          panels={panels}
          presets={presets}
          removeActiveFilterChip={removeActiveFilterChip}
          sampleDraftFilters={sampleDraftFilters}
          savingPreset={savePresetMutation.isPending}
          setDraftFilterValue={setDraftFilterValue}
          tags={tags}
          toggleDraftFilterListValue={toggleDraftFilterListValue}
        />
        )}
          </>
        }
      />

      {panelsFailed ? (
        <QueryFailure
          what="the gene panel list"
          error={panelsError}
          onRetry={() => void refetchPanels()}
          consequence="Panels cannot be chosen, and the default Mendeliome scope is not applied."
        />
      ) : null}

      <div className="variant-results-region">
        {/* A new search covers the previous results; the same search run again does not. */}
        {isFetching && isPlaceholderData ? (
          <VariantResultsLoading
            title="Loading variants"
            message="Applying your filters to this family’s structural-variant calls."
          />
        ) : null}
        {/* A failed search is said as such: its variants used to render as an empty
            table, a family without SVs (#606). */}
        {isError ? (
          <QueryFailure what="the structural variants" error={error} onRetry={() => void refetch()} />
        ) : (
        <div
          className={isFetching && isPlaceholderData ? 'variant-results-fetching' : undefined}
          aria-busy={isFetching}
        >
      <StructuralVariantResults
        familyId={familyId}
        speciesName={speciesName}
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        candidateCap={data}
        filteredTotal={filteredTotal}
        filteredTotalIsEstimated={Boolean(data?.total_is_estimated)}
        linkSearch={linkSearch}
        members={orderedMembers}
        onPageChange={goToPage}
        overallTotal={overallTotal}
        page={page}
        projectId={projectId}
        readOnly={Boolean(familyData?.read_only)}
        reviewIsPending={reviewMutation.isPending}
        reviewError={
          reviewMutation.isError
            ? getErrorMessage(reviewMutation.error, 'Unable to save the variant review')
            : null
        }
        summary={data?.summary || {}}
        tags={tags}
        totalPages={totalPages}
        variants={data?.variants || []}
        onToggleReviewTag={async (variant, tagKey) => {
          await reviewMutation.mutateAsync({ variant, payload: quickTagTogglePayload(variant.review, tagKey) });
        }}
        onOpenReview={() => reviewMutation.reset()}
        onSaveReview={async (variant, payload) => {
          await reviewMutation.mutateAsync({ variant, payload });
        }}
      />
        </div>
        )}
      </div>
      {familyId ? <AnnotationProvenanceSummary familyId={familyId} modality="sv" /> : null}
    </div>
  );
};

export default FamilyStructuralVariantsPage;
