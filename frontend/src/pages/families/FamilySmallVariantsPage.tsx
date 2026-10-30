import React, { useId, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import {
  isReviewConflict,
  withPartnerReviewVersion,
  withReviewVersion,
} from '../../lib/reviewConcurrency';
import { getErrorMessage } from '../../lib/errorMessage';
import FamilyCaseHeader, { FamilyFact } from './FamilyCaseHeader';
import FilterCollapseToggle from './FilterCollapseToggle';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import QueryFailure from '../../components/QueryFailure';
import AnnotationProvenanceSummary from './AnnotationProvenanceSummary';
import SmallVariantFilterForm from './SmallVariantFilterForm';
import SmallVariantResults from './SmallVariantResults';
import VariantResultsLoading from './VariantResultsLoading';
import {
  addableTagDefinitions,
  BUILT_IN_SMALL_PRESETS,
  buildPresetPayload,
  describeUnappliedFilters,
  hasLocationProblems,
  SMALL_VARIANT_UNSUPPORTED_FILTERS,
  smallVariantAppliesFilterChip,
  smallVariantLocationProblems,
  useSmallVariantSearchState,
  type FamilySmallVariantPage,
  type GenePanel,
  type SmallVariant,
  type SmallVariantFamily,
  type SmallVariantFilterPreset,
  type SmallVariantPage,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  buildOptimisticReview,
  buildSmallVariantReviewPath,
  fetchFamilyReviewTags,
  findLoadedVariantReview,
  hasReviewContent,
  quickTagTogglePayload,
  searchReadsReviews,
  updateSmallVariantPageReview,
} from './smallVariantReview';
import { apiPath, raw } from '../../lib/apiPath';
import { formatVariantTotal } from './smallVariantResultUtils';

const formatSummaryCount = (value: number | undefined): string =>
  Math.max(value ?? 0, 0).toLocaleString();

const FamilySmallVariantsPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const {
    data: family,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(
    family?.projects as string[] | undefined,
    preferredProjectId,
  );
  const variantQueryReady = Boolean(
    familyId && family && (!(family.projects?.length) || projectId),
  );
  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion, isError: referenceFailed },
    'Not linked',
  );

  const {
    data: panels = [],
    isLoading: panelsLoading,
    isError: panelsFailed,
    error: panelsError,
    refetch: refetchPanels,
  } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });
  // The generated Mendeliome panel scopes the default small-variant search.
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
    draftLocationProblems,
    filters,
    goToPage,
    handleApply,
    handleGtToggle,
    handleReset,
    handleSampleFieldChange,
    members,
    page,
    removeActiveFilterChip,
    requestQueryString,
    setDraftFilterValue,
    sampleDraftFilters,
    sampleFilters,
    searchReady,
    toggleDraftFilterListValue,
  } = useSmallVariantSearchState({
    family,
    locationSearch: location.search,
    navigate,
    resolvedProjectId: projectId,
    mendeliomePanelId,
    panelsLoaded: !panelsLoading,
  });
  // Chipped (and counted, activeFilterCount): the filters the small-variant list applies. A
  // search saved on the NIPT page, which shares the saved searches, or a link, may carry the NIPT
  // list's own: an inheritance view, which failed the whole search or, the fetal de novo one, was
  // read as de novo / dominant, and filters the list never applied though they were chipped
  // (found while fixing #1012). They are not sent, and are named once instead.
  const appliedFilterChips = useMemo(
    () => activeFilterChips.filter(smallVariantAppliesFilterChip),
    [activeFilterChips],
  );
  const unappliedFiltersNote = useMemo(
    () =>
      describeUnappliedFilters(
        'small-variant list',
        activeFilterChips.filter((chip) => !smallVariantAppliesFilterChip(chip)),
      ),
    [activeFilterChips],
  );
  const [workspaceFeedback, setWorkspaceFeedback] = useState<{
    tone: 'error' | 'success';
    message: string;
  } | null>(null);
  // Collapse the whole filter panel to give the variant cards more room. The form stays
  // mounted while hidden, so what it holds (the loaded preset, open sections) survives.
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  const filterPanelId = useId();
  // Opened without a search in the URL, the page loads the Phenotype-priority preset scoped
  // to the Mendeliome panel (useSmallVariantSearchState): the form names it as the active
  // filter once that draft is in place.
  const [openedOnDefault] = useState(() =>
    Array.from(new URLSearchParams(location.search).keys()).every(
      (key) => key === 'project_id' || key === 'page',
    ),
  );
  const defaultPresetLabel =
    BUILT_IN_SMALL_PRESETS.find((preset) => preset.value === 'phenotype_priority')?.label ??
    'Phenotype priority';
  const initialPreset = useMemo(
    () =>
      openedOnDefault && searchReady && members.length
        ? {
            value: 'built-in:phenotype_priority',
            label: mendeliomePanelId
              ? `${defaultPresetLabel}, Mendeliome panel`
              : defaultPresetLabel,
            overrides: { panel_id: mendeliomePanelId ?? '' },
          }
        : null,
    [openedOnDefault, searchReady, members.length, mendeliomePanelId, defaultPresetLabel],
  );

  const { data: presets = [] } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'small-variant-filter-presets'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variant-filter-presets`);
      return res.data as SmallVariantFilterPreset[];
    },
  });

  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', projectId || null],
    enabled: variantQueryReady,
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });

  const { data, isLoading, isFetching, isPlaceholderData, isError, error } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'small-variants', requestQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      // A location from the URL that cannot be read fails the search with its reason:
      // sent on, a malformed locus was searched as a gene name (#604).
      const locationProblems = smallVariantLocationProblems(filters);
      if (hasLocationProblems(locationProblems)) {
        throw new Error([...locationProblems.include, ...locationProblems.exclude].join(' '));
      }
      const res = await api.get(apiPath`/families/${familyId}/small-variants?${raw(requestQueryString)}`);
      return res.data as FamilySmallVariantPage;
    },
    // Keep the previous page's results on screen while the next page/filter loads
    // so pagination and filter changes don't unmount and remount the whole page.
    placeholderData: keepPreviousData,
  });
  const smallVariantSummary = data?.small_variant_summary ?? null;
  const allVariantTotal =
    data?.unfiltered_total ??
    smallVariantSummary?.total_variants ??
    data?.total ??
    0;
  const allVariantTotalIsEstimated =
    data?.unfiltered_total_is_estimated ??
    false;

  const savePresetMutation = useMutation({
    mutationFn: async (payload: {
      name: string;
      description?: string;
    }) => {
      if (!familyId) {
        throw new Error('Family id is required');
      }
      const res = await api.post(apiPath`/families/${familyId}/small-variant-filter-presets`, {
        ...payload,
        ...buildPresetPayload({
          filters,
          members,
          sampleFilters,
        }),
      });
      return res.data as SmallVariantFilterPreset;
    },
    onSuccess: async () => {
      setWorkspaceFeedback({
        tone: 'success',
        message: 'Saved search updated.',
      });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'small-variant-filter-presets'],
      });
    },
    onError: (error) => {
      setWorkspaceFeedback({
        tone: 'error',
        message: getErrorMessage(error, 'Unable to save this search preset'),
      });
    },
  });

  const reviewMutation = useMutation({
    mutationFn: async ({
      variant,
      payload,
    }: {
      variant: SmallVariant;
      payload: SmallVariantReviewSavePayload;
    }) => {
      if (!familyId) {
        throw new Error('Family id is required');
      }
      const reviewPath = buildSmallVariantReviewPath(familyId, variant._id);
      // A pair save writes the partner's review too: it names the version the list loaded.
      const partnerId = payload.compound_het?.partner_variant_id;
      const partnerReview = partnerId
        ? findLoadedVariantReview(
            queryClient
              .getQueriesData<SmallVariantPage>({ queryKey: ['family', familyId, 'small-variants'] })
              .map(([, page]) => page),
            partnerId,
          )
        : undefined;
      const body = withReviewVersion(withPartnerReviewVersion(payload, partnerReview), variant.review);
      const res = projectId
        ? await api.put(reviewPath, body, { params: { project_id: projectId } })
        : await api.put(reviewPath, body);
      return { review: res.data as SmallVariantReview, variantId: variant._id };
    },
    onMutate: async ({ variant, payload }) => {
      await queryClient.cancelQueries({ queryKey: ['family', familyId, 'small-variants'] });
      const snapshots = queryClient.getQueriesData<SmallVariantPage>({
        queryKey: ['family', familyId, 'small-variants'],
      });
      const optimisticReview = buildOptimisticReview(variant, payload);
      snapshots.forEach(([queryKey]) => {
        queryClient.setQueryData<SmallVariantPage>(queryKey, (current) =>
          updateSmallVariantPageReview(current, variant._id, optimisticReview),
        );
      });
      return { snapshots };
    },
    onSuccess: ({ review, variantId }, { payload }) => {
      queryClient
        .getQueriesData<SmallVariantPage>({ queryKey: ['family', familyId, 'small-variants'] })
        .forEach(([queryKey]) => {
          queryClient.setQueryData<SmallVariantPage>(queryKey, (current) =>
            updateSmallVariantPageReview(current, variantId, hasReviewContent(review) ? review : null),
          );
        });
      setWorkspaceFeedback({
        tone: 'success',
        message: 'Variant review saved.',
      });
      // The card shows the saved review already. The list is searched again only when its
      // filters read reviews (a tag or note can add or drop a variant there), or when a pair
      // save changed the partner's review too; other cached searches are marked stale and
      // searched again when next shown.
      void queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'small-variants'],
        refetchType:
          payload.compound_het?.partner_variant_id || searchReadsReviews(requestQueryString)
            ? 'active'
            : 'none',
      });
    },
    onError: (error, _variables, context) => {
      context?.snapshots.forEach(([queryKey, snapshot]) => {
        queryClient.setQueryData(queryKey, snapshot);
      });
      setWorkspaceFeedback({
        tone: 'error',
        message: getErrorMessage(error, 'Unable to save the variant review'),
      });
      // Someone else saved this review meanwhile: show theirs rather than a stale copy.
      if (isReviewConflict(error)) {
        void queryClient.invalidateQueries({ queryKey: ['family', familyId, 'small-variants'] });
      }
    },
  });

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 100));

  // The family failed: the search waits for it, and would otherwise wait for good. Without
  // it, whether its import is complete is not known either (SAFE-14).
  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Small variants"
        what="Family"
        error={familyError}
        notFoundMessage="This family could not be found."
        onRetry={() => void refetchFamily()}
      />
    );
  }

  // The project catalogue failed: the search runs within the family's project, and would
  // otherwise wait for it for good (#608).
  if (referenceFailed) {
    return (
      <PageState
        kicker="Small variants"
        title="Reference could not be loaded"
        message="The family's project, and with it the reference assembly, could not be loaded. The search runs within that project, so it cannot run without it."
        action={
          <button type="button" className="button-secondary" onClick={retryReference}>
            Retry
          </button>
        }
      />
    );
  }

  if (!variantQueryReady || (isLoading && !data)) {
    return (
      <PageState
        loading
        kicker="Small variants"
        title="Loading small variants"
        message="Collecting filtered small variant calls for this family."
      />
    );
  }

  if (isError) {
    return (
      <PageState
        kicker="Small variants"
        title="Unable to load small variants"
        message={getErrorMessage(error, 'The small-variant query failed before results could be loaded.')}
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
        kicker="Small variants"
        familyId={familyId ?? ''}
        family={family}
        projectId={projectId ?? preferredProjectId}
        assemblyLabel={referenceLabel}
        current="small-variants"
        className="variant-workbench-card"
        facts={
          data ? (
            <>
              <FamilyFact
                label="Showing"
                value={formatVariantTotal(data.total, data.total_is_estimated, data.count_limit)}
              />
              <FamilyFact
                label="All variants"
                value={formatVariantTotal(allVariantTotal, allVariantTotalIsEstimated, data.count_limit)}
              />
              {smallVariantSummary ? (
                <>
                  <FamilyFact label="SNVs" value={formatSummaryCount(smallVariantSummary.snv_count)} />
                  <FamilyFact label="Indels" value={formatSummaryCount(smallVariantSummary.indel_count)} />
                </>
              ) : null}
              <FamilyFact label="Active filters" value={activeFilterCount} />
              <FamilyFact label="Tag library" value={addableTagDefinitions(tags).length} />
              {isFetching ? <FamilyFact label="Updating…" /> : null}
            </>
          ) : null
        }
        footer={
          <>
        <FilterCollapseToggle
          collapsed={filtersCollapsed}
          controls={filterPanelId}
          onToggle={() => setFiltersCollapsed((current) => !current)}
        />
        <div id={filterPanelId} hidden={filtersCollapsed}>
        <SmallVariantFilterForm
          collapsibleAdvanced
          initialPreset={initialPreset}
          unsupportedFilters={SMALL_VARIANT_UNSUPPORTED_FILTERS}
          unappliedFiltersNote={unappliedFiltersNote}
          activeFilterChips={appliedFilterChips}
          applyPreset={applyPreset}
          applySavedPreset={applySavedPreset}
          draftFilters={draftFilters}
          feedback={workspaceFeedback}
          handleApply={handleApply}
          draftLocationProblems={draftLocationProblems}
          handleGtToggle={handleGtToggle}
          handleReset={handleReset}
          handleSampleFieldChange={handleSampleFieldChange}
          members={members}
          relationships={family?.relationships ?? []}
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
        </div>
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
        {/* A new search covers the previous results while it runs; the same search run again
            (after a review saved under a review filter) leaves them usable, with the header's
            "Updating…" chip. */}
        {isFetching && isPlaceholderData ? (
          <VariantResultsLoading
            title="Loading variants"
            message="Applying your filters to this family’s small-variant calls."
          />
        ) : null}
        <div
          className={isFetching && isPlaceholderData ? 'variant-results-fetching' : undefined}
          aria-busy={isFetching}
        >
      <SmallVariantResults
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        data={data}
        familyId={familyId}
        locationSearch={location.search}
        members={members}
        relationships={family?.relationships}
        onPageChange={goToPage}
        page={page}
        projectId={projectId}
        requestQueryString={requestQueryString}
        readOnly={Boolean(family?.read_only)}
        reviewIsPending={reviewMutation.isPending}
        reviewError={
          reviewMutation.isError
            ? getErrorMessage(reviewMutation.error, 'Unable to save the variant review')
            : null
        }
        speciesName={speciesName}
        tags={tags}
        totalPages={totalPages}
        onToggleReviewTag={async (variant, tagKey) => {
          await reviewMutation.mutateAsync({ variant, payload: quickTagTogglePayload(variant.review, tagKey) });
        }}
        onOpenReview={() => reviewMutation.reset()}
        onSaveReview={async (variant, payload) => {
          await reviewMutation.mutateAsync({ variant, payload });
        }}
      />
        </div>
      </div>
      {familyId ? <AnnotationProvenanceSummary familyId={familyId} modality="snv" /> : null}
    </div>
  );
};

export default FamilySmallVariantsPage;
