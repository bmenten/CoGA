import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { READ_ONLY_HINT } from '../../lib/familyAccess';
import ModalDialog from '../../components/ModalDialog';
import {
  failuresByJob,
  importIncompleteFromMetadata,
  importUnfinishedFromMetadata,
  pendingDatasets,
  unfinishedWriter,
  type FamilyImportUnfinished,
} from '../../components/ImportIncompleteBanner';
import { apiErrorMessage } from './reportSignoutQueries';

/** What a page that signs a family out holds of the sign-out (`useReportSignOut`). */
export interface ReportSignOut {
  /** Sign out with no gate acknowledged; a gate that refuses opens its dialog. */
  signOut: () => void;
  isPending: boolean;
  /** Why the family was not signed out, when it was refused outright or failed. */
  error: string | null;
  /** The gates' acknowledgement dialogs, where the page renders them. */
  dialogs: React.ReactNode;
}

/** What decides whether a page offers the sign-out (`signOutRefusal`). */
export interface SignOutOffer {
  /** The family is read-only for the signed-in user: a reader in its project (SEC-1). */
  readOnly: boolean;
  /** False off the validated assembly scope; null or undefined while it is not known. */
  assemblyValidated: boolean | null | undefined;
  signoutsFailed: boolean;
  signoutsPending: boolean;
}

/**
 * Why a page does not offer the sign-out, or null when it does; the button is disabled with
 * this as its reason. The live report and the NIPT page both read it, so a family is offered
 * the sign-out alike on both: never to a reader in its project, who signs nothing out (SEC-1;
 * the server refuses them with 403); never off the validated assembly scope, which the server
 * refuses anyway (#515); and not while the sign-out record is unknown, as the analysis may be
 * signed already (#605). ``subject`` names what is signed out: the report, or the NIPT
 * analysis.
 */
export const signOutRefusal = (offer: SignOutOffer, subject: 'report' | 'analysis'): string | null => {
  if (offer.readOnly) return READ_ONLY_HINT;
  if (offer.assemblyValidated === false) {
    return `Not validated for clinical use — this ${subject} cannot be signed out`;
  }
  if (offer.signoutsFailed) return 'The sign-out record could not be loaded';
  if (offer.signoutsPending) return 'The sign-out record is still loading';
  return null;
};

/**
 * The family sign-out (`POST /families/{family_id}/report/sign-out`) with its gates: the
 * live report signs a family out with it, and the NIPT page a monogenic NIPT family, so both
 * sign out through one flow, one record per analysis. A gate that refuses opens its dialog,
 * acknowledged with a reason that is frozen into the signed record; one that cannot be
 * acknowledged (the assembly scope, data being written) is said in `error`. ``onSigned`` is
 * given the new version. For a reader in the family's project (``readOnly``) it sends
 * nothing, whatever calls it: a reader signs nothing out (SEC-1).
 */
