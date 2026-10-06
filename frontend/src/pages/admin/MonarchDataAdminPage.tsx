import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatCount, formatTimestamp } from '../../lib/format';

interface MonarchStatus {
  release_version?: string | null;
  gene_disease_pairs: number;
  genes: number;
  diseases: number;
  causal_pairs: number;
  disease_phenotype_pairs: number;
  phenotype_diseases: number;
  phenotypes: number;
  last_updated_at?: string | null;
}

interface MonarchRefreshSummary {
  release_version?: string | null;
  files_loaded: number;
  gene_disease_pairs: number;
  genes: number;
  diseases: number;
  causal_pairs: number;
  disease_phenotype_pairs: number;
  phenotype_diseases: number;
  phenotypes: number;
  excluded_phenotype_pairs: number;
  completed_at: string;
  duration_seconds: number;
  // The generated Mendeliome panel is re-versioned after each refresh; it can fail
  // without failing the refresh, and is then reported instead of hidden (#514).
  mendeliome_regenerated?: boolean;
  mendeliome_error?: string | null;
}

interface MonarchSearchGene {
  gene_symbol: string;
  hgnc_id: string;
  predicate?: string | null;
  causal: boolean;
}

interface MonarchSearchPhenotype {
  hpo_id: string;
  phenotype_label?: string | null;
  matched: boolean;
}

type MonarchMatchType = 'disease' | 'phenotype' | 'both';

interface MonarchSearchDisease {
  mondo_id: string;
  disease_label?: string | null;
  match_type: MonarchMatchType;
  gene_count: number;
  genes: MonarchSearchGene[];
  phenotype_count: number;
  matched_phenotype_count: number;
  phenotypes: MonarchSearchPhenotype[];
}

interface MonarchGeneOverviewItem {
  gene_symbol: string;
  hgnc_id: string;
  causal: boolean;
  disease_count: number;
}

interface MonarchGeneOverview {
  total: number;
  genes: MonarchGeneOverviewItem[];
}

interface MonarchSearchResult {
  query: string;
  total: number;
  diseases: MonarchSearchDisease[];
  gene_overview: MonarchGeneOverview;
}

const SEARCH_LIMIT = 25;

const matchTypeLabels: Record<MonarchMatchType, string> = {
  disease: 'Disease name',
  phenotype: 'Phenotype',
  both: 'Disease & phenotype',
};

// One empty list, so the memo and effect below see a stable value before data arrive.
const NO_DISEASES: MonarchSearchDisease[] = [];

const MonarchDataAdminPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{
    tone: 'success' | 'warning' | 'error';
    message: string;
  } | null>(null);
  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [selectedMondoId, setSelectedMondoId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<MonarchStatus>({
    queryKey: ['admin', 'monarch-status'],
    queryFn: async () => {
      const response = await api.get('/admin/monarch/status');
      return response.data as MonarchStatus;
    },
    retry: false,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      setAppliedSearch(search.trim());
    }, 300);

    return () => clearTimeout(timer);
  }, [search]);

  const searchQuery = useQuery<MonarchSearchResult>({
    queryKey: ['admin', 'monarch', 'search', appliedSearch],
    queryFn: async ({ signal }: { signal?: AbortSignal }) => {
      const response = await api.get('/admin/monarch/search', {
        params: { q: appliedSearch, limit: SEARCH_LIMIT },
        signal,
      });
      return response.data as MonarchSearchResult;
    },
    enabled: appliedSearch.length > 0,
    retry: false,
  });

  const handleSearch = () => {
    const trimmed = search.trim();
    if (trimmed === appliedSearch) {
      if (trimmed.length > 0) {
        searchQuery.refetch();
      }
      return;
    }
    setAppliedSearch(trimmed);
  };

  const diseases = searchQuery.data?.diseases ?? NO_DISEASES;
  const geneOverview = searchQuery.data?.gene_overview ?? null;
  const selectedDisease = useMemo(
    () => diseases.find((disease) => disease.mondo_id === selectedMondoId) ?? diseases[0] ?? null,
    [diseases, selectedMondoId]
  );

  useEffect(() => {
    if (!selectedDisease) {
      setSelectedMondoId(null);
      return;
    }
    setSelectedMondoId((current) =>
      current && diseases.some((disease) => disease.mondo_id === current)
        ? current
        : selectedDisease.mondo_id
    );
  }, [diseases, selectedDisease]);

  const refreshMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/admin/monarch/refresh');
      return response.data as MonarchRefreshSummary;
    },
    onSuccess: async (summary) => {
      const updated = `Updated to Monarch release ${summary.release_version ?? 'latest'} — `
        + `${formatCount(summary.gene_disease_pairs)} gene–disease and `
        + `${formatCount(summary.disease_phenotype_pairs)} disease–phenotype pairs `
        + `in ${summary.duration_seconds.toFixed(1)}s.`;
      setStatus(
        summary.mendeliome_regenerated === false
          ? {
              tone: 'warning',
              message: `${updated} The Mendeliome panel was not regenerated`
                + `${summary.mendeliome_error ? ` (${summary.mendeliome_error})` : ''}`
                + ' — it still reflects the previous release. Update it from Gene panels → Mendeliome.',
            }
          : { tone: 'success', message: updated },
      );
      await queryClient.invalidateQueries({ queryKey: ['admin', 'monarch-status'] });
      await queryClient.invalidateQueries({ queryKey: ['admin', 'monarch', 'search'] });
    },
    onError: (mutationError) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(mutationError, 'Could not update the Monarch knowledgebase.'),
      });
    },
  });

  if (isLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading Monarch data"
        message="Reading the currently loaded Monarch release and table sizes."
      />
    );
  }

  if (error || !data) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load Monarch data"
        message={getErrorMessage(error, 'The Monarch knowledgebase status could not be loaded.')}
      />
    );
  }

  const isEmpty = data.gene_disease_pairs === 0 && data.disease_phenotype_pairs === 0;
  const total = searchQuery.data?.total ?? 0;
  const hasMore = total > diseases.length;

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-1">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Monarch data</h1>
            <p className="catalog-card-copy">
              Gene–disease and disease–phenotype associations from the Monarch Initiative
              knowledge graph. Updating downloads the latest monthly Monarch release and
              replaces both knowledgebase tables in a single transaction.
            </p>
          </div>
          <div className="surface-card-muted gene-profile-status">
            <span className="gene-profile-status-label">Loaded release</span>
            <strong>{data.release_version ?? '—'}</strong>
            <span className="dashboard-link-note">
              Last updated {formatTimestamp(data.last_updated_at)}
            </span>
          </div>
        </div>

        <div className="gene-sync-summary-grid">
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Gene–disease pairs</span>
            <strong>{formatCount(data.gene_disease_pairs)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Genes</span>
            <strong>{formatCount(data.genes)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Diseases</span>
            <strong>{formatCount(data.diseases)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Causal pairs</span>
            <strong>{formatCount(data.causal_pairs)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Disease–phenotype pairs</span>
            <strong>{formatCount(data.disease_phenotype_pairs)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Phenotype diseases</span>
            <strong>{formatCount(data.phenotype_diseases)}</strong>
          </div>
          <div className="gene-profile-stat">
            <span className="gene-profile-stat-label">Phenotypes</span>
            <strong>{formatCount(data.phenotypes)}</strong>
          </div>
        </div>

        <div className="gene-sync-action-grid">
          <article className="surface-card-muted gene-sync-action-panel">
            <p className="section-title">Update Monarch knowledgebase</p>
            <p className="dashboard-link-note">
              Downloads the latest Monarch release and atomically replaces the gene–disease and
              disease–phenotype tables. Runs inline and usually completes in a few seconds.
            </p>
            <div className="inline-actions">
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setStatus(null);
                  refreshMutation.mutate();
                }}
                disabled={refreshMutation.isPending}
              >
                {refreshMutation.isPending
                  ? 'Updating…'
                  : isEmpty
                    ? 'Import Monarch data'
                    : 'Update Monarch data'}
              </button>
            </div>
          </article>
        </div>

        {status ? (
          <div
            className={`status-note status-note--${status.tone}`}
            role={status.tone === 'success' ? 'status' : 'alert'}
          >
            {status.message}
          </div>
        ) : null}
      </section>

      <section className="surface-card space-y-4">
        <div className="catalog-card-header">
          <div className="space-y-2">
            <h2 className="section-title">Search diseases &amp; phenotypes</h2>
            <p className="section-copy">
              Search by disease name, MONDO id, phenotype name, or HP id to see the linked genes
              and expected phenotypes. Phenotype matches are HPO-closure aware — searching a term
              also surfaces diseases annotated with any more specific descendant term.
            </p>
          </div>
          {appliedSearch ? (
            <span className="badge-chip">
              {diseases.length}
              {hasMore ? ` of ${formatCount(total)}` : ''} shown
            </span>
          ) : null}
        </div>

        <label className="field-label" htmlFor="monarch-admin-search">
          Search term
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto]">
            <input
              id="monarch-admin-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  handleSearch();
                }
              }}
              placeholder="MONDO:0007739, epilepsy, HP:0001250, seizure..."
            />
            <button type="button" className="button-secondary" onClick={handleSearch}>
              Search
            </button>
          </div>
        </label>

        {searchQuery.error ? (
          <p className="status-note status-note--error">
            {getErrorMessage(searchQuery.error, 'Monarch search failed.')}
          </p>
        ) : null}

        {!appliedSearch ? (
          <p className="table-empty">Enter a disease or phenotype to search the knowledgebase.</p>
        ) : (
          <>
          {geneOverview && geneOverview.genes.length > 0 ? (
            <div className="surface-card-muted space-y-3">
              <div className="catalog-card-header">
                <div className="space-y-1">
                  <h3 className="section-title">Linked genes across all matches</h3>
                  <p className="section-copy">
                    Every gene tied to a matched disease, most-linked first. Causal links are
                    flagged; the count is how many matched diseases each gene comes from.
                  </p>
                </div>
                <span className="badge-chip">
                  {geneOverview.genes.length}
                  {geneOverview.total > geneOverview.genes.length
                    ? ` of ${formatCount(geneOverview.total)}`
                    : ''}{' '}
                  genes
                </span>
              </div>
              <ul className="flex flex-wrap gap-2">
                {geneOverview.genes.map((gene) => (
                  <li
                    key={gene.hgnc_id || gene.gene_symbol}
                    className={`badge-chip ${gene.causal ? 'badge-chip--success' : ''}`}
                    title={`${gene.hgnc_id}${gene.causal ? ' · causal' : ''} · ${gene.disease_count} ${
                      gene.disease_count === 1 ? 'disease' : 'diseases'
                    }`}
                  >
                    {gene.gene_symbol}
                    <span className="table-subtle">· {formatCount(gene.disease_count)}</span>
                  </li>
                ))}
              </ul>
              {geneOverview.total > geneOverview.genes.length ? (
                <p className="table-subtle">
                  Showing {geneOverview.genes.length} of {formatCount(geneOverview.total)} linked genes.
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table table-sticky">
                <thead>
                  <tr>
                    <th>MONDO ID</th>
                    <th>Disease</th>
                    <th>Match</th>
                    <th>Genes</th>
                    <th>Phenotypes</th>
                  </tr>
                </thead>
                <tbody>
                  {diseases.map((disease) => (
                    <tr
                      key={disease.mondo_id}
                      onClick={() => setSelectedMondoId(disease.mondo_id)}
                      className="cursor-pointer"
                    >
                      <td className="font-mono text-xs">{disease.mondo_id}</td>
                      <td>{disease.disease_label || '—'}</td>
                      <td>{matchTypeLabels[disease.match_type]}</td>
                      <td>{formatCount(disease.gene_count)}</td>
                      <td>{formatCount(disease.phenotype_count)}</td>
                    </tr>
                  ))}
                  {!searchQuery.isLoading && diseases.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="table-empty">
                        No diseases or phenotypes match this search.
                      </td>
                    </tr>
                  ) : null}
                  {searchQuery.isLoading ? (
                    <tr>
                      <td colSpan={5} className="table-empty">
                        Searching…
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <aside className="surface-card-muted space-y-4">
              {selectedDisease ? (
                <>
                  <div className="space-y-2">
                    <p className="page-kicker">{selectedDisease.mondo_id}</p>
                    <h3 className="section-title">{selectedDisease.disease_label || 'Unnamed disease'}</h3>
                    <div className="admin-data-summary">
                      <span className="badge-chip">{matchTypeLabels[selectedDisease.match_type]}</span>
                      <span className="badge-chip">{formatCount(selectedDisease.gene_count)} genes</span>
                      <span className="badge-chip">
                        {formatCount(selectedDisease.phenotype_count)} phenotypes
                      </span>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <h4 className="table-subtle">Linked genes</h4>
                    {selectedDisease.genes.length ? (
                      <ul className="space-y-1 text-sm">
                        {selectedDisease.genes.map((gene) => (
                          <li key={gene.hgnc_id || gene.gene_symbol}>
                            <span className="font-mono text-xs">{gene.gene_symbol}</span>{' '}
                            {gene.causal ? <span className="badge-chip">causal</span> : null}{' '}
                            <span className="table-subtle">
                              {gene.predicate ? gene.predicate.replace(/_/g, ' ') : ''}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="table-subtle">No genes are linked to this disease in Monarch.</p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <h4 className="table-subtle">
                      Expected phenotypes
                      {selectedDisease.matched_phenotype_count > 0
                        ? ` — ${formatCount(selectedDisease.matched_phenotype_count)} matched`
                        : ''}
                    </h4>
                    {selectedDisease.phenotypes.length ? (
                      <ul className="space-y-1 text-sm">
                        {selectedDisease.phenotypes.map((phenotype) => (
                          <li key={phenotype.hpo_id}>
                            <span className="font-mono text-xs">{phenotype.hpo_id}</span>{' '}
                            {phenotype.phenotype_label || ''}{' '}
                            {phenotype.matched ? <span className="badge-chip">match</span> : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="table-subtle">No expected phenotypes are stored for this disease.</p>
                    )}
                    {selectedDisease.phenotype_count > selectedDisease.phenotypes.length ? (
                      <p className="table-subtle">
                        Showing {selectedDisease.phenotypes.length} of{' '}
                        {formatCount(selectedDisease.phenotype_count)} phenotypes.
                      </p>
                    ) : null}
                  </div>
                </>
              ) : (
                <p className="table-empty">Select a disease to view its genes and phenotypes.</p>
              )}
            </aside>
          </div>
          </>
        )}
      </section>
    </div>
  );
};

export default MonarchDataAdminPage;
