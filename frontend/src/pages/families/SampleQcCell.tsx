import React, { useState } from 'react';
import api, { resolveApiUrl } from '../../lib/api';
import InfoTip from '../../components/InfoTip';
import type {
  ApiFamilyRecord,
  ApiSampleSequencingQcEvaluation,
  ApiSampleSequencingQcMetric,
  QcStatus,
} from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import { QC_STATUS_LABEL } from '../../lib/qcStatus';
import {
  formatSequencingQcDetail,
  formatSequencingQcSummary,
  sequencingQcForMember,
} from './familyDetailHelpers';
import { apiPath } from '../../lib/apiPath';
import { crossedLimit, formatQcLimit, formatQcMetricValue } from '../../lib/qcMetricValue';
import type { FamilyQcReportLinkOut } from '../../lib/apiSchema.generated';

interface SampleQcCellProps {
  familyId: string;
  member?: ApiFamilyRecord['members'][number];
}

/**
 * Chip tone per verdict. `warn` is amber and `fail` red — the two the operator asked
 * for; `pass` stays neutral so a coloured chip always means "look at this". `skip` is
 * this codebase's word for "not assessed" (no cut-off configured, or no verdict served):
 * it takes the grey the app gives a check that did not run (`QC_STATUS_CHIP.skip`, as on
 * the Sample QC page), so it does not wear the passing chip's tone.
 */
const VERDICT_CHIP_CLASS: Record<QcStatus, string> = {
  pass: 'family-qc-chip--neutral',
  warn: 'table-chip--warning',
  fail: 'table-chip--critical',
  skip: 'table-chip--neutral',
};

/**
 * The verdict word shown beside the number, in the app-wide QC vocabulary
 * (`QC_STATUS_LABEL`, as on the sample-integrity QC page) rather than a second spelling.
 *
 * A passing chip shows the number alone and stays narrow, so a word beside the number
 * always means "look at this": a warning, a fail, or QC that was not assessed ("Not
 * run"), which must never read as a pass (REQ-QC-004). A passing chip with no number to
 * show says "Pass", so a served verdict never leaves the chip empty or "-". Colour is never
 * the only carrier of the state — this is a regulated device and a colour-vision-deficient
 * reviewer must still be able to read the outcome.
 */
const VERDICT_LABEL: Record<QcStatus, string> = {
  pass: '',
  warn: QC_STATUS_LABEL.warn,
  fail: QC_STATUS_LABEL.fail,
  skip: QC_STATUS_LABEL.skip,
};

/**
 * Why a sample's QC was not assessed, leading its tooltip: none of its metrics has a
 * limit in the family's profile (the backend's `skip`), or the family read served no
 * verdict for it (nothing it could judge, or the evaluation failed).
 */
const NOT_RUN_NO_LIMIT =
  `${QC_STATUS_LABEL.skip}: not assessed, because no limit is configured ` +
  "for any of this sample's metrics";
const NOT_RUN_NO_VERDICT =
  `${QC_STATUS_LABEL.skip}: not assessed, because the server returned no QC verdict ` +
  'for this sample';

const isBreach = (metric: ApiSampleSequencingQcMetric): boolean =>
  metric.verdict === 'warn' || metric.verdict === 'fail';

/**
 * A breaching metric in the tooltip: "Mean depth 18.6x below 20x". The value is written as
 * the chip writes it (`formatQcMetricValue`, shared with a signed version's sequencing QC);
 * the limit exactly as configured.
 */
const describeBreach = (metric: ApiSampleSequencingQcMetric): string => {
  const bound = crossedLimit(metric);
  const value = formatQcMetricValue(metric);
  if (typeof bound !== 'number') return `${metric.label} ${value}`;
  return `${metric.label} ${value} ${metric.direction === 'lower_is_worse' ? 'below' : 'above'} ${formatQcLimit(metric.unit, bound)}`;
};

/**
 * The short name a metric goes by on the chip, before its value ("ADO 32.1%"), where the
 * chip shows the metric that decided the verdict rather than a depth. The breach sentence
 * names it in full ("Allele drop-out (ADO) 32.1% above 30%"). A metric not listed goes by
 * the label the server serves with it.
 */
const CHIP_METRIC_NAME: Record<string, string> = {
  'pgt.allele_dropout_rate': 'ADO',
  'pgt.allele_dropin_rate': 'ADI',
  'pgt.mendelian_concordance': 'Concordance',
  'pgt.mendelian_concordance_imputed': 'Imputed concordance',
};

/**
 * The metric that decided a served verdict: the first metric, in the order the server
 * lists them (its catalogue's), whose own verdict is the sample's. That is the first
 * failing metric of a fail, the first warning of a warning, and the first metric judged
 * within its limits of a pass. A sample that was not assessed has none: no limit judged
 * any of its numbers.
 */
const decidingMetric = (
  evaluation: ApiSampleSequencingQcEvaluation | null | undefined,
): ApiSampleSequencingQcMetric | null => {
  if (!evaluation || evaluation.verdict === 'skip') return null;
  return evaluation.metrics.find((metric) => metric.verdict === evaluation.verdict) ?? null;
};

