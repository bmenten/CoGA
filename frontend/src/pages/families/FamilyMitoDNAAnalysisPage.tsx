import React, { useCallback, useMemo, useState } from 'react';
import { useLocation, useParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isReviewConflict, withReviewVersion } from '../../lib/reviewConcurrency';
import { QC_STATUS_LABEL } from '../../lib/qcStatus';
import type {
  ApiFamilyMitoDNAAnalysis,
  ApiFamilyMember,
  ApiFamilyRecord,
  ApiMitoDNASample,
  ApiMitoDNAVariant,
  ApiMitoDNAVariantSampleCall,
} from '../../lib/apiTypes';
import FamilyPageHeader from './FamilyPageHeader';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import { getErrorMessage } from '../../lib/errorMessage';
import AcmgClassificationModal, { type AcmgModalVariant } from './AcmgClassificationModal';
import type { AcmgMitoContext } from '../../lib/acmg';
import type { SmallVariantReview, SmallVariantReviewSavePayload } from './smallVariantSearch';
import { buildSmallVariantReviewPath } from './smallVariantReview';
import { apiPath } from '../../lib/apiPath';
import { FamilyReadOnlyContext } from '../../lib/familyAccess';
import { raw, routePath } from '../../lib/routePath';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import GeneDiseaseHeading from '../../components/GeneDiseaseHeading';
import SampleLabel from '../../components/SampleLabel';
import { omimSearchHref } from '../../lib/omim';

// gnomAD allele-frequency cut-offs offered in the "common variant" filter.
const GNOMAD_AF_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'Any frequency' },
  { value: '0.05', label: '≤ 5% (exclude common)' },
  { value: '0.01', label: '≤ 1%' },
  { value: '0.005', label: '≤ 0.5%' },
  { value: '0.001', label: '≤ 0.1% (rare)' },
];

const isSynonymousVariant = (variant: ApiMitoDNAVariant): boolean =>
  (variant.annotation.consequence_terms ?? []).some((term) => term.includes('synonymous'));

// Adapt an MT variant to the SmallVariant fields the ACMG modal reads. MT
// variants live in the small-variant store, so the modal, its auto-evaluation
// and the shared review endpoint all operate on the same identity. The `mito`
// context carries the mtDNA-specific fields the mt evaluator (McCormick 2020)
// needs — locus category, MITOMAP status, maternal transmission, heteroplasmy.
const toSmallVariantForAcmg = (
  variant: ApiMitoDNAVariant,
  samples: ApiMitoDNASample[],
): AcmgModalVariant => {
  const proband =
    samples.find((s) => (s.role ?? '').toLowerCase() === 'proband') ??
    samples.find((s) => s.affected);
  return {
    _id: variant.variant_id,
    chr: 'MT',
    start: variant.position,
    end: variant.position,
    type: variant.type,
    ref: variant.ref,
    alt: variant.alt,
    rsid: variant.rsid ?? null,
    gene: variant.annotation.gene ?? null,
    gene_id: null,
    hgvsc: null,
    hgvsp: null,
    effect: variant.annotation.consequence ?? null,
    gnomad_af: variant.annotation.gnomad_af ?? null,
    review: variant.review ?? null,
    priority: null,
    genotypes: Object.values(variant.calls).map((call) => ({
      sample: call.sample,
      gt: call.genotype,
      dp: call.depth ?? null,
    })),
    mito: {
      category: (variant.annotation.category ?? null) as AcmgMitoContext['category'],
      clinicalSignificance: variant.annotation.clinical_significance ?? null,
      disorders: variant.annotation.disorders ?? [],
      maternalTransmission: variant.maternal_transmission ?? null,
      probandHaplogroup: proband?.haplogroup ?? null,
      calls: Object.values(variant.calls).map((call) => ({
        sampleId: call.sample,
        role: call.role ?? undefined,
        affected: call.affected ?? undefined,
        zygosity: call.zygosity,
        alleleFraction: call.allele_fraction ?? null,
      })),
    },
  };
};

const samplesToMembers = (samples: ApiMitoDNASample[]): ApiFamilyMember[] =>
  samples.map((sample) => ({
    sample_id: sample.sample_id,
    role: sample.role || 'relative',
    affected: Boolean(sample.affected),
    sex: sample.sex || 'und',
  }));



