import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isReviewConflict, withReviewVersion } from '../../lib/reviewConcurrency';
import { getErrorMessage } from '../../lib/errorMessage';
import { useFamilyReference } from '../../lib/reference';
import type {
  ApiNiptCoverageSummary,
  ApiNiptSummary,
  ApiSmallVariantReviewSummary,
} from '../../lib/apiTypes';
import {
  CATEGORY_LABELS,
  MONOGENIC_NIPT_ANALYSIS_TYPE,
  depth,
  pct,
} from './niptClassification';
import { parseCommaSeparatedValues } from '../../lib/sampleFilterState';
import { parsePedigree } from '../../lib/pedigree';
import {
  buildPresetPayload,
  createEmptySmallFilters,
  describeUnappliedFilters,
  isNiptInheritanceView,
  NIPT_BUILT_IN_PRESETS,
  NIPT_INHERITANCE_LABELS,
  NIPT_INHERITANCE_VIEWS,
  NIPT_UNSUPPORTED_FILTERS,
  niptAppliesFilterChip,
  REPORT_TAG_KEY,
  useSmallVariantSearchState,
  type GenePanel,
  type NiptSmallVariantPage,
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
  fetchFamilyReviewTags,
  hasReviewContent,
  quickTagTogglePayload,
  updateSmallVariantPageReview,
} from './smallVariantReview';
import Pedigree from '../../components/visualizations/Pedigree';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyPageBanners from '../../components/FamilyPageBanners';
import QueryFailure from '../../components/QueryFailure';
import SmallVariantFilterForm from './SmallVariantFilterForm';
import FilterCollapseToggle from './FilterCollapseToggle';
import SmallVariantResults from './SmallVariantResults';
import NiptQcPanel from './NiptQcPanel';
import NiptRecessiveGenes from './NiptRecessiveGenes';
import NiptTargetCoverage from './NiptTargetCoverage';
import VariantResultsLoading from './VariantResultsLoading';
import { apiPath } from '../../lib/apiPath';
import NiptCoverageNotes from './NiptCoverageNotes';
import { signOutRefusal, useReportSignOut } from './reportSignOut';
import { useReportSignoutCheck, useReportSignouts } from './reportSignoutQueries';
import {
  describeReportSections,
  formatReportTime,
  LIVE_REPORT_SIGN_OFF_STATEMENT,
  liveReportSignedState,
  NIPT_NOT_CLASSIFIED_REASONS,
  notComparedSentence,
} from './signedReportRecord';
import { raw, routePath } from '../../lib/routePath';

const PAGE_SIZE = 50;

