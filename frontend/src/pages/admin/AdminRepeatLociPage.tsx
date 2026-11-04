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
import { describeCountOffset } from '../../lib/repeatStatus';
import type { StatusTone } from './dataManagementTypes';

const QUERY_KEY = ['admin', 'repeat-loci'];

const when = (value: string | null | undefined): string =>
  value ? `${value.replace('T', ' ').slice(0, 16)} UTC` : '';

// What the dialog was opened to do: record the locus as validated, withdraw that, or change the
// count correction alone. Each save states the whole record, so the dialog holds both.
type PendingMode = 'validate' | 'withdraw' | 'correct';

const DIALOG_TITLES: Record<PendingMode, string> = {
  validate: 'Record as validated',
  withdraw: 'Withdraw the validation',
  correct: 'Count correction',
};

// A typed correction as a whole number of repeats within the bound; null when it is not one.
const parseOffset = (value: string, limit: number): number | null => {
  const trimmed = value.trim();
  if (!/^[+-]?\d+$/.test(trimmed)) return null;
  const offset = Number(trimmed);
  return Number.isSafeInteger(offset) && Math.abs(offset) <= limit ? offset : null;
};

/**
 * The repeat catalogue's loci with QA's record of TRGT's count at each (owner's decisions,
 * 2026-10-14 and 2026-11-04): a count correction, and whether the count, so corrected, is the
 * clinical repeat number its ranges count.
 *
 * TRGT counts the motif across the region its own catalogue gives a locus: at FMR1 it leaves
 * out the AGG interruptions, at HTT it counts one CAG more, at POLG four flanking GCT copies.
 * A correction QA records here is added to TRGT's count before every call at the locus is
 * classified, validated or not, and TRGT's own count stays shown beside it. QA and the
 * laboratory director check each locus; until they record it here as validated, the repeat page
 * and the genome tracks mark a count near a boundary there. Nothing is filled in in advance, and
 * each change is a clinical audit event with the record before and after and its reason.
 */
const AdminRepeatLociPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [withCallsOnly, setWithCallsOnly] = useState(false);
  // The locus whose record is being changed, and to what: confirmed with a reason first.
  const [pending, setPending] = useState<{ locus: RepeatLocusCountUnitsOut; mode: PendingMode } | null>(null);
  const [reason, setReason] = useState('');
  // The correction as typed in the dialog, filled with the locus's current one.
  const [offsetText, setOffsetText] = useState('0');

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
    mutationFn: async (change: { locusId: string; validated: boolean; countOffset: number; reason: string }) =>
      (
        await api.put(apiPath`/admin/repeat-loci/${change.locusId}/count-units`, {
          validated: change.validated,
          count_offset: change.countOffset,
          reason: change.reason,
        })
      ).data,
    onSuccess: async (_record, change) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      // The repeat page and the tracks read the record with their calls.
      await queryClient.invalidateQueries({ queryKey: ['family'] });
      await queryClient.invalidateQueries({ queryKey: ['repeat-expansions'] });
      await queryClient.invalidateQueries({ queryKey: ['genome-repeat-expansions'] });
      const correction = describeCountOffset(change.countOffset);
      setStatus({
        tone: 'success',
        message: `Recorded TRGT's counting units at ${change.locusId} as ${
          change.validated ? 'validated' : 'not validated'
        }${correction ? `, with the count correction ${correction}` : ''}.`,
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
  const offsetLimit = data?.offset_limit ?? 0;
  const pendingOffset = parseOffset(offsetText, offsetLimit);
  // What the save records as validated. A validation covers the correction it was given with: a
  // changed correction alone is recorded as not validated, so its counts are marked again until QA
  // validates the locus with it.
  const pendingValidated =
    pending?.mode === 'validate' ||
    (pending?.mode === 'correct' && pending.locus.validated && pendingOffset === (pending.locus.count_offset ?? 0));
  const open = (locus: RepeatLocusCountUnitsOut, mode: PendingMode) => {
    setReason('');
    setOffsetText(String(locus.count_offset ?? 0));
    setPending({ locus, mode });
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <PageHeader
        kicker="Administration"
        title="Repeat catalogue: TRGT counting units"
        description={
          <>
            TRGT counts the motif across the region its own catalogue gives a locus, which need not be the clinical repeat
            number the locus&apos;s ranges count. QA and the laboratory director check each locus the laboratory&apos;s TRGT
            catalogue uses. A count correction recorded here is added to TRGT&apos;s count before every call at the locus is
            classified, validated or not; TRGT&apos;s own count stays shown beside it. Until a locus is recorded here as
            validated, the repeat page and the genome tracks mark a count within {margin} repeats of a boundary there as
            near a boundary; the mark changes no status. Record a locus as validated only when TRGT&apos;s count there, with
            its correction, is the clinical repeat number. No locus has a correction until one is recorded here.
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
                    {describeCountOffset(locus.count_offset) && (
                      <div>Correction: {describeCountOffset(locus.count_offset)}</div>
                    )}
                    {locus.reason && <div className="table-subtle">{locus.reason}</div>}
                    {locus.updated_at && (
                      <div className="table-subtle">
                        {locus.updated_by ?? 'unknown'}, {when(locus.updated_at)}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="compact-toolbar">
                      <button
                        type="button"
                        className="button-ghost"
                        disabled={saveMutation.isPending}
                        onClick={() => open(locus, locus.validated ? 'withdraw' : 'validate')}
                      >
                        {locus.validated ? 'Withdraw validation' : 'Record as validated'}
                      </button>
                      <button
                        type="button"
                        className="button-ghost"
                        disabled={saveMutation.isPending}
                        onClick={() => open(locus, 'correct')}
                      >
                        Set count correction
                      </button>
                    </div>
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
          isDirty={() => reason.trim() !== '' || offsetText.trim() !== String(pending.locus.count_offset ?? 0)}
        >
          {(requestClose) => (
            <>
              <h2 className="section-title">
                {DIALOG_TITLES[pending.mode]}: {pending.locus.display_name} ({pending.locus.locus_id})
              </h2>
              <p className="section-copy">
                {pending.mode === 'validate'
                  ? `TRGT's count at this locus, with the correction below, is the clinical repeat number its ranges count. Counts near a boundary here will no longer be marked.`
                  : pending.mode === 'withdraw'
                    ? `TRGT's count at this locus, with the correction below, is not, or no longer, known to be the clinical repeat number. Counts within ${margin} repeats of a boundary here will be marked again.`
                    : `The correction is added to TRGT's count before every call at this locus is classified, whether or not the locus is validated; TRGT's own count stays shown beside it. ${
                        pending.locus.validated
                          ? `The locus is validated with its current correction. A different correction is recorded as not validated: counts within ${margin} repeats of a boundary are marked again until QA records the locus as validated with it.`
                          : `Counts within ${margin} repeats of a boundary stay marked until the locus is validated.`
                      }`}
              </p>
              {pending.locus.known_difference && (
                <p className="report-disclaimer">Known: {pending.locus.known_difference}</p>
              )}
              <label className="form-field">
                <span>Count correction (repeats added to TRGT&apos;s count)</span>
                <input
                  type="number"
                  step={1}
                  min={-offsetLimit}
                  max={offsetLimit}
                  value={offsetText}
                  aria-invalid={pendingOffset === null}
                  onChange={(event) => setOffsetText(event.target.value)}
                />
                <span className="field-hint">
                  A whole number from −{offsetLimit} to {offsetLimit}: 0 for none, −1 turns TRGT&apos;s 18 into 17.
                </span>
              </label>
              {/* Where a constant correction is wrong (FMR1), said next to the field. */}
              {pending.locus.offset_note && <p className="report-disclaimer">{pending.locus.offset_note}</p>}
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
                  disabled={saveMutation.isPending || !reason.trim() || pendingOffset === null}
                  onClick={() => {
                    if (pendingOffset === null) return;
                    const change = {
                      locusId: pending.locus.locus_id,
                      validated: pendingValidated,
                      countOffset: pendingOffset,
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