export const useReportSignOut = (
  familyId: string | undefined,
  onSigned: (version: number) => void,
  { readOnly }: { readOnly: boolean },
): ReportSignOut => {
  const queryClient = useQueryClient();

  // Override dialogs. Each gate is acknowledged with a reason that is frozen into the
  // signed record: evidence drift first, then a failing / unverifiable Sample QC, then an
  // incomplete import. Each dialog keeps the attempt its gate refused (`vars`), so the
  // acknowledgements already given travel on with the next one.
  type SignOutVars = {
    acknowledgeDrift: boolean;
    driftReason?: string;
    acknowledgeQc?: boolean;
    qcReason?: string;
    acknowledgeImportIncomplete?: boolean;
    importReason?: string;
  };
  const [driftGate, setDriftGate] = useState<{ message: string; vars: SignOutVars } | null>(
    null,
  );
  const [driftReason, setDriftReason] = useState('');
  const [qcGate, setQcGate] = useState<{
    message: string;
    vars: SignOutVars;
    summary?: { overall_status?: string; messages?: string[] };
  } | null>(null);
  const [qcReason, setQcReason] = useState('');
  const [importGate, setImportGate] = useState<{
    message: string;
    vars: SignOutVars;
    failed: string[];
    imported: string[];
    jobId: string | null;
    failedByJob: [string, string[]][] | null;
    unfinished: FamilyImportUnfinished[];
  } | null>(null);
  const [importReason, setImportReason] = useState('');
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const closeGates = () => {
    setDriftGate(null);
    setQcGate(null);
    setImportGate(null);
  };

  const signOut = useMutation({
    mutationFn: async (vars: SignOutVars) =>
      (
        await api.post(apiPath`/families/${familyId}/report/sign-out`, {
          acknowledge_drift: vars.acknowledgeDrift,
          drift_acknowledgement_reason: vars.driftReason,
          acknowledge_qc: vars.acknowledgeQc ?? false,
          qc_acknowledgement_reason: vars.qcReason,
          acknowledge_import_incomplete: vars.acknowledgeImportIncomplete ?? false,
          import_incomplete_acknowledgement_reason: vars.importReason,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'report-signouts'] });
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'report-signout-check'] });
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
    },
  });

  const attemptSignOut = async (vars: SignOutVars) => {
    setSignOutError(null);
    try {
      const signed = (await signOut.mutateAsync(vars)) as { version?: unknown } | undefined;
      closeGates();
      setDriftReason('');
      setQcReason('');
      setImportReason('');
      // Show the signer what was signed: the new version, rendered from its record, with the
      // report code the EPD report quotes (the signed view confirms it once).
      if (typeof signed?.version === 'number') {
        onSigned(signed.version);
      }
    } catch (error) {
      const response = (
        error as { response?: { status?: number; data?: { detail?: unknown } } }
      ).response;
      if (response?.status !== 409) {
        // Anything but a gate is a failure the reviewer must see: the report was NOT
        // signed out.
        closeGates();
        setSignOutError(apiErrorMessage(error, 'The report could not be signed out'));
        return;
      }
      const detail = response.data?.detail;
      const gate =
        detail && typeof detail === 'object' ? (detail as { gate?: string }).gate : undefined;
      closeGates();
      // Off the validated assembly scope: a refusal with no override, never the drift
      // dialog that a plain 409 would open (#515).
      if (gate === 'assembly_scope') {
        setSignOutError(
          (detail as { message?: string }).message ||
            'This family is outside the validated assembly scope.',
        );
        return;
      }
      // The family's data is being written: an import queued or running, or another write
      // of its variants. No override either; the reviewer signs out once it has finished.
      if (gate === 'import_in_progress' || gate === 'variant_writes_in_progress') {
        setSignOutError(
          (detail as { message?: string }).message ||
            'This family’s data is being written. Sign out once that has finished.',
        );
        return;
      }
      // A failing Sample QC returns a structured detail (gate discriminator + a failure
      // summary); it needs an acknowledge-WITH-REASON override, so open the dialog.
      if (gate === 'sample_qc') {
        const qc = detail as {
          message?: string;
          qc_summary?: { overall_status?: string; messages?: string[] };
        };
        setQcGate({
          message: qc.message || 'Sample-integrity QC failed.',
          summary: qc.qc_summary,
          vars,
        });
        return;
      }
      // An import that partly failed: structured like the QC gate, naming what the import
      // left out, and acknowledged the same way.
      if (gate === 'import_incomplete') {
        const incomplete = detail as {
          message?: string;
          import_incomplete?: {
            failed_datasets?: string[];
            imported_datasets?: string[];
            job_id?: string | null;
            failed_jobs?: Record<string, string | null>;
          } | null;
        };
        setImportGate({
          message: incomplete.message || 'The family’s import is incomplete.',
          failed: incomplete.import_incomplete?.failed_datasets ?? [],
          imported: incomplete.import_incomplete?.imported_datasets ?? [],
          jobId: incomplete.import_incomplete?.job_id ?? null,
          // The jobs that hold the failed datasets' errors, when more than one does.
          failedByJob: failuresByJob(
            importIncompleteFromMetadata(detail) ?? {
              at: null,
              failedDatasets: [],
              importedDatasets: [],
              jobId: null,
              failedJobs: {},
            },
          ),
          // The imports that began writing the family and did not finish (detail.import_unfinished).
          unfinished: importUnfinishedFromMetadata(detail),
          vars,
        });
        return;
      }
      // A gate this page does not know is a refusal, never the drift override below.
      if (gate !== undefined) {
        setSignOutError(
          (detail as { message?: string }).message || 'The report could not be signed out.',
        );
        return;
      }
      // Evidence-drift gate (plain string detail): acknowledge WITH a reason, like QC.
      setDriftGate({
        message:
          typeof detail === 'string'
            ? detail
            : 'Evidence has changed since some classifications were made.',
        vars,
      });
    }
  };

  const handleSignOut = () => {
    if (readOnly) {
      return;
    }
    void attemptSignOut({
      acknowledgeDrift: false,
      acknowledgeQc: false,
      acknowledgeImportIncomplete: false,
    });
  };

  const submitDriftAcknowledgement = async () => {
    const reason = driftReason.trim();
    if (!reason || !driftGate) {
      return;
    }
    await attemptSignOut({ ...driftGate.vars, acknowledgeDrift: true, driftReason: reason });
  };

  const submitQcAcknowledgement = async () => {
    const reason = qcReason.trim();
    if (!reason || !qcGate) {
      return;
    }
    await attemptSignOut({ ...qcGate.vars, acknowledgeQc: true, qcReason: reason });
  };

  const submitImportAcknowledgement = async () => {
    const reason = importReason.trim();
    if (!reason || !importGate) {
      return;
    }
    await attemptSignOut({
      ...importGate.vars,
      acknowledgeImportIncomplete: true,
      importReason: reason,
    });
  };

  return {
    signOut: handleSignOut,
    isPending: signOut.isPending,
    error: signOutError,
    dialogs: (
      <>
        {driftGate ? (
          <ModalDialog
            label="Evidence drift acknowledgement required"
            className="modal-surface surface-card report-qc-ack-modal"
            closeOnBackdrop={false}
            discardMessage="Discard the reason you have typed?"
            onClose={() => {
              setDriftGate(null);
              setDriftReason('');
            }}
          >
              <h2 className="report-paragraph">
                <strong>Evidence drift — acknowledgement required</strong>
              </h2>
              <p className="report-paragraph">{driftGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              <label className="report-footer-label" htmlFor="drift-ack-reason">
                Reason for signing out despite the evidence drift (required)
              </label>
              <textarea
                id="drift-ack-reason"
                className="variant-review-textarea"
                rows={3}
                value={driftReason}
                onChange={(event) => setDriftReason(event.target.value)}
              />
              <div className="inline-actions modal-actions">
                <button
                  type="button"
                  className="form-button"
                  onClick={() => {
                    setDriftGate(null);
                    setDriftReason('');
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="form-button"
                  disabled={!driftReason.trim() || signOut.isPending}
                  onClick={submitDriftAcknowledgement}
                >
                  Sign out anyway
                </button>
              </div>
          </ModalDialog>
        ) : null}

        {qcGate ? (
          <ModalDialog
            label="Sample-integrity QC acknowledgement required"
            className="modal-surface surface-card report-qc-ack-modal"
            closeOnBackdrop={false}
            discardMessage="Discard the reason you have typed?"
            onClose={() => {
              setQcGate(null);
              setQcReason('');
            }}
          >
              <h2 className="report-paragraph">
                <strong>Sample-integrity QC — acknowledgement required</strong>
              </h2>
              {/* The backend message describes the specific concern — a detected mismatch
                  (fail) OR an asserted relationship that could not be verified (missing
                  data). Render it verbatim so the reviewer attests to the right thing. */}
              <p className="report-paragraph">{qcGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              {qcGate.summary?.messages?.length ? (
                <ul>
                  {qcGate.summary.messages.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              ) : null}
              <label className="report-footer-label" htmlFor="qc-ack-reason">
                Reason for signing out despite the QC concern (required)
              </label>
              <textarea
                id="qc-ack-reason"
                className="variant-review-textarea"
                rows={3}
                value={qcReason}
                onChange={(event) => setQcReason(event.target.value)}
              />
              <div className="inline-actions modal-actions">
                <button
                  type="button"
                  className="form-button"
                  onClick={() => {
                    setQcGate(null);
                    setQcReason('');
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="form-button"
                  disabled={!qcReason.trim() || signOut.isPending}
                  onClick={submitQcAcknowledgement}
                >
                  Sign out anyway
                </button>
              </div>
          </ModalDialog>
        ) : null}

        {importGate ? (
          <ModalDialog
            label="Incomplete import acknowledgement required"
            className="modal-surface surface-card report-qc-ack-modal"
            closeOnBackdrop={false}
            discardMessage="Discard the reason you have typed?"
            onClose={() => {
              setImportGate(null);
              setImportReason('');
            }}
          >
              <h2 className="report-paragraph">
                <strong>Incomplete import — acknowledgement required</strong>
              </h2>
              {/* The backend message names what the import left out. Render it verbatim so
                  the signer attests to the right thing. */}
              <p className="report-paragraph">{importGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              {importGate.failed.length ||
              importGate.imported.length ||
              importGate.jobId ||
              importGate.unfinished.length ? (
                <ul>
                  {importGate.failed.length ? (
                    <li>Failed to import: {importGate.failed.join(', ')}</li>
                  ) : null}
                  {importGate.imported.length ? (
                    <li>Imported: {importGate.imported.join(', ')}</li>
                  ) : null}
                  {/* Its record holds each dataset's error; an earlier failure kept with a
                      later one is in its own job. */}
                  {importGate.failedByJob ? (
                    importGate.failedByJob.map(([job, names]) => (
                      <li key={job}>
                        Import job {job}: {names.join(', ')}
                      </li>
                    ))
                  ) : importGate.jobId ? (
                    <li>Import job: {importGate.jobId}</li>
                  ) : null}
                  {/* An import that stopped part-way: its record shows how far it got. A
                      single-file upload's or a deletion's mark has no job: its writer is named. */}
                  {importGate.unfinished.map((entry) => (
                    <li key={entry.key}>
                      Not finished
                      {unfinishedWriter(entry.key) === 'upload'
                        ? ' (single-file upload)'
                        : unfinishedWriter(entry.key) === 'deletion'
                          ? ' (deletion of a sample’s data)'
                          : entry.jobId
                            ? ` (import job ${entry.jobId})`
                            : ''}
                      :{' '}
                      {pendingDatasets(entry).length
                        ? `${pendingDatasets(entry).join(', ')} may be partly written or missing`
                        : entry.datasets.length
                          ? 'it had finished each of its datasets'
                          : 'what it was importing was not recorded'}
                    </li>
                  ))}
                </ul>
              ) : null}
              <label className="report-footer-label" htmlFor="import-ack-reason">
                Reason for signing out despite the incomplete import (required)
              </label>
              <textarea
                id="import-ack-reason"
                className="variant-review-textarea"
                rows={3}
                value={importReason}
                onChange={(event) => setImportReason(event.target.value)}
              />
              <div className="inline-actions modal-actions">
                <button
                  type="button"
                  className="form-button"
                  onClick={() => {
                    setImportGate(null);
                    setImportReason('');
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="form-button"
                  disabled={!importReason.trim() || signOut.isPending}
                  onClick={submitImportAcknowledgement}
                >
                  Sign out anyway
                </button>
              </div>
          </ModalDialog>
        ) : null}
      </>
    ),
  };
};
