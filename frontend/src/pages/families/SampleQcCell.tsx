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
 * The verdict word shown beside the depth, in the app-wide QC vocabulary
 * (`QC_STATUS_LABEL`, as on the sample-integrity QC page) rather than a second spelling.
 *
 * A passing chip shows the number alone and stays narrow, so a word beside the depth
 * always means "look at this": a warning, a fail, or QC that was not assessed ("Not
 * run"), which must never read as a pass (REQ-QC-004). Colour is never the only carrier
 * of the state — this is a regulated device and a colour-vision-deficient reviewer must
 * still be able to read the outcome.
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

/**
 * How a metric's value is written, per unit, rounded as the chip and the tooltip's metric
 * list round it: "18.6x", "6.4%", "Q32", "14,283 bp". `digits` is the default rounding;
 * a unit not listed here is written as served, with its unit after it.
 */
interface UnitFormat {
  digits?: number;
  write: (value: number, digits: number) => string;
}

const grouped = (value: number, digits: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const UNIT_FORMAT: Record<string, UnitFormat> = {
  x: { digits: 1, write: (value, digits) => `${value.toFixed(digits)}x` },
  '%': { digits: 1, write: (value, digits) => `${value.toFixed(digits)}%` },
  Q: { digits: 0, write: (value, digits) => `Q${value.toFixed(digits)}` },
  bp: { digits: 0, write: (value, digits) => `${grouped(value, digits)} bp` },
  reads: { digits: 0, write: (value, digits) => `${grouped(value, digits)} reads` },
  fraction: { digits: 3, write: (value, digits) => value.toFixed(digits) },
};

const unitFormat = (unit: string): UnitFormat =>
  UNIT_FORMAT[unit] ?? { write: (value, digits) => `${value.toFixed(digits)} ${unit}`.trim() };

/** The decimals a number is written with ("19.96" → 2), so it can be shown as it is. */
const decimalsOf = (value: number): number => {
  const [mantissa, exponent = '0'] = String(value).split('e');
  const decimals = (mantissa.split('.')[1] ?? '').length - Number(exponent);
  return Math.min(20, Math.max(0, decimals));
};

/**
 * A breaching metric in the tooltip: "Mean depth 18.6x below 20x". The value is rounded as
 * the chip rounds it, unless that would put it on or past the limit it crossed (19.96
 * against 20 is not "20.0x below 20x"): then it gets the decimals it needs to stay on the
 * failing side. The limit is written exactly as configured.
 */
const describeBreach = (metric: ApiSampleSequencingQcMetric): string => {
  const format = unitFormat(metric.unit);
  const bound = metric.verdict === 'fail' ? metric.error_value : metric.warn_value;
  const lowerIsWorse = metric.direction === 'lower_is_worse';
  const exact = decimalsOf(metric.value);
  let digits = format.digits ?? exact;
  if (typeof bound !== 'number') return `${metric.label} ${format.write(metric.value, digits)}`;
  while (digits < exact) {
    const shown = Number(metric.value.toFixed(digits));
    if (lowerIsWorse ? shown < bound : shown > bound) break;
    digits += 1;
  }
  const value = format.write(metric.value, digits);
  const limit = format.write(bound, decimalsOf(bound));
  return `${metric.label} ${value} ${lowerIsWorse ? 'below' : 'above'} ${limit}`;
};

/** Sentence describing which metrics breached, for the tooltip. */
const breachSentence = (qc: ApiSampleSequencingQcEvaluation): string | null => {
  if (qc.verdict !== 'warn' && qc.verdict !== 'fail') return null;
  const breached = qc.metrics.filter((metric) => metric.verdict === 'warn' || metric.verdict === 'fail');
  if (!breached.length) return null;
  const gate = qc.verdict === 'fail' ? 'Failed' : 'Warning';
  return `${gate}: ${breached.map(describeBreach).join('; ')}`;
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
 * It shows the QC verdict and mean depth; every recorded metric, the cut-offs it was
 * judged against and which of them breached are on hover or keyboard focus. Clicking
 * opens the pipeline's rendered QC report.
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

  if (!qc || (!summary && !qc.report)) {
    return <span className="dashboard-link-note">-</span>;
  }

  const openReport = async () => {
    if (!member) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.get(
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
  // Verdict first, then the headline number: "Warning 18.6x", "Not run 18.6x". The
  // verdict covers the case where a metric other than depth is the one that breached.
  const label = [verdictWord, summary].filter(Boolean).join(' ') || 'QC report';

  const tooltip = [
    leadSentence(evaluation),
    detail,
    evaluation?.profile_label ? `Thresholds: ${evaluation.profile_label} profile` : null,
    qc.report ? 'Click to open the pipeline QC report in a new tab' : null,
  ]
    .filter(Boolean)
    .join(' — ');

  return (
    <div className="family-qc-cell">
      {/* InfoTip rather than a `title`: the native tooltip only appears after a
          browser-controlled delay of a second or more, which reads as broken on a
          chip whose whole point is the number behind it. */}
      {qc.report ? (
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