// The inheritance views of the variant list (NIPT_INHERITANCE_VIEWS, the ones the server
// takes). Picking one clears the category ticks (`categories: ''`): a view reads the fetal
// inheritance itself, and a category tick would narrow it (a de novo candidate whose father's
// call is too weak to tell it from a paternal allele is category 7). "Any" leaves whatever is
// checked.
const INHERITANCE_PRESETS: { value: string; label: string; categories?: string }[] = [
  { value: '', label: 'Any inheritance' },
  ...NIPT_INHERITANCE_VIEWS.map((value) => ({ value, label: NIPT_INHERITANCE_LABELS[value], categories: '' })),
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
// the NIPT-supported keys are emitted: the review tags and excluded review tags by the
// small-variant list's names, but no per-sample genotype, review classification,
// saved-notes or phenotype params; the maternal/fetal category + inheritance view + min
// confidence are NIPT-specific. An inheritance mode that is not one of the list's views, from
// a saved search made on the small-variant list or a link, is not sent: the server refused
// the whole search for it (400).
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
  listValues('review_tag', f.review_tags);
  listValues('exclude_review_tag', f.exclude_review_tags);
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
  if (isNiptInheritanceView(f.inheritance)) params.inheritance = f.inheritance;
  if (f.include_not_inherited === 'true') params.include_not_inherited = true;
  str('de_novo_priority', f.de_novo_priority);
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

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const isMonogenicNipt =
    (family?.metadata as { analysis_type?: string } | undefined)?.analysis_type ===
    MONOGENIC_NIPT_ANALYSIS_TYPE;

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects as string[] | undefined);

  // The shared small-variant filter state drives the reused SmallVariantFilterForm.
  const {
    activeFilterChips,
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
    runSearch,
    sampleDraftFilters,
    sampleFilters,
    searchReady,
    setDraftFilterValue,
    toggleDraftFilterListValue,
  } = useSmallVariantSearchState({
    family,
    locationSearch: location.search,
    navigate,
    resolvedProjectId: projectId,
    // Opens on the De novo preset: the small-variant page's Phenotype-priority default
    // rests on genotype and phenotype filters that do not apply to cfDNA.
    freshOpenPreset: 'nipt_de_novo',
  });
  // Chipped and counted: the filters the NIPT list applies. A saved search made on the
  // small-variant list, or a link, may carry others, such as a classification: they showed as
  // chips the list did not apply (ENG-19). They are named once instead.
  const appliedFilterChips = useMemo(() => activeFilterChips.filter(niptAppliesFilterChip), [activeFilterChips]);
  const unappliedFiltersNote = useMemo(
    () => describeUnappliedFilters('NIPT list', activeFilterChips.filter((chip) => !niptAppliesFilterChip(chip))),
    [activeFilterChips],
  );

  const {
    data: panels = [],
    isError: panelsFailed,
    error: panelsError,
    refetch: refetchPanels,
  } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

  const {
    data: summary,
    isError: summaryFailed,
    error: summaryError,
    refetch: refetchSummary,
  } = useQuery<ApiNiptSummary>({
    queryKey: ['family', familyId, 'nipt', 'summary'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/nipt/summary`);
      return res.data as ApiNiptSummary;
    },
  });

  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', projectId || null],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });

  // How many variants are tagged report, for "Show reported": the family's review summary,
  // which counts a tag's variants as the review-tag filter selects them (the family page's
  // curation summary, under the same key).
  const reviewSummaryKey = ['family', familyId, 'small-variant-review-summary'] as const;
  const { data: reviewSummary } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: reviewSummaryKey,
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });

  // Custom saved filter settings reuse the small-variant preset store (per
  // family); NIPT filters live in the shared filter state, so they round-trip.
  const { data: presets = [] } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['family', familyId, 'small-variant-filter-presets'],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variant-filter-presets`);
      return res.data as SmallVariantFilterPreset[];
    },
  });

  const savePresetMutation = useMutation({
    mutationFn: async (payload: { name: string; description?: string }) => {
      if (!familyId) {
        throw new Error('Family id is required');
      }
      const res = await api.post(apiPath`/families/${familyId}/small-variant-filter-presets`, {
        ...payload,
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

  const {
    data: coverage,
    isError: coverageFailed,
    error: coverageError,
    refetch: refetchCoverage,
  } = useQuery<ApiNiptCoverageSummary>({
    queryKey: ['family', familyId, 'nipt', 'coverage', filters.panel_id, filters.gene],
    enabled: Boolean(familyId && isMonogenicNipt),
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (filters.panel_id) params.panel_id = filters.panel_id;
      if (filters.gene.trim()) params.gene = filters.gene.trim();
      const res = await api.get(apiPath`/families/${familyId}/nipt/coverage`, { params });
      return res.data as ApiNiptCoverageSummary;
    },
  });

  const niptVariantsKey = ['family', familyId, 'nipt', 'variants'] as const;
  const {
    data: variantPage,
    isFetching: variantsFetching,
    isError: variantsFailed,
    error: variantsError,
    refetch: refetchVariants,
  } = useQuery<SmallVariantPage>({
    queryKey: [...niptVariantsKey, JSON.stringify(filters), page],
    // Wait for the De novo default (or the URL's search), so no unfiltered search goes out.
    enabled: Boolean(familyId && isMonogenicNipt && searchReady),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/nipt/variants`, {
        params: buildVariantParams(filters, page),
        paramsSerializer: niptParamsSerializer,
      });
      return res.data as NiptSmallVariantPage;
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
      const body = withReviewVersion(payload, variant.review);
      const res = projectId
        ? await api.put(reviewPath, body, { params: { project_id: projectId } })
        : await api.put(reviewPath, body);
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
      // A tag added or removed changes the count "Show reported" gives.
      void queryClient.invalidateQueries({ queryKey: reviewSummaryKey });
    },
    onError: (error, _variables, context) => {
      context?.snapshots.forEach(([key, snapshot]) => {
        queryClient.setQueryData(key, snapshot);
      });
      // Someone else saved this review meanwhile: show theirs rather than a stale copy.
      if (isReviewConflict(error)) {
        void queryClient.invalidateQueries({ queryKey: niptVariantsKey });
      }
    },
  });

  // The NIPT analysis is signed out through the family sign-out, its gates and report code:
  // one record per analysis, which holds the NIPT results (signed_record_nipt). The signed
  // version opens on the report page, which renders it from its record.
  const {
    data: signouts,
    isError: signoutsFailed,
    isPending: signoutsPending,
    refetch: refetchSignouts,
  } = useReportSignouts(isMonogenicNipt ? familyId : undefined);
  const latestSignout = signouts?.latest ?? null;
  const { data: signoutCheck, isError: signoutCheckFailed } = useReportSignoutCheck(
    familyId,
    latestSignout?.version ?? null,
  );
  const signedVersionLink = (version: number) => {
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    params.set('version', String(version));
    return { pathname: routePath`/families/${familyId}/report`, search: `?${params.toString()}` };
  };
  const {
    signOut,
    isPending: signOutPending,
    error: signOutError,
    dialogs: signOutDialogs,
  } = useReportSignOut(
    familyId,
    (version) => navigate(signedVersionLink(version), { state: { signedOut: { version } } }),
    // A reader in the family's project signs nothing out (SEC-1).
    { readOnly: Boolean(family?.read_only) },
  );

  const pedRows = useMemo(() => parsePedigree(family?.pedigree), [family?.pedigree]);
  // Unknown, not zero, while the summary could not be loaded (#606).
  const categoryCounts = summaryFailed ? null : (summary?.category_counts ?? {});
  // The variants a sign-out freezes: those tagged report, every other filter cleared (the
  // inheritance view, the categories and the minimum confidence too), each as this
  // analysis classifies it. Unknown, not zero, until the review summary is in.
  const reportedCount = reviewSummary ? (reviewSummary.tag_counts?.[REPORT_TAG_KEY] ?? 0) : null;
  const showReported = () => runSearch({ ...createEmptySmallFilters(), review_tags: REPORT_TAG_KEY });
  const notClassified = variantPage?.not_classified ?? [];

  // The project's reference too: the assembly-scope warning shows nothing while it loads
  // (#515), so the NIPT analysis showed without "Not validated for clinical use" (ENG-12).
  if (familyLoading || referenceLoading) {
    return <PageState loading kicker="Monogenic NIPT" title="Loading family" />;
  }
  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Monogenic NIPT"
        what="Family"
        error={familyError}
        notFoundMessage="This family could not be found."
        onRetry={() => void refetchFamily()}
        action={<Link className="button-secondary" to="/dashboard">Back to the dashboard</Link>}
      />
    );
  }
  if (!family) {
    return (
      <PageState
        kicker="Monogenic NIPT"
        title="Family not found"
        message="This family could not be found."
        action={<Link className="button-secondary" to="/dashboard">Back to the dashboard</Link>}
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
          <Link className="button-secondary" to={routePath`/families/${familyId}`}>
            Back to family
          </Link>
        }
      />
    );
  }

  // Until the fetal fraction and the first classified variants are in, the page waits as
  // the small-variant page does: no candidate is shown without the quality checks it rests
  // on (TF-12 U1).
  if ((!summary && !summaryFailed) || (!variantPage && !variantsFailed)) {
    return (
      <PageState
        loading
        kicker="Monogenic NIPT"
        title="Loading the NIPT analysis"
        message="Estimating the fetal fraction and classifying the plasma's calls against the parents."
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
  // The coverage page reads the same genes; its way back returns to this page as it is
  // now, filters and all (they live in the URL).
  const coverageHref = routePath`/families/${familyId}/nipt/coverage${raw(niptReportQuerySuffix)}`;
  const coverageLinkState = { from: `${location.pathname}${location.search}` };
  // Whether the family's data still matches the latest signed version (sign-out-check), read
  // as the live reports read it.
  const signOutBlocked = signOutRefusal(
    { readOnly: Boolean(family?.read_only), assemblyValidated, signoutsFailed, signoutsPending },
    'analysis',
  );
  const signoutCheckState = liveReportSignedState({
    signoutsFailed,
    signoutsPending,
    latestVersion: latestSignout?.version ?? null,
    checkFailed: signoutCheckFailed,
    check: signoutCheck,
  });
  // What the check could not compare, the signed version not holding it (TRACE-19).
  const notCompared = notComparedSentence(signoutCheck?.not_compared, `signed version ${latestSignout?.version}`);

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
                    {summaryFailed ? (
                      // Not "—": a failed estimate also hides its low-confidence warning
                      // (#606, TF-12 U1).
                      <span className="badge-chip badge-chip--signature" role="alert">
                        Fetal fraction could not be loaded
                      </span>
                    ) : (
                      <span className="badge-chip badge-chip--signature">Fetal fraction {pct(ff?.ff)}</span>
                    )}
                    {ff?.ci_low != null && ff?.ci_high != null ? (
                      <span className="badge-chip">95% CI {pct(ff.ci_low)}–{pct(ff.ci_high)}</span>
                    ) : null}
                    {ff ? (
                      <span className="badge-chip" title={ff.method}>
                        {ff.n_sites} cat-7 sites
                      </span>
                    ) : null}
                    {ff?.low_confidence ? <span className="badge-chip">Low-confidence FF</span> : null}
                  </div>
                  <div className="variant-summary-row">
                    {FILTER_STEPS.map((step) => (
                      <span className="badge-chip" key={step.key}>
                        {step.label} {summaryFailed ? '—' : (summary?.filter_counts?.[step.key] ?? 0)}
                      </span>
                    ))}
                    <span className="badge-chip">Active filters {appliedFilterChips.length}</span>
                    {variantsFetching ? <span className="badge-chip">Updating…</span> : null}
                  </div>
                </div>
              </div>
              <div className="inline-actions">
                <Link to={routePath`/families/${familyId}`} className="button-ghost hover:no-underline">
                  Family
                </Link>
                <Link
                  to={routePath`/families/${familyId}/nipt/report${raw(niptReportQuerySuffix)}`}
                  className="button-secondary hover:no-underline"
                >
                  Report
                </Link>
                <button
                  type="button"
                  className="form-button"
                  onClick={signOut}
                  // As on the report page: not to a reader (SEC-1), nor off the validated scope,
                  // nor while the sign-out record is unknown (the analysis may be signed already).
                  disabled={signOutPending || signOutBlocked !== null}
                  title={signOutBlocked ?? undefined}
                >
                  {signOutPending
                    ? 'Signing out…'
                    : latestSignout
                      ? 'Amend sign-out'
                      : 'Sign out NIPT analysis'}
                </button>
              </div>
            </div>
            {/* As on every family page: a NIPT family can be partly imported like any other,
                or be on an assembly outside the validated scope. */}
            <FamilyPageBanners
              metadata={family.metadata}
              assemblyScope={{
                name: assemblyName,
                validated: assemblyValidated,
                unavailable: referenceFailed,
                onRetry: retryReference,
              }}
            />
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

        <FilterCollapseToggle
          collapsed={filtersCollapsed}
          onToggle={() => setFiltersCollapsed((current) => !current)}
        />

        {!filtersCollapsed && (
          <SmallVariantFilterForm
            mode="nipt"
            familyAware={false}
            unsupportedFilters={NIPT_UNSUPPORTED_FILTERS}
            unappliedFiltersNote={unappliedFiltersNote}
            categoryCounts={categoryCounts}
            categoryLabels={CATEGORY_LABELS}
            niptInheritancePresets={INHERITANCE_PRESETS}
            builtInPresets={NIPT_BUILT_IN_PRESETS}
            activeFilterChips={appliedFilterChips}
            applyPreset={applyPreset}
            applySavedPreset={applySavedPreset}
            draftFilters={draftFilters}
            handleApply={handleApply}
            draftLocationProblems={draftLocationProblems}
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

      {signOutError ? (
        <section className="surface-card report-signout-error" role="alert">
          <p className="report-paragraph">
            <strong>Not signed out.</strong> {signOutError}
          </p>
        </section>
      ) : null}
      {signOutDialogs}

      <section className="surface-card space-y-2" aria-label="Sign-out">
        <h2 className="section-title">Sign-out</h2>
        {signoutsFailed ? (
          <p className="table-subtle" role="alert">
            The sign-out record could not be loaded, so it is not known whether this analysis has been
            signed out.{' '}
            <button type="button" className="button-link" onClick={() => void refetchSignouts()}>
              Retry
            </button>
          </p>
        ) : signoutsPending ? (
          <p className="table-subtle" role="status">
            Looking up the signed versions…
          </p>
        ) : latestSignout ? (
          <p
            className={`table-subtle${signoutCheckState === 'changed' ? ' status-note status-note--warning' : ''}`}
            role={signoutCheckState === 'changed' ? 'alert' : undefined}
          >
            Signed version {latestSignout.version}
            {latestSignout.report_code ? ` (report code ${latestSignout.report_code})` : ''} was signed out by{' '}
            <strong>{latestSignout.signed_out_by}</strong> on {formatReportTime(latestSignout.signed_out_at)}.{' '}
            {signoutCheckState === 'checking' ? 'Checking the family’s current data against it… ' : null}
            {signoutCheckState === 'matches'
              ? notCompared
                ? `The family’s current data still matches it in everything it holds. ${notCompared} `
                : 'The family’s current data still matches it. '
              : null}
            {signoutCheckState === 'changed' ? (
              <>
                <strong>Changed since sign-out:</strong>{' '}
                {describeReportSections(signoutCheck?.changed_sections ?? [])}.{' '}
                {notCompared ? `${notCompared} ` : null}Sign out again to issue a new version.{' '}
              </>
            ) : null}
            {signoutCheckState === 'unverified'
              ? 'Whether the family’s current data still matches it could not be checked. '
              : null}
            <Link to={signedVersionLink(latestSignout.version)}>View signed version {latestSignout.version}</Link>
          </p>
        ) : (
          <p className="table-subtle">This NIPT analysis has not been signed out.</p>
        )}
        <p className="table-subtle">
          {LIVE_REPORT_SIGN_OFF_STATEMENT} The family has one signed record: signing out here or from
          the family report freezes the same analysis, with its NIPT results (the fetal fraction, the
          quality checks, the target coverage, and each variant tagged report as this analysis
          classifies it).
        </p>
        <p className="table-subtle">
          <button type="button" className="button-secondary" onClick={showReported}>
            {reportedCount === null ? 'Show reported' : `Show reported (${reportedCount})`}
          </button>{' '}
          lists the variants tagged report, with every other filter cleared: those a sign-out would freeze.
        </p>
      </section>

      {summary?.qc ? <NiptQcPanel qc={summary.qc} fetalFraction={summary.fetal_fraction} /> : null}

      {summaryFailed ? (
        <QueryFailure
          what="the fetal-fraction estimate"
          error={summaryError}
          onRetry={() => void refetchSummary()}
          consequence="Its low-confidence warning, and the filter and category counts, are unknown. Do not interpret the category calls without it."
        />
      ) : null}
      {panelsFailed ? (
        <QueryFailure
          what="the gene panel list"
          error={panelsError}
          onRetry={() => void refetchPanels()}
          consequence="Panels cannot be chosen."
        />
      ) : null}

      <div className="variant-results-region">
        {variantsFetching ? (
          <VariantResultsLoading
            title="Loading NIPT variants"
            message="Classifying the plasma's calls against the parents for this search."
          />
        ) : null}
        <div className={variantsFetching ? 'variant-results-fetching' : undefined} aria-busy={variantsFetching}>
        {/* The list classifies the first variants of the search, in genomic order, up to a
            limit. Past it the list stops part-way through the genome: said, not shown as
            complete. */}
        {!variantsFailed && variantPage?.total_is_estimated ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--warning mb-4" role="status">
            More variants matched this search than CoGA classifies at once
            {typeof variantPage.count_limit === 'number'
              ? ` (${variantPage.count_limit.toLocaleString()})`
              : ''}
            , so the list stops part-way through the genome and its count is a lower bound. Narrow
            the search with a gene panel, a gene or a region.
          </div>
        ) : null}
        {/* A variant with the selected review tags that the analysis does not classify is in
            no list: named, with why, as the signed record names a reported one. */}
        {!variantsFailed && notClassified.length ? (
          <div
            className="variant-workspace-feedback variant-workspace-feedback--warning mb-4"
            role="status"
            aria-label="Tagged variants not classified"
          >
            <p>
              {notClassified.length === 1
                ? '1 variant with the selected review tags is not in this list: the analysis does not classify it.'
                : `${notClassified.length} variants with the selected review tags are not in this list: the analysis does not classify them.`}{' '}
              A sign-out names a reported one as not classified, with why.
            </p>
            <ul className="list-disc pl-5">
              {notClassified.map((entry) => (
                <li key={entry.variant_id}>
                  <strong>{entry.variant_id}</strong>: not classified —{' '}
                  {NIPT_NOT_CLASSIFIED_REASONS[entry.reason] ?? entry.reason}.
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {!variantsFailed && filters.inheritance === 'recessive_at_risk' && variantPage ? (
          <NiptRecessiveGenes genes={variantPage.recessive_genes ?? []} />
        ) : null}
        {/* A failed search is said as such: it read "No variants match the current
            search" (#606). */}
        {variantsFailed ? (
          <QueryFailure what="the NIPT variants" error={variantsError} onRetry={() => void refetchVariants()} />
        ) : (
        <SmallVariantResults
          assemblyName={assemblyName}
          assemblyVersion={assemblyVersion}
          data={variantPage}
          familyId={familyId}
          locationSearch={location.search}
          members={members}
          relationships={family?.relationships}
          onPageChange={goToPage}
          page={page}
          projectId={projectId}
          requestQueryString=""
          speciesName={speciesName}
          totalPages={pageCount}
          tags={tags}
          showCsvExport={false}
          readOnly={Boolean(family?.read_only)}
          reviewIsPending={reviewMutation.isPending}
          reviewError={
            reviewMutation.isError
              ? getErrorMessage(reviewMutation.error, 'Unable to save the variant review')
              : null
          }
          onToggleReviewTag={async (variant, tagKey) => {
            await reviewMutation.mutateAsync({ variant, payload: quickTagTogglePayload(variant.review, tagKey) });
          }}
          onOpenReview={() => reviewMutation.reset()}
          onSaveReview={async (variant, payload) => {
            await reviewMutation.mutateAsync({ variant, payload });
          }}
        />
        )}
        </div>

        <section className="surface-card space-y-2" aria-label="On-target coverage">
          <h2 className="section-title">On-target coverage</h2>
          {coverage?.targets ? (
            <NiptTargetCoverage
              coverage={coverage.targets}
              scoped={Boolean(filters.panel_id || filters.gene.trim())}
              detailsHref={coverageHref}
              detailsState={coverageLinkState}
            />
          ) : coverage ? (
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
                {/* The regions themselves are named on the coverage page: here, how many. */}
                {coverage.low_coverage_regions && coverage.low_coverage_regions.length > 0 ? (
                  <p className="status-note status-note--warning nipt-coverage-verdict" role="status">
                    {coverage.low_coverage_regions.length} of {coverage.target_region_count} panel gene
                    {coverage.target_region_count === 1 ? '' : 's'} below QC (median &lt; {depth(coverage.min_depth)}{' '}
                    or incomplete coverage).{' '}
                    <Link to={coverageHref} state={coverageLinkState}>
                      Coverage details
                    </Link>
                  </p>
                ) : (
                  <p className="table-subtle nipt-coverage-verdict">
                    All {coverage.target_region_count} panel gene
                    {coverage.target_region_count === 1 ? '' : 's'} adequately covered (≥ {depth(coverage.min_depth)}).{' '}
                    <Link to={coverageHref} state={coverageLinkState}>
                      Coverage details
                    </Link>
                  </p>
                )}
                <NiptCoverageNotes notes={coverage.coverage_notes} />
              </>
            )
          ) : coverageFailed ? (
            // It stayed at "Loading coverage…" for good (#606).
            <QueryFailure what="the coverage QC" error={coverageError} onRetry={() => void refetchCoverage()} />
          ) : (
            <p className="table-subtle" role="status">
              <span className="viz-loading-spinner" aria-hidden="true" /> Loading coverage…
            </p>
          )}
        </section>
      </div>
    </div>
  );
};

export default FamilyNiptPage;
