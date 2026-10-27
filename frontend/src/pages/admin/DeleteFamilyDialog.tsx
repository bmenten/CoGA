import React, { useEffect, useState } from 'react';
import ModalDialog from '../../components/ModalDialog';

interface DeleteFamilyDialogProps {
  familyId: string;
  busy: boolean;
  onCancel: () => void;
  /** Called with the reason the administrator gave, which the deletion requires. */
  onConfirm: (reason: string) => void;
}

/**
 * Two-step destructive confirmation for permanently deleting an entire family.
 * Step 1 spells out exactly what will be erased and requires the operator to type
 * the family name verbatim and to say why it goes: the reason is recorded on the
 * family's last clinical audit event, and the API refuses a deletion without one
 * (TRACE-16). Step 2 is a final confirmation immediately before execution. The
 * delete button is only ever enabled once the typed name matches and a reason is given.
 */
const DeleteFamilyDialog: React.FC<DeleteFamilyDialogProps> = ({
  familyId,
  busy,
  onCancel,
  onConfirm,
}) => {
  const [typedName, setTypedName] = useState('');
  const [reason, setReason] = useState('');
  const [step, setStep] = useState<'warn' | 'final'>('warn');

  useEffect(() => {
    setTypedName('');
    setReason('');
    setStep('warn');
  }, [familyId]);

  const nameMatches = typedName.trim() === familyId;
  const statedReason = reason.trim();
  const ready = nameMatches && statedReason !== '';

  return (
    // ModalDialog: Escape cancels, except while the deletion runs, with a focus trap and
    // focus restore (#529). Focus starts in the name field, the first control. A click
    // on the backdrop does nothing in a destructive confirmation.
    <ModalDialog
      onClose={() => {
        if (!busy) onCancel();
      }}
      label={`Delete family ${familyId}`}
      className="modal-surface surface-card admin-delete-family-modal"
      closeOnBackdrop={false}
      confirmDiscard={false}
    >
      <div className="space-y-2">
        <p className="page-kicker page-kicker--danger">Permanent deletion</p>
        <h2 className="section-title">Delete family {familyId}</h2>
      </div>

      {step === 'warn' ? (
        <>
          <div className="admin-delete-family-warning">
            <p>This action permanently deletes:</p>
            <ul>
              <li>Family metadata</li>
              <li>Samples</li>
              <li>Variant data</li>
              <li>Annotations</li>
              <li>Imported files linked to this family</li>
            </ul>
            <p className="admin-delete-family-warning-strong">
              This operation cannot be undone.
            </p>
          </div>

          <label className="field-label">
            Type the family name <strong>{familyId}</strong> to continue
            <input
              type="text"
              value={typedName}
              onChange={(event) => setTypedName(event.target.value)}
              placeholder={familyId}
              autoComplete="off"
              spellCheck={false}
            />
          </label>

          <label className="field-label">
            Reason for the deletion (required; it is recorded on the family&apos;s audit trail)
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              maxLength={1000}
            />
          </label>

          <div className="inline-actions modal-actions">
            <button type="button" className="button-secondary" onClick={onCancel}>
              Cancel
            </button>
            <button
              type="button"
              className="button-danger"
              disabled={!ready}
              onClick={() => setStep('final')}
            >
              Continue
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="admin-delete-family-warning">
            <p className="admin-delete-family-warning-strong">
              Final confirmation: permanently delete {familyId} and every linked
              record and file?
            </p>
            <p>There is no recovery after this step.</p>
            <p>Reason: {statedReason}</p>
          </div>

          <div className="inline-actions modal-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setStep('warn')}
              disabled={busy}
            >
              Back
            </button>
            <button
              type="button"
              className="button-danger"
              disabled={!ready || busy}
              onClick={() => onConfirm(statedReason)}
            >
              {busy ? 'Deleting…' : `Permanently delete ${familyId}`}
            </button>
          </div>
        </>
      )}
    </ModalDialog>
  );
};

export default DeleteFamilyDialog;