const formatPercent = (value?: number | null, digits = 1): string =>
  value == null
    ? 'n/a'
    : `${(value * 100).toLocaleString(undefined, {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      })}%`;

const formatNumber = (value?: number | null, digits = 0): string =>
  value == null
    ? 'n/a'
    : value.toLocaleString(undefined, {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      });

const formatDepth = (value?: number | null): string =>
  value == null ? 'n/a' : `${formatNumber(value, 1)}x`;

/**
 * The mitochondrial contig as IGV names it. IGV's hg19, hg38 and hs1 genomes all call it
 * chrM and alias MT to it, so chrM opens the variant whether the reads were aligned to
 * "MT" (GRCh37/b37) or "chrM"; "chrMT", what prefixing the stored "MT" gives, is no name
 * IGV knows. The assembly is not in the link: the IGV page reads the family's project
 * (project_id) and opens that assembly's genome.
 */
export const MITO_IGV_CONTIG = 'chrM';

/** The variant's span on chrM, as the small-variant links give IGV a variant: start-end. */
export const getMitoIgvLocus = (variant: Pick<ApiMitoDNAVariant, 'position' | 'ref'>): string => {
  const start = Math.max(1, variant.position);
  const end = start + Math.max(1, variant.ref?.length || 1) - 1;
  return `${MITO_IGV_CONTIG}:${start}-${end}`;
};

export const buildMitoIgvHref = (
  familyId: string,
  variant: Pick<ApiMitoDNAVariant, 'position' | 'ref'>,
  projectId?: string | null,
): string => {
  const params = new URLSearchParams({ locus: getMitoIgvLocus(variant) });
  if (projectId) params.set('project_id', projectId);
  params.set('back_path', routePath`/families/${familyId}/mitochondrial-dna`);
  return routePath`/families/${familyId}/igv${raw(`?${params.toString()}`)}`;
};

const buildMitoChromosomeHref = (
  familyId: string,
  start: number,
  end: number,
  projectId?: string | null,
): string => {
  const params = new URLSearchParams({
    start: String(Math.max(0, start - 100)),
    end: String(end + 100),
  });
  if (projectId) params.set('project_id', projectId);
  return routePath`/families/${familyId}/chromosome/MT${raw(`?${params.toString()}`)}`;
};

// The card's rail, keyed to the significance as the small-variant cards key it to ClinVar.
const significanceRail = (status: ApiMitoDNAVariant['annotation']['clinical_significance']) => {
  if (status === 'pathogenic' || status === 'likely_pathogenic') return ' variant-card--clinvar-pathogenic';
  if (status === 'benign' || status === 'likely_benign' || status === 'polymorphism') {
    return ' variant-card--clinvar-benign';
  }
  if (status === 'reported' || status === 'uncertain') return ' family-mtdna-card--reported';
  return '';
};

const significanceLabel = (status: ApiMitoDNAVariant['annotation']['clinical_significance']) => {
  switch (status) {
    case 'likely_pathogenic':
      return 'Likely pathogenic';
    case 'likely_benign':
      return 'Likely benign';
    case 'polymorphism':
      return 'Polymorphism';
    case 'reported':
      return 'Reported';
    case 'uncertain':
      return 'Uncertain';
    case 'pathogenic':
      return 'Pathogenic';
    case 'benign':
      return 'Benign';
    default:
      return 'Unknown';
  }
};

const zygosityLabel = (zygosity: ApiMitoDNAVariantSampleCall['zygosity']) => {
  switch (zygosity) {
    case 'homoplasmic':
      return 'Homoplasmic';
    case 'heteroplasmic':
      return 'Heteroplasmic';
    case 'low_level':
      return 'Low-level';
    case 'reference':
      return 'Reference';
    case 'no_call':
      return 'No call';
    default:
      return 'Unknown';
  }
};

