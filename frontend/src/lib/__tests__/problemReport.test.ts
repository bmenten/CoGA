// Where a user reports a problem (TF-15 §7, TF-16, TF-17): in the clinical build a problem
// goes through the CMGG route, never the public GitHub issue form.
import { afterEach, describe, expect, it, vi } from 'vitest';

import { currentProblemReportLink, resolveProblemReportLink } from '../problemReport';

const GITHUB_ISSUES = 'https://github.com/bmenten/coga/issues/new/choose';
const CMGGMC_ROUTE = 'https://cmggmc.example.org/probleemmelding';

describe('resolveProblemReportLink', () => {
  it('sends a problem report to the configured CMGGMC route, in any build', () => {
    for (const production of [true, false]) {
      expect(
        resolveProblemReportLink({
          reportUrl: ` ${CMGGMC_ROUTE} `,
          production,
          githubIssuesUrl: GITHUB_ISSUES,
        }),
      ).toEqual({ href: CMGGMC_ROUTE, label: 'Report a problem', newTab: true });
    }
  });

  it('opens a mail route in the mail client, not in a new tab', () => {
    expect(
      resolveProblemReportLink({
        reportUrl: 'mailto:probleemmelding@example.org',
        production: true,
        githubIssuesUrl: GITHUB_ISSUES,
      }),
    ).toEqual({ href: 'mailto:probleemmelding@example.org', label: 'Report a problem', newTab: false });
  });

  it('offers no link in a production build without the route, never the GitHub issue form', () => {
    for (const reportUrl of [undefined, '', '   ']) {
      expect(
        resolveProblemReportLink({ reportUrl, production: true, githubIssuesUrl: GITHUB_ISSUES }),
      ).toBeNull();
    }
  });

  it('keeps the GitHub issue form in a development build without the route', () => {
    expect(
      resolveProblemReportLink({ reportUrl: '', production: false, githubIssuesUrl: GITHUB_ISSUES }),
    ).toEqual({ href: GITHUB_ISSUES, label: 'Submit issue / request', newTab: true });
  });

  it('ignores a route that is not a web or mail address', () => {
    for (const reportUrl of ['javascript:alert(1)', 'cmggmc.example.org/probleemmelding', 'ftp://example.org']) {
      expect(
        resolveProblemReportLink({ reportUrl, production: true, githubIssuesUrl: GITHUB_ISSUES }),
      ).toBeNull();
    }
  });
});

describe('currentProblemReportLink', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('reads the route and the kind of build from the build variables', () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', CMGGMC_ROUTE);
    expect(currentProblemReportLink()?.href).toBe(CMGGMC_ROUTE);

    vi.stubEnv('VITE_PROBLEM_REPORT_URL', '');
    vi.stubEnv('PROD', true);
    expect(currentProblemReportLink()).toBeNull();

    vi.stubEnv('PROD', false);
    expect(currentProblemReportLink()?.href).toMatch(/^https:\/\/github\.com\//);
  });
});