/** The deciding metric as the chip writes it: "ADO 32.1%", or null when there is none. */
const formatDecidingMetric = (evaluation: ApiSampleSequencingQcEvaluation | null | undefined): string | null => {
  const metric = decidingMetric(evaluation);
  if (!metric) return null;
  return `${CHIP_METRIC_NAME[metric.metric_key] ?? metric.label} ${formatQcMetricValue(metric)}`;
};

/** Sentence describing which metrics breached, for the tooltip. */
const breachSentence = (qc: ApiSampleSequencingQcEvaluation): string | null => {
  if (qc.verdict !== 'warn' && qc.verdict !== 'fail') return null;
  const breaches = qc.metrics.filter(isBreach);
  if (!breaches.length) return null;
  const gate = qc.verdict === 'fail' ? 'Failed' : 'Warning';
  return `${gate}: ${breaches.map(describeBreach).join('; ')}`;
};

/** The tooltip's first sentence: why the sample was not assessed, or what breached. */
const leadSentence = (evaluation: ApiSampleSequencingQcEvaluation | null | undefined): string | null => {
  if (!evaluation) return NOT_RUN_NO_VERDICT;
  if (evaluation.verdict === 'skip') return NOT_RUN_NO_LIMIT;
  return breachSentence(evaluation);
};

/**
 * Sequencing QC for one family member — a compact pill beside the Status chip.
 *
 * It shows the QC verdict and one number: the mean depth (or the read-length N50), or,
 * for a sample that has neither, the value of the metric that decided the verdict, such as
 * an embryo's allele drop-out when the PGT pipeline's family tables are all its QC holds
 * ("Fail ADO 32.1%"). Every recorded metric, the cut-offs it was judged against and which
 * of them breached are on hover or keyboard focus. Clicking opens the pipeline's rendered
 * QC report.
 *
 * The report opens through a short-lived link rather than a direct href: the API is
 * bearer-authenticated, so a plain navigation would 401, and the report is untrusted
 * pipeline HTML the backend serves sandboxed. `noopener` keeps it off this document.
 */
const SampleQcCell: React.FC<SampleQcCellProps> = ({ familyId, member }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const qc = member ? sequencingQcForMember(member) : undefined;
  const evaluation = member?.sequencing_qc;
  const summary = formatSequencingQcSummary(qc);
  const detail = formatSequencingQcDetail(qc);

  // "-" means no sequencing QC to show. A served verdict always shows, and so does recorded
  // QC the server did not judge (it reads "Not run" below): neither is an empty cell.
  if (!evaluation && !summary && !detail && !qc?.report) {
    return <span className="dashboard-link-note">-</span>;
  }

  const openReport = async () => {
    if (!member) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.get<FamilyQcReportLinkOut>(
        apiPath`/families/${familyId}/qc-report/${member.sample_id}/link`,
      );
      const url = response.data?.url;
      if (!url) {
        setError('No QC report is available');
        return;
      }
      // The link is backend-relative in local mode; without the API base the browser
      // resolves it against the SPA origin and the client router shows "page not found".
      window.open(resolveApiUrl(url), '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(getErrorMessage(err, 'Could not open the QC report'));
    } finally {
      setBusy(false);
    }
  };

  // No verdict served reads as not assessed, never as a pass.
  const verdict = evaluation?.verdict ?? 'skip';
  const verdictWord = VERDICT_LABEL[verdict];
  const toneClass = VERDICT_CHIP_CLASS[verdict];
  // Verdict first, then the number: "Warning 18.6x", "Not run 18.6x". The depth stays the
  // number where the sample has one, and the verdict covers the case where another metric
  // is the one that breached; without a depth or N50 the number is the value of the metric
  // that decided the verdict ("Fail ADO 32.1%"). A sample that was not assessed has no such
  // metric and reads "Not run" alone, and a pass with no number reads "Pass".
  const number = summary ?? formatDecidingMetric(evaluation);
  const label = [verdictWord, number].filter(Boolean).join(' ') || QC_STATUS_LABEL[verdict];
  const report = qc?.report;

  const tooltip = [
    leadSentence(evaluation),
    detail,
    evaluation?.profile_label ? `Thresholds: ${evaluation.profile_label} profile` : null,
    report ? 'Click to open the pipeline QC report in a new tab' : null,
  ]
    .filter(Boolean)
    .join(' — ');

  return (
    <div className="family-qc-cell">
      {/* InfoTip rather than a `title`: the native tooltip only appears after a
          browser-controlled delay of a second or more, which reads as broken on a
          chip whose whole point is the number behind it. */}
      {report ? (
        <InfoTip label={tooltip} interactiveChild>
          <button
            type="button"
            className={`table-chip family-qc-chip ${toneClass}`.trim()}
            onClick={openReport}
            disabled={busy}
          >
            <span className="family-qc-chip-value">{busy ? 'Opening…' : label}</span>
          </button>
        </InfoTip>
      ) : (
        // With no report there is nothing to open, so the chip is no button; it still
        // takes keyboard focus, or its tooltip, and the breach it names, would be mouse-only.
        <InfoTip
          label={tooltip}
          className={`table-chip family-qc-chip-static ${toneClass}`.trim()}
          staticChild
        >
          {label}
        </InfoTip>
      )}
      {error && <div className="dashboard-link-note family-qc-cell-error">{error}</div>}
    </div>
  );
};

export default SampleQcCell;