const transmissionLabel = (status: ApiMitoDNAVariant['maternal_transmission']) => {
  switch (status) {
    case 'maternal_shared':
      return 'Maternal transmission';
    case 'maternal_not_observed':
      return 'Not seen in mother';
    // No mother linked to the proband, or none with mtDNA data or a call here: her
    // absence says nothing, so these stay out of the maternal review.
    case 'mother_not_assessed':
      return 'Mother not assessed';
    case 'no_proband':
      return 'No proband';
    case 'maternal_only':
      return 'Mother only';
    case 'father_only':
      return 'Father only';
    case 'family_private':
      return 'Family private';
    case 'no_alt_calls':
      return 'No alternate calls';
    default:
      return 'Unknown';
  }
};

const hasAltCall = (call?: ApiMitoDNAVariantSampleCall): boolean =>
  Boolean(call && ['homoplasmic', 'heteroplasmic', 'low_level'].includes(call.zygosity));

const hasHeteroplasmicCall = (variant: ApiMitoDNAVariant): boolean =>
  Object.values(variant.calls).some((call) => call.zygosity === 'heteroplasmic' || call.zygosity === 'low_level');

const hasHomoplasmicCall = (variant: ApiMitoDNAVariant): boolean =>
  Object.values(variant.calls).some((call) => call.zygosity === 'homoplasmic');

const isClinicalVariant = (variant: ApiMitoDNAVariant): boolean =>
  ['pathogenic', 'likely_pathogenic', 'reported', 'uncertain'].includes(
    variant.annotation.clinical_significance,
  ) || variant.annotation.disorders.length > 0;

const orderedByFamilyRole = (samples: ApiMitoDNASample[]): ApiMitoDNASample[] => {
  const ordered = sortFamilyMembersProbandFirst(samplesToMembers(samples));
  const rank = new Map(ordered.map((member, index) => [member.sample_id, index]));
  return [...samples].sort(
    (left, right) => (rank.get(left.sample_id) ?? 999) - (rank.get(right.sample_id) ?? 999),
  );
};

