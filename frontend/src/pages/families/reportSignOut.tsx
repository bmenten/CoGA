import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ReportSignoutRequest } from '../../lib/apiSchema.generated';
import { READ_ONLY_HINT } from '../../lib/familyAccess';
import ModalDialog from '../../components/ModalDialog';
import {
  failuresByJob,
  importIncompleteFromMetadata,
  importUnfinishedFromMetadata,
  pendingDatasets,
  unfinishedWriter,
  type FamilyImportIncomplete,
  type FamilyImportUnfinished,
} from '../../components/ImportIncompleteBanner';
import { apiErrorMessage } from './reportSignoutQueries';


import {
  FINDING_DRIFT_WORDS,
  FINDING_NOUN,
  describeSignedDrift,
  describeSignedStructuralDrift,
  describeUnreviewedFinding,
  parseDrift,
  parseFindingDrift,
  parseUnreviewedFindings,
  type FindingDriftItem,
  type UnreviewedFinding,
  parseStructuralDrift,
  type SignedDriftItem,
  type SignedStructuralDriftItem,
} from './signedReportRecord';

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

/**
 * The concerns a Sample-QC refusal names, each group as the 409 lists it (CLIN-58): the checks
 * that failed, those that could not run, a NIPT family's NIPT quality checks that failed, and
 * the warnings beside them. Each concern names its sample, pair or child (CLIN-8).
 */
interface QcConcerns {
  failed: string[];
  unverifiable: string[];
  niptFailed: string[];
  warnings: string[];
}

/** The dialog's groups, in its order, each under its heading; an empty group is not shown. */
const QC_CONCERN_GROUPS: ReadonlyArray<{ key: keyof QcConcerns; id: string; heading: string }> = [
  { key: 'failed', id: 'qc-concerns-failed', heading: 'Sample QC checks that failed' },
  { key: 'unverifiable', id: 'qc-concerns-could-not-run', heading: 'Checks that could not run' },
  { key: 'niptFailed', id: 'qc-concerns-nipt-failed', heading: 'NIPT quality checks that failed' },
  {
    key: 'warnings',
    id: 'qc-concerns-warnings',
    heading: 'Warnings, which do not stop the sign-out on their own',
  },
];

const stringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/**
 * What a gate's refusal says of the concerns it lists (TRACE-19): their fingerprint, which the
 * dialog sends back with the acknowledgement, so it covers the concerns the dialog showed and
 * no others; and whether the concerns changed after an earlier dialog showed them, in which case
 * the refusal lists them as they are now for a fresh acknowledgement.
 */
const shownConcerns = (detail: unknown): { fingerprint: string | undefined; changed: boolean } => {
  const refusal = detail as { concerns_fingerprint?: unknown; concerns_changed?: unknown };
  return {
    fingerprint:
      typeof refusal.concerns_fingerprint === 'string' ? refusal.concerns_fingerprint : undefined,
    changed: refusal.concerns_changed === true,
  };
};

/** Said in a dialog the refusal reopened because its concerns changed after it showed them. */
const CONCERNS_CHANGED =
  'Nothing was signed out: the concerns changed after this dialog listed them. They are listed again as they are now. Read them, and give your reason again.';

const ConcernsChanged: React.FC<{ changed: boolean }> = ({ changed }) =>
  changed ? (
    <p className="report-signed-warning" role="alert">
      {CONCERNS_CHANGED}
    </p>
  ) : null;

