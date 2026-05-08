import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';

interface GeneInfoRefreshJob {
  _id: string;
  scope: 'symbol' | 'all_human';
  symbol?: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed';
  requested_by: string;
  requested_at: string;
  started_at?: string | null;
  heartbeat_at?: string | null;
  completed_at?: string | null;
  total_symbols: number;
  completed_symbols: number;
  updated_records: number;
  human_assemblies: number;
  current_symbol?: string | null;
  error?: string | null;
  metadata: Record<string, unknown>;
}

interface GeneInfoSourceSummary {
  source: string;
  latest_fetched_at?: string | null;
  success_count: number;
  missing_count: number;
  error_count: number;
  record_count: number;
}

interface GeneReferenceAdminStatus {
  active_job?: GeneInfoRefreshJob | null;
  recent_jobs: GeneInfoRefreshJob[];
  source_summaries: GeneInfoSourceSummary[];
  total_cached_records: number;
  human_gene_symbols: number;
  human_assemblies: number;
  last_completed_at?: string | null;
}

const formatTimestamp = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const formatNumber = (value: number) => new Intl.NumberFormat().format(value);

const progressPercent = (job: GeneInfoRefreshJob) => {
  if (!job.total_symbols) return 0;
  return Math.max(0, Math.min(100, Math.round((job.completed_symbols / job.total_symbols) * 100)));
};

const GeneReferenceAdminPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [symbol, setSymbol] = useState('');
  const [status, setStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);

  const {
    data,
    isLoading,
    error,
  } = useQuery<GeneReferenceAdminStatus>({
    queryKey: ['admin', 'gene-reference-status'],
    queryFn: async () => {
      const response = await api.get('/admin/gene-reference/status');
      return response.data as GeneReferenceAdminStatus;
    },
    refetchInterval: (query) => {
      const payload = query.state.data as GeneReferenceAdminStatus | undefined;
      const active = payload?.active_job;
      return active && (active.status === 'queued' || active.status === 'running') ? 3000 : 15000;
    },
    retry: false,
  });

  const refreshData = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin', 'gene-reference-status'] });
    await queryClient.invalidateQueries({ queryKey: ['gene-profile'] });
  };

  const refreshAllMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/admin/gene-reference/refresh-all');
      return response.data as GeneInfoRefreshJob;
    },
    onSuccess: async (job) => {
      setStatus({ tone: 'success', message: `Started bulk refresh job ${job._id}.` });
      await refreshData();
    },
    onError: (mutationError) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(mutationError, 'Could not start the bulk gene reference refresh.'),
      });
    },
  });

  const refreshGeneMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/admin/gene-reference/refresh-gene', null, {
        params: { symbol: symbol.trim() },
      });
      return response.data as GeneInfoRefreshJob;
    },
    onSuccess: async (job) => {
      setStatus({ tone: 'success', message: `Started gene refresh job for ${job.symbol}.` });
      setSymbol('');
      await refreshData();
    },
    onError: (mutationError) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(mutationError, 'Could not start the single-gene refresh.'),
      });
    },
  });

  const activeJob = data?.active_job ?? null;
  const lastCompleted = useMemo(
    () => data?.recent_jobs.find((job) => job.status === 'completed') ?? null,
    [data?.recent_jobs],
  );

  if (isLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading gene reference sync"
        message="Preparing the cached gene reference job status and provider summaries."
      />
    );
  }

  if (error || !data) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load gene reference sync"
        message={getErrorMessage(error, 'The gene reference sync status could not be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Gene reference sync</h1>
            <p className="catalog-card-copy">
              Refresh cached human gene context for one symbol or for the full imported catalog. The
              sync merges HGNC, Ensembl, NCBI Gene, ClinGen, GenCC, ClinVar gene-condition data,
              and optional local dbNSFP gene annotations.
            </p>
          </div>
          <div className="surface-card-muted gene-profile-status">
            <span className="gene-profile-status-label">Last completed sync</span>
            <strong>{formatTimestamp(data.last_completed_at)}</strong>
            <span className="dashboard-link-note">
              {formatNumber(data.human_gene_symbols)} human genes across {data.human_assemblies} assemblies
            </span>
          </div>
        </div>

        <div className="gene-sync-summary-grid">
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Cached records</span>
            <strong>{formatNumber(data.total_cached_records)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Imported human genes</span>
            <strong>{formatNumber(data.human_gene_symbols)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Assemblies</span>
            <strong>{data.human_assemblies}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Latest job</span>
            <strong>{activeJob ? activeJob.status : lastCompleted?.status || 'idle'}</strong>
          </div>
        </div>

        <div className="gene-sync-action-grid">
          <article className="surface-card-muted gene-sync-action-panel">
            <p className="section-title">Refresh one human gene</p>
            <p className="dashboard-link-note">
              Use this when you want to update the cached context for a specific symbol without
              touching the rest of the local gene catalog.
            </p>
            <div className="gene-sync-inline-form">
              <label className="field-label">
                Gene symbol
                <input
                  aria-label="Refresh gene symbol"
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="BRCA1"
                />
              </label>
              <button
                type="button"
                className="form-button"
                onClick={() => refreshGeneMutation.mutate()}
                disabled={!symbol.trim() || refreshGeneMutation.isPending || Boolean(activeJob)}
              >
                Refresh gene
              </button>
            </div>
          </article>

          <article className="surface-card-muted gene-sync-action-panel">
            <p className="section-title">Refresh all imported human genes</p>
            <p className="dashboard-link-note">
              This updates the cache for every locally imported human gene so the explorer remains
              available offline after the job finishes.
            </p>
            <div className="inline-actions">
              <button
                type="button"
                className="button-secondary"
                onClick={() => refreshAllMutation.mutate()}
                disabled={refreshAllMutation.isPending || Boolean(activeJob)}
              >
                Refresh all human genes
              </button>
              <Link to="/genes" className="button-secondary hover:no-underline">
                Open gene explorer
              </Link>
            </div>
          </article>
        </div>

        {status ? (
          <div className={`status-note ${status.tone === 'error' ? 'status-note--error' : 'status-note--success'}`}>
            {status.message}
          </div>
        ) : null}
      </section>

      <section className="gene-profile-grid">
        <article className="surface-card">
          <div className="catalog-card-header">
            <div>
              <p className="page-kicker">Job status</p>
              <h2 className="catalog-card-title">Active refresh job</h2>
            </div>
            <span className="badge-chip badge-chip--signature">{activeJob ? activeJob.status : 'Idle'}</span>
          </div>
          {activeJob ? (
            <div className="gene-sync-job-card mt-4">
              <div className="gene-assembly-item-head">
                <strong>{activeJob.scope === 'all_human' ? 'All imported human genes' : activeJob.symbol}</strong>
                <span className="variant-card-chip variant-card-chip--soft">{activeJob.scope}</span>
              </div>
              <p className="dashboard-link-note">
                Requested by {activeJob.requested_by} on {formatTimestamp(activeJob.requested_at)}
              </p>
              <div className="gene-sync-progress-shell">
                <div className="gene-sync-progress-bar" style={{ width: `${progressPercent(activeJob)}%` }} />
              </div>
              <div className="gene-sync-job-metrics">
                <span>{progressPercent(activeJob)}%</span>
                <span>
                  {formatNumber(activeJob.completed_symbols)} / {formatNumber(activeJob.total_symbols || 0)} genes
                </span>
                <span>{formatNumber(activeJob.updated_records)} cached records</span>
              </div>
              <p className="dashboard-link-note">
                {activeJob.current_symbol ? `Current gene: ${activeJob.current_symbol}` : 'Waiting for the next update tick.'}
              </p>
              {activeJob.error ? <p className="status-note status-note--error">{activeJob.error}</p> : null}
            </div>
          ) : (
            <p className="dashboard-link-note mt-4">
              No refresh job is active at the moment.
            </p>
          )}
        </article>

        <article className="surface-card">
          <div className="catalog-card-header">
            <div>
              <p className="page-kicker">Providers</p>
              <h2 className="catalog-card-title">Cached source coverage</h2>
            </div>
            <span className="badge-chip">{data.source_summaries.length}</span>
          </div>
          <div className="data-table-shell overflow-x-auto mt-4">
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Latest fetch</th>
                  <th>Success</th>
                  <th>Missing</th>
                  <th>Error</th>
                  <th>Records</th>
                </tr>
              </thead>
              <tbody>
                {data.source_summaries.map((source) => (
                  <tr key={source.source}>
                    <td>{source.source.toUpperCase()}</td>
                    <td>{formatTimestamp(source.latest_fetched_at)}</td>
                    <td>{formatNumber(source.success_count)}</td>
                    <td>{formatNumber(source.missing_count)}</td>
                    <td>{formatNumber(source.error_count)}</td>
                    <td>{formatNumber(source.record_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      </section>

      <section className="surface-card">
        <div className="catalog-card-header">
          <div>
            <p className="page-kicker">History</p>
            <h2 className="catalog-card-title">Recent gene reference jobs</h2>
          </div>
          <span className="badge-chip">{data.recent_jobs.length}</span>
        </div>
        <div className="data-table-shell overflow-x-auto mt-4">
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                <th>Requested</th>
                <th>Scope</th>
                <th>Symbol</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Records</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_jobs.map((job) => (
                <tr key={job._id}>
                  <td>{formatTimestamp(job.requested_at)}</td>
                  <td>{job.scope}</td>
                  <td>{job.symbol || 'All human genes'}</td>
                  <td>{job.status}</td>
                  <td>
                    {formatNumber(job.completed_symbols)} / {formatNumber(job.total_symbols || 0)}
                  </td>
                  <td>{formatNumber(job.updated_records)}</td>
                  <td>{formatTimestamp(job.completed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default GeneReferenceAdminPage;
