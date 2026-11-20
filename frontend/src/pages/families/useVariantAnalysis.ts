import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import { variantAnalysisOf } from '../../lib/familyAnalyses';

/**
 * The analysis a part of a family's page reads the family's small variants for
 * (`variantAnalysisOf`): of a family with two or more analyses with small variants of their own,
 * the one the page's URL names, else the one imported last; null for any other family. Read from
 * the family's record, the query every family page shares.
 */
export const useVariantAnalysis = (familyId: string | undefined): string | null => {
  const location = useLocation();
  const { data } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => (await api.get(apiPath`/families/${familyId ?? ''}`)).data as ApiFamilyRecord,
  });
  return variantAnalysisOf(data?.metadata, location.search);
};
