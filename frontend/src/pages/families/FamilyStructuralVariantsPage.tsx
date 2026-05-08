import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import Pedigree from '../../components/visualizations/Pedigree';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import StructuralVariantFilterForm from './StructuralVariantFilterForm';
import StructuralVariantResults from './StructuralVariantResults';
import {
  buildStructuralPresetPayload,
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
  parsePedigree,
} from './smallVariantSearch';

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
    updated_at: new Date().toISOString(),
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
      const res = await api.get(`/families/${familyId}`);
      return res.data as StructuralVariantFamily;
    },
  });

  const {
    activeFilterChips,
    activeFilterCount,
    applyPreset,
    applySavedPreset,
    draftFilters,
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
  });
  const [workspaceFeedback, setWorkspaceFeedback] = useState<{
    type: 'error' | 'success';
    message: string;
  } | null>(null);

  const {
    speciesName,
    assemblyName,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
  } = useFamilyReference(
    familyData?.projects as string[] | undefined,
    preferredProjectId,
  );
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    familyData?.projects?.length && referenceLoading
      ? 'Loading linked reference...'
      : 'Reference not linked',
  );

  const { data: panels = [] } = useQuery<StructuralGenePanel[]>({
    queryKey: ['panels'],
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as StructuralGenePanel[];
    },
  });

  const { data: presets = [] } = useQuery<StructuralVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'structural-variant-filter-presets'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-filter-presets`);
      return res.data as StructuralVariantFilterPreset[];
    },
  });

  const { data: tags = [] } = useQuery<StructuralVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'structural-variant-tags', projectId || null],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as StructuralVariantTagDefinition[];
    },
  });

  const { data, isLoading } = useQuery<StructuralVariantPage>({
    queryKey: ['family', familyId, 'structural-variants', requestQueryString],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variants?${requestQueryString}`);
      return res.data as StructuralVariantPage;
    },
  });

  const { data: allData } = useQuery<{ total: number }>({
    queryKey: ['family', familyId, 'structural-variants', 'total'],
    queryFn: async () => {
      const params = new URLSearchParams({ page: '1', page_size: '1' });
      const res = await api.get(`/families/${familyId}/structural-variants?${params.toString()}`);
      return { total: res.data.total };
    },
  });

  const filteredTotal = data?.total ?? 0;
  const overallTotal = allData?.total ?? filteredTotal;
  const totalPages = Math.max(1, Math.ceil(filteredTotal / 100));
  const pedRows = useMemo(() => parsePedigree(familyData?.pedigree), [familyData?.pedigree]);

  const savePresetMutation = useMutation({
    mutationFn: async (payload: { name: string; description?: string; scope: 'family' | 'global' }) => {
      if (!familyId) throw new Error('Family id is required');
      const res = await api.post(`/families/${familyId}/structural-variant-filter-presets`, {
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
      const res = projectId
        ? await api.put(reviewPath, payload, { params: { project_id: projectId } })
        : await api.put(reviewPath, payload);
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
    },
  });

  if (isLoading) {
    return (
      <PageState
        kicker="Structural Variants"
        title="Loading structural variants"
        message="Preparing the structural variant table, summaries, and filters."
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
                <p className="page-kicker">Structural Variants</p>
                <h1 className="catalog-card-title">Family {familyId}</h1>
                <p className="catalog-card-copy">{referenceLabel}</p>
                <div className="variant-summary-row">
                  <span className="badge-chip">Showing {filteredTotal}</span>
                  <span className="badge-chip">All variants {overallTotal}</span>
                  <span className="badge-chip">Active filters {activeFilterCount}</span>
                  <span className="badge-chip">Tag library {tags.length}</span>
                </div>
              </div>
              <div className="inline-actions">
                <Link
                  to={`/families/${familyId}/genome${linkSearch}${projectId ? `${linkSearch ? '&' : '?'}project_id=${projectId}` : ''}`}
                  className="button-secondary hover:no-underline"
                >
                  Genome
                </Link>
                <Link
                  to={`/families/${familyId}/circos${linkSearch}${projectId ? `${linkSearch ? '&' : '?'}project_id=${projectId}` : ''}`}
                  className="button-secondary hover:no-underline"
                >
                  Circos
                </Link>
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

        <StructuralVariantFilterForm
          activeFilterChips={activeFilterChips}
          applyPreset={applyPreset}
          applySavedPreset={applySavedPreset}
          draftFilters={draftFilters}
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
      </section>

      <StructuralVariantResults
        familyId={familyId}
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
  );
};

export default FamilyStructuralVariantsPage;
