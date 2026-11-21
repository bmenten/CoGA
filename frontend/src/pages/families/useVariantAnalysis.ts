import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import { structuralVariantAnalysisOf, variantAnalysisOf } from '../../lib/familyAnalyses';

/** The family's metadata, from the family's record: the query every family page shares. */
const useFamilyMetadata = (familyId: string | undefined): unknown => {
  const { data } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => (await api.get(apiPath`/families/${familyId ?? ''}`)).data as ApiFamilyRecord,
  });
  return data?.metadata;
};

/**
 * The analysis a part of a family's page reads the family's small variants for
 * (`variantAnalysisOf`): of a family with two or more analyses with small variants of their own,
 * the one the page's URL names, else the one imported last; null for any other family.
 */
export const useVariantAnalysis = (familyId: string | undefined): string | null => {
  const location = useLocation();
  return variantAnalysisOf(useFamilyMetadata(familyId), location.search);
};

/**
 * The analysis a part of a family's page reads the family's structural variants for
 * (`structuralVariantAnalysisOf`): of a family with two or more analyses with structural variants
 * of their own, the one the page's URL names, else the one imported last; null for any other
 * family.
 */
export const useStructuralVariantAnalysis = (familyId: string | undefined): string | null => {
  const location = useLocation();
  return structuralVariantAnalysisOf(useFamilyMetadata(familyId), location.search);
};
