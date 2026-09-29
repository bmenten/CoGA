import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from './api';
import type { ApiFamilyMember, ApiFamilyRegionOfInterest } from './apiTypes';
import { classifyEmbryosAtRoi, type EmbryoClassification } from './embryoSegregation';
import type { HaplotypeMemberLike, HaplotypeSampleLike } from './haplotypeRisk';
import { apiPath } from './apiPath';

// Fetch a window around the ROI (shared query cache with the ROI marker page) so
// the relatives' IBD lineage resolves robustly, then classify AT the ROI itself.
const ROI_FLANK = 1_000_000;

interface HaplotypeResponse {
  samples: HaplotypeSampleLike[];
}

const toMember = (m: ApiFamilyMember): HaplotypeMemberLike => ({
  sample_id: m.sample_id,
  role: m.role,
  affected: m.affected,
  sex: m.sex,
  clinical_status: m.clinical_status,
  carrier_status: m.carrier_status,
  carrier_type: m.carrier_type,
});

/**
 * Derived embryo segregation at the ROI, keyed by embryo sample id. Reuses the
 * canonical disease-haplotype inference (haplotypeRisk.ts) over the ROI's
 * lineage-tagged haplotypes — this is DERIVED FROM THE ANALYSIS, not entered by an
 * analyst. Returns an empty map until there is a ROI, embryos, and haplotype data.
 */
export const useEmbryoSegregation = ({
  familyId,
  roi,
  members,
  inheritanceModel,
}: {
  familyId: string;
  roi: ApiFamilyRegionOfInterest | null;
  members: ApiFamilyMember[];
  inheritanceModel?: string | null;
}): {
  byEmbryo: Map<string, EmbryoClassification>;
  isLoading: boolean;
  /** The haplotypes at the ROI could not be loaded: no segregation is derived, and the
   * recombination and uninformative warnings are unknown, not absent (#607). */
  isError: boolean;
  retry: () => void;
} => {
  const hasEmbryos = members.some((m) => String(m.role || '').toLowerCase() === 'embryo');
  const winStart = roi ? Math.max(0, roi.start - ROI_FLANK) : 0;
  const winEnd = roi ? roi.end + ROI_FLANK : 0;

  const { data, isLoading, isError, refetch } = useQuery<HaplotypeResponse>({
    queryKey: ['haplotypes', familyId, roi?.chr, winStart, winEnd],
    enabled: !!roi && !!familyId && hasEmbryos,
    staleTime: Infinity,
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/haplotypes`, {
          params: { chr: roi!.chr, start: winStart, end: winEnd },
        })
      ).data as HaplotypeResponse,
  });

  const byEmbryo = useMemo(() => {
    if (!roi || !data?.samples?.length) return new Map<string, EmbryoClassification>();
    const rows = classifyEmbryosAtRoi({
      members: members.map(toMember),
      samples: data.samples,
      inheritanceModel,
      region: { chr: roi.chr, start: roi.start, end: roi.end },
    });
    return new Map(rows.map((r) => [r.sampleId, r]));
  }, [roi, data?.samples, members, inheritanceModel]);

  return {
    byEmbryo,
    isLoading: isLoading && hasEmbryos && !!roi,
    isError: isError && hasEmbryos && !!roi,
    retry: () => {
      void refetch();
    },
  };
};
