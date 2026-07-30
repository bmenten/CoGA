import React, { useState } from 'react';
import api, { resolveApiUrl } from '../../lib/api';
import InfoTip from '../../components/InfoTip';
import type { ApiFamilyRecord, ApiSampleSequencingQcEvaluation } from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import {
  formatSequencingQcDetail,
  formatSequencingQcSummary,
  sequencingQcForMember,
} from './familyDetailHelpers';

interface SampleQcCellProps {
  familyId: string;
  member?: ApiFamilyRecord['members'][number];
}

/**
 * Chip tone per verdict. `warn` is amber and `fail` red — the two the operator asked
 * for; `pass` and `skip` stay neutral so a coloured chip always means "look at this".
 * `skip` is this codebase's word for "no cut-off configured", i.e. not assessed.
 */
const VERDICT_CHIP_CLASS: Record<ApiSampleSequencingQcEvaluation['verdict'], string> = {
  pass: 'family-qc-chip--neutral',
  warn: 'table-chip--warning',
  fail: 'table-chip--critical',
  skip: 'family-qc-chip--neutral',
};

/**
 * The verdict word shown beside the depth, using the same labels as the sample-integrity
 * QC page (`FamilySampleQcPage` STATUS_META) rather than a second spelling.
 *
 * Only the problem verdicts are labelled: a passing or unassessed chip shows the number
 * alone and stays narrow, so a word beside the depth always means "look at this". Colour
 * is never the only carrier of a QC failure — this is a regulated device and a
 * colour-vision-deficient reviewer must still be able to read the outcome.
 */
const VERDICT_LABEL: Record<ApiSampleSequencingQcEvaluation['verdict'], string> = {
  pass: '',
  warn: 'Warning',
  fail: 'Fail',
  skip: '',
};

/** Sentence describing which metrics breached, for the tooltip. */
const breachSentence = (qc: ApiSampleSequencingQcEvaluation): string | null => {
  if (qc.verdict !== 'warn' && qc.verdict !== 'fail') return null;
  const breached = qc.metrics.filter((metric) => metric.verdict === 'warn' || metric.verdict === 'fail');
  if (!breached.length) return null;
  const described = breached
    .map((metric) => {
      const bound = metric.verdict === 'fail' ? metric.error_value : metric.warn_value;
      const side = metric.direction === 'lower_is_worse' ? 'below' : 'above';
      return `${metric.label} ${metric.value} ${side} ${bound}`;
    })
    .join('; ');
  const gate = qc.verdict === 'fail' ? 'Failed' : 'Warning';
  return `${gate}: ${described}`;
};

/**
 * Sequencing QC for one family member — a compact pill beside the Status chip.
 *
 * It shows the QC verdict and mean depth; every recorded metric, the cut-offs it was
 * judged against and which of them breached are on hover. Clicking opens the pipeline's
 * rendered QC report.
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
        `/families/${encodeURIComponent(familyId)}/qc-report/${encodeURIComponent(member.sample_id)}/link`,
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

  const verdict = evaluation?.verdict ?? 'skip';
  const verdictWord = VERDICT_LABEL[verdict];
  const toneClass = VERDICT_CHIP_CLASS[verdict];
  // Verdict first, then the headline number: "warn 18.6x". The verdict covers the case
  // where a metric other than depth is the one that breached.
  const label = [verdictWord, summary].filter(Boolean).join(' ') || 'QC report';

  const tooltip = [
    breachSentence(evaluation ?? { verdict: 'skip', metrics: [], breached: [] }),
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
        <InfoTip
          label={tooltip}
          className={`table-chip family-qc-chip-static ${toneClass}`.trim()}
          interactiveChild
        >
          {label}
        </InfoTip>
      )}
      {error && <div className="dashboard-link-note family-qc-cell-error">{error}</div>}
    </div>
  );
};

export default SampleQcCell;
