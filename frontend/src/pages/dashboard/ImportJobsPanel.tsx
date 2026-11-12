import React, { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  FamilyImportValidationIssue,
  FamilyPackageImportJobOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatTimestamp } from '../../lib/format';
import { routePath } from '../../lib/routePath';
import {
  datasetProgressText,
  jobProgressText,
  runningDatasetText,
  useNow,
} from './importProgress';

type ImportJob = FamilyPackageImportJobOut;

const activeStatuses = new Set<ImportJob['status']>(['queued', 'validating', 'running']);

// The table lists the latest 10 jobs; "Show all" lists every job the history returns, at most
// 100 (the endpoint's maximum, routers/family_imports.py), in a list that scrolls. Collapsed,
// the panel reads 25, as many as it needs to know there are more.
const RECENT_JOBS_SHOWN = 10;
const RECENT_JOBS_LIMIT = 25;
const ALL_JOBS_LIMIT = 100;

const issueLabel = (issue: FamilyImportValidationIssue) =>
  [issue.dataset, issue.sample_id, issue.path].filter(Boolean).join(' · ');

/** A running job's dataset in the jobs table: its share read and its time left. */
const RunningDatasetNote: React.FC<{ job: ImportJob; now: number }> = ({ job, now }) => {
  const text = job.status === 'running' ? runningDatasetText(job.datasets, now) : null;
  return text ? <div className="table-subtle">{text}</div> : null;
};

