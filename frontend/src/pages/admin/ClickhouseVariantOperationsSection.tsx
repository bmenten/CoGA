import React from 'react';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import {
  formatCount,
  formatStorageBytes,
  type ClickHouseVariantAssemblyStatus,
  type ClickHouseVariantIntegrity,
} from './dataManagementTypes';
import { apiPath } from '../../lib/apiPath';

const INTEGRITY_LABELS: Record<ClickHouseVariantIntegrity['status'], string> = {
  ok: 'Healthy',
  degraded: 'Degraded',
  corrupt: 'Corrupt',
  missing: 'No tables',
};

type RunAction = (
  key: string,
  confirmation: string,
  action: () => Promise<unknown>,
  successMessage: string,
) => void;

interface ClickhouseVariantOperationsSectionProps {
  assemblies: ClickHouseVariantAssemblyStatus[];
  loading: boolean;
  errorMessage?: string | null;
  busyKey: string | null;
  onRunAction: RunAction;
}

const HEALTH_LABELS: Record<ClickHouseVariantAssemblyStatus['health'], string> = {
  ready: 'Ready',
  mutating: 'Pending mutations',
  missing: 'Missing tables',
};

const trimAssemblyPrefix = (assemblyName: string, tableName: string): string =>
  tableName.startsWith(`${assemblyName}/`) ? tableName.slice(assemblyName.length + 1) : tableName;

