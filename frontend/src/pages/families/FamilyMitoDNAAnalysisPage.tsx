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
import {
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { buildSmallVariantReviewPath, fetchFamilyReviewTags, quickTagTogglePayload } from './smallVariantReview';
import { sortReviewTagKeys, visibleReviewTagKeys } from './smallVariantResultUtils';
import ReviewTagChip from './ReviewTagChip';
import SmallVariantReviewDialog from './SmallVariantReviewDialog';
import { apiPath } from '../../lib/apiPath';
import { FamilyReadOnlyContext, READ_ONLY_HINT } from '../../lib/familyAccess';
import { raw, routePath } from '../../lib/routePath';
import GeneDiseaseHeading from '../../components/GeneDiseaseHeading';
import InfoIcon from '../../components/InfoIcon';
import { omimGeneHref, omimSearchHref } from '../../lib/omim';
import { geneInfoHref } from '../../lib/geneInfo';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import FindingCard, { FindingLinks, type FindingRail } from './FindingCard';
import FamilyGenotypeGrid, { type GenotypeColumn, type GenotypeRow, type GenotypeTone } from './FamilyGenotypeGrid';

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

// The card's rail and its significance chip, keyed to the significance as the small-variant
// cards key them to ClinVar.
const significanceRail = (status: ApiMitoDNAVariant['annotation']['clinical_significance']): FindingRail => {
  if (status === 'pathogenic' || status === 'likely_pathogenic') return 'pathogenic';
  if (status === 'benign' || status === 'likely_benign' || status === 'polymorphism') return 'benign';
  if (status === 'reported' || status === 'uncertain') return 'warning';
  return 'neutral';
};

const significanceChipTone = (status: ApiMitoDNAVariant['annotation']['clinical_significance']): string => {
  if (status === 'pathogenic' || status === 'likely_pathogenic') return 'critical';
  if (status === 'benign' || status === 'likely_benign' || status === 'polymorphism') return 'success';
  if (status === 'reported' || status === 'uncertain') return 'warning';
  return 'neutral';
};

// The gene a variant names, for its gene page and OMIM: none for the control region or an
// intergenic stretch, which are no genes.
const mitoGeneSymbol = (variant: ApiMitoDNAVariant): string | null =>
  variant.annotation.gene && !['control', 'intergenic'].includes(variant.annotation.category || '')
    ? variant.annotation.gene
    : null;

const MITO_GENOTYPE_COLUMNS: GenotypeColumn[] = [
  { key: 'dp', label: 'DP', numeric: true },
  { key: 'ad', label: 'AD', numeric: true },
];

const ZYGOSITY_TONE: Record<ApiMitoDNAVariantSampleCall['zygosity'], GenotypeTone> = {
  homoplasmic: 'alert',
  heteroplasmic: 'warn',
  low_level: 'warn',
  reference: 'ref',
  no_call: 'na',
  unknown: 'na',
};

// One member's call on the variant: the heteroplasmy (the result) as the call, the read depth
// behind it, the zygosity as its status.
const mitoGenotypeRow = (sample: ApiMitoDNASample, call?: ApiMitoDNAVariantSampleCall): GenotypeRow => ({
  member: { ...sample, role: sample.role || 'relative' },
  tone: call ? ZYGOSITY_TONE[call.zygosity] : 'na',
  call: call ? call.display : 'No data',
  values: call ? { dp: formatNumber(call.depth), ad: formatNumber(call.alt_depth) } : undefined,
  status: call ? { label: zygosityLabel(call.zygosity), tone: ZYGOSITY_TONE[call.zygosity] } : null,
});


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

const SEX_SYMBOL: Record<string, string> = { male: '♂', female: '♀' };

// One sample in the family's mtDNA summary: one compact row, its QC verdict and notes on it.
const SampleSummaryRow: React.FC<{ sample: ApiMitoDNASample }> = ({ sample }) => {
  const sex = (sample.sex || '').toLowerCase();
  return (
    <tr className={`family-mtdna-summary-row family-mtdna-summary-row--${sample.qc.status}`}>
      <td>
        <span className="variant-card-gtline-sample">
          <span
            className={`variant-card-gtline-sex variant-card-gtline-sex--${SEX_SYMBOL[sex] ? sex : 'und'}`}
            title={sample.sex || 'unknown sex'}
          >
            {SEX_SYMBOL[sex] ?? '⚥'}
          </span>
          {sample.sample_id}
        </span>
      </td>
      <td className="table-subtle">{sample.role || 'relative'}</td>
      <td>{sample.haplogroup || 'n/a'}</td>
      <td className="family-mtdna-summary-num">{formatDepth(sample.coverage.mean_depth)}</td>
      <td className="family-mtdna-summary-num">{formatPercent(sample.coverage.breadth, 0)}</td>
      <td className="family-mtdna-summary-num">
        {sample.qc.contamination != null ? formatPercent(sample.qc.contamination, 2) : '—'}
      </td>
      <td>
        <span className={`table-chip family-mtdna-qc-chip family-mtdna-qc-chip--${sample.qc.status}`}>
          {QC_STATUS_LABEL[sample.qc.status]}
        </span>
      </td>
      <td className="family-mtdna-qc-notes">{sample.qc.notes.slice(0, 2).join(' · ')}</td>
    </tr>
  );
};

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
  // The variant whose tags and note are open in the review dialog.
  const [notesVariant, setNotesVariant] = useState<ApiMitoDNAVariant | null>(null);
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

  // The review tags the small-variant pages offer: an mtDNA variant's review is the same
  // small-variant review, so it takes the same tags (and the Report tag puts it on the report).
  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', resolvedProjectId || null],
    enabled: Boolean(familyId && (!family?.projects?.length || !referenceLoading)),
    queryFn: () => fetchFamilyReviewTags(familyId, resolvedProjectId || undefined),
  });
  const tagMap = useMemo(() => getTagDefinitionMap(tags), [tags]);

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

  // A save that failed: the message, and a reload when someone else saved the review meanwhile.
  const reportReviewFailure = useCallback(
    (error: unknown, fallback: string) => {
      setReviewError(getErrorMessage(error, fallback));
      if (isReviewConflict(error)) {
        void queryClient.invalidateQueries({
          queryKey: ['family', familyId, 'mitochondrial-dna', resolvedProjectId],
        });
      }
    },
    [queryClient, familyId, resolvedProjectId],
  );

  // Review, Exclude and Report: a quick tag on the review, as on the small-variant cards.
  const toggleReviewTag = useCallback(
    async (variant: ApiMitoDNAVariant, tagKey: string) => {
      setReviewError(null);
      try {
        await reviewMutation.mutateAsync({
          variantId: variant.variant_id,
          review: variant.review,
          payload: quickTagTogglePayload(variant.review, tagKey),
        });
      } catch (error) {
        reportReviewFailure(error, 'Unable to save the variant review.');
      }
    },
    [reviewMutation, reportReviewFailure],
  );

  const handleSaveNotes = useCallback(
    async (payload: SmallVariantReviewSavePayload) => {
      if (!notesVariant) return;
      setReviewError(null);
      try {
        await reviewMutation.mutateAsync({ variantId: notesVariant.variant_id, review: notesVariant.review, payload });
        setNotesVariant(null);
      } catch (error) {
        reportReviewFailure(error, 'Unable to save the variant review.');
        throw error;
      }
    },
    [notesVariant, reviewMutation, reportReviewFailure],
  );

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

      <section className="surface-card space-y-2">
        <div className="paraphase-section-head">
          <h2 className="section-title">Family mitochondrial summary</h2>
          <InfoIcon
            name="About this summary"
            text="Mothers and children are grouped together as the maternal line; fathers are shown apart from it."
          />
        </div>

        {/* One compact row per sample, the maternal line first and the father apart. */}
        <div className="data-table-shell data-table-shell--fit">
          <table className="analysis-table family-mtdna-summary-table">
            <thead>
              <tr>
                <th>Sample</th>
                <th>Role</th>
                <th>Haplogroup</th>
                <th className="family-mtdna-summary-num">Mean depth</th>
                <th className="family-mtdna-summary-num">Breadth</th>
                <th className="family-mtdna-summary-num">Contam.</th>
                <th>QC</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              <tr className="family-mtdna-summary-group">
                <th colSpan={8} scope="rowgroup">
                  Maternal line{' '}
                  <span className="table-subtle">
                    {mothers.length} mother{mothers.length === 1 ? '' : 's'} · {children.length} child sample
                    {children.length === 1 ? '' : 's'}
                  </span>
                </th>
              </tr>
              {maternalLine.map((sample) => (
                <SampleSummaryRow key={sample.sample_id} sample={sample} />
              ))}
              {maternalLine.length === 0 && (
                <tr>
                  <td colSpan={8} className="table-subtle">
                    No maternal-line samples are available.
                  </td>
                </tr>
              )}
              <tr className="family-mtdna-summary-group">
                <th colSpan={8} scope="rowgroup">
                  Father
                </th>
              </tr>
              {fathers.map((sample) => (
                <SampleSummaryRow key={sample.sample_id} sample={sample} />
              ))}
              {fathers.length === 0 && (
                <tr>
                  <td colSpan={8} className="table-subtle">
                    No father sample is available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
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

        {/* A quick tag that could not be saved says so here; the dialogs show their own. */}
        {reviewError && !acmgVariant && !notesVariant ? (
          <p className="status-note status-note--error" role="alert">
            {reviewError}
          </p>
        ) : null}
        <div className="variant-card-list family-mtdna-variant-list">
          {filteredVariants.map((variant) => {
            const gene = variant.annotation.gene || variant.annotation.region;
            const mitomapQuery = variant.annotation.mitomap_query || variant.label;
            const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
            const visibleReviewTags = visibleReviewTagKeys(sortedReviewTags);
            const quickTags = [
              { key: COLLABORATION_QUICK_TAGS.review, label: 'Review' },
              { key: COLLABORATION_QUICK_TAGS.excluded, label: 'Exclude' },
              { key: COLLABORATION_QUICK_TAGS.report, label: 'Report' },
            ];
            const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
            return (
              <FindingCard
                key={variant.variant_id}
                aria-label={`${variant.label}${gene ? ` in ${gene}` : ''}`}
                rail={significanceRail(variant.annotation.clinical_significance)}
                className={`family-mtdna-card${isExcluded ? ' variant-card--excluded' : ''}`}
                subhead={
                  visibleReviewTags.length ? (
                    <div className="variant-card-chip-row variant-card-tag-row">
                      {visibleReviewTags.map((tagKey) => (
                        <ReviewTagChip
                          key={tagKey}
                          className="variant-card-chip variant-card-chip--tag"
                          tagKey={tagKey}
                          tagMap={tagMap}
                          tagMetadata={variant.review?.tag_metadata}
                        />
                      ))}
                    </div>
                  ) : null
                }
                note={
                  variant.review?.note ? <p className="variant-card-review-note">{variant.review.note}</p> : null
                }
                heading={
                  <GeneDiseaseHeading
                    gene={gene || 'mtDNA'}
                    geneHref={geneInfoHref(mitoGeneSymbol(variant), family.family_id, resolvedProjectId)}
                    diseases={variant.annotation.disorders.map((disorder) => ({
                      name: disorder,
                      omimUrl: omimSearchHref(disorder),
                    }))}
                  >
                    {variant.annotation.consequence ? (
                      <span className="variant-card-chip variant-card-chip--impact">
                        {variant.annotation.consequence}
                      </span>
                    ) : null}
                  </GeneDiseaseHeading>
                }
                tags={
                  <>
                    <span
                      className={`variant-card-chip variant-card-chip--${significanceChipTone(
                        variant.annotation.clinical_significance,
                      )} family-mtdna-significance`}
                    >
                      {significanceLabel(variant.annotation.clinical_significance)}
                    </span>
                    {variant.review?.acmg?.classification && (
                      <span className="variant-card-chip variant-card-chip--strong family-mtdna-acmg-chip">
                        ACMG: {variant.review.acmg.classification}
                      </span>
                    )}
                  </>
                }
                columns={[
                  {
                    key: 'variant',
                    title: 'Variant',
                    content: (
                      <dl className="variant-card-mini-dl">
                        <div>
                          <dt>Change</dt>
                          <dd className="family-mtdna-card-label">{variant.label}</dd>
                        </div>
                        <div>
                          <dt>Position</dt>
                          <dd>
                            MT:{variant.position.toLocaleString()} · {variant.type}
                          </dd>
                        </div>
                        {variant.rsid ? (
                          <div>
                            <dt>dbSNP</dt>
                            <dd>{variant.rsid}</dd>
                          </div>
                        ) : null}
                        <div>
                          <dt>Category</dt>
                          <dd>{variant.annotation.category || 'mitochondrial genome'}</dd>
                        </div>
                        <div>
                          <dt>gnomAD</dt>
                          <dd>
                            {variant.annotation.gnomad_af != null
                              ? formatPercent(variant.annotation.gnomad_af, 3)
                              : '—'}
                          </dd>
                        </div>
                        <div>
                          <dt>Transmission</dt>
                          <dd>
                            <span
                              className={`table-chip family-mtdna-transmission family-mtdna-transmission--${variant.maternal_transmission}`}
                            >
                              {transmissionLabel(variant.maternal_transmission)}
                            </span>
                          </dd>
                        </div>
                        {variant.annotation.polymorphism_notes.length > 0 && (
                          <div>
                            <dt>Notes</dt>
                            <dd>{variant.annotation.polymorphism_notes.slice(0, 2).join(' · ')}</dd>
                          </div>
                        )}
                      </dl>
                    ),
                  },
                  {
                    key: 'genotypes',
                    title: 'Genotypes',
                    // The maternal line apart from the father: mtDNA comes from the mother.
                    content: (
                      <FamilyGenotypeGrid
                        callLabel="Heteroplasmy"
                        columns={MITO_GENOTYPE_COLUMNS}
                        withStatus
                        entries={[
                          {
                            group: 'Mother(s) and children',
                            empty: maternalLine.length ? undefined : 'No maternal-line sample',
                          },
                          ...maternalLine.map((sample) => mitoGenotypeRow(sample, variant.calls[sample.sample_id])),
                          { group: 'Father', empty: fathers.length ? undefined : 'No father sample' },
                          ...fathers.map((sample) => mitoGenotypeRow(sample, variant.calls[sample.sample_id])),
                        ]}
                      />
                    ),
                  },
                ]}
                actions={
                  <>
                    {quickTags.map((quickTag) => {
                      const active = sortedReviewTags.includes(quickTag.key);
                      return (
                        <button
                          key={quickTag.key}
                          type="button"
                          className={`variant-quick-toggle${active ? ' variant-quick-toggle--active' : ''}`}
                          aria-pressed={active}
                          disabled={Boolean(family.read_only) || reviewMutation.isPending}
                          title={family.read_only ? READ_ONLY_HINT : undefined}
                          onClick={() => {
                            void toggleReviewTag(variant, quickTag.key);
                          }}
                        >
                          {quickTag.label}
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      className="variant-review-link"
                      onClick={() => {
                        setReviewError(null);
                        setNotesVariant(variant);
                      }}
                    >
                      Tags &amp; notes
                    </button>
                    <button
                      type="button"
                      className="variant-review-link"
                      onClick={() => openAcmgModal(variant)}
                      aria-label={`Classify ${variant.label} with ACMG`}
                    >
                      {variant.review?.acmg ? 'Edit ACMG' : 'ACMG classify'}
                    </button>
                    {copiedMitomapVariant === mitomapQuery && (
                      <span className="family-mtdna-copy-note" aria-live="polite">
                        Copied
                      </span>
                    )}
                  </>
                }
                links={
                  <FindingLinks
                    igv={{
                      href: buildMitoIgvHref(family.family_id, variant, resolvedProjectId),
                      label: `Open IGV at ${getMitoIgvLocus(variant)}`,
                    }}
                    chromosome={{
                      href: buildMitoChromosomeHref(
                        family.family_id,
                        variant.position,
                        variant.position,
                        resolvedProjectId,
                      ),
                      label: `Open chromosome view at MT:${variant.position.toLocaleString()}`,
                    }}
                    omim={mitoGeneSymbol(variant) ? omimGeneHref(mitoGeneSymbol(variant) as string) : null}
                  >
                    <button
                      type="button"
                      className="variant-card-resource variant-card-resource--clinical family-mtdna-mitomap"
                      onClick={() => openMitomapVariant(variant)}
                      aria-label={`Open MITOMAP for ${mitomapQuery}`}
                    >
                      MITOMAP
                    </button>
                  </FindingLinks>
                }
              />
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

      {notesVariant && (
        <FamilyReadOnlyContext.Provider value={Boolean(family.read_only)}>
          <SmallVariantReviewDialog
            familyId={family.family_id}
            projectId={resolvedProjectId ?? undefined}
            members={samplesToMembers(mtDNA.samples)}
            // No gene constraint or AlphaMissense score for an mtDNA variant: the dialog
            // shows none.
            variant={{
              ...toSmallVariantForAcmg(notesVariant, mtDNA.samples),
              gene_pli: null,
              gene_missense_z: null,
              alpha_missense_pathogenicity: null,
              alpha_missense_class: null,
            }}
            tags={tags}
            errorMessage={reviewError}
            isPending={reviewMutation.isPending}
            onClose={() => setNotesVariant(null)}
            onSave={handleSaveNotes}
          />
        </FamilyReadOnlyContext.Provider>
      )}
    </div>
  );
};

export default FamilyMitoDNAAnalysisPage;
