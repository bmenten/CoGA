import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from './api';
import { apiPath } from './apiPath';
import type { PgtAnalysisOut } from './apiSchema.generated';
import { embryoClassificationsFromAnalysis, mixedHomologsWarning } from './embryoSegregation';

/**
 * The embryos the PGT analysis leaves uncalled because their markers at the ROI mix both of a
 * parent's homologs (backend/app/services/pgt_embryo_qc.py), with the warning the family page
 * gives, by sample id. Their haplotype blocks show one homolog of that parent where the embryo
 * may carry both (a trisomy), so the haplotype tracks read no risk state from them either: the
 * tracks and the family page give the same call. Empty while the analysis loads, when it fails,
 * and for a family without an ROI.
 */
export const useMixedHomologEmbryos = (familyId: string, enabled: boolean): Map<string, string> => {
  const { data } = useQuery<PgtAnalysisOut>({
    queryKey: ['pgt-analysis-mixed-homologs', familyId],
    enabled: enabled && !!familyId,
    staleTime: 60_000,
    queryFn: async () => (await api.get(apiPath`/families/${familyId}/pgt-analysis`)).data as PgtAnalysisOut,
  });
  return useMemo(() => {
    const warnings = new Map<string, string>();
    if (!data || !Array.isArray(data.embryos)) return warnings;
    embryoClassificationsFromAnalysis(data).forEach((embryo, sample) => {
      if (embryo.mixedHomologs.length) {
        warnings.set(sample, mixedHomologsWarning(embryo.mixedHomologs, embryo.maxMarkerDiscordance));
      }
    });
    return warnings;
  }, [data]);
};
