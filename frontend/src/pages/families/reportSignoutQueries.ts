import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiReportSignoutCheck, ApiReportSignoutList } from '../../lib/apiTypes';

/** A failed request in words a reviewer reads: the server's detail, else what failed and why. */
export const apiErrorMessage = (error: unknown, fallback: string): string => {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    .response;
  const detail = response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (response?.status) return `${fallback} (the server returned ${response.status}).`;
  return `${fallback} (the server could not be reached).`;
};

/** The family's signed versions, newest first. The live and the signed report share it. */
export const useReportSignouts = (familyId: string | undefined) =>
  useQuery<ApiReportSignoutList>({
    queryKey: ['family', familyId, 'report-signouts'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/report/sign-outs`)).data as ApiReportSignoutList,
  });

/**
 * Whether the report as it would be signed now still matches the latest signed version
 * (`version`). Asked again each time a report opens: a cached answer could predate a change.
 */
export const useReportSignoutCheck = (familyId: string | undefined, version: number | null) =>
  useQuery<ApiReportSignoutCheck>({
    queryKey: ['family', familyId, 'report-signout-check', version],
    enabled: Boolean(familyId && version !== null),
    staleTime: 0,
    refetchOnMount: 'always',
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/report/sign-out-check`))
        .data as ApiReportSignoutCheck,
  });

/** Saves a signed version's record, exactly as the server serves it, as a JSON file. */
export const useSignedVersionDownload = (familyId: string) => {
  const [error, setError] = useState<{ version: number; message: string } | null>(null);
  const download = async (version: number) => {
    setError(null);
    try {
      const res = await api.get(apiPath`/families/${familyId}/report/sign-outs/${version}`);
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${familyId}-signed-report-v${version}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError({
        version,
        message: apiErrorMessage(caught, 'The signed version could not be downloaded'),
      });
    }
  };
  return { download, error };
};
