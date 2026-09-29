import { githubIssuesUrl } from './githubLinks';

/**
 * Where a user reports a problem with CoGA (TF-15 §7, TF-16, TF-17).
 *
 * In the clinical build a problem or an incident goes through the CMGG route, a CMGGMC
 * probleemmelding, and never through the public GitHub issue form: SECURITY.md keeps clinical
 * incidents off GitHub. The build variable VITE_PROBLEM_REPORT_URL names that route (a web
 * or a mailto: address). Without it, a development build keeps the GitHub issue form, and a
 * production build offers no link at all rather than sending a user to GitHub.
 */
export interface ProblemReportLink {
  href: string;
  label: string;
  /** A web page opens in a new tab; a mailto: address opens the mail client. */
  newTab: boolean;
}

const ROUTE_ADDRESS = /^(https?:\/\/|mailto:)/i;

export const resolveProblemReportLink = ({
  reportUrl,
  production,
  githubIssuesUrl: githubIssues,
}: {
  reportUrl?: string;
  production: boolean;
  githubIssuesUrl: string;
}): ProblemReportLink | null => {
  const route = reportUrl?.trim() ?? '';
  if (ROUTE_ADDRESS.test(route)) {
    return { href: route, label: 'Report a problem', newTab: !/^mailto:/i.test(route) };
  }
  if (production) {
    return null;
  }
  return { href: githubIssues, label: 'Submit issue / request', newTab: true };
};

/** The link for this build: read from the build variables when it is shown. */
export const currentProblemReportLink = (): ProblemReportLink | null =>
  resolveProblemReportLink({
    reportUrl: import.meta.env.VITE_PROBLEM_REPORT_URL,
    production: import.meta.env.PROD,
    githubIssuesUrl,
  });