/** A flag naming nothing: what an import refusal whose detail holds no flag reads as. */
const NO_FLAG: FamilyImportIncomplete = {
  at: null,
  failedDatasets: [],
  importedDatasets: [],
  jobId: null,
  failedJobs: {},
  withoutJob: [],
};

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
 * this as its reason. The live report and the NIPT report both read it, so a family is offered
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
 * live report signs a family out with it, and the NIPT report a monogenic NIPT family, so both
 * sign out through one flow, one record per analysis. A gate that refuses opens its dialog,
 * which lists the gate's concerns and is acknowledged with a reason that is frozen into the
 * signed record and with the fingerprint of the concerns it listed, so the acknowledgement
 * covers what the dialog showed (TRACE-19); one that cannot be acknowledged (the assembly
 * scope, data being written) is said in `error`. ``onSigned`` is
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
  // acknowledgements already given travel on with the next one. Each acknowledgement carries
  // the fingerprint of the concerns its dialog showed (`fingerprint`, TRACE-19): it covers
  // those, and a sign-out whose concerns are no longer those is refused with them as they are
  // now (`changed`), which reopens the dialog for a fresh acknowledgement.
  type SignOutVars = {
    acknowledgeDrift: boolean;
    driftReason?: string;
    driftFingerprint?: string;
    acknowledgeQc?: boolean;
    qcReason?: string;
    qcFingerprint?: string;
    acknowledgeImportIncomplete?: boolean;
    importReason?: string;
    importFingerprint?: string;
    acknowledgeUnreviewed?: boolean;
    unreviewedReason?: string;
    unreviewedFingerprint?: string;
  };
  type ShownConcerns = { vars: SignOutVars; fingerprint: string | undefined; changed: boolean };
  const [driftGate, setDriftGate] = useState<
    | (ShownConcerns & {
        message: string;
        drifted: SignedDriftItem[];
        structuralDrifted: SignedStructuralDriftItem[];
        findingDrifted: FindingDriftItem[];
      })
    | null
  >(null);
  const [driftReason, setDriftReason] = useState('');
  const [qcGate, setQcGate] = useState<(ShownConcerns & {
    message: string;
    concerns: QcConcerns;
  }) | null>(null);
  const [qcReason, setQcReason] = useState('');
  const [importGate, setImportGate] = useState<(ShownConcerns & {
    message: string;
    failed: string[];
    imported: string[];
    jobId: string | null;
    failedByJob: [string, string[]][] | null;
    /** Failed datasets no import job records an error of, beside others a job does. */
    withoutJob: string[];
    unfinished: FamilyImportUnfinished[];
  }) | null>(null);
  const [importReason, setImportReason] = useState('');
  const [unreviewedGate, setUnreviewedGate] = useState<(ShownConcerns & {
    message: string;
    findings: UnreviewedFinding[];
  }) | null>(null);
  const [unreviewedReason, setUnreviewedReason] = useState('');
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const closeGates = () => {
    setDriftGate(null);
    setQcGate(null);
    setImportGate(null);
    setUnreviewedGate(null);
  };

  const signOut = useMutation({
    mutationFn: async (vars: SignOutVars) => {
      // The API's own request model: a field it renames fails the type check here.
      const body: ReportSignoutRequest = {
        acknowledge_drift: vars.acknowledgeDrift,
        drift_acknowledgement_reason: vars.driftReason,
        drift_concerns_fingerprint: vars.driftFingerprint,
        acknowledge_qc: vars.acknowledgeQc ?? false,
        qc_acknowledgement_reason: vars.qcReason,
        qc_concerns_fingerprint: vars.qcFingerprint,
        acknowledge_import_incomplete: vars.acknowledgeImportIncomplete ?? false,
        import_incomplete_acknowledgement_reason: vars.importReason,
        import_incomplete_concerns_fingerprint: vars.importFingerprint,
        acknowledge_unreviewed_findings: vars.acknowledgeUnreviewed ?? false,
        unreviewed_findings_acknowledgement_reason: vars.unreviewedReason,
        unreviewed_findings_concerns_fingerprint: vars.unreviewedFingerprint,
      };
      return (await api.post(apiPath`/families/${familyId}/report/sign-out`, body)).data;
    },
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
      setUnreviewedReason('');
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
      // Evidence drift returns the classifications it counts, as the signed record freezes
      // them; it needs an acknowledge-WITH-REASON override, so open the dialog, which lists
      // them (TRACE-19: before, it gave their number only).
      if (gate === 'evidence_drift') {
        const drift = detail as {
          message?: string;
          drifted?: unknown;
          structural_drifted?: unknown;
          finding_drifted?: unknown;
        };
        const shown = shownConcerns(detail);
        // Concerns that changed after the dialog showed them are acknowledged afresh: the
        // reason written for the earlier ones is not carried over to them.
        if (shown.changed) setDriftReason('');
        setDriftGate({
          message: drift.message || 'Evidence has changed since some classifications were made.',
          drifted: parseDrift({ drifted: drift.drifted })?.drifted ?? [],
          structuralDrifted: parseStructuralDrift({ drifted: drift.structural_drifted })?.drifted ?? [],
          findingDrifted: parseFindingDrift(drift.finding_drifted),
          ...shown,
          vars,
        });
        return;
      }
      // A failing Sample QC returns a structured detail (gate discriminator + its concerns,
      // in groups); it needs an acknowledge-WITH-REASON override, so open the dialog. The
      // dialog shows every group, so a failure never hides a check that could not run.
      if (gate === 'sample_qc') {
        const qc = detail as {
          message?: string;
          failed_checks?: unknown;
          unverifiable_checks?: unknown;
          nipt_failed_checks?: unknown;
          warning_checks?: unknown;
        };
        const shown = shownConcerns(detail);
        if (shown.changed) setQcReason('');
        setQcGate({
          message: qc.message || 'Sample-integrity QC failed.',
          concerns: {
            failed: stringList(qc.failed_checks),
            unverifiable: stringList(qc.unverifiable_checks),
            niptFailed: stringList(qc.nipt_failed_checks),
            warnings: stringList(qc.warning_checks),
          },
          ...shown,
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
        const read = importIncompleteFromMetadata(detail);
        const shown = shownConcerns(detail);
        if (shown.changed) setImportReason('');
        setImportGate({
          message: incomplete.message || 'The family’s import is incomplete.',
          failed: incomplete.import_incomplete?.failed_datasets ?? [],
          imported: incomplete.import_incomplete?.imported_datasets ?? [],
          jobId: incomplete.import_incomplete?.job_id ?? null,
          // The jobs that hold the failed datasets' errors, when more than one does, or when
          // a failure no job records is beside them (failed_jobs lists it with none).
          failedByJob: failuresByJob(read ?? NO_FLAG),
          withoutJob: read?.withoutJob ?? [],
          // The imports that began writing the family and did not finish (detail.import_unfinished).
          unfinished: importUnfinishedFromMetadata(detail),
          ...shown,
          vars,
        });
        return;
      }
      // An abnormal repeat or Paraphase call without a review (REQ-TRACE-016): listed, and
      // acknowledged with a reason like the gates above.
      if (gate === 'unreviewed_findings') {
        const unreviewed = detail as { message?: string; unreviewed_findings?: unknown };
        const shown = shownConcerns(detail);
        if (shown.changed) setUnreviewedReason('');
        setUnreviewedGate({
          message: unreviewed.message || 'Some repeat or Paraphase findings have no review.',
          findings: parseUnreviewedFindings(unreviewed.unreviewed_findings),
          ...shown,
          vars,
        });
        return;
      }
      // A refusal this page does not know (a gate it was not built for, or none named) is a
      // refusal, never an override: no dialog opens without the concerns it acknowledges.
      setSignOutError(
        (typeof detail === 'string' ? detail : (detail as { message?: string } | null | undefined)?.message) ||
          'The report could not be signed out.',
      );
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
      acknowledgeUnreviewed: false,
    });
  };

  const submitDriftAcknowledgement = async () => {
    const reason = driftReason.trim();
    if (!reason || !driftGate) {
      return;
    }
    await attemptSignOut({
      ...driftGate.vars,
      acknowledgeDrift: true,
      driftReason: reason,
      driftFingerprint: driftGate.fingerprint,
    });
  };

  const submitQcAcknowledgement = async () => {
    const reason = qcReason.trim();
    if (!reason || !qcGate) {
      return;
    }
    await attemptSignOut({
      ...qcGate.vars,
      acknowledgeQc: true,
      qcReason: reason,
      qcFingerprint: qcGate.fingerprint,
    });
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
      importFingerprint: importGate.fingerprint,
    });
  };

  const submitUnreviewedAcknowledgement = async () => {
    const reason = unreviewedReason.trim();
    if (!reason || !unreviewedGate) {
      return;
    }
    await attemptSignOut({
      ...unreviewedGate.vars,
      acknowledgeUnreviewed: true,
      unreviewedReason: reason,
      unreviewedFingerprint: unreviewedGate.fingerprint,
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
              <ConcernsChanged changed={driftGate.changed} />
              <p className="report-paragraph">{driftGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              {/* Each classification the gate counts, as the signed version will list it: the
                  acknowledgement covers these (TRACE-19). */}
              {driftGate.drifted.length || driftGate.structuralDrifted.length || driftGate.findingDrifted.length ? (
                <>
                  <h3 className="report-subheading" id="drift-concerns">
                    Classifications whose evidence changed or cannot be verified
                  </h3>
                  <ul className="report-drift-list" aria-labelledby="drift-concerns">
                    {driftGate.drifted.map((item, index) => (
                      <li key={`${item.variantId}-${index}`}>
                        <strong>{item.variantId}</strong> — {describeSignedDrift(item)}
                      </li>
                    ))}
                    {driftGate.structuralDrifted.map((item, index) => (
                      <li key={`sv:${item.variantId}-${index}`}>
                        <strong>{item.variantId}</strong> (structural variant) —{' '}
                        {describeSignedStructuralDrift(item)}
                      </li>
                    ))}
                    {driftGate.findingDrifted.map((item) => (
                      <li key={`${item.kind}:${item.key}`}>
                        <strong>{item.key}</strong> ({FINDING_NOUN[item.kind] ?? item.kind} review) —{' '}
                        {FINDING_DRIFT_WORDS[item.status] ?? item.status}
                        {item.changes.length ? `: ${item.changes.join('; ')}` : ''}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
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
              <ConcernsChanged changed={qcGate.changed} />
              {/* The backend message names each kind of concern the gate holds — a detected
                  mismatch (fail), a check that could not run (missing data), a failed NIPT
                  check. Render it verbatim so the reviewer attests to the right thing. */}
              <p className="report-paragraph">{qcGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              {/* Every concern, each group under its heading, as the 409 lists them and the
                  sign-out's audit event records them (CLIN-58). */}
              {QC_CONCERN_GROUPS.map(({ key, id, heading }) =>
                qcGate.concerns[key].length ? (
                  <React.Fragment key={key}>
                    <h3 className="report-subheading" id={id}>
                      {heading}
                    </h3>
                    <ul aria-labelledby={id}>
                      {qcGate.concerns[key].map((concern, index) => (
                        <li key={index}>{concern}</li>
                      ))}
                    </ul>
                  </React.Fragment>
                ) : null,
              )}
              <label className="report-footer-label" htmlFor="qc-ack-reason">
                Reason for signing out despite the QC concerns listed (required)
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
              <ConcernsChanged changed={importGate.changed} />
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
                  {/* A single-file upload's failure, or an import's run outside a job: no
                      job records its error, whatever the flag's own job (SAFE-18). */}
                  {importGate.withoutJob.length ? (
                    <li>No import job: {importGate.withoutJob.join(', ')}</li>
                  ) : null}
                  {/* An import that stopped part-way: its record shows how far it got. A
                      single-file upload's or a deletion's mark has no job: its writer is named. */}
                  {importGate.unfinished.map((entry) => (
                    <li key={entry.key}>
                      {/* A failed overwrite whose restore did not finish, not an import that
                          stopped: the refusal freezes it as such (SAFE-18). */}
                      {entry.wholeFamily ? 'Restore not finished' : 'Not finished'}
                      {unfinishedWriter(entry.key) === 'upload'
                        ? ' (single-file upload)'
                        : unfinishedWriter(entry.key) === 'deletion'
                          ? ' (deletion of this family’s data)'
                          : entry.jobId
                            ? ` (import job ${entry.jobId})`
                            : ''}
                      :{' '}
                      {pendingDatasets(entry).length
                        ? `${pendingDatasets(entry).join(', ')} may be partly written or missing${
                            entry.wholeFamily ? ', whichever import or upload wrote them' : ''
                          }`
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
        {unreviewedGate ? (
          <ModalDialog
            label="Findings without a review: acknowledgement required"
            className="modal-surface surface-card report-qc-ack-modal"
            closeOnBackdrop={false}
            discardMessage="Discard the reason you have typed?"
            onClose={() => {
              setUnreviewedGate(null);
              setUnreviewedReason('');
            }}
          >
              <h2 className="report-paragraph">
                <strong>Findings without a review — acknowledgement required</strong>
              </h2>
              <ConcernsChanged changed={unreviewedGate.changed} />
              <p className="report-paragraph">{unreviewedGate.message}</p>
              <p className="report-paragraph">
                To sign out anyway you must record a reason — it is frozen into the signed record.
              </p>
              {/* Each repeat locus and Paraphase region the gate lists: the acknowledgement
                  covers these (TRACE-19). */}
              {unreviewedGate.findings.length ? (
                <ul className="report-drift-list" aria-label="Findings without a review">
                  {unreviewedGate.findings.map((finding) => (
                    <li key={`${finding.kind}:${finding.key ?? 'unreadable'}`}>
                      {describeUnreviewedFinding(finding)}
                    </li>
                  ))}
                </ul>
              ) : null}
              <label className="report-footer-label" htmlFor="unreviewed-ack-reason">
                Reason for signing out with findings that have no review (required)
              </label>
              <textarea
                id="unreviewed-ack-reason"
                className="variant-review-textarea"
                rows={3}
                value={unreviewedReason}
                onChange={(event) => setUnreviewedReason(event.target.value)}
              />
              <div className="inline-actions modal-actions">
                <button
                  type="button"
                  className="form-button"
                  onClick={() => {
                    setUnreviewedGate(null);
                    setUnreviewedReason('');
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="form-button"
                  disabled={!unreviewedReason.trim() || signOut.isPending}
                  onClick={submitUnreviewedAcknowledgement}
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
