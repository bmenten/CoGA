import React, { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';

interface GeneRegion {
  gene: string;
  chr: string;
  start: number;
  end: number;
}

interface GenePanel {
  _id: string;
  name: string;
  genes: string[];
  gene_count?: number;
  regions: GeneRegion[];
  created_by: string;
  created_by_email?: string | null;
  created_at: string;
  description?: string | null;
}

const GenePanelDetailPage: React.FC = () => {
  const { panelId } = useParams();
  const { data: panel } = useQuery<GenePanel>({
    queryKey: ['panel', panelId],
    queryFn: async () => {
      const res = await api.get(`/panels/${panelId}`);
      return res.data as GenePanel;
    },
  });

  const [sortKey, setSortKey] = useState<keyof GeneRegion>('gene');
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

  const handleSort = (key: keyof GeneRegion) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const handleFilterChange = (
    e: React.ChangeEvent<HTMLInputElement>,
    key: keyof GeneRegion,
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

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Panel Detail</p>
            <h2 className="catalog-card-title">{panel.name}</h2>
            <p className="catalog-card-copy">
              Filter and sort the genomic regions in this panel.
            </p>
            <p className="catalog-card-copy">
              Created {formatDateTime(panel.created_at)} by {panel.created_by_email || panel.created_by} •{' '}
              {panel.gene_count ?? panel.genes.length} genes
            </p>
            {panel.description && <p className="catalog-card-copy">{panel.description}</p>}
          </div>
        </div>
      </section>
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
            <tr key={r.gene}>
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
