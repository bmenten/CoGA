import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import { useFamilyReference } from '../../lib/reference';
import type { ApiNiptCoverageSummary, ApiNiptSummary } from '../../lib/apiTypes';
import {
  CATEGORY_LABELS,
  depth,
  lowCoverageChipClass,
  lowCoverageDetail,
  pct,
} from './niptClassification';
import {
  buildPresetPayload,
  NIPT_BUILT_IN_PRESETS,
  normalizeReviewClassification,
  parseCommaSeparatedValues,
  parsePedigree,
  useSmallVariantSearchState,
  type GenePanel,
  type SmallFilterState,
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
  hasReviewContent,
  updateSmallVariantPageReview,
} from './smallVariantReview';
import Pedigree from '../../components/visualizations/Pedigree';
import PageState from '../../components/PageState';
import SmallVariantFilterForm from './SmallVariantFilterForm';
import SmallVariantResults from './SmallVariantResults';

const MONOGENIC_NIPT_ANALYSIS_TYPE = 'monogenic_nipt';
const PAGE_SIZE = 50;

// `categories` is ticked in the category checkboxes when the preset is picked.
// Recessive groups by gene (no single category), so it clears them; "Any" leaves
// whatever is checked.
const INHERITANCE_PRESETS: { value: string; label: string; categories?: string }[] = [
  { value: '', label: 'Any inheritance' },
  { value: 'de_novo', label: 'De novo candidates', categories: '1' },
  { value: 'paternal_dominant', label: 'Paternal dominant (transmitted)', categories: '7' },
  { value: 'maternal_dominant', label: 'Maternal dominant (transmitted)', categories: '3' },
  { value: 'recessive_at_risk', label: 'Recessive at-risk', categories: '' },
];

const FILTER_STEPS: { key: string; label: string }[] = [
  { key: 'total_in', label: 'Total' },
  { key: 'failed_quality', label: 'Quality-filtered' },
  { key: 'failed_artifact', label: 'Artifact-filtered' },
  { key: 'passed', label: 'Analysed' },
];

// Serialize repeated params (category/impact/effect/clinvar arrays) as
// `key=a&key=b`, which the FastAPI `Query(None)` list params expect.
const niptParamsSerializer = (params: Record<string, unknown>): string => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)));
    } else if (value !== undefined && value !== null && value !== '') {
      search.append(key, String(value));
    }
  });
  return search.toString();
};

// Map the shared small-variant filter state to the /nipt/variants params. Only
// the NIPT-supported keys are emitted (no per-sample genotype / review-state /
// phenotype params); the maternal/fetal category + inheritance preset + min
// confidence are NIPT-specific.
const buildVariantParams = (f: SmallFilterState, page: number): Record<string, unknown> => {
  const params: Record<string, unknown> = { page, page_size: PAGE_SIZE };
  const str = (key: string, value: string) => {
    if (value && value.trim()) params[key] = value.trim();
  };
  const numeric = (key: string, value: string) => {
    if (value && value.trim()) params[key] = Number(value);
  };
  const flag = (key: string, value: string) => {
    if (value === 'true') params[key] = true;
  };
  const listValues = (key: string, value: string) => {
    const values = parseCommaSeparatedValues(value);
    if (values.length) params[key] = values;
  };

  str('gene', f.gene);
  str('exclude_gene', f.exclude_gene);
  str('panel_id', f.panel_id);
  str('intervals', f.intervals);
  str('exclude_intervals', f.exclude_intervals);
  str('chr', f.chr);
  numeric('start', f.start);
  numeric('end', f.end);
  numeric('ps', f.ps);
  str('type', f.type);
  str('source', f.source);
  str('transcript', f.transcript);
  str('rsid', f.rsid);
  str('hgvsc', f.hgvsc);
  str('hgvsp', f.hgvsp);
  str('sift', f.sift);
  str('polyphen', f.polyphen);
  listValues('impact', f.impact);
  listValues('effect', f.effect);
  listValues('clinvar', f.clinvar);
  listValues('exclude_clinvar', f.exclude_clinvar);
  flag('clinvar_overrides_frequency', f.clinvar_overrides_frequency);
  numeric('max_gnomad_af', f.max_gnomad_af);
  numeric('max_gnomad_exomes_af', f.max_gnomad_exomes_af);
  numeric('max_gnomad_genomes_af', f.max_gnomad_genomes_af);
  numeric('max_gnomad_popmax_af', f.max_gnomad_popmax_af);
  numeric('max_topmed_af', f.max_topmed_af);
  numeric('max_gnomad_ac', f.max_gnomad_ac);
  numeric('max_gnomad_hom_count', f.max_gnomad_hom_count);
  numeric('max_gnomad_hemi_count', f.max_gnomad_hemi_count);
  numeric('min_cadd', f.min_cadd);
  numeric('min_revel', f.min_revel);
  numeric('min_spliceai', f.min_spliceai);
  flag('canonical_only', f.canonical_only);
  flag('mane_only', f.mane_only);
  flag('lof_only', f.lof_only);

  const categories = parseCommaSeparatedValues(f.category)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (categories.length) params.category = categories;
  numeric('min_confidence', f.min_confidence);
  if (f.inheritance) params.inheritance = f.inheritance;
  return params;
};

const FamilyNiptPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  const [presetFeedback, setPresetFeedback] = useState<
    { tone: 'error' | 'success'; message: string } | null
  >(null);

  const { data: family, isLoading: familyLoading, isError: familyError } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const isMonogenicNipt =
    (family?.metadata as { analysis_type?: string } | undefined)?.analysis_type ===
    MONOGENIC_NIPT_ANALYSIS_TYPE;

  const { speciesName, assemblyName, assemblyVersion, projectId } = useFamilyReference(
    family?.projects as string[] | undefined,
  );

  // The shared small-variant filter state drives the reused SmallVariantFilterForm.
  const {
    activeFilterChips,
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
    sampleDraftFilters,
    sampleFilters,
    setDraftFilterValue,
    toggleDraftFilterListValue,
  } = useSmallVariantSearchState({
    family,
    locationSearch: location.search,
    navigate,
    resolvedProjectId: projectId,
  });

  const { data: panels = [] } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

  const { data: summary } = useQuery<ApiNiptSummary>({
    queryKey: ['family', familyId, 'nipt', 'summary'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/nipt/summary`);
      return res.data as ApiNiptSummary;
    },
  });

  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', projectId || null],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });

  // Custom saved filter settings reuse the small-variant preset store (per
  // family); NIPT filters live in the shared filter state, so they round-trip.
  const { data: presets = [] } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'small-variant-filter-presets'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-filter-presets`);
      return res.data as SmallVariantFilterPreset[];
    },
  });

  const savePresetMutation = useMutation({
    mutationFn: async (payload: { name: string; description?: string }) => {
      if (!familyId) {
        throw new Error('Family id is required');
      }
      const res = await api.post(`/families/${familyId}/small-variant-filter-presets`, {
        ...payload,
        scope: 'global',
        ...buildPresetPayload({ filters, members, sampleFilters }),
      });
      return res.data as SmallVariantFilterPreset;
    },
    onSuccess: async () => {
      setPresetFeedback({ tone: 'success', message: 'Saved search updated.' });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'small-variant-filter-presets'],
      });
    },
    onError: (error) => {
      setPresetFeedback({
        tone: 'error',
        message: getErrorMessage(error, 'Unable to save this search preset'),
      });
    },
  });

  const { data: coverage } = useQuery<ApiNiptCoverageSummary>({
    queryKey: ['family', familyId, 'nipt', 'coverage', filters.panel_id, filters.gene],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (filters.panel_id) params.panel_id = filters.panel_id;
      if (filters.gene.trim()) params.gene = filters.gene.trim();
      const res = await api.get(`/families/${familyId}/nipt/coverage`, { params });
      return res.data as ApiNiptCoverageSummary;
    },
  });

  const niptVariantsKey = ['family', familyId, 'nipt', 'variants'] as const;
  const { data: variantPage, isFetching: variantsFetching } = useQuery<SmallVariantPage>({
    queryKey: [...niptVariantsKey, JSON.stringify(filters), page],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/nipt/variants`, {
        params: buildVariantParams(filters, page),
        paramsSerializer: niptParamsSerializer,
      });
      return res.data as SmallVariantPage;
    },
    placeholderData: keepPreviousData,
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
      return res.data as SmallVariantReview;
    },
    onMutate: async ({ variant, payload }) => {
      await queryClient.cancelQueries({ queryKey: niptVariantsKey });
      const snapshots = queryClient.getQueriesData<SmallVariantPage>({ queryKey: niptVariantsKey });
      const optimisticReview = buildOptimisticReview(variant, payload);
      snapshots.forEach(([key]) => {
        queryClient.setQueryData<SmallVariantPage>(key, (current) =>
          updateSmallVariantPageReview(current, variant._id, optimisticReview),
        );
      });
      return { snapshots };
    },
    onSuccess: (review, { variant }) => {
      queryClient.getQueriesData<SmallVariantPage>({ queryKey: niptVariantsKey }).forEach(([key]) => {
        queryClient.setQueryData<SmallVariantPage>(key, (current) =>
          updateSmallVariantPageReview(current, variant._id, hasReviewContent(review) ? review : null),
        );
      });
      void queryClient.invalidateQueries({ queryKey: niptVariantsKey });
    },
    onError: (_error, _variables, context) => {
      context?.snapshots.forEach(([key, snapshot]) => {
        queryClient.setQueryData(key, snapshot);
      });
    },
  });

  const pedRows = useMemo(() => parsePedigree(family?.pedigree), [family?.pedigree]);
  const categoryCounts = summary?.category_counts ?? {};

  if (familyLoading) {
    return <PageState kicker="Monogenic NIPT" title="Loading family…" />;
  }
  if (familyError || !family) {
    return (
      <PageState
        kicker="Monogenic NIPT"
        title="Family not found"
        message="This family could not be loaded."
        action={<Link className="button-secondary" to="/families">Back to families</Link>}
      />
    );
  }
  if (!isMonogenicNipt) {
    return (
      <PageState
        kicker="Monogenic NIPT"
        title="Not a monogenic NIPT family"
        message={`Family ${familyId} is not configured for monogenic NIPT analysis.`}
        action={
          <Link className="button-secondary" to={`/families/${familyId}`}>
            Back to family
          </Link>
        }
      />
    );
  }

  const ff = summary?.fetal_fraction;
  const totalVariants = variantPage?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(totalVariants / PAGE_SIZE));

  // Carry the active panel / gene / project context into the report so its
  // coverage QC and candidate list match what the analyst is viewing.
  const niptReportQuerySuffix = (() => {
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    if (filters.panel_id) params.set('panel_id', filters.panel_id);
    if (filters.gene.trim()) params.set('gene', filters.gene.trim());
    const query = params.toString();
    return query ? `?${query}` : '';
  })();

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card variant-workbench-card">
        <div className={`page-top-card-grid${pedRows.length ? ' page-top-card-grid--with-visual' : ''}`}>
          <div className="page-top-card-copy">
            <div className="page-header">
              <div className="space-y-2">
                <p className="page-kicker">Monogenic NIPT</p>
                <h1 className="catalog-card-title">Family {familyId}</h1>
                <div className="variant-sample-summary">
                  <div className="variant-summary-row">
                    <span className="badge-chip badge-chip--signature">Fetal fraction {pct(ff?.ff)}</span>
                    {ff?.ci_low != null && ff?.ci_high != null ? (
                      <span className="badge-chip">95% CI {pct(ff.ci_low)}–{pct(ff.ci_high)}</span>
                    ) : null}
                    {ff ? (
                      <span className="badge-chip" title={ff.method}>
                        {ff.n_sites} cat-7 sites
                      </span>
                    ) : null}
                    {ff?.low_confidence ? <span className="badge-chip">Low-confidence FF</span> : null}
                    {ff?.ff_external != null ? (
                      <span className="badge-chip">External FF {pct(ff.ff_external)}</span>
                    ) : null}
                    {ff?.disagreement ? <span className="badge-chip">FF disagreement</span> : null}
                  </div>
                  <div className="variant-summary-row">
                    {FILTER_STEPS.map((step) => (
                      <span className="badge-chip" key={step.key}>
                        {step.label} {summary?.filter_counts?.[step.key] ?? 0}
                      </span>
                    ))}
                    <span className="badge-chip">Active filters {activeFilterChips.length}</span>
                    {variantsFetching ? <span className="badge-chip">Updating…</span> : null}
                  </div>
                </div>
              </div>
              <div className="inline-actions">
                <Link to={`/families/${familyId}`} className="button-ghost hover:no-underline">
                  Family
                </Link>
                <Link
                  to={`/families/${familyId}/nipt/report${niptReportQuerySuffix}`}
                  className="button-secondary hover:no-underline"
                >
                  Report
                </Link>
              </div>
            </div>
          </div>
          {pedRows.length > 0 && (
            <div className="page-top-card-visual">
              <div className="page-top-card-pedigree">
                <p className="analysis-section-title">Pedigree</p>
                <Pedigree rows={pedRows} members={family.members} relationships={family.relationships ?? []} />
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
            <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            <span>{filtersCollapsed ? 'Show filters' : 'Hide filters'}</span>
          </button>
        </div>

        {!filtersCollapsed && (
          <SmallVariantFilterForm
            mode="nipt"
            familyAware={false}
            categoryCounts={categoryCounts}
            categoryLabels={CATEGORY_LABELS}
            niptInheritancePresets={INHERITANCE_PRESETS}
            builtInPresets={NIPT_BUILT_IN_PRESETS}
            activeFilterChips={activeFilterChips}
            applyPreset={applyPreset}
            applySavedPreset={applySavedPreset}
            draftFilters={draftFilters}
            handleApply={handleApply}
            handleGtToggle={handleGtToggle}
            handleReset={handleReset}
            handleSampleFieldChange={handleSampleFieldChange}
            members={members}
            relationships={family.relationships ?? []}
            panels={panels}
            presets={presets}
            removeActiveFilterChip={removeActiveFilterChip}
            sampleDraftFilters={sampleDraftFilters}
            setDraftFilterValue={setDraftFilterValue}
            tags={tags}
            toggleDraftFilterListValue={toggleDraftFilterListValue}
            savingPreset={savePresetMutation.isPending}
            feedback={presetFeedback}
            onSaveCurrentPreset={async (payload) => {
              await savePresetMutation.mutateAsync(payload);
            }}
          />
        )}
      </section>

      <div className="variant-results-region">
        <SmallVariantResults
          assemblyName={assemblyName}
          assemblyVersion={assemblyVersion}
          data={variantPage}
          familyId={familyId}
          locationSearch={location.search}
          members={members}
          onPageChange={goToPage}
          page={page}
          projectId={projectId}
          requestQueryString=""
          speciesName={speciesName}
          totalPages={pageCount}
          tags={tags}
          showCsvExport={false}
          reviewIsPending={reviewMutation.isPending}
          reviewError={
            reviewMutation.isError
              ? getErrorMessage(reviewMutation.error, 'Unable to save the variant review')
              : null
          }
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
                  normalizeReviewClassification(
                    variant.review?.classification,
                    variant.review?.tags,
                  ) || undefined,
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

        <section className="surface-card space-y-2" aria-label="On-target coverage">
          <h2 className="section-title">On-target coverage</h2>
          {coverage ? (
            coverage.target_region_count === 0 ? (
              <p className="table-subtle">
                No target regions — set a family ROI or gene panel to report coverage.
              </p>
            ) : (
              <>
                <div className="family-workspace-summary">
                  <div className="family-workspace-stat">
                    <span className="family-workspace-stat-value">{depth(coverage.overall_median_on_target)}</span>
                    <span className="family-workspace-stat-copy">
                      Median on-target ({coverage.target_region_count} region
                      {coverage.target_region_count === 1 ? '' : 's'})
                    </span>
                  </div>
                </div>
                {coverage.low_coverage_regions && coverage.low_coverage_regions.length > 0 ? (
                  <div className="nipt-coverage-qc" role="status">
                    <p className="table-subtle">
                      {coverage.low_coverage_regions.length} of {coverage.target_region_count} panel gene
                      {coverage.target_region_count === 1 ? '' : 's'} below QC (median &lt;{' '}
                      {depth(coverage.min_depth)} or incomplete coverage):
                    </p>
                    <div className="table-chip-list">
                      {coverage.low_coverage_regions.map((region) => (
                        <span
                          key={`${region.label}:${region.chr}`}
                          className={lowCoverageChipClass(region.reason)}
                          title={`${region.label}: ${lowCoverageDetail(region)}`}
                        >
                          {region.label} · {lowCoverageDetail(region)}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="table-subtle">
                    All {coverage.target_region_count} panel gene
                    {coverage.target_region_count === 1 ? '' : 's'} adequately covered (≥ {depth(coverage.min_depth)}).
                  </p>
                )}
              </>
            )
          ) : (
            <p className="table-subtle">Loading coverage…</p>
          )}
        </section>
      </div>
    </div>
  );
};

export default FamilyNiptPage;
