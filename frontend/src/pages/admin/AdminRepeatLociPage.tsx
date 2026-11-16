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
import { strchiveLocusHref, strchiveReleaseHref } from '../../lib/strchive';
import type { StatusTone } from './dataManagementTypes';

const QUERY_KEY = ['admin', 'repeat-loci'];

const when = (value: string | null | undefined): string =>
  value ? `${value.replace('T', ' ').slice(0, 16)} UTC` : '';

const countRange = (min: number, max: number): string => (min === max ? `${min}` : `${min}–${max}`);

/**
 * A locus's cut-offs in words, as the STRchive release states them: "normal 6–26 · grey zone ≥ 27
 * · pathogenic ≥ 36"; at a locus pathogenic by contraction, "normal 4 · pathogenic 3"; at a locus
 * without a threshold, its benign range or "no cut-off".
 */
export const describeCutoffs = (locus: RepeatLocusCountUnitsOut): string => {
  const { benign_min: benignMin, benign_max: benignMax, pathogenic_min: pathogenicMin } = locus;
  const normal = benignMin != null && benignMax != null ? `normal ${countRange(benignMin, benignMax)}` : null;
  if (normal && pathogenicMin != null && benignMax != null && benignMax >= pathogenicMin) {
    const pathogenicMax = locus.pathogenic_max;
    return `${normal} · pathogenic ${
      pathogenicMax != null ? countRange(pathogenicMin, pathogenicMax) : pathogenicMin
    }`;
  }
  const thresholds = [
    locus.warning_min != null ? `grey zone ≥ ${locus.warning_min}` : null,
    locus.premutation_min != null ? `premutation ≥ ${locus.premutation_min}` : null,
    pathogenicMin != null ? `pathogenic ≥ ${pathogenicMin}` : null,
  ].filter((part): part is string => part !== null);
  if (!thresholds.length) return normal ? `${normal} · no cut-off` : 'no cut-off';
  return [normal, ...thresholds].filter((part): part is string => part !== null).join(' · ');
};

// A typed correction as a whole number of repeats within the bound; null when it is not one.
const parseOffset = (value: string, limit: number): number | null => {
  const trimmed = value.trim();
  if (!/^[+-]?\d+$/.test(trimmed)) return null;
  const offset = Number(trimmed);
  return Number.isSafeInteger(offset) && Math.abs(offset) <= limit ? offset : null;
};

/**
 * The repeat catalogue's loci: each locus's cut-offs, as the pinned STRchive release states them
 * (not validated per locus: the owner's decision, 2026-11-16), and QA's count correction there
 * (the owner's decision, 2026-11-04).
 *
 * TRGT counts the motif across the region its own catalogue gives a locus: at FMR1 it leaves
 * out the AGG interruptions, at HTT it counts one CAG more, at POLG four flanking GCT copies.
 * A correction QA records here is added to TRGT's count before every call at the locus is
 * classified, and TRGT's own count stays shown beside it. At every locus the repeat page and the
 * genome tracks mark a count near a cut-off. Nothing is filled in in advance, and each change is
 * a clinical audit event with the correction before and after and its reason.
 */
const AdminRepeatLociPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [withCallsOnly, setWithCallsOnly] = useState(false);
  // The locus whose correction is being changed: confirmed with a reason first.
  const [pending, setPending] = useState<RepeatLocusCountUnitsOut | null>(null);
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

  const saveMutation = useMutation({
    mutationFn: async (change: { locusId: string; countOffset: number; reason: string }) =>
      (
        await api.put(apiPath`/admin/repeat-loci/${change.locusId}/count-units`, {
          count_offset: change.countOffset,
          reason: change.reason,
        })
      ).data,
    onSuccess: async (_record, change) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      // The repeat page and the tracks read the correction with their calls.
      await queryClient.invalidateQueries({ queryKey: ['family'] });
      await queryClient.invalidateQueries({ queryKey: ['repeat-expansions'] });
      await queryClient.invalidateQueries({ queryKey: ['genome-repeat-expansions'] });
      setStatus({
        tone: 'success',
        message: `Recorded the count correction at ${change.locusId}: ${
          describeCountOffset(change.countOffset) ?? 'no correction'
        }.`,
      });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not save the count correction.') }),
  });

  if (isLoading) {
    return (
      <PageState
        loading
        kicker="Admin"
        title="Loading the repeat catalogue"
        message="Fetching the repeat loci, their cut-offs and count corrections."
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
  const release = data?.strchive_release ?? '';
  const open = (locus: RepeatLocusCountUnitsOut) => {
    setReason('');
    setOffsetText(String(locus.count_offset ?? 0));
    setPending(locus);
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <PageHeader
        kicker="Administration"
        title="Repeat catalogue"
        description={
          <>
            The cut-offs are STRchive&apos;s, as the release states them. TRGT counts the motif across the region its own
            catalogue gives a locus, which need not be the clinical repeat number the cut-offs count, so the repeat page
            and the genome tracks mark every count within {margin} repeats of a cut-off as near a boundary; the mark
            changes no status. A count correction recorded here is added to TRGT&apos;s count before every call at the
            locus is classified; TRGT&apos;s own count stays shown beside it. No locus has a correction until one is
            recorded here.
          </>
        }
      >
        <p className="dashboard-link-note">
          Cut-offs from{' '}
          <a className="table-link" href={strchiveReleaseHref(release)} target="_blank" rel="noreferrer">
            STRchive {release}
          </a>
          , {(data?.loci ?? []).length} loci. Each count correction is kept in the clinical audit trail with its reason.
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
                <th>Cut-offs</th>
                <th>Motif</th>
                <th>Calls</th>
                <th>Known about TRGT&apos;s count</th>
                <th>Count correction</th>
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
                    <div className="table-subtle">
                      {locus.strchive_id ? (
                        <a
                          className="table-link"
                          href={strchiveLocusHref(locus.strchive_id)}
                          target="_blank"
                          rel="noreferrer"
                          title={`Open ${locus.strchive_id} on STRchive`}
                        >
                          {locus.disease}
                        </a>
                      ) : (
                        locus.disease
                      )}
                    </div>
                  </td>
                  <td>{describeCutoffs(locus)}</td>
                  <td className="table-subtle">{locus.motif ?? '—'}</td>
                  <td className="table-subtle">{locus.call_count.toLocaleString()}</td>
                  <td>{locus.known_difference ?? <span className="table-subtle">—</span>}</td>
                  <td>
                    <span>{describeCountOffset(locus.count_offset) ?? 'None'}</span>
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
                      onClick={() => open(locus)}
                    >
                      Set count correction
                    </button>
                  </td>
                </tr>
              ))}
              {loci.length === 0 && (
                <tr>
                  <td colSpan={7} className="table-subtle">
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
          label="Confirm the count correction"
          discardMessage="Discard the reason you have typed?"
          isDirty={() => reason.trim() !== '' || offsetText.trim() !== String(pending.count_offset ?? 0)}
        >
          {(requestClose) => (
            <>
              <h2 className="section-title">
                Count correction: {pending.display_name} ({pending.locus_id})
              </h2>
              <p className="section-copy">
                {`The correction is added to TRGT's count before every call at this locus is classified; TRGT's own count stays shown beside it. Counts within ${margin} repeats of a cut-off stay marked.`}
              </p>
              {pending.known_difference && <p className="report-disclaimer">Known: {pending.known_difference}</p>}
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
              {pending.offset_note && <p className="report-disclaimer">{pending.offset_note}</p>}
              <label className="form-field">
                <span>Reason</span>
                {/* Required: which check supports the correction (the report, the TRGT catalogue
                    and version it covered). */}
                <textarea
                  rows={2}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="e.g. report, TRGT catalogue and version checked"
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
                      locusId: pending.locus_id,
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
              Every count correction, from the clinical audit trail (chain system:repeat-loci). Append-only: entries cannot
              be altered or removed.
            </p>
          </div>
        </div>
        {(data?.changes ?? []).length === 0 ? (
          <p className="dashboard-link-note">No count correction has been recorded yet.</p>
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
