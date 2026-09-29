import React, { useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isReviewConflict, withReviewVersion } from '../../lib/reviewConcurrency';
import { getErrorMessage } from '../../lib/errorMessage';
import FamilyPageHeader from './FamilyPageHeader';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import LoadingBar from '../../components/LoadingBar';
import QueryFailure from '../../components/QueryFailure';
import AnnotationProvenanceSummary from './AnnotationProvenanceSummary';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import StructuralVariantFilterForm from './StructuralVariantFilterForm';
import StructuralVariantResults from './StructuralVariantResults';
import {
  buildStructuralPresetPayload,
  structuralLocationProblem,
  useStructuralVariantSearchState,
  type StructuralGenePanel,
  type StructuralSummary,
  type StructuralVariant,
  type StructuralVariantFamily,
  type StructuralVariantFilterPreset,
  type StructuralVariantReview,
  type StructuralVariantReviewSavePayload,
  type StructuralVariantTagDefinition,
} from './structuralVariantSearch';
import {
  normalizeReviewClassification,
} from './smallVariantSearch';
import { apiPath, raw } from '../../lib/apiPath';

type StructuralVariantPage = {
  variants: StructuralVariant[];
  total: number;
  summary?: StructuralSummary;
};

const buildStructuralVariantReviewPath = (familyId: string, variantId: string): string =>
  `/families/${encodeURIComponent(familyId)}/structural-variants/${encodeURIComponent(variantId)}/review`;

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

  const { data: familyData } = useQuery<StructuralVariantFamily>({
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
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion, isError: referenceFailed },
    familyData?.projects?.length && referenceLoading
      ? 'Loading linked reference...'
      : 'Reference not linked',
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
    queryFn: async () => {
      // Structural and small-variant tags share the same store (small_variant_tag_definitions);
      // there is no separate structural endpoint.
      const res = await api.get(apiPath`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as StructuralVariantTagDefinition[];
    },
  });

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery<StructuralVariantPage>({
    queryKey: ['family', familyId, 'structural-variants', requestQueryString],
    queryFn: async () => {
      // A location from the URL that reads as neither a gene nor a region fails the
      // search: sent on, it was searched as a gene name and read as no SVs (#604).
      const locationProblem = structuralLocationProblem(filters);
      if (locationProblem) throw new Error(locationProblem);
      const res = await api.get(apiPath`/families/${familyId}/structural-variants?${raw(requestQueryString)}`);
      return res.data as StructuralVariantPage;
    },
    // Keep the previous page's results on screen while the next page/filter loads
    // so pagination and filter changes don't unmount and remount the whole page.
    placeholderData: keepPreviousData,
  });

  const { data: allData, isError: allDataFailed } = useQuery<{ total: number }>({
    queryKey: ['family', familyId, 'structural-variants', 'total'],
    queryFn: async () => {
      const params = new URLSearchParams({ page: '1', page_size: '1' });
      const res = await api.get(apiPath`/families/${familyId}/structural-variants?${raw(params.toString())}`);
      return { total: res.data.total };
    },
  });

  const filteredTotal = data?.total ?? 0;
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
      void queryClient.invalidateQueries({ queryKey: ['family', familyId, 'structural-variants'] });
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

  if (isLoading && !data) {
    return (
      <PageState
        loading
        kicker="Structural Variants"
        title="Loading structural variants"
        message="Preparing the structural variant table, summaries, and filters."
      />
    );
  }

  return (
    <div className="page-shell analysis-shell">
      <FamilyPageHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="Structural Variants"
        familyId={familyId}
        family={familyData}
        projectId={projectId}
        className="variant-workbench-card"
        actions={
          <>
            <GenomeWorkspaceLink
              to={`/families/${familyId}/genome${linkSearch}${projectId ? `${linkSearch ? '&' : '?'}project_id=${projectId}` : ''}`}
              className="button-secondary workspace-button hover:no-underline"
              label="Open the genome overview"
            >
              Genome
            </GenomeWorkspaceLink>
            <GenomeWorkspaceLink
              to={`/families/${familyId}/circos${linkSearch}${projectId ? `${linkSearch ? '&' : '?'}project_id=${projectId}` : ''}`}
              className="button-secondary workspace-button hover:no-underline"
              label="Open the Circos view"
            >
              Circos
            </GenomeWorkspaceLink>
          </>
        }
        footer={
          <>

        <div className="variant-filter-collapse-bar">
          <button
            type="button"
            className="variant-filter-collapse-toggle"
            aria-expanded={!filtersCollapsed}
            onClick={() => setFiltersCollapsed((current) => !current)}
          >
            <span className="variant-filter-dropdown-caret" aria-hidden="true">
              ▾
            </span>
            <span>{filtersCollapsed ? 'Show filters' : 'Hide filters'}</span>
          </button>
        </div>
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
      >
        <p className="catalog-card-copy">{referenceLabel}</p>
        <div className="variant-summary-row">
          {/* A failed search counts nothing: its count is unknown, not 0 (#606). */}
          <span className="badge-chip">Showing {isError ? '—' : filteredTotal}</span>
          <span className="badge-chip">All variants {overallTotal ?? '—'}</span>
          <span className="badge-chip">Active filters {activeFilterCount}</span>
          <span className="badge-chip">Tag library {tags.length}</span>
          {isFetching ? <span className="badge-chip">Updating…</span> : null}
        </div>
      </FamilyPageHeader>

      {panelsFailed ? (
        <QueryFailure
          what="the gene panel list"
          error={panelsError}
          onRetry={() => void refetchPanels()}
          consequence="Panels cannot be chosen, and the default Mendeliome scope is not applied."
        />
      ) : null}

      <div className="variant-results-region">
        {isFetching ? <LoadingBar label="Loading variants" /> : null}
        {isFetching ? (
          <>
            <div className="variant-results-overlay" aria-hidden="true" />
            <div
              className="variant-results-loading-card"
              role="status"
              aria-live="polite"
              aria-busy="true"
            >
              <span
                className="viz-loading-spinner viz-loading-spinner--lg"
                aria-hidden="true"
              />
              <div className="variant-results-overlay-text">
                <span className="variant-results-overlay-title">Loading variants…</span>
                <span className="variant-results-overlay-sub">
                  Applying your filters to this family’s structural-variant calls.
                </span>
              </div>
            </div>
          </>
        ) : null}
        {/* A failed search is said as such: its variants used to render as an empty
            table, a family without SVs (#606). */}
        {isError ? (
          <QueryFailure what="the structural variants" error={error} onRetry={() => void refetch()} />
        ) : (
        <div className={isFetching ? 'variant-results-fetching' : undefined} aria-busy={isFetching}>
      <StructuralVariantResults
        familyId={familyId}
        speciesName={speciesName}
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        filteredTotal={filteredTotal}
        linkSearch={linkSearch}
        members={orderedMembers}
        onPageChange={goToPage}
        overallTotal={overallTotal}
        page={page}
        projectId={projectId}
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
          const nextTags = new Set(variant.review?.tags || []);
          if (nextTags.has(tagKey)) nextTags.delete(tagKey);
          else nextTags.add(tagKey);
          await reviewMutation.mutateAsync({
            variant,
            payload: {
              classification:
                normalizeReviewClassification(variant.review?.classification, variant.review?.tags) ||
                undefined,
              tags: Array.from(nextTags).sort((left, right) => left.localeCompare(right)),
              note: variant.review?.note || undefined,
            },
          });
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
