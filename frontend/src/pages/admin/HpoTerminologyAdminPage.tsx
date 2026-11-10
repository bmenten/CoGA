import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import type {
  HpoAdminSummaryOut,
  HpoAdminTermOut,
  HpoOntologySyncOut,
  HpoOntologySyncRequest,
  HpoReleaseCheckOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatDate, formatDateTime } from '../../lib/format';

// As the backend serves them, the summary with the ontology file it is configured to load, a
// sync with the release its file states and the SHA-256 of its bytes (TRACE-18).
type HpoAdminTerm = HpoAdminTermOut;
type HpoAdminSummary = HpoAdminSummaryOut;
type HpoSyncResult = HpoOntologySyncOut;

const formatNumber = (value?: number | null) =>
  typeof value === 'number' ? value.toLocaleString() : '0';

const previewLabels: Record<string, string> = {
  terms: 'Terms in release',
  active_terms: 'Active terms',
  obsolete_terms: 'Obsolete terms',
  replaced_terms: 'Replaced terms',
  synonyms: 'Synonyms',
  edges: 'Relationships',
  closure_rows: 'Closure rows',
  new_terms: 'New terms',
  changed_terms: 'Changed terms',
  unchanged_terms: 'Unchanged terms',
  removed_from_release: 'Missing from new release',
};

// One empty list, so the memo and effect below see a stable value before data arrive.
const NO_TERMS: HpoAdminTerm[] = [];

const HpoTerminologyAdminPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  // The path the admin typed, if any. Until then the field holds the file the backend is
  // configured to load, from the summary: the page's own copy of that default had drifted
  // from the backend's (hpo.obo against hp.obo).
  const [syncPathDraft, setSyncPathDraft] = useState<string | null>(null);
  // Where the ontology comes from: a release HPO publishes, downloaded from its own PURL, or a
  // file on the server.
  const [syncSource, setSyncSource] = useState<'download' | 'file'>('file');
  const [downloadRelease, setDownloadRelease] = useState('');
  const [releaseVersion, setReleaseVersion] = useState('');
  const [releaseDate, setReleaseDate] = useState('');
  const [selectedTermId, setSelectedTermId] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<HpoSyncResult | null>(null);
  // The request the shown result answers. Apply goes out only for exactly the previewed
  // file and overrides: it used to take whatever the fields held, with or without a
  // preview, while the preview of another file stayed on screen (#526).
  const [resultRequest, setResultRequest] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setAppliedSearch(search.trim());
    }, 300);

    return () => clearTimeout(timer);
  }, [search]);

  const handleSearch = () => {
    const trimmedSearch = search.trim();
    if (trimmedSearch === appliedSearch) {
      termsQuery.refetch();
      return;
    }
    setAppliedSearch(trimmedSearch);
  };
  const [status, setStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);

  const summaryQuery = useQuery<HpoAdminSummary>({
    queryKey: ['admin', 'hpo', 'summary'],
    queryFn: async () => {
      const response = await api.get('/admin/hpo/summary');
      return response.data as HpoAdminSummary;
    },
    retry: false,
  });
  const configuredPath = summaryQuery.data?.ontology_path ?? '';
  const syncPath = syncPathDraft ?? configuredPath;

  // The newest release HPO has published, beside the loaded one. Read when the page opens;
  // HPO unreachable is said in the answer's error.
  const releaseCheckQuery = useQuery<HpoReleaseCheckOut>({
    queryKey: ['admin', 'hpo', 'release-check'],
    queryFn: async () => (await api.get('/admin/hpo/release-check')).data as HpoReleaseCheckOut,
    staleTime: 15 * 60 * 1000,
    retry: false,
  });
  const releaseCheck = releaseCheckQuery.data;

  const termsQuery = useQuery<HpoAdminTerm[]>({
    queryKey: ['admin', 'hpo', 'terms', appliedSearch],
    queryFn: async ({ signal }: { signal?: AbortSignal }) => {
      const response = await api.get('/admin/hpo/terms', {
        params: {
          q: appliedSearch || undefined,
          limit: 100,
        },
        signal,
      });
      return response.data as HpoAdminTerm[];
    },
    retry: false,
  });

  const terms = termsQuery.data ?? NO_TERMS;
  const selectedTerm = useMemo(
    () => terms.find((term) => term.hpo_id === selectedTermId) ?? terms[0] ?? null,
    [selectedTermId, terms]
  );

  useEffect(() => {
    if (!selectedTerm) {
      setSelectedTermId(null);
      return;
    }
    setSelectedTermId((current) =>
      current && terms.some((term) => term.hpo_id === current)
        ? current
        : selectedTerm.hpo_id
    );
  }, [selectedTerm, terms]);

  const syncRequest: HpoOntologySyncRequest =
    syncSource === 'download'
      ? { download_release: downloadRelease.trim() }
      : {
          path: syncPath.trim(),
          release_version: releaseVersion.trim() || null,
          release_date: releaseDate || null,
        };
  const syncRequestKey = JSON.stringify(syncRequest);
  const shownResult = resultRequest === syncRequestKey ? syncResult : null;
  const previewedCurrentRequest = Boolean(shownResult?.preview_only);

  const syncMutation = useMutation({
    mutationFn: async ({ previewOnly }: { previewOnly: boolean; requestKey: string }) => {
      const response = await api.post('/admin/hpo/sync', {
        ...syncRequest,
        // Apply imports exactly the bytes the preview showed: a file changed since, or a
        // release HPO serves anew, is refused (TRACE-18).
        ...(previewOnly ? {} : { expected_sha256: shownResult?.release_sha256 ?? null }),
        preview_only: previewOnly,
      });
      return response.data as HpoSyncResult;
    },
    onSuccess: async (result, { requestKey }) => {
      setSyncResult(result);
      setResultRequest(requestKey);
      setStatus({
        tone: 'success',
        message: result.preview_only
          ? 'HPO synchronization preview is ready.'
          : 'HPO terminology synchronized.',
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin', 'hpo'] }),
        queryClient.invalidateQueries({ queryKey: ['hpo'] }),
      ]);
    },
    onError: (error) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'HPO synchronization failed.'),
      });
    },
  });

  if (summaryQuery.isLoading) {
    return (
      <PageState
        loading
        kicker="Administration"
        title="Loading HPO terminology"
        message="Preparing ontology summary and term browser."
      />
    );
  }

  if (summaryQuery.error) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load HPO terminology"
        message={getErrorMessage(summaryQuery.error, 'HPO summary could not be loaded.')}
      />
    );
  }

  const summary = summaryQuery.data;
  const syncTarget = syncSource === 'download' ? downloadRelease.trim() : syncPath.trim();
  const canSync = syncTarget.length > 0 && !syncMutation.isPending;
  const canApply = canSync && previewedCurrentRequest;
  const pendingAction = syncMutation.isPending
    ? syncMutation.variables?.previewOnly
      ? 'preview'
      : 'apply'
    : null;
  const ontologyLoaded = summary?.ontology_loaded ?? false;

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">HPO terminology</h1>
            <p className="catalog-card-copy">
              Browse imported Human Phenotype Ontology terms, review release status, and synchronize ontology files.
            </p>
          </div>
        </div>
      </section>

      <section className="surface-card-muted admin-data-summary" aria-label="HPO statistics">
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Total terms</span>
          <strong className="admin-data-summary-value">{formatNumber(summary?.total_terms)}</strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Active</span>
          <strong className="admin-data-summary-value">{formatNumber(summary?.active_terms)}</strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Obsolete</span>
          <strong className="admin-data-summary-value">{formatNumber(summary?.obsolete_terms)}</strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Release</span>
          <strong className="admin-data-summary-value">{summary?.release_version || 'Unknown'}</strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Release date</span>
          <strong className="admin-data-summary-value">
            {formatDate(summary?.release_date, ontologyLoaded ? 'Unknown' : 'Not installed')}
          </strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">File SHA-256</span>
          <strong className="admin-data-summary-value font-mono text-xs" title={summary?.release_sha256 ?? undefined}>
            {summary?.release_sha256 ? `${summary.release_sha256.slice(0, 12)}…` : 'Not installed'}
          </strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Last sync</span>
          <strong className="admin-data-summary-value">{formatDateTime(summary?.last_sync_date, 'Not synced')}</strong>
        </div>
      </section>

      {!ontologyLoaded ? (
        <p className="status-note status-note--error">
          No HPO terminology is installed. Preview and apply an HPO synchronization file to load terms.
        </p>
      ) : null}

      {status ? (
        <p className={`status-note ${status.tone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
          {status.message}
        </p>
      ) : null}

      <section className="surface-card-flat space-y-4">
        <div className="catalog-card-header">
          <div className="space-y-2">
            <h2 className="section-title">HPO synchronization</h2>
            <p className="section-copy">
              Preview ontology changes before applying an HPO release, downloaded from HPO or from a file on the
              server. The release is the one the file states, and the terms keep the file&apos;s SHA-256. An
              expected release or date that the file does not state stops the sync, and Apply imports only the
              file the preview showed.
            </p>
          </div>
        </div>
        <div className="space-y-2" aria-label="HPO release check">
          {releaseCheckQuery.isLoading ? (
            <p className="status-note status-note--info">Checking HPO for a newer release…</p>
          ) : releaseCheckQuery.error || releaseCheck?.error ? (
            <p className="status-note status-note--warning">
              Could not check HPO for a newer release:{' '}
              {releaseCheck?.error ?? getErrorMessage(releaseCheckQuery.error, 'the check failed.')}
            </p>
          ) : releaseCheck?.newer_available && releaseCheck.latest_release_date ? (
            <div className="status-note status-note--info action-row">
              <span>
                A newer HPO release is available: <strong>{releaseCheck.latest_release_date}</strong>
                {releaseCheck.published_at ? ` (published ${formatDate(releaseCheck.published_at)})` : ''}. Loaded:{' '}
                {releaseCheck.loaded_release_date ?? 'none'}.
              </span>
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setSyncSource('download');
                  setDownloadRelease(releaseCheck.latest_release_date ?? '');
                }}
              >
                Use this release
              </button>
            </div>
          ) : releaseCheck?.latest_release_date ? (
            <p className="status-note status-note--success">
              The loaded release is HPO&apos;s newest ({releaseCheck.latest_release_date}).
            </p>
          ) : null}
          {releaseCheck ? (
            <p className="table-subtle">
              A start on an empty database downloads and imports HPO&apos;s newest release. When HPO cannot be
              reached, it imports the vendored release {releaseCheck.pinned_release_date} instead.
            </p>
          ) : null}
        </div>
        <div className="action-row" role="radiogroup" aria-label="Ontology source">
          <label className="analysis-checkbox">
            <input
              type="radio"
              name="hpo-sync-source"
              checked={syncSource === 'download'}
              onChange={() => setSyncSource('download')}
            />
            Download an HPO release
          </label>
          <label className="analysis-checkbox">
            <input
              type="radio"
              name="hpo-sync-source"
              checked={syncSource === 'file'}
              onChange={() => setSyncSource('file')}
            />
            File on the server
          </label>
        </div>
        {syncSource === 'download' ? (
          <label className="field-label" htmlFor="hpo-download-release">
            HPO release
            <input
              id="hpo-download-release"
              value={downloadRelease}
              onChange={(event) => setDownloadRelease(event.target.value)}
              placeholder={releaseCheck?.latest_release_date ?? 'e.g. 2026-09-01'}
            />
          </label>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(180px,0.4fr)_minmax(180px,0.4fr)]">
            <label className="field-label" htmlFor="hpo-sync-path">
              Ontology file path
              <input
                id="hpo-sync-path"
                value={syncPath}
                onChange={(event) => setSyncPathDraft(event.target.value)}
                placeholder={configuredPath || 'Path to the ontology file on the server'}
              />
            </label>
            <label className="field-label" htmlFor="hpo-release-version">
              Expected release
              <input
                id="hpo-release-version"
                value={releaseVersion}
                onChange={(event) => setReleaseVersion(event.target.value)}
                placeholder="Optional, e.g. 2026-02-16"
              />
            </label>
            <label className="field-label" htmlFor="hpo-release-date">
              Expected release date
              <input
                id="hpo-release-date"
                type="date"
                value={releaseDate}
                onChange={(event) => setReleaseDate(event.target.value)}
              />
            </label>
          </div>
        )}
        <div className="action-row">
          <button
            type="button"
            className="button-secondary"
            disabled={!canSync}
            onClick={() => syncMutation.mutate({ previewOnly: true, requestKey: syncRequestKey })}
          >
            {pendingAction === 'preview' ? 'Checking...' : 'Preview changes'}
          </button>
          <button
            type="button"
            className="form-button"
            disabled={!canApply}
            onClick={() => {
              const what =
                syncSource === 'download'
                  ? `HPO release ${downloadRelease.trim()}, downloaded from HPO,`
                  : `the HPO ontology in ${syncPath.trim()}`;
              if (window.confirm(`Apply ${what} now? The terminology is replaced as previewed.`)) {
                syncMutation.mutate({ previewOnly: false, requestKey: syncRequestKey });
              }
            }}
          >
            {pendingAction === 'apply' ? 'Synchronizing...' : 'Apply sync'}
          </button>
        </div>
        {!previewedCurrentRequest && syncTarget && !syncMutation.isPending ? (
          <p className="table-subtle">Preview these settings first; Apply sync applies what was previewed.</p>
        ) : null}
        {shownResult ? (
          <p className="table-subtle">
            File: release {shownResult.release_version || 'not stated'}
            {shownResult.release_date ? ` (${shownResult.release_date})` : ''}, SHA-256{' '}
            <span className="font-mono">{shownResult.release_sha256 || 'not computed'}</span>.
          </p>
        ) : null}
        {shownResult ? (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Change</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(shownResult.preview).map(([key, value]) => (
                  <tr key={key}>
                    <td>{previewLabels[key] || key}</td>
                    <td>{formatNumber(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="surface-card space-y-4">
        <div className="catalog-card-header">
          <div className="space-y-2">
            <h2 className="section-title">HPO overview</h2>
            <p className="section-copy">
              Search by HPO ID, name, or synonym. Results include obsolete terms for administrative review.
            </p>
          </div>
          <span className="badge-chip">{terms.length} shown</span>
        </div>
        <label className="field-label" htmlFor="hpo-admin-search">
          Search terms
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto]">
            <input
              id="hpo-admin-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  handleSearch();
                }
              }}
              placeholder="HP:0001250, seizure, synonym..."
            />
            <button type="button" className="button-secondary" onClick={handleSearch}>
              Search
            </button>
          </div>
        </label>
        {termsQuery.error ? (
          <p className="status-note status-note--error">
            {getErrorMessage(termsQuery.error, 'HPO terms could not be loaded.')}
          </p>
        ) : null}
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>HPO ID</th>
                  <th>Name</th>
                  <th>Synonyms</th>
                  <th>Parents</th>
                  <th>Children</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {terms.map((term) => (
                  <tr
                    key={term.hpo_id}
                    onClick={() => setSelectedTermId(term.hpo_id)}
                    className="cursor-pointer"
                  >
                    <td className="font-mono text-xs">{term.hpo_id}</td>
                    <td>{term.label}</td>
                    <td>{term.synonyms.length}</td>
                    <td>{term.parent_count}</td>
                    <td>{term.child_count}</td>
                    <td>{term.is_obsolete ? 'Obsolete' : 'Active'}</td>
                  </tr>
                ))}
                {terms.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="table-empty">
                      No HPO terms match the current search.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <aside className="surface-card-muted space-y-4">
            {selectedTerm ? (
              <>
                <div className="space-y-2">
                  <p className="page-kicker">{selectedTerm.hpo_id}</p>
                  <h3 className="section-title">{selectedTerm.label}</h3>
                  <p className="section-copy">
                    {selectedTerm.definition || 'No definition is stored for this term.'}
                  </p>
                </div>
                <div className="admin-data-summary">
                  <span className="badge-chip">{selectedTerm.is_obsolete ? 'Obsolete' : 'Active'}</span>
                  {selectedTerm.replaced_by ? (
                    <span className="badge-chip">Replaced by {selectedTerm.replaced_by}</span>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <h4 className="table-subtle">Synonyms</h4>
                  <p className="section-copy">
                    {selectedTerm.synonyms.length ? selectedTerm.synonyms.join(', ') : 'No synonyms stored.'}
                  </p>
                </div>
                <div className="space-y-2">
                  <h4 className="table-subtle">Parent terms</h4>
                  <ul className="space-y-1 text-sm">
                    {selectedTerm.parents.length ? (
                      selectedTerm.parents.map((parent) => (
                        <li key={`${parent.hpo_id}-${parent.relation}`}>
                          <span className="font-mono text-xs">{parent.hpo_id}</span> {parent.label}
                        </li>
                      ))
                    ) : (
                      <li className="table-subtle">No parent terms stored.</li>
                    )}
                  </ul>
                </div>
                <div className="space-y-2">
                  <h4 className="table-subtle">Child terms</h4>
                  <ul className="space-y-1 text-sm">
                    {selectedTerm.children.length ? (
                      selectedTerm.children.slice(0, 20).map((child) => (
                        <li key={`${child.hpo_id}-${child.relation}`}>
                          <span className="font-mono text-xs">{child.hpo_id}</span> {child.label}
                        </li>
                      ))
                    ) : (
                      <li className="table-subtle">No child terms stored.</li>
                    )}
                  </ul>
                  {selectedTerm.children.length > 20 ? (
                    <p className="table-subtle">
                      Showing 20 of {selectedTerm.children.length} child terms.
                    </p>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="table-empty">Select a term to view details.</p>
            )}
          </aside>
        </div>
      </section>

      <section className="surface-card-flat space-y-3">
        <h2 className="section-title">Future vocabulary support</h2>
        <p className="section-copy">
          The module is scoped around ontology terms and relationships so custom phenotype terms, institution-specific vocabularies, OMIM, MONDO, and Orphanet mappings can be added without changing the Admin navigation.
        </p>
      </section>
    </div>
  );
};

export default HpoTerminologyAdminPage;
