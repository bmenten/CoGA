import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath, raw } from '../../lib/apiPath';
import type { ApiReportSignoutCheck, ApiReportSignoutList } from '../../lib/apiTypes';
import type { ReportSignoutSummary } from '../../lib/apiSchema.generated';

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
 * The latest signed version of one analysis (`analysisType`, as the page signs it out): each
 * analysis of a family that holds several is signed out on its own, in the family's one series of
 * versions. Null for an analysis never signed out; the family's latest when the page signs out
 * the whole family (`analysisType` null: a family of one analysis or none).
 */
export const latestSignoutOf = (
  list: ApiReportSignoutList | undefined,
  analysisType: string | null,
): ReportSignoutSummary | null => {
  if (!list) return null;
  if (analysisType === null) return list.latest;
  return list.latest_per_analysis.find((entry) => entry.analysis_type === analysisType) ?? null;
};

/**
 * The latest signed version of the analysis a signed record names (`analysisType`; null for the
 * record of a family that held none): the version that supersedes it, or itself.
 */
export const latestSignoutOfRecord = (
  list: ApiReportSignoutList | undefined,
  analysisType: string | null,
): ReportSignoutSummary | null =>
  list?.latest_per_analysis.find((entry) => entry.analysis_type === analysisType) ?? null;

/** The query string that names the analysis a request is of; none for the whole family. */
const analysisQuery = (analysisType: string | null) =>
  raw(analysisType === null ? '' : `?${new URLSearchParams({ analysis_type: analysisType })}`);

/**
 * Whether the report of one analysis (`analysisType`; null for a family of one analysis or
 * none) as it would be signed now still matches that analysis's latest signed version
 * (`version`). Asked again each time a report opens: a cached answer could predate a change.
 */
export const reportSignoutCheckQuery = (
  familyId: string | undefined,
  version: number | null,
  analysisType: string | null = null,
) => ({
  queryKey: ['family', familyId, 'report-signout-check', version, analysisType],
  enabled: Boolean(familyId && version !== null),
  staleTime: 0,
  refetchOnMount: 'always' as const,
  queryFn: async () =>
    (await api.get(apiPath`/families/${familyId}/report/sign-out-check${analysisQuery(analysisType)}`))
      .data as ApiReportSignoutCheck,
});

/** The check of `reportSignoutCheckQuery`, for one analysis. */
export const useReportSignoutCheck = (
  familyId: string | undefined,
  version: number | null,
  analysisType: string | null = null,
) => useQuery<ApiReportSignoutCheck>(reportSignoutCheckQuery(familyId, version, analysisType));

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
