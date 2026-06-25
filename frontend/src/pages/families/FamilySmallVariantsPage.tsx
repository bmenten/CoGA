import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import Pedigree from '../../components/visualizations/Pedigree';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import LoadingBar from '../../components/LoadingBar';
import SmallVariantFilterForm from './SmallVariantFilterForm';
import SmallVariantResults from './SmallVariantResults';
import {
  buildPresetPayload,
  parsePedigree,
  useSmallVariantSearchState,
  type GenePanel,
  type SmallVariant,
  type SmallVariantFamily,
  type SmallVariantFilterPreset,
  type SmallVariantPage,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
  normalizeReviewClassification,
} from './smallVariantSearch';
import {
  buildOptimisticReview,
  buildSmallVariantReviewPath,
  hasReviewContent,
  updateSmallVariantPageReview,
} from './smallVariantReview';

const formatVariantTotal = (total: number | undefined, estimated?: boolean): string => {
  const safeTotal = Math.max(total ?? 0, 0);
  if (!estimated || safeTotal <= 0) {
    return String(safeTotal);
  }
  return `${Math.max(safeTotal - 1, 0)}+`;
};

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

  const { data: family } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const {
    speciesName,
    assemblyName,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
  } = useFamilyReference(
    family?.projects as string[] | undefined,
    preferredProjectId,
  );
  const variantQueryReady = Boolean(
    familyId && family && (!(family.projects?.length) || projectId),
  );
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    family?.projects?.length && referenceLoading ? 'Loading linked reference...' : 'Reference not linked',
  );

  const { data: panels = [], isLoading: panelsLoading } = useQuery<GenePanel[]>({
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
    toggleDraftFilterListValue,
  } = useSmallVariantSearchState({
    family,
    locationSearch: location.search,
    navigate,
    resolvedProjectId: projectId,
    mendeliomePanelId,
    panelsLoaded: !panelsLoading,
  });
  const [workspaceFeedback, setWorkspaceFeedback] = useState<{
    tone: 'error' | 'success';
    message: string;
  } | null>(null);
  // Collapse the whole filter panel to give the variant cards more room.
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);

  const { data: presets = [] } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'small-variant-filter-presets'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-filter-presets`);
      return res.data as SmallVariantFilterPreset[];
    },
  });

  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', projectId || null],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });

  const { data, isLoading, isFetching, isError, error } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'small-variants', requestQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variants?${requestQueryString}`);
      return res.data as SmallVariantPage;
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
      const res = await api.post(`/families/${familyId}/small-variant-filter-presets`, {
        ...payload,
        scope: 'global',
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
      const res = projectId
        ? await api.put(reviewPath, payload, { params: { project_id: projectId } })
        : await api.put(reviewPath, payload);
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
    onSuccess: ({ review, variantId }) => {
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
      void queryClient.invalidateQueries({ queryKey: ['family', familyId, 'small-variants'] });
    },
    onError: (error, _variables, context) => {
      context?.snapshots.forEach(([queryKey, snapshot]) => {
        queryClient.setQueryData(queryKey, snapshot);
      });
      setWorkspaceFeedback({
        tone: 'error',
        message: getErrorMessage(error, 'Unable to save the variant review'),
      });
    },
  });

  const pedRows = useMemo(() => parsePedigree(family?.pedigree), [family?.pedigree]);
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 100));

  if (!variantQueryReady || (isLoading && !data)) {
    return (
      <PageState
        loading
        kicker="Small Variants"
        title="Loading small variants"
        message="Collecting filtered small variant calls for this family."
      />
    );
  }

  if (isError) {
    return (
      <PageState
        kicker="Small Variants"
        title="Unable to load small variants"
        message={getErrorMessage(error, 'The small-variant query failed before results could be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card variant-workbench-card">
        <div className={`page-top-card-grid${pedRows.length ? ' page-top-card-grid--with-visual' : ''}`}>
          <div className="page-top-card-copy">
            <div className="page-header">
              <div className="space-y-2">
                <p className="page-kicker">Small Variants</p>
                <h1 className="catalog-card-title">Family {familyId}</h1>
                {data ? (
                  <div className="variant-sample-summary">
                    {smallVariantSummary?.sample_counts?.length ? (
                      <div className="data-table-shell overflow-x-auto">
                        <table className="analysis-table variant-sample-summary-table">
                          <thead>
                            <tr>
                              <th>Sample</th>
                              <th>Variants</th>
                              <th>Het</th>
                              <th>Hom</th>
                            </tr>
                          </thead>
                          <tbody>
                            {smallVariantSummary.sample_counts.map((sampleSummary) => (
                              <tr key={sampleSummary.sample_id}>
                                <td>{sampleSummary.sample_id}</td>
                                <td>{formatSummaryCount(sampleSummary.non_ref_count)}</td>
                                <td>{formatSummaryCount(sampleSummary.het_count)}</td>
                                <td>{formatSummaryCount(sampleSummary.hom_alt_count)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    <div className="variant-summary-row">
                      <span className="badge-chip badge-chip--emphasis">
                        Showing {formatVariantTotal(data?.total, data?.total_is_estimated)}
                      </span>
                      <span className="badge-chip">
                        All variants {formatVariantTotal(allVariantTotal, allVariantTotalIsEstimated)}
                      </span>
                      {smallVariantSummary ? (
                        <>
                          <span className="badge-chip">
                            SNVs {formatSummaryCount(smallVariantSummary.snv_count)}
                          </span>
                          <span className="badge-chip">
                            Indels {formatSummaryCount(smallVariantSummary.indel_count)}
                          </span>
                        </>
                      ) : null}
                      <span className="badge-chip">Active filters {activeFilterCount}</span>
                      <span className="badge-chip">Tag library {tags.length}</span>
                      {isFetching ? <span className="badge-chip">Updating…</span> : null}
                    </div>
                  </div>
                ) : null}
              </div>
              <div className="inline-actions">
                <Link to={`/families/${familyId}`} className="button-ghost hover:no-underline">
                  Family
                </Link>
              </div>
            </div>
          </div>
          {pedRows.length > 0 && (
            <div className="page-top-card-visual">
              <div className="page-top-card-pedigree">
                <p className="analysis-section-title">Pedigree</p>
                <Pedigree
                  rows={pedRows}
                  members={family?.members}
                  relationships={family?.relationships}
                  inheritanceModel={(family?.metadata?.pgt as { inheritance_model?: string } | undefined)?.inheritance_model}
                />
              </div>
            </div>
          )}
        </div>
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
        <SmallVariantFilterForm
          activeFilterChips={activeFilterChips}
          applyPreset={applyPreset}
          applySavedPreset={applySavedPreset}
          draftFilters={draftFilters}
          feedback={workspaceFeedback}
          handleApply={handleApply}
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
        )}
      </section>

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
                  Applying your filters to this family’s small-variant calls.
                </span>
              </div>
            </div>
          </>
        ) : null}
        <div className={isFetching ? 'variant-results-fetching' : undefined} aria-busy={isFetching}>
      <SmallVariantResults
        assemblyName={assemblyName}
        assemblyVersion={assemblyVersion}
        data={data}
        familyId={familyId}
        locationSearch={location.search}
        members={members}
        onPageChange={goToPage}
        page={page}
        projectId={projectId}
        requestQueryString={requestQueryString}
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
          const nextTags = new Set(variant.review?.tags || []);
          if (nextTags.has(tagKey)) {
            nextTags.delete(tagKey);
          } else {
            nextTags.add(tagKey);
          }
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
      </div>
    </div>
  );
};

export default FamilySmallVariantsPage;
