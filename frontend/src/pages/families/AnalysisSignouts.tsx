import React from 'react';
import { Link, type To } from 'react-router';
import { useQueries } from '@tanstack/react-query';

import type { ApiReportSignoutCheck, ApiReportSignoutList } from '../../lib/apiTypes';
import { analysisLabel } from '../../lib/familyAnalyses';
import { latestSignoutOf, reportSignoutCheckQuery } from './reportSignoutQueries';
import { describeReportSections, formatReportTime } from './signedReportRecord';

/** An analysis's name at the start of a line: "Monogenic NIPT". */
const capitalised = (analysisType: string): string => {
  const label = analysisLabel(analysisType) ?? analysisType;
  return label.charAt(0).toUpperCase() + label.slice(1);
};

/** Whether the family's data still match an analysis's latest signed version, in words. */
const checkWords = (
  version: number,
  check: { data?: ApiReportSignoutCheck; isError: boolean },
): React.ReactNode => {
  if (check.isError) return `It could not be checked against the family's data — treat it as unverified.`;
  if (!check.data) return 'Checking it against the family’s data…';
  if (check.data.version !== version || typeof check.data.matches !== 'boolean') {
    return `It could not be checked against the family's data — treat it as unverified.`;
  }
  if (check.data.matches) return 'The family’s data still match it.';
  return (
    <>
      <strong>⚠ Changed since sign-out:</strong> {describeReportSections(check.data.changed_sections)}.
    </>
  );
};

/**
 * Each analysis of a family that holds several, with its latest signed version and whether the
 * family's data still match it: each analysis is signed out on its own, from its own report
 * (the owner's decisions of 2026-11-19), so the family's Report page shows where each stands.
 * `signedOutOn` names the page that signs an analysis out when it is not this one;
 * `versionLink` opens a signed version.
 */
const AnalysisSignouts: React.FC<{
  familyId: string;
  analyses: string[];
  signouts: ApiReportSignoutList | undefined;
  versionLink: (version: number) => To;
  signedOutOn: (analysis: string) => React.ReactNode;
}> = ({ familyId, analyses, signouts, versionLink, signedOutOn }) => {
  const latest = analyses.map((analysisType) => latestSignoutOf(signouts, analysisType));
  const checks = useQueries({
    queries: analyses.map((analysisType, index) =>
      reportSignoutCheckQuery(familyId, latest[index]?.version ?? null, analysisType),
    ),
  });
  return (
    <section className="surface-card report-analysis-signouts" aria-label="Sign-out of each analysis">
      <h2 className="report-audit-heading">Sign-out of each analysis</h2>
      <p className="report-paragraph">
        This family holds {analyses.length} analyses, each signed out on its own: the sign-out of one
        freezes and checks its own data only.
      </p>
      <ul className="report-analysis-signout-list">
        {analyses.map((analysisType, index) => {
          const signed = latest[index];
          const where = signedOutOn(analysisType);
          return (
            <li key={analysisType}>
              <strong>{capitalised(analysisType)}:</strong>{' '}
              {signed ? (
                <>
                  <Link to={versionLink(signed.version)}>signed version {signed.version}</Link>
                  {signed.report_code ? ` (report code ${signed.report_code})` : ''}, signed out by{' '}
                  {signed.signed_out_by} on {formatReportTime(signed.signed_out_at)}.{' '}
                  {checkWords(signed.version, checks[index])}
                </>
              ) : (
                'not signed out.'
              )}
              {where ? <> {where}</> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
};

export default AnalysisSignouts;
