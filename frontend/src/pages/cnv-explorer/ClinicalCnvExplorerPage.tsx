import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import api from '../../lib/api';
import type { ApiAssemblyRecord, ApiClinicalCnv } from '../../lib/apiTypes';

const formatBp = (bp: number) => bp.toLocaleString();

const formatSize = (size: number) => {
  if (size >= 1_000_000) return `${(size / 1_000_000).toFixed(2)} Mb`;
  if (size >= 1_000) return `${(size / 1_000).toFixed(1)} kb`;
  return `${size} bp`;
};

const ClinicalCnvExplorerPage = () => {
  const [assembly, setAssembly] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');

  const { data: assemblies } = useQuery<ApiAssemblyRecord[]>({
    queryKey: ['assemblies'],
    queryFn: async () => (await api.get('/assemblies')).data as ApiAssemblyRecord[],
  });

  useEffect(() => {
    if (!assembly && assemblies && assemblies.length > 0) {
      setAssembly(assemblies[0].assembly_name);
    }
  }, [assembly, assemblies]);

  const { data: cnvs, isLoading } = useQuery<ApiClinicalCnv[]>({
    queryKey: ['cnv-catalog', assembly, appliedSearch],
    queryFn: async () => {
      const res = await api.get(`/cnvs/${assembly}/catalog`, {
        params: { search: appliedSearch || undefined, limit: 1000 },
      });
      return res.data as ApiClinicalCnv[];
    },
    enabled: Boolean(assembly),
  });

  const rows = cnvs ?? [];

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card space-y-4">
        <div className="space-y-1">
          <p className="page-kicker">Clinical CNV explorer</p>
          <h2 className="section-title">
            Clinical CNVs
            {cnvs ? <span className="variant-results-count">{rows.length.toLocaleString()}</span> : null}
          </h2>
          <p className="table-subtle">
            Curated recurrent CNV syndromes (ClinGen / DECIPHER / literature) available in this CoGA
            instance. Select one to view its details.
          </p>
        </div>

        <form
          className="cnv-explorer-toolbar"
          onSubmit={(event) => {
            event.preventDefault();
            setAppliedSearch(searchInput.trim());
          }}
        >
          {assemblies && assemblies.length > 1 ? (
            <label className="field-label">
              Assembly
              <select value={assembly} onChange={(event) => setAssembly(event.target.value)}>
                {assemblies.map((item) => (
                  <option key={item.assembly_name} value={item.assembly_name}>
                    {item.assembly_name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="field-label cnv-explorer-search">
            Search
            <input
              type="text"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Name, chromosome, or type"
            />
          </label>
          <button type="submit">Search</button>
          {appliedSearch ? (
            <button
              type="button"
              className="button-ghost"
              onClick={() => {
                setSearchInput('');
                setAppliedSearch('');
              }}
            >
              Clear
            </button>
          ) : null}
        </form>

        {isLoading ? (
          <p className="table-subtle">Loading clinical CNVs…</p>
        ) : rows.length === 0 ? (
          <div className="variant-results-empty">
            <p className="table-empty">No clinical CNVs match the current search.</p>
          </div>
        ) : (
          <div className="analysis-results-card overflow-x-auto">
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>CNV</th>
                  <th>Cytoband</th>
                  <th>Location</th>
                  <th className="table-mono">Size</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((cnv) => (
                  <tr key={cnv._id}>
                    <td>
                      <Link to={`/cnv-details/${cnv._id}`} className="table-link">
                        {cnv.label}
                      </Link>
                    </td>
                    <td className="table-mono">
                      {cnv.cytoband || <span className="table-empty">—</span>}
                    </td>
                    <td className="table-mono">
                      {cnv.chr}:{formatBp(cnv.start)}–{formatBp(cnv.end)}
                    </td>
                    <td className="table-mono">{formatSize(Math.max(cnv.end - cnv.start, 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default ClinicalCnvExplorerPage;
