import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import AdminModal from '../../components/AdminModal';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatDate, formatDateTime } from '../../lib/format';
import type {
  ClinvarLoadJobOut,
  ClinvarLoadStatusOut,
  ClinvarReleaseOut,
} from '../../lib/apiSchema.generated';

// How far a ClinVar load has come: the file downloaded, then its records stored.
const loadProgress = (job: ClinvarLoadJobOut) => {
  if (job.status === 'queued') return 'queued';
  if (!job.release) return 'looking for the newest release';
  if (job.records_read > 0) return `${job.records_stored.toLocaleString()} records stored`;
  const total = job.bytes_total ?? 0;
  return total > 0 ? `${Math.min(100, Math.floor((job.bytes_read / total) * 100))} % downloaded` : 'downloading';
};

const releaseCell = (release: ClinvarReleaseOut | null | undefined) =>
  release ? (
    <span title={release.source_version}>
      {release.release} · {release.records.toLocaleString()} records · loaded {formatDate(release.loaded_at)}
    </span>
  ) : (
    '—'
  );

// The newest load of each assembly, while it released nothing: its checks refused the file, or
// it could not be completed.
const failedLoads = (jobs: ClinvarLoadJobOut[]) => {
  const newest = new Map<string, ClinvarLoadJobOut>();
  for (const job of jobs) {
    if (!newest.has(job.assembly_name)) newest.set(job.assembly_name, job);
  }
  return [...newest.values()].filter((job) => job.status === 'refused' || job.status === 'failed');
};

// The ClinVar release CoGA loaded itself for each assembly ClinVar publishes a VCF for: the
// current one, the ClinVar every small variant shows and uses, and the one before it, with a load
// of NCBI's newest weekly VCF. For administrators.
const ClinvarReleasesPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ text: string; failed: boolean } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: status } = useQuery<ClinvarLoadStatusOut>({
    queryKey: ['admin', 'clinvar', 'status'],
    queryFn: async () => (await api.get('/admin/clinvar/status')).data as ClinvarLoadStatusOut,
    refetchInterval: (query) => (query.state.data?.active_job ? 3000 : false),
    retry: false,
  });
  if (!status || status.assemblies.length === 0) return null;

  const load = async (assemblyName: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await api.post('/admin/clinvar/load', { assembly: assemblyName });
      setNotice({ text: `Started the ClinVar load for ${assemblyName}.`, failed: false });
      await queryClient.invalidateQueries({ queryKey: ['admin', 'clinvar', 'status'] });
    } catch (err: unknown) {
      setNotice({ text: getErrorMessage(err, 'Could not start the ClinVar load.'), failed: true });
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  const active = status.active_job;
  const failed = failedLoads(status.recent_jobs);
  return (
    <section className="surface-card space-y-4" aria-label="ClinVar releases">
      <h2 className="section-title">ClinVar</h2>
      <p className="section-copy">
        The ClinVar release CoGA loaded from NCBI&apos;s weekly VCF: the ClinVar every small variant shows
        and every filter, ranking and criterion uses. The ClinVar in a callset&apos;s annotation is neither
        shown nor used. The current release is the one every screen reads; the one before it is kept.
      </p>
      <p className="section-copy">
        {status.weekly_load
          ? 'CoGA looks each day for a release newer than the current one and loads it; it becomes current only when its checks pass. A release its checks refused is not loaded again by the check, and every active administrator is told by mail; a load that could not be completed is tried again the next day.'
          : 'A newer release is loaded only when an administrator loads it here (the daily check, CLINVAR_WEEKLY_LOAD, is off).'}
      </p>
      {notice ? (
        <p
          className="section-copy"
          style={{ color: notice.failed ? 'var(--color-signature-red-dark)' : 'var(--color-secondary)' }}
        >
          {notice.text}
        </p>
      ) : null}
      {active ? (
        <p className="section-copy" aria-live="polite" aria-label="ClinVar load in progress">
          Loading ClinVar for {active.assembly_name}
          {active.release ? `, release ${active.release}` : ''}: {loadProgress(active)}. The current release
          stays until the whole file is stored and checked.
        </p>
      ) : null}
      {failed.map((job) => (
        <p
          key={job._id}
          className="section-copy"
          style={{ color: 'var(--color-signature-red-dark)' }}
          aria-label={`Failed ClinVar load for ${job.assembly_name}`}
        >
          The ClinVar load for {job.assembly_name} {job.status === 'refused' ? 'was refused' : 'failed'}
          {job.completed_at ? ` (${formatDateTime(job.completed_at)})` : ''}: {job.error || 'no reason was recorded'}
        </p>
      ))}
      <div className="data-table-shell">
        <table className="analysis-table">
          <thead>
            <tr>
              <th>Assembly</th>
              <th>Current release</th>
              <th>Previous release</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {status.assemblies.map((entry) => (
              <tr key={entry.assembly_name}>
                <td>{entry.assembly_name}</td>
                <td>{releaseCell(entry.current)}</td>
                <td>{releaseCell(entry.previous)}</td>
                <td>
                  <button
                    type="button"
                    className="button-secondary"
                    aria-label={`Load the newest ClinVar release for ${entry.assembly_name}`}
                    disabled={Boolean(active)}
                    onClick={() => setConfirming(entry.assembly_name)}
                  >
                    Load newest
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {confirming ? (
        <AdminModal title="Load ClinVar" onClose={() => (busy ? undefined : setConfirming(null))}>
          <div className="space-y-4">
            <p className="section-copy">
              Load ClinVar&apos;s newest weekly release for {confirming}? This downloads NCBI&apos;s weekly ClinVar
              VCF (about 190 MB), checks its MD5, its release date and its assembly, and makes it the current
              ClinVar release only once every record is stored and counted, with no more than 5 % fewer records
              than the current release. It runs in the background and may take several minutes.
            </p>
            <div className="analysis-toolbar items-center" style={{ justifyContent: 'flex-end', gap: '0.5rem' }}>
              <button
                type="button"
                className="button-ghost"
                onClick={() => setConfirming(null)}
                disabled={busy}
                // The confirmation opens on its safe choice.
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
              >
                Cancel
              </button>
              <button type="button" className="form-button" onClick={() => load(confirming)} disabled={busy}>
                {busy ? 'Working…' : 'Load'}
              </button>
            </div>
          </div>
        </AdminModal>
      ) : null}
    </section>
  );
};

export default ClinvarReleasesPanel;
