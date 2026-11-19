import React from 'react';

import InfoTip from '../../components/InfoTip';
import type { ApiFamilyRecord, ApiSampleSequencingQcMetric } from '../../lib/apiTypes';
import { crossedLimit, formatQcLimit, formatQcMetricValue } from '../../lib/qcMetricValue';
import { QC_STATUS_CHIP, QC_STATUS_LABEL } from '../../lib/qcStatus';
import { analysisLabel } from '../../lib/familyAnalyses';

/**
 * Each member's sequencing QC as the pipeline reported it and the family record serves it,
 * judged against the family's QC cut-offs, as columns of the Sample QC page's per-sample table:
 * for a PGT family the mean coverage and Qualimap's alignment metrics of every sample, and for
 * each embryo its allele drop-out and drop-in (Picard) and its Mendelian concordance with its
 * parents before and after imputation (RTG Tools), read from the pipeline's tables
 * (`picard/<family>_ADO_ADI.csv`, `rtgtools/<family>_{cohort,imputed}_concordance.csv`): the
 * numbers its dashboard shows. A value without a cut-off is shown and not judged; a breached one
 * carries its verdict.
 */

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

export interface SequencingQcColumn {
  key: string;
  label: string;
  /** What the column head says: the dashboard's short name, else the server's label. */
  name: string;
}

/**
 * Every metric any member has, of any analysis, once, in the order the server lists them (its
 * catalogue's).
 */
export const sequencingQcColumns = (members: ApiFamilyRecord['members']): SequencingQcColumn[] => {
  const columns = new Map<string, SequencingQcColumn>();
  for (const member of members) {
    for (const metric of Object.values(member.sequencing_qc ?? {}).flatMap((evaluation) => evaluation.metrics)) {
      if (!columns.has(metric.metric_key)) {
        columns.set(metric.metric_key, {
          key: metric.metric_key,
          label: metric.label,
          name: COLUMN_NAME[metric.metric_key] ?? metric.label,
        });
      }
    }
  }
  return [...columns.values()];
};

/**
 * What the sequencing QC was judged against, for the text above the table: each analysis's QC
 * profiles (its own, else the family's named one, else the default, metric by metric), and the
 * measures no member's value of was judged (no cut-off is set for them).
 */
export const sequencingQcNote = (members: ApiFamilyRecord['members'], columns: SequencingQcColumn[]): string => {
  const profilesByAnalysis = new Map<string, string>();
  for (const member of members) {
    for (const [analysis, evaluation] of Object.entries(member.sequencing_qc ?? {})) {
      const labels = (evaluation.profiles ?? []).map((profile) => profile.label);
      if (labels.length && !profilesByAnalysis.has(analysis)) profilesByAnalysis.set(analysis, labels.join(', else '));
    }
  }
  const several = profilesByAnalysis.size > 1;
  const profiles = [...profilesByAnalysis.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([analysis, labels]) => (several ? `${analysisLabel(analysis) ?? analysis}: ${labels}` : labels));
  const unjudged = columns.filter((column) =>
    members.every((member) =>
      Object.values(member.sequencing_qc ?? {}).every((evaluation) => {
        const metric = evaluation.metrics.find((entry) => entry.metric_key === column.key);
        return !metric || metric.verdict === 'skip';
      }),
    ),
  );
  return [
    'Sequencing & embryo QC as the pipeline reported it: coverage and alignment, and for each embryo its allele ' +
      'drop-out (ADO) and drop-in (ADI) and its Mendelian concordance with its parents before and after imputation.',
    profiles.length
      ? `Judged against the cut-offs of ${profiles.join('; ')}, metric by metric.`
      : 'No QC profile applies to this family.',
    unjudged.length
      ? `No cut-off is set for ${unjudged.map((column) => column.name).join(', ')}: those values are shown, not judged.`
      : '',
  ]
    .filter(Boolean)
    .join(' ');
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

/** One member's value of one measure, a breached one with its verdict; "-" when it has none. */
export const SequencingQcCell: React.FC<{ metric: ApiSampleSequencingQcMetric | undefined }> = ({ metric }) => {
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