const ClickhouseVariantOperationsSection: React.FC<ClickhouseVariantOperationsSectionProps> = ({
  assemblies,
  loading,
  errorMessage,
  busyKey,
  onRunAction,
}) => {
  // The integrity check is a read-only probe (unlike the mutating onRunAction
  // operations), so it manages its own per-assembly fetch state and shows the
  // returned report inline.
  const [integrity, setIntegrity] = React.useState<Record<string, ClickHouseVariantIntegrity>>({});
  const [integrityBusy, setIntegrityBusy] = React.useState<string | null>(null);
  const [integrityError, setIntegrityError] = React.useState<Record<string, string>>({});

  const runIntegrityCheck = async (assemblyName: string) => {
    setIntegrityBusy(assemblyName);
    setIntegrityError((prev) => {
      const next = { ...prev };
      delete next[assemblyName];
      return next;
    });
    try {
      const response = await api.get<ClickHouseVariantIntegrity>(
        `/admin/clickhouse/variants/${assemblyName}/integrity`,
      );
      setIntegrity((prev) => ({ ...prev, [assemblyName]: response.data }));
    } catch (error) {
      setIntegrityError((prev) => ({ ...prev, [assemblyName]: getErrorMessage(error) }));
    } finally {
      setIntegrityBusy(null);
    }
  };

  return (
    <section className="surface-card space-y-4" aria-label="ClickHouse variant operations">
      <div className="page-header">
        <div className="space-y-2">
          <p className="page-kicker">ClickHouse</p>
          <h2 className="section-title">Variant operations</h2>
          <p className="section-copy">
            Inspect CoGA variant tables per assembly, then ensure, rebuild or optimize them when
            operational cleanup is needed.
          </p>
        </div>
      </div>

      {loading ? (
        <p className="table-empty">Loading ClickHouse variant status…</p>
      ) : errorMessage ? (
        <p className="table-empty">{errorMessage}</p>
      ) : assemblies.length === 0 ? (
        <p className="table-empty">No ClickHouse-backed variant assemblies are available yet.</p>
      ) : (
        <div className="admin-variant-ops-grid">
          {assemblies.map((assembly) => (
            <article key={assembly.assembly_name} className="admin-variant-card">
              <div className="admin-variant-card-header">
                <div className="space-y-2">
                  <h3>{assembly.assembly_name}</h3>
                  <div className="admin-variant-metrics">
                    <span className="badge-chip">{HEALTH_LABELS[assembly.health]}</span>
                    <span className="badge-chip">
                      {formatCount(assembly.small_variant_rows)} SNV rows
                    </span>
                    <span className="badge-chip">
                      {formatCount(assembly.structural_variant_rows)} SV rows
                    </span>
                    <span className="badge-chip">
                      {formatCount(assembly.pending_mutations)} pending mutations
                    </span>
                    <span className="badge-chip">
                      {assembly.existing_table_count}/{assembly.expected_table_count} objects
                    </span>
                    <span className="badge-chip">
                      {formatStorageBytes(assembly.total_bytes_on_disk)}
                    </span>
                  </div>
                </div>
                <div className="inline-actions">
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={busyKey === `clickhouse-ensure:${assembly.assembly_name}`}
                    onClick={() =>
                      onRunAction(
                        `clickhouse-ensure:${assembly.assembly_name}`,
                        `Ensure the ClickHouse variant tables for assembly ${assembly.assembly_name}?`,
                        () => api.post(apiPath`/admin/clickhouse/variants/${assembly.assembly_name}/ensure`),
                        `Ensured ClickHouse variant tables for ${assembly.assembly_name}.`,
                      )
                    }
                  >
                    Ensure tables
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={integrityBusy === assembly.assembly_name}
                    onClick={() => runIntegrityCheck(assembly.assembly_name)}
                  >
                    {integrityBusy === assembly.assembly_name ? 'Checking…' : 'Integrity check'}
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={busyKey === `clickhouse-rebuild-gene-index:${assembly.assembly_name}`}
                    onClick={() =>
                      onRunAction(
                        `clickhouse-rebuild-gene-index:${assembly.assembly_name}`,
                        `Rebuild the small variant gene index for assembly ${assembly.assembly_name}?`,
                        () =>
                          api.post(
                            apiPath`/admin/clickhouse/variants/${assembly.assembly_name}/rebuild-small-variant-gene-index`,
                          ),
                        `Rebuilt small variant gene index for ${assembly.assembly_name}.`,
                      )
                    }
                  >
                    Rebuild gene index
                  </button>
                  <button
                    type="button"
                    className="form-button"
                    disabled={busyKey === `clickhouse-optimize:${assembly.assembly_name}`}
                    onClick={() =>
                      onRunAction(
                        `clickhouse-optimize:${assembly.assembly_name}`,
                        `Optimize the ClickHouse variant tables for assembly ${assembly.assembly_name}?`,
                        () => api.post(apiPath`/admin/clickhouse/variants/${assembly.assembly_name}/optimize`),
                        `Optimized ClickHouse variant tables for ${assembly.assembly_name}.`,
                      )
                    }
                  >
                    Optimize tables
                  </button>
                </div>
              </div>

              {assembly.missing_tables.length > 0 && (
                <div className="admin-variant-missing">
                  <span className="admin-project-access-label">Missing objects</span>
                  <div className="admin-project-access-chip-list">
                    {assembly.missing_tables.map((tableName) => (
                      <span key={tableName} className="badge-chip">
                        {trimAssemblyPrefix(assembly.assembly_name, tableName)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {integrityError[assembly.assembly_name] && (
                <p className="table-empty">{integrityError[assembly.assembly_name]}</p>
              )}

              {integrity[assembly.assembly_name] && (
                <div
                  className="admin-variant-integrity"
                  aria-label={`Integrity report for ${assembly.assembly_name}`}
                >
                  <div className="admin-variant-metrics">
                    <span className="badge-chip">
                      Integrity: {INTEGRITY_LABELS[integrity[assembly.assembly_name].status]}
                    </span>
                    {integrity[assembly.assembly_name].detached_broken_parts.length > 0 && (
                      <span className="badge-chip">
                        {formatCount(
                          integrity[assembly.assembly_name].detached_broken_parts.reduce(
                            (sum, part) => sum + part.count,
                            0,
                          ),
                        )}{' '}
                        detached broken parts
                      </span>
                    )}
                    {integrity[assembly.assembly_name].gene_index_consistency.checked && (
                      <span className="badge-chip">
                        gene_index{' '}
                        {integrity[assembly.assembly_name].gene_index_consistency.consistent
                          ? 'consistent'
                          : `drift ${integrity[assembly.assembly_name].gene_index_consistency.drift}`}
                      </span>
                    )}
                  </div>
                  {integrity[assembly.assembly_name].notes.length > 0 && (
                    <ul className="admin-variant-integrity-notes">
                      {integrity[assembly.assembly_name].notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <div className="admin-variant-table-list">
                {assembly.tables.map((table) => (
                  <div
                    key={table.name}
                    className={`admin-variant-table-row${
                      table.exists ? '' : ' admin-variant-table-row--missing'
                    }`}
                  >
                    <div className="admin-variant-table-copy">
                      <strong>{trimAssemblyPrefix(assembly.assembly_name, table.name)}</strong>
                      <span>{table.engine || table.kind}</span>
                    </div>
                    <div className="admin-variant-table-metrics">
                      <span>{formatCount(table.row_count)} rows</span>
                      <span>{formatStorageBytes(table.bytes_on_disk)}</span>
                      <span>{formatCount(table.pending_mutations)} mutations</span>
                    </div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
};

export default ClickhouseVariantOperationsSection;
