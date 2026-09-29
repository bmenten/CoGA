import { useQuery } from '@tanstack/react-query';

import api from './api';
import { apiPath } from './apiPath';
import type { VersionOut } from './apiSchema.generated';

/**
 * The running build (TF-18 §2): the semantic version and git commit the backend reports at
 * /api/version. The app footer and every report footer show it (TF-15 §1); the same values
 * are frozen into each signed report.
 */
export const APP_VERSION_QUERY_KEY = ['app-version'] as const;

const APP_FOOTER_STALE_TIME_MS = 5 * 60 * 1000;

/** The running build. A response that names no version fails: the footer then says so. */
export const fetchAppVersion = async (): Promise<VersionOut> => {
  const { data } = await api.get<VersionOut>(apiPath`/version`);
  if (
    !data ||
    typeof data.version !== 'string' ||
    !data.version.trim() ||
    typeof data.git_sha !== 'string'
  ) {
    throw new Error('The server did not report its version.');
  }
  return data;
};

/** `0.1.0 (0123456)`: the version and the short git SHA, left out when the build has none. */
export const formatBuild = (version: string, gitSha?: string | null): string =>
  `${version}${gitSha && gitSha !== 'unknown' ? ` (${gitSha.slice(0, 7)})` : ''}`;

/** `CoGA 0.1.0 (0123456)`, as reports and the signed record name the software. */
export const formatSoftwareVersion = (version: string, gitSha?: string | null): string =>
  `CoGA ${formatBuild(version, gitSha)}`;

/** The running build for the app footer. */
export const useAppVersion = () =>
  useQuery({
    queryKey: APP_VERSION_QUERY_KEY,
    queryFn: fetchAppVersion,
    staleTime: APP_FOOTER_STALE_TIME_MS,
  });

export interface ReportBuild {
  /** The build the server reported after the report was opened; absent until then. */
  build?: VersionOut;
  /** It could not be loaded: the report says so, and a printout says it is incomplete. */
  failed: boolean;
  retry: () => void;
}

/**
 * The running build as a report states it. Asked again each time the report is opened, and
 * shown only once that answer is in: a build cached before a redeploy must not stand on a
 * report the new build rendered.
 */
export const useReportBuild = (): ReportBuild => {
  const query = useQuery({
    queryKey: APP_VERSION_QUERY_KEY,
    queryFn: fetchAppVersion,
    staleTime: 0,
    refetchOnMount: 'always',
  });
  return {
    build: query.isFetchedAfterMount && !query.isError ? query.data : undefined,
    failed: query.isError,
    retry: () => {
      void query.refetch();
    },
  };
};
