import React, { useMemo, useState } from 'react';
import PageHeader from '../../components/PageHeader';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import ModalDialog from '../../components/ModalDialog';
import { getErrorMessage } from '../../lib/errorMessage';
import { apiPath } from '../../lib/apiPath';
import type {
  RepeatLocusCountUnitsCatalogueOut,
  RepeatLocusCountUnitsOut,
} from '../../lib/apiSchema.generated';
import type { StatusTone } from './dataManagementTypes';

const QUERY_KEY = ['admin', 'repeat-loci'];

const when = (value: string | null | undefined): string =>
  value ? `${value.replace('T', ' ').slice(0, 16)} UTC` : '';

/**
 * The repeat catalogue's loci with QA's record of whether TRGT's count at each is the clinical
 * repeat number its ranges count (owner's decision, 2026-10-14).
 *
 * TRGT counts the motif across the region its own catalogue gives a locus: at FMR1 it leaves
 * out the AGG interruptions, at HTT it counts one CAG more, at POLG four flanking GCT copies.
 * QA and the laboratory director check each locus; until they record it here as validated,
 * the repeat page and the genome tracks mark a count near a boundary there. The record changes
 * no status, and each change is a clinical audit event with its reason.
 */
const AdminRepeatLociPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [withCallsOnly, setWithCallsOnly] = useState(false);
  // The locus whose record is being changed, and to what: confirmed with a reason first.
  const [pending, setPending] = useState<{ locus: RepeatLocusCountUnitsOut; validated: boolean } | null>(null);
  const [reason, setReason] = useState('');

  const { data, isLoading, error } = useQuery<RepeatLocusCountUnitsCatalogueOut>({
    queryKey: QUERY_KEY,
    queryFn: async () =>
      (await api.get(apiPath`/admin/repeat-loci`)).data as RepeatLocusCountUnitsCatalogueOut,
    retry: false,
  });

  // The rows the laboratory's TRGT catalogue uses first: those its stored calls name.
  const loci = useMemo(
    () =>
      [...(data?.loci ?? [])]
        .filter((locus) => !withCallsOnly || locus.call_count > 0)
        .sort(
          (left, right) =>
            Number(right.call_count > 0) - Number(left.call_count > 0) ||
            left.gene.localeCompare(right.gene) ||
            left.locus_id.localeCompare(right.locus_id),
        ),
    [data?.loci, withCallsOnly],
  );
  const validatedCount = (data?.loci ?? []).filter((locus) => locus.validated).length;

  const saveMutation = useMutation({
    mutationFn: async (change: { locusId: string; validated: boolean; reason: string }) =>
      (
        await api.put(apiPath`/admin/repeat-loci/${change.locusId}/count-units`, {
          validated: change.validated,
          reason: change.reason,
        })
      ).data,
    onSuccess: async (_record, change) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      // The repeat page and the tracks read the record with their calls.
      await queryClient.invalidateQueries({ queryKey: ['family'] });
      await queryClient.invalidateQueries({ queryKey: ['repeat-expansions'] });
      await queryClient.invalidateQueries({ queryKey: ['genome-repeat-expansions'] });
      setStatus({
        tone: 'success',
        message: change.validated
          ? `Recorded TRGT's counting units at ${change.locusId} as validated.`
          : `Recorded TRGT's counting units at ${change.locusId} as not validated.`,
      });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not save the record.') }),
  });

  if (isLoading) {
    return (
      <PageState
        loading
        kicker="Admin"
        title="Loading the repeat catalogue"
        message="Fetching the repeat loci and their counting-unit records."
      />
    );
  }

  if (error) {
    return (
      <PageState
        kicker="Admin"
        title="Could not load the repeat catalogue"
        message={getErrorMessage(error, 'The repeat catalogue could not be loaded.')}
      />
    );
  }

  const margin = data?.margin ?? 0;

  return (
    <div className="page-shell admin-compact space-y-5">
      <PageHeader
        kicker="Administration"
        title="Repeat catalogue: TRGT counting units"
        description={
          <>
            TRGT counts the motif across the region its own catalogue gives a locus, which need not be the clinical repeat
            number the locus&apos;s ranges count. QA and the laboratory director check each locus the laboratory&apos;s TRGT
            catalogue uses. Until a locus is recorded here as validated, the repeat page and the genome tracks mark a count
            within {margin} repeats of a boundary there as near a boundary. The record changes no status. Record a locus as
            validated only when TRGT&apos;s count there is the clinical repeat number; a locus whose count needs a correction
            stays not validated until CoGA applies corrections.
          </>
        }
      >
        <p className="dashboard-link-note">
          {validatedCount} of {(data?.loci ?? []).length} loci validated. Each change is kept in the clinical audit trail
          with its reason.
        </p>
        {status && (
          <div
            className={`status-note ${
              status.tone === 'success' ? 'status-note--success' : 'status-note--error'
            }`}
          >
            {status.message}
          </div>
        )}
      </PageHeader>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Loci</h2>
            <p className="family-workspace-card-subtitle">
              The loci the stored calls name come first: they are the rows the laboratory&apos;s TRGT catalogue resolves to.
            </p>
          </div>
          <label className="family-repeat-filter-toggle">
            <input
              type="checkbox"
              checked={withCallsOnly}
              onChange={(event) => setWithCallsOnly(event.target.checked)}
            />
            <span>Loci with calls only</span>
          </label>
        </div>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
              <tr>
                <th>Locus</th>
                <th>Motif</th>
                <th>Calls</th>
                <th>Known about TRGT&apos;s count</th>
                <th>Counting units</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {loci.map((locus) => (
                <tr key={locus.locus_id}>
                  <td>
                    <strong>{locus.display_name}</strong>
                    <div className="table-subtle">
                      {locus.locus_id} · {locus.source}
                    </div>
                    <div className="table-subtle">{locus.disease}</div>
                  </td>
                  <td className="table-subtle">{locus.motif ?? '—'}</td>
                  <td className="table-subtle">{locus.call_count.toLocaleString()}</td>
                  <td>{locus.known_difference ?? <span className="table-subtle">—</span>}</td>
                  <td>
                    {locus.validated ? <strong>Validated</strong> : <span>Not validated</span>}
                    {locus.reason && <div className="table-subtle">{locus.reason}</div>}
                    {locus.updated_at && (
                      <div className="table-subtle">
                        {locus.updated_by ?? 'unknown'}, {when(locus.updated_at)}
                      </div>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button-ghost"
                      disabled={saveMutation.isPending}
                      onClick={() => {
                        setReason('');
                        setPending({ locus, validated: !locus.validated });
                      }}
                    >
                      {locus.validated ? 'Withdraw validation' : 'Record as validated'}
                    </button>
                  </td>
                </tr>
              ))}
              {loci.length === 0 && (
                <tr>
                  <td colSpan={6} className="table-subtle">
                    {withCallsOnly ? 'No stored call names a catalogue locus.' : 'The repeat catalogue is empty.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {pending && (
        <ModalDialog
          onClose={() => {
            setPending(null);
            setReason('');
          }}
          label="Confirm the counting-unit record"
          discardMessage="Discard the reason you have typed?"
          isDirty={() => reason.trim() !== ''}
        >
          {(requestClose) => (
            <>
              <h2 className="section-title">
                {pending.validated ? 'Record as validated' : 'Withdraw the validation'}:{' '}
                {pending.locus.display_name} ({pending.locus.locus_id})
              </h2>
              <p className="section-copy">
                {pending.validated
                  ? `TRGT's count at this locus is the clinical repeat number its ranges count. Counts near a boundary here will no longer be marked.`
                  : `TRGT's count at this locus is not, or no longer, known to be the clinical repeat number. Counts within ${margin} repeats of a boundary here will be marked again.`}
              </p>
              {pending.locus.known_difference && (
                <p className="report-disclaimer">Known: {pending.locus.known_difference}</p>
              )}
              <label className="form-field">
                <span>Reason</span>
                {/* Required: which check supports the record (the validation report, the TRGT
                    catalogue and version it covered). */}
                <textarea
                  rows={2}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="e.g. validation report, TRGT catalogue and version checked"
                />
              </label>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  className="form-button"
                  disabled={saveMutation.isPending || !reason.trim()}
                  onClick={() => {
                    const change = {
                      locusId: pending.locus.locus_id,
                      validated: pending.validated,
                      reason: reason.trim(),
                    };
                    setPending(null);
                    setReason('');
                    saveMutation.mutate(change);
                  }}
                >
                  Confirm
                </button>
                <button type="button" className="button-ghost" onClick={requestClose}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </ModalDialog>
      )}

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Change history</h2>
            <p className="family-workspace-card-subtitle">
              Every record, from the clinical audit trail (chain system:repeat-loci). Append-only: entries cannot be altered
              or removed.
            </p>
          </div>
        </div>
        {(data?.changes ?? []).length === 0 ? (
          <p className="dashboard-link-note">No locus has been recorded yet.</p>
        ) : (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Change</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {(data?.changes ?? []).map((change, index) => (
                  <tr key={`${change.changed_at}-${change.locus_id}-${index}`}>
                    <td className="table-subtle">{when(change.changed_at)}</td>
                    <td className="table-subtle">{change.changed_by ?? 'unknown'}</td>
                    <td>{change.summary}</td>
                    <td>{change.reason}</td>
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

export default AdminRepeatLociPage;
