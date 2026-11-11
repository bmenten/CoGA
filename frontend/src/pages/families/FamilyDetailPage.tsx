import React, { useEffect, useMemo, useState } from 'react';
import { useParams, Link, useLocation } from 'react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import MonarchPhenotypeMatchPanel from './MonarchPhenotypeMatchPanel';
import { phaseCorrectionsFromMetadata } from '../../lib/haplotypePhaseCorrections';
import { useEmbryoSegregation } from '../../lib/useEmbryoSegregation';
import FamilyMembersCard from './FamilyMembersCard';
import type {
  ApiFamilyRecord,
  ApiHpoAnnotation,
  ApiSmallVariantReviewSummary,
} from '../../lib/apiTypes';
import type { FindingReviewSummaryOut } from '../../lib/apiSchema.generated';
import FamilyCaseHeader from './FamilyCaseHeader';
import { useFamilyDataPresence } from './familyDataPresence';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import { isAdmin } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import { getTagDefinitionMap, type SmallVariantTagDefinition } from './smallVariantSearch';
import { getReviewTagStyle } from './smallVariantResultUtils';
import { fetchFamilyReviewTags } from './smallVariantReview';
import { MONOGENIC_NIPT_ANALYSIS_TYPE } from './niptClassification';
import { formatRegion, getReviewSummaryTags } from './familyDetailHelpers';
import PipelineSettingsPanel, { pipelineSettingsFromMetadata } from './PipelineSettingsPanel';
import { apiPath, raw } from '../../lib/apiPath';
import { routePath } from '../../lib/routePath';

interface FamilyDetailPageProps {
  /**
   * When provided, render this family instead of reading the id from the route.
   * Used to embed the workspace inside the admin Families page.
   */
  familyId?: string;
  /** Drop the standalone page-shell chrome so the workspace nests inside a card. */
  embedded?: boolean;
}

