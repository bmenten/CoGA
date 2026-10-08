import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiChromosome } from '../../lib/apiTypes';

/**
 * A chromosome's length and cytobands, as the ideograms draw them. Reference data: fetched
 * once per assembly and chromosome, and kept. The whole-chromosome and the zoomed ideogram
 * share the query, so they cannot ask for it with different options.
 */
export const useChromosome = (assembly: string, chrom: string) =>
  useQuery<ApiChromosome>({
    queryKey: ['chromosome', assembly, chrom],
    queryFn: async () => {
      const res = await api.get(apiPath`/chromosomes/${assembly}/${chrom}`);
      return res.data as ApiChromosome;
    },
    staleTime: Infinity,
    gcTime: Infinity,
  });
