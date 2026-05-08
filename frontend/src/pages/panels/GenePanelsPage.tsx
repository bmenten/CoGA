import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import api from '../../lib/api';
import { isAdmin } from '../../lib/auth';

interface GeneLocation {
  chr: string;
  start: number;
  end: number;
}

interface GenePanel {
  _id: string;
  name: string;
  genes: string[];
  gene_count?: number;
  regions: GeneLocation[];
  created_by: string;
  created_by_email?: string | null;
  created_at: string;
  description?: string | null;
}

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
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Panels</p>
            <h2 className="catalog-card-title">Gene panels</h2>
            <p className="catalog-card-copy">
              Review curated panel content and genomic footprint. Creating or deleting panels is an
              administrative task.
            </p>
          </div>
        </div>
      </section>
      {userIsAdmin && (
        <>
          <form onSubmit={handleSubmit} className="surface-card field-grid max-w-2xl">
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
          {status && <p className="form-status max-w-2xl">{status}</p>}
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
        <h3 className="section-title">Existing Panels</h3>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
          <tr>
            <th>Name</th>
            <th># Genes</th>
            <th>Size</th>
            <th>Created</th>
            <th>Created By</th>
            <th>Description</th>
            {userIsAdmin && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {panels?.map((p) => {
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
