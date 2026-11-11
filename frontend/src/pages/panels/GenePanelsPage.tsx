import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import api from '../../lib/api';
import { hasSuperuserRights } from '../../lib/auth';
import type { GeneLocation, GenePanel } from '../../lib/apiTypes';
import type {
  GenePanelCreateResponse,
  MendeliomeRegenerateResponse,
  PanelAppImportResponse,
  PanelAppPanelSearchResponse,
  PanelAppPanelSummaryOut,
} from '../../lib/apiSchema.generated';
import { apiPath } from '../../lib/apiPath';
import type { ApiErrorLike } from '../../lib/errorMessage';
import ConfirmDeleteDialog from '../../components/ConfirmDeleteDialog';
import PageHeader from '../../components/PageHeader';
import QueryFailure from '../../components/QueryFailure';
import { formatDateTime } from '../../lib/format';
import { compareAssemblyNames } from '../../lib/reference';
import { routePath } from '../../lib/routePath';
import PanelAppSearchRow from './PanelAppSearchRow';
import { panelAppSelection, panelAppSelectionLabel } from './panelAppConfidence';

// A panel request's error detail: a message, a validation list, or a message naming genes.
type PanelErrorDetail =
  | string
  | Array<string | { msg?: unknown } | null>
  | { message?: string; genes?: unknown }
  | undefined;

const panelErrorDetail = (err: unknown): PanelErrorDetail =>
  (err as ApiErrorLike).response?.data?.detail as PanelErrorDetail;

const apiErrorMessage = (err: unknown, fallback: string) => {
  const detail = panelErrorDetail(err);
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => {
        if (typeof entry === 'string') return entry;
        if (entry && typeof entry.msg === 'string') return entry.msg;
        return null;
      })
      .filter(Boolean);
    return messages.length ? messages.join('; ') : fallback;
  }
  return fallback;
};

// A refused panel write: its detail as said, or its message naming the genes concerned
// (for example the genes a panel still uses); the fallback otherwise.
const panelWriteErrorMessage = (err: unknown, fallback: string): string => {
  const detail = panelErrorDetail(err);
  if (typeof detail === 'string') return detail;
  if (detail && !Array.isArray(detail) && detail.message) {
    const genes = Array.isArray(detail.genes) ? `: ${detail.genes.join(', ')}` : '';
    return `${detail.message}${genes}`;
  }
  return fallback;
};

