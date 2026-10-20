import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from './api';
import type { ApiFamilyMember, ApiFamilyRegionOfInterest } from './apiTypes';
import type { PgtAnalysisOut } from './apiSchema.generated';
import { embryoClassificationsFromAnalysis, type EmbryoClassification } from './embryoSegregation';
import type { HaplotypePhaseCorrection } from './haplotypePhaseCorrections';
import { apiPath } from './apiPath';

/**
 * The embryo calls at the ROI, keyed by embryo sample id, as the backend makes them
 * (`GET /families/{id}/pgt-analysis`, backend/app/services/pgt_embryo_calls.py): the calls a
 * sign-out of the family freezes into its signed record. DERIVED FROM THE ANALYSIS, not
 * entered by an analyst. Empty until there is a ROI, embryos, and haplotype data.
 */
export const useEmbryoSegregation = ({
  familyId,
  roi,
  members,
  inheritanceModel,
  phaseCorrections,
}: {
  familyId: string;
  roi: ApiFamilyRegionOfInterest | null;
  members: ApiFamilyMember[];
  /** The family's recorded model: the calls change with it, so the query does too. */
  inheritanceModel?: string | null;
  /** The parents' phase corrections the family records; a new import changes them. */
  phaseCorrections?: HaplotypePhaseCorrection[];
}): {
  byEmbryo: Map<string, EmbryoClassification>;
  isLoading: boolean;
  /** The calls at the ROI could not be loaded: no segregation is shown, and the
   * recombination and uninformative warnings are unknown, not absent (#607). */
  isError: boolean;
  retry: () => void;
} => {
  const hasEmbryos = members.some((m) => String(m.role || '').toLowerCase() === 'embryo');
  // What the calls are made from on the family's side: the ROI, the members' roles, sexes
  // and statuses, the model and the parents' phase corrections. A change to any of them
  // asks for the calls again.
  const inputs = useMemo(
    () =>
      JSON.stringify([
        roi ? [roi.chr, roi.start, roi.end] : null,
        members.map((m) => [m.sample_id, m.role, m.sex, m.affected, m.clinical_status, m.carrier_status, m.carrier_type]),
        inheritanceModel ?? null,
        phaseCorrections ?? [],
      ]),
    [roi, members, inheritanceModel, phaseCorrections],
  );

  const { data, isLoading, isError, refetch } = useQuery<PgtAnalysisOut>({
    queryKey: ['pgt-analysis', familyId, inputs],
    enabled: !!roi && !!familyId && hasEmbryos,
    staleTime: Infinity,
    queryFn: async () => (await api.get(apiPath`/families/${familyId}/pgt-analysis`)).data as PgtAnalysisOut,
  });

  const byEmbryo = useMemo(
    () =>
      roi && data && Array.isArray(data.embryos)
        ? embryoClassificationsFromAnalysis(data)
        : new Map<string, EmbryoClassification>(),
    [roi, data],
  );

  return {
    byEmbryo,
    isLoading: isLoading && hasEmbryos && !!roi,
    isError: isError && hasEmbryos && !!roi,
    retry: () => {
      void refetch();
    },
  };
};
