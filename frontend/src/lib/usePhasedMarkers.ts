import { useQuery } from '@tanstack/react-query';
import api from './api';
import { apiPath } from './apiPath';
import type { PhasedMarkerResponse } from './apiSchema.generated';
import { useSameSpanFallbackData } from './useSameSpanFallbackData';

/**
 * The family's raw phased markers in a window, as the haplotype track draws them and its head
 * counts them. Every reader of one window shares one request (the query key), so the head's
 * informative-site count is the count of the markers drawn below it.
 *
 * The per-site fetch is capped server-side and drops the highest-coordinate tail when the
 * window holds too many sites: `truncated` then empties the markers and sites, as the overlay
 * would be partial and misleading. `hemizygous_x_ranges` are where in the window a man has one X
 * (chrX outside the PARs of the family's assembly).
 *
 * A pan keeps the previous window's markers until the next arrive (same span); a zoom, another
 * family or chromosome, or a failed request drops them (`useSameSpanFallbackData`).
 */
export const usePhasedMarkers = ({
  familyId,
  chrom,
  regionStart,
  regionEnd,
  enabled,
}: {
  familyId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  enabled: boolean;
}): { data: PhasedMarkerResponse | null; isError: boolean } => {
  const { data, isError } = useQuery<PhasedMarkerResponse>({
    queryKey: ['phased-markers', familyId, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/phased-markers`, {
        params: { chr: chrom, start: regionStart, end: regionEnd },
      });
      return res.data as PhasedMarkerResponse;
    },
    enabled,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const held = useSameSpanFallbackData(
    data,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${familyId}|${chrom}`,
    isError,
  );
  return { data: held, isError };
};
