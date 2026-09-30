import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import type { HpoAdminSummaryOut } from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatDate, formatDateTime } from '../../lib/format';

type HpoRelation = {
  hpo_id: string;
  label: string;
  relation: string;
};

type HpoAdminTerm = {
  hpo_id: string;
  label: string;
  definition?: string | null;
  is_obsolete: boolean;
  replaced_by?: string | null;
  release_version?: string | null;
  release_date?: string | null;
  synonyms: string[];
  parents: HpoRelation[];
  children: HpoRelation[];
  parent_count: number;
  child_count: number;
};

// As the backend serves it, including the ontology file it is configured to load.
type HpoAdminSummary = HpoAdminSummaryOut;

type HpoSyncResult = {
  preview_only: boolean;
  release_version?: string | null;
  release_date?: string | null;
  current: HpoAdminSummary;
  preview: Record<string, number>;
  imported?: {
    terms: number;
    synonyms: number;
    edges: number;
    closure_rows: number;
  } | null;
};

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

  const syncRequest = {
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
  const canSync = syncPath.trim().length > 0 && !syncMutation.isPending;
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
              Preview ontology changes before applying a local HPO ontology file. Release metadata is read from the file.
            </p>
          </div>
          <span className="badge-chip">
            Automatic updates {summary?.automatic_update_supported ? 'available' : 'not configured'}
          </span>
        </div>
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
            Release version override
            <input
              id="hpo-release-version"
              value={releaseVersion}
              onChange={(event) => setReleaseVersion(event.target.value)}
              placeholder="Read from file"
            />
          </label>
          <label className="field-label" htmlFor="hpo-release-date">
            Release date override
            <input
              id="hpo-release-date"
              type="date"
              value={releaseDate}
              onChange={(event) => setReleaseDate(event.target.value)}
            />
          </label>
        </div>
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
              if (
                window.confirm(
                  `Apply the HPO ontology in ${syncRequest.path} now? The terminology is replaced as previewed.`,
                )
              ) {
                syncMutation.mutate({ previewOnly: false, requestKey: syncRequestKey });
              }
            }}
          >
            {pendingAction === 'apply' ? 'Synchronizing...' : 'Apply sync'}
          </button>
        </div>
        {!previewedCurrentRequest && syncPath.trim() && !syncMutation.isPending ? (
          <p className="table-subtle">Preview these settings first; Apply sync applies what was previewed.</p>
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
                  <h4 className="table-subtle uppercase">Synonyms</h4>
                  <p className="section-copy">
                    {selectedTerm.synonyms.length ? selectedTerm.synonyms.join(', ') : 'No synonyms stored.'}
                  </p>
                </div>
                <div className="space-y-2">
                  <h4 className="table-subtle uppercase">Parent terms</h4>
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
                  <h4 className="table-subtle uppercase">Child terms</h4>
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