const GenePanelsPage: React.FC = () => {
  const { data: panels, refetch, isError: panelsFailed, error: panelsError } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

  const [name, setName] = useState('');
  const [genes, setGenes] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState('');
  const [panelSearch, setPanelSearch] = useState('');
  const [panelAppQuery, setPanelAppQuery] = useState('');
  const [panelAppResults, setPanelAppResults] = useState<PanelAppPanelSummaryOut[]>([]);
  const [panelAppLoading, setPanelAppLoading] = useState(false);
  // The PanelApp panel and choice of confidence levels being imported, if any.
  const [panelAppImporting, setPanelAppImporting] = useState<{ panelappId: number; selection: string } | null>(
    null,
  );
  const [panelAppAssembly, setPanelAppAssembly] = useState<'GRCh38' | 'GRCh37'>('GRCh38');
  const [includePanelAppRegions, setIncludePanelAppRegions] = useState(true);
  const [includePanelAppStrs, setIncludePanelAppStrs] = useState(true);
  // Gene panels are a superuser's or an admin's to change.
  const userIsAdmin = hasSuperuserRights();

  const formatSpan = (total: number) =>
    total >= 1_000_000 ? `${(total / 1_000_000).toFixed(2)} Mb` : `${(total / 1000).toFixed(2)} kb`;
  // Regions are stored per assembly: one total over all of them would count each
  // gene once per loaded assembly, so the size is given per assembly, in the backend's order:
  // GRCh38, T2T-CHM13, GRCh37, then the rest.
  const formatSize = (regions: GeneLocation[]) => {
    const byAssembly = new Map<string, number>();
    regions.forEach((r) => {
      const key = r.assembly || '';
      byAssembly.set(key, (byAssembly.get(key) ?? 0) + (r.end - r.start));
    });
    if (byAssembly.size <= 1) return formatSpan([...byAssembly.values()][0] ?? 0);
    return [...byAssembly.entries()]
      .sort(([a], [b]) => compareAssemblyNames(a, b))
      .map(([assembly, total]) => `${formatSpan(total)} (${assembly || 'unknown'})`)
      .join(' · ');
  };
  const formatSource = (panel: GenePanel) => {
    if (panel.source === 'panelapp') return 'PanelApp';
    if (panel.source === 'mendeliome') return 'Mendeliome';
    return 'Local';
  };

  const [mendeliomeBusy, setMendeliomeBusy] = useState(false);
  const mendeliome = panels?.find((p) => p.source === 'mendeliome');
  const regenerateMendeliome = async () => {
    setMendeliomeBusy(true);
    setStatus('');
    try {
      const res = await api.post<MendeliomeRegenerateResponse>('/panels/mendeliome/regenerate');
      setStatus(res.data.message || 'Mendeliome updated');
      await refetch();
    } catch (err) {
      setStatus(apiErrorMessage(err, 'Error generating the Mendeliome'));
    } finally {
      setMendeliomeBusy(false);
    }
  };

  // A delete asks first, naming the panel and its version: it takes the panel's genes,
  // regions and version history with it. Only the dialog's confirmation sends it.
  const [panelToDelete, setPanelToDelete] = useState<GenePanel | null>(null);
  const [deletingPanel, setDeletingPanel] = useState(false);
  const handleDelete = async (id: string) => {
    setDeletingPanel(true);
    try {
      await api.delete(apiPath`/panels/${id}`);
      setStatus('Panel deleted');
      refetch();
    } catch (err) {
      setStatus(panelWriteErrorMessage(err, 'Error deleting panel'));
    } finally {
      setDeletingPanel(false);
      setPanelToDelete(null);
    }
  };
  const panelLabel = (panel: GenePanel) => `${panel.name} v${panel.version ?? 1}`;

  const searchPanelApp = async (event: React.FormEvent) => {
    event.preventDefault();
    setPanelAppLoading(true);
    setStatus('');
    try {
      const res = await api.get<PanelAppPanelSearchResponse>('/panels/panelapp/search', {
        params: { query: panelAppQuery.trim(), page_size: 20 },
      });
      setPanelAppResults(res.data.results);
      if (!res.data.results.length) {
        setStatus('No PanelApp panels found');
      }
    } catch {
      setStatus('Could not search PanelApp');
    } finally {
      setPanelAppLoading(false);
    }
  };

  // Each choice of confidence levels of a PanelApp panel is a panel of its own; importing one
  // the catalogue holds updates that panel only.
  const importPanelApp = async (panel: PanelAppPanelSummaryOut, confidenceLevels: string[]) => {
    setPanelAppImporting({ panelappId: panel.panelapp_id, selection: panelAppSelection(confidenceLevels) });
    setStatus('');
    try {
      const res = await api.post<PanelAppImportResponse>('/panels/import/panelapp', {
        panelapp_id: panel.panelapp_id,
        version: panel.version || undefined,
        confidence_levels: confidenceLevels,
        include_regions: includePanelAppRegions,
        include_strs: includePanelAppStrs,
        assembly: panelAppAssembly,
      });
      setStatus(res.data.message || 'PanelApp panel imported');
      await refetch();
    } catch (err) {
      setStatus(apiErrorMessage(err, 'Error importing PanelApp panel'));
    } finally {
      setPanelAppImporting(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (!name.trim()) {
        setStatus('Panel name is required');
        return;
      }
      const res = await api.post<GenePanelCreateResponse>('/panels', {
        name,
        genes: genes
          .split(/[\s,]+/)
          .map((g) => g.trim())
          .filter(Boolean),
        description: description.trim() || undefined,
      });
      setName('');
      setGenes('');
      setDescription('');
      const msg = res.data.message ?? 'Panel created';
      setStatus(msg);
      refetch();
    } catch (err) {
      setStatus(panelWriteErrorMessage(err, 'Error creating panel'));
    }
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <PageHeader
        kicker="Panels"
        title="Gene panels"
        description={
          userIsAdmin
            ? 'Curated panel content and genomic footprint: import panels from PanelApp, keep the Mendeliome in step with Monarch, add your own panels, and manage the catalogue.'
            : 'Curated panel content and genomic footprint. Panel creation and deletion are available only to admins; you can browse the existing panel catalog below.'
        }
      />
      {userIsAdmin && (
        <div className="surface-card card-sections">
          <section className="card-section" aria-labelledby="panels-external-heading">
            <div className="space-y-1">
              <h2 id="panels-external-heading" className="section-title">
                Import and sync external panels
              </h2>
              <p className="section-copy">
                Panels CoGA takes from an external source: a PanelApp panel at its current version,
                and the Mendeliome built from the loaded Monarch release. A PanelApp panel can be
                imported green only, green + amber, or green + amber + red: each is a panel of its
                own, to filter on apart.
              </p>
            </div>

            <div className="panel-admin-block">
              <h3 className="card-section-subtitle">Import PanelApp panel</h3>
              <form onSubmit={searchPanelApp} className="analysis-toolbar items-end">
                <label className="field-label">
                  <span>PanelApp search</span>
                  <input
                    value={panelAppQuery}
                    onChange={(e) => setPanelAppQuery(e.target.value)}
                    placeholder="Ataxia, R169, cardiomyopathy"
                  />
                </label>
                <label className="field-label">
                  <span>Assembly</span>
                  <select
                    value={panelAppAssembly}
                    onChange={(event) => setPanelAppAssembly(event.target.value as 'GRCh38' | 'GRCh37')}
                  >
                    <option value="GRCh38">GRCh38</option>
                    <option value="GRCh37">GRCh37</option>
                  </select>
                </label>
                <label className="analysis-checkbox">
                  <input
                    type="checkbox"
                    checked={includePanelAppRegions}
                    onChange={(event) => setIncludePanelAppRegions(event.target.checked)}
                  />
                  Regions
                </label>
                <label className="analysis-checkbox">
                  <input
                    type="checkbox"
                    checked={includePanelAppStrs}
                    onChange={(event) => setIncludePanelAppStrs(event.target.checked)}
                  />
                  STRs
                </label>
                <button type="submit" className="form-button" disabled={panelAppLoading}>
                  {panelAppLoading ? 'Searching...' : 'Search PanelApp'}
                </button>
              </form>
              {panelAppResults.length > 0 && (
                <div className="data-table-shell overflow-x-auto">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Panel</th>
                        <th>Version</th>
                        <th>Content by confidence</th>
                        <th>Type</th>
                        <th>Import as panel</th>
                      </tr>
                    </thead>
                    <tbody>
                      {panelAppResults.map((panel) => (
                        <PanelAppSearchRow
                          key={panel.panelapp_id}
                          panel={panel}
                          imported={(panels ?? []).filter(
                            (entry) =>
                              entry.source === 'panelapp' && entry.external_id === String(panel.panelapp_id),
                          )}
                          importing={
                            panelAppImporting?.panelappId === panel.panelapp_id ? panelAppImporting.selection : null
                          }
                          busy={panelAppImporting !== null}
                          includeRegions={includePanelAppRegions}
                          includeStrs={includePanelAppStrs}
                          onImport={(found, levels) => void importPanelApp(found, levels)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="panel-admin-block">
              <h3 className="card-section-subtitle">Mendeliome update from Monarch</h3>
              <p className="section-copy">
                All genes with a Mendelian disease association in the Monarch knowledge graph
                (aggregating OMIM, Orphanet and ClinGen). Updating stamps the panel with the current
                Monarch release and, when the gene set changes, makes a new version; earlier
                versions stay in the panel&apos;s version history. The Monarch release itself is
                loaded under <Link to="/admin/reference/monarch">Monarch data</Link>.
                {mendeliome ? (
                  <>
                    {' '}
                    Current: <strong>v{mendeliome.version ?? 1}</strong>
                    {mendeliome.external_version ? ` · Monarch ${mendeliome.external_version}` : ''} ·{' '}
                    {mendeliome.gene_count ?? mendeliome.genes.length} genes.
                  </>
                ) : null}
              </p>
              <div className="inline-actions">
                <button
                  type="button"
                  className="form-button"
                  onClick={regenerateMendeliome}
                  disabled={mendeliomeBusy}
                >
                  {mendeliomeBusy
                    ? 'Working…'
                    : mendeliome
                      ? `Update Mendeliome (v${mendeliome.version ?? 1})`
                      : 'Generate Mendeliome'}
                </button>
              </div>
            </div>
          </section>

          <section className="card-section" aria-labelledby="panels-custom-heading">
            <h2 id="panels-custom-heading" className="section-title">
              Add new custom gene panel
            </h2>
            <form onSubmit={handleSubmit} className="field-grid panel-create-form">
              <label className="field-label">
                <span>Panel name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </label>
              <label className="field-label">
                <span>Genes (comma or space separated)</span>
                <textarea
                  value={genes}
                  onChange={(e) => setGenes(e.target.value)}
                />
              </label>
              <label className="field-label">
                <span>Description (optional)</span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
              <button type="submit" className="form-button">
                Create panel
              </button>
            </form>
          </section>

          {status && <p className="form-status">{status}</p>}
        </div>
      )}
      <section className="surface-card space-y-4" aria-labelledby="panels-existing-heading">
        <div className="analysis-toolbar items-end">
          <h2 id="panels-existing-heading" className="section-title">
            Existing panels
          </h2>
          <input
            type="search"
            value={panelSearch}
            onChange={(e) => setPanelSearch(e.target.value)}
            placeholder="Filter by name, source, or description"
            aria-label="Filter panels"
            className="panel-search-input"
          />
        </div>
        {/* A failed catalogue is not an empty one: it is said, never shown as a table without rows. */}
        {panelsFailed ? (
          <QueryFailure what="the gene panels" error={panelsError} onRetry={() => void refetch()} />
        ) : null}
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
          <tr>
            <th>Name</th>
            <th># Genes</th>
            <th>Size</th>
            <th>Source</th>
            <th>Version</th>
            <th>Created</th>
            <th>Created by</th>
            <th>Description</th>
            {userIsAdmin && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {panels
            ?.filter((p) => {
              const q = panelSearch.trim().toLowerCase();
              if (!q) return true;
              return [
                p.name,
                formatSource(p),
                p.description || '',
                p.external_version || '',
                panelAppSelectionLabel(p.external_selection),
              ]
                .some((value) => String(value).toLowerCase().includes(q));
            })
            .map((p) => {
            const geneCount = p.gene_count ?? p.genes.length;
            return (
              <tr key={p._id}>
                <td>
                  <Link to={routePath`/panels/${p._id}`} className="table-link">
                    {p.name}
                  </Link>
                </td>
                <td>{geneCount}</td>
                <td>{formatSize(p.regions)}</td>
                <td>
                  {formatSource(p)}
                  {p.external_version || p.external_selection ? (
                    <div className="table-subtle">
                      {[p.external_version, panelAppSelectionLabel(p.external_selection)].filter(Boolean).join(' · ')}
                    </div>
                  ) : null}
                </td>
                <td>v{p.version ?? 1}</td>
                <td>{formatDateTime(p.created_at)}</td>
                <td>{p.created_by_email || p.created_by}</td>
                <td className="project-catalog-note-cell">{p.description || '—'}</td>
                {userIsAdmin && (
                  <td>
                    <button type="button" onClick={() => setPanelToDelete(p)} className="button-danger">
                      Delete
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
          </table>
        </div>
      </section>
      {panelToDelete ? (
        <ConfirmDeleteDialog
          title={`Delete gene panel ${panelLabel(panelToDelete)}`}
          confirmLabel="Delete panel"
          busy={deletingPanel}
          onCancel={() => setPanelToDelete(null)}
          onConfirm={() => void handleDelete(panelToDelete._id)}
        >
          <p>
            This deletes the gene panel <strong>{panelLabel(panelToDelete)}</strong>
            {panelToDelete.external_version
              ? ` (${formatSource(panelToDelete)} ${panelToDelete.external_version})`
              : ''}
            , with its genes, its regions and every archived version.
          </p>
        </ConfirmDeleteDialog>
      ) : null}
    </div>
  );
};

export default GenePanelsPage;
