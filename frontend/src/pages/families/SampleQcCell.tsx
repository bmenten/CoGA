import React, { useState } from 'react';
import api from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatSequencingQcSummary, sequencingQcForMember } from './familyDetailHelpers';

interface SampleQcCellProps {
  familyId: string;
  member?: ApiFamilyRecord['members'][number];
}

/**
 * Sequencing QC for one family member: the headline numbers recorded at import, plus a
 * link to the pipeline's rendered QC report (NanoPlot today, MultiQC once the pipeline
 * switches).
 *
 * The report is fetched through a short-lived link rather than a direct href: the API
 * is bearer-authenticated, so a plain navigation would 401, and the report itself is
 * untrusted pipeline HTML that the backend serves sandboxed. Opening it in a new tab
 * with `noopener` keeps it away from this document.
 */
const SampleQcCell: React.FC<SampleQcCellProps> = ({ familyId, member }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const qc = member ? sequencingQcForMember(member) : undefined;
  const summary = formatSequencingQcSummary(qc);

  if (!qc) {
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
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(getErrorMessage(err, 'Could not open the QC report'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1">
      {summary && (
        <span className="table-chip" title="Mean depth · read-length N50 · median read quality">
          {summary}
        </span>
      )}
      {qc.report ? (
        <button type="button" className="button-ghost" onClick={openReport} disabled={busy}>
          {busy ? 'Opening…' : 'QC report'}
        </button>
      ) : (
        !summary && <span className="dashboard-link-note">-</span>
      )}
      {error && <div className="dashboard-link-note">{error}</div>}
    </div>
  );
};

export default SampleQcCell;
