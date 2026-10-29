import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath, raw } from '../../lib/apiPath';
import type {
  FamilyMitoDNAAnalysisOut,
  FamilyParaphaseTableOut,
  FamilyRepeatExpansionTableOut,
  VariantPage,
} from '../../lib/apiSchema.generated';

/**
 * Which kinds of data a family has, for the links every family page shows: the analyses, and
 * the family's Sample QC and report.
 *
 * Presence-only reads: `count_only` returns a flag instead of the full small, structural,
 * repeat, Paraphase or mtDNA payloads; for small and structural variants this skips the
 * bounded row count and summary aggregation, which scan the whole family. The queries share
 * their keys across pages, so moving from one family page to another reads them once. The keys
 * sit under the family (`['family', id, 'has-data', …]`), apart from each page's own list: a review
 * saved on the small-variant page writes into its list's cached pages, and a presence answer
 * (`{ total }`) among them broke that save.
 *
 * A check that failed leaves it unknown whether the family has that data: its link is shown,
 * so its page can say for itself, rather than hidden as if there were none (#607).
 */
export interface FamilyDataPresence {
  /** Every check has answered or failed: "Checking…" used to stay up for good (#607). */
  loaded: boolean;
  hasSmallVariants: boolean;
  hasStructuralVariants: boolean;
  hasRepeatExpansions: boolean;
  hasParaphase: boolean;
  hasMitoDna: boolean;
  /**
   * The variant summary summarises the structural variants, from every SV caller (HiFiCNV
   * copy-number calls included), and nothing else: offered when the family has them, or when
   * that check failed, never for small variants alone.
   */
  hasVariantSummary: boolean;
  hasAnyVariantData: boolean;
  /** The checks that failed, named for the failure notice. */
  failedChecks: string[];
  retryFailedChecks: () => void;
}

export const useFamilyDataPresence = ({
  familyId,
  projectId,
  ready,
}: {
  familyId: string | undefined;
  projectId: string | null | undefined;
  /** The family is loaded and, when it has projects, the project resolved. */
  ready: boolean;
}): FamilyDataPresence => {
  const presenceParams = (() => {
    const params = new URLSearchParams({ count_only: 'true' });
    if (projectId) params.set('project_id', projectId);
    return `?${params.toString()}`;
  })();
  const enabled = Boolean(familyId) && ready;

  const structural = useQuery<Pick<VariantPage, 'total'>>({
    queryKey: ['family', familyId, 'has-data', 'structural-variants', projectId || null],
    enabled,
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/structural-variants${raw(presenceParams)}`)).data as VariantPage,
  });
  const small = useQuery<Pick<VariantPage, 'total'>>({
    queryKey: ['family', familyId, 'has-data', 'small-variants', projectId || null],
    enabled,
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/small-variants${raw(presenceParams)}`)).data as VariantPage,
  });
  const repeats = useQuery<Pick<FamilyRepeatExpansionTableOut, 'loci_count'>>({
    queryKey: ['family', familyId, 'has-data', 'repeat-expansions', projectId || null],
    enabled,
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/repeat-expansions${raw(presenceParams)}`))
        .data as FamilyRepeatExpansionTableOut,
  });
  const paraphase = useQuery<Pick<FamilyParaphaseTableOut, 'genes_count'>>({
    queryKey: ['family', familyId, 'has-data', 'paraphase', projectId || null],
    enabled,
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/paraphase${raw(presenceParams)}`)).data as FamilyParaphaseTableOut,
  });
  const mito = useQuery<Pick<FamilyMitoDNAAnalysisOut, 'variant_count' | 'has_coverage'>>({
    queryKey: ['family', familyId, 'has-data', 'mitochondrial-dna', projectId || null],
    enabled,
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/mitochondrial-dna${raw(presenceParams)}`))
        .data as FamilyMitoDNAAnalysisOut,
  });

  const hasStructuralVariants = (structural.data?.total ?? 0) > 0 || structural.isError;
  const hasSmallVariants = (small.data?.total ?? 0) > 0 || small.isError;
  const hasRepeatExpansions = (repeats.data?.loci_count ?? 0) > 0 || repeats.isError;
  const hasParaphase = (paraphase.data?.genes_count ?? 0) > 0 || paraphase.isError;
  // mtDNA data is present when there are chrM variants or any sample carries mtDNA coverage
  // (coverage-only families still have something to show).
  const hasMitoDna =
    (mito.data?.variant_count ?? 0) > 0 || (mito.data?.has_coverage ?? false) || mito.isError;
  const checks = [
    { query: small, name: 'small variants' },
    { query: structural, name: 'structural variants' },
    { query: repeats, name: 'repeat expansions' },
    { query: paraphase, name: 'Paraphase results' },
    { query: mito, name: 'mtDNA data' },
  ];
  return {
    loaded: enabled && checks.every(({ query }) => query.data !== undefined || query.isError),
    hasSmallVariants,
    hasStructuralVariants,
    hasRepeatExpansions,
    hasParaphase,
    hasMitoDna,
    hasVariantSummary: hasStructuralVariants,
    hasAnyVariantData:
      hasStructuralVariants || hasSmallVariants || hasRepeatExpansions || hasParaphase || hasMitoDna,
    failedChecks: checks.filter(({ query }) => query.isError).map(({ name }) => name),
    retryFailedChecks: () => {
      checks.forEach(({ query }) => {
        if (query.isError) void query.refetch();
      });
    },
  };
};
