import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import api from '../../lib/api';
import { isAdmin } from '../../lib/auth';
import type { GeneLocation, GenePanel } from '../../lib/apiTypes';

interface PanelAppPanelSummary {
  panelapp_id: number;
  name: string;
  disease_group?: string | null;
  disease_sub_group?: string | null;
  status?: string | null;
  version?: string | null;
  version_created?: string | null;
  relevant_disorders: string[];
  gene_count: number;
  str_count: number;
  region_count: number;
  types: string[];
  url: string;
}

const apiErrorMessage = (err: any, fallback: string) => {
  const detail = err.response?.data?.detail;
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

const GenePanelsPage: React.FC = () => {
  const { data: panels, refetch } = useQuery<GenePanel[]>({
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
  const [panelAppResults, setPanelAppResults] = useState<PanelAppPanelSummary[]>([]);
  const [panelAppLoading, setPanelAppLoading] = useState(false);
  const [panelAppImporting, setPanelAppImporting] = useState<number | null>(null);
  const [confidencePreset, setConfidencePreset] = useState('3');
  const [panelAppAssembly, setPanelAppAssembly] = useState<'GRCh38' | 'GRCh37'>('GRCh38');
  const [includePanelAppRegions, setIncludePanelAppRegions] = useState(true);
  const [includePanelAppStrs, setIncludePanelAppStrs] = useState(true);
  const userIsAdmin = isAdmin();

  const formatDateTime = (value: string) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString();
  };

  const formatSize = (regions: GeneLocation[]) => {
    const total = regions.reduce((sum, r) => sum + (r.end - r.start), 0);
    if (total >= 1_000_000) {
      return `${(total / 1_000_000).toFixed(2)} Mb`;
    }
    return `${(total / 1000).toFixed(2)} kb`;
  };
  const formatSource = (panel: GenePanel) => {
    if (panel.source === 'panelapp') return 'PanelApp';
    if (panel.source === 'mendeliome') return 'Mendeliome';
    return 'Local';
  };
  const confidenceLevels = confidencePreset.split(',');

  const [mendeliomeBusy, setMendeliomeBusy] = useState(false);
  const mendeliome = panels?.find((p) => p.source === 'mendeliome');
  const regenerateMendeliome = async () => {
    setMendeliomeBusy(true);
    setStatus('');
    try {
      const res = await api.post('/panels/mendeliome/regenerate');
      setStatus(res.data?.message || 'Mendeliome updated');
      await refetch();
    } catch (err: any) {
      setStatus(apiErrorMessage(err, 'Error generating the Mendeliome'));
    } finally {
      setMendeliomeBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/panels/${id}`);
      setStatus('Panel deleted');
      refetch();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (typeof detail === 'string') {
        setStatus(detail);
      } else if (detail?.message) {
        const genes = Array.isArray(detail.genes)
          ? `: ${detail.genes.join(', ')}`
          : '';
        setStatus(`${detail.message}${genes}`);
      } else {
        setStatus('Error deleting panel');
      }
    }
  };

  const searchPanelApp = async (event: React.FormEvent) => {
    event.preventDefault();
    setPanelAppLoading(true);
    setStatus('');
    try {
      const res = await api.get('/panels/panelapp/search', {
        params: { query: panelAppQuery.trim(), page_size: 20 },
      });
      setPanelAppResults(res.data?.results || []);
      if (!res.data?.results?.length) {
        setStatus('No PanelApp panels found');
      }
    } catch {
      setStatus('Could not search PanelApp');
    } finally {
      setPanelAppLoading(false);
    }
  };

  const importPanelApp = async (panel: PanelAppPanelSummary) => {
    setPanelAppImporting(panel.panelapp_id);
    setStatus('');
    try {
      const res = await api.post('/panels/import/panelapp', {
        panelapp_id: panel.panelapp_id,
        version: panel.version || undefined,
        confidence_levels: confidenceLevels,
        include_regions: includePanelAppRegions,
        include_strs: includePanelAppStrs,
        assembly: panelAppAssembly,
      });
      setStatus(res.data?.message || 'PanelApp panel imported');
      await refetch();
    } catch (err: any) {
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
      const res = await api.post('/panels', {
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
      const msg = res.data?.message ?? 'Panel created';
      setStatus(msg);
      refetch();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (typeof detail === 'string') {
        setStatus(detail);
      } else if (detail?.message) {
        const genes = Array.isArray(detail.genes)
          ? `: ${detail.genes.join(', ')}`
          : '';
        setStatus(`${detail.message}${genes}`);
      } else {
        setStatus('Error creating panel');
      }
    }
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-1">
            <p className="page-kicker">Panels</p>
            <h2 className="catalog-card-title">Gene panels</h2>
            <p className="catalog-card-copy">
              Curated panel content and genomic footprint. Create or delete panels (admin).
            </p>
          </div>
        </div>
      </section>
      {userIsAdmin && (
        <>
          <div className="grid gap-4 xl:grid-cols-2 items-start">
          <form onSubmit={handleSubmit} className="surface-card field-grid">
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
            <button type="submit" className="form-button w-full justify-center">
              Create Panel
            </button>
          </form>
          <section className="surface-card field-grid">
            <h3 className="section-title">Import PanelApp panel</h3>
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
                <span>Confidence</span>
                <select
                  value={confidencePreset}
                  onChange={(event) => setConfidencePreset(event.target.value)}
                >
                  <option value="3">Green only</option>
                  <option value="2,3">Green + amber</option>
                  <option value="1,2,3">All confidence levels</option>
                </select>
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
                      <th>Content</th>
                      <th>Type</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {panelAppResults.map((panel) => (
                      <tr key={panel.panelapp_id}>
                        <td>
                          <div className="font-semibold">{panel.name}</div>
                          <div className="table-subtle">
                            PanelApp {panel.panelapp_id}
                            {panel.disease_group ? ` · ${panel.disease_group}` : ''}
                          </div>
                        </td>
                        <td>
                          {panel.version || 'latest'}
                          {panel.version_created && (
                            <div className="table-subtle">{formatDateTime(panel.version_created)}</div>
                          )}
                        </td>
                        <td>
                          {panel.gene_count} genes · {panel.region_count} regions · {panel.str_count} STRs
                        </td>
                        <td>{panel.types.join(', ') || 'PanelApp'}</td>
                        <td>
                          <button
                            type="button"
                            className="form-button"
                            onClick={() => importPanelApp(panel)}
                            disabled={panelAppImporting === panel.panelapp_id}
                          >
                            {panelAppImporting === panel.panelapp_id ? 'Importing...' : 'Import'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          </div>
          <section className="surface-card field-grid">
            <h3 className="section-title">Mendeliome (generated from Monarch)</h3>
            <p className="section-copy">
              All genes with a Mendelian disease association in the Monarch knowledge graph
              (aggregating OMIM, Orphanet and ClinGen). Updating creates a new version stamped
              with the current Monarch release; the previous version is archived, never deleted.
              {mendeliome ? (
                <>
                  {' '}
                  Current: <strong>v{mendeliome.version ?? 1}</strong>
                  {mendeliome.external_version ? ` · Monarch ${mendeliome.external_version}` : ''} ·{' '}
                  {mendeliome.gene_count ?? mendeliome.genes.length} genes.
                </>
              ) : null}
            </p>
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
          </section>
          {status && <p className="form-status">{status}</p>}
        </>
      )}
      {!userIsAdmin && (
        <section className="surface-card">
          <p className="section-copy">
            Panel creation and deletion are available only to admins. You can still browse the
            existing panel catalog below.
          </p>
        </section>
      )}
      <section className="surface-card space-y-4">
        <div className="analysis-toolbar items-end">
          <h3 className="section-title">Existing Panels</h3>
          <input
            type="search"
            value={panelSearch}
            onChange={(e) => setPanelSearch(e.target.value)}
            placeholder="Filter by name, source, or description"
            aria-label="Filter panels"
            className="panel-search-input"
          />
        </div>
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
            <th>Created By</th>
            <th>Description</th>
            {userIsAdmin && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {panels
            ?.filter((p) => {
              const q = panelSearch.trim().toLowerCase();
              if (!q) return true;
              return [p.name, formatSource(p), p.description || '', p.external_version || '']
                .some((value) => String(value).toLowerCase().includes(q));
            })
            .map((p) => {
            const geneCount = p.gene_count ?? p.genes.length;
            return (
              <tr key={p._id}>
                <td>
                  <Link to={`/panels/${p._id}`} className="table-link">
                    {p.name}
                  </Link>
                </td>
                <td>{geneCount}</td>
                <td>{formatSize(p.regions)}</td>
                <td>
                  {formatSource(p)}
                  {p.external_version ? (
                    <div className="table-subtle">{p.external_version}</div>
                  ) : null}
                </td>
                <td>v{p.version ?? 1}</td>
                <td>{formatDateTime(p.created_at)}</td>
                <td>{p.created_by_email || p.created_by}</td>
                <td className="project-catalog-note-cell">{p.description || '—'}</td>
                {userIsAdmin && (
                  <td>
                    <button onClick={() => handleDelete(p._id)} className="button-danger">
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
    </div>
  );
};

export default GenePanelsPage;
