import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import api from '../../lib/api';
import type { ApiAssemblyRecord, ApiClinicalCnv } from '../../lib/apiTypes';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import { CLINVAR_SUPPORT_NOT_RECORDED, clinvarSupportSummary } from '../../lib/clinicalCnvSupport';

// The catalogue request's page size; reaching it means there may be more (#526).
const CATALOG_LIMIT = 1000;

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

  const {
    data: assemblies,
    isLoading: assembliesLoading,
    isError: assembliesFailed,
    refetch: refetchAssemblies,
  } = useQuery<ApiAssemblyRecord[]>({
    queryKey: ['assemblies'],
    queryFn: async () => (await api.get('/assemblies')).data as ApiAssemblyRecord[],
  });

  useEffect(() => {
    if (!assembly && assemblies && assemblies.length > 0) {
      setAssembly(assemblies[0].assembly_name);
    }
  }, [assembly, assemblies]);

  const {
    data: cnvs,
    isLoading,
    error: cnvsError,
    refetch: refetchCnvs,
  } = useQuery<ApiClinicalCnv[]>({
    queryKey: ['cnv-catalog', assembly, appliedSearch],
    queryFn: async () => {
      const res = await api.get(apiPath`/cnvs/${assembly}/catalog`, {
        params: { search: appliedSearch || undefined, limit: CATALOG_LIMIT },
      });
      return res.data as ApiClinicalCnv[];
    },
    enabled: Boolean(assembly),
  });

  const rows = cnvs ?? [];
  const truncated = rows.length >= CATALOG_LIMIT;

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card space-y-4">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Clinical CNV explorer</p>
            <h1 className="catalog-card-title">
              Clinical CNVs
              {cnvs && !cnvsError ? (
                <span className="variant-results-count">
                  {rows.length.toLocaleString()}
                  {truncated ? '+' : ''}
                </span>
              ) : null}
            </h1>
            <p className="page-copy">
              Curated recurrent CNV syndromes (ClinGen / DECIPHER / literature) available in this
              CoGA instance. Select one to view its details.
            </p>
          </div>
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

        {/* A failed or unfinished lookup is not an empty catalogue: "no match" would read
            as "this syndrome is not in the catalogue" (#526, as #510). */}
        {assembliesLoading ? (
          <p className="table-subtle">Loading assemblies…</p>
        ) : assembliesFailed ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--error" role="alert">
            Could not load the assemblies — this is not an empty result.{' '}
            <button type="button" className="button-link" onClick={() => void refetchAssemblies()}>
              Retry
            </button>
          </div>
        ) : !assemblies || assemblies.length === 0 ? (
          <p className="table-subtle">
            No assembly is set up in this CoGA instance, so there is no CNV catalogue to show.
          </p>
        ) : isLoading ? (
          <p className="table-subtle">Loading clinical CNVs…</p>
        ) : cnvsError ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--error" role="alert">
            Could not load the clinical CNV catalogue (
            {getErrorMessage(cnvsError, 'the request failed').replace(/\.+$/, '')}) — this is not an
            empty result.{' '}
            <button type="button" className="button-link" onClick={() => void refetchCnvs()}>
              Retry
            </button>
          </div>
        ) : rows.length === 0 ? (
          <div className="variant-results-empty">
            <p className="table-empty">No clinical CNVs match the current search.</p>
          </div>
        ) : (
          <div className="analysis-results-card overflow-x-auto">
            {truncated ? (
              <p className="table-subtle">
                Showing the first {CATALOG_LIMIT.toLocaleString()} CNVs; narrow the search to see
                the rest.
              </p>
            ) : null}
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>CNV</th>
                  <th>Cytoband</th>
                  <th>Location</th>
                  <th className="table-mono">Size</th>
                  <th title="Pathogenic ClinVar CNVs overlapping the region by at least 30 % reciprocally">
                    ClinVar P/LP
                  </th>
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
                    <td className="table-mono">
                      {clinvarSupportSummary(cnv) ?? (
                        <span className="table-empty" title={CLINVAR_SUPPORT_NOT_RECORDED}>
                          —
                        </span>
                      )}
                    </td>
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