const IssueList: React.FC<{
  title: string;
  issues: FamilyImportValidationIssue[];
  className: string;
}> = ({ title, issues, className }) =>
  issues.length > 0 ? (
    <div>
      <h3 className="eyebrow-label">{title}</h3>
      <ul className={`mt-3 space-y-2 text-sm ${className}`}>
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${index}`}>
            {issue.message}
            {issueLabel(issue) ? ` (${issueLabel(issue)})` : ''}
          </li>
        ))}
      </ul>
    </div>
  ) : null;

/** One import job's report: its datasets, issues, progress and log. */
const ImportJobReport: React.FC<{ job: ImportJob; now: number }> = ({ job, now }) => {
  const counts = useMemo(
    () => ({
      imported: job.datasets.filter((dataset) => dataset.status === 'imported').length,
      registered: job.datasets.filter((dataset) => dataset.status === 'registered').length,
      failed: job.datasets.filter((dataset) => dataset.status === 'failed').length,
    }),
    [job.datasets]
  );
  const progress = job.status === 'running' ? jobProgressText(job.datasets, now) : null;
  return (
    <div className="surface-card-muted space-y-5">
      <div className="grid gap-3 md:grid-cols-4">
        <div className="stat-card">
          <span className="stat-label">Status</span>
          <span className="stat-value">{job.status}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Family</span>
          <span className="stat-value">{job.family_id || '—'}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Imported</span>
          <span className="stat-value">{counts.imported}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Registered</span>
          <span className="stat-value">{counts.registered}</span>
        </div>
      </div>

      <div className="text-sm leading-7 text-(--color-text-muted)">
        Requested {formatTimestamp(job.requested_at)} · Last update{' '}
        {formatTimestamp(job.heartbeat_at)}
        {job.manifest_sha256 ? (
          <>
            {' '}
            · Manifest kept in CoGA (SHA-256 <code>{job.manifest_sha256.slice(0, 12)}</code>)
          </>
        ) : null}
        {job.package_fingerprint ? (
          <>
            {' '}
            · Package fingerprint <code>{job.package_fingerprint.slice(0, 12)}</code>
          </>
        ) : null}
      </div>

      {progress ? <p className="status-note status-note--info">{progress}</p> : null}
      {job.error ? <p className="status-note status-note--error">{job.error}</p> : null}

      <IssueList
        title="Validation errors"
        issues={job.validation_errors}
        className="text-(--color-variant-del)"
      />
      <IssueList
        title="Warnings"
        issues={job.validation_warnings}
        className="text-(--color-text-muted)"
      />

      {job.datasets.length > 0 ? (
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Samples</th>
                <th>Files</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {job.datasets.map((dataset) => (
                <tr key={dataset.dataset_type}>
                  <td>{dataset.dataset_type}</td>
                  <td>{dataset.status}</td>
                  <td>{datasetProgressText(dataset, now) ?? '—'}</td>
                  <td>{dataset.samples.length ? dataset.samples.join(', ') : '—'}</td>
                  <td>{dataset.files.length}</td>
                  <td>{dataset.message || (dataset.enabled ? 'Ready' : 'Optional')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {job.logs.length > 0 ? (
        <div>
          <h3 className="eyebrow-label">Log</h3>
          <div className="mono-panel mt-3 max-h-44 overflow-y-auto !text-xs">
            {job.logs.map((line, index) => (
              <div key={`${line}-${index}`}>{line}</div>
            ))}
          </div>
        </div>
      ) : null}

      {job.status === 'completed' && job.family_id && !job.dry_run ? (
        <Link
          to={routePath`/families/${job.family_id}`}
          className="button-secondary hover:no-underline"
        >
          Open imported family
        </Link>
      ) : null}
      {counts.failed > 0 ? (
        <p className="status-note status-note--error">
          {counts.failed} dataset import step(s) failed.
        </p>
      ) : null}
    </div>
  );
};

/**
 * The import jobs an approved delivery queued (docs/data-import.md, "The import job"): the
 * latest 10 first, all of them on "Show all", each one's report on View, followed while it runs.
 */
const ImportJobsPanel: React.FC = () => {
  const [jobId, setJobId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const limit = showAll ? ALL_JOBS_LIMIT : RECENT_JOBS_LIMIT;

  const recentJobsQuery = useQuery<ImportJob[]>({
    queryKey: ['family-import-jobs', limit],
    queryFn: async () =>
      (await api.get('/family-imports', { params: { limit } })).data as ImportJob[],
    // The list stays while "Show all" loads the longer one.
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const jobs = query.state.data as ImportJob[] | undefined;
      return jobs?.some((job) => activeStatuses.has(job.status)) ? 5000 : 15000;
    },
  });
  const jobQuery = useQuery<ImportJob>({
    queryKey: ['family-import-job', jobId],
    queryFn: async () => (await api.get(apiPath`/family-imports/${jobId}`)).data as ImportJob,
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const job = query.state.data as ImportJob | undefined;
      return job && activeStatuses.has(job.status) ? 2500 : false;
    },
    retry: false,
  });

  const recentJobs = recentJobsQuery.data ?? [];
  const listedJobs = showAll ? recentJobs : recentJobs.slice(0, RECENT_JOBS_SHOWN);
  const hasMore = recentJobs.length > RECENT_JOBS_SHOWN;
  const currentJob = jobQuery.data;
  const anyJobRunning =
    recentJobs.some((job) => job.status === 'running') || currentJob?.status === 'running';
  const now = useNow(anyJobRunning);

  return (
    <section className="surface-card space-y-4 import-jobs-panel">
      <div className="catalog-card-header">
        <div>
          <p className="page-kicker">Job history</p>
          <h2 className="section-title">Recent family imports</h2>
        </div>
        <span className="badge-chip">{recentJobs.length}</span>
      </div>
      {recentJobsQuery.error ? (
        <p className="status-note status-note--error">
          {getErrorMessage(recentJobsQuery.error, 'Import job history could not be loaded.')}
        </p>
      ) : null}
      {recentJobs.length > 0 ? (
        <div
          className={`data-table-shell overflow-x-auto${showAll ? ' import-jobs-table--all' : ''}`}
        >
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                <th>Status</th>
                <th>Family</th>
                <th>Path</th>
                <th>Requested</th>
                <th>Last update</th>
                <th>Mode</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {listedJobs.map((job) => (
                <tr key={job._id}>
                  <td>
                    {job.status}
                    <RunningDatasetNote job={job} now={now} />
                  </td>
                  <td>{job.family_id || '—'}</td>
                  <td className="font-mono text-xs">{job.submitted_path}</td>
                  <td>{formatTimestamp(job.requested_at)}</td>
                  <td>{formatTimestamp(job.heartbeat_at || job.completed_at)}</td>
                  <td>{job.dry_run ? 'Dry run' : 'Import'}</td>
                  <td>
                    <button
                      type="button"
                      className="button-secondary"
                      onClick={() => setJobId(job._id)}
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="table-subtle">No family import jobs have been submitted yet.</p>
      )}
      {hasMore || showAll ? (
        <p className="import-jobs-more table-subtle">
          {showAll
            ? recentJobs.length >= ALL_JOBS_LIMIT
              ? `The latest ${ALL_JOBS_LIMIT} jobs, the most the history lists.`
              : `All ${recentJobs.length} jobs.`
            : `The latest ${RECENT_JOBS_SHOWN} jobs.`}{' '}
          <button
            type="button"
            className="button-link"
            onClick={() => setShowAll((current) => !current)}
          >
            {showAll ? `Show the latest ${RECENT_JOBS_SHOWN}` : 'Show all'}
          </button>
        </p>
      ) : null}
      {jobQuery.error ? (
        <p className="status-note status-note--error">
          {getErrorMessage(jobQuery.error, 'Import job status could not be loaded.')}
        </p>
      ) : null}
      {currentJob ? <ImportJobReport job={currentJob} now={now} /> : null}
    </section>
  );
};

export default ImportJobsPanel;
