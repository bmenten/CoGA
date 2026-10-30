import React from 'react';

import InfoTip from '../../components/InfoTip';
import type { ApiFamilyRecord, ApiSampleSequencingQcMetric } from '../../lib/apiTypes';
import { crossedLimit, formatQcLimit, formatQcMetricValue } from '../../lib/qcMetricValue';
import { QC_STATUS_CHIP, QC_STATUS_LABEL } from '../../lib/qcStatus';

/**
 * The short names the PGT pipeline's dashboard gives its embryo measures, as column heads; a
 * metric not listed goes by the label the server serves with it, which the head's tip gives
 * in full.
 */
const COLUMN_NAME: Record<string, string> = {
  'pgt.allele_dropout_rate': 'ADO',
  'pgt.allele_dropin_rate': 'ADI',
  'pgt.mendelian_concordance': 'Concordance before imputation',
  'pgt.mendelian_concordance_imputed': 'Concordance after imputation',
};

interface Column {
  key: string;
  label: string;
}

/** Every metric any member has, once, in the order the server lists them (its catalogue's). */
export const sequencingQcColumns = (members: ApiFamilyRecord['members']): Column[] => {
  const columns = new Map<string, Column>();
  for (const member of members) {
    for (const metric of member.sequencing_qc?.metrics ?? []) {
      if (!columns.has(metric.metric_key)) columns.set(metric.metric_key, { key: metric.metric_key, label: metric.label });
    }
  }
  return [...columns.values()];
};

/** What a value is judged against: "warning above 30%, fail above 40%", or that no cut-off is set. */
const describeLimits = (metric: ApiSampleSequencingQcMetric): string => {
  const side = metric.direction === 'lower_is_worse' ? 'below' : 'above';
  const limits = [
    typeof metric.warn_value === 'number' ? `warning ${side} ${formatQcLimit(metric.unit, metric.warn_value)}` : null,
    typeof metric.error_value === 'number' ? `fail ${side} ${formatQcLimit(metric.unit, metric.error_value)}` : null,
  ].filter(Boolean);
  return limits.length ? limits.join(', ') : 'no cut-off is set, so it is shown and not judged';
};

const MetricCell: React.FC<{ metric: ApiSampleSequencingQcMetric | undefined }> = ({ metric }) => {
  if (!metric) return <span className="dashboard-link-note">-</span>;
  const value = formatQcMetricValue(metric);
  const breached = metric.verdict === 'warn' || metric.verdict === 'fail';
  const bound = crossedLimit(metric);
  const tip = breached && typeof bound === 'number'
    ? `${metric.label} ${value}: ${QC_STATUS_LABEL[metric.verdict].toLowerCase()} (${metric.direction === 'lower_is_worse' ? 'below' : 'above'} ${formatQcLimit(metric.unit, bound)})`
    : `${metric.label} ${value}: ${describeLimits(metric)}`;
  return (
    <InfoTip label={tip} staticChild>
      {breached ? <span className={QC_STATUS_CHIP[metric.verdict]}>{value} · {QC_STATUS_LABEL[metric.verdict]}</span> : value}
    </InfoTip>
  );
};

/**
 * Each member's sequencing QC as the pipeline reported it and the family record serves it,
 * judged against the family's QC cut-offs: for a PGT family the mean coverage and Qualimap's
 * alignment metrics of every sample, and for each embryo its allele drop-out and drop-in
 * (Picard) and its Mendelian concordance with its parents before and after imputation (RTG
 * Tools), read from the pipeline's tables (`picard/<family>_ADO_ADI.csv`,
 * `rtgtools/<family>_{cohort,imputed}_concordance.csv`): the numbers its dashboard shows. A
 * value without a cut-off is shown and not judged; a breached one carries its verdict.
 */
const SequencingQcTable: React.FC<{ members: ApiFamilyRecord['members'] }> = ({ members }) => {
  const rows = members.filter((member) => member.sequencing_qc?.metrics?.length);
  const columns = sequencingQcColumns(rows);
  if (!rows.length || !columns.length) return null;
  const profile = rows.map((member) => member.sequencing_qc?.profile_label).find(Boolean);
  const unjudged = columns.filter((column) =>
    rows.every((member) => {
      const metric = member.sequencing_qc?.metrics.find((entry) => entry.metric_key === column.key);
      return !metric || metric.verdict === 'skip';
    }),
  );
  return (
    <section className="surface-card space-y-2">
      <h2 className="section-title">Sequencing &amp; embryo QC</h2>
      <p className="table-subtle">
        As the pipeline reported them for each sample: coverage and alignment, and for each embryo its allele
        drop-out (ADO) and drop-in (ADI) and its Mendelian concordance with its parents before and after
        imputation. {profile ? `Judged against the cut-offs of ${profile}.` : 'No QC profile applies to this family.'}
        {unjudged.length
          ? ` No cut-off is set for ${unjudged.map((column) => COLUMN_NAME[column.key] ?? column.label).join(', ')}: those values are shown, not judged.`
          : ''}
      </p>
      <div className="data-table-shell overflow-x-auto">
        <table className="analysis-table family-members-table">
          <thead>
            <tr>
              <th>Sample</th>
              <th>Role</th>
              {columns.map((column) => (
                <th key={column.key} title={column.label}>
                  {COLUMN_NAME[column.key] ?? column.label}
                </th>
              ))}
              <th>QC</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((member) => {
              const evaluation = member.sequencing_qc!;
              const byKey = new Map(evaluation.metrics.map((metric) => [metric.metric_key, metric]));
              return (
                <tr key={member.sample_id}>
                  <td>{member.sample_id}</td>
                  <td>{member.role || '-'}</td>
                  {columns.map((column) => (
                    <td key={column.key}>
                      <MetricCell metric={byKey.get(column.key)} />
                    </td>
                  ))}
                  <td>
                    <span className={QC_STATUS_CHIP[evaluation.verdict]}>{QC_STATUS_LABEL[evaluation.verdict]}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default SequencingQcTable;
