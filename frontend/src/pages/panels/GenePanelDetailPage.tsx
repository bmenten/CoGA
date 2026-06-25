import React, { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isAdmin } from '../../lib/auth';
import PageState from '../../components/PageState';
import type {
  GeneLocation,
  GenePanel,
  GenePanelVersionList,
} from '../../lib/apiTypes';

const GenePanelDetailPage: React.FC = () => {
  const { panelId } = useParams();
  const queryClient = useQueryClient();
  const userIsAdmin = isAdmin();
  const { data: panel } = useQuery<GenePanel>({
    queryKey: ['panel', panelId],
    queryFn: async () => {
      const res = await api.get(`/panels/${panelId}`);
      return res.data as GenePanel;
    },
  });

  const { data: versionList } = useQuery<GenePanelVersionList>({
    queryKey: ['panel', panelId, 'versions'],
    enabled: Boolean(panelId),
    queryFn: async () =>
      (await api.get(`/panels/${panelId}/versions`)).data as GenePanelVersionList,
  });

  const [editing, setEditing] = useState(false);
  const [editGenes, setEditGenes] = useState('');
  const [editStatus, setEditStatus] = useState('');
  const [saving, setSaving] = useState(false);

  const [sortKey, setSortKey] = useState<keyof GeneLocation>('gene');
  const [sortAsc, setSortAsc] = useState(true);
  const [filters, setFilters] = useState({
    gene: '',
    chr: '',
    start: '',
    end: '',
  });
  const formatDateTime = (value: string) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString();
  };
  const startEdit = () => {
    setEditGenes((panel?.genes ?? []).join(', '));
    setEditStatus('');
    setEditing(true);
  };

  const saveEdit = async () => {
    setSaving(true);
    setEditStatus('');
    try {
      const res = await api.put(`/panels/${panelId}`, {
        genes: editGenes
          .split(/[\s,]+/)
          .map((g) => g.trim())
          .filter(Boolean),
      });
      setEditStatus(res.data?.message || 'Panel updated');
      setEditing(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['panel', panelId] }),
        queryClient.invalidateQueries({ queryKey: ['panel', panelId, 'versions'] }),
        queryClient.invalidateQueries({ queryKey: ['panels'] }),
      ]);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setEditStatus(typeof detail === 'string' ? detail : 'Error updating panel');
    } finally {
      setSaving(false);
    }
  };

  const handleSort = (key: keyof GeneLocation) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const handleFilterChange = (
    e: React.ChangeEvent<HTMLInputElement>,
    key: keyof GeneLocation,
  ) => {
    const { value } = e.target;
    setFilters((f) => ({ ...f, [key]: value }));
  };

  const regions = useMemo(() => {
    if (!panel) return [];
    return [...panel.regions]
      .filter((r) =>
        (!filters.gene ||
          r.gene.toLowerCase().includes(filters.gene.toLowerCase())) &&
        (!filters.chr ||
          r.chr.toLowerCase().includes(filters.chr.toLowerCase())) &&
        (!filters.start || String(r.start).includes(filters.start)) &&
        (!filters.end || String(r.end).includes(filters.end)),
      )
      .sort((a, b) => {
        const aVal = a[sortKey];
        const bVal = b[sortKey];
        if (aVal < bVal) return sortAsc ? -1 : 1;
        if (aVal > bVal) return sortAsc ? 1 : -1;
        return 0;
      });
  }, [panel, filters, sortKey, sortAsc]);

  if (!panel) {
    return (
      <PageState
        kicker="Panel Detail"
        title="Loading gene panel"
        message="Preparing panel regions and sorting controls."
      />
    );
  }
  const sourceLabel =
    panel.source === 'panelapp'
      ? 'PanelApp'
      : panel.source === 'mendeliome'
        ? 'Mendeliome'
        : 'Local';
  const isLocal = !panel.source || panel.source === 'local';

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Panel Detail</p>
            <h2 className="catalog-card-title">
              {panel.name} <span className="panel-version-chip">v{panel.version ?? 1}</span>
            </h2>
            <p className="catalog-card-copy">
              Filter and sort the genomic regions in this panel.
            </p>
            <p className="catalog-card-copy">
              Created {formatDateTime(panel.created_at)} by {panel.created_by_email || panel.created_by} •{' '}
              {panel.gene_count ?? panel.genes.length} genes
            </p>
            <p className="catalog-card-copy">
              {sourceLabel}
              {panel.external_id ? ` ${panel.external_id}` : ''}
              {panel.external_version ? ` · version ${panel.external_version}` : ''}
              {panel.source_updated_at ? ` · updated ${formatDateTime(panel.source_updated_at)}` : ''}
            </p>
            {panel.description && <p className="catalog-card-copy">{panel.description}</p>}
            {panel.external_url && (
              <a className="table-link" href={panel.external_url} target="_blank" rel="noreferrer">
                Open source panel
              </a>
            )}
            {userIsAdmin && isLocal && !editing && (
              <button type="button" className="form-button" onClick={startEdit}>
                Edit genes (new version)
              </button>
            )}
            {userIsAdmin && !isLocal && (
              <p className="catalog-card-copy table-subtle">
                This panel is generated/imported — update it from the panel catalog
                (regenerate / re-import), not by editing genes here.
              </p>
            )}
            {editStatus && <p className="form-status">{editStatus}</p>}
          </div>
        </div>
      </section>

      {userIsAdmin && isLocal && editing && (
        <section className="surface-card field-grid">
          <h3 className="section-title">Edit genes</h3>
          <p className="section-copy">
            Add or remove genes (comma or space separated). Saving creates a new version; the
            current v{panel.version ?? 1} is archived.
          </p>
          <textarea
            value={editGenes}
            onChange={(e) => setEditGenes(e.target.value)}
            rows={6}
            aria-label="Panel genes"
          />
          <div className="analysis-toolbar">
            <button type="button" className="form-button" onClick={saveEdit} disabled={saving}>
              {saving ? 'Saving…' : 'Save new version'}
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      {versionList?.versions?.length ? (
        <section className="surface-card space-y-3">
          <h3 className="section-title">Version history</h3>
          <p className="section-copy">
            Every version is archived with a timestamp and never deleted.
          </p>
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th># Genes</th>
                  <th>Source release</th>
                  <th>Created</th>
                  <th>By</th>
                </tr>
              </thead>
              <tbody>
                {versionList.versions.map((v) => (
                  <tr key={v.version}>
                    <td>
                      v{v.version}
                      {v.version === versionList.current_version ? (
                        <span className="table-subtle"> (current)</span>
                      ) : null}
                    </td>
                    <td>{v.gene_count}</td>
                    <td>{v.external_version || '—'}</td>
                    <td>{formatDateTime(v.created_at)}</td>
                    <td>{v.created_by_email || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      <div className="surface-card">
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table table-sticky">
            <thead>
          <tr>
            <th
              className="table-sortable"
              onClick={() => handleSort('gene')}
            >
              Gene {sortKey === 'gene' && (sortAsc ? '▲' : '▼')}
            </th>
            <th
              className="table-sortable"
              onClick={() => handleSort('chr')}
            >
              Chromosome {sortKey === 'chr' && (sortAsc ? '▲' : '▼')}
            </th>
            <th
              className="table-sortable"
              onClick={() => handleSort('start')}
            >
              Start {sortKey === 'start' && (sortAsc ? '▲' : '▼')}
            </th>
            <th
              className="table-sortable"
              onClick={() => handleSort('end')}
            >
              End {sortKey === 'end' && (sortAsc ? '▲' : '▼')}
            </th>
          </tr>
          <tr className="table-filter-row">
            <th>
              <input
                placeholder="Filter gene"
                value={filters.gene}
                onChange={(e) => handleFilterChange(e, 'gene')}
              />
            </th>
            <th>
              <input
                placeholder="Filter chr"
                value={filters.chr}
                onChange={(e) => handleFilterChange(e, 'chr')}
              />
            </th>
            <th>
              <input
                placeholder="Filter start"
                value={filters.start}
                onChange={(e) => handleFilterChange(e, 'start')}
              />
            </th>
            <th>
              <input
                placeholder="Filter end"
                value={filters.end}
                onChange={(e) => handleFilterChange(e, 'end')}
              />
            </th>
          </tr>
        </thead>
        <tbody>
          {regions.map((r) => (
            <tr key={`${r.gene}:${r.chr}:${r.start}-${r.end}`}>
              <td>{r.gene}</td>
              <td>{r.chr}</td>
              <td>{r.start}</td>
              <td>{r.end}</td>
            </tr>
          ))}
        </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default GenePanelDetailPage;