const FamilyDetailPage: React.FC<FamilyDetailPageProps> = ({
  familyId: familyIdProp,
  embedded = false,
}) => {
  const { familyId: familyIdParam } = useParams<{ familyId: string }>();
  const familyId = familyIdProp ?? familyIdParam;
  const location = useLocation();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const userIsAdmin = isAdmin();
  // Setting the ROI (PUT /families/{id}/roi) is an administrator's, like editing the members.
  const canEditRoi = userIsAdmin;
  const queryClient = useQueryClient();
  // Haplotype lineage colours and embryo segregation depend on the pedigree roles and
  // affected status. Those queries never go stale on their own (staleTime: Infinity), so
  // a structure or member edit must drop them — otherwise embryo classification pairs
  // the new roles with pre-edit haplotypes until a reload.
  const invalidatePedigreeDependentQueries = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['haplotypes', familyId] }),
      queryClient.invalidateQueries({ queryKey: ['phased-markers', familyId] }),
      queryClient.invalidateQueries({ queryKey: ['genome-haplotypes'] }),
      queryClient.invalidateQueries({ queryKey: ['pgt-analysis', familyId] }),
    ]);
  const [roiInput, setRoiInput] = useState('');
  const [roiBusy, setRoiBusy] = useState(false);
  const [roiStatus, setRoiStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(
    null,
  );
  const {
    data,
    isLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as ApiFamilyRecord;
    },
  });
  // A reader in the family's projects changes nothing here: the server refuses it, and
  // the controls say so.
  const readOnly = Boolean(data?.read_only);

  const {
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(data?.projects, preferredProjectId);
  const variantCountsReady = Boolean(
    familyId && data && (!(data.projects?.length) || projectId),
  );

  // Which kinds of data the family has: the header's analysis row and the curation lines below
  // read the same checks (useFamilyDataPresence shares their queries).
  const presence = useFamilyDataPresence({ familyId, projectId, ready: variantCountsReady });
  const {
    hasStructuralVariants: hasVariants,
    hasSmallVariants,
    hasRepeatExpansions,
    hasParaphase,
    hasAnyVariantData,
    loaded: variantCountsLoaded,
  } = presence;
  // Monogenic NIPT families surface a dedicated analysis tab (see docs/monogenic-nipt.md).
  const isMonogenicNipt = data?.metadata?.analysis_type === MONOGENIC_NIPT_ANALYSIS_TYPE;
  const assemblyLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion, isError: referenceFailed },
    'Not linked',
  );
  const { data: reviewSummary, isError: reviewSummaryFailed } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'small-variant-review-summary'],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: smallVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });
  const { data: structuralReviewSummary, isError: structuralReviewSummaryFailed } =
    useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'structural-variant-review-summary'],
    enabled: Boolean(familyId && hasVariants),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/structural-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: structuralVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'structural-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasVariants),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });
  // The repeat and Paraphase reviews in the family's assembly, counted for their curation lines.
  const hasFindingReviews = hasRepeatExpansions || hasParaphase;
  const { data: findingReviewSummary, isError: findingReviewSummaryFailed } = useQuery<FindingReviewSummaryOut>({
    queryKey: ['family', familyId, 'finding-review-summary', projectId || null],
    enabled: Boolean(familyId && hasFindingReviews),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/finding-review-summary`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as FindingReviewSummaryOut;
    },
  });
  const { data: findingReviewTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasFindingReviews),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId),
  });
  const { data: hpoAnnotations = [], isError: hpoFailed } = useQuery<ApiHpoAnnotation[]>({
    queryKey: ['family', familyId, 'hpo'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/hpo`);
      return res.data as ApiHpoAnnotation[];
    },
  });

  const smallVariantReviewTagMap = useMemo(() => getTagDefinitionMap(smallVariantTags), [smallVariantTags]);
  const structuralVariantReviewTagMap = useMemo(
    () => getTagDefinitionMap(structuralVariantTags),
    [structuralVariantTags],
  );
  const reviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(reviewSummary?.tag_counts, smallVariantTags);
  }, [reviewSummary?.tag_counts, smallVariantTags]);
  const structuralReviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(structuralReviewSummary?.tag_counts, structuralVariantTags);
  }, [structuralReviewSummary?.tag_counts, structuralVariantTags]);
  const findingReviewTagMap = useMemo(() => getTagDefinitionMap(findingReviewTags), [findingReviewTags]);
  const findingCurationLines = [
    ...(hasRepeatExpansions ? [{ key: 'repeat' as const, label: 'Repeat expansions', noun: 'repeat loci' }] : []),
    ...(hasParaphase ? [{ key: 'paraphase' as const, label: 'Paraphase', noun: 'Paraphase regions' }] : []),
  ];
  const pipelineSettings = useMemo(
    () => pipelineSettingsFromMetadata(data?.metadata),
    [data?.metadata],
  );
  // The embryo calls at the ROI (carrier/affected/unaffected), shown per embryo in the
  // Family members table below: made by the backend from the haplotype analysis, the calls
  // a sign-out of the family freezes.
  const phaseCorrections = useMemo(
    () => phaseCorrectionsFromMetadata(data?.metadata as Record<string, unknown> | undefined),
    [data?.metadata],
  );
  const {
    byEmbryo: embryoSegregation,
    isError: segregationFailed,
    blocksOutdated: haplotypeBlocksOutdated,
    retry: retrySegregation,
  } = useEmbryoSegregation({
    familyId: data?.family_id ?? '',
    roi: data?.roi ?? null,
    members: data?.members ?? [],
    inheritanceModel: (data?.metadata?.pgt as { inheritance_model?: string } | undefined)
      ?.inheritance_model,
    phaseCorrections,
  });
  // A PGT case: phased markers across the ROI only exist where embryos were analysed.
  const hasEmbryos = useMemo(
    () => (data?.members ?? []).some((m) => String(m.role || '').toLowerCase() === 'embryo'),
    [data?.members],
  );
  const hpoAnnotationsBySample = useMemo(() => {
    return hpoAnnotations.reduce<Record<string, ApiHpoAnnotation[]>>((acc, annotation) => {
      acc[annotation.sample_id] = acc[annotation.sample_id] || [];
      acc[annotation.sample_id].push(annotation);
      return acc;
    }, {});
  }, [hpoAnnotations]);
  const phenotypeSampleIds = useMemo(
    () => Object.keys(hpoAnnotationsBySample),
    [hpoAnnotationsBySample],
  );

  useEffect(() => {
    setRoiInput(data?.roi?.query ?? '');
  }, [data?.roi?.query]);

  if (isLoading) {
    return (
      <PageState
        loading
        kicker="Family"
        title="Loading family workspace"
        message="Preparing family metadata, pedigree and navigation links."
      />
    );
  }

  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Family"
        what="Family"
        error={familyError}
        notFoundMessage="This workspace could not resolve the requested family."
        onRetry={() => void refetchFamily()}
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Family"
        title="Family not found"
        message="This workspace could not resolve the requested family."
      />
    );
  }

  // The project's reference too, before anything of the family: the assembly-scope warning
  // shows nothing while it loads, and the workspace never shows the family without "Not
  // validated for clinical use".
  if (referenceLoading) {
    return (
      <PageState
        loading
        kicker="Family"
        title="Loading family workspace"
        message="Preparing family metadata, pedigree and navigation links."
      />
    );
  }

  const saveRoi = async (clear = false) => {
    if (!familyId) return;
    setRoiBusy(true);
    setRoiStatus(null);
    try {
      const response = await api.put(apiPath`/families/${familyId}/roi`, {
        query: clear ? '' : roiInput,
        project_id: projectId,
      });
      const updatedFamily = response.data as ApiFamilyRecord;
      queryClient.setQueryData(['family', familyId], updatedFamily);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      setRoiInput(updatedFamily.roi?.query ?? '');
      setRoiStatus({
        tone: 'success',
        message: clear
          ? 'Family ROI cleared.'
          : `Family ROI saved for ${updatedFamily.roi?.label ?? 'the selected locus'}.`,
      });
    } catch (error) {
      setRoiStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family ROI.'),
      });
    } finally {
      setRoiBusy(false);
    }
  };

  return (
    <div
      className={`family-detail-page space-y-6${embedded ? '' : ' page-shell'}`}
    >
      <FamilyCaseHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        assemblyLabel={assemblyLabel}
        kicker="Family workspace"
        familyId={data.family_id}
        family={data}
        projectId={projectId}
        isWorkspace
        phenotypeSampleIds={phenotypeSampleIds}
      />

      <section className="family-workspace-grid">
        <article className="surface-card-flat family-workspace-card family-workspace-card--variants">
          <div className="family-workspace-card-head">
            <div className="space-y-1">
              <h2 className="section-title">Curation</h2>
              {/* The analyses themselves are in the top card's row, on every family page. */}
              <p className="family-workspace-card-subtitle">The review so far, per kind of variant</p>
            </div>
          </div>
          {!variantCountsLoaded && (
            <p className="dashboard-link-note">Checking available family data…</p>
          )}
          {variantCountsLoaded && !hasAnyVariantData && !isMonogenicNipt && (
            <p className="dashboard-link-note">
              No variant data is loaded for this family yet — each type activates
              automatically once its data is imported.
            </p>
          )}
          {/* Curation so far, a quiet line per reviewable variant type under the analyses
              rather than between them. */}
          {(hasSmallVariants || hasVariants || hasFindingReviews) && (
            <div className="family-curation-summary">
              {hasSmallVariants && (
                <div className="family-curation-line" role="group" aria-label="Small variant curation">
                  <span className="family-curation-type">Small variants</span>
                  <span className="family-curation-count">
                    Reviewed <strong>{reviewSummaryFailed ? '—' : (reviewSummary?.reviewed_variant_count ?? 0)}</strong>
                  </span>
                  <span className="family-curation-count">
                    Notes <strong>{reviewSummaryFailed ? '—' : (reviewSummary?.note_count ?? 0)}</strong>
                  </span>
                  {reviewSummaryTags.map((tag) => (
                    <span
                      key={tag.key}
                      className="table-chip table-chip--tag family-review-summary-chip"
                      style={getReviewTagStyle(tag.key, smallVariantReviewTagMap)}
                      title={`${tag.count} small variants tagged ${tag.label}`}
                    >
                      {tag.label} <strong>{tag.count}</strong>
                    </span>
                  ))}
                </div>
              )}
              {hasVariants && (
                <div className="family-curation-line" role="group" aria-label="Structural variant curation">
                  <span className="family-curation-type">Structural variants</span>
                  <span className="family-curation-count">
                    Reviewed <strong>{structuralReviewSummaryFailed ? '—' : (structuralReviewSummary?.reviewed_variant_count ?? 0)}</strong>
                  </span>
                  <span className="family-curation-count">
                    Notes <strong>{structuralReviewSummaryFailed ? '—' : (structuralReviewSummary?.note_count ?? 0)}</strong>
                  </span>
                  {structuralReviewSummaryTags.map((tag) => (
                    <span
                      key={tag.key}
                      className="table-chip table-chip--tag family-review-summary-chip"
                      style={getReviewTagStyle(tag.key, structuralVariantReviewTagMap)}
                      title={`${tag.count} structural variants tagged ${tag.label}`}
                    >
                      {tag.label} <strong>{tag.count}</strong>
                    </span>
                  ))}
                </div>
              )}
              {findingCurationLines.map((line) => {
                const counts = findingReviewSummary?.[line.key];
                const shown = (value: number | undefined) => (findingReviewSummaryFailed ? '—' : (value ?? 0));
                return (
                  <div
                    key={line.key}
                    className="family-curation-line"
                    role="group"
                    aria-label={`${line.label} curation`}
                  >
                    <span className="family-curation-type">{line.label}</span>
                    <span className="family-curation-count">
                      Reviewed <strong>{shown(counts?.reviewed_count)}</strong>
                    </span>
                    <span className="family-curation-count">
                      Classified <strong>{shown(counts?.classified_count)}</strong>
                    </span>
                    <span className="family-curation-count">
                      Notes <strong>{shown(counts?.note_count)}</strong>
                    </span>
                    {getReviewSummaryTags(counts?.tag_counts, findingReviewTags).map((tag) => (
                      <span
                        key={tag.key}
                        className="table-chip table-chip--tag family-review-summary-chip"
                        style={getReviewTagStyle(tag.key, findingReviewTagMap)}
                        title={`${tag.count} ${line.noun} tagged ${tag.label}`}
                      >
                        {tag.label} <strong>{tag.count}</strong>
                      </span>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </article>

        <div className="family-workspace-side">
          <article className="surface-card-flat family-workspace-card family-workspace-card--viz">
            <div className="family-workspace-card-head">
              <div className="space-y-1">
                <h2 className="section-title">Visualization</h2>
                <p className="family-workspace-card-subtitle">Browse the data across the genome</p>
              </div>
            </div>
            {/* The same row of entries as the analyses: views of the whole genome, always
                offered, kept in their own card because they are not tied to one data type. */}
            <nav className="family-module-nav" aria-label="Genome views">
              <Link
                to={routePath`/families/${data.family_id}/genome`}
                className="family-module-link hover:no-underline"
              >
                Genome view
              </Link>
              <Link
                to={routePath`/families/${data.family_id}/chromosome/1`}
                className="family-module-link hover:no-underline"
              >
                Chromosome view
              </Link>
              <Link
                to={routePath`/families/${data.family_id}/circos`}
                className="family-module-link hover:no-underline"
              >
                Circos plot
              </Link>
              <Link
                to={routePath`/families/${data.family_id}/igv`}
                className="family-module-link hover:no-underline"
              >
                IGV viewer
              </Link>
            </nav>
          </article>

          <article className="surface-card-flat family-workspace-card family-workspace-card--roi">
            <div className="family-workspace-card-head">
              {/* With a region set the card says so on the title line and stops there —
                  it is a one-line fact, not a section. */}
              <div className="family-roi-headline">
                <h2 className="section-title">Region of interest</h2>
                {data.roi ? (
                  <Link
                    to={routePath`/families/${data.family_id}/chromosome/${data.roi.chr}?start=${Math.max(
                      0,
                      data.roi.start - 1_000_000,
                    )}&end=${data.roi.end + 1_000_000}${raw(
                      projectId ? `&project_id=${encodeURIComponent(projectId)}` : '',
                    )}`}
                    className="family-roi-link"
                    title={`Open ${data.roi.label} in chromosome view`}
                  >
                    {data.roi.label}
                    <span className="family-roi-region"> · {formatRegion(data.roi)}</span>
                  </Link>
                ) : (
                  <span className="family-workspace-card-subtitle">
                    No region of interest set
                  </span>
                )}
                {/* Phased markers only exist for a PGT case, so the review link only
                    appears where there is something to review. */}
                {data.roi && hasEmbryos && (
                  <Link
                    to={routePath`/families/${data.family_id}/roi-markers${raw(
                      projectId ? `?project_id=${encodeURIComponent(projectId)}` : '',
                    )}`}
                    className="family-roi-link family-roi-markers-link"
                    title="Review all phased markers across the ROI ± 1 Mb"
                  >
                    Review ROI markers →
                  </Link>
                )}
              </div>
            </div>
            {canEditRoi && (
              <div className="family-roi-admin">
                <div className="family-roi-edit">
                  <input
                    type="text"
                    value={roiInput}
                    onChange={(e) => setRoiInput(e.target.value)}
                    placeholder="Gene or locus (e.g. BRCA1)"
                    disabled={roiBusy}
                    aria-label="Region of interest gene or locus"
                  />
                  <button
                    type="button"
                    onClick={() => saveRoi(false)}
                    disabled={roiBusy || roiInput.trim().length === 0}
                  >
                    Save
                  </button>
                  {data.roi && (
                    <button
                      type="button"
                      className="button-ghost"
                      onClick={() => saveRoi(true)}
                      disabled={roiBusy}
                    >
                      Clear
                    </button>
                  )}
                </div>
                {roiStatus && (
                  <div
                    className={`status-note ${
                      roiStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                    }`}
                  >
                    {roiStatus.message}
                  </div>
                )}
              </div>
            )}
          </article>
        </div>
      </section>

      {/* Keyed by family: another family starts with no edit open. */}
      <FamilyMembersCard
        key={data.family_id}
        family={data}
        familyId={familyId ?? data.family_id}
        canEditMembers={userIsAdmin && !readOnly}
        canEditPhenotypes={!readOnly}
        hpoAnnotationsBySample={hpoAnnotationsBySample}
        hpoFailed={hpoFailed}
        embryoSegregation={embryoSegregation}
        segregationFailed={segregationFailed}
        retrySegregation={retrySegregation}
        haplotypeBlocksOutdated={haplotypeBlocksOutdated}
        onPedigreeChanged={invalidatePedigreeDependentQueries}
      />

      <MonarchPhenotypeMatchPanel familyId={familyId} projectId={projectId} />

      {/* Last on the page, as on the clinical report: how the data was produced is
          context for everything above it, not a starting point. The workspace variant
          differs only in its lead sentence — there is no "Modules & versions" footer
          here to point at. */}
      <PipelineSettingsPanel familyId={data.family_id} settings={pipelineSettings} variant="workspace" />

    </div>
  );
};

export default FamilyDetailPage;
