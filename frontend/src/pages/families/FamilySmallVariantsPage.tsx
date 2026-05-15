import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import Pedigree from '../../components/visualizations/Pedigree';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
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

const buildSmallVariantReviewPath = (familyId: string, variantId: string): string =>
  `/families/${encodeURIComponent(familyId)}/small-variants/${encodeURIComponent(variantId)}/review`;

const hasReviewContent = (review: SmallVariantReview | null | undefined): boolean =>
  Boolean(
    review?.classification ||
      review?.tags?.length ||
      review?.note ||
      review?.compound_het,
  );

const formatVariantTotal = (total: number | undefined, estimated?: boolean): string => {
  const safeTotal = Math.max(total ?? 0, 0);
  if (!estimated || safeTotal <= 0) {
    return String(safeTotal);
  }
  return `${Math.max(safeTotal - 1, 0)}+`;
};

const formatSummaryCount = (value: number | undefined): string =>
  Math.max(value ?? 0, 0).toLocaleString();

const buildOptimisticReview = (
  variant: SmallVariant,
  payload: SmallVariantReviewSavePayload,
): SmallVariantReview | null => {
  const nextReview: SmallVariantReview = {
    variant_id: variant.review?.variant_id || variant._id,
    classification: payload.classification ?? null,
    tags: payload.tags,
    tag_metadata: variant.review?.tag_metadata || {},
    note: payload.note ?? null,
    updated_by: variant.review?.updated_by ?? null,
    updated_at: new Date().toISOString(),
    compound_het:
      'compound_het' in payload
        ? variant.review?.compound_het ?? null
        : variant.review?.compound_het ?? null,
  };

  return hasReviewContent(nextReview) ? nextReview : null;
};

const withUpdatedVariantReview = (
  variant: SmallVariant,
  variantId: string,
  review: SmallVariantReview | null,
): SmallVariant => {
  if (variant._id !== variantId) {
    return variant;
  }
  return { ...variant, review };
};

const updateSmallVariantPageReview = (
  page: SmallVariantPage | undefined,
  variantId: string,
  review: SmallVariantReview | null,
): SmallVariantPage | undefined => {
  if (!page) {
    return page;
  }

  return {
    ...page,
    variants: page.variants.map((variant) =>
      withUpdatedVariantReview(variant, variantId, review),
    ),
    variant_groups: page.variant_groups?.map((group) => ({
      ...group,
      variants: group.variants.map((variant) =>
        withUpdatedVariantReview(variant, variantId, review),
      ),
    })),
  };
};

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
  });
  const [workspaceFeedback, setWorkspaceFeedback] = useState<{
    tone: 'error' | 'success';
    message: string;
  } | null>(null);

  const { data: panels = [] } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

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

  const { data, isLoading, isError, error } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'small-variants', requestQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variants?${requestQueryString}`);
      return res.data as SmallVariantPage;
    },
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

  if (!variantQueryReady || isLoading) {
    return (
      <PageState
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
                {smallVariantSummary?.sample_counts?.length ? (
                  <div className="variant-sample-summary">
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
                    <div className="variant-summary-row">
                      <span className="badge-chip">
                        Showing {formatVariantTotal(data?.total, data?.total_is_estimated)}
                      </span>
                      <span className="badge-chip">Active filters {activeFilterCount}</span>
                      <span className="badge-chip">Tag library {tags.length}</span>
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
                <Pedigree rows={pedRows} />
              </div>
            </div>
          )}
        </div>
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
      </section>

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
  );
};

export default FamilySmallVariantsPage;
