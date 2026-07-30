import React, { useState } from 'react';
import api, { resolveApiUrl } from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
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
 * Sequencing QC for one family member.
 *
 * The metrics recorded at import *are* the control that opens the pipeline's rendered
 * QC report (NanoPlot today, MultiQC once the pipeline switches) — one target rather
 * than a chip beside a button, since both said the same thing.
 *
 * The report opens through a short-lived link rather than a direct href: the API is
 * bearer-authenticated, so a plain navigation would 401, and the report is untrusted
 * pipeline HTML the backend serves sandboxed. `noopener` keeps it off this document.
 */
const SampleQcCell: React.FC<SampleQcCellProps> = ({ familyId, member }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const qc = member ? sequencingQcForMember(member) : undefined;
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

  // Without a report there is nothing to open, so the metrics stay a plain chip
  // rather than a control that looks clickable and does nothing.
  const label = summary ?? 'QC report';

  return (
    <div className="family-qc-cell">
      {qc.report ? (
        <button
          type="button"
          className="table-chip family-qc-chip"
          onClick={openReport}
          disabled={busy}
          title={[detail, 'Opens the pipeline QC report in a new tab'].filter(Boolean).join('\n')}
        >
          <span className="family-qc-chip-value">{busy ? 'Opening…' : label}</span>
          <span className="family-qc-chip-icon" aria-hidden="true">
            ↗
          </span>
        </button>
      ) : (
        <span className="table-chip family-qc-chip-static" title={detail ?? undefined}>
          {label}
        </span>
      )}
      {error && <div className="dashboard-link-note family-qc-cell-error">{error}</div>}
    </div>
  );
};

export default SampleQcCell;
