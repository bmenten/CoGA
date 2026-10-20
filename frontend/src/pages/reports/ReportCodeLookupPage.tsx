import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ReportCodeLookupOut } from '../../lib/apiSchema.generated';
import { formatReportTime } from '../families/signedReportRecord';

type LookupError = { response?: { status?: number; data?: { detail?: unknown } } };

/** Why a code found no record, in words a reader can act on. */
const lookupErrorMessage = (error: unknown): string => {
  const response = (error as LookupError).response;
  const detail = response?.data?.detail;
  if (response?.status === 422) {
    const message =
      detail && typeof detail === 'object' ? (detail as { message?: unknown }).message : undefined;
    return typeof message === 'string' && message
      ? message
      : 'This is not a report code. Check it against the EPD report.';
  }
  if (response?.status === 404) {
    // The same for a code no record has and one whose family is outside your projects.
    return 'No signed record you can open has this report code. Check it against the EPD report.';
  }
  return 'The report code could not be looked up. Try again.';
};

/**
 * Finds a signed analysis record by the report code the EPD report quotes. The code names one
 * record, of one family and version; a code typed wrong is caught by its check symbol. The
 * record opens on the family's report page, rendered from its frozen record.
 */
const ReportCodeLookupPage: React.FC = () => {
  const { code: codeParam } = useParams<{ code?: string }>();
  const navigate = useNavigate();
  const code = (codeParam ?? '').trim();
  const [draft, setDraft] = useState(code);
  useEffect(() => setDraft(code), [code]);

  const lookup = useQuery<ReportCodeLookupOut>({
    queryKey: ['report-code', code],
    enabled: Boolean(code),
    retry: false,
    queryFn: async () => (await api.get(apiPath`/report-codes/${code}`)).data as ReportCodeLookupOut,
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const typed = draft.trim();
    if (typed) navigate(`/report-codes/${encodeURIComponent(typed)}`);
  };

  const found = lookup.data;
  return (
    <div className="page-shell space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-1">
            <p className="page-kicker">Signed records</p>
            <h2 className="catalog-card-title">Find a signed record by its report code</h2>
            <p className="catalog-card-copy">
              A sign-out in CoGA is the analytical sign-off of a family’s analysis. The EPD report
              of the medical sign-off refers to the signed record by its report code; enter it to
              open that record.
            </p>
          </div>
        </div>
        <form className="report-code-form" onSubmit={submit}>
          <label className="field-label">
            <span>Report code</span>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="7KQM2X-2-4PHT"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <button type="submit" className="form-button" disabled={!draft.trim()}>
            Find the signed record
          </button>
        </form>
      </section>

      {code && lookup.isLoading ? (
        <section className="surface-card">
          <p className="report-paragraph">Looking up report code {code}…</p>
        </section>
      ) : null}
      {code && lookup.isError ? (
        <section className="surface-card report-incomplete" role="alert">
          <p className="report-paragraph">{lookupErrorMessage(lookup.error)}</p>
        </section>
      ) : null}
      {found ? (
        <section className="surface-card report-signout" aria-label={`Report code ${found.report_code}`}>
          <p className="report-signout-line">
            Report code {found.report_code}: signed version {found.version} of family{' '}
            {found.family_id}
          </p>
          <p className="report-signout-status">
            Signed out by <strong>{found.signed_out_by}</strong> on{' '}
            {formatReportTime(found.signed_out_at)}.
          </p>
          <p className="report-signout-hash">
            <span className="report-footer-label">Content hash</span> {found.content_hash}
          </p>
          <p className="report-signout-actions">
            <Link
              className="form-button"
              to={`/families/${encodeURIComponent(found.family_id)}/report?version=${found.version}`}
            >
              Open signed version {found.version}
            </Link>
          </p>
        </section>
      ) : null}
    </div>
  );
};

export default ReportCodeLookupPage;