const variantMatchesSearch = (variant: ApiMitoDNAVariant, query: string): boolean => {
  if (!query) return true;
  const haystack = [
    variant.label,
    variant.variant_id,
    variant.rsid,
    variant.annotation.gene,
    variant.annotation.region,
    variant.annotation.consequence,
    significanceLabel(variant.annotation.clinical_significance),
    ...variant.annotation.disorders,
    ...variant.annotation.polymorphism_notes,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(query);
};

// One member's call on the variant: who, the zygosity, the heteroplasmy (the result, so the
// largest figure on the row), then the read depth behind it.
const VariantCallRow: React.FC<{
  call?: ApiMitoDNAVariantSampleCall;
  sample: ApiMitoDNASample;
}> = ({ call, sample }) => (
  <div
    className={`family-mtdna-call family-mtdna-call--${call?.zygosity || 'missing'}${
      hasAltCall(call) ? ' family-mtdna-call--alt' : ''
    }`}
  >
    <SampleLabel sampleId={sample.sample_id} role={sample.role || 'relative'} emphasis={hasAltCall(call)} />
    {call ? (
      <>
        <span className="family-mtdna-call-zygosity">{zygosityLabel(call.zygosity)}</span>
        <strong className="family-mtdna-call-af">{call.display}</strong>
        <span className="family-mtdna-call-depth">
          DP {formatNumber(call.depth)} · AD {formatNumber(call.alt_depth)}
        </span>
      </>
    ) : (
      <span className="table-subtle family-mtdna-call-zygosity">No data</span>
    )}
  </div>
);

const SampleSummaryCard: React.FC<{ sample: ApiMitoDNASample }> = ({ sample }) => (
  <div className={`family-mtdna-sample-card family-mtdna-sample-card--${sample.qc.status}`}>
    <div className="family-mtdna-sample-card-head">
      <div>
        <strong>{sample.sample_id}</strong>
        <div className="table-subtle">{sample.role || 'relative'}</div>
      </div>
      <span className={`table-chip family-mtdna-qc-chip family-mtdna-qc-chip--${sample.qc.status}`}>
        {QC_STATUS_LABEL[sample.qc.status]}
      </span>
    </div>
    <div className="family-mtdna-sample-metrics">
      <span>
        Haplogroup <strong>{sample.haplogroup || 'n/a'}</strong>
      </span>
      <span>
        Mean depth <strong>{formatDepth(sample.coverage.mean_depth)}</strong>
      </span>
      <span>
        Breadth <strong>{formatPercent(sample.coverage.breadth, 0)}</strong>
      </span>
      {sample.qc.contamination != null && (
        <span>
          Contam. <strong>{formatPercent(sample.qc.contamination, 2)}</strong>
        </span>
      )}
    </div>
    {sample.qc.notes.length > 0 && (
      <div className="family-mtdna-qc-notes">{sample.qc.notes.slice(0, 2).join(' · ')}</div>
    )}
  </div>
);

const FamilyMitoDNAAnalysisPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const projectIdParam = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const [searchText, setSearchText] = useState('');
  const [scope, setScope] = useState<'all' | 'clinical' | 'heteroplasmic' | 'homoplasmic' | 'maternal_review'>('all');
  const [maxGnomadAf, setMaxGnomadAf] = useState('');
  const [excludeSynonymous, setExcludeSynonymous] = useState(false);
  const [copiedMitomapVariant, setCopiedMitomapVariant] = useState<string | null>(null);
  const [acmgVariant, setAcmgVariant] = useState<ApiMitoDNAVariant | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}`);
      return response.data as ApiFamilyRecord;
    },
  });

  const {
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectIdParam);

  const {
    data: mtDNA,
    isLoading: mtDNALoading,
    isError: mtDNAFailed,
    error: mtDNAError,
    refetch: refetchMtDNA,
  } = useQuery<ApiFamilyMitoDNAAnalysis>({
    queryKey: ['family', familyId, 'mitochondrial-dna', resolvedProjectId],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}/mitochondrial-dna`, {
        params: resolvedProjectId ? { project_id: resolvedProjectId } : undefined,
      });
      return response.data as ApiFamilyMitoDNAAnalysis;
    },
    enabled: Boolean(familyId && (!family?.projects?.length || !referenceLoading)),
  });

  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion, isError: referenceFailed },
    'Not linked',
  );
  const orderedSamples = useMemo(() => orderedByFamilyRole(mtDNA?.samples || []), [mtDNA?.samples]);
  const { mothers, fathers, maternalLine, children } = useMemo(() => {
    const maternal = orderedSamples.filter((sample) => sample.role !== 'father');
    return {
      mothers: orderedSamples.filter((sample) => sample.role === 'mother'),
      fathers: orderedSamples.filter((sample) => sample.role === 'father'),
      maternalLine: maternal,
      children: maternal.filter((sample) => sample.role !== 'mother'),
    };
  }, [orderedSamples]);

  const gnomadAfThreshold = useMemo(() => {
    const parsed = Number.parseFloat(maxGnomadAf);
    return Number.isFinite(parsed) ? parsed : null;
  }, [maxGnomadAf]);

  const filteredVariants = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    return (mtDNA?.variants || []).filter((variant) => {
      if (!variantMatchesSearch(variant, query)) return false;
      // gnomAD frequency: drop variants whose annotated AF exceeds the cut-off.
      // Variants without an annotated frequency are kept — commonness is unknown.
      if (
        gnomadAfThreshold != null &&
        variant.annotation.gnomad_af != null &&
        variant.annotation.gnomad_af > gnomadAfThreshold
      ) {
        return false;
      }
      if (excludeSynonymous && isSynonymousVariant(variant)) return false;
      if (scope === 'clinical') return isClinicalVariant(variant);
      if (scope === 'heteroplasmic') return hasHeteroplasmicCall(variant);
      if (scope === 'homoplasmic') return hasHomoplasmicCall(variant);
      if (scope === 'maternal_review') {
        return ['maternal_not_observed', 'father_only', 'family_private'].includes(
          variant.maternal_transmission,
        );
      }
      return true;
    });
  }, [mtDNA?.variants, scope, searchText, gnomadAfThreshold, excludeSynonymous]);

  const heteroplasmicCount = useMemo(
    () => (mtDNA?.variants || []).filter(hasHeteroplasmicCall).length,
    [mtDNA?.variants],
  );
  const homoplasmicCount = useMemo(
    () => (mtDNA?.variants || []).filter(hasHomoplasmicCall).length,
    [mtDNA?.variants],
  );
  const clinicalCount = useMemo(
    () => (mtDNA?.variants || []).filter(isClinicalVariant).length,
    [mtDNA?.variants],
  );
  const qcWarningCount = useMemo(
    () => (mtDNA?.samples || []).filter((sample) => sample.qc.status === 'warn' || sample.qc.status === 'fail').length,
    [mtDNA?.samples],
  );
  const openMitomapVariant = useCallback((variant: ApiMitoDNAVariant) => {
    const query = variant.annotation.mitomap_query || variant.label;
    if (navigator.clipboard?.writeText) {
      void navigator.clipboard
        .writeText(query)
        .then(() => {
          setCopiedMitomapVariant(query);
          window.setTimeout(
            () => setCopiedMitomapVariant((current) => (current === query ? null : current)),
            2000,
          );
        })
        .catch(() => undefined);
    }
    window.open(variant.annotation.mitomap_url, '_blank', 'noopener,noreferrer');
  }, []);

  const reviewMutation = useMutation({
    mutationFn: async ({
      variantId,
      review,
      payload,
    }: {
      variantId: string;
      review?: SmallVariantReview | null;
      payload: SmallVariantReviewSavePayload;
    }) => {
      if (!familyId) {
        throw new Error('Family id is required');
      }
      const path = buildSmallVariantReviewPath(familyId, variantId);
      const body = withReviewVersion(payload, review);
      const res = resolvedProjectId
        ? await api.put(path, body, { params: { project_id: resolvedProjectId } })
        : await api.put(path, body);
      return res.data as SmallVariantReview;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'mitochondrial-dna', resolvedProjectId],
      });
    },
  });

  const openAcmgModal = useCallback((variant: ApiMitoDNAVariant) => {
    setReviewError(null);
    setAcmgVariant(variant);
  }, []);

  const handleSaveAcmg = useCallback(
    async (payload: SmallVariantReviewSavePayload) => {
      if (!acmgVariant) return;
      setReviewError(null);
      try {
        await reviewMutation.mutateAsync({
          variantId: acmgVariant.variant_id,
          review: acmgVariant.review,
          payload,
        });
        setAcmgVariant(null);
      } catch (error) {
        setReviewError(getErrorMessage(error, 'Unable to save the ACMG classification.'));
        // Someone else saved this review meanwhile: reload so the current one is shown.
        if (isReviewConflict(error)) {
          void queryClient.invalidateQueries({
            queryKey: ['family', familyId, 'mitochondrial-dna', resolvedProjectId],
          });
        }
        throw error;
      }
    },
    [acmgVariant, reviewMutation, queryClient, familyId, resolvedProjectId],
  );

  if (familyLoading || mtDNALoading || (family?.projects?.length && referenceLoading)) {
    return (
      <PageState
        loading
        kicker="mtDNA"
        title="Loading mtDNA analysis"
        message="Preparing mitochondrial variant calls, haplogroups, coverage and QC."
      />
    );
  }

  if (familyFailed || mtDNAFailed) {
    return (
      <FamilyLoadFailure
        kicker="mtDNA"
        what={familyFailed ? 'Family' : 'mtDNA analysis'}
        error={familyFailed ? familyError : mtDNAError}
        notFoundMessage="This mtDNA workspace could not resolve the requested family."
        onRetry={() => {
          if (familyFailed) void refetchFamily();
          if (mtDNAFailed) void refetchMtDNA();
        }}
      />
    );
  }

  if (!family || !mtDNA) {
    return (
      <PageState
        kicker="mtDNA"
        title="Family not found"
        message="This mtDNA workspace could not resolve the requested family."
      />
    );
  }

  return (
    <div className="page-shell family-mtdna-page space-y-6">
      <FamilyPageHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="mtDNA analysis"
        family={family}
        projectId={resolvedProjectId}
      >
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="stat-label">Members</span>
                <strong className="family-workspace-stat-value">{orderedSamples.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Assembly</span>
                <strong className="family-workspace-stat-copy">{referenceLabel}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">MT variants</span>
                <strong className="family-workspace-stat-value">{mtDNA.variants.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Heteroplasmic</span>
                <strong className="family-workspace-stat-value">{heteroplasmicCount}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">QC warnings</span>
                <strong className="family-workspace-stat-value">{qcWarningCount}</strong>
              </div>
            </div>
      </FamilyPageHeader>

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div className="space-y-1">
            <h2 className="section-title">Family mitochondrial summary</h2>
            <p className="catalog-card-copy">
              Mothers and children are grouped together; fathers are shown separately from the maternal line.
            </p>
          </div>
        </div>

        <div className="family-mtdna-sample-layout">
          <div className="family-mtdna-sample-group">
            <div className="family-mtdna-sample-group-head">
              <span>Maternal line</span>
              <strong>
                {mothers.length} mother{mothers.length === 1 ? '' : 's'} · {children.length} child sample{children.length === 1 ? '' : 's'}
              </strong>
            </div>
            <div className="family-mtdna-sample-grid">
              {maternalLine.map((sample) => (
                <SampleSummaryCard key={sample.sample_id} sample={sample} />
              ))}
              {maternalLine.length === 0 && (
                <p className="table-subtle">No maternal-line samples are available.</p>
              )}
            </div>
          </div>
          <div className="family-mtdna-sample-group family-mtdna-sample-group--father">
            <div className="family-mtdna-sample-group-head">
              <span>Father</span>
              <strong>{fathers.length || 'No'} sample{fathers.length === 1 ? '' : 's'}</strong>
            </div>
            <div className="family-mtdna-sample-grid">
              {fathers.map((sample) => (
                <SampleSummaryCard key={sample.sample_id} sample={sample} />
              ))}
              {fathers.length === 0 && <p className="table-subtle">No father sample is available.</p>}
            </div>
          </div>
        </div>

        {mtDNA.qc_notes.length > 0 && (
          <div className="family-mtdna-qc-banner">
            {mtDNA.qc_notes.map((note) => (
              <span key={note}>{note}</span>
            ))}
          </div>
        )}
      </section>

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div className="space-y-1">
            <h2 className="section-title">Mitochondrial variants</h2>
            <p className="catalog-card-copy">
              Homoplasmy starts at {formatPercent(mtDNA.homoplasmy_threshold, 0)}; heteroplasmy is shown from {formatPercent(mtDNA.heteroplasmy_threshold, 0)}.
            </p>
          </div>
        </div>

        <div className="family-mtdna-toolbar">
          <label className="family-mtdna-filter-field" htmlFor="mtdna-search">
            <span>Search</span>
            <input
              id="mtdna-search"
              type="search"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Variant, gene or disorder"
            />
          </label>
          <div className="family-mtdna-scope-toggle" aria-label="mtDNA variant scope">
            <button
              type="button"
              className={scope === 'all' ? 'is-active' : ''}
              onClick={() => setScope('all')}
            >
              All
            </button>
            <button
              type="button"
              className={scope === 'clinical' ? 'is-active' : ''}
              onClick={() => setScope('clinical')}
            >
              Clinical
            </button>
            <button
              type="button"
              className={scope === 'heteroplasmic' ? 'is-active' : ''}
              onClick={() => setScope('heteroplasmic')}
            >
              Heteroplasmic
            </button>
            <button
              type="button"
              className={scope === 'homoplasmic' ? 'is-active' : ''}
              onClick={() => setScope('homoplasmic')}
            >
              Homoplasmic
            </button>
            <button
              type="button"
              className={scope === 'maternal_review' ? 'is-active' : ''}
              onClick={() => setScope('maternal_review')}
            >
              Maternal review
            </button>
          </div>
          <label className="family-mtdna-filter-field" htmlFor="mtdna-gnomad-af">
            <span>gnomAD frequency</span>
            <select
              id="mtdna-gnomad-af"
              value={maxGnomadAf}
              onChange={(event) => setMaxGnomadAf(event.target.value)}
            >
              {GNOMAD_AF_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="family-mtdna-filter-toggle">
            <input
              type="checkbox"
              checked={excludeSynonymous}
              onChange={(event) => setExcludeSynonymous(event.target.checked)}
            />
            <span>Hide synonymous</span>
          </label>
          <div className="family-mtdna-filter-count">
            {filteredVariants.length} of {mtDNA.variants.length} variants · {clinicalCount} clinical · {homoplasmicCount} homoplasmic
          </div>
        </div>

        <div className="variant-card-list family-mtdna-variant-list">
          {filteredVariants.map((variant) => {
            const gene = variant.annotation.gene || variant.annotation.region;
            const mitomapQuery = variant.annotation.mitomap_query || variant.label;
            return (
              <article
                key={variant.variant_id}
                aria-label={`${variant.label}${gene ? ` in ${gene}` : ''}`}
                className={`variant-card family-mtdna-card${significanceRail(
                  variant.annotation.clinical_significance,
                )}`}
              >
                {/* Variant level: what the variant is, what is known of it, where to look. */}
                <div className="family-mtdna-card-variant">
                  <GeneDiseaseHeading
                    gene={gene}
                    diseases={variant.annotation.disorders.map((disorder) => ({
                      name: disorder,
                      omimUrl: omimSearchHref(disorder),
                    }))}
                  />
                  <div className="family-mtdna-card-id">
                    <strong className="family-mtdna-card-label">{variant.label}</strong>
                    <span
                      className={`table-chip family-mtdna-significance family-mtdna-significance--${variant.annotation.clinical_significance}`}
                    >
                      {significanceLabel(variant.annotation.clinical_significance)}
                    </span>
                    {variant.review?.acmg?.classification && (
                      <span className="table-chip family-mtdna-acmg-chip">
                        ACMG: {variant.review.acmg.classification}
                      </span>
                    )}
                  </div>
                  <div className="table-subtle family-mtdna-card-meta">
                    <span>
                      MT:{variant.position.toLocaleString()} · {variant.type}
                    </span>
                    {variant.rsid && <span>{variant.rsid}</span>}
                  </div>
                  <div className="family-mtdna-card-meta">
                    <span>{variant.annotation.category || 'mitochondrial genome'}</span>
                    {variant.annotation.consequence && <span>{variant.annotation.consequence}</span>}
                    {variant.annotation.gnomad_af != null && (
                      <span>gnomAD {formatPercent(variant.annotation.gnomad_af, 3)}</span>
                    )}
                  </div>
                  {variant.annotation.polymorphism_notes.length > 0 && (
                    <div className="family-mtdna-polymorphism">
                      {variant.annotation.polymorphism_notes.slice(0, 2).join(' · ')}
                    </div>
                  )}
                  <div className="finding-actions">
                    <GenomeWorkspaceLink
                      to={buildMitoIgvHref(family.family_id, variant, resolvedProjectId)}
                      className="variant-card-resource variant-card-resource--clinical"
                      label={`Open IGV at ${getMitoIgvLocus(variant)}`}
                    >
                      IGV
                    </GenomeWorkspaceLink>
                    <GenomeWorkspaceLink
                      to={buildMitoChromosomeHref(
                        family.family_id,
                        variant.position,
                        variant.position,
                        resolvedProjectId,
                      )}
                      className="variant-card-resource variant-card-resource--clinical"
                      label={`Open chromosome view at MT:${variant.position.toLocaleString()}`}
                    >
                      Chromosome view
                    </GenomeWorkspaceLink>
                    <button
                      type="button"
                      className="button-link"
                      onClick={() => openMitomapVariant(variant)}
                      aria-label={`Open MITOMAP for ${mitomapQuery}`}
                    >
                      MITOMAP
                    </button>
                    <button
                      type="button"
                      className="button-link"
                      onClick={() => openAcmgModal(variant)}
                      aria-label={`Classify ${variant.label} with ACMG`}
                    >
                      {variant.review?.acmg ? 'Edit ACMG' : 'Classify (ACMG)'}
                    </button>
                    {copiedMitomapVariant === mitomapQuery && (
                      <span className="family-mtdna-copy-note" aria-live="polite">
                        Copied
                      </span>
                    )}
                  </div>
                </div>
                {/* Sample level: each member's call, the maternal line apart from the father. */}
                <div className="family-mtdna-card-samples">
                  <div className="family-mtdna-card-transmission">
                    <span className="stat-label">Transmission</span>
                    <span
                      className={`table-chip family-mtdna-transmission family-mtdna-transmission--${variant.maternal_transmission}`}
                    >
                      {transmissionLabel(variant.maternal_transmission)}
                    </span>
                  </div>
                  <div className="family-mtdna-call-group">
                    <span className="family-mtdna-call-group-label">Mother(s) and children</span>
                    {maternalLine.map((sample) => (
                      <VariantCallRow
                        key={sample.sample_id}
                        sample={sample}
                        call={variant.calls[sample.sample_id]}
                      />
                    ))}
                    {maternalLine.length === 0 && (
                      <span className="table-subtle">No maternal-line sample</span>
                    )}
                  </div>
                  <div className="family-mtdna-call-group">
                    <span className="family-mtdna-call-group-label">Father</span>
                    {fathers.map((sample) => (
                      <VariantCallRow
                        key={sample.sample_id}
                        sample={sample}
                        call={variant.calls[sample.sample_id]}
                      />
                    ))}
                    {fathers.length === 0 && <span className="table-subtle">No father sample</span>}
                  </div>
                </div>
              </article>
            );
          })}
          {filteredVariants.length === 0 && (
            <p className="table-subtle">
              {mtDNA.variants.length
                ? 'No mtDNA variants match the selected filters.'
                : 'No MT/chrM small variants are loaded for this family.'}
            </p>
          )}
        </div>
      </section>

      {(mtDNA.structural_variants?.length ?? 0) > 0 && (
        <section className="surface-card space-y-4">
          <div className="page-header">
            <div className="space-y-1">
              <h2 className="section-title">Mitochondrial structural variants</h2>
              <p className="catalog-card-copy">
                Large deletions and duplications on chrM, with each member&apos;s heteroplasmy (the fraction of reads that
                carry the event).
              </p>
            </div>
          </div>
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table family-mtdna-table">
              <thead>
                <tr>
                  <th>Structural variant</th>
                  <th>Genes</th>
                  <th>Heteroplasmy per member</th>
                </tr>
              </thead>
              <tbody>
                {(mtDNA.structural_variants ?? []).map((sv) => (
                  <tr key={sv.variant_id} className="family-mtdna-row">
                    <td>
                      <div className="font-semibold">
                        {sv.sv_type} m.{sv.start.toLocaleString()}-{sv.end.toLocaleString()}
                      </div>
                      <div className="table-subtle">
                        {sv.length != null ? `${sv.length.toLocaleString()} bp` : 'length n/a'}
                        {sv.filters.length > 0 && ` · ${sv.filters.join(', ')}`}
                      </div>
                      <div className="finding-actions">
                        <GenomeWorkspaceLink
                          to={buildMitoChromosomeHref(family.family_id, sv.start, sv.end, resolvedProjectId)}
                          className="variant-card-resource variant-card-resource--clinical"
                          label={`Open chromosome view at MT:${sv.start.toLocaleString()}-${sv.end.toLocaleString()}`}
                        >
                          Chromosome view
                        </GenomeWorkspaceLink>
                      </div>
                    </td>
                    <td>{sv.genes.length ? sv.genes.join(', ') : <span className="table-subtle">None</span>}</td>
                    <td>
                      <div className="family-mtdna-call-grid">
                        {Object.values(sv.calls).map((call) => (
                          <span key={call.sample} className="table-chip">
                            {call.sample}: {formatPercent(call.heteroplasmy)}
                            {call.read_support != null && ` (${call.read_support} reads)`}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {acmgVariant && (
        <FamilyReadOnlyContext.Provider value={Boolean(family.read_only)}>
          <AcmgClassificationModal
            familyId={family.family_id}
            projectId={resolvedProjectId ?? undefined}
            variant={toSmallVariantForAcmg(acmgVariant, mtDNA.samples)}
            members={samplesToMembers(mtDNA.samples)}
            assemblyName={assemblyName ?? undefined}
            assemblyVersion={assemblyVersion ?? undefined}
            errorMessage={reviewError}
            isPending={reviewMutation.isPending}
            onClose={() => setAcmgVariant(null)}
            onSave={handleSaveAcmg}
          />
        </FamilyReadOnlyContext.Provider>
      )}
    </div>
  );
};

export default FamilyMitoDNAAnalysisPage;
